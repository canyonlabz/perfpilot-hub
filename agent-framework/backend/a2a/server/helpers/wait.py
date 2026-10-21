"""Shared wait helper for blocking A2A SendMessage (both bindings).

A2A ``SendMessageConfiguration.returnImmediately`` defaults to ``false``:
the server MUST wait until the task reaches a terminal state
(``completed``, ``failed``, ``cancelled``, ``rejected``) or an interrupted
state (``input_required``, ``auth_required``) before returning.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from stores import task_store

# Interrupted (resumable) states are not terminal, but a blocking
# SendMessage must still return when the server needs more input.
_INTERRUPTED_STATUSES = ("input_required", "auth_required")
SETTLED_STATUSES: frozenset[str] = frozenset(
    task_store.TERMINAL_STATUSES + _INTERRUPTED_STATUSES
)

# Re-check the DB on this interval if no event arrives (missed broadcast).
_POLL_TIMEOUT_SECONDS = 15.0


def return_immediately_from_payload(body: dict) -> bool:
    """Read ``configuration.returnImmediately`` from a (normalized) body.

    Default is ``False`` (blocking). Accepts both camelCase and snake_case
    keys. The retired ``blocking`` field is ignored.
    """
    config = body.get("_a2a_v1_configuration")
    if not isinstance(config, dict):
        config = body.get("configuration")
    if not isinstance(config, dict):
        return False
    raw = config.get("returnImmediately")
    if raw is None:
        raw = config.get("return_immediately")
    return raw is True


async def wait_for_task_settled(
    task_id: UUID,
    queue: asyncio.Queue,
) -> task_store.AgentTask:
    """Block until the task is terminal or interrupted.

    Prefers in-process subscription events and falls back to a DB read
    on heartbeat timeout so a missed broadcast cannot hang forever.
    """
    task = await task_store.get_task(task_id)
    if task is not None and task.status in SETTLED_STATUSES:
        return task

    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=_POLL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            task = await task_store.get_task(task_id)
            if task is not None and task.status in SETTLED_STATUSES:
                return task
            continue
        if getattr(event, "status", None) in SETTLED_STATUSES:
            refreshed = await task_store.get_task(task_id)
            if refreshed is not None:
                return refreshed

    raise RuntimeError(f"wait_for_task_settled exited without a settled task {task_id}")


async def start_task_and_maybe_wait(
    task: task_store.AgentTask,
    task_executor: Any,
    return_immediately: bool,
) -> task_store.AgentTask:
    """Start background execution; wait only when the client is blocking."""
    if return_immediately:
        asyncio.create_task(task_executor.execute_task(task.task_id))
        return task

    queue = await task_executor.subscribe(task.task_id)
    try:
        asyncio.create_task(task_executor.execute_task(task.task_id))
        return await wait_for_task_settled(task.task_id, queue)
    finally:
        await task_executor.unsubscribe(task.task_id, queue)
