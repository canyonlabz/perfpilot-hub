"""SSE stream-lifetime helpers shared by the JSON-RPC and REST bindings.

Child specialist events are forwarded onto a parent subscriber queue
with the child's ``task_id`` unchanged. The stream must stay open until
the *subscribed* task itself is terminal.
"""

from __future__ import annotations

from typing import Any

from stores import task_store


def sse_event_closes_stream(event: Any, subscribed_task_id: Any) -> bool:
    """Return True when ``event`` should end the SSE subscription.

    Proxied child terminals (``completed`` / ``failed`` / ``cancelled`` /
    ``rejected``) keep the child's ``task_id``. Those frames are still
    yielded to the client; they must not close a parent subscription.

    Interrupted states (``input_required``, ``auth_required``) are not
    terminal and never close the stream.

    Args:
        event: A ``TaskEvent`` (or any object with ``status`` / ``task_id``).
        subscribed_task_id: The task id the client subscribed to (UUID or str).

    Returns:
        True only when ``event.status`` is terminal **and** ``event.task_id``
        matches ``subscribed_task_id``.
    """
    status = getattr(event, "status", None)
    if status not in task_store.TERMINAL_STATUSES:
        return False
    event_task_id = getattr(event, "task_id", None)
    if event_task_id is None or subscribed_task_id is None:
        return False
    return str(event_task_id) == str(subscribed_task_id)
