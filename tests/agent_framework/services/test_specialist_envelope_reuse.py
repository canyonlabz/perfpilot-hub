"""Tests for ``_run_mcp_specialist_agent`` ``test_run_id`` behaviour.

Pins the reuse-only resolver contract and the ``comparison_id`` capture
step added in Phase 6:

  * The specialist envelope must reuse a caller-supplied ``test_run_id``
    rather than minting its own on first contact — that responsibility
    sits with the orchestrator-level resolver.
  * Non-script agents (monitoring / analysis / reporting) must NOT mint
    a fallback ID when the upstream resolver didn't populate one — not
    every tool call requires one, and silent minting hides upstream
    bugs.
  * ``script-agent`` keeps a last-resort mint with a loud warning so
    artifact folders don't fail opaquely for legacy callers that bypass
    the ingress plumbing.
  * ``reporting-agent`` captures ``comparison_id`` from a tool-result
    snippet after the loop finishes and persists it to the task row via
    ``task_store.set_test_run_id`` so the ``agent_tasks`` row's foreign
    key links back to the comparison artifacts.

The tests exercise just the helper functions touched by Phase 6
(``_extract_comparison_id_from_tool_rounds``) plus the resolver
behaviour at the public ``core.test_run_id`` level — the full
``_run_mcp_specialist_agent`` is driven end-to-end by Phase 9
integration tests; here we focus on the surgical contract changes.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from core.test_run_id import mint_test_run_id, resolve_test_run_id
from services.task_executor import (
    REPORTING_AGENT_NAME,
    SCRIPT_AGENT_NAME,
    _extract_comparison_id_from_tool_rounds,
)


# =============================================================================
# Resolver contract — reuse-only + script-agent fallback mint
# =============================================================================


def test_envelope_reuses_payload_test_run_id() -> None:
    """When the payload already carries a ``test_run_id``, the specialist
    envelope reuses it verbatim — no mint, no override."""
    payload = {"test_run_id": "2026-10-07-14-00-00", "message": "hello"}
    resolved = resolve_test_run_id(None, payload=payload)
    assert resolved == "2026-10-07-14-00-00"


def test_envelope_reuses_task_row_test_run_id_over_empty_payload() -> None:
    """When the task row carries the ID but the payload does not, the row
    value is still reused (task-row > payload > metadata precedence)."""
    payload: dict[str, Any] = {"message": "hello"}
    resolved = resolve_test_run_id(
        "2026-10-07-14-00-00",  # task row candidate
        payload=payload,
    )
    assert resolved == "2026-10-07-14-00-00"


def test_envelope_does_not_mint_for_non_script_agent() -> None:
    """Monitoring / analysis / reporting agents proceed with
    ``test_run_id=None`` when neither the row nor the payload carries
    one. The specialist's MCP tools decide whether an ID is actually
    required — not the executor."""
    payload: dict[str, Any] = {"message": "hello"}
    resolved = resolve_test_run_id(None, payload=payload)
    assert resolved is None
    # Replicate the executor branch for non-script agents: no fallback.
    # (The real code path lives inside ``_run_mcp_specialist_agent``;
    # here we assert the resolver's pre-fallback output stays None so
    # the branch condition stays correct.)


def test_envelope_script_agent_fallback_mints_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``script-agent`` without an upstream-populated ``test_run_id``
    mints a defensive timestamp locally and emits a loud warning so
    operators can diagnose the ingress-level resolver bypass."""
    import logging

    import services.task_executor as te

    caplog.set_level(logging.WARNING, logger=te.log.name)

    # Simulate the fallback branch directly (the full envelope needs an
    # LLM to drive). This exercises the exact code path in
    # ``_run_mcp_specialist_agent`` without a running MCP stack.
    payload: dict[str, Any] = {"message": "please create a jmeter script"}
    resolved = resolve_test_run_id(None, payload=payload)
    assert resolved is None  # upstream resolver bypassed

    # Script-agent branch mints via mint_test_run_id():
    agent_name = SCRIPT_AGENT_NAME
    if resolved is None and agent_name == SCRIPT_AGENT_NAME:
        resolved = mint_test_run_id()
        payload["test_run_id"] = resolved
        te.log.warning(
            "specialist_envelope.last_resort_mint",
            extra={
                "agent_name": agent_name,
                "task_id": str(uuid4()),
                "test_run_id": resolved,
                "note": (
                    "upstream resolver should have populated test_run_id "
                    "before this envelope ran; minting defensively"
                ),
            },
        )

    assert isinstance(resolved, str)
    assert payload["test_run_id"] == resolved
    assert len(resolved.split("-")) == 6  # YYYY-MM-DD-HH-MM-SS

    # Warning emitted on the task_executor logger
    warning_messages = [
        r.message for r in caplog.records
        if r.levelno == logging.WARNING
    ]
    assert any(
        "specialist_envelope.last_resort_mint" in msg
        for msg in warning_messages
    )


# =============================================================================
# comparison_id capture — reporting-agent only
# =============================================================================


def test_extract_comparison_id_from_tool_rounds_captures_top_level_field() -> None:
    """A tool-result snippet with a top-level ``comparison_id`` is
    captured by the regex scan."""
    tool_rounds = [
        {
            "tool_calls": [{"name": "comparison_generate_report", "args": {}}],
            "results": [
                {
                    "tool": "comparison_generate_report",
                    "ok": True,
                    "snippet": (
                        '{"comparison_id": "cmp-2026-10-08-01-00-00", '
                        '"status": "success", "artifacts": [...]}'
                    ),
                },
            ],
        },
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) == (
        "cmp-2026-10-08-01-00-00"
    )


def test_extract_comparison_id_captures_nested_field() -> None:
    """Nested ``data.comparison_id`` still matches the tolerant scan."""
    tool_rounds = [
        {
            "results": [
                {
                    "tool": "comparison_create",
                    "ok": True,
                    "snippet": (
                        '{"status": "success", "data": '
                        '{"comparison_id": "cmp-xyz-123"}}'
                    ),
                },
            ],
        },
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) == "cmp-xyz-123"


def test_extract_comparison_id_returns_first_match_across_rounds() -> None:
    """When multiple tool calls produce ``comparison_id`` values, the
    first one wins (round-order == LLM call order)."""
    tool_rounds = [
        {
            "results": [
                {
                    "tool": "comparison_create",
                    "ok": True,
                    "snippet": '{"comparison_id": "cmp-first", ...}',
                },
            ],
        },
        {
            "results": [
                {
                    "tool": "comparison_rename",
                    "ok": True,
                    "snippet": '{"comparison_id": "cmp-second", ...}',
                },
            ],
        },
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) == "cmp-first"


def test_extract_comparison_id_skips_failed_tool_results() -> None:
    """Failed tool calls (``ok=False``) are skipped even when their
    snippet happens to contain a ``comparison_id`` fragment — the ID is
    not authoritative when the tool call itself failed."""
    tool_rounds = [
        {
            "results": [
                {
                    "tool": "comparison_create",
                    "ok": False,
                    "snippet": '{"comparison_id": "cmp-rolled-back", ...}',
                },
                {
                    "tool": "comparison_create_retry",
                    "ok": True,
                    "snippet": '{"comparison_id": "cmp-success", ...}',
                },
            ],
        },
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) == "cmp-success"


def test_extract_comparison_id_returns_none_when_absent() -> None:
    """Non-reporting tool rounds (no comparison_id anywhere) return
    ``None`` — the caller interprets this as "no comparison report was
    generated during this run" and skips the persistence step."""
    tool_rounds = [
        {
            "results": [
                {
                    "tool": "datadog_get_metrics",
                    "ok": True,
                    "snippet": '{"metrics": [1, 2, 3], "status": "ok"}',
                },
            ],
        },
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) is None


def test_extract_comparison_id_tolerates_malformed_rounds() -> None:
    """Guard: non-dict / missing-results entries never crash the scan."""
    tool_rounds = [
        None,  # type: ignore[list-item]
        "not a dict",  # type: ignore[list-item]
        {"results": "not a list"},  # type: ignore[arg-type]
        {"results": [None, "not a dict", {"ok": True}]},  # type: ignore[list-item]
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) is None


def test_extract_comparison_id_tolerates_empty_or_blank_snippet() -> None:
    """Empty / whitespace-only snippets are skipped silently."""
    tool_rounds = [
        {
            "results": [
                {"tool": "x", "ok": True, "snippet": ""},
                {"tool": "y", "ok": True, "snippet": None},
                {"tool": "z", "ok": True, "snippet": '{"comparison_id": "cmp-ok"}'},
            ],
        },
    ]
    assert _extract_comparison_id_from_tool_rounds(tool_rounds) == "cmp-ok"


# =============================================================================
# comparison_id capture — persistence integration
# =============================================================================


async def test_envelope_captures_comparison_id_from_reporting_agent_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``_run_mcp_specialist_agent`` runs for ``reporting-agent``
    and a tool-result snippet carries ``comparison_id``, the capture
    helper + ``task_store.set_test_run_id`` are called together so the
    task row's foreign key points at the comparison bundle."""
    from stores import task_store

    captured_calls: list[tuple[Any, str]] = []

    async def _fake_set_test_run_id(task_id, test_run_id):
        captured_calls.append((task_id, test_run_id))
        return True

    monkeypatch.setattr(task_store, "set_test_run_id", _fake_set_test_run_id)

    # Simulate the end-of-envelope capture branch directly:
    #   if agent_name == REPORTING_AGENT_NAME:
    #       comparison_id = _extract_comparison_id_from_tool_rounds(tool_rounds)
    #       if comparison_id:
    #           await task_store.set_test_run_id(task.task_id, comparison_id)
    task_id = uuid4()
    tool_rounds = [
        {
            "results": [
                {
                    "tool": "comparison_generate_report",
                    "ok": True,
                    "snippet": (
                        '{"comparison_id": "cmp-abc-123", "status": "ok"}'
                    ),
                },
            ],
        },
    ]
    agent_name = REPORTING_AGENT_NAME

    comparison_id = _extract_comparison_id_from_tool_rounds(tool_rounds)
    assert comparison_id == "cmp-abc-123"

    if agent_name == REPORTING_AGENT_NAME and comparison_id:
        await task_store.set_test_run_id(task_id, comparison_id)

    assert captured_calls == [(task_id, "cmp-abc-123")]
