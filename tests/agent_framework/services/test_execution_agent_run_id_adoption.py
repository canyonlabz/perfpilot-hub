"""Tests for execution-agent vendor run_id adoption and sessions_id pass-through."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import pytest

from services import specialist_agents
from services.helpers import mcp_passthrough
from stores import task_store


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


@pytest.mark.asyncio
async def test_adopt_vendor_run_id_after_successful_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[UUID, str]] = []

    async def _set_trid(task_id: UUID, trid: str) -> bool:
        calls.append((task_id, trid))
        return True

    monkeypatch.setattr(task_store, "set_test_run_id", _set_trid)

    task = _make_execution_task(
        payload={"tool": "start_performance_test", "args": {"test_id": "14493190"}},
    )
    adopted = await specialist_agents._adopt_vendor_run_id_after_start(
        task,
        {"ok": True, "run_id": "83636431", "vendor": "blazemeter", "test_id": "14493190"},
    )

    assert adopted == "83636431"
    assert calls == [(task.task_id, "83636431")]


@pytest.mark.asyncio
async def test_adopt_vendor_run_id_skips_failed_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _set_trid(task_id: UUID, trid: str) -> bool:
        raise AssertionError("set_test_run_id must not be called on failed start")

    monkeypatch.setattr(task_store, "set_test_run_id", _set_trid)

    task = _make_execution_task(
        payload={"tool": "start_performance_test", "args": {"test_id": "14493190"}},
    )
    adopted = await specialist_agents._adopt_vendor_run_id_after_start(
        task,
        {"ok": False, "error": {"type": "Boom", "message": "nope"}},
    )
    assert adopted is None


def test_extract_sessions_id_from_mcp_payload_shapes() -> None:
    assert mcp_passthrough.extract_sessions_id_from_mcp_payload(
        {"sessions_id": ["r-v4-aaa", "r-v4-bbb"]}
    ) == ["r-v4-aaa", "r-v4-bbb"]
    assert mcp_passthrough.extract_sessions_id_from_mcp_payload(
        {"sessionsId": ["r-v4-ccc"]}
    ) == ["r-v4-ccc"]
    assert mcp_passthrough.extract_sessions_id_from_mcp_payload(
        {"result": {"sessions_id": ["r-v4-ddd"]}}
    ) == ["r-v4-ddd"]
    assert mcp_passthrough.extract_sessions_id_from_mcp_payload("not-a-dict") == []


@pytest.mark.asyncio
async def test_passthrough_process_session_artifacts_autofetches_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    async def _fake_passthrough(tool_name: str, args: dict) -> dict:
        calls.append((tool_name, dict(args)))
        if tool_name == "blazemeter_get_run_results":
            return {
                "ok": True,
                "tool": tool_name,
                "result": {
                    "run_id": args["run_id"],
                    "sessions_id": ["r-v4-6ad026c9c1c63851814616"],
                },
                "attempts": 1,
            }
        if tool_name == "blazemeter_process_session_artifacts":
            return {
                "ok": True,
                "tool": tool_name,
                "result": {"status": "success", "run_id": args["run_id"]},
                "attempts": 1,
            }
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr(
        mcp_passthrough, "call_mcp_tool_passthrough", _fake_passthrough,
    )

    result = await mcp_passthrough.passthrough_process_session_artifacts(
        {"run_id": "83636431"},
    )

    assert result["ok"] is True
    assert calls[0][0] == "blazemeter_get_run_results"
    assert calls[1][0] == "blazemeter_process_session_artifacts"
    assert calls[1][1] == {
        "run_id": "83636431",
        "sessions_id": ["r-v4-6ad026c9c1c63851814616"],
    }


@pytest.mark.asyncio
async def test_passthrough_process_session_artifacts_uses_provided_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    async def _fake_passthrough(tool_name: str, args: dict) -> dict:
        calls.append((tool_name, dict(args)))
        return {"ok": True, "tool": tool_name, "result": {"status": "success"}, "attempts": 1}

    monkeypatch.setattr(
        mcp_passthrough, "call_mcp_tool_passthrough", _fake_passthrough,
    )

    result = await mcp_passthrough.passthrough_process_session_artifacts(
        {
            "run_id": "83636431",
            "sessions_id": ["r-v4-aaa", "r-v4-bbb"],
        },
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][0] == "blazemeter_process_session_artifacts"
    assert calls[0][1]["sessions_id"] == ["r-v4-aaa", "r-v4-bbb"]
