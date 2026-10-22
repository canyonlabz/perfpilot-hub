"""In-process task event subscription bus.

Owns the SSE-facing pub/sub surface used by the A2A and Web-UI servers:

  * ``TaskEvent`` — one state-transition snapshot delivered to subscribers
  * ``subscribe`` / ``unsubscribe`` — register an ``asyncio.Queue`` per task
  * ``register_parent_task`` — map specialist child → orchestrator parent
    so child events are proxied onto the parent stream
  * ``broadcast`` — push an event to a task's subscribers (and parent)

This module is intentionally free of agent-dispatch / webhook logic so
both ``services.task_executor`` and ``services.specialist_agents`` can
import from it without creating a circular dependency.

Cross-process pub/sub (Redis / Postgres LISTEN / etc.) is out of scope
for this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

log = logging.getLogger(__name__)


@dataclass
class TaskEvent:
    """One state-transition snapshot delivered to in-process subscribers.

    Mirrors the shape we send over SSE on A2A ``POST /message:stream``
    (and JSON-RPC ``SendStreamingMessage``). Includes both IDs from the
    three-ID model so SSE consumers can correlate to the broader session.

    When ``status == "input_required"``, the three ``input_required_*``
    fields carry the question text and machine-readable reason code
    that the SSE emitter composes into the A2A ``TaskStatus.message``
    (A2A §4.1.3 TASK_STATE_INPUT_REQUIRED). On any other status these
    fields are ``None`` and the SSE emitter ignores them.
    """

    task_id: str
    session_id: Optional[str]
    external_session_id: Optional[str]
    agent_name: str
    status: str
    progress: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[dict] = None
    timestamp: str = ""
    # Populated only when ``status == "input_required"``.
    input_required_text: Optional[str] = None
    input_required_reason_code: Optional[str] = None
    input_required_data: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_sse_data(self) -> str:
        """Render a JSON line suitable for an SSE ``data:`` field."""
        return json.dumps(asdict(self))


# =============================================================================
# Subscription bus (in-process)
# =============================================================================
# Each task_id maps to a list of asyncio.Queue subscribers. SSE consumers
# subscribe before submitting the task (or right after) and unsubscribe on
# disconnect. This is intentionally in-process - cross-process pub/sub is an
# Epic 4 concern (Redis / Postgres LISTEN / etc.).

_subscribers: dict[UUID, list[asyncio.Queue]] = {}
_subscribers_lock = asyncio.Lock()

# Maps child (specialist) task_id -> parent (orchestrator) task_id so that
# broadcast() can forward specialist events to parent SSE subscribers.
# Populated by the orchestrator tool loop when draining pending children.
_parent_task_cache: dict[UUID, UUID] = {}


async def subscribe(task_id: UUID) -> asyncio.Queue:
    """Register a queue for state events on ``task_id``. Returns the queue."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    async with _subscribers_lock:
        _subscribers.setdefault(task_id, []).append(queue)
    return queue


async def unsubscribe(task_id: UUID, queue: asyncio.Queue) -> None:
    """Remove a queue from the subscriber list. Safe to call twice."""
    async with _subscribers_lock:
        queues = _subscribers.get(task_id)
        if queues and queue in queues:
            queues.remove(queue)
        if queues is not None and not queues:
            _subscribers.pop(task_id, None)


def register_parent_task(child_task_id: UUID, parent_task_id: UUID) -> None:
    """Record a parent-child relationship for SSE event proxying."""
    _parent_task_cache[child_task_id] = parent_task_id


async def broadcast(task_id: UUID, event: TaskEvent) -> None:
    """Push ``event`` to every queue subscribed to ``task_id``.

    When the task has a known parent (via ``_parent_task_cache``), the event
    is also forwarded to all subscribers of the parent task so that SSE
    consumers on the orchestrator stream see specialist progress in real
    time.  The forwarded event retains the original specialist
    ``task_id`` and ``agent_name`` so the consumer can distinguish it from
    orchestrator-originated events.

    Drops silently on full queues.
    """
    parent_id = _parent_task_cache.get(task_id)

    async with _subscribers_lock:
        queues = list(_subscribers.get(task_id, ()))
        parent_queues = list(_subscribers.get(parent_id, ())) if parent_id else []

    for queue in queues:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("Dropping SSE event for task %s; subscriber queue full", task_id)

    for queue in parent_queues:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning(
                "Dropping proxied SSE event for parent task %s (child %s); queue full",
                parent_id, task_id,
            )
