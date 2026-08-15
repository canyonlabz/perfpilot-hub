"""CRUD over the `hitl_approvals` table (V2 doc Section 15).

Records every HITL prompt and its eventual approval/rejection. The
reporting-agent (F3.10) drives multi-round revise loops by:

  1. Calling `create_prompt(task_id, prompt)` -> row inserted with
     `decision='pending'`.
  2. The browser surfaces the prompt; a human approves or rejects.
  3. The AG-UI bridge writes the decision via `record_decision()`.
  4. The agent reads the decision via `get_pending_for_task()` /
     `get_latest_for_task()` and either ships the artifact or revises.

The same row schema is used regardless of whether the decision arrives
from CopilotKit (port 8002) or from an A2A client posting a HITL reply
(port 8001). One audit log, two front doors.

Lazy heavy imports stay consistent with `session_store.py` and
`task_store.py`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from . import db

log = logging.getLogger(__name__)

VALID_DECISIONS = ("approved", "rejected", "pending")


@dataclass
class HitlApproval:
    """Materialized view of a row in `hitl_approvals`."""

    id: int
    task_id: UUID
    prompt: dict
    decision: str
    feedback: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


def _coerce_jsonb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _row_to_approval(row: Any) -> HitlApproval:
    return HitlApproval(
        id=row["id"],
        task_id=row["task_id"],
        prompt=_coerce_jsonb(row["prompt"]) or {},
        decision=row["decision"],
        feedback=row["feedback"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )


async def create_prompt(task_id: UUID, prompt: dict) -> HitlApproval:
    """Insert a new HITL prompt in `pending` state. Returns the materialized row."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO hitl_approvals (task_id, prompt, decision)
            VALUES ($1, $2::jsonb, 'pending')
            RETURNING id, task_id, prompt, decision, feedback, decided_by, decided_at
            """,
            task_id,
            json.dumps(prompt or {}),
        )
    return _row_to_approval(row)


async def get_approval(approval_id: int) -> Optional[HitlApproval]:
    """Fetch a single row by primary key."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, task_id, prompt, decision, feedback, decided_by, decided_at
            FROM hitl_approvals
            WHERE id = $1
            """,
            approval_id,
        )
    return _row_to_approval(row) if row is not None else None


async def get_pending_for_task(task_id: UUID) -> list[HitlApproval]:
    """Return open (`decision='pending'`) prompts for the given task, oldest first."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, task_id, prompt, decision, feedback, decided_by, decided_at
            FROM hitl_approvals
            WHERE task_id = $1 AND decision = 'pending'
            ORDER BY id ASC
            """,
            task_id,
        )
    return [_row_to_approval(r) for r in rows]


async def list_for_task(task_id: UUID) -> list[HitlApproval]:
    """Full HITL history for a task, oldest first. Useful for revise-loop audit."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, task_id, prompt, decision, feedback, decided_by, decided_at
            FROM hitl_approvals
            WHERE task_id = $1
            ORDER BY id ASC
            """,
            task_id,
        )
    return [_row_to_approval(r) for r in rows]


async def record_decision(
    approval_id: int,
    *,
    decision: str,
    feedback: Optional[str] = None,
    decided_by: Optional[str] = None,
) -> Optional[HitlApproval]:
    """Move an approval from `pending` to `approved` or `rejected`.

    Args:
        approval_id: Primary key.
        decision: 'approved' or 'rejected'. 'pending' is rejected here -
            new prompts go through `create_prompt()` instead.
        feedback: Free-text feedback (typically only meaningful on
            'rejected'; ignored elsewhere).
        decided_by: Epic 3 free-text user hint. Becomes EntraID principal
            in Epic 4.

    Returns:
        The updated row, or None if the row was missing or already
        terminal (idempotent retries are silent no-ops).
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE hitl_approvals
               SET decision   = $2,
                   feedback   = $3,
                   decided_by = $4,
                   decided_at = NOW()
             WHERE id = $1
               AND decision = 'pending'
            RETURNING id, task_id, prompt, decision, feedback, decided_by, decided_at
            """,
            approval_id,
            decision,
            feedback if decision == "rejected" else None,
            decided_by,
        )
    return _row_to_approval(row) if row is not None else None
