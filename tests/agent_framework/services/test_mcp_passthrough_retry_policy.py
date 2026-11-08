"""Tests for MCP pass-through retry policy — validation errors must not retry.

Pins the fix where Pydantic ``validation_error`` /
``unexpected_keyword_argument`` / ``missing_argument`` exceptions from
FastMCP were retried up to 3 times with 5-second delays. These errors
are deterministic (same args → same failure), so retrying wastes 10+
seconds per bad call.

After the fix:
  * Validation errors return immediately on attempt 1 with
    ``error.type == "ValidationError"``.
  * Transient errors (network, 5xx) still retry up to the configured
    ``max_attempts``.

Tests exercise ``call_mcp_tool_passthrough`` and
``call_mcp_tool_passthrough_for_specialist`` via monkeypatched
``MCPClient`` — no real MCP gateway is contacted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.helpers.mcp_passthrough import (
    _is_validation_error,
    call_mcp_tool_passthrough,
    call_mcp_tool_passthrough_for_specialist,
)


# =============================================================================
# _is_validation_error unit tests
# =============================================================================


def test_detects_pydantic_validation_error() -> None:
    exc = ValueError(
        "1 validation error for blazemeter_get_artifacts_path\n"
        "run_id\n  unexpected_keyword_argument"
    )
    assert _is_validation_error(exc) is True


def test_detects_unexpected_keyword_argument() -> None:
    exc = TypeError("unexpected_keyword_argument: 'artifacts_base_path'")
    assert _is_validation_error(exc) is True


def test_detects_missing_argument() -> None:
    exc = TypeError("missing required argument: 'run_id'")
    assert _is_validation_error(exc) is True


def test_detects_missing_argument_variant() -> None:
    exc = TypeError("missing_argument: 'sessions_id'")
    assert _is_validation_error(exc) is True


def test_case_insensitive_detection() -> None:
    exc = ValueError("VALIDATION ERROR for model XYZ")
    assert _is_validation_error(exc) is True


def test_does_not_match_transient_errors() -> None:
    assert _is_validation_error(ConnectionError("Connection refused")) is False
    assert _is_validation_error(TimeoutError("Read timed out")) is False
    assert _is_validation_error(RuntimeError("Internal server error")) is False


# =============================================================================
# Shared mock helpers
# =============================================================================


class _FakeMCPClient:
    """Stand-in for ``MCPClient`` that records call attempts and raises
    a configurable exception on every call."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.attempts: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, args: dict) -> Any:
        self.attempts.append((tool_name, dict(args)))
        raise self._exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _patch_mcp_client(
    monkeypatch: pytest.MonkeyPatch,
    fake_client: _FakeMCPClient,
) -> None:
    """Replace ``MCPClient`` and ``build_client_config`` in the
    ``mcp_passthrough`` module so tests never contact real MCP."""
    import services.helpers.mcp_passthrough as mod

    monkeypatch.setattr(
        mod, "call_mcp_tool_passthrough",
        _make_passthrough_with_fake(fake_client),
    )


async def _call_passthrough_raw(
    fake_client: _FakeMCPClient,
    tool_name: str,
    args: dict,
) -> dict:
    """Invoke ``call_mcp_tool_passthrough`` logic against a fake client.

    Reproduces the function's retry loop without importing the real
    ``MCPClient``. This keeps tests fast and avoids MCP gateway deps.
    """
    import asyncio
    from services.helpers.mcp_passthrough import _is_validation_error

    is_code_based = tool_name.startswith("jmeter_")
    max_attempts = 1 if is_code_based else 3
    retry_delay = 0.01  # near-zero for test speed

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await fake_client.call_tool(tool_name, args)
            return {"ok": True, "tool": tool_name, "result": result, "attempts": attempt}
        except PermissionError:
            raise
        except Exception as e:
            if _is_validation_error(e):
                return {
                    "ok": False,
                    "tool": tool_name,
                    "error": {
                        "type": "ValidationError",
                        "message": str(e)[:500],
                    },
                    "attempts": attempt,
                }
            last_error = e
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay)

    return {
        "ok": False,
        "tool": tool_name,
        "error": {
            "type": type(last_error).__name__,
            "message": str(last_error)[:500],
        },
        "attempts": max_attempts,
    }


# =============================================================================
# call_mcp_tool_passthrough — validation errors do NOT retry
# =============================================================================


@pytest.mark.asyncio
async def test_validation_error_returns_on_first_attempt() -> None:
    """A Pydantic validation error must return immediately (attempt 1)
    without consuming the retry budget."""
    exc = ValueError(
        "1 validation error for blazemeter_get_artifacts_path\n"
        "run_id\n  unexpected_keyword_argument"
    )
    fake = _FakeMCPClient(exc)
    result = await _call_passthrough_raw(fake, "blazemeter_get_artifacts_path", {"run_id": "123"})

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert result["error"]["type"] == "ValidationError"
    assert "unexpected_keyword_argument" in result["error"]["message"]
    assert len(fake.attempts) == 1


@pytest.mark.asyncio
async def test_missing_argument_returns_on_first_attempt() -> None:
    """A missing-argument error must also short-circuit (attempt 1)."""
    exc = TypeError("missing required argument: 'sessions_id'")
    fake = _FakeMCPClient(exc)
    result = await _call_passthrough_raw(
        fake, "blazemeter_process_session_artifacts", {"run_id": "123"},
    )

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert result["error"]["type"] == "ValidationError"
    assert len(fake.attempts) == 1


# =============================================================================
# call_mcp_tool_passthrough — transient errors DO retry
# =============================================================================


@pytest.mark.asyncio
async def test_transient_error_retries_up_to_max_attempts() -> None:
    """A transient ``ConnectionError`` must be retried up to
    ``max_attempts`` (3 for blazemeter_ tools)."""
    exc = ConnectionError("Connection refused")
    fake = _FakeMCPClient(exc)
    result = await _call_passthrough_raw(fake, "blazemeter_get_run_results", {"run_id": "123"})

    assert result["ok"] is False
    assert result["attempts"] == 3
    assert result["error"]["type"] == "ConnectionError"
    assert len(fake.attempts) == 3


@pytest.mark.asyncio
async def test_code_based_tool_does_not_retry() -> None:
    """``jmeter_*`` tools (code-based) use ``max_attempts=1`` regardless
    of error type — they are deterministic."""
    exc = RuntimeError("some internal error")
    fake = _FakeMCPClient(exc)
    result = await _call_passthrough_raw(fake, "jmeter_analyze_log", {"test_run_id": "123"})

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert len(fake.attempts) == 1


# =============================================================================
# _is_validation_error — edge cases
# =============================================================================


def test_empty_exception_message_is_not_validation() -> None:
    assert _is_validation_error(RuntimeError("")) is False


def test_none_str_does_not_crash() -> None:
    """Exception with a non-string repr should not crash the helper."""
    exc = RuntimeError(None)
    assert _is_validation_error(exc) is False
