"""CRUD over the `agent_sessions` table.

Sessions are the broader conversational context that contains tasks. See V2
doc Section 4.3 for the three-ID model (`external_session_id` >
`session_id` > `task_id`). This module is the data-access layer for the
session-level half of that model. Tasks (`agent_tasks`) get their own store
in Feature 3.5 alongside the A2A server.

Exposes a small set of functions so callers (the A2A server, the AG-UI
bridge, the orchestrator) do not need to know SQL. Each function uses the
shared asyncpg pool from `utils.db`.

Status: minimal-but-real for Feature 3.3. Additional helpers (search by
external_session_id, bulk inactivity sweep, etc.) are added as later
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

VALID_SOURCES = ("web_ui", "cursor", "claude", "a2a_external", "cli")


@dataclass
class AgentSession:
    """Materialized view of a row in `agent_sessions`."""

    session_id: UUID
    source: str
    started_at: datetime
    last_activity_at: datetime
    external_session_id: Optional[str] = None
    user_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    ended_at: Optional[datetime] = None


def _row_to_session(row: Any) -> AgentSession:
    """Convert an asyncpg `Record` to an `AgentSession`."""
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return AgentSession(
        session_id=row["session_id"],
        external_session_id=row["external_session_id"],
        source=row["source"],
        user_id=row["user_id"],
        metadata=metadata or {},
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        last_activity_at=row["last_activity_at"],
    )


async def create_session(
    source: str,
    *,
    external_session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> AgentSession:
    """Insert a new row in `agent_sessions` and return the materialized view.

    Args:
        source: Origin tag, ideally one of `VALID_SOURCES`. Free-text is
            allowed but a warning is logged when an unknown value is used.
        external_session_id: Optional propagated SDLC trace ID.
        user_id: Epic 3 resolved identifier from the X-PerfPilot-Token
            header or the perfpilot_token cookie. Epic 4: subject identifier supplied
            by whichever upstream auth middleware the operator chose
            (vendor-agnostic; e.g. ACA Easy Auth + EntraID `oid`, AWS ALB
            + Cognito, GCP IAP, oauth2-proxy). See Decision 20 in the
            Epic 3 status doc.
        metadata: Arbitrary JSONB payload (UI client info, etc.).

    Returns:
        The new `AgentSession`. The server-side default generates `session_id`
        via `gen_random_uuid()`.
    """
    if source not in VALID_SOURCES:
        log.warning("create_session received unknown source '%s' (expected one of %s)", source, VALID_SOURCES)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_sessions (external_session_id, source, user_id, metadata)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING session_id, external_session_id, source, user_id, metadata,
                      started_at, ended_at, last_activity_at
            """,
            external_session_id,
            source,
            user_id,
            json.dumps(metadata or {}),
        )
    return _row_to_session(row)


async def get_session(session_id: UUID) -> Optional[AgentSession]:
    """Fetch a single session by id. Returns None when not found."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT session_id, external_session_id, source, user_id, metadata,
                   started_at, ended_at, last_activity_at
            FROM agent_sessions
            WHERE session_id = $1
            """,
            session_id,
        )
    return _row_to_session(row) if row is not None else None


async def touch_session(session_id: UUID) -> None:
    """Bump `last_activity_at` to NOW() for inactivity-timeout tracking."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET last_activity_at = NOW() WHERE session_id = $1",
            session_id,
        )


async def end_session(session_id: UUID) -> None:
    """Mark a session ended by setting `ended_at = NOW()`. Idempotent."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_sessions SET ended_at = NOW() WHERE session_id = $1 AND ended_at IS NULL",
            session_id,
        )


async def delete_session(session_id: UUID) -> bool:
    """Delete a session row. Returns True when a row was removed.

    Intended for tests and operator clean-up only. Production retention is
    managed via inactivity sweeps (added in a later Feature).
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM agent_sessions WHERE session_id = $1", session_id)
    return result.endswith(" 1")


async def list_sessions(
    *,
    limit: int = 50,
    offset: int = 0,
    source: Optional[str] = None,
    include_ended: bool = False,
    user_id: Optional[str] = None,
) -> list[AgentSession]:
    """Return recent sessions ordered by `last_activity_at DESC`.

    Used by the AG-UI bridge (PBI 3.6.4) to power the browser's session
    picker and the "come back tomorrow" UX. Pagination is offset-based for
    Epic 3 simplicity; cursor pagination can be added in a later Feature.

    Args:
        limit: Max rows to return. Capped at 200.
        offset: Skip this many rows.
        source: Optional filter on the `source` column (e.g. `web_ui`).
        include_ended: If False (default), excludes rows where `ended_at`
            is set. Most browser views want only live sessions.
        user_id: When provided, restrict to sessions owned by this user.
            Used by F3.7.0b owner-filtering so Alice never sees Bob's
            sessions on `GET /api/sessions`. When None (the default) all
            sessions are returned regardless of owner.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where_parts: list[str] = []
    args: list[Any] = []
    if source is not None:
        args.append(source)
        where_parts.append(f"source = ${len(args)}")
    if user_id is not None:
        args.append(user_id)
        where_parts.append(f"user_id = ${len(args)}")
    if not include_ended:
        where_parts.append("ended_at IS NULL")
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    args.extend([limit, offset])
    query = f"""
        SELECT session_id, external_session_id, source, user_id, metadata,
               started_at, ended_at, last_activity_at
        FROM agent_sessions
        {where_clause}
        ORDER BY last_activity_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
    """

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [_row_to_session(r) for r in rows]
