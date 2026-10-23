"""A2A-E8: specialist A/B/C asks settle as INPUT_REQUIRED, not COMPLETED."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from core.task_signals import TaskInputRequiredSignal
from services import task_executor
from services.helpers.input_required_msgs import (
    REASON_AGENT_INPUT_REQUIRED,
    compose_input_required_question,
    raise_if_specialist_asks_for_input,
    specialist_reply_asks_for_input,
)
from stores import task_store


# Mirrors the Playwright conversion blocker: lettered options + pick-one.
_ABC_ASK = (
    "Summary of what I ran and the current blocker\n\n"
    "What I need from you (pick one)\n"
    "A. (Recommended) Provide the test_spec file path.\n"
    "B. Allow me to proceed with a best-effort JMX from the trace.\n"
    "C. Re-run the capture after saving the spec to disk.\n\n"
    "Which option do you want?"
)

_SUCCESS_JMX = (
    "Generated checkout.jmx under artifacts/2026-10-08-14-30-00/jmeter/.\n"
    "Smoke test passed. Option A think-time settings were kept."
)


def test_lettered_abc_ask_is_detected() -> None:
    assert specialist_reply_asks_for_input(_ABC_ASK) is True


def test_successful_jmx_summary_is_not_detected() -> None:
    assert specialist_reply_asks_for_input(_SUCCESS_JMX) is False


def test_single_option_without_ask_is_not_detected() -> None:
    text = "A. Use the existing HAR file and generate the script."
    assert specialist_reply_asks_for_input(text) is False


def test_ask_phrase_without_lettered_options_is_not_detected() -> None:
    text = "Please choose a test_run_id so I can continue."
    assert specialist_reply_asks_for_input(text) is False


def test_empty_or_non_text_is_not_detected() -> None:
    assert specialist_reply_asks_for_input("") is False
    assert specialist_reply_asks_for_input("   ") is False


def test_raise_if_asks_uses_agent_reply_as_question() -> None:
    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        raise_if_specialist_asks_for_input({
            "reply_text": _ABC_ASK,
            "exit_reason": "text_response",
        })
    sig = exc_info.value
    assert sig.reason_code == REASON_AGENT_INPUT_REQUIRED
    assert "Which option do you want?" in sig.question_text
    assert sig.reason_data == {"exit_reason": "text_response"}


def test_raise_if_asks_is_noop_for_completed_work() -> None:
    raise_if_specialist_asks_for_input({"reply_text": _SUCCESS_JMX})
    raise_if_specialist_asks_for_input(None)
    raise_if_specialist_asks_for_input("not a dict")


def test_compose_agent_input_required_fallback() -> None:
    text = compose_input_required_question(reason_code=REASON_AGENT_INPUT_REQUIRED)
    assert "choose how to continue" in text


def _make_task(*, status: str = "pending") -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="script-agent",
        status=status,
        payload={"parts": [{"text": "convert playwright"}]},
        submitted_at=now,
        updated_at=now,
    )


@pytest.fixture
def execute_task_harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, list] = {
        "mark_input_required": [],
        "mark_completed": [],
        "mark_failed": [],
        "mark_running": [],
    }
    broadcasts: list = []

    async def _set_running(task_id: UUID) -> bool:
        calls["mark_running"].append(task_id)
        return True

    async def _mark_ir(
        task_id: UUID,
        question_text: str,
        reason_code: str,
        reason_data: Optional[dict] = None,
    ) -> bool:
        calls["mark_input_required"].append({
            "task_id": task_id,
            "question_text": question_text,
            "reason_code": reason_code,
            "reason_data": reason_data,
        })
        return True

    async def _mark_completed(task_id: UUID, result: dict) -> bool:
        calls["mark_completed"].append((task_id, result))
        return True

    async def _mark_failed(task_id: UUID, error: dict) -> bool:
        calls["mark_failed"].append((task_id, error))
        return True

    async def _dispatch(task, common):  # noqa: ARG001
        return {
            "reply_text": _ABC_ASK,
            "exit_reason": "text_response",
            "tool_rounds": [],
        }

    async def _broadcast(task_id: UUID, event: task_executor.TaskEvent) -> None:
        broadcasts.append((task_id, event))

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(task_store, "mark_running", _set_running)
    monkeypatch.setattr(task_store, "mark_input_required", _mark_ir)
    monkeypatch.setattr(task_store, "mark_completed", _mark_completed)
    monkeypatch.setattr(task_store, "mark_failed", _mark_failed)
    monkeypatch.setattr(task_executor, "_dispatch_agent", _dispatch)
    monkeypatch.setattr(task_executor, "_broadcast", _broadcast)
    monkeypatch.setattr(task_executor, "_deliver_webhooks", _noop)
    monkeypatch.setattr(task_executor, "_inject_completion_message", _noop)

    from services import hitl_gates
    monkeypatch.setattr(hitl_gates, "enforce_at_task_start", _noop)

    return {"calls": calls, "broadcasts": broadcasts}


async def test_execute_task_abc_ask_is_input_required_not_completed(
    monkeypatch: pytest.MonkeyPatch,
    execute_task_harness: dict[str, Any],
) -> None:
    task = _make_task()

    async def _get_task(task_id: UUID):
        return task

    monkeypatch.setattr(task_store, "get_task", _get_task)

    await task_executor.execute_task(task.task_id)

    calls = execute_task_harness["calls"]
    assert len(calls["mark_input_required"]) == 1
    recorded = calls["mark_input_required"][0]
    assert recorded["reason_code"] == REASON_AGENT_INPUT_REQUIRED
    assert "Which option do you want?" in recorded["question_text"]
    assert calls["mark_completed"] == []
    assert calls["mark_failed"] == []

    ir_events = [
        event
        for _, event in execute_task_harness["broadcasts"]
        if event.status == "input_required"
    ]
    assert len(ir_events) == 1
    assert ir_events[0].input_required_reason_code == REASON_AGENT_INPUT_REQUIRED
