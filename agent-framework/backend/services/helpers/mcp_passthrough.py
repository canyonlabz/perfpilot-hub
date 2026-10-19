"""MCP gateway pass-through helpers for specialist / execution agents.

Provides direct tool calls (with retry policy) and the BlazeMeter
session-artifacts convenience wrapper that auto-fetches ``sessions_id``
when the caller omits it.

Leaf-ward of ``task_executor`` / ``specialist_agents``: imports only
``services.mcp_client``, ``services.mcp_tool_registry``, and stdlib.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


def extract_sessions_id_from_mcp_payload(payload: Any) -> list[str]:
    """Pull the load-generator ``sessions_id`` list from an MCP result."""
    data = payload
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], dict):
        data = data["result"]
    if not isinstance(data, dict):
        return []
    sid = data.get("sessions_id") or data.get("sessionsId") or []
    if isinstance(sid, (list, tuple)):
        return [str(x) for x in sid if x is not None and str(x).strip()]
    if isinstance(sid, str) and sid.strip():
        return [sid.strip()]
    return []


async def call_mcp_tool_passthrough_for_specialist(
    tool_name: str,
    args: dict,
    allowed_namespaces: list[str],
) -> dict:
    """Call an MCP tool via the gateway with namespace-scoped retry policy.

    Generalizes the execution-agent's ``call_mcp_tool_passthrough()``
    for any specialist. Retry classification uses the registry's
    ``_is_api_based()`` helper so the policy stays consistent with the
    auto-discovery wrappers.

    Args:
        tool_name: Fully-qualified gateway tool name.
        args: Tool arguments dict.
        allowed_namespaces: Bare namespace list for ``MCPClient`` config
            (e.g. ``["datadog"]``).

    Returns:
        ``{ok: True, tool, result, attempts}`` on success.
        ``{ok: False, tool, error, attempts}`` on failure.
    """
    from services.mcp_client import MCPClient, build_client_config
    from services.mcp_tool_registry import _is_api_based

    is_api = _is_api_based(tool_name)
    max_attempts = 3 if is_api else 1
    retry_delay = 5.0

    config = build_client_config(allowed_namespaces)
    async with MCPClient(config) as client:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await client.call_tool(tool_name, args)
                data = getattr(result, "data", None)
                if data is not None:
                    payload = data
                else:
                    content = getattr(result, "content", None) or []
                    payload = getattr(content[0], "text", None) if content else str(result)
                return {"ok": True, "tool": tool_name, "result": payload, "attempts": attempt}
            except PermissionError:
                raise
            except Exception as e:
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


async def call_mcp_tool_passthrough(tool_name: str, args: dict) -> dict:
    """Call an MCP tool directly and return a structured result dict.

    Used for MCP pass-through dispatch when the orchestrator delegates a
    specific MCP tool (e.g. ``blazemeter_get_public_report``) rather than
    a composite agent tool. Follows the same retry policy as the
    execution-agent's API-based MCP calls: up to 3 attempts with 5s
    back-off for ``blazemeter_*`` tools; single attempt for ``jmeter_*``
    (code-based, deterministic).
    """
    from services.mcp_client import MCPClient, build_client_config

    is_code_based = tool_name.startswith("jmeter_")
    max_attempts = 1 if is_code_based else 3
    retry_delay = 5.0

    config = build_client_config(["blazemeter", "jmeter"])
    async with MCPClient(config) as client:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await client.call_tool(tool_name, args)
                data = getattr(result, "data", None)
                if data is not None:
                    payload = data
                else:
                    content = getattr(result, "content", None) or []
                    payload = getattr(content[0], "text", None) if content else str(result)
                return {"ok": True, "tool": tool_name, "result": payload, "attempts": attempt}
            except PermissionError:
                raise
            except Exception as e:
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


async def passthrough_process_session_artifacts(args: dict) -> dict:
    """Pass-through for ``blazemeter_process_session_artifacts``.

    The MCP tool requires both ``run_id`` and ``sessions_id`` (list; one
    entry per load generator). When the orchestrator omits
    ``sessions_id``, fetch it via ``blazemeter_get_run_results`` first
    rather than sending an incomplete call that fails pydantic validation.
    """
    run_id = args.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return {
            "ok": False,
            "tool": "blazemeter_process_session_artifacts",
            "error": {
                "type": "InvalidArgs",
                "message": (
                    "blazemeter_process_session_artifacts requires "
                    "args.run_id (string)."
                ),
            },
            "attempts": 0,
        }

    sessions_id = args.get("sessions_id")
    if isinstance(sessions_id, (list, tuple)):
        sessions_id = [str(x) for x in sessions_id if x is not None and str(x).strip()]
    elif isinstance(sessions_id, str) and sessions_id.strip():
        sessions_id = [sessions_id.strip()]
    else:
        sessions_id = []

    if not sessions_id:
        summary = await call_mcp_tool_passthrough(
            "blazemeter_get_run_results",
            {"run_id": run_id.strip()},
        )
        if not summary.get("ok"):
            return {
                "ok": False,
                "tool": "blazemeter_process_session_artifacts",
                "error": {
                    "type": "MissingSessionsId",
                    "message": (
                        "sessions_id was not provided and "
                        "blazemeter_get_run_results failed while "
                        "auto-fetching it."
                    ),
                    "upstream": summary.get("error"),
                },
                "attempts": summary.get("attempts", 0),
            }
        sessions_id = extract_sessions_id_from_mcp_payload(summary.get("result"))
        if not sessions_id:
            return {
                "ok": False,
                "tool": "blazemeter_process_session_artifacts",
                "error": {
                    "type": "MissingSessionsId",
                    "message": (
                        "sessions_id was not provided and "
                        "blazemeter_get_run_results returned no "
                        "sessions_id list for this run_id."
                    ),
                },
                "attempts": summary.get("attempts", 0),
            }
        log.info(
            "execution_agent.passthrough.autofetch_sessions_id",
            extra={
                "run_id": run_id.strip(),
                "sessions_count": len(sessions_id),
            },
        )

    return await call_mcp_tool_passthrough(
        "blazemeter_process_session_artifacts",
        {"run_id": run_id.strip(), "sessions_id": sessions_id},
    )
