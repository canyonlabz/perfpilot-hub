"""Parent SSE stream lifetime: close only on the subscribed task terminal.

Child specialist events are forwarded onto the parent queue with the
child ``task_id`` unchanged. ``sse_event_closes_stream`` must keep the
parent subscription open through those frames and close only when the
subscribed task itself is terminal.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from a2a.server.helpers.sse import sse_event_closes_stream
from services.subscriptions import TaskEvent
from stores.task_store import TERMINAL_STATUSES

PARENT_ID = UUID("92147b63-f31b-4d84-9e85-659465e5d62b")
CHILD_ID = UUID("938835de-e53e-495a-8c1a-19080384caa4")


def _event(task_id, status: str, **overrides) -> TaskEvent:
    base = dict(
        task_id=str(task_id),
        session_id=None,
        external_session_id=None,
        agent_name="orchestrator",
        status=status,
    )
    base.update(overrides)
    return TaskEvent(**base)


@pytest.mark.parametrize("status", list(TERMINAL_STATUSES))
def test_child_terminal_does_not_close_parent_stream(status: str) -> None:
    event = _event(CHILD_ID, status)
    assert sse_event_closes_stream(event, PARENT_ID) is False


@pytest.mark.parametrize("status", list(TERMINAL_STATUSES))
def test_subscribed_task_terminal_closes_stream(status: str) -> None:
    event = _event(PARENT_ID, status)
    assert sse_event_closes_stream(event, PARENT_ID) is True


@pytest.mark.parametrize(
    "status",
    ["running", "pending", "input_required", "auth_required"],
)
def test_non_terminal_never_closes_even_for_subscribed_task(status: str) -> None:
    event = _event(PARENT_ID, status)
    assert sse_event_closes_stream(event, PARENT_ID) is False


def test_child_completed_then_parent_completed_matches_smoke_sequence() -> None:
    """Playwright smoke last frame was child COMPLETED; parent completed later."""
    child_done = _event(CHILD_ID, "completed")
    parent_working = _event(PARENT_ID, "running", progress="waiting_for_specialists")
    parent_done = _event(PARENT_ID, "completed")

    assert sse_event_closes_stream(child_done, PARENT_ID) is False
    assert sse_event_closes_stream(parent_working, PARENT_ID) is False
    assert sse_event_closes_stream(parent_done, PARENT_ID) is True


def test_uuid_and_str_subscribed_ids_compare_equal() -> None:
    event = _event(PARENT_ID, "completed")
    assert sse_event_closes_stream(event, PARENT_ID) is True
    assert sse_event_closes_stream(event, str(PARENT_ID)) is True


def test_missing_event_task_id_does_not_close() -> None:
    event = SimpleNamespace(status="completed", task_id=None)
    assert sse_event_closes_stream(event, PARENT_ID) is False


def test_subscribe_to_child_still_closes_on_that_child() -> None:
    """A client subscribed to the child task must still see it close."""
    event = _event(CHILD_ID, "completed")
    assert sse_event_closes_stream(event, CHILD_ID) is True
    assert sse_event_closes_stream(event, uuid4()) is False
