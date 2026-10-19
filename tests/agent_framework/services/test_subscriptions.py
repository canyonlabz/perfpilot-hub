"""Unit tests for ``services.subscriptions`` (in-process SSE bus).

Covers subscribe/unsubscribe, direct broadcast delivery, parent-task
event proxying, and silent drop on a full subscriber queue. No database
or agent runtime is required.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from services.subscriptions import (
    TaskEvent,
    _parent_task_cache,
    _subscribers,
    broadcast,
    register_parent_task,
    subscribe,
    unsubscribe,
)


@pytest.fixture(autouse=True)
def _clear_subscription_state():
    """Isolate bus state between tests (module-level dicts)."""
    _subscribers.clear()
    _parent_task_cache.clear()
    yield
    _subscribers.clear()
    _parent_task_cache.clear()


def _event(*, task_id: str, agent_name: str = "orchestrator", status: str = "running") -> TaskEvent:
    return TaskEvent(
        task_id=task_id,
        session_id=str(uuid4()),
        external_session_id="ext-session",
        agent_name=agent_name,
        status=status,
        progress="started",
    )


@pytest.mark.asyncio
async def test_subscribe_receives_broadcast_event() -> None:
    task_id = uuid4()
    queue = await subscribe(task_id)

    event = _event(task_id=str(task_id))
    await broadcast(task_id, event)

    received = queue.get_nowait()
    assert received is event
    assert received.status == "running"


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    task_id = uuid4()
    queue = await subscribe(task_id)
    await unsubscribe(task_id, queue)

    await broadcast(task_id, _event(task_id=str(task_id)))

    assert queue.empty()
    assert task_id not in _subscribers


@pytest.mark.asyncio
async def test_unsubscribe_is_idempotent() -> None:
    task_id = uuid4()
    queue = await subscribe(task_id)
    await unsubscribe(task_id, queue)
    await unsubscribe(task_id, queue)  # second call must not raise


@pytest.mark.asyncio
async def test_broadcast_proxies_to_parent_subscribers() -> None:
    parent_id = uuid4()
    child_id = uuid4()
    parent_queue = await subscribe(parent_id)
    child_queue = await subscribe(child_id)
    register_parent_task(child_id, parent_id)

    event = _event(task_id=str(child_id), agent_name="execution-agent", status="running")
    await broadcast(child_id, event)

    assert child_queue.get_nowait() is event
    proxied = parent_queue.get_nowait()
    assert proxied is event
    assert proxied.task_id == str(child_id)
    assert proxied.agent_name == "execution-agent"


@pytest.mark.asyncio
async def test_broadcast_drops_silently_when_queue_full() -> None:
    task_id = uuid4()
    # Bypass subscribe() so we can set maxsize=1 and fill it.
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    _subscribers[task_id] = [queue]
    queue.put_nowait(_event(task_id=str(task_id), status="running"))

    # Must not raise even though the queue is full.
    await broadcast(task_id, _event(task_id=str(task_id), status="completed"))

    assert queue.qsize() == 1
    assert queue.get_nowait().status == "running"


def test_task_event_to_sse_data_is_json() -> None:
    event = _event(task_id=str(uuid4()), status="completed")
    payload = event.to_sse_data()
    assert '"status": "completed"' in payload or '"status":"completed"' in payload
    assert event.timestamp  # auto-populated in __post_init__
