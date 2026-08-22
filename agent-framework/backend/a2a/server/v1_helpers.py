"""A2A v1.0.0 shared helper functions.

Constants, response header builders, request body normalizer, and task
converters used by both the HTTP+JSON/REST and JSON-RPC 2.0 bindings.

Extracted from ``a2a_server.py`` during the sub-module refactor to keep
the main server file focused on app creation, lifespan, and legacy
routes.

These helpers are protocol-agnostic — they produce dicts and headers
consumed by the route handlers in ``a2a_v1_routes.py``.
"""

from __future__ import annotations

from typing import Any, Optional

from services import task_executor
from stores import task_store


# =============================================================================
# Constants
# =============================================================================

A2A_V1_AGENT_NAME = "orchestrator"
"""The agent name used for all A2A v1 routes (implicit target)."""

A2A_VERSION_HEADER = "1.0"
"""Value of the ``A2A-Version`` response header."""


# =============================================================================
# Response headers
# =============================================================================


def _a2a_v1_response_headers(
    thread_headers: Optional[dict] = None,
) -> dict:
    """Build response headers for A2A v1 routes.

    Always includes ``A2A-Version: 1.0``. Thread headers are merged when
    provided (task-creating endpoints pass the result of
    ``_thread_response_headers()``).

    Args:
        thread_headers: Optional dict of thread-related headers to merge.

    Returns:
        A dict of response headers.
    """
    headers: dict = {"A2A-Version": A2A_VERSION_HEADER}
    if thread_headers is not None:
        headers.update(thread_headers)
    return headers


# =============================================================================
# Request body normalizer
# =============================================================================


def _normalize_a2a_v1_body(body: dict) -> dict:
    """Translate an A2A v1 ``SendMessageRequest`` envelope into the internal
    payload format that ``task_executor._extract_user_message_from_payload()``
    and ``a2a_parts_parser`` already understand.

    If the body is already a legacy PerfPilot payload (top-level ``message``
    string, ``text``, ``prompt``, or ``parts[]``), it is returned unchanged.

    A2A v1 structure::

        {
          "message": {
            "messageId": "...",
            "role": "ROLE_USER",
            "parts": [{"text": "Hello"}],
            "contextId": "ctx-001"
          },
          "configuration": {...},
          "metadata": {...}
        }

    Normalized internal structure::

        {
          "message": "Hello",           # first text Part -> top-level message
          "parts": [{"text": "Hello"}], # pass through for a2a_parts_parser
          "metadata": {...},            # merged from envelope + message metadata
          "_a2a_v1_envelope": {...},    # stash original envelope for audit
          "_a2a_v1_context_id": "ctx-001"
        }
    """
    from a2a.shared.models import is_a2a_v1_request

    if not is_a2a_v1_request(body):
        return body

    msg = body["message"]
    parts = msg.get("parts") or []
    context_id = msg.get("contextId") or msg.get("context_id")

    first_text = None
    normalized_parts: list[dict] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        normalized_parts.append(part)
        if first_text is None and isinstance(part.get("text"), str):
            first_text = part["text"]

    result: dict = {}

    if first_text:
        result["message"] = first_text

    if normalized_parts:
        result["parts"] = normalized_parts

    envelope_metadata = body.get("metadata")
    msg_metadata = msg.get("metadata")
    if isinstance(envelope_metadata, dict) or isinstance(msg_metadata, dict):
        merged: dict = {}
        if isinstance(msg_metadata, dict):
            merged.update(msg_metadata)
        if isinstance(envelope_metadata, dict):
            merged.update(envelope_metadata)
        if merged:
            result["metadata"] = merged

    if context_id:
        result["_a2a_v1_context_id"] = context_id

    result["_a2a_v1_envelope"] = body

    config = body.get("configuration")
    if isinstance(config, dict):
        result["_a2a_v1_configuration"] = config

    test_run_id = None
    if isinstance(envelope_metadata, dict):
        test_run_id = envelope_metadata.get("test_run_id")
    if test_run_id and isinstance(test_run_id, str):
        result["test_run_id"] = test_run_id

    return result


# =============================================================================
# Task converters
# =============================================================================


def _task_to_a2a_v1(task: task_store.AgentTask) -> dict:
    """Convert an ``AgentTask`` DB row to an A2A v1 ``Task`` response dict.

    Returns a camelCase dict suitable for JSON serialization. Uses the
    Phase 1 Pydantic models for structure and the state mapping for
    PerfPilot-to-A2A status translation.
    """
    from a2a.shared.models import (
        Task,
        TaskStatus,
        a2a_timestamp,
        perfpilot_status_to_a2a,
    )

    a2a_state = perfpilot_status_to_a2a(task.status)

    status_timestamp = a2a_timestamp()
    if task.completed_at:
        status_timestamp = task.completed_at.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{task.completed_at.microsecond // 1000:03d}Z"
    elif task.started_at:
        status_timestamp = task.started_at.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{task.started_at.microsecond // 1000:03d}Z"
    elif task.submitted_at:
        status_timestamp = task.submitted_at.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{task.submitted_at.microsecond // 1000:03d}Z"

    task_status = TaskStatus(state=a2a_state, timestamp=status_timestamp)

    context_id = None
    if isinstance(task.payload, dict):
        context_id = task.payload.get("_a2a_v1_context_id")

    artifacts = None
    artifact = _reply_text_artifact(str(task.task_id), task.result)
    if artifact is not None:
        artifacts = [artifact]

    metadata: dict[str, Any] = {
        "agentName": task.agent_name,
    }
    if task.test_run_id:
        metadata["testRunId"] = task.test_run_id
    if task.error and isinstance(task.error, dict):
        metadata["error"] = task.error

    a2a_task = Task(
        id=str(task.task_id),
        context_id=context_id,
        status=task_status,
        artifacts=artifacts,
        metadata=metadata if metadata else None,
    )

    return a2a_task.model_dump(by_alias=True, exclude_none=True)


def _reply_text_artifact(task_id: str, result: Any) -> Optional[Any]:
    """Build an A2A ``Artifact`` from ``result.reply_text`` when present."""
    from a2a.shared.models import Artifact, Part

    if not result or not isinstance(result, dict):
        return None
    reply_text = result.get("reply_text")
    if not isinstance(reply_text, str) or not reply_text.strip():
        return None
    return Artifact(
        artifact_id=f"{task_id}-result",
        parts=[Part(text=reply_text, media_type="text/plain")],
        name="Agent response",
    )


def _context_id_from_task_or_event(
    *,
    context_id: Optional[str] = None,
    task: Optional[task_store.AgentTask] = None,
) -> str:
    """Resolve A2A ``contextId`` for streaming events (required by spec)."""
    if isinstance(context_id, str) and context_id:
        return context_id
    if task is not None and isinstance(task.payload, dict):
        cid = task.payload.get("_a2a_v1_context_id")
        if isinstance(cid, str) and cid:
            return cid
    return ""


def _task_event_to_a2a_v1_sse(
    event: task_executor.TaskEvent,
    *,
    context_id: Optional[str] = None,
) -> dict:
    """Convert a ``TaskEvent`` to an A2A v1 SSE ``StreamResponse`` payload.

    Returns a dict with exactly one ``statusUpdate`` key whose value is a
    ``TaskStatusUpdateEvent`` (A2A §3.2.3 / §4.2.1). Progress strings are
    carried in ``metadata.progress`` when present.
    """
    from a2a.shared.models import (
        StreamResponse,
        TaskStatus,
        TaskStatusUpdateEvent,
        a2a_timestamp,
        perfpilot_status_to_a2a,
    )

    a2a_state = perfpilot_status_to_a2a(event.status)
    status = TaskStatus(state=a2a_state, timestamp=a2a_timestamp())
    meta: Optional[dict[str, Any]] = None
    if event.progress:
        meta = {"progress": event.progress}

    stream = StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id=event.task_id,
            context_id=_context_id_from_task_or_event(context_id=context_id),
            status=status,
            metadata=meta,
        ),
    )
    return stream.model_dump(by_alias=True, exclude_none=True)


def _task_event_to_a2a_v1_artifact_sse(
    event: task_executor.TaskEvent,
    *,
    context_id: Optional[str] = None,
) -> Optional[dict]:
    """Build an A2A ``artifactUpdate`` StreamResponse for a completed event.

    Returns ``None`` when the event is not completed or has no
    ``reply_text`` artifact. Callers must emit this immediately before
    the terminal ``statusUpdate``.
    """
    from a2a.shared.models import StreamResponse, TaskArtifactUpdateEvent

    if event.status != "completed":
        return None
    artifact = _reply_text_artifact(event.task_id, event.result)
    if artifact is None:
        return None

    stream = StreamResponse(
        artifact_update=TaskArtifactUpdateEvent(
            task_id=event.task_id,
            context_id=_context_id_from_task_or_event(context_id=context_id),
            artifact=artifact,
            last_chunk=True,
        ),
    )
    return stream.model_dump(by_alias=True, exclude_none=True)
