"""Orchestration wrapper around the pure ``test_run_id`` classifier.

``core.test_run_id.resolve_test_run_id_outcome`` is a pure classifier:
given a payload / user text / parts, it returns a ``ResolveResult`` with
one of four outcomes (``reuse``, ``mint``, ``skip_mint``, ``reject``)
and never touches the DB. This module wraps that classifier with the
service-layer side effects the orchestrator needs:

  * Pre-call: enrich with the thread's prior ``test_run_id`` values so
    the ``reject`` branch can surface candidates to the client.
  * Post-call MINT: persist the fresh ID onto the task row so later
    turns on the same thread reuse the value.
  * Post-call REJECT: raise ``TaskInputRequiredSignal`` carrying a
    server-authored question (from
    ``services.helpers.input_required_msgs``) plus the structured
    reasonCode. ``execute_task`` catches the signal and transitions the
    task row to ``input_required`` (A2A §4.1.3 TASK_STATE_INPUT_REQUIRED).

Dependency direction: this module imports from ``core/``, ``stores/``,
and its sibling helpers. It is NOT imported by ``core/`` or ``stores/``.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.task_signals import TaskInputRequiredSignal
from core.test_run_id import (
    OUTCOME_MINT,
    OUTCOME_REJECT,
    REASON_MISSING_TEST_RUN_ID,
    resolve_test_run_id_outcome,
)
from services.helpers.input_required_msgs import compose_input_required_question
from stores import task_store

log = logging.getLogger(__name__)


async def resolve_test_run_id_or_signal_input_required(
    task: task_store.AgentTask,
    user_message: Optional[str],
    thread_id: Optional[str],
) -> Optional[str]:
    """Resolve ``test_run_id`` for the orchestrator task or signal INPUT_REQUIRED.

    Thin wrapper around ``core.test_run_id.resolve_test_run_id_outcome``
    that enriches the resolver call with the thread's prior
    ``test_run_id`` candidates (so the reject reasonData can offer the
    client a short pick-list) and translates the four outcomes into
    orchestrator-friendly behavior:

      * ``reuse``     — return the existing ID; caller propagates it.
      * ``mint``      — return the minted ID; persist it to the task
                        row so later turns see it via
                        ``list_tasks_for_thread``.
      * ``skip_mint`` — return ``None``; the comparison-report path
                        receives its ID later from the MCP tool result
                        and persists it via ``set_test_run_id``.
      * ``reject``    — raise ``TaskInputRequiredSignal`` with the
                        composed question text and reasonCode.

    Args:
        task: The orchestrator task currently executing.
        user_message: The user's prose message (if any) for the current
            turn. Used for the Web-UI prose-heuristic fallback and the
            labeled-ID extraction path.
        thread_id: The resolved A2A thread ID, or ``None`` for callers
            that bypass the thread resolver. When present, the thread's
            prior tasks are inspected for distinct ``test_run_id``
            values to include as candidates on reject.

    Returns:
        The authoritative ``test_run_id`` for reuse/mint, or ``None``
        for the skip_mint case.

    Raises:
        TaskInputRequiredSignal: when the resolver outcome is ``reject``.
    """
    inbound_payload = task.payload if isinstance(task.payload, dict) else None
    if inbound_payload is not None and getattr(task, "test_run_id", None):
        inbound_payload.setdefault("test_run_id", task.test_run_id)

    # Collect distinct test_run_id values from prior tasks on this
    # thread. The resolver passes these back via reason_data on reject
    # so the client UI can show a dropdown of known candidates.
    thread_candidates: list[str] = []
    if thread_id:
        try:
            thread_tasks = await task_store.list_tasks_for_thread(
                thread_id, limit=50,
            )
            seen: set[str] = set()
            for t in thread_tasks:
                tr = getattr(t, "test_run_id", None)
                if isinstance(tr, str) and tr and tr not in seen:
                    seen.add(tr)
                    thread_candidates.append(tr)
        except Exception:
            log.exception(
                "resolve_test_run_id_or_signal: list_tasks_for_thread "
                "failed; continuing without candidates"
            )

    outcome = resolve_test_run_id_outcome(
        payload=inbound_payload,
        user_text=user_message,
        parts=(inbound_payload.get("parts") if inbound_payload else None),
        thread_test_run_ids=thread_candidates or None,
    )

    if outcome.outcome == OUTCOME_REJECT:
        reason_code = outcome.reason_code or REASON_MISSING_TEST_RUN_ID
        question_text = compose_input_required_question(
            reason_code=reason_code,
            reason_data=outcome.reason_data,
        )
        raise TaskInputRequiredSignal(
            reason_code=reason_code,
            question_text=question_text,
            reason_data=outcome.reason_data,
        )

    # Persist minted IDs onto the task row so subsequent turns on the
    # same thread can reuse the value. REUSE already has the ID on the
    # row; SKIP_MINT writes back later from the MCP tool result.
    if outcome.outcome == OUTCOME_MINT and outcome.id:
        try:
            await task_store.set_test_run_id(task.task_id, outcome.id)
        except Exception:
            log.exception(
                "resolve_test_run_id_or_signal: set_test_run_id failed; "
                "continuing with ContextVar propagation only"
            )

    return outcome.id
