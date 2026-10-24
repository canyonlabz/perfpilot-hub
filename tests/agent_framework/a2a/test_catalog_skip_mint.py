"""A2A-E4: catalog / list-specialists must not demand a test_run_id."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import pytest

from core.task_signals import TaskInputRequiredSignal
from services.helpers import test_run_id_resolver
from stores import task_store


def _make_task(*, payload: Optional[dict] = None) -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="running",
        payload=payload if payload is not None else {},
        submitted_at=now,
        updated_at=now,
    )


@pytest.fixture
def task_store_mock(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {
        "set_test_run_id": [],
        "list_tasks_for_thread": [],
    }

    async def _set_test_run_id(task_id: Any, test_run_id: str) -> None:
        calls["set_test_run_id"].append((task_id, test_run_id))

    async def _list_tasks_for_thread(thread_id: str, limit: int = 50) -> list:
        calls["list_tasks_for_thread"].append((thread_id, limit))
        return []

    monkeypatch.setattr(task_store, "set_test_run_id", _set_test_run_id)
    monkeypatch.setattr(task_store, "list_tasks_for_thread", _list_tasks_for_thread)
    return calls


@pytest.mark.parametrize(
    "user_message",
    [
        "What specialists can you delegate to?",
        "what can you do?",
        "which specialists are available?",
    ],
)
async def test_catalog_prose_is_not_missing_test_run_id(
    task_store_mock: dict[str, list],
    user_message: str,
) -> None:
    task = _make_task(payload={})
    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task, user_message=user_message, thread_id=None,
    )
    assert result is None
    assert task_store_mock["set_test_run_id"] == []


async def test_catalog_metadata_is_not_missing_test_run_id(
    task_store_mock: dict[str, list],
) -> None:
    task = _make_task(
        payload={
            "parts": [
                {"text": "show the team", "metadata": {"task": "catalog"}},
            ]
        }
    )
    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task, user_message=None, thread_id=None,
    )
    assert result is None
    assert task_store_mock["set_test_run_id"] == []


async def test_extract_without_id_still_signals_input_required(
    task_store_mock: dict[str, list],
) -> None:
    task = _make_task(payload={})
    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
            task,
            user_message="extract results from the last BlazeMeter run",
            thread_id=None,
        )
    assert exc_info.value.reason_code == "missing_test_run_id"
    assert task_store_mock["set_test_run_id"] == []
