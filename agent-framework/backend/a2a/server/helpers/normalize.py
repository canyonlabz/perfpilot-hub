"""Normalize an inbound A2A v1 SendMessageRequest into the internal payload.

Fails closed on flat legacy bodies. Validates ``message`` with the A2A
Pydantic models and reads PerfPilot fields from ``message.metadata`` and
``parts[].metadata``.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError


class IngressValidationError(Exception):
    """Inbound SendMessage envelope failed A2A Message validation."""

    def __init__(self, detail: str, metadata: Optional[dict[str, str]] = None) -> None:
        self.detail = detail
        self.metadata: dict[str, str] = {"detail": detail}
        if metadata:
            self.metadata.update(metadata)
        super().__init__(detail)


def _is_flat_legacy_body(body: Any) -> bool:
    """Return True for the retired top-level ``message`` / ``text`` / ``prompt`` shape."""
    if not isinstance(body, dict):
        return True
    if isinstance(body.get("text"), str) or isinstance(body.get("prompt"), str):
        return True
    msg = body.get("message")
    return not isinstance(msg, dict)


def _validation_detail(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid SendMessageRequest"
    first = errors[0]
    loc = ".".join(str(item) for item in first.get("loc", ()))
    msg = str(first.get("msg") or "invalid")
    if loc:
        return f"{loc}: {msg}"
    return msg


def _first_nonempty_str(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_a2a_v1_body(body: dict) -> dict:
    """Validate an A2A v1 ``SendMessageRequest`` and flatten it for executors.

    Required inbound ``message`` fields: ``messageId``, ``role``
    (``ROLE_USER``), ``parts`` (non-empty), ``contextId``.

    Flat PerfPilot bodies (top-level ``message`` string, ``text``, or
    ``prompt``) are rejected.

    Normalized internal structure::

        {
          "message": "Hello",
          "parts": [{"text": "Hello", "mediaType": "text/plain"}],
          "metadata": {...},
          "test_run_id": "...",          # from message/parts metadata
          "_a2a_v1_envelope": {...},
          "_a2a_v1_context_id": "ctx-001",
          "_a2a_v1_resume_task_id": "existing-task-uuid"
        }
    """
    from a2a.shared.models import Role, SendMessageRequest

    if _is_flat_legacy_body(body):
        raise IngressValidationError(
            "A2A SendMessage requires a message object with messageId, "
            "role, parts, and contextId",
            {"field": "message"},
        )

    msg_raw = body["message"]
    if not _first_nonempty_str(msg_raw.get("messageId"), msg_raw.get("message_id")):
        raise IngressValidationError(
            "message.messageId is required",
            {"field": "message.messageId"},
        )
    if msg_raw.get("role") != "ROLE_USER":
        raise IngressValidationError(
            "message.role must be ROLE_USER",
            {"field": "message.role"},
        )
    parts_raw = msg_raw.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        raise IngressValidationError(
            "message.parts must be a non-empty array",
            {"field": "message.parts"},
        )
    if not _first_nonempty_str(msg_raw.get("contextId"), msg_raw.get("context_id")):
        raise IngressValidationError(
            "message.contextId is required",
            {"field": "message.contextId"},
        )

    try:
        request = SendMessageRequest.model_validate(body)
    except ValidationError as exc:
        raise IngressValidationError(
            _validation_detail(exc),
            {"field": "message"},
        ) from exc

    msg = request.message
    if msg.role != Role.ROLE_USER:
        raise IngressValidationError(
            "message.role must be ROLE_USER",
            {"field": "message.role"},
        )
    context_id = _first_nonempty_str(msg.context_id)
    if context_id is None:
        raise IngressValidationError(
            "message.contextId is required",
            {"field": "message.contextId"},
        )

    normalized_parts: list[dict] = [
        part.model_dump(by_alias=True, exclude_none=True) for part in msg.parts
    ]
    first_text = None
    for part in msg.parts:
        if isinstance(part.text, str) and part.text.strip() and first_text is None:
            first_text = part.text

    result: dict = {
        "_a2a_v1_envelope": body,
        "_a2a_v1_context_id": context_id,
    }
    if first_text:
        result["message"] = first_text
    if normalized_parts:
        result["parts"] = normalized_parts

    merged_metadata: dict = {}
    if isinstance(request.metadata, dict):
        merged_metadata.update(request.metadata)
    if isinstance(msg.metadata, dict):
        merged_metadata.update(msg.metadata)
    for part in msg.parts:
        if not isinstance(part.metadata, dict):
            continue
        for key, value in part.metadata.items():
            if not isinstance(key, str) or value is None:
                continue
            if key not in merged_metadata:
                merged_metadata[key] = value
    if merged_metadata:
        result["metadata"] = merged_metadata

    if isinstance(msg.task_id, str) and msg.task_id.strip():
        result["_a2a_v1_resume_task_id"] = msg.task_id.strip()

    if request.configuration is not None:
        result["_a2a_v1_configuration"] = request.configuration.model_dump(
            by_alias=True, exclude_none=True,
        )

    test_run_id = _first_nonempty_str(merged_metadata.get("test_run_id"))
    if test_run_id is None:
        for part in msg.parts:
            if isinstance(part.metadata, dict):
                test_run_id = _first_nonempty_str(part.metadata.get("test_run_id"))
                if test_run_id is not None:
                    break
    if test_run_id is not None:
        result["test_run_id"] = test_run_id

    return result
