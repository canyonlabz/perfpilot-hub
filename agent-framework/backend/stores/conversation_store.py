"""CRUD over the `conversation_messages` table.

`conversation_messages` is the append-only transcript per `(thread_id,
agent)`. PBI 3.7.6b uses it to power the `GET /api/threads/{thread_id}/
messages` endpoint that drives the ChatGPT-style sidebar's "click a
thread to see its history" UX. PBI 3.7.7 then uses the same helpers to
load message history before each AG2 turn, replacing CopilotKit's
browser-supplied `RunAgentInput.messages` as the source of truth
(Decision 14 - "DB is source of truth, keyed by thread_id").

Authorization filters do NOT live in this module. Route handlers MUST
verify that the requesting user owns the parent thread (via
`thread_store.get_thread().user_id`) before exposing message content.

Lazy heavy imports stay consistent with `session_store.py` and the rest
of the `utils/` package: only stdlib at module top, asyncpg work goes
through the shared `utils.db` pool.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from . import db

log = logging.getLogger(__name__)

VALID_ROLES = ("system", "user", "assistant", "tool")


@dataclass
class ConversationMessage:
    """Materialized view of a row in `conversation_messages`."""

    id: int
    thread_id: str
    agent_name: str
    role: str
    content: Any
    created_at: datetime


def _coerce_jsonb(value: Any) -> Any:
    """asyncpg may return JSONB columns as decoded objects or as raw strings."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _row_to_message(row: Any) -> ConversationMessage:
    return ConversationMessage(
        id=row["id"],
        thread_id=row["thread_id"],
        agent_name=row["agent_name"],
        role=row["role"],
        content=_coerce_jsonb(row["content"]),
        created_at=row["created_at"],
    )


async def append_message(
    thread_id: str,
    *,
    agent_name: str,
    role: str,
    content: Any,
) -> ConversationMessage:
    """Append a new message to the thread's transcript and return the row.

    Args:
        thread_id: The thread this message belongs to. No FK enforcement
            on the column itself, but PBI 3.7.7 always passes a value
            that exists in `agent_threads.thread_id`.
        agent_name: Author identifier (e.g. `"orchestrator"`, `"user"`).
            For user messages PBI 3.7.7 uses the literal string
            `"user"` as a convention so transcripts read naturally.
        role: One of `VALID_ROLES`. Raises `ValueError` otherwise.
        content: Message body. Stored as JSONB so it can be a plain
            string (user/assistant prose), a tool-call frame, structured
            output, or an attachment-aware shape -- the schema does not
            restrict the inner shape.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")

    # JSONB accepts any JSON-encoded value. `json.dumps("hello")` becomes
    # the JSONB string `"hello"`; `json.dumps({"a": 1})` becomes the
    # JSONB object `{"a": 1}` -- same call handles both shapes.
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_messages (thread_id, agent_name, role, content)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id, thread_id, agent_name, role, content, created_at
            """,
            thread_id,
            agent_name,
            role,
            json.dumps(content),
        )
    return _row_to_message(row)


async def list_for_thread(
    thread_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
    ascending: bool = True,
) -> list[ConversationMessage]:
    """Return the conversation transcript for `thread_id`.

    Args:
        thread_id: The thread to fetch messages for.
        limit: Max rows. Clamped to [1, 1000]. Defaults to 200 (enough
            for a long-running ChatGPT-style session without paging).
        offset: Skip this many rows. Useful for paginating older history
            from the sidebar.
        ascending: When True (default), oldest message first -- the
            order LLMs expect. Set False for "show me the last N
            messages newest-first" UI patterns.

    Returns:
        List of `ConversationMessage`, possibly empty. Never raises on
        no-results.
    """
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    order = "ASC" if ascending else "DESC"

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, thread_id, agent_name, role, content, created_at
            FROM conversation_messages
            WHERE thread_id = $1
            ORDER BY created_at {order}, id {order}
            LIMIT $2 OFFSET $3
            """,
            thread_id,
            limit,
            offset,
        )
    return [_row_to_message(r) for r in rows]


async def count_for_thread(thread_id: str) -> int:
    """Return the total number of messages on `thread_id`. Used for pagination."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM conversation_messages WHERE thread_id = $1",
            thread_id,
        )
    return int(row["n"] or 0) if row is not None else 0


async def delete_all_for_thread(thread_id: str) -> int:
    """Hard-delete every message on `thread_id`. Returns row count removed.

    Intended for tests and operator cleanup. Production retention is
    handled via the parent thread's soft-delete (`thread_store.
    soft_delete_thread`) which keeps the message history intact for
    auditability.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM conversation_messages WHERE thread_id = $1",
            thread_id,
        )
    # asyncpg execute result format: "DELETE <n>"
    try:
        return int(result.split()[-1])
    except (IndexError, ValueError):
        return 0
