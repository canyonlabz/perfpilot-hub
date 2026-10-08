"""Tests for the A2A v1 SSE ``TaskStatusUpdateEvent`` emission on
``input_required`` status.

Covers ``a2a.server.v1_helpers._task_event_to_a2a_v1_sse``. The function
converts a ``TaskEvent`` into an A2A v1 SSE ``StreamResponse`` payload:

  * Non-``input_required`` events render as a plain
    ``TaskStatusUpdateEvent`` with no attached ``TaskStatus.message``.
    (Regression guard — the Phase 3 changes must not alter the shape of
    terminal or progress events.)
  * ``input_required`` events render with ``TaskStatus.state ==
    "TASK_STATE_INPUT_REQUIRED"`` and ``TaskStatus.message`` carrying
    a two-part A2A ``Message`` (text + data) with ``role=ROLE_AGENT``.

The function is pure (no I/O, no DB); tests feed a crafted
``TaskEvent`` and inspect the returned dict directly.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a.server.v1_helpers import _task_event_to_a2a_v1_sse
from services.task_executor import TaskEvent


# =============================================================================
# Helpers
# =============================================================================


def _make_event(status: str, **overrides: Any) -> TaskEvent:
    """Build a ``TaskEvent`` with sensible defaults for SSE emission tests."""
    base: dict[str, Any] = dict(
        task_id="task-1",
        session_id=None,
        external_session_id=None,
        agent_name="orchestrator",
        status=status,
    )
    base.update(overrides)
    return TaskEvent(**base)


def _status_update(payload: dict) -> dict:
    """Extract the inner ``statusUpdate`` object from a StreamResponse dict."""
    return payload["statusUpdate"]


def _task_status(payload: dict) -> dict:
    return _status_update(payload)["status"]


# =============================================================================
# input_required events — new shape per A2A §4.1.3 + §4
# =============================================================================


def test_sse_status_update_has_task_state_input_required_enum() -> None:
    """A2A §4.1.3: the state enum must be ``TASK_STATE_INPUT_REQUIRED``
    exactly — custom spellings would break conformant clients."""
    event = _make_event(
        status="input_required",
        input_required_text="I need a test_run_id to continue.",
        input_required_reason_code="missing_test_run_id",
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    assert _task_status(payload)["state"] == "TASK_STATE_INPUT_REQUIRED"


def test_sse_status_update_includes_task_and_context_ids() -> None:
    event = _make_event(
        status="input_required",
        input_required_text="q",
        input_required_reason_code="missing_test_run_id",
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    status_update = _status_update(payload)
    assert status_update["taskId"] == "task-1"
    assert status_update["contextId"] == "ctx-1"


def test_sse_status_message_contains_text_and_data_parts() -> None:
    """TaskStatus.message carries a two-part A2A Message:
      * parts[0]: text (text/plain) — human-readable prompt
      * parts[1]: data (application/json) — machine-readable reason
    """
    event = _make_event(
        status="input_required",
        input_required_text="Please share your test_run_id.",
        input_required_reason_code="missing_test_run_id",
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    message = _task_status(payload)["message"]
    assert message["role"] == "ROLE_AGENT"
    assert len(message["parts"]) == 2

    text_part, data_part = message["parts"]
    assert text_part["text"] == "Please share your test_run_id."
    assert text_part["mediaType"] == "text/plain"
    assert "data" in data_part
    assert data_part["mediaType"] == "application/json"


def test_sse_data_part_contains_reason_code() -> None:
    event = _make_event(
        status="input_required",
        input_required_text="q",
        input_required_reason_code="missing_test_run_id",
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    data_part = _task_status(payload)["message"]["parts"][1]
    assert data_part["data"]["reasonCode"] == "missing_test_run_id"


def test_sse_data_part_merges_input_required_data() -> None:
    """Any extra keys on ``input_required_data`` are merged into the
    data part alongside ``reasonCode`` (e.g. candidate IDs for the UI)."""
    event = _make_event(
        status="input_required",
        input_required_text="q",
        input_required_reason_code="missing_test_run_id",
        input_required_data={
            "candidate_test_run_ids": [
                "2026-10-05-10-00-00",
                "2026-10-06-10-00-00",
            ],
        },
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    data = _task_status(payload)["message"]["parts"][1]["data"]
    assert data["reasonCode"] == "missing_test_run_id"
    assert data["candidate_test_run_ids"] == [
        "2026-10-05-10-00-00",
        "2026-10-06-10-00-00",
    ]


def test_sse_data_part_reason_code_cannot_be_shadowed_by_input_required_data() -> None:
    """A malicious / buggy caller putting ``reasonCode`` into
    ``input_required_data`` must not be able to overwrite the
    authoritative value coming from ``input_required_reason_code``."""
    event = _make_event(
        status="input_required",
        input_required_text="q",
        input_required_reason_code="missing_test_run_id",
        input_required_data={"reasonCode": "spoofed"},
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    data = _task_status(payload)["message"]["parts"][1]["data"]
    assert data["reasonCode"] == "missing_test_run_id"


def test_sse_message_carries_task_and_context_ids() -> None:
    """The embedded ``Message`` also echoes ``taskId`` + ``contextId``
    so clients can route the message to the right task tab without
    re-parsing the outer envelope."""
    event = _make_event(
        status="input_required",
        input_required_text="q",
        input_required_reason_code="missing_test_run_id",
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    message = _task_status(payload)["message"]
    assert message["taskId"] == "task-1"
    assert message["contextId"] == "ctx-1"


def test_sse_input_required_without_text_omits_message() -> None:
    """Defensive: a bare ``input_required`` event (no text) must not
    produce an invalid Message with empty parts. The emitter omits the
    whole TaskStatus.message block instead."""
    event = _make_event(status="input_required")
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    task_status = _task_status(payload)
    assert task_status["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert "message" not in task_status


# =============================================================================
# Regression — other statuses are unchanged by the Phase 3 additions
# =============================================================================


def test_sse_terminal_completed_status_still_emits_as_today() -> None:
    event = _make_event(status="completed", result={"reply_text": "done"})
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    task_status = _task_status(payload)
    assert task_status["state"] == "TASK_STATE_COMPLETED"
    assert "message" not in task_status


def test_sse_terminal_failed_status_still_emits_as_today() -> None:
    event = _make_event(status="failed", error={"message": "boom"})
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    task_status = _task_status(payload)
    assert task_status["state"] == "TASK_STATE_FAILED"
    assert "message" not in task_status


def test_sse_running_status_still_emits_as_today() -> None:
    event = _make_event(status="running", progress="invoking_llm")
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    status_update = _status_update(payload)
    assert _task_status(payload)["state"] == "TASK_STATE_WORKING"
    # Progress still goes into statusUpdate.metadata.progress.
    assert status_update["metadata"]["progress"] == "invoking_llm"
    # No embedded message on running events.
    assert "message" not in _task_status(payload)


def test_sse_running_status_input_required_fields_ignored() -> None:
    """If a non-input_required event happens to have populated
    ``input_required_*`` fields (defensive case — should never happen
    in practice), the SSE emitter must ignore them."""
    event = _make_event(
        status="running",
        input_required_text="should be ignored",
        input_required_reason_code="should_be_ignored",
    )
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")

    task_status = _task_status(payload)
    assert task_status["state"] == "TASK_STATE_WORKING"
    assert "message" not in task_status


@pytest.mark.parametrize(
    "perfpilot_status,expected_a2a",
    [
        ("pending", "TASK_STATE_SUBMITTED"),
        ("running", "TASK_STATE_WORKING"),
        ("completed", "TASK_STATE_COMPLETED"),
        ("failed", "TASK_STATE_FAILED"),
        ("cancelled", "TASK_STATE_CANCELED"),
        ("input_required", "TASK_STATE_INPUT_REQUIRED"),
        ("rejected", "TASK_STATE_REJECTED"),
        ("auth_required", "TASK_STATE_AUTH_REQUIRED"),
    ],
)
def test_sse_status_mapping_covers_all_a2a_states(
    perfpilot_status: str, expected_a2a: str,
) -> None:
    """Pins the full PerfPilot -> A2A TaskState mapping emitted via
    the SSE converter so a drift in ``perfpilot_status_to_a2a`` or in
    the ``TaskState`` enum trips the suite."""
    event = _make_event(status=perfpilot_status)
    payload = _task_event_to_a2a_v1_sse(event, context_id="ctx-1")
    assert _task_status(payload)["state"] == expected_a2a
