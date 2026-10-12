"""In-process signals raised by orchestrator / specialist code to drive
A2A task-lifecycle transitions that are not plain terminal success or
failure.

These are not errors in the usual sense — they carry structured
information the outer ``execute_task`` wrapper translates into a
spec-compliant task-state transition (A2A §4.1.3). Catching code
converts the signal into a DB state write plus an SSE
``TaskStatusUpdateEvent`` payload.

Currently supported:

  * ``TaskInputRequiredSignal`` — the agent cannot proceed without
    additional client input. Transitions the task to
    ``input_required`` (A2A TASK_STATE_INPUT_REQUIRED). Non-terminal:
    the client is expected to resume the task by sending a follow-up
    message carrying the same ``taskId`` + ``contextId`` (A2A §6.3
    multi-turn interaction).

Adding a new signal here is intentionally lightweight — subclass
``Exception`` directly and add an ``execute_task`` branch rather than
re-using existing error types, so the two paths stay visually distinct
in the control flow.
"""

from __future__ import annotations

from typing import Any, Optional


class TaskInputRequiredSignal(Exception):
    """Raised when a task cannot proceed without additional client input.

    The outer ``execute_task`` wrapper catches this signal and transitions
    the task row to ``input_required`` via
    ``task_store.mark_input_required`` instead of calling
    ``mark_failed``. An SSE ``TaskStatusUpdateEvent`` is then emitted
    with ``state = TASK_STATE_INPUT_REQUIRED`` and a ``TaskStatus.message``
    carrying the question text and a machine-readable ``reasonCode``.

    Attributes:
        reason_code: Short machine-readable code the client can switch
            on to drive UX (e.g. ``"missing_test_run_id"``). This value
            is also persisted on the task row's ``result`` column so
            subsequent HTTP polls see the same code.
        question_text: Human-readable prompt sent back to the client as
            the user-visible message body.
        reason_data: Optional supplementary structured payload (e.g.
            ``{"candidate_test_run_ids": [...]}``). ``None`` when the
            resolver has no additional data to include.
    """

    def __init__(
        self,
        *,
        reason_code: str,
        question_text: str,
        reason_data: Optional[dict[str, Any]] = None,
    ) -> None:
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise ValueError("reason_code must be a non-empty string")
        if not isinstance(question_text, str) or not question_text.strip():
            raise ValueError("question_text must be a non-empty string")
        if reason_data is not None and not isinstance(reason_data, dict):
            raise ValueError("reason_data must be a dict when provided")

        self.reason_code: str = reason_code.strip()
        self.question_text: str = question_text.strip()
        self.reason_data: Optional[dict[str, Any]] = reason_data

        # Compose a short ``str(exc)`` so stack-trace logs stay useful
        # without duplicating the structured attributes.
        super().__init__(f"{self.reason_code}: {self.question_text}")
