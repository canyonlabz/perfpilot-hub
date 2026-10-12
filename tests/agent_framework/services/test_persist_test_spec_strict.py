"""Tests for ``_persist_test_spec_from_parts`` strict Playwright gate.

Phase 6 locks down the test-specs materialization helper so it only runs
for Playwright inbound payloads. HAR captures and OpenAPI specs are
no-ops here — their specialist tools fetch their own source files, so
the test-specs/ folder is only populated for the browser automation
path.

Additionally, when Playwright is declared on the parts metadata but the
normalizer finds no recognizable step-based content, the helper raises
``TaskInputRequiredSignal(reason_code="malformed_playwright_test_spec")``
so the task row transitions to ``input_required`` and the client can
resend corrected parts rather than silently proceeding into an
opaque specialist failure.

The tests do not touch the real filesystem (writes are monkeypatched)
and do not require a running database (``agent_test_run_id_var`` and
``agent_spec_file_var`` are simple ContextVars).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from core.task_signals import TaskInputRequiredSignal
from services.task_executor import _persist_test_spec_from_parts
from stores import task_store


# =============================================================================
# Fixtures
# =============================================================================


def _make_task(
    *,
    payload: dict[str, Any],
    task_id: Optional[UUID] = None,
    test_run_id: Optional[str] = None,
) -> task_store.AgentTask:
    """Fabricate an ``AgentTask`` row for the helper to inspect."""
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=task_id or uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="running",
        payload=payload,
        submitted_at=now,
        updated_at=now,
        external_session_id="ext-session",
        test_run_id=test_run_id,
        thread_id="thread-abc",
        result=None,
        error=None,
        subscriber_endpoints=[],
    )


def _playwright_payload(
    *,
    steps_text: str = "Step 1: Navigate to https://example.com/.",
    test_run_id: Optional[str] = "2026-10-07-14-00-00",
) -> dict[str, Any]:
    """Build an A2A payload that classifies as source_type=playwright.

    ``extract_source_type`` reads ``parts[1].metadata.source_type``, so
    we build a two-part shape: parts[0] is the user message, parts[1]
    carries both the test spec text and the source_type metadata.
    """
    payload: dict[str, Any] = {
        "parts": [
            {"text": "Run this test"},
            {
                "text": steps_text,
                "mediaType": "text/markdown",
                "metadata": {"source_type": "playwright"},
            },
        ],
    }
    if test_run_id is not None:
        payload["test_run_id"] = test_run_id
    return payload


@pytest.fixture
def no_filesystem_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Redirect ``Path.write_text`` + ``Path.mkdir`` to in-memory sinks.

    Returns a list that captures every ``(spec_path, content)`` pair
    the helper tries to write so tests can assert on both path shape
    and body without touching the real disk.
    """
    from pathlib import Path

    captured: list[tuple[str, str]] = []

    def _fake_write_text(self, content, encoding="utf-8"):
        captured.append((str(self), content))
        return len(content)

    def _fake_mkdir(self, parents=False, exist_ok=False):
        return None

    monkeypatch.setattr(Path, "write_text", _fake_write_text)
    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)
    return captured


# =============================================================================
# Strict gate — HAR / OpenAPI / no source_type payloads are no-ops
# =============================================================================


def test_har_payload_is_a_noop(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """A HAR source_type must not materialize a test spec file; the
    har specialist tools fetch the HAR capture directly."""
    payload: dict[str, Any] = {
        "test_run_id": "2026-10-07-14-00-00",
        "parts": [
            {"text": "Convert this HAR"},
            {
                "data": {"log": {"entries": []}},
                "mediaType": "application/json",
                "metadata": {"source_type": "har"},
            },
        ],
    }
    task = _make_task(payload=payload)
    _persist_test_spec_from_parts(task)
    assert no_filesystem_writes == []


def test_openapi_payload_is_a_noop(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """An OpenAPI source_type must not materialize a test spec file."""
    payload: dict[str, Any] = {
        "test_run_id": "2026-10-07-14-00-00",
        "parts": [
            {"text": "Convert this OpenAPI spec"},
            {
                "data": {"openapi": "3.0.0", "paths": {}},
                "mediaType": "application/json",
                "metadata": {"source_type": "openapi"},
            },
        ],
    }
    task = _make_task(payload=payload)
    _persist_test_spec_from_parts(task)
    assert no_filesystem_writes == []


def test_no_source_type_metadata_is_a_noop(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """A payload with parts[] but no ``source_type`` metadata is a
    no-op — this is the Web-UI prose-driven path where the orchestrator
    LLM decides what to do, not the A2A auto-materialization path."""
    payload: dict[str, Any] = {
        "test_run_id": "2026-10-07-14-00-00",
        "parts": [
            {"text": "Here's some test content"},
            {"text": "Step 1: do something"},
        ],
    }
    task = _make_task(payload=payload)
    _persist_test_spec_from_parts(task)
    assert no_filesystem_writes == []


def test_no_parts_is_a_noop(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """Guard: payload without ``parts[]`` is a no-op, as before."""
    payload: dict[str, Any] = {
        "test_run_id": "2026-10-07-14-00-00",
        "message": "regular chat message",
    }
    task = _make_task(payload=payload)
    _persist_test_spec_from_parts(task)
    assert no_filesystem_writes == []


def test_non_dict_payload_is_a_noop(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """Guard: non-dict payload is a no-op (defensive)."""
    task = _make_task(payload={})
    object.__setattr__(task, "payload", "not a dict")  # type: ignore[arg-type]
    _persist_test_spec_from_parts(task)
    assert no_filesystem_writes == []


# =============================================================================
# Strict gate — Playwright happy path writes file WITHOUT minting
# =============================================================================


def test_playwright_with_valid_steps_writes_file_no_mint(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """When source_type=playwright AND the payload carries a valid
    test_run_id AND the normalizer returns step-based content, the
    helper writes the spec file under
    ``artifacts/{test_run_id}/test-specs/{test_run_id}_test_spec.md``.
    Crucially, it must NOT mint a new ``test_run_id`` — the caller's
    value is reused verbatim."""
    payload = _playwright_payload(
        steps_text=(
            "Step 1: Navigate to https://example.com/.\n"
            "Step 2: Click the login button."
        ),
        test_run_id="2026-10-07-14-00-00",
    )
    task = _make_task(payload=payload)

    _persist_test_spec_from_parts(task)

    assert len(no_filesystem_writes) == 1
    spec_path, content = no_filesystem_writes[0]
    # test_run_id is reused on both the folder path and the file name.
    assert "2026-10-07-14-00-00" in spec_path
    assert spec_path.endswith("2026-10-07-14-00-00_test_spec.md")
    assert "Step 1: Navigate" in content
    assert content.rstrip().endswith("END TASK")
    # Payload's test_run_id is unchanged (no mint).
    assert payload["test_run_id"] == "2026-10-07-14-00-00"


def test_playwright_with_valid_steps_reads_test_run_id_from_task_row(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """When the payload omits ``test_run_id`` but the task row carries
    it, the row's value is reused — the standard resolve_test_run_id
    precedence (task row > payload > metadata)."""
    payload = _playwright_payload(test_run_id=None)  # payload has no ID
    task = _make_task(
        payload=payload,
        test_run_id="2026-10-06-10-00-00",  # row has ID
    )
    _persist_test_spec_from_parts(task)
    assert len(no_filesystem_writes) == 1
    spec_path, _ = no_filesystem_writes[0]
    assert "2026-10-06-10-00-00" in spec_path


# =============================================================================
# Strict gate — Playwright + missing test_run_id is a defensive warning
# =============================================================================


def test_playwright_with_missing_test_run_id_logs_warning_and_skips(
    no_filesystem_writes: list[tuple[str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When Playwright is declared but neither the task row nor the
    payload carries a ``test_run_id``, the helper logs a warning and
    returns without writing — the orchestrator-level resolver should
    have minted at ingress, so a missing ID here signals a bypass."""
    import logging

    import services.task_executor as te

    caplog.set_level(logging.WARNING, logger=te.log.name)

    payload = _playwright_payload(test_run_id=None)
    task = _make_task(payload=payload, test_run_id=None)

    _persist_test_spec_from_parts(task)

    assert no_filesystem_writes == []
    assert any(
        "_persist_test_spec_from_parts.missing_test_run_id" in r.message
        for r in caplog.records
    )


# =============================================================================
# Strict gate — malformed Playwright payload raises INPUT_REQUIRED
# =============================================================================


def test_playwright_with_no_steps_raises_malformed_playwright_test_spec(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """When source_type=playwright but the parts carry no step-based
    content (e.g. the ADO test case has no ``steps[]``, or the text
    part is pure prose with no ``Step N:`` lines), the helper raises
    ``TaskInputRequiredSignal(reason_code="malformed_playwright_test_spec")``
    so the task row transitions to ``input_required`` and the client
    can resend corrected content."""
    payload: dict[str, Any] = {
        "test_run_id": "2026-10-07-14-00-00",
        "parts": [
            {"text": "Run this test"},
            {
                "text": "This is just prose without any step syntax.",
                "mediaType": "text/markdown",
                "metadata": {"source_type": "playwright"},
            },
        ],
    }
    task = _make_task(payload=payload)

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        _persist_test_spec_from_parts(task)

    sig = exc_info.value
    assert sig.reason_code == "malformed_playwright_test_spec"
    assert isinstance(sig.question_text, str) and sig.question_text.strip()
    assert sig.reason_data is not None
    assert sig.reason_data.get("source_type") == "playwright"
    assert sig.reason_data.get("parts_count") == 2
    # The file write must NOT have happened on the error path.
    assert no_filesystem_writes == []


def test_playwright_with_empty_ado_steps_raises_malformed_playwright_test_spec(
    no_filesystem_writes: list[tuple[str, str]],
) -> None:
    """An ADO test case shape with ``test_cases[]`` but all steps empty
    also surfaces as malformed — the normalizer returns None for empty
    step arrays."""
    payload: dict[str, Any] = {
        "test_run_id": "2026-10-07-14-00-00",
        "parts": [
            {"text": "Run this test"},
            {
                "data": {
                    "test_cases": [
                        {"name": "TC01", "steps": []},
                    ],
                },
                "mediaType": "application/vnd.azure.devops.testcase+json",
                "metadata": {"source_type": "playwright"},
            },
        ],
    }
    task = _make_task(payload=payload)

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        _persist_test_spec_from_parts(task)

    assert exc_info.value.reason_code == "malformed_playwright_test_spec"
    assert no_filesystem_writes == []
