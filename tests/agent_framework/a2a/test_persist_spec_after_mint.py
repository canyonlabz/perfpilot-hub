"""Playwright spec persist must run after ``test_run_id`` mint.

``persist_test_spec_from_parts`` is reuse-only. On first contact the
orchestrator mints an ID; persist then writes
``artifacts/{id}/test-specs/{id}_test_spec.md`` and sets
``agent_spec_file_var`` for specialist delegation.
"""

from __future__ import annotations

import inspect
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from core.task_signals import TaskInputRequiredSignal
from services import task_executor
from services.helpers.test_specs import resolve_test_run_id_then_persist_spec
from stores import task_store


_TRID_FORMAT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")


def _make_task(
    *,
    payload: dict[str, Any],
    test_run_id: Optional[str] = None,
) -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="running",
        payload=payload,
        submitted_at=now,
        updated_at=now,
        test_run_id=test_run_id,
        thread_id="thread-playwright",
    )


def _playwright_payload(*, steps: str = "Step 1: Navigate to https://example.com/.") -> dict[str, Any]:
    return {
        "parts": [
            {
                "text": "Convert this Playwright test into a JMeter script.",
                "metadata": {"task": "generate_jmx_from_playwright"},
            },
            {
                "text": steps,
                "mediaType": "text/markdown",
                "metadata": {"source_type": "playwright"},
            },
            {
                "text": "## Known Placeholders: {{baseUrl}}",
                "metadata": {"environment": "QA"},
            },
        ],
    }


@pytest.fixture(autouse=True)
def _reset_spec_contextvars() -> None:
    from agents.orchestrator.agent import agent_spec_file_var, agent_test_run_id_var

    spec_token = agent_spec_file_var.set(None)
    trid_token = agent_test_run_id_var.set(None)
    yield
    agent_spec_file_var.reset(spec_token)
    agent_test_run_id_var.reset(trid_token)


@pytest.fixture
def capture_spec_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    captured: list[tuple[str, str]] = []

    def _fake_write_text(self, content, encoding="utf-8"):
        captured.append((str(self), content))
        return len(content)

    monkeypatch.setattr(Path, "write_text", _fake_write_text)
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    return captured


@pytest.fixture
def task_store_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[UUID, str]]:
    recorded: list[tuple[UUID, str]] = []

    async def _set_trid(task_id: UUID, trid: str) -> bool:
        recorded.append((task_id, trid))
        return True

    async def _list_for_thread(thread_id: str, *, limit: int = 50, offset: int = 0, user_id=None):
        return []

    monkeypatch.setattr(task_store, "set_test_run_id", _set_trid)
    monkeypatch.setattr(task_store, "list_tasks_for_thread", _list_for_thread)
    return recorded


async def test_playwright_mint_then_persist_writes_spec(
    capture_spec_writes: list[tuple[str, str]],
    task_store_writes: list[tuple[UUID, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fresh Playwright request: mint an ID, then write the spec file."""
    import services.helpers.test_specs as test_specs_mod

    caplog.set_level(logging.WARNING, logger=test_specs_mod.log.name)

    task = _make_task(payload=_playwright_payload())
    result = await resolve_test_run_id_then_persist_spec(
        task, user_message="Convert this Playwright test.", thread_id=None,
    )

    assert result is not None
    assert _TRID_FORMAT_RE.match(result)
    assert task.test_run_id == result
    assert task.payload.get("test_run_id") == result
    assert len(task_store_writes) == 1
    assert task_store_writes[0][1] == result

    assert len(capture_spec_writes) == 1
    spec_path, spec_body = capture_spec_writes[0]
    assert f"{result}_test_spec.md" in spec_path
    assert result in spec_path
    assert "Step 1:" in spec_body
    assert not any(
        "persist_test_spec_from_parts.missing_test_run_id" in r.message
        for r in caplog.records
    )

    from agents.orchestrator.agent import agent_spec_file_var
    assert agent_spec_file_var.get() == spec_path


async def test_reject_does_not_persist_spec(
    capture_spec_writes: list[tuple[str, str]],
    task_store_writes: list[tuple[UUID, str]],
) -> None:
    """Catalog / extract-style reject must not write a spec file."""
    task = _make_task(
        payload={"parts": [{"text": "Pull BlazeMeter results for the last run."}]},
    )

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        await resolve_test_run_id_then_persist_spec(
            task, user_message="Pull BlazeMeter results for the last run.", thread_id=None,
        )

    assert exc_info.value.reason_code == "missing_test_run_id"
    assert capture_spec_writes == []
    assert task_store_writes == []


def test_orchestrator_uses_resolve_then_persist_helper() -> None:
    """``_run_orchestrator`` must not call persist before the resolver."""
    source = inspect.getsource(task_executor._run_orchestrator)
    assert "resolve_test_run_id_then_persist_spec" in source
    assert "persist_test_spec_from_parts(" not in source
