"""CRUD over the `tool_call_traces` table.

`tool_call_traces` is the audit trail of every MCP tool call fired by an
agent during the multi-turn tool loop. It enables cost accounting,
debugging, and is the foundation for context compaction (replacing old
tool results in conversation history with compact references once the
full output is persisted here).

Design choices:
- **Fire-and-forget by default.** The primary caller (`task_executor.py`)
  uses `schedule_trace_insert()` which wraps the insert in
  `asyncio.create_task()`. Failures are logged but never propagate to
  the tool loop — trace persistence must not break automation runs.
- **JSONB coercion.** `args`, `result`, and `error` columns are JSONB.
  This module accepts dicts, lists, strings, or None and serializes
  appropriately.
- **Lazy heavy imports.** Consistent with the rest of `utils/`.

Schema reference: `agent-framework/sql/006_create_tool_call_traces.sql`
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from . import db

log = logging.getLogger(__name__)


def _to_jsonb(value: Any) -> Optional[str]:
    """Serialize a value to a JSON string suitable for asyncpg JSONB insert.

    Returns None if the input is None (maps to SQL NULL).
    Strings are wrapped as JSON strings. Dicts/lists serialize directly.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except (json.JSONDecodeError, TypeError):
            return json.dumps(value)
    return json.dumps(value, default=str)


async def insert_tool_trace(
    *,
    task_id: Any,
    agent_name: str,
    tool_name: str,
    args: Any,
    result: Any = None,
    error: Any = None,
    latency_ms: Optional[int] = None,
) -> Optional[int]:
    """Insert a tool call trace row and return the row id.

    Args:
        task_id: UUID of the owning agent_task (FK to agent_tasks).
        agent_name: The agent that fired the tool call.
        tool_name: Namespaced MCP tool name (e.g., `jmeter_run_smoke_test`).
        args: Tool call arguments (dict or JSON string).
        result: Tool call result (dict, string, or None).
        error: Error payload if the call failed (dict, string, or None).
        latency_ms: Round-trip execution time in milliseconds.

    Returns:
        The row `id` on success, or None if the insert failed.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tool_call_traces
                (task_id, agent_name, mcp_tool_name, args, result, error, latency_ms)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7)
            RETURNING id
            """,
            task_id,
            agent_name,
            tool_name,
            _to_jsonb(args),
            _to_jsonb(result),
            _to_jsonb(error),
            latency_ms,
        )
    return row["id"] if row else None


def schedule_trace_insert(
    *,
    task_id: Any,
    agent_name: str,
    tool_name: str,
    args: Any,
    result: Any = None,
    error: Any = None,
    latency_ms: Optional[int] = None,
) -> None:
    """Fire-and-forget wrapper around insert_tool_trace().

    Schedules the DB write as a background task. Failures are logged at
    WARNING level but never propagate to the caller. This ensures that
    trace persistence cannot break the multi-turn tool loop.
    """

    async def _safe_insert() -> None:
        try:
            await insert_tool_trace(
                task_id=task_id,
                agent_name=agent_name,
                tool_name=tool_name,
                args=args,
                result=result,
                error=error,
                latency_ms=latency_ms,
            )
        except Exception:
            log.warning(
                "Failed to persist tool_call_trace for %s/%s (task %s)",
                agent_name,
                tool_name,
                task_id,
                exc_info=True,
            )

    asyncio.create_task(_safe_insert())


def measure_latency_ms(start_ns: int) -> int:
    """Convert a `time.perf_counter_ns()` start value to elapsed milliseconds."""
    return int((time.perf_counter_ns() - start_ns) / 1_000_000)


# ---------------------------------------------------------------------------
# Token ledger (B5) — per-iteration context pressure metrics
# ---------------------------------------------------------------------------


async def insert_token_ledger_entry(
    *,
    task_id: Any,
    agent_name: str,
    loop_iteration: int,
    prompt_tokens: int,
    utilization_pct: float,
    completion_tokens: Optional[int] = None,
    mcp_overhead_tokens: Optional[int] = None,
    compaction_triggered: bool = False,
    compacted_count: int = 0,
    tokens_freed: int = 0,
) -> Optional[int]:
    """Insert a token ledger row and return the row id.

    Args:
        task_id: UUID of the owning agent_task.
        agent_name: The agent running the loop.
        loop_iteration: 1-indexed loop round number.
        prompt_tokens: Estimated tokens in the messages before LLM call.
        utilization_pct: Fraction of context window used (0.0–1.0).
        completion_tokens: Tokens in the LLM response (if available).
        mcp_overhead_tokens: Estimated tokens from MCP tool results.
        compaction_triggered: Whether compaction fired this iteration.
        compacted_count: Number of tool results compacted.
        tokens_freed: Tokens reclaimed by compaction.

    Returns:
        The row `id` on success, or None if the insert failed.
    """
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO token_ledger
                (task_id, agent_name, loop_iteration, prompt_tokens,
                 completion_tokens, mcp_overhead_tokens, utilization_pct,
                 compaction_triggered, compacted_count, tokens_freed)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
            """,
            task_id,
            agent_name,
            loop_iteration,
            prompt_tokens,
            completion_tokens,
            mcp_overhead_tokens,
            utilization_pct,
            compaction_triggered,
            compacted_count,
            tokens_freed,
        )
    return row["id"] if row else None


def schedule_token_ledger_insert(
    *,
    task_id: Any,
    agent_name: str,
    loop_iteration: int,
    prompt_tokens: int,
    utilization_pct: float,
    completion_tokens: Optional[int] = None,
    mcp_overhead_tokens: Optional[int] = None,
    compaction_triggered: bool = False,
    compacted_count: int = 0,
    tokens_freed: int = 0,
) -> None:
    """Fire-and-forget wrapper for insert_token_ledger_entry()."""

    async def _safe_insert() -> None:
        try:
            await insert_token_ledger_entry(
                task_id=task_id,
                agent_name=agent_name,
                loop_iteration=loop_iteration,
                prompt_tokens=prompt_tokens,
                utilization_pct=utilization_pct,
                completion_tokens=completion_tokens,
                mcp_overhead_tokens=mcp_overhead_tokens,
                compaction_triggered=compaction_triggered,
                compacted_count=compacted_count,
                tokens_freed=tokens_freed,
            )
        except Exception:
            log.warning(
                "Failed to persist token_ledger entry for %s round %d (task %s)",
                agent_name,
                loop_iteration,
                task_id,
                exc_info=True,
            )

    asyncio.create_task(_safe_insert())
