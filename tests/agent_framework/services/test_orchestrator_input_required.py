"""Tests for the orchestrator INPUT_REQUIRED signalling path.

Covers two surfaces:

  1. ``services.helpers.test_run_id_resolver.resolve_test_run_id_or_signal_input_required``
     — the service-layer orchestration wrapper that classifies the
     inbound request (via the pure ``core.test_run_id`` classifier),
     persists minted IDs, and either returns a resolved ``test_run_id``
     or raises ``TaskInputRequiredSignal``.

  2. ``services.task_executor.execute_task`` — the outer wrapper that
     catches ``TaskInputRequiredSignal`` and transitions the task row to
     ``input_required`` via ``task_store.mark_input_required`` plus
     broadcasts a ``TaskEvent(status="input_required", ...)`` for SSE
     consumers, instead of calling ``mark_failed``.

Tests do not require a live DB or any running services. ``task_store``
writes and ``_dispatch_agent`` are monkeypatched to capture calls.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from core.task_signals import TaskInputRequiredSignal
from services import task_executor
from services.helpers import test_run_id_resolver
from stores import task_store


_TRID_FORMAT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")


# =============================================================================
# Shared helpers
# =============================================================================


def _make_task(
    *,
    task_id: Optional[UUID] = None,
    status: str = "running",
    payload: Optional[dict] = None,
    test_run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> task_store.AgentTask:
    """Build an in-memory ``AgentTask`` for test fixtures.

    Only populates fields the orchestrator helper reads; other fields
    get safe defaults so dataclass construction succeeds without a DB.
    """
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=task_id or uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status=status,
        payload=payload if payload is not None else {},
        submitted_at=now,
        updated_at=now,
        test_run_id=test_run_id,
        thread_id=thread_id,
    )


@pytest.fixture
def task_store_mock(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Capture every ``task_store`` write made by the code under test.

    Returns a dict with keys for each write function. Each value is a
    list of recorded calls. Tests inspect this to assert what was
    persisted without touching Postgres.
    """
    calls: dict[str, list] = {
        "set_test_run_id": [],
        "mark_input_required": [],
        "mark_running": [],
        "mark_failed": [],
        "mark_completed": [],
        "mark_cancelled": [],
        "list_tasks_for_thread": [],
        "get_task": [],
    }

    thread_tasks: list[task_store.AgentTask] = []

    async def _set_trid(task_id: UUID, trid: str) -> bool:
        calls["set_test_run_id"].append((task_id, trid))
        return True

    async def _mark_ir(
        task_id: UUID,
        question_text: str,
        reason_code: str,
        reason_data: Optional[dict] = None,
    ) -> bool:
        calls["mark_input_required"].append(
            {
                "task_id": task_id,
                "question_text": question_text,
                "reason_code": reason_code,
                "reason_data": reason_data,
            }
        )
        return True

    async def _mark_running(task_id: UUID) -> bool:
        calls["mark_running"].append(task_id)
        return True

    async def _mark_failed(task_id: UUID, error: dict) -> bool:
        calls["mark_failed"].append((task_id, error))
        return True

    async def _mark_completed(task_id: UUID, result: dict) -> bool:
        calls["mark_completed"].append((task_id, result))
        return True

    async def _mark_cancelled(task_id: UUID, reason: Optional[str] = None) -> bool:
        calls["mark_cancelled"].append((task_id, reason))
        return True

    async def _list_for_thread(
        thread_id: str, *, limit: int = 50, offset: int = 0,
        user_id: Optional[str] = None,
    ) -> list[task_store.AgentTask]:
        calls["list_tasks_for_thread"].append(
            {"thread_id": thread_id, "limit": limit, "offset": offset}
        )
        return list(thread_tasks)

    async def _get_task(task_id: UUID) -> Optional[task_store.AgentTask]:
        calls["get_task"].append(task_id)
        return None  # tests set this explicitly where needed

    monkeypatch.setattr(task_store, "set_test_run_id", _set_trid)
    monkeypatch.setattr(task_store, "mark_input_required", _mark_ir)
    monkeypatch.setattr(task_store, "mark_running", _mark_running)
    monkeypatch.setattr(task_store, "mark_failed", _mark_failed)
    monkeypatch.setattr(task_store, "mark_completed", _mark_completed)
    monkeypatch.setattr(task_store, "mark_cancelled", _mark_cancelled)
    monkeypatch.setattr(task_store, "list_tasks_for_thread", _list_for_thread)
    monkeypatch.setattr(task_store, "get_task", _get_task)

    # Expose a knob for tests to seed thread history.
    calls["_thread_tasks"] = thread_tasks  # type: ignore[assignment]
    return calls


# =============================================================================
# resolve_test_run_id_or_signal_input_required — REUSE outcome
# =============================================================================


async def test_resolve_returns_existing_id_from_payload(
    task_store_mock: dict[str, list],
) -> None:
    task = _make_task(payload={"test_run_id": "2026-10-06-10-00-00"})

    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task, user_message=None, thread_id=None,
    )

    assert result == "2026-10-06-10-00-00"
    # REUSE must NOT persist via set_test_run_id — the row already has it.
    assert task_store_mock["set_test_run_id"] == []


async def test_resolve_returns_existing_id_from_task_column(
    task_store_mock: dict[str, list],
) -> None:
    """Column takes precedence via the inbound-payload seeding in the
    helper: when ``task.test_run_id`` is set, it's injected into
    ``payload["test_run_id"]`` so the resolver reuses it."""
    task = _make_task(payload={}, test_run_id="2026-10-06-10-00-00")

    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task, user_message=None, thread_id=None,
    )

    assert result == "2026-10-06-10-00-00"
    assert task_store_mock["set_test_run_id"] == []


# =============================================================================
# resolve_test_run_id_or_signal_input_required — MINT outcome
# =============================================================================


async def test_resolve_mints_for_a2a_source_type_har(
    task_store_mock: dict[str, list],
) -> None:
    """A2A ``parts[1].metadata.source_type = har`` triggers a mint; the
    ID is persisted back to the task row for cross-turn reuse."""
    task = _make_task(
        payload={
            "parts": [
                {"text": "create a jmx from this har"},
                {"metadata": {"source_type": "har"}},
            ],
        }
    )

    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task, user_message=None, thread_id=None,
    )

    assert result is not None
    assert _TRID_FORMAT_RE.match(result), result
    # MINT persists via set_test_run_id so later turns see the ID.
    assert len(task_store_mock["set_test_run_id"]) == 1
    recorded_task_id, recorded_trid = task_store_mock["set_test_run_id"][0]
    assert recorded_task_id == task.task_id
    assert recorded_trid == result


async def test_resolve_mints_for_webui_prose_hint(
    task_store_mock: dict[str, list],
) -> None:
    """Web-UI chat path has no parts metadata; the prose heuristic
    recognizes script-creation intent and still mints."""
    task = _make_task(payload={})

    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task,
        user_message="please create a JMeter script from the HAR I'll upload",
        thread_id=None,
    )

    assert result is not None
    assert _TRID_FORMAT_RE.match(result)
    assert len(task_store_mock["set_test_run_id"]) == 1


# =============================================================================
# resolve_test_run_id_or_signal_input_required — SKIP_MINT outcome
# =============================================================================


async def test_resolve_does_not_signal_for_comparison_report_payload(
    task_store_mock: dict[str, list],
) -> None:
    """Comparison-report requests return None without raising and
    without calling set_test_run_id — the framework defers to the
    PerfReport MCP's own ``comparison_id`` which is captured later."""
    task = _make_task(
        payload={
            "parts": [
                {"text": "compare these runs"},
                {
                    "metadata": {
                        "task_type": "comparison_report",
                        "test_run_ids": [
                            "2026-10-05-10-00-00",
                            "2026-10-06-10-00-00",
                        ],
                    }
                },
            ]
        }
    )

    result = await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
        task, user_message=None, thread_id=None,
    )

    assert result is None
    assert task_store_mock["set_test_run_id"] == []
    assert task_store_mock["mark_input_required"] == []


# =============================================================================
# resolve_test_run_id_or_signal_input_required — REJECT outcome
# =============================================================================


async def test_resolve_signals_input_required_when_no_id_and_no_source_type(
    task_store_mock: dict[str, list],
) -> None:
    task = _make_task(payload={})

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
            task, user_message="hi there", thread_id=None,
        )

    sig = exc_info.value
    assert sig.reason_code == "missing_test_run_id"
    assert sig.question_text  # non-empty
    # No thread → no candidates in reason_data.
    assert sig.reason_data is None


async def test_resolve_signals_for_unknown_source_type_like_blazemeter(
    task_store_mock: dict[str, list],
) -> None:
    """Only ``playwright | har | openapi`` auto-mint. A BlazeMeter
    source_type with no existing ID must reject rather than mint."""
    task = _make_task(
        payload={
            "parts": [
                {"text": "pull BlazeMeter results"},
                {"metadata": {"source_type": "blazemeter"}},
            ],
        }
    )

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
            task, user_message=None, thread_id=None,
        )

    assert exc_info.value.reason_code == "missing_test_run_id"


async def test_resolve_signal_includes_candidate_ids_from_thread(
    task_store_mock: dict[str, list],
) -> None:
    """Candidate IDs collected from ``list_tasks_for_thread`` must
    appear in both ``reason_data`` (machine-readable) and the composed
    ``question_text`` (human-readable)."""
    thread_tasks = task_store_mock["_thread_tasks"]
    thread_tasks.append(_make_task(test_run_id="2026-10-05-10-00-00"))
    thread_tasks.append(_make_task(test_run_id="2026-10-06-10-00-00"))
    # Duplicate — the helper deduplicates.
    thread_tasks.append(_make_task(test_run_id="2026-10-05-10-00-00"))
    # Task with no test_run_id — must be skipped.
    thread_tasks.append(_make_task(test_run_id=None))

    task = _make_task(payload={}, thread_id="thread-1")

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
            task, user_message="analyze last run", thread_id="thread-1",
        )

    sig = exc_info.value
    assert sig.reason_data is not None
    assert sig.reason_data["candidate_test_run_ids"] == [
        "2026-10-05-10-00-00",
        "2026-10-06-10-00-00",
    ]
    assert "2026-10-05-10-00-00" in sig.question_text
    assert "2026-10-06-10-00-00" in sig.question_text


async def test_resolve_handles_missing_thread_id_gracefully(
    task_store_mock: dict[str, list],
) -> None:
    """When ``thread_id`` is None, ``list_tasks_for_thread`` must not
    be called."""
    task = _make_task(payload={})

    with pytest.raises(TaskInputRequiredSignal):
        await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
            task, user_message=None, thread_id=None,
        )

    assert task_store_mock["list_tasks_for_thread"] == []


async def test_resolve_recovers_from_list_tasks_for_thread_error(
    task_store_mock: dict[str, list], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure in ``list_tasks_for_thread`` must not block the
    classifier — the resolver proceeds with no candidates and the
    signal fires with ``reason_data=None``."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("db outage")
    monkeypatch.setattr(task_store, "list_tasks_for_thread", _boom)

    task = _make_task(payload={}, thread_id="thread-1")

    with pytest.raises(TaskInputRequiredSignal) as exc_info:
        await test_run_id_resolver.resolve_test_run_id_or_signal_input_required(
            task, user_message=None, thread_id="thread-1",
        )

    assert exc_info.value.reason_code == "missing_test_run_id"
    # No candidates recovered.
    assert exc_info.value.reason_data is None


# =============================================================================
# execute_task — catches TaskInputRequiredSignal
# =============================================================================


@pytest.fixture
def execute_task_mocks(
    monkeypatch: pytest.MonkeyPatch,
    task_store_mock: dict[str, list],
) -> dict[str, Any]:
    """Shared fixture for ``execute_task`` tests.

    Mocks ``_dispatch_agent``, ``_broadcast``, and the HITL gate so the
    outer try/except is exercised in isolation. The caller sets
    ``dispatch_behavior`` on the returned dict to control what
    ``_dispatch_agent`` does.
    """
    broadcasts: list = []
    state: dict[str, Any] = {
        "dispatch_behavior": None,  # callable or exception to raise
        "broadcasts": broadcasts,
        "task_store": task_store_mock,
    }

    async def _dispatch(task, common):
        beh = state["dispatch_behavior"]
        if isinstance(beh, BaseException):
            raise beh
        if callable(beh):
            return beh(task, common)
        return {"reply_text": "ok"}

    async def _broadcast(task_id: UUID, event: task_executor.TaskEvent) -> None:
        broadcasts.append((task_id, event))

    async def _hitl_passthrough(task, on_progress):
        return None

    async def _deliver_webhooks(final):  # noqa: ARG001
        return None

    async def _inject_completion_message(final):  # noqa: ARG001
        return None

    monkeypatch.setattr(task_executor, "_dispatch_agent", _dispatch)
    monkeypatch.setattr(task_executor, "_broadcast", _broadcast)
    monkeypatch.setattr(task_executor, "_deliver_webhooks", _deliver_webhooks)
    monkeypatch.setattr(
        task_executor, "_inject_completion_message", _inject_completion_message
    )

    from services import hitl_gates
    monkeypatch.setattr(hitl_gates, "enforce_at_task_start", _hitl_passthrough)

    return state


async def test_execute_task_catches_signal_and_marks_input_required(
    monkeypatch: pytest.MonkeyPatch,
    execute_task_mocks: dict[str, Any],
) -> None:
    task = _make_task(status="pending")
    ts = execute_task_mocks["task_store"]

    async def _get_task(task_id: UUID):
        return task
    monkeypatch.setattr(task_store, "get_task", _get_task)

    execute_task_mocks["dispatch_behavior"] = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="I need a test_run_id to continue.",
        reason_data={"candidate_test_run_ids": ["2026-10-06-10-00-00"]},
    )

    await task_executor.execute_task(task.task_id)

    # mark_input_required called exactly once with the signal's fields.
    assert len(ts["mark_input_required"]) == 1
    call = ts["mark_input_required"][0]
    assert call["task_id"] == task.task_id
    assert call["reason_code"] == "missing_test_run_id"
    assert call["question_text"] == "I need a test_run_id to continue."
    assert call["reason_data"] == {"candidate_test_run_ids": ["2026-10-06-10-00-00"]}

    # mark_failed MUST NOT be called — INPUT_REQUIRED is not a failure.
    assert ts["mark_failed"] == []
    # mark_completed MUST NOT be called.
    assert ts["mark_completed"] == []


async def test_execute_task_broadcasts_input_required_event(
    monkeypatch: pytest.MonkeyPatch,
    execute_task_mocks: dict[str, Any],
) -> None:
    task = _make_task(status="pending")

    async def _get_task(task_id: UUID):
        return task
    monkeypatch.setattr(task_store, "get_task", _get_task)

    execute_task_mocks["dispatch_behavior"] = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="I need a test_run_id to continue.",
        reason_data={"candidate_test_run_ids": ["2026-10-06-10-00-00"]},
    )

    await task_executor.execute_task(task.task_id)

    ir_events = [
        event
        for _, event in execute_task_mocks["broadcasts"]
        if event.status == "input_required"
    ]
    assert len(ir_events) == 1
    event = ir_events[0]
    assert event.input_required_text == "I need a test_run_id to continue."
    assert event.input_required_reason_code == "missing_test_run_id"
    assert event.input_required_data == {"candidate_test_run_ids": ["2026-10-06-10-00-00"]}
    # Routing fields filled from `common`.
    assert event.task_id == str(task.task_id)
    assert event.agent_name == "orchestrator"


async def test_execute_task_still_runs_normally_without_signal(
    monkeypatch: pytest.MonkeyPatch,
    execute_task_mocks: dict[str, Any],
) -> None:
    """Regression guard: non-signal paths (success / failure / cancel)
    continue to behave as before this phase."""
    task = _make_task(status="pending")
    ts = execute_task_mocks["task_store"]

    async def _get_task(task_id: UUID):
        return task
    monkeypatch.setattr(task_store, "get_task", _get_task)

    def _ok_dispatch(task, common):
        return {"reply_text": "ok"}
    execute_task_mocks["dispatch_behavior"] = _ok_dispatch

    await task_executor.execute_task(task.task_id)

    assert len(ts["mark_completed"]) == 1
    assert ts["mark_input_required"] == []
    assert ts["mark_failed"] == []


async def test_execute_task_marks_failed_for_non_signal_exception(
    monkeypatch: pytest.MonkeyPatch,
    execute_task_mocks: dict[str, Any],
) -> None:
    """Non-signal exceptions must still fall through to the generic
    failure branch — the signal catch must not swallow them."""
    task = _make_task(status="pending")
    ts = execute_task_mocks["task_store"]

    async def _get_task(task_id: UUID):
        return task
    monkeypatch.setattr(task_store, "get_task", _get_task)

    execute_task_mocks["dispatch_behavior"] = RuntimeError("boom")

    await task_executor.execute_task(task.task_id)

    assert len(ts["mark_failed"]) == 1
    assert ts["mark_input_required"] == []
    assert ts["mark_completed"] == []
