"""Regression guards around ``spec_normalizer`` after Phase 6.

Phase 6 kept ``spec_normalizer`` unchanged — the strict Playwright gate
lives upstream in ``services.task_executor._persist_test_spec_from_parts``
where ``source_type`` is accessible via the full payload. These tests
pin the invariant that:

  * ``spec_normalizer._looks_like_test_spec`` continues to detect
    step-based prose (Web-UI prose fallback). It is NOT a dispatch
    signal — only a shape check for text parts inside the strict gate.
  * The strict gate at ``_persist_test_spec_from_parts`` is what
    requires ``source_type == "playwright"``; the normalizer itself
    is media-type-agnostic and will happily extract content from HAR
    or OpenAPI parts if called directly (the gate upstream prevents
    that from happening in production flows).

The gate-side behaviour is covered more thoroughly in
``test_persist_test_spec_strict.py``. These tests act as early-warning
regression guards: if a future refactor accidentally moves the gate
into ``spec_normalizer`` and breaks the Web-UI prose shape detection,
these guards fail before the integration tests do.
"""

from __future__ import annotations

import pytest

from a2a.server import spec_normalizer


# =============================================================================
# _looks_like_test_spec — Web-UI prose fallback regression guards
# =============================================================================


def test_looks_like_test_spec_still_used_for_prose_fallback() -> None:
    """``_looks_like_test_spec`` detects step-based prose using
    ``^\\s*(step|tc|ts|test case|test step)\\s*\\d`` with multiline +
    case-insensitive flags. The Web-UI prose path relies on this to
    identify user prose that could be used as a test spec; breaking
    this helper breaks the Web-UI flow."""
    assert spec_normalizer._looks_like_test_spec(
        "Step 1: Navigate to https://example.com/."
    )
    assert spec_normalizer._looks_like_test_spec(
        "step 1: lowercase still matches"
    )
    assert spec_normalizer._looks_like_test_spec(
        "TC01: ADO test case prefix"
    )
    assert spec_normalizer._looks_like_test_spec(
        "TS01: test suite prefix"
    )
    assert spec_normalizer._looks_like_test_spec(
        "Test Case 1: labeled test case"
    )
    assert spec_normalizer._looks_like_test_spec(
        "Test Step 2: labeled test step"
    )


def test_looks_like_test_spec_matches_in_middle_of_prose() -> None:
    """Multiline mode: the step pattern can appear on any line, not
    only the first — this matches real user messages that may start
    with "Please run this test:" followed by step lines."""
    text = (
        "Please run this test for the login flow:\n"
        "\n"
        "Step 1: Navigate to https://example.com/.\n"
        "Step 2: Enter credentials and click Login.\n"
    )
    assert spec_normalizer._looks_like_test_spec(text)


def test_looks_like_test_spec_rejects_pure_prose() -> None:
    """Non-step-based prose must NOT match — otherwise the Web-UI
    heuristic would incorrectly dispatch casual chat to the test-spec
    pipeline."""
    assert not spec_normalizer._looks_like_test_spec(
        "Hi, how are things going? Can you tell me about yesterday's run?"
    )
    assert not spec_normalizer._looks_like_test_spec(
        "The response time was 500ms on the login endpoint."
    )
    assert not spec_normalizer._looks_like_test_spec("")


# =============================================================================
# Strict gate wrapper semantics — gate is at ``_persist_test_spec_from_parts``
# =============================================================================


def test_strict_gate_requires_source_type_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the dependency direction: ``spec_normalizer.normalize_parts_to_spec``
    is called ONLY from inside the strict gate at
    ``_persist_test_spec_from_parts`` after that caller has verified
    ``source_type == "playwright"``. The normalizer itself does NOT
    know about ``source_type`` — it's a pure parts→markdown function.

    This test observes the dependency by patching
    ``spec_normalizer.normalize_parts_to_spec`` and confirming it is
    never called when the gate upstream classifies the request as HAR
    (verified via ``_persist_test_spec_from_parts``)."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from services.task_executor import _persist_test_spec_from_parts
    from stores import task_store

    normalize_calls: list[list[dict]] = []

    def _spy(parts):
        normalize_calls.append(parts)
        return None

    monkeypatch.setattr(spec_normalizer, "normalize_parts_to_spec", _spy)

    now = datetime.now(timezone.utc)
    har_task = task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="running",
        payload={
            "test_run_id": "2026-10-07-14-00-00",
            "parts": [
                {"text": "Run this HAR"},
                {
                    "data": {"log": {"entries": []}},
                    "mediaType": "application/json",
                    "metadata": {"source_type": "har"},
                },
            ],
        },
        submitted_at=now,
        updated_at=now,
    )
    _persist_test_spec_from_parts(har_task)
    # The HAR source_type blocks the gate — normalizer must never run.
    assert normalize_calls == []


def test_strict_gate_rejects_har_or_openapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both ``har`` and ``openapi`` source types short-circuit the
    helper before ``spec_normalizer`` is consulted. The normalizer is
    never asked to make the HAR vs Playwright decision — that
    classification happens upstream in the strict gate."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from services.task_executor import _persist_test_spec_from_parts
    from stores import task_store

    normalize_calls: list[list[dict]] = []
    monkeypatch.setattr(
        spec_normalizer, "normalize_parts_to_spec",
        lambda parts: normalize_calls.append(parts) or None,
    )

    now = datetime.now(timezone.utc)
    for source_type in ("har", "openapi"):
        task = task_store.AgentTask(
            task_id=uuid4(),
            session_id=uuid4(),
            agent_name="orchestrator",
            status="running",
            payload={
                "test_run_id": "2026-10-07-14-00-00",
                "parts": [
                    {"text": "Run this"},
                    {
                        "data": {"ignored": True},
                        "mediaType": "application/json",
                        "metadata": {"source_type": source_type},
                    },
                ],
            },
            submitted_at=now,
            updated_at=now,
        )
        _persist_test_spec_from_parts(task)

    assert normalize_calls == []


def test_strict_gate_opens_for_source_type_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reciprocal regression guard: when source_type IS playwright, the
    gate opens and ``spec_normalizer.normalize_parts_to_spec`` is
    invoked exactly once with the full parts list."""
    from datetime import datetime, timezone
    from pathlib import Path
    from uuid import uuid4

    from services.task_executor import _persist_test_spec_from_parts
    from stores import task_store

    # Prevent any real filesystem write — this test is about the
    # dependency wiring, not the on-disk outcome.
    monkeypatch.setattr(Path, "mkdir", lambda self, **kw: None)
    monkeypatch.setattr(Path, "write_text", lambda self, c, **kw: len(c))

    normalize_calls: list[list[dict]] = []

    def _spy(parts):
        normalize_calls.append(parts)
        return "Step 1: open page\nEND TASK\n"

    monkeypatch.setattr(spec_normalizer, "normalize_parts_to_spec", _spy)

    now = datetime.now(timezone.utc)
    playwright_task = task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="running",
        payload={
            "test_run_id": "2026-10-07-14-00-00",
            "parts": [
                {"text": "Run this test"},
                {
                    "text": "Step 1: Navigate to https://example.com/.",
                    "mediaType": "text/markdown",
                    "metadata": {"source_type": "playwright"},
                },
            ],
        },
        submitted_at=now,
        updated_at=now,
    )
    _persist_test_spec_from_parts(playwright_task)
    assert len(normalize_calls) == 1
    # The normalizer saw the FULL parts list (both parts), not just the
    # second one — the gate selects whether to call it at all, not what
    # it sees.
    assert len(normalize_calls[0]) == 2
