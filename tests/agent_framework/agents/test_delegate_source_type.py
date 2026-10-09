"""Tests for ``delegate_to_specialist`` — ``source_type`` arg + the
framework-authoritative ``test_run_id`` contract.

Pins the Phase 7 orchestrator-tool contract additions:

  * The new ``source_type`` tool arg is copied onto the child payload
    when (and only when) ``agent_name == "script-agent"``. Other
    specialists silently drop it (debug log only).
  * ``comparison={"test_run_ids": [...]}`` payloads threaded to
    ``reporting-agent`` are preserved verbatim on the child payload
    (shallow-copy guarantee) so the reporting-agent envelope can
    forward them to the PerfReport MCP comparison tools.
  * script-agent delegations WITHOUT ``source_type`` no longer trigger
    an auto-mint at delegation time — they fall through to the normal
    LLM-tool-arg/body-dict branch, letting the specialist envelope's
    last-resort fallback (Phase 6) catch any still-missing IDs with a
    loud warning.
  * The ContextVar / ``_caller_identity`` resolution still beats every
    other candidate, including script-agent + source_type (framework
    authority is absolute).

These tests exercise the tool function directly against an in-memory
stand-in for ``_standalone_create_task`` so the DB layer is not
required. End-to-end integration lives in Phase 9.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def orch(monkeypatch):
    """Import the orchestrator module and reset its process-scoped state.

    Also patches ``_standalone_create_task`` with a capture helper so
    tests can inspect the exact payload / test_run_id that would be
    written to the ``agent_tasks`` table.

    Resets both ``_caller_identity`` (module dict) and the four
    ContextVars (``agent_test_run_id_var``, ``agent_session_id_var``,
    ``agent_thread_id_var``, ``agent_task_id_var``) so a stale value
    from a prior test cannot bleed into this one.
    """
    from agents.orchestrator import agent as orchestrator_module

    orchestrator_module.clear_caller_identity()
    orchestrator_module.drain_pending_executions()

    # Reset the four ContextVars that participate in test_run_id and
    # session resolution. ContextVar.set returns a Token the caller can
    # use to .reset() later; here we unconditionally overwrite to None
    # at the start of each test since we only want a clean baseline.
    orchestrator_module.agent_test_run_id_var.set(None)
    orchestrator_module.agent_session_id_var.set(None)
    orchestrator_module.agent_thread_id_var.set(None)
    orchestrator_module.agent_task_id_var.set(None)

    captured: dict[str, Any] = {}

    async def _fake_standalone_create_task(
        session_id: UUID,
        agent_name: str,
        payload: dict,
        test_run_id: str | None = None,
        thread_id: str | None = None,
        parent_task_id: UUID | None = None,
    ) -> dict:
        captured["session_id"] = session_id
        captured["agent_name"] = agent_name
        captured["payload"] = payload
        captured["test_run_id"] = test_run_id
        captured["thread_id"] = thread_id
        captured["parent_task_id"] = parent_task_id
        return {
            "task_id": uuid4(),
            "session_id": session_id,
            "agent_name": agent_name,
            "status": "pending",
            "test_run_id": test_run_id,
            "thread_id": thread_id,
            "parent_task_id": parent_task_id,
            "submitted_at": "2026-10-08T00:00:00Z",
        }

    monkeypatch.setattr(
        orchestrator_module,
        "_standalone_create_task",
        _fake_standalone_create_task,
    )

    orchestrator_module._captured_create_task = captured  # type: ignore[attr-defined]
    yield orchestrator_module

    orchestrator_module.clear_caller_identity()
    orchestrator_module.drain_pending_executions()
    orchestrator_module.agent_test_run_id_var.set(None)
    orchestrator_module.agent_session_id_var.set(None)
    orchestrator_module.agent_thread_id_var.set(None)
    orchestrator_module.agent_task_id_var.set(None)


def _prime_caller_identity(
    orch_module: Any,
    *,
    session_id: str | None = None,
    thread_id: str = "thread-test",
    task_id: str | None = None,
    test_run_id: str | None = None,
) -> None:
    """Populate ``_caller_identity`` with the minimum fields the tool
    needs to succeed (session_id is a hard requirement — delegation
    short-circuits with ``NoSession`` otherwise)."""
    orch_module.set_caller_identity(
        user_id="u-test",
        thread_id=thread_id,
        session_id=session_id or str(uuid4()),
        task_id=task_id,
        test_run_id=test_run_id,
    )


# =============================================================================
# source_type thread-through — script-agent only
# =============================================================================


async def test_source_type_arg_copied_to_child_payload_for_script_agent(
    orch,
) -> None:
    """When delegating to ``script-agent`` with ``source_type``, the
    normalized string is injected into the child task's payload so the
    specialist can read it from the payload envelope."""
    _prime_caller_identity(orch)

    await orch.delegate_to_specialist(
        agent_name="script-agent",
        payload={"user_message": "please create a jmeter script"},
        source_type="Playwright",  # mixed case — should be normalized
    )

    captured = orch._captured_create_task
    assert captured["agent_name"] == "script-agent"
    assert captured["payload"]["source_type"] == "playwright"
    # test_run_id is minted (fresh UTC stamp, 6 dash-separated parts)
    assert isinstance(captured["test_run_id"], str)
    assert len(captured["test_run_id"].split("-")) == 6


async def test_source_type_arg_ignored_for_other_agents(orch) -> None:
    """Non-script agents receive the payload unchanged — ``source_type``
    is dropped silently (debug log only) so stray LLM args cannot leak
    into unrelated specialist envelopes."""
    _prime_caller_identity(orch)

    for agent_name in (
        "execution-agent",
        "monitoring-agent",
        "analysis-agent",
        "reporting-agent",
        "notifications-agent",
    ):
        orch._captured_create_task.clear()
        await orch.delegate_to_specialist(
            agent_name=agent_name,
            payload={"message": "x"},
            source_type="playwright",
        )
        captured = orch._captured_create_task
        assert "source_type" not in captured["payload"], (
            f"source_type leaked into {agent_name} payload"
        )


# =============================================================================
# comparison intent — reporting-agent passthrough
# =============================================================================


async def test_comparison_payload_threaded_to_reporting_agent(orch) -> None:
    """A ``comparison={"test_run_ids": [...]}`` payload threaded to
    ``reporting-agent`` is preserved verbatim via the shallow-copy
    ``body = dict(payload or {})`` step. The reporting-agent envelope
    uses this to drive the PerfReport MCP comparison tools."""
    _prime_caller_identity(orch)

    await orch.delegate_to_specialist(
        agent_name="reporting-agent",
        payload={
            "user_message": "generate a comparison report",
            "comparison": {
                "test_run_ids": [
                    "2026-10-07-14-00-00",
                    "2026-10-06-10-00-00",
                ],
            },
        },
    )

    captured = orch._captured_create_task
    assert captured["agent_name"] == "reporting-agent"
    assert captured["payload"]["comparison"] == {
        "test_run_ids": [
            "2026-10-07-14-00-00",
            "2026-10-06-10-00-00",
        ],
    }


async def test_comparison_payload_without_reporting_agent_still_preserved(
    orch,
) -> None:
    """Even for non-reporting agents, the shallow copy keeps the
    comparison block on the body — the observability log is scoped to
    reporting-agent, but the data itself is never stripped. (This is
    not a user-facing contract; it just locks in the no-mutation
    guarantee of ``body = dict(payload or {})``.)"""
    _prime_caller_identity(orch)

    await orch.delegate_to_specialist(
        agent_name="execution-agent",
        payload={
            "message": "weird call",
            "comparison": {"test_run_ids": ["2026-10-07-14-00-00"]},
        },
    )

    captured = orch._captured_create_task
    assert captured["payload"]["comparison"] == {
        "test_run_ids": ["2026-10-07-14-00-00"],
    }


# =============================================================================
# test_run_id resolution branch — script-agent without source_type
# =============================================================================


async def test_delegate_without_source_type_to_script_agent_falls_through_to_envelope(
    orch,
) -> None:
    """script-agent delegations WITHOUT ``source_type`` no longer
    auto-mint at the orchestrator layer. The ContextVar resolver still
    runs first, so a context-primed ID wins; otherwise the branch falls
    through to the LLM-tool-arg / body-dict path. For this test the
    LLM did not supply a timestamp either, so ``test_run_id`` lands as
    ``None`` on the DB row — the specialist envelope's last-resort
    fallback catches it with a warning."""
    _prime_caller_identity(orch)

    # No context test_run_id, no payload test_run_id, no tool arg.
    await orch.delegate_to_specialist(
        agent_name="script-agent",
        payload={"user_message": "ambiguous ask without source_type"},
    )

    captured = orch._captured_create_task
    assert captured["agent_name"] == "script-agent"
    assert captured["test_run_id"] is None
    assert "test_run_id" not in captured["payload"]


async def test_delegate_without_source_type_still_mints_when_context_has_id(
    orch,
) -> None:
    """When ``_caller_identity`` already carries a ``test_run_id`` from
    a prior turn, that ID wins — the missing ``source_type`` is
    irrelevant because the framework-authoritative resolver succeeds
    before the mint branch is even considered."""
    _prime_caller_identity(orch, test_run_id="2026-10-05-09-00-00")

    await orch.delegate_to_specialist(
        agent_name="script-agent",
        payload={"user_message": "ambiguous but context has an ID"},
    )

    captured = orch._captured_create_task
    assert captured["test_run_id"] == "2026-10-05-09-00-00"
    assert captured["payload"]["test_run_id"] == "2026-10-05-09-00-00"


# =============================================================================
# ContextVar / _caller_identity authority — beats every other candidate
# =============================================================================


async def test_resolved_test_run_id_still_wins_over_llm_tool_arg(orch) -> None:
    """When ``_caller_identity["test_run_id"]`` is set, the LLM-supplied
    ``test_run_id`` tool arg is ignored even if it is a well-formed
    timestamp. Framework authority is absolute — stale LLM
    hallucinations cannot overwrite the active-run ID."""
    _prime_caller_identity(orch, test_run_id="2026-10-07-14-00-00")

    await orch.delegate_to_specialist(
        agent_name="execution-agent",
        payload={"message": "run the test"},
        test_run_id="2026-01-01-00-00-00",  # LLM hallucination
    )

    captured = orch._captured_create_task
    assert captured["test_run_id"] == "2026-10-07-14-00-00"
    assert captured["payload"]["test_run_id"] == "2026-10-07-14-00-00"


async def test_resolved_test_run_id_wins_over_source_type_mint(orch) -> None:
    """script-agent + explicit ``source_type`` normally mints a fresh
    ID, but if ``_caller_identity`` already carries one, that wins —
    the mint branch is bypassed. This prevents a mid-conversation
    script re-run from accidentally creating a parallel artifact tree."""
    _prime_caller_identity(orch, test_run_id="2026-10-07-14-00-00")

    await orch.delegate_to_specialist(
        agent_name="script-agent",
        payload={"user_message": "re-generate the jmx"},
        source_type="har",
    )

    captured = orch._captured_create_task
    assert captured["test_run_id"] == "2026-10-07-14-00-00"
    # source_type is still copied onto the child payload — the specialist
    # may still want to know what kind of source it was asked about.
    assert captured["payload"]["source_type"] == "har"


# =============================================================================
# Guard: NoSession short-circuit still runs before source_type logic
# =============================================================================


async def test_delegate_without_session_short_circuits_before_source_type(
    orch,
) -> None:
    """Delegation requires a session_id — the ``NoSession`` guard runs
    before any of the new Phase 7 logic. Without this guard, we'd
    accidentally mint and then fail on INSERT. Important for A2A
    callers that bypass the usual middleware."""
    # Intentionally skip _prime_caller_identity so session_id is None.
    import json

    result_json = await orch.delegate_to_specialist(
        agent_name="script-agent",
        payload={"user_message": "x"},
        source_type="playwright",
    )
    result = json.loads(result_json)
    assert result["ok"] is False
    assert result["error"]["type"] == "NoSession"
    # No DB call happened — captured is still empty.
    assert orch._captured_create_task == {}
