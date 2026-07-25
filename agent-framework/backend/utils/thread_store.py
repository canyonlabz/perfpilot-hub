"""CRUD over the `agent_threads` table.

Threads are the persistent conversation containers. A thread survives across
sessions, devices, and days. See `agent-framework/sql/008_create_agent_threads.sql`
for the full DDL rationale and the F3.7 plan in
`docs/plans/Epic-3-Implementation-Status.md` Section 5 for the lifetime model.

Ownership is one of two flavours, enforced by the table's CHECK constraint:

  * Web UI / IDE / CLI threads -> `user_id` is set (caller principal)
  * A2A-originated threads     -> `external_thread_id` is set (upstream label)

Authorization filters live in this module's helpers (`list_for_user`,
`get_by_external_thread_id`) and on the route handlers that call them.

Status: minimal-but-real for PBI 3.7.0. Helpers added here:

    create                      - insert or upsert by external_thread_id
    get                         - fetch by internal `thread_id`
    get_by_external_thread_id   - fetch by upstream label
    list_for_user               - sidebar list for a Web UI / IDE user
    touch                       - bump `last_message_at` after appending a message
    set_title                   - rename a thread
    set_status                  - active / archived / deleted toggle
    archive / unarchive / delete- thin wrappers over `set_status`

Async-only. Uses the shared asyncpg pool from `utils.db`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from . import db

log = logging.getLogger(__name__)

# Mirrors `agent_sessions.source` vocabulary so the two stores share a single
# origin taxonomy. Free-text is allowed; unknown values log a warning at
# `create_thread()`.
VALID_SOURCES = ("web_ui", "cursor", "claude", "a2a_external", "cli")

VALID_STATUSES = ("active", "archived", "deleted")


@dataclass
class AgentThread:
    """Materialized view of a row in `agent_threads`."""

    thread_id: str
    source: str
    status: str
    created_at: datetime
    updated_at: datetime
    user_id: Optional[str] = None
    external_thread_id: Optional[str] = None
    title: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    last_message_at: Optional[datetime] = None


# All SELECT statements use this column list verbatim so `_row_to_thread`
# never goes out of sync with the queries.
_SELECT_COLS = (
    "thread_id, user_id, external_thread_id, source, title, status, metadata, "
    "created_at, updated_at, last_message_at"
)


def _row_to_thread(row: Any) -> AgentThread:
    """Convert an asyncpg `Record` to an `AgentThread`."""
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return AgentThread(
        thread_id=row["thread_id"],
        user_id=row["user_id"],
        external_thread_id=row["external_thread_id"],
        source=row["source"],
        title=row["title"],
        status=row["status"],
        metadata=metadata or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
    )


async def create_thread(
    source: str,
    *,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    external_thread_id: Optional[str] = None,
    title: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> AgentThread:
    """Insert a new row in `agent_threads` and return the materialized view.

    Args:
        source: Origin tag, ideally one of `VALID_SOURCES`. Free-text is
            allowed but a warning is logged when an unknown value is used.
        thread_id: Optional caller-supplied stable ID. For Web UI threads
            this is the CopilotKit `threadId`. If omitted the server-side
            DEFAULT mints a fresh UUID hex.
        user_id: Web UI / IDE / CLI owner. Required when
            `external_thread_id` is None (CHECK constraint).
        external_thread_id: A2A persistent thread label. Required when
            `user_id` is None.
        title: Optional initial display title for the sidebar.
        metadata: Free-form JSONB payload.

    Returns:
        The new `AgentThread`. Status defaults to `'active'`.

    Raises:
        ValueError: If neither `user_id` nor `external_thread_id` is set
            (caught here rather than letting the DB CHECK constraint fire,
            so callers get a clearer error message).
    """
    if user_id is None and external_thread_id is None:
        raise ValueError(
            "create_thread requires either user_id (Web UI / IDE / CLI) or "
            "external_thread_id (A2A); both were None."
        )
    if source not in VALID_SOURCES:
        log.warning(
            "create_thread received unknown source '%s' (expected one of %s)",
            source,
            VALID_SOURCES,
        )

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # We let the DB default the thread_id when the caller didn't supply
        # one. Two INSERT variants keep the SQL simple and avoid sentinel
        # values that confuse asyncpg's parameter typing.
        if thread_id is None:
            row = await conn.fetchrow(
                f"""
                INSERT INTO agent_threads
                    (user_id, external_thread_id, source, title, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING {_SELECT_COLS}
                """,
                user_id,
                external_thread_id,
                source,
                title,
                json.dumps(metadata or {}),
            )
        else:
            row = await conn.fetchrow(
                f"""
                INSERT INTO agent_threads
                    (thread_id, user_id, external_thread_id, source, title, metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING {_SELECT_COLS}
                """,
                thread_id,
                user_id,
                external_thread_id,
                source,
                title,
                json.dumps(metadata or {}),
            )
    return _row_to_thread(row)


async def get_thread(thread_id: str) -> Optional[AgentThread]:
    """Fetch a single thread by internal `thread_id`. Returns None when not found.

    No authorization check is applied here. Route handlers MUST filter on
    `user_id` (Web UI / IDE / CLI) or `external_thread_id` (A2A) before
    returning the row to the caller.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLS} FROM agent_threads WHERE thread_id = $1",
            thread_id,
        )
    return _row_to_thread(row) if row is not None else None


async def get_by_external_thread_id(external_thread_id: str) -> Optional[AgentThread]:
    """Fetch a thread by the A2A `external_thread_id` label.

    This is the lookup the A2A executor performs when an upstream caller
    presents `X-External-Thread-Id`. Returns None when no thread carries
    that label yet (executor then creates one and returns its
    `external_thread_id` in the response).
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLS} FROM agent_threads WHERE external_thread_id = $1",
            external_thread_id,
        )
    return _row_to_thread(row) if row is not None else None


async def list_for_user(
    user_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = "active",
    include_archived: bool = False,
) -> list[AgentThread]:
    """List threads owned by a Web UI / IDE / CLI user.

    Powers the ChatGPT-style sidebar in F3.7.6b. Orders by
    `last_message_at DESC NULLS LAST` so freshly-created threads with no
    messages yet still bubble to the top once the first message lands.

    Args:
        user_id: The resolved user identifier. Threads with NULL `user_id`
            (i.e., A2A-originated) are never returned by this function.
        limit: Max rows. Capped at 200.
        offset: Skip this many rows.
        status: If set, filter by exact status value
            (default `'active'`). Pass `None` together with
            `include_archived=True` to return everything except `'deleted'`.
        include_archived: When True and `status` is None, includes
            `'archived'` rows alongside `'active'`. `'deleted'` is always
            excluded by this helper; use direct SQL for tombstone recovery.

    Returns:
        Ordered list (possibly empty). Never raises on no-results.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where_parts: list[str] = ["user_id = $1"]
    args: list[Any] = [user_id]
    if status is not None:
        if status not in VALID_STATUSES:
            log.warning(
                "list_for_user received unknown status '%s' (expected one of %s); coercing to active",
                status,
                VALID_STATUSES,
            )
            status = "active"
        args.append(status)
        where_parts.append(f"status = ${len(args)}")
    elif include_archived:
        where_parts.append("status IN ('active', 'archived')")
    else:
        where_parts.append("status = 'active'")

    args.extend([limit, offset])
    query = f"""
        SELECT {_SELECT_COLS}
        FROM agent_threads
        WHERE {' AND '.join(where_parts)}
        ORDER BY last_message_at DESC NULLS LAST, created_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
    """

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [_row_to_thread(r) for r in rows]


async def touch_thread(thread_id: str) -> None:
    """Bump `last_message_at` and `updated_at` to NOW().

    Called by the message-persistence path after appending a row to
    `conversation_messages`. Re-orders the sidebar list.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_threads
               SET last_message_at = NOW(),
                   updated_at      = NOW()
             WHERE thread_id = $1
            """,
            thread_id,
        )


async def set_title(thread_id: str, title: Optional[str]) -> Optional[AgentThread]:
    """Rename the thread. Pass `title=None` to clear.

    Returns the updated row, or None when no thread carries that id.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE agent_threads
               SET title      = $2,
                   updated_at = NOW()
             WHERE thread_id = $1
            RETURNING {_SELECT_COLS}
            """,
            thread_id,
            title,
        )
    return _row_to_thread(row) if row is not None else None


async def set_status(thread_id: str, status: str) -> Optional[AgentThread]:
    """Move the thread to `active`, `archived`, or `deleted`.

    Returns the updated row, or None when no thread carries that id.

    Raises:
        ValueError: When `status` is not in `VALID_STATUSES`.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got '{status}'")

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE agent_threads
               SET status     = $2,
                   updated_at = NOW()
             WHERE thread_id = $1
            RETURNING {_SELECT_COLS}
            """,
            thread_id,
            status,
        )
    return _row_to_thread(row) if row is not None else None


async def archive_thread(thread_id: str) -> Optional[AgentThread]:
    """Soft-archive a thread (status -> 'archived'). Thin wrapper over `set_status`."""
    return await set_status(thread_id, "archived")


async def unarchive_thread(thread_id: str) -> Optional[AgentThread]:
    """Restore an archived thread to active (status -> 'active')."""
    return await set_status(thread_id, "active")


async def soft_delete_thread(thread_id: str) -> Optional[AgentThread]:
    """Mark a thread as deleted without dropping the row.

    `conversation_messages` rows belonging to a soft-deleted thread are
    retained for auditability. Use `hard_delete_thread` for unrecoverable
    removal.
    """
    return await set_status(thread_id, "deleted")


async def hard_delete_thread(thread_id: str) -> bool:
    """Delete the row entirely. Returns True when a row was removed.

    Intended for tests and operator clean-up only. Production retention is
    managed via the soft-delete path above.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM agent_threads WHERE thread_id = $1",
            thread_id,
        )
    return result.endswith(" 1")
