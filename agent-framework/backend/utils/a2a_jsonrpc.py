"""A2A v1.0.0 JSON-RPC 2.0 binding utilities.

Provides envelope parsing, response builders, error helpers, and method
classification for the JSON-RPC 2.0 protocol binding (A2A spec Section 9).

The single ``POST /a2a/v1`` endpoint in ``a2a_server.py`` delegates to these
helpers for request validation and response formatting. Method dispatch
itself lives in the route handler to stay consistent with the Phase 2
HTTP+JSON/REST binding pattern.

This module is standalone — it depends only on ``a2a_models`` for error
code constants. No FastAPI, database, or AG2 imports.

Spec reference: https://a2a-protocol.org/v1.0.0/specification/#9-json-rpc-20-protocol-binding
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Union

from .a2a_models import (
    A2A_ERROR_NAMES,
    A2A_ERROR_TASK_NOT_FOUND,
    A2A_ERROR_UNSUPPORTED_OPERATION,
    a2a_timestamp,
)


# =============================================================================
# Standard JSON-RPC 2.0 error codes (spec Section 9.5)
# =============================================================================

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

JSONRPC_ERROR_MESSAGES: dict[int, str] = {
    JSONRPC_PARSE_ERROR: "Invalid JSON payload",
    JSONRPC_INVALID_REQUEST: "Request payload validation error",
    JSONRPC_METHOD_NOT_FOUND: "Method not found",
    JSONRPC_INVALID_PARAMS: "Invalid parameters",
    JSONRPC_INTERNAL_ERROR: "Internal error",
}


# =============================================================================
# Supported A2A methods and classification
# =============================================================================

STREAMING_METHODS: frozenset[str] = frozenset({
    "SendStreamingMessage",
    "SubscribeToTask",
})

SUPPORTED_METHODS: frozenset[str] = frozenset({
    "SendMessage",
    "SendStreamingMessage",
    "GetTask",
    "ListTasks",
    "CancelTask",
    "SubscribeToTask",
    "GetExtendedAgentCard",
})


def is_streaming_method(method: str) -> bool:
    """Return ``True`` if *method* requires an SSE streaming response.

    Args:
        method: PascalCase JSON-RPC method name.

    Returns:
        ``True`` for ``SendStreamingMessage`` and ``SubscribeToTask``.
    """
    return method in STREAMING_METHODS


def is_supported_method(method: str) -> bool:
    """Return ``True`` if *method* is a recognized A2A JSON-RPC method.

    Args:
        method: PascalCase JSON-RPC method name.

    Returns:
        ``True`` if the method is in the supported set.
    """
    return method in SUPPORTED_METHODS


# =============================================================================
# Parsed request dataclass
# =============================================================================


@dataclass(frozen=True, slots=True)
class JsonRpcRequest:
    """Validated JSON-RPC 2.0 request envelope.

    Attributes:
        id: Client-supplied request identifier (string, int, or ``None``
            for notifications).
        method: PascalCase A2A method name.
        params: Method parameters dict (may be empty).
    """

    id: Union[str, int, None]
    method: str
    params: dict[str, Any]


# =============================================================================
# Parsing exception
# =============================================================================


class JsonRpcError(Exception):
    """Raised when a JSON-RPC request fails validation.

    Carries the pre-built error response dict so the route handler can
    return it directly as a ``JSONResponse``.
    """

    def __init__(self, response: dict) -> None:
        self.response = response
        super().__init__(response.get("error", {}).get("message", "JSON-RPC error"))


# =============================================================================
# Envelope parser
# =============================================================================


def parse_jsonrpc_request(body: Any) -> JsonRpcRequest:
    """Parse and validate a JSON-RPC 2.0 request envelope.

    Validates the ``jsonrpc``, ``method``, ``id``, and ``params`` fields
    according to the JSON-RPC 2.0 specification.

    Args:
        body: The JSON-decoded request body (should be a ``dict``).

    Returns:
        A validated ``JsonRpcRequest`` instance.

    Raises:
        JsonRpcError: If the envelope is malformed or contains an
            unsupported method. The exception carries the pre-built
            error response dict.
    """
    if not isinstance(body, dict):
        raise JsonRpcError(jsonrpc_error(
            None,
            JSONRPC_INVALID_REQUEST,
            JSONRPC_ERROR_MESSAGES[JSONRPC_INVALID_REQUEST],
            a2a_error_data(
                "INVALID_REQUEST",
                "a2a-protocol.org",
                {"detail": "Request body must be a JSON object"},
            ),
        ))

    req_id = body.get("id")

    if body.get("jsonrpc") != "2.0":
        raise JsonRpcError(jsonrpc_error(
            req_id,
            JSONRPC_INVALID_REQUEST,
            JSONRPC_ERROR_MESSAGES[JSONRPC_INVALID_REQUEST],
            a2a_error_data(
                "INVALID_REQUEST",
                "a2a-protocol.org",
                {"detail": "Field 'jsonrpc' must be '2.0'"},
            ),
        ))

    method = body.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(jsonrpc_error(
            req_id,
            JSONRPC_INVALID_REQUEST,
            JSONRPC_ERROR_MESSAGES[JSONRPC_INVALID_REQUEST],
            a2a_error_data(
                "INVALID_REQUEST",
                "a2a-protocol.org",
                {"detail": "Field 'method' must be a non-empty string"},
            ),
        ))

    if not is_supported_method(method):
        raise JsonRpcError(jsonrpc_error(
            req_id,
            JSONRPC_METHOD_NOT_FOUND,
            JSONRPC_ERROR_MESSAGES[JSONRPC_METHOD_NOT_FOUND],
            a2a_error_data(
                "METHOD_NOT_FOUND",
                "a2a-protocol.org",
                {"method": method},
            ),
        ))

    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise JsonRpcError(jsonrpc_error(
            req_id,
            JSONRPC_INVALID_PARAMS,
            JSONRPC_ERROR_MESSAGES[JSONRPC_INVALID_PARAMS],
            a2a_error_data(
                "INVALID_PARAMS",
                "a2a-protocol.org",
                {"detail": "Field 'params' must be a JSON object when present"},
            ),
        ))

    return JsonRpcRequest(id=req_id, method=method, params=params)


# =============================================================================
# Response builders
# =============================================================================


def jsonrpc_success(
    req_id: Union[str, int, None],
    result: Any,
) -> dict:
    """Build a JSON-RPC 2.0 success response.

    Args:
        req_id: The request identifier echoed back to the client.
        result: The method result (usually a dict).

    Returns:
        A dict ready for JSON serialization.
    """
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


def jsonrpc_error(
    req_id: Union[str, int, None],
    code: int,
    message: str,
    data: Optional[list[dict[str, Any]]] = None,
) -> dict:
    """Build a JSON-RPC 2.0 error response.

    The ``data`` field follows the A2A spec Section 9.5 convention: an
    array of objects each containing an ``@type`` key. When ``data`` is
    ``None``, the ``data`` field is omitted.

    Args:
        req_id: The request identifier (``None`` if parsing failed before
            extracting the id).
        code: Numeric error code (standard JSON-RPC or A2A-specific).
        message: Human-readable error message.
        data: Optional array of structured error detail objects.

    Returns:
        A dict ready for JSON serialization.
    """
    error_obj: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        error_obj["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": error_obj,
    }


def jsonrpc_sse_event(
    req_id: Union[str, int, None],
    result: Any,
) -> str:
    """Format a JSON-RPC success envelope as an SSE ``data:`` payload.

    Used by ``SendStreamingMessage`` and ``SubscribeToTask`` to wrap each
    ``StreamResponse`` event in a JSON-RPC envelope before sending over
    the SSE stream (spec Section 9.4.2).

    Args:
        req_id: The original request identifier.
        result: The ``StreamResponse`` dict for this event.

    Returns:
        A JSON string suitable for the SSE ``data:`` field.
    """
    return json.dumps(
        jsonrpc_success(req_id, result),
        separators=(",", ":"),
    )


# =============================================================================
# Error data helpers
# =============================================================================

_GOOGLE_RPC_ERROR_INFO_TYPE = "type.googleapis.com/google.rpc.ErrorInfo"


def a2a_error_data(
    reason: str,
    domain: str = "a2a-protocol.org",
    metadata: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    """Build the ``data`` array for a JSON-RPC error using ``google.rpc.ErrorInfo``.

    Follows A2A spec Section 9.5: each element has an ``@type`` key
    identifying the detail type.

    Args:
        reason: Machine-readable error reason (e.g. ``"TASK_NOT_FOUND"``).
        domain: Error domain string. Defaults to ``"a2a-protocol.org"``.
        metadata: Additional key-value pairs for the error context.

    Returns:
        A list with a single ``ErrorInfo`` dict, ready for the ``data``
        field of a JSON-RPC error response.
    """
    info: dict[str, Any] = {
        "@type": _GOOGLE_RPC_ERROR_INFO_TYPE,
        "reason": reason,
        "domain": domain,
    }
    if metadata:
        info["metadata"] = metadata
    return [info]


def task_not_found_error(
    req_id: Union[str, int, None],
    task_id: str,
) -> dict:
    """Build a ``TaskNotFoundError`` JSON-RPC error response.

    Args:
        req_id: The JSON-RPC request identifier.
        task_id: The task ID that was not found.

    Returns:
        A complete JSON-RPC error response dict.
    """
    return jsonrpc_error(
        req_id,
        A2A_ERROR_TASK_NOT_FOUND,
        A2A_ERROR_NAMES.get(A2A_ERROR_TASK_NOT_FOUND, "TaskNotFoundError"),
        a2a_error_data(
            "TASK_NOT_FOUND",
            metadata={"taskId": task_id, "timestamp": a2a_timestamp()},
        ),
    )


def unsupported_operation_error(
    req_id: Union[str, int, None],
    operation: str,
    detail: Optional[str] = None,
) -> dict:
    """Build an ``UnsupportedOperationError`` JSON-RPC error response.

    Args:
        req_id: The JSON-RPC request identifier.
        operation: The operation/method name that is not supported.
        detail: Optional human-readable detail string.

    Returns:
        A complete JSON-RPC error response dict.
    """
    meta: dict[str, str] = {"operation": operation}
    if detail:
        meta["detail"] = detail
    return jsonrpc_error(
        req_id,
        A2A_ERROR_UNSUPPORTED_OPERATION,
        A2A_ERROR_NAMES.get(A2A_ERROR_UNSUPPORTED_OPERATION, "UnsupportedOperationError"),
        a2a_error_data("UNSUPPORTED_OPERATION", metadata=meta),
    )


def internal_error(
    req_id: Union[str, int, None],
    detail: str,
) -> dict:
    """Build a generic internal error JSON-RPC response.

    Args:
        req_id: The JSON-RPC request identifier.
        detail: Description of the internal error.

    Returns:
        A complete JSON-RPC error response dict.
    """
    return jsonrpc_error(
        req_id,
        JSONRPC_INTERNAL_ERROR,
        JSONRPC_ERROR_MESSAGES[JSONRPC_INTERNAL_ERROR],
        a2a_error_data("INTERNAL_ERROR", metadata={"detail": detail}),
    )
