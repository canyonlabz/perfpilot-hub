"""CRUD over the `agent_tasks` table.

Tasks are the per-A2A-call records that back the three long-running task
patterns from V2 doc Section 14 (poll, SSE, webhook). One row per
`tasks/send` invocation, lifecycle states `pending -> running -> completed
| failed | cancelled`.

Mirrors the shape and lazy-import philosophy of `session_store.py`. Heavier
helpers (cancel, list-by-session, retention sweeps) are added as later
Features need them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from . import db

log = logging.getLogger(__name__)

VALID_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


@dataclass
class AgentTask:
    """Materialized view of a row in `agent_tasks`."""

    task_id: UUID
    session_id: UUID
    agent_name: str
    status: str
    payload: dict
    submitted_at: datetime
    updated_at: datetime
    external_session_id: Optional[str] = None
    test_run_id: Optional[str] = None
    thread_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[dict] = None
    subscriber_endpoints: list = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


def _coerce_jsonb(value: Any) -> Any:
    """asyncpg returns JSONB as either str (default) or already-decoded dict."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _row_to_task(row: Any) -> AgentTask:
    return AgentTask(
        task_id=row["task_id"],
        session_id=row["session_id"],
        external_session_id=row["external_session_id"],
        agent_name=row["agent_name"],
        status=row["status"],
        test_run_id=row["test_run_id"],
        thread_id=row.get("thread_id"),
        payload=_coerce_jsonb(row["payload"]) or {},
        result=_coerce_jsonb(row["result"]),
        error=_coerce_jsonb(row["error"]),
        subscriber_endpoints=_coerce_jsonb(row["subscriber_endpoints"]) or [],
        submitted_at=row["submitted_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        updated_at=row["updated_at"],
    )


async def create_task(
    *,
    session_id: UUID,
    agent_name: str,
    payload: dict,
    external_session_id: Optional[str] = None,
    test_run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    subscriber_endpoints: Optional[list[str]] = None,
) -> AgentTask:
    """Insert a new `agent_tasks` row in `pending` state and return it."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_tasks (
                session_id, external_session_id, agent_name, status,
                test_run_id, thread_id, payload, subscriber_endpoints
            )
            VALUES ($1, $2, $3, 'pending', $4, $5, $6::jsonb, $7::jsonb)
            RETURNING task_id, session_id, external_session_id, agent_name, status,
                      test_run_id, thread_id, payload, result, error,
                      subscriber_endpoints, submitted_at, started_at,
                      completed_at, updated_at
            """,
            session_id,
            external_session_id,
            agent_name,
            test_run_id,
            thread_id,
            json.dumps(payload or {}),
            json.dumps(subscriber_endpoints or []),
        )
    return _row_to_task(row)


async def get_task(task_id: UUID) -> Optional[AgentTask]:
    """Fetch a task by id. Returns None when the row is absent."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT task_id, session_id, external_session_id, agent_name, status,
                   test_run_id, thread_id, payload, result, error,
                   subscriber_endpoints, submitted_at, started_at,
                   completed_at, updated_at
            FROM agent_tasks
            WHERE task_id = $1
            """,
            task_id,
        )
    return _row_to_task(row) if row is not None else None


async def mark_running(task_id: UUID) -> None:
    """Transition `pending -> running` and stamp `started_at = NOW()`."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'running',
                started_at = COALESCE(started_at, NOW()),
                updated_at = NOW()
            WHERE task_id = $1
              AND status = 'pending'
            """,
            task_id,
        )


async def mark_completed(task_id: UUID, result: dict) -> None:
    """Transition any non-terminal task to `completed` with a result body."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'completed',
                result = $2::jsonb,
                completed_at = NOW(),
                updated_at = NOW()
            WHERE task_id = $1
              AND status NOT IN ('completed', 'failed', 'cancelled')
            """,
            task_id,
            json.dumps(result or {}),
        )


async def mark_failed(task_id: UUID, error: dict) -> None:
    """Transition any non-terminal task to `failed` with an error body."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'failed',
                error = $2::jsonb,
                completed_at = NOW(),
                updated_at = NOW()
            WHERE task_id = $1
              AND status NOT IN ('completed', 'failed', 'cancelled')
            """,
            task_id,
            json.dumps(error or {}),
        )


async def mark_cancelled(task_id: UUID, reason: Optional[str] = None) -> bool:
    """Transition any non-terminal task to `cancelled`. Returns True if changed."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'cancelled',
                error = $2::jsonb,
                completed_at = NOW(),
                updated_at = NOW()
            WHERE task_id = $1
              AND status NOT IN ('completed', 'failed', 'cancelled')
            """,
            task_id,
            json.dumps({"reason": reason or "cancelled by client"}),
        )
    return result.endswith(" 1")


async def delete_task(task_id: UUID) -> bool:
    """Delete a task row. Returns True when a row was removed.

    Intended for tests and operator clean-up only.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM agent_tasks WHERE task_id = $1", task_id)
    return result.endswith(" 1")


# =============================================================================
# Run-oriented helpers (PBI 3.6.6) - group tasks by `test_run_id`
# =============================================================================

@dataclass
class RunSummary:
    """Aggregated view of every task that shares the same `test_run_id`."""

    test_run_id: str
    task_count: int
    completed_count: int
    failed_count: int
    active_count: int
    cancelled_count: int
    started_at: datetime
    last_activity_at: datetime
    agent_names: list[str] = field(default_factory=list)


async def list_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[str] = None,
) -> list[RunSummary]:
    """Return distinct `test_run_id` values grouped by activity.

    Used by the AG-UI bridge (PBI 3.6.6) to power the browser's "recent
    runs" list and the "come back tomorrow" UX.

    Args:
        limit: Page size, clamped to [1, 200].
        offset: Page offset, clamped to >= 0.
        user_id: When provided, restrict the result to runs whose tasks
            belong to sessions owned by this `user_id`. Used by F3.7.0b
            owner-filtering so Alice never sees Bob's runs. When None
            (the default) all runs are returned, regardless of owner --
            kept for tests and internal callers; route handlers should
            always supply a `user_id`.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    base_sql = """
        SELECT
            t.test_run_id,
            COUNT(*)::INT AS task_count,
            SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)::INT AS completed_count,
            SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END)::INT AS failed_count,
            SUM(CASE WHEN t.status IN ('pending', 'running') THEN 1 ELSE 0 END)::INT AS active_count,
            SUM(CASE WHEN t.status = 'cancelled' THEN 1 ELSE 0 END)::INT AS cancelled_count,
            MIN(t.submitted_at) AS started_at,
            MAX(t.updated_at)   AS last_activity_at,
            ARRAY_AGG(DISTINCT t.agent_name ORDER BY t.agent_name) AS agent_names
        FROM agent_tasks t
        {join_clause}
        WHERE t.test_run_id IS NOT NULL
          {extra_where}
        GROUP BY t.test_run_id
        ORDER BY MAX(t.updated_at) DESC
        LIMIT $1 OFFSET $2
    """

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            sql = base_sql.format(join_clause="", extra_where="")
            rows = await conn.fetch(sql, limit, offset)
        else:
            sql = base_sql.format(
                join_clause="JOIN agent_sessions s ON s.session_id = t.session_id",
                extra_where="AND s.user_id = $3",
            )
            rows = await conn.fetch(sql, limit, offset, user_id)
    return [
        RunSummary(
            test_run_id=row["test_run_id"],
            task_count=row["task_count"],
            completed_count=row["completed_count"],
            failed_count=row["failed_count"],
            active_count=row["active_count"],
            cancelled_count=row["cancelled_count"],
            started_at=row["started_at"],
            last_activity_at=row["last_activity_at"],
            agent_names=list(row["agent_names"] or []),
        )
        for row in rows
    ]


async def list_tasks_for_run(
    test_run_id: str,
    *,
    user_id: Optional[str] = None,
) -> list[AgentTask]:
    """Return every task that carries the given `test_run_id`, oldest first.

    Args:
        test_run_id: The test_run_id to filter on.
        user_id: When provided, also require that each task's session is
            owned by this `user_id`. Used by F3.7.0b owner-filtering on
            `GET /api/runs/{test_run_id}`. When None (the default) no
            owner filter is applied.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            rows = await conn.fetch(
                """
                SELECT task_id, session_id, external_session_id, agent_name, status,
                       test_run_id, thread_id, payload, result, error,
                       subscriber_endpoints, submitted_at, started_at,
                       completed_at, updated_at
                FROM agent_tasks
                WHERE test_run_id = $1
                ORDER BY submitted_at ASC
                """,
                test_run_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT t.task_id, t.session_id, t.external_session_id, t.agent_name,
                       t.status, t.test_run_id, t.thread_id, t.payload, t.result,
                       t.error, t.subscriber_endpoints, t.submitted_at, t.started_at,
                       t.completed_at, t.updated_at
                FROM agent_tasks t
                JOIN agent_sessions s ON s.session_id = t.session_id
                WHERE t.test_run_id = $1
                  AND s.user_id = $2
                ORDER BY t.submitted_at ASC
                """,
                test_run_id,
                user_id,
            )
    return [_row_to_task(r) for r in rows]


# =============================================================================
# Thread-oriented helpers (BUG-09) - discover tasks by conversation thread
# =============================================================================

async def list_tasks_for_thread(
    thread_id: str,
    *,
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentTask]:
    """Return tasks originated from a specific thread, newest first.

    Owner-filtered: only tasks in sessions owned by `user_id` are returned.
    Includes tasks regardless of test_run_id (null or populated).

    Args:
        thread_id: The thread (conversation) to scope by.
        user_id: When provided, restrict to tasks whose session is owned by
            this user. Route handlers should always supply a `user_id`.
        limit: Page size, clamped to [1, 200].
        offset: Page offset, clamped to >= 0.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if user_id is None:
            rows = await conn.fetch(
                """
                SELECT task_id, session_id, external_session_id, agent_name, status,
                       test_run_id, thread_id, payload, result, error,
                       subscriber_endpoints, submitted_at, started_at,
                       completed_at, updated_at
                FROM agent_tasks
                WHERE thread_id = $1
                ORDER BY submitted_at DESC
                LIMIT $2 OFFSET $3
                """,
                thread_id,
                limit,
                offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT t.task_id, t.session_id, t.external_session_id, t.agent_name,
                       t.status, t.test_run_id, t.thread_id, t.payload, t.result,
                       t.error, t.subscriber_endpoints, t.submitted_at, t.started_at,
                       t.completed_at, t.updated_at
                FROM agent_tasks t
                JOIN agent_sessions s ON s.session_id = t.session_id
                WHERE t.thread_id = $1
                  AND s.user_id = $2
                ORDER BY t.submitted_at DESC
                LIMIT $3 OFFSET $4
                """,
                thread_id,
                user_id,
                limit,
                offset,
            )
    return [_row_to_task(r) for r in rows]
