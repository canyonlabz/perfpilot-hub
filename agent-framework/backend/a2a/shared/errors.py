"""A2A v1.0.0 HTTP binding error response builders.

Produces ``google.rpc.Status`` JSON error responses for the A2A v1
HTTP+JSON/REST binding (spec Section 11.6). Each error response follows
this canonical shape::

    {
      "error": {
        "code": <HTTP status int>,
        "status": "<gRPC status name>",
        "message": "<human-readable message>",
        "details": [
          {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "<A2A_ERROR_REASON>",
            "domain": "a2a-protocol.org",
            "metadata": { ... }
          }
        ]
      }
    }

The JSON-RPC binding has its own error format handled by
``a2a_jsonrpc.py``. This module covers the HTTP binding only.

Legacy PerfPilot routes (``/agents/{name}/...``) are unaffected — they
continue using FastAPI's ``HTTPException`` -> ``{"detail": "..."}``
format.

Spec references:
  - Error format: https://a2a-protocol.org/v1.0.0/specification/#116-error-handling
  - Code mappings: https://a2a-protocol.org/v1.0.0/specification/#54-error-code-mappings
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi.responses import JSONResponse

from .models import (
    A2A_ERROR_TASK_NOT_CANCELABLE,
    A2A_ERROR_TASK_NOT_FOUND,
    A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED,
    A2A_ERROR_UNSUPPORTED_OPERATION,
    A2A_ERROR_CONTENT_TYPE_NOT_SUPPORTED,
    A2A_ERROR_INVALID_AGENT_RESPONSE,
    A2A_ERROR_EXTENDED_AGENT_CARD_NOT_CONFIGURED,
    A2A_ERROR_EXTENSION_SUPPORT_REQUIRED,
    A2A_ERROR_VERSION_NOT_SUPPORTED,
    a2a_timestamp,
)


# =============================================================================
# A2A error code -> HTTP status mapping (spec Section 5.4)
# =============================================================================

A2A_CODE_TO_HTTP: dict[int, int] = {
    A2A_ERROR_TASK_NOT_FOUND: 404,
    A2A_ERROR_TASK_NOT_CANCELABLE: 400,
    A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED: 400,
    A2A_ERROR_UNSUPPORTED_OPERATION: 400,
    A2A_ERROR_CONTENT_TYPE_NOT_SUPPORTED: 400,
    A2A_ERROR_INVALID_AGENT_RESPONSE: 500,
    A2A_ERROR_EXTENDED_AGENT_CARD_NOT_CONFIGURED: 400,
    A2A_ERROR_EXTENSION_SUPPORT_REQUIRED: 400,
    A2A_ERROR_VERSION_NOT_SUPPORTED: 400,
}

# =============================================================================
# A2A error code -> gRPC status name (spec Section 5.4)
# =============================================================================

A2A_CODE_TO_GRPC_STATUS: dict[int, str] = {
    A2A_ERROR_TASK_NOT_FOUND: "NOT_FOUND",
    A2A_ERROR_TASK_NOT_CANCELABLE: "FAILED_PRECONDITION",
    A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED: "FAILED_PRECONDITION",
    A2A_ERROR_UNSUPPORTED_OPERATION: "FAILED_PRECONDITION",
    A2A_ERROR_CONTENT_TYPE_NOT_SUPPORTED: "INVALID_ARGUMENT",
    A2A_ERROR_INVALID_AGENT_RESPONSE: "INTERNAL",
    A2A_ERROR_EXTENDED_AGENT_CARD_NOT_CONFIGURED: "FAILED_PRECONDITION",
    A2A_ERROR_EXTENSION_SUPPORT_REQUIRED: "FAILED_PRECONDITION",
    A2A_ERROR_VERSION_NOT_SUPPORTED: "FAILED_PRECONDITION",
}

# Standard HTTP status -> gRPC status name (for non-A2A-specific errors)
HTTP_TO_GRPC_STATUS: dict[int, str] = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "ALREADY_EXISTS",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    501: "UNIMPLEMENTED",
    503: "UNAVAILABLE",
}


# =============================================================================
# google.rpc.ErrorInfo detail builder
# =============================================================================

_GOOGLE_RPC_ERROR_INFO_TYPE = "type.googleapis.com/google.rpc.ErrorInfo"


def _error_detail(
    reason: str,
    domain: str = "a2a-protocol.org",
    metadata: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Build a single ``google.rpc.ErrorInfo`` detail object.

    Args:
        reason: Machine-readable error reason in UPPER_SNAKE_CASE
            (e.g. ``"TASK_NOT_FOUND"``).
        domain: Error domain. Defaults to ``"a2a-protocol.org"``.
        metadata: Additional key-value context for the error.

    Returns:
        A dict with ``@type``, ``reason``, ``domain``, and optional
        ``metadata``.
    """
    detail: dict[str, Any] = {
        "@type": _GOOGLE_RPC_ERROR_INFO_TYPE,
        "reason": reason,
        "domain": domain,
    }
    if metadata:
        detail["metadata"] = metadata
    return detail


# =============================================================================
# Core builder
# =============================================================================


def a2a_http_error(
    http_status: int,
    grpc_status: str,
    message: str,
    details: Optional[list[dict[str, Any]]] = None,
) -> dict:
    """Build a ``google.rpc.Status`` error body for the HTTP binding.

    Args:
        http_status: HTTP status code (e.g. ``404``).
        grpc_status: gRPC status name (e.g. ``"NOT_FOUND"``).
        message: Human-readable error message.
        details: Optional list of detail objects (each with ``@type``).

    Returns:
        A dict shaped as ``{"error": {"code": ..., "status": ...,
        "message": ..., "details": [...]}}``.
    """
    error_obj: dict[str, Any] = {
        "code": http_status,
        "status": grpc_status,
        "message": message,
    }
    if details:
        error_obj["details"] = details
    return {"error": error_obj}


def a2a_http_error_response(
    http_status: int,
    grpc_status: str,
    message: str,
    details: Optional[list[dict[str, Any]]] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """Build a ``JSONResponse`` with ``google.rpc.Status`` body and A2A headers.

    Args:
        http_status: HTTP status code.
        grpc_status: gRPC status name.
        message: Human-readable error message.
        details: Optional detail objects.
        extra_headers: Additional headers to merge (e.g. thread headers).

    Returns:
        A FastAPI ``JSONResponse`` ready to return from a route handler.
    """
    headers: dict[str, str] = {"A2A-Version": "1.0"}
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(
        content=a2a_http_error(http_status, grpc_status, message, details),
        status_code=http_status,
        headers=headers,
    )


# =============================================================================
# Convenience builders
# =============================================================================


def http_task_not_found(task_id: str) -> JSONResponse:
    """Return a 404 ``TaskNotFoundError`` response.

    Args:
        task_id: The task ID that was not found.
    """
    return a2a_http_error_response(
        404,
        "NOT_FOUND",
        "The specified task ID does not exist",
        [_error_detail(
            "TASK_NOT_FOUND",
            metadata={"taskId": task_id, "timestamp": a2a_timestamp()},
        )],
    )


def http_bad_request(
    message: str,
    reason: str = "INVALID_ARGUMENT",
    metadata: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """Return a 400 Bad Request response.

    Args:
        message: Human-readable error message.
        reason: Machine-readable reason code.
        metadata: Additional context.
    """
    return a2a_http_error_response(
        400,
        "INVALID_ARGUMENT",
        message,
        [_error_detail(reason, metadata=metadata)],
    )


def http_not_found(
    message: str,
    reason: str = "NOT_FOUND",
    metadata: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """Return a 404 Not Found response.

    Args:
        message: Human-readable error message.
        reason: Machine-readable reason code.
        metadata: Additional context.
    """
    return a2a_http_error_response(
        404,
        "NOT_FOUND",
        message,
        [_error_detail(reason, metadata=metadata)],
    )


def http_internal_error(
    message: str,
    metadata: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """Return a 500 Internal Server Error response.

    Args:
        message: Human-readable error message.
        metadata: Additional context.
    """
    return a2a_http_error_response(
        500,
        "INTERNAL",
        message,
        [_error_detail("INTERNAL_ERROR", metadata=metadata)],
    )


def http_unavailable(
    message: str,
    metadata: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """Return a 503 Service Unavailable response.

    Args:
        message: Human-readable error message.
        metadata: Additional context.
    """
    return a2a_http_error_response(
        503,
        "UNAVAILABLE",
        message,
        [_error_detail("SERVICE_UNAVAILABLE", metadata=metadata)],
    )


def httpexception_to_a2a(
    status_code: int,
    detail: str,
) -> JSONResponse:
    """Convert a FastAPI ``HTTPException`` into an A2A v1 error response.

    Used to wrap ``HTTPException`` raised by shared helpers
    (``_require_enabled_agent``, ``_require_session``, ``_read_json_body``)
    so A2A v1 REST routes return ``google.rpc.Status`` format instead of
    FastAPI's default ``{"detail": "..."}``.

    Args:
        status_code: The HTTP status code from the exception.
        detail: The detail string from the exception.

    Returns:
        A ``JSONResponse`` in ``google.rpc.Status`` format.
    """
    grpc_status = HTTP_TO_GRPC_STATUS.get(status_code, "INTERNAL")
    reason = grpc_status
    return a2a_http_error_response(
        status_code,
        grpc_status,
        detail,
        [_error_detail(reason, metadata={"detail": detail})],
    )
