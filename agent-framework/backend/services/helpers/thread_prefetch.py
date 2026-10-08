"""Thread-scoped prefetch helpers for the ``test_run_id`` resolver.

A single conversation thread may span many tasks. When a new turn
arrives, both the A2A ingress resolver (orchestrator-side) and the
Web-UI resolver (``agui_server``) benefit from the list of distinct
``test_run_id`` values previously minted or persisted on that thread:

  * The ``reject`` outcome packages the list into ``reason_data`` so
    the client UI can offer a short pick-list of known candidates.
  * The Web-UI resolver applies an implicit-REUSE policy when the
    thread has exactly one known ``test_run_id`` and no other signal.
  * Specialists called via ``delegate_to_specialist`` on comparison
    intents can consume the list via ``_caller_identity``.

Dependency direction: this helper imports from ``stores/`` only. It is
not imported by any ``core/`` or ``stores/`` module.
"""

from __future__ import annotations

import logging
from typing import Optional

from stores import task_store

log = logging.getLogger(__name__)


async def list_known_test_run_ids_for_thread(
    thread_id: Optional[str],
    *,
    limit: int = 50,
) -> list[str]:
    """Return distinct ``test_run_id`` values from prior tasks on a thread.

    The result preserves first-seen order from ``list_tasks_for_thread``
    (which returns tasks newest-first), so the caller sees the most
    recently used ``test_run_id`` as the first entry — the natural
    "default" when applying an implicit-REUSE policy.

    Args:
        thread_id: The conversation thread to scan. ``None`` short-
            circuits to an empty list (no thread → no history to mine).
        limit: Maximum number of tasks to inspect. Defaults to 50 —
            enough to capture every ``test_run_id`` on a reasonably
            long-running thread while bounding the query cost.

    Returns:
        A list of distinct, non-empty ``test_run_id`` strings in
        newest-first order. Returns ``[]`` when ``thread_id`` is
        falsy, when the thread has no tasks, when no task carries a
        ``test_run_id``, or when the DB lookup fails (errors are
        logged, not raised, so a prefetch outage never blocks the
        resolver from running).
    """
    if not thread_id:
        return []

    try:
        tasks = await task_store.list_tasks_for_thread(thread_id, limit=limit)
    except Exception:
        log.exception(
            "thread_prefetch.list_known_test_run_ids.lookup_failed",
            extra={"thread_id": thread_id, "limit": limit},
        )
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for task in tasks:
        tr = getattr(task, "test_run_id", None)
        if isinstance(tr, str) and tr and tr not in seen:
            seen.add(tr)
            ordered.append(tr)
    return ordered
