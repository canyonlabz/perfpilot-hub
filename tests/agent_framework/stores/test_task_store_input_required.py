"""Unit tests for A2A v1 state transitions on the `agent_tasks` table.

Covers the three new `stores.task_store` helpers:

- ``set_test_run_id``       — post-insert field update
- ``mark_input_required``   — transitions non-terminal task to A2A
                              INPUT_REQUIRED and persists the question +
                              reasonCode on the task's ``result`` field
- ``append_resume_message`` — implements the A2A §6.3 multi-turn resume:
                              appends to ``payload.resume_messages`` and
                              transitions INPUT_REQUIRED -> running

Also pins the ``VALID_STATUSES`` / ``TERMINAL_STATUSES`` constants to the
A2A v1 TaskState enum (A2A specification §4.1.3) so a future accidental
rename of a status string trips the suite.

The tests do not require a real PostgreSQL instance. ``stores.db.get_pool``
is monkeypatched to return a fake pool whose connection records every
executed SQL statement as ``(sql, args)`` so each test can assert on
both the SQL shape and the argument values.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from stores import db
from stores import task_store


# =============================================================================
# Fake asyncpg pool / connection
# =============================================================================


class _FakeConnection:
    """Minimal stand-in for an ``asyncpg.Connection``.

    Records every call to ``execute``, ``fetchrow``, ``fetch``, or
    ``fetchval`` as a ``(sql, args)`` tuple so tests can assert the
    exact SQL text and bound arguments. ``execute_result`` defaults to
    ``"UPDATE 1"`` to match asyncpg's convention for a successful
    one-row update; tests can override it to ``"UPDATE 0"`` to simulate
    a no-row-matched outcome.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_result: str = "UPDATE 1"
        self.fetchrow_result: Any = None
        self.fetch_result: list[Any] = []
        self.fetchval_result: Any = None

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return self.execute_result

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.fetchrow_result

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.calls.append((sql, args))
        return self.fetch_result

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.fetchval_result


class _FakeAcquireContext:
    """Async context manager returned by ``_FakePool.acquire``."""

    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    """In-memory replacement for ``asyncpg.pool.Pool``."""

    def __init__(self) -> None:
        self.conn = _FakeConnection()

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self.conn)


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    """Patch ``stores.db.get_pool`` to yield an in-memory fake pool.

    The pool's single connection records every SQL statement executed
    during the test. Access the recorded statements via
    ``fake_pool.conn.calls`` (list of ``(sql, args)`` tuples) and tune
    the behaviour via ``fake_pool.conn.execute_result``.
    """
    pool = _FakePool()

    async def _get_pool() -> _FakePool:
        return pool

    monkeypatch.setattr(db, "get_pool", _get_pool)
    return pool


# =============================================================================
# VALID_STATUSES / TERMINAL_STATUSES
# =============================================================================


def test_valid_statuses_includes_input_required() -> None:
    """A2A §4.1.3 adds INPUT_REQUIRED; our Python enum must mirror that."""
    assert "input_required" in task_store.VALID_STATUSES


def test_valid_statuses_includes_rejected_and_auth_required() -> None:
    """A2A §4.1.3 defines REJECTED (terminal) and AUTH_REQUIRED (interrupted)."""
    assert "rejected" in task_store.VALID_STATUSES
    assert "auth_required" in task_store.VALID_STATUSES


def test_valid_statuses_preserves_legacy_values() -> None:
    """Adding A2A spec values must not break existing legacy statuses."""
    for legacy in ("pending", "running", "completed", "failed", "cancelled"):
        assert legacy in task_store.VALID_STATUSES


def test_terminal_statuses_includes_rejected() -> None:
    """Per A2A §4.1.3, REJECTED is terminal alongside COMPLETED, FAILED, CANCELED."""
    assert "rejected" in task_store.TERMINAL_STATUSES


def test_terminal_statuses_exclude_input_required_and_auth_required() -> None:
    """Per A2A §4.1.3, INPUT_REQUIRED and AUTH_REQUIRED are interrupted
    (non-terminal); the task is expected to resume."""
    assert "input_required" not in task_store.TERMINAL_STATUSES
    assert "auth_required" not in task_store.TERMINAL_STATUSES


# =============================================================================
# set_test_run_id
# =============================================================================


async def test_set_test_run_id_updates_row(fake_pool: _FakePool) -> None:
    task_id = uuid4()
    updated = await task_store.set_test_run_id(task_id, "2026-10-06-23-45-00")

    assert updated is True
    assert len(fake_pool.conn.calls) == 1
    sql, args = fake_pool.conn.calls[0]
    assert "UPDATE agent_tasks" in sql
    assert "test_run_id" in sql
    assert args == (task_id, "2026-10-06-23-45-00")


async def test_set_test_run_id_returns_false_when_no_row_matched(
    fake_pool: _FakePool,
) -> None:
    fake_pool.conn.execute_result = "UPDATE 0"
    updated = await task_store.set_test_run_id(uuid4(), "2026-10-06-23-45-00")
    assert updated is False


async def test_set_test_run_id_rejects_empty_string(fake_pool: _FakePool) -> None:
    with pytest.raises(ValueError):
        await task_store.set_test_run_id(uuid4(), "")
    # No SQL should have been executed on a validation failure.
    assert fake_pool.conn.calls == []


async def test_set_test_run_id_rejects_whitespace_only(fake_pool: _FakePool) -> None:
    with pytest.raises(ValueError):
        await task_store.set_test_run_id(uuid4(), "   ")
    assert fake_pool.conn.calls == []


async def test_set_test_run_id_rejects_non_string(fake_pool: _FakePool) -> None:
    with pytest.raises(ValueError):
        # Passing a non-string ID should fail validation, not reach SQL.
        await task_store.set_test_run_id(uuid4(), 12345)  # type: ignore[arg-type]
    assert fake_pool.conn.calls == []


async def test_set_test_run_id_strips_surrounding_whitespace(
    fake_pool: _FakePool,
) -> None:
    task_id = uuid4()
    await task_store.set_test_run_id(task_id, "  2026-10-06-23-45-00  ")
    _, args = fake_pool.conn.calls[0]
    assert args == (task_id, "2026-10-06-23-45-00")


# =============================================================================
# mark_input_required
# =============================================================================


async def test_mark_input_required_transitions_status(fake_pool: _FakePool) -> None:
    task_id = uuid4()
    updated = await task_store.mark_input_required(
        task_id,
        question_text="Which test_run_id should I use?",
        reason_code="missing_test_run_id",
    )

    assert updated is True
    assert len(fake_pool.conn.calls) == 1
    sql, args = fake_pool.conn.calls[0]
    assert "UPDATE agent_tasks" in sql
    assert "status = 'input_required'" in sql
    # args: (task_id, result_json)
    assert args[0] == task_id
    body = json.loads(args[1])
    assert body["input_required"]["question_text"] == "Which test_run_id should I use?"
    assert body["input_required"]["reason_code"] == "missing_test_run_id"
    assert body["input_required"]["reason_data"] == {}


async def test_mark_input_required_persists_reason_data(fake_pool: _FakePool) -> None:
    task_id = uuid4()
    reason_data = {
        "candidate_test_run_ids": [
            "2026-10-06-10-00-00",
            "2026-10-06-11-00-00",
        ]
    }
    await task_store.mark_input_required(
        task_id,
        question_text="More than one test_run_id is on this thread. Which should I use?",
        reason_code="missing_test_run_id",
        reason_data=reason_data,
    )

    _, args = fake_pool.conn.calls[0]
    body = json.loads(args[1])
    assert body["input_required"]["reason_data"] == reason_data


async def test_mark_input_required_skips_terminal_tasks(fake_pool: _FakePool) -> None:
    """The WHERE clause must exclude already-terminal tasks so we never
    overwrite a completed/failed/cancelled/rejected result row."""
    await task_store.mark_input_required(
        uuid4(),
        question_text="irrelevant",
        reason_code="missing_test_run_id",
    )
    sql, _ = fake_pool.conn.calls[0]
    assert "status NOT IN" in sql
    for terminal in ("'completed'", "'failed'", "'cancelled'", "'rejected'"):
        assert terminal in sql, f"terminal state {terminal} missing from WHERE clause"


async def test_mark_input_required_returns_false_when_no_row_matched(
    fake_pool: _FakePool,
) -> None:
    fake_pool.conn.execute_result = "UPDATE 0"
    updated = await task_store.mark_input_required(
        uuid4(),
        question_text="irrelevant",
        reason_code="missing_test_run_id",
    )
    assert updated is False


# =============================================================================
# append_resume_message
# =============================================================================


async def test_append_resume_message_appends_to_payload(
    fake_pool: _FakePool,
) -> None:
    task_id = uuid4()
    message_part = {
        "role": "ROLE_USER",
        "parts": [{"text": "test_run_id: 2026-10-06-10-00-00"}],
    }
    updated = await task_store.append_resume_message(task_id, message_part)

    assert updated is True
    assert len(fake_pool.conn.calls) == 1
    sql, args = fake_pool.conn.calls[0]
    assert "jsonb_set" in sql
    assert "resume_messages" in sql
    # Transition back to running on resume (A2A §6.3).
    assert "status = 'running'" in sql
    assert args[0] == task_id
    appended = json.loads(args[1])
    assert appended == [message_part]


async def test_append_resume_message_only_from_input_required_state(
    fake_pool: _FakePool,
) -> None:
    """The WHERE clause must restrict the UPDATE to tasks currently in
    input_required so we never resume a terminal or already-running task."""
    await task_store.append_resume_message(
        uuid4(),
        {"role": "ROLE_USER", "parts": []},
    )
    sql, _ = fake_pool.conn.calls[0]
    assert "status = 'input_required'" in sql


async def test_append_resume_message_clears_result_on_resume(
    fake_pool: _FakePool,
) -> None:
    """The INPUT_REQUIRED question + reasonCode was stored in task.result;
    clearing it on resume prevents the next SSE emit from re-sending stale
    input."""
    await task_store.append_resume_message(
        uuid4(),
        {"role": "ROLE_USER", "parts": []},
    )
    sql, _ = fake_pool.conn.calls[0]
    assert "result = NULL" in sql


async def test_append_resume_message_returns_false_when_not_in_input_required(
    fake_pool: _FakePool,
) -> None:
    """A follow-up message against a task that isn't in input_required
    (e.g. still running, or already terminal) must be a no-op signalled
    by a False return value."""
    fake_pool.conn.execute_result = "UPDATE 0"
    updated = await task_store.append_resume_message(
        uuid4(),
        {"role": "ROLE_USER", "parts": []},
    )
    assert updated is False
