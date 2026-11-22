"""Tests for the native specialist turn in ``services.specialist_agents``.

Covers reply translation only: tool-round audits, halt exit reasons,
context metrics, and the ``agent.ask`` call shape. AG2 event classes are
real. The model, gateway, config files, and database are not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ag2.events import (
    HaltEvent,
    ToolCallEvent,
    ToolCallsEvent,
    ToolErrorEvent,
    ToolResultEvent,
    UsageEvent,
)
from ag2.events.types import Usage

from services import specialist_agents
from stores import task_store


def _budget(limit: int = 1000) -> SimpleNamespace:
    return SimpleNamespace(context_limit=limit)


def _call(name: str, arguments: str, call_id: str = "call-1") -> ToolCallEvent:
    return ToolCallEvent(id=call_id, name=name, arguments=arguments)


def test_reconstruct_tool_rounds_keeps_comparison_id_snippet() -> None:
    call = _call(
        "comparison_generate_report",
        '{"test_run_ids": ["a", "b"]}',
    )
    result = ToolResultEvent.from_call(
        call,
        '{"ok": true, "comparison_id": "cmp-123", "rest": "ignored"}',
    )
    events = [
        ToolCallsEvent(calls=[call]),
        call,
        result,
    ]

    rounds = specialist_agents._reconstruct_tool_rounds(events)

    assert len(rounds) == 1
    assert rounds[0]["round"] == 1
    assert rounds[0]["tool_calls"] == [{
        "name": "comparison_generate_report",
        "args": {"test_run_ids": ["a", "b"]},
    }]
    assert rounds[0]["results"][0]["ok"] is True
    assert rounds[0]["results"][0]["snippet"].startswith('{"ok": true')
    assert specialist_agents._extract_comparison_id_from_tool_rounds(rounds) == "cmp-123"


def test_reconstruct_marks_tool_error_not_ok() -> None:
    call = _call("jmeter_convert", "{}")
    error = ToolErrorEvent.from_call(call, RuntimeError("converter down"))
    rounds = specialist_agents._reconstruct_tool_rounds([
        ToolCallsEvent(calls=[call]),
        error,
    ])

    assert rounds[0]["results"][0]["ok"] is False
    assert "RuntimeError" in rounds[0]["results"][0]["snippet"]


def test_exit_reason_prefers_latest_known_halt() -> None:
    events = [
        HaltEvent(reason="round cap", source="round-limit"),
        HaltEvent(reason="denied", source="hitl-gate"),
    ]
    assert specialist_agents._native_exit_reason(events, "partial") == "hitl_halted"
    assert specialist_agents._native_exit_reason(
        [HaltEvent(reason="repeat", source="tool-repeat-throttle")],
        "",
    ) == "consecutive_repeat_limit"
    assert specialist_agents._native_exit_reason([], "done") == "text_response"
    assert specialist_agents._native_exit_reason([], "") == "null_reply"


def test_ctx_metrics_use_last_main_loop_usage() -> None:
    events = [
        UsageEvent(usage=Usage(prompt_tokens=10), kind="model_call", task_id="child"),
        UsageEvent(usage=Usage(prompt_tokens=500), kind="model_call"),
        UsageEvent(usage=Usage(prompt_tokens=9000), kind="compaction"),
    ]
    metrics = specialist_agents._native_ctx_metrics(events, _budget(1000))
    assert metrics == {
        "context_tokens": 500,
        "context_utilization_pct": 50.0,
        "context_limit": 1000,
    }


@pytest.mark.asyncio
async def test_run_native_agent_asks_with_task_id_and_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = _call("datadog_query", '{"env": "qa"}', call_id="c-9")
    events = [
        ToolCallsEvent(calls=[call]),
        ToolResultEvent.from_call(call, '{"ok": true}'),
        UsageEvent(usage=Usage(prompt_tokens=250), kind="model_call"),
    ]
    seen: dict[str, Any] = {}

    class _History:
        async def get_events(self) -> list:
            return events

    class _Reply:
        body = "finished"
        context = SimpleNamespace(stream=SimpleNamespace(history=_History()))

    async def _ask(message: str, **kwargs: Any) -> _Reply:
        seen["message"] = message
        seen["kwargs"] = kwargs
        return _Reply()

    broadcast = AsyncMock()
    monkeypatch.setattr(specialist_agents, "_broadcast", broadcast)

    reply_text, raw_reply, rounds, exit_reason, metrics = (
        await specialist_agents._run_native_agent(
            SimpleNamespace(ask=_ask),
            user_prompt="check the hosts",
            secrets_hint="confluence is cloud",
            task_id="task-1",
            common={
                "task_id": "task-1",
                "session_id": "session-1",
                "external_session_id": None,
                "agent_name": "monitoring-agent",
            },
            agent_name="monitoring-agent",
            max_tool_rounds=7,
            context_budget=_budget(1000),
        )
    )

    assert seen["message"] == "check the hosts"
    assert seen["kwargs"]["variables"] == {"task_id": "task-1"}
    assert seen["kwargs"]["prompt"] == ("confluence is cloud",)
    assert reply_text == "finished"
    assert raw_reply == "finished"
    assert exit_reason == "text_response"
    assert rounds[0]["results"][0]["tool"] == "datadog_query"
    assert metrics["context_tokens"] == 250
    broadcast.assert_awaited_once()
    event = broadcast.await_args.args[1]
    assert event.progress == "tool_round_1_of_7"


def _task(payload: dict) -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="monitoring-agent",
        status="running",
        payload=payload,
        submitted_at=now,
        updated_at=now,
        test_run_id="run-1",
    )


@pytest.mark.asyncio
async def test_mcp_specialist_closes_agent_after_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[Any] = []

    class _History:
        async def get_events(self) -> list:
            return []

    class _Reply:
        body = "ok"
        context = SimpleNamespace(stream=SimpleNamespace(history=_History()))

    async def _ask(message: str, **kwargs: Any) -> _Reply:
        return _Reply()

    agent = SimpleNamespace(ask=_ask)

    async def _close(target: Any) -> None:
        closed.append(target)

    fake_module = SimpleNamespace(build_monitoring_agent=lambda: agent)
    monkeypatch.setattr(
        specialist_agents, "_load_specialist_module", AsyncMock(return_value=fake_module),
    )
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())
    monkeypatch.setattr(
        specialist_agents, "_restore_web_ui_caller", lambda task, payload: (False, None),
    )
    monkeypatch.setattr(
        "utils.config_loader.load_agent_config",
        lambda name: {"mcp_tools": {"allowed_namespaces": ["datadog"]}},
    )
    monkeypatch.setattr(
        "core.agent_harness.load_harness_settings",
        lambda name: SimpleNamespace(max_tool_rounds=7),
    )
    monkeypatch.setattr(
        "services.helpers.token_context_window.resolve_context_budget_for_agent",
        lambda name: _budget(),
    )
    monkeypatch.setattr("core.base_agent.aclose_agent", _close)

    envelope = await specialist_agents._run_mcp_specialist_agent(
        _task({"user_message": "pull host metrics"}),
        {
            "task_id": "task-1",
            "session_id": "session-1",
            "external_session_id": None,
            "agent_name": "monitoring-agent",
        },
        "monitoring-agent",
    )

    assert envelope["reply_text"] == "ok"
    assert envelope["exit_reason"] == "text_response"
    assert closed == [agent]


@pytest.mark.asyncio
async def test_disabled_agent_returns_without_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = {"count": 0}

    async def _ask(message: str, **kwargs: Any) -> None:
        asked["count"] += 1

    async def _close(target: Any) -> None:
        asked["closed"] = True

    fake_module = SimpleNamespace(
        build_monitoring_agent=lambda: {"status": "disabled", "agent_name": "monitoring-agent"},
    )
    monkeypatch.setattr(
        specialist_agents, "_load_specialist_module", AsyncMock(return_value=fake_module),
    )
    monkeypatch.setattr(specialist_agents, "_broadcast", AsyncMock())
    monkeypatch.setattr(
        "utils.config_loader.load_agent_config",
        lambda name: {},
    )
    monkeypatch.setattr(
        "core.agent_harness.load_harness_settings",
        lambda name: SimpleNamespace(max_tool_rounds=7),
    )
    monkeypatch.setattr(
        "services.helpers.token_context_window.resolve_context_budget_for_agent",
        lambda name: _budget(),
    )
    monkeypatch.setattr("core.base_agent.aclose_agent", _close)

    envelope = await specialist_agents._run_mcp_specialist_agent(
        _task({"user_message": "pull host metrics"}),
        {
            "task_id": "task-1",
            "session_id": "session-1",
            "external_session_id": None,
            "agent_name": "monitoring-agent",
        },
        "monitoring-agent",
    )

    assert envelope["error"]["type"] == "AgentDisabled"
    assert asked["count"] == 0
    assert "closed" not in asked
