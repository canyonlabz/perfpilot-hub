"""A2A §6.3 multi-turn resume classifier.

When an A2A client wants to answer a question the agent raised via
``TASK_STATE_INPUT_REQUIRED`` (A2A §4.1.3), it sends a new
``message/send`` or ``message/stream`` request whose ``message.taskId``
and ``message.contextId`` match the paused task. This module classifies
the inbound body into one of three outcomes:

  * ``pass_through`` — no ``_a2a_v1_resume_task_id`` on the body; the
    caller proceeds with the normal new-task flow (``create_task`` then
    ``execute_task``).
  * ``resumed``      — the task was found, is in ``input_required``,
    the ``contextId`` matches, and the resume message was appended.
    The caller returns the task to the client (as ``Task`` 202 for REST
    or as a JSON-RPC success / SSE opening frame for streaming) and
    schedules ``task_executor.execute_task(task.task_id)``.
  * ``error``        — validation failed. The caller translates
    ``ResumeResult`` fields into its own protocol binding error shape
    (REST ``google.rpc.Status`` or JSON-RPC error).

This module does NOT re-enqueue the task — the caller handles
subscribe-before-enqueue sequencing (which matters for the streaming
ingress paths where the SSE subscription must register before the task
re-starts executing).

Dependency direction: this module imports from ``stores/``, ``a2a/shared/``,
and ``a2a/server/v1_helpers``. It is not imported by any ``core/``,
``stores/``, or ``services/`` module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from a2a.shared.models import (
    A2A_ERROR_NAMES,
    A2A_ERROR_TASK_NOT_FOUND,
    A2A_ERROR_UNSUPPORTED_OPERATION,
    a2a_timestamp,
)
from stores import task_store

log = logging.getLogger(__name__)


# =============================================================================
# ResumeResult
# =============================================================================
# ``kind`` string tags so downstream code can switch without importing the
# dataclass. HTTP status codes map to the google.rpc.Status table so REST
# callers can render the error directly.

RESUME_KIND_PASS_THROUGH = "pass_through"
RESUME_KIND_RESUMED = "resumed"
RESUME_KIND_ERROR = "error"

# Error-code string tags (not JSON-RPC integer codes). The caller
# translates these into the protocol-specific error builder:
#   TASK_NOT_FOUND         → task_not_found_error (JSON-RPC)
#                          → http_not_found       (REST)
#   UNSUPPORTED_OPERATION  → unsupported_operation_error (JSON-RPC)
#                          → http_bad_request              (REST)
#   INVALID_PARAMS         → jsonrpc_error JSONRPC_INVALID_PARAMS (JSON-RPC)
#                          → http_bad_request                       (REST)
RESUME_ERROR_TASK_NOT_FOUND = "TASK_NOT_FOUND"
RESUME_ERROR_UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
RESUME_ERROR_INVALID_PARAMS = "INVALID_PARAMS"


@dataclass(frozen=True)
class ResumeResult:
    """Discriminated outcome of a resume-flow classification.

    Fields are populated per ``kind``:

      * ``pass_through``: all other fields are ``None``.
      * ``resumed``: ``task`` carries the refreshed ``AgentTask`` row
                     (status is now ``running``). All other fields are
                     ``None``.
      * ``error``: ``error_code``, ``error_message`` are set;
                   ``error_metadata`` is populated with ``taskId`` and
                   any detail the caller should surface; ``http_status``
                   is the HTTP status code for REST callers.
    """

    kind: str
    task: Optional[task_store.AgentTask] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_metadata: Optional[dict[str, Any]] = None
    http_status: Optional[int] = None


# =============================================================================
# Classifier
# =============================================================================


def _error(
    *,
    code: str,
    message: str,
    metadata: dict[str, Any],
    http_status: int,
) -> ResumeResult:
    """Build an error ``ResumeResult`` with the standard field set."""
    return ResumeResult(
        kind=RESUME_KIND_ERROR,
        error_code=code,
        error_message=message,
        error_metadata=metadata,
        http_status=http_status,
    )


def _build_resume_message_part(body: dict) -> dict:
    """Compose the message dict appended to ``payload["resume_messages"]``.

    The orchestrator picks these up on re-execution and appends each one
    (newest-last) to its user-message prompt. Everything the resolver
    needs to re-classify (new user text, new parts, metadata, timestamp)
    is captured so later turns are self-contained.
    """
    part: dict[str, Any] = {
        "role": "ROLE_USER",
        "received_at": a2a_timestamp(),
    }
    msg = body.get("message")
    if isinstance(msg, str) and msg.strip():
        part["message_text"] = msg
    parts = body.get("parts")
    if isinstance(parts, list) and parts:
        part["parts"] = parts
    metadata = body.get("metadata")
    if isinstance(metadata, dict) and metadata:
        part["metadata"] = metadata
    return part


async def try_handle_resume(body: dict) -> ResumeResult:
    """Classify the inbound body as pass-through, resumed, or error.

    On ``resumed`` outcome the resume message has already been appended
    to ``payload.resume_messages`` and the task status has been
    transitioned from ``input_required`` back to ``running`` (via
    ``task_store.append_resume_message``). The caller is responsible for
    scheduling ``task_executor.execute_task(task.task_id)`` and
    (for streaming paths) subscribing to the task's event queue
    beforehand.

    Args:
        body: The normalized A2A v1 request body (output of
            ``_normalize_a2a_v1_body``). Must carry
            ``_a2a_v1_resume_task_id`` for the resume path to engage;
            otherwise returns ``pass_through`` so the caller proceeds
            with the standard new-task flow.

    Returns:
        A ``ResumeResult`` whose ``kind`` is one of
        ``pass_through`` / ``resumed`` / ``error``.
    """
    resume_task_id = body.get("_a2a_v1_resume_task_id")
    if not isinstance(resume_task_id, str) or not resume_task_id.strip():
        return ResumeResult(kind=RESUME_KIND_PASS_THROUGH)

    # Parse the task ID. A malformed UUID is a client error.
    try:
        task_uuid = UUID(resume_task_id)
    except (ValueError, TypeError):
        return _error(
            code=RESUME_ERROR_INVALID_PARAMS,
            message="Invalid parameters",
            metadata={
                "taskId": resume_task_id,
                "detail": f"Malformed task id: {resume_task_id}",
            },
            http_status=400,
        )

    task = await task_store.get_task(task_uuid)
    if task is None:
        return _error(
            code=RESUME_ERROR_TASK_NOT_FOUND,
            message=A2A_ERROR_NAMES.get(
                A2A_ERROR_TASK_NOT_FOUND, "TaskNotFoundError",
            ),
            metadata={"taskId": resume_task_id, "timestamp": a2a_timestamp()},
            http_status=404,
        )

    # The task must be awaiting input (A2A §4.1.3). Any other status
    # (running, terminal, auth_required) is a wrong-state error.
    if task.status != "input_required":
        return _error(
            code=RESUME_ERROR_UNSUPPORTED_OPERATION,
            message=A2A_ERROR_NAMES.get(
                A2A_ERROR_UNSUPPORTED_OPERATION, "UnsupportedOperationError",
            ),
            metadata={
                "taskId": resume_task_id,
                "operation": "message/send",
                "detail": (
                    f"Task is not awaiting input (current state: {task.status}); "
                    "resume is only valid for tasks in 'input_required'."
                ),
                "currentState": task.status,
            },
            http_status=400,
        )

    # A2A §3.4.3: if the client supplies a contextId, it must match the
    # task's bound contextId. Mismatched contextIds are an INVALID_PARAMS
    # error because the resume is being routed to the wrong conversation.
    incoming_context_id = body.get("_a2a_v1_context_id")
    task_context_id = None
    if isinstance(task.payload, dict):
        task_context_id = task.payload.get("_a2a_v1_context_id")
    if (
        isinstance(incoming_context_id, str)
        and incoming_context_id
        and isinstance(task_context_id, str)
        and task_context_id
        and incoming_context_id != task_context_id
    ):
        return _error(
            code=RESUME_ERROR_INVALID_PARAMS,
            message="Invalid parameters",
            metadata={
                "taskId": resume_task_id,
                "detail": "Mismatching contextId and taskId",
                "providedContextId": incoming_context_id,
                "expectedContextId": task_context_id,
            },
            http_status=400,
        )

    # Append the resume message and transition the task row back to
    # running. ``append_resume_message`` is a conditional UPDATE scoped
    # to WHERE status = 'input_required', so a concurrent duplicate
    # resume returns False and we surface it as a wrong-state error.
    message_part = _build_resume_message_part(body)
    appended = await task_store.append_resume_message(task_uuid, message_part)
    if not appended:
        # Race: another resume request already transitioned this task.
        return _error(
            code=RESUME_ERROR_UNSUPPORTED_OPERATION,
            message=A2A_ERROR_NAMES.get(
                A2A_ERROR_UNSUPPORTED_OPERATION, "UnsupportedOperationError",
            ),
            metadata={
                "taskId": resume_task_id,
                "operation": "message/send",
                "detail": (
                    "Task was no longer in 'input_required' when the resume "
                    "message arrived — a concurrent resume may have won the race."
                ),
            },
            http_status=400,
        )

    # Re-read so the returned task reflects the new status + payload
    # (resume_messages appended, status=running, result cleared).
    refreshed = await task_store.get_task(task_uuid)
    if refreshed is None:
        # Vanishingly rare — the row was deleted between append and
        # re-read. Treat as not-found.
        return _error(
            code=RESUME_ERROR_TASK_NOT_FOUND,
            message=A2A_ERROR_NAMES.get(
                A2A_ERROR_TASK_NOT_FOUND, "TaskNotFoundError",
            ),
            metadata={"taskId": resume_task_id, "timestamp": a2a_timestamp()},
            http_status=404,
        )

    log.info(
        "a2a.resume.appended",
        extra={
            "task_id": resume_task_id,
            "context_id": incoming_context_id,
            "has_message_text": "message_text" in message_part,
            "parts_count": len(message_part.get("parts", []) or []),
        },
    )
    return ResumeResult(kind=RESUME_KIND_RESUMED, task=refreshed)
