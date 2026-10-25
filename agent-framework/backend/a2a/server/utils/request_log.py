"""Inbound A2A request classification; emit via ``utils.logging_config``.

This module only names A2A events and their attributes. ``log_event``
owns OTEL-shaped emit (body, severity, attributes, trace_id, span_id)
so spans/traces can be added later without rewriting these helpers.
Do not log cookies, tokens, or message bodies.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from utils.logging_config import log_event

_LOGGER_NAME = "a2a.server.request"
_GET_METHODS = frozenset({"GetTask"})


def _extra(
    *,
    rpc_method: Optional[str] = None,
    rpc_id: Any = None,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
    error_reason: Optional[str] = None,
    error_field: Optional[str] = None,
    error_detail: Optional[str] = None,
) -> dict[str, Any]:
    values = {
        "rpc_method": rpc_method,
        "rpc_id": None if rpc_id is None else str(rpc_id),
        "task_id": task_id,
        "context_id": context_id,
        "error_reason": error_reason,
        "error_field": error_field,
        "error_detail": error_detail,
    }
    return {key: value for key, value in values.items() if value not in (None, "")}


def log_a2a_reject(
    *,
    method: Optional[str],
    rpc_id: Any = None,
    reason: Optional[str] = None,
    field: Optional[str] = None,
    detail: Optional[str] = None,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
) -> None:
    """Warning for client rejects (missing contextId, flat body, resume)."""
    log_event(
        _LOGGER_NAME,
        logging.WARNING,
        "a2a.request.reject",
        attributes=_extra(
            rpc_method=method,
            rpc_id=rpc_id,
            task_id=task_id,
            context_id=context_id,
            error_reason=reason,
            error_field=field,
            error_detail=detail,
        ),
    )


def log_a2a_accept(
    *,
    method: str,
    rpc_id: Any = None,
    task_id: Optional[str] = None,
    context_id: Optional[str] = None,
) -> None:
    """Info for accepted Send/Stream/Cancel/Subscribe/discovery.

    GetTask is debug so polling does not INFO-spam.
    """
    attributes = _extra(
        rpc_method=method,
        rpc_id=rpc_id,
        task_id=task_id,
        context_id=context_id,
    )
    if method in _GET_METHODS:
        log_event(
            _LOGGER_NAME,
            logging.DEBUG,
            "a2a.request.poll",
            attributes=attributes,
        )
        return
    log_event(
        _LOGGER_NAME,
        logging.INFO,
        "a2a.request.accept",
        attributes=attributes,
    )


def error_fields_from_jsonrpc(content: dict) -> dict[str, Optional[str]]:
    """Pull reason / field / detail from a JSON-RPC error envelope."""
    error = content.get("error") if isinstance(content, dict) else None
    if not isinstance(error, dict):
        return {}
    data = error.get("data")
    first = data[0] if isinstance(data, list) and data else None
    metadata = first.get("metadata") if isinstance(first, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    reason = first.get("reason") if isinstance(first, dict) else None
    return {
        "reason": reason if isinstance(reason, str) else None,
        "field": metadata.get("field") if isinstance(metadata.get("field"), str) else None,
        "detail": metadata.get("detail") if isinstance(metadata.get("detail"), str) else error.get("message"),
        "task_id": metadata.get("taskId") if isinstance(metadata.get("taskId"), str) else None,
        "context_id": metadata.get("contextId") if isinstance(metadata.get("contextId"), str) else None,
    }


def ids_from_jsonrpc_result(content: dict) -> tuple[Optional[str], Optional[str]]:
    """Return ``(task_id, context_id)`` from a JSON-RPC success envelope."""
    result = content.get("result") if isinstance(content, dict) else None
    if not isinstance(result, dict):
        return None, None
    task = result.get("task") if isinstance(result.get("task"), dict) else result
    if not isinstance(task, dict):
        return None, None
    task_id = task.get("id")
    context_id = task.get("contextId") or task.get("context_id")
    return (
        task_id if isinstance(task_id, str) else None,
        context_id if isinstance(context_id, str) else None,
    )


def log_jsonrpc_envelope(method: Optional[str], rpc_id: Any, content: dict) -> None:
    """Classify a JSON-RPC response body as reject or accept/poll."""
    if not isinstance(content, dict):
        return
    if "error" in content:
        fields = error_fields_from_jsonrpc(content)
        log_a2a_reject(
            method=method,
            rpc_id=content.get("id", rpc_id),
            reason=fields.get("reason"),
            field=fields.get("field"),
            detail=fields.get("detail"),
            task_id=fields.get("task_id"),
            context_id=fields.get("context_id"),
        )
        return
    task_id, context_id = ids_from_jsonrpc_result(content)
    log_a2a_accept(
        method=method or "unknown",
        rpc_id=content.get("id", rpc_id),
        task_id=task_id,
        context_id=context_id,
    )


def log_a2a_reject_from_exc(
    exc: Any,
    *,
    method: str,
    rpc_id: Any = None,
) -> None:
    """Warning from ``IngressValidationError`` / ``ContextResolutionError``."""
    metadata = getattr(exc, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    log_a2a_reject(
        method=method,
        rpc_id=rpc_id,
        reason="INVALID_PARAMS",
        field=metadata.get("field") if isinstance(metadata.get("field"), str) else None,
        detail=getattr(exc, "detail", None) or str(exc),
        context_id=metadata.get("contextId") if isinstance(metadata.get("contextId"), str) else None,
    )
