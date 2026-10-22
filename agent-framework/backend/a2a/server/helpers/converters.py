"""Convert internal task rows and events into A2A v1 Task / StreamResponse dicts.

Protocol-agnostic — consumed by both REST and JSON-RPC bindings.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import task_executor
from stores import task_store

# Sentinel: REST SSE path (not JSON-RPC)
_SSE_REST = object()


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

    context_id = _payload_context_id(task.payload)

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
    """Resolve A2A ``contextId`` for streaming events (required by spec).

    Never returns an empty string. Callers must have a real conversation
    id from the inbound message or the stored task payload.
    """
    if isinstance(context_id, str) and context_id.strip():
        return context_id.strip()
    if task is not None:
        cid = _payload_context_id(task.payload)
        if cid is not None:
            return cid
    raise ValueError("contextId is required on A2A Task and StreamResponse events")


def _payload_context_id(payload: Any) -> Optional[str]:
    """Return ``_a2a_v1_context_id`` from a task payload when present."""
    if isinstance(payload, dict):
        cid = payload.get("_a2a_v1_context_id")
        if isinstance(cid, str) and cid:
            return cid
    return None


def _task_event_to_a2a_v1_sse(
    event: task_executor.TaskEvent,
    *,
    context_id: Optional[str] = None,
) -> dict:
    """Convert a ``TaskEvent`` to an A2A v1 SSE ``StreamResponse`` payload.

    Returns a dict with exactly one ``statusUpdate`` key whose value is a
    ``TaskStatusUpdateEvent`` (A2A §3.2.3 / §4.2.1). Progress strings are
    carried in ``metadata.progress`` when present.

    When ``event.status == "input_required"`` and the event carries
    ``input_required_text``, the resulting ``TaskStatus.message`` is
    populated with a single text part whose ``metadata.reasonCode``
    (plus any supplementary keys from ``input_required_data``) is the
    machine-readable switch clients use.

    The ``Message.role`` is ``ROLE_AGENT`` per A2A §4 (the server is
    speaking to the client). This mirrors the A2A §4.1.3
    ``TASK_STATE_INPUT_REQUIRED`` + §6.3 multi-turn interaction pattern:
    the client resumes by replying to the same ``taskId`` + ``contextId``.
    """
    from a2a.shared.models import (
        Message,
        Part,
        Role,
        StreamResponse,
        TaskStatus,
        TaskStatusUpdateEvent,
        a2a_timestamp,
        perfpilot_status_to_a2a,
    )

    a2a_state = perfpilot_status_to_a2a(event.status)

    status_message: Optional[Message] = None
    if event.status == "input_required" and event.input_required_text:
        part_metadata: dict[str, Any] = {
            "reasonCode": event.input_required_reason_code or "",
        }
        if isinstance(event.input_required_data, dict):
            for key, value in event.input_required_data.items():
                if isinstance(key, str) and key != "reasonCode":
                    part_metadata[key] = value

        status_message = Message(
            message_id=f"{event.task_id}-input-required",
            role=Role.ROLE_AGENT,
            parts=[
                Part(
                    text=event.input_required_text,
                    media_type="text/markdown",
                    metadata=part_metadata,
                ),
            ],
            task_id=event.task_id,
            context_id=_context_id_from_task_or_event(context_id=context_id) or None,
        )

    status = TaskStatus(
        state=a2a_state,
        timestamp=a2a_timestamp(),
        message=status_message,
    )
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


def _yield_task_event_sse(
    event: Any,
    *,
    context_id: Optional[str],
    jsonrpc_id: Any = _SSE_REST,
) -> list[dict]:
    """Build SSE yield dicts for one TaskEvent (artifact then status).

    On completed events with ``reply_text``, emits an A2A ``artifactUpdate``
    immediately before the terminal ``statusUpdate``.
    """
    frames: list[dict] = []
    artifact_payload = _task_event_to_a2a_v1_artifact_sse(
        event, context_id=context_id,
    )
    status_payload = _task_event_to_a2a_v1_sse(event, context_id=context_id)

    if jsonrpc_id is _SSE_REST:
        if artifact_payload:
            frames.append({
                "event": "artifact",
                "data": json.dumps(artifact_payload),
            })
        frames.append({
            "event": "status",
            "data": json.dumps(status_payload),
        })
    else:
        from a2a.shared.jsonrpc import jsonrpc_sse_event

        if artifact_payload:
            frames.append({
                "event": "artifact",
                "data": jsonrpc_sse_event(jsonrpc_id, artifact_payload),
            })
        frames.append({
            "event": "status",
            "data": jsonrpc_sse_event(jsonrpc_id, status_payload),
        })
    return frames
