"""
Error taxonomy and Result type for msteams-mcp operations.

Provides machine-readable error codes that help LLMs understand
failures and take appropriate action.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar, Union

T = TypeVar("T")


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    API_ERROR = "API_ERROR"
    BROWSER_ERROR = "BROWSER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


_DEFAULT_SUGGESTIONS: dict[ErrorCode, list[str]] = {
    ErrorCode.AUTH_REQUIRED: [
        "IMMEDIATELY call teams_login to authenticate",
        "Do NOT skip this step or tell the user Teams is unavailable",
        "After login succeeds, retry the original request",
    ],
    ErrorCode.AUTH_EXPIRED: [
        "IMMEDIATELY call teams_login to refresh authentication",
        "Do NOT skip this step or tell the user Teams is unavailable",
        "After login succeeds, retry the original request",
    ],
    ErrorCode.RATE_LIMITED: ["Wait before retrying", "Reduce request frequency"],
    ErrorCode.NOT_FOUND: ["Check the ID/query is correct", "Verify the resource exists"],
    ErrorCode.INVALID_INPUT: ["Check the input parameters", "Review the tool documentation"],
    ErrorCode.API_ERROR: ["Retry the request", "Check teams_status for system health"],
    ErrorCode.BROWSER_ERROR: ["Call teams_login to restart browser session"],
    ErrorCode.NETWORK_ERROR: ["Check network connectivity", "Retry the request"],
    ErrorCode.TIMEOUT: ["Retry the request", "Use smaller page sizes"],
    ErrorCode.UNKNOWN: ["Check teams_status", "Try teams_login if authentication issues"],
}

_RETRYABLE_BY_DEFAULT: set[ErrorCode] = {
    ErrorCode.RATE_LIMITED,
    ErrorCode.NETWORK_ERROR,
    ErrorCode.TIMEOUT,
    ErrorCode.API_ERROR,
}


@dataclass
class McpError:
    """Structured error with machine-readable information."""
    code: ErrorCode
    message: str
    retryable: bool = False
    retry_after_sec: float | None = None
    suggestions: list[str] = field(default_factory=list)


def create_error(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool | None = None,
    retry_after_sec: float | None = None,
    suggestions: list[str] | None = None,
) -> McpError:
    """Create a standardised MCP error with sensible defaults."""
    return McpError(
        code=code,
        message=message,
        retryable=retryable if retryable is not None else (code in _RETRYABLE_BY_DEFAULT),
        retry_after_sec=retry_after_sec,
        suggestions=suggestions if suggestions is not None else list(_DEFAULT_SUGGESTIONS.get(code, [])),
    )


def classify_http_error(status: int, message: str | None = None) -> ErrorCode:
    """Map an HTTP status code to an ErrorCode."""
    if status == 401:
        return ErrorCode.AUTH_EXPIRED
    if status == 403:
        return ErrorCode.AUTH_REQUIRED
    if status == 404:
        return ErrorCode.NOT_FOUND
    if status == 429:
        return ErrorCode.RATE_LIMITED
    if status in (400, 422):
        return ErrorCode.INVALID_INPUT
    if status >= 500:
        return ErrorCode.API_ERROR
    if message:
        lower = message.lower()
        if "timeout" in lower:
            return ErrorCode.TIMEOUT
        if "network" in lower or "econnreset" in lower:
            return ErrorCode.NETWORK_ERROR
    return ErrorCode.UNKNOWN


# ---------------------------------------------------------------------------
# Result type — discriminated union for success / failure
# ---------------------------------------------------------------------------

@dataclass
class Ok(Generic[T]):
    """Successful result with a value."""
    value: T
    ok: bool = field(default=True, init=False)


@dataclass
class Err:
    """Failed result with an McpError."""
    error: McpError
    ok: bool = field(default=False, init=False)


Result = Union[Ok[T], Err]


def ok(value: T) -> Ok[T]:
    """Create a successful result."""
    return Ok(value=value)


def err(error: McpError) -> Err:
    """Create a failed result."""
    return Err(error=error)


def err_from(code: ErrorCode, message: str, **kwargs) -> Err:
    """Shorthand to create a failed result from code + message."""
    return Err(error=create_error(code, message, **kwargs))
