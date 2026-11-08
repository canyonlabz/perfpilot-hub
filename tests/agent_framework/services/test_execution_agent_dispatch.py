"""Tests for execution-agent dispatch routing in _run_execution_agent.

Validates the three-way routing introduced by the dispatcher change:

1. Intent-based delegation (no ``tool`` field) → routed through
   ``_run_mcp_specialist_agent`` (LLM loop with FastMCP catalog).
2. Explicit MCP tool name (``blazemeter_*`` / ``jmeter_*``) → also
   routed through ``_run_mcp_specialist_agent``.
3. Composite tool name (``extract_test_run_artifacts``, etc.) → still
   uses direct Python dispatch (no LLM loop).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services import specialist_agents
from stores import task_store


# =============================================================================
# Helpers
# =============================================================================


def _make_execution_task(
    *,
    payload: Optional[dict] = None,
    test_run_id: Optional[str] = None,
) -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="execution-agent",
        status="running",
        payload=payload if payload is not None else {},
        submitted_at=now,
        updated_at=now,
        test_run_id=test_run_id,
    )


_COMMON = {
    "task_id": str(uuid4()),
    "session_id": str(uuid4()),
    "external_session_id": None,
    "agent_name": "execution-agent",
}


# =============================================================================
# Intent-based delegation (no tool field) → LLM dispatch
# =============================================================================


@pytest.mark.asyncio
async def test_intent_based_routes_to_llm_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payload without a ``tool`` field should route through
    ``_run_mcp_specialist_agent`` (the LLM loop path)."""
    sentinel = {"agent": "execution-agent", "reply_text": "Done."}
    mock_llm = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    task = _make_execution_task(
        payload={
            "user_message": "Get the public report URL for run 83856195",
            "test_run_id": "83856195",
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_awaited_once_with(task, _COMMON, "execution-agent")
    assert result is sentinel


@pytest.mark.asyncio
async def test_intent_based_with_none_tool_routes_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``tool: None`` should also route to the LLM path."""
    sentinel = {"agent": "execution-agent", "reply_text": "OK"}
    mock_llm = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    task = _make_execution_task(
        payload={
            "tool": None,
            "user_message": "List BlazeMeter workspaces",
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_awaited_once()
    assert result is sentinel


@pytest.mark.asyncio
async def test_intent_based_with_empty_tool_routes_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``tool: ""`` should also route to the LLM path."""
    sentinel = {"agent": "execution-agent", "reply_text": "OK"}
    mock_llm = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    task = _make_execution_task(
        payload={
            "tool": "",
            "user_message": "What tests are available?",
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_awaited_once()
    assert result is sentinel


# =============================================================================
# MCP tool name (blazemeter_* / jmeter_*) → LLM dispatch
# =============================================================================


@pytest.mark.asyncio
async def test_blazemeter_mcp_tool_routes_to_llm_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``blazemeter_*`` tool name should route through the LLM path
    instead of the old MCP pass-through."""
    sentinel = {"agent": "execution-agent", "reply_text": "public URL"}
    mock_llm = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    task = _make_execution_task(
        payload={
            "tool": "blazemeter_get_public_report",
            "args": {"run_id": "83856195"},
            "user_message": "Get public report for run 83856195",
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_awaited_once_with(task, _COMMON, "execution-agent")
    assert result is sentinel


@pytest.mark.asyncio
async def test_jmeter_mcp_tool_routes_to_llm_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``jmeter_*`` tool name should route through the LLM path."""
    sentinel = {"agent": "execution-agent", "reply_text": "log analysis"}
    mock_llm = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    task = _make_execution_task(
        payload={
            "tool": "jmeter_analyze_jmeter_log",
            "args": {"test_run_id": "83856195", "log_source": "blazemeter"},
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_awaited_once_with(task, _COMMON, "execution-agent")
    assert result is sentinel


# =============================================================================
# Composite tool name → direct Python dispatch (unchanged)
# =============================================================================


@pytest.mark.asyncio
async def test_composite_tool_uses_direct_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A composite tool (e.g. ``extract_test_run_artifacts``) must NOT
    route through ``_run_mcp_specialist_agent`` — it should use direct
    Python dispatch (module load + function call)."""
    mock_llm = AsyncMock()
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    fake_result = {"ok": True, "status": "success", "test_run_id": "83856195"}
    fake_fn = AsyncMock(return_value=fake_result)

    fake_module = type("FakeModule", (), {"extract_test_run_artifacts": fake_fn})()
    monkeypatch.setattr(
        specialist_agents, "_load_specialist_module", AsyncMock(return_value=fake_module),
    )

    task = _make_execution_task(
        payload={
            "tool": "extract_test_run_artifacts",
            "args": {"test_run_id": "83856195"},
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_not_awaited()
    assert result["tool_result"] == fake_result


@pytest.mark.asyncio
async def test_start_performance_test_uses_direct_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_performance_test`` is a composite tool — direct dispatch."""
    mock_llm = AsyncMock()
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    fake_result = {"ok": True, "run_id": "99999", "vendor": "blazemeter"}
    fake_fn = AsyncMock(return_value=fake_result)

    fake_module = type("FakeModule", (), {"start_performance_test": fake_fn})()
    monkeypatch.setattr(
        specialist_agents, "_load_specialist_module", AsyncMock(return_value=fake_module),
    )
    monkeypatch.setattr(
        specialist_agents, "_adopt_vendor_run_id_after_start",
        AsyncMock(return_value="99999"),
    )

    task = _make_execution_task(
        payload={
            "tool": "start_performance_test",
            "args": {"test_id": "14493190"},
        },
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_not_awaited()
    assert result["tool_result"] == fake_result


# =============================================================================
# Unknown tool → error
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool name that is neither composite nor MCP-prefixed should
    return an ``UnknownTool`` error."""
    mock_llm = AsyncMock()
    monkeypatch.setattr(specialist_agents, "_run_mcp_specialist_agent", mock_llm)
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())

    task = _make_execution_task(
        payload={"tool": "datadog_get_metrics", "args": {}},
    )
    result = await specialist_agents._run_execution_agent(task, _COMMON)

    mock_llm.assert_not_awaited()
    assert result["tool_result"]["ok"] is False
    assert result["tool_result"]["error"]["type"] == "UnknownTool"
