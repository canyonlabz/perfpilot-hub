"""Shared AG2 multi-turn tool-execution loop.

Used by the orchestrator (``services.task_executor``) and MCP specialist
runtimes (``services.specialist_agents``). Owns the LLM call wrapper,
per-tool execution against an AG2 agent, and the async multi-round loop
with HITL gates, token-pressure tracking, and compaction.

Depends on:
  * ``services.helpers.token_context_window`` — estimate / compact
  * ``services.subscriptions`` — SSE ``broadcast`` / ``TaskEvent``
  * ``services.hitl_gates`` — mid-loop write-action gates
  * ``stores.trace_store`` — token ledger + tool-call traces

Must not import ``task_executor`` or ``specialist_agents``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from services import hitl_gates
from services.helpers.token_context_window import (
    compact_tool_results,
    estimate_token_count,
)
from services.subscriptions import TaskEvent, broadcast as _broadcast
from stores import task_store
from stores import trace_store

log = logging.getLogger(__name__)


# =============================================================================
# AG2 agent invocation (sync, runs inside asyncio.to_thread)
# =============================================================================

def generate_reply_sync(
    agent: Any,
    messages: list[dict],
) -> Any:
    """Thin sync wrapper around AG2's ``generate_reply()``.

    This is the ONLY synchronous operation in the specialist loop —
    AG2's LLM call.  Runs inside ``asyncio.to_thread()`` so the async
    event loop is not blocked.

    Returns whatever AG2 returns: a ``str`` (text response), a
    ``dict`` (tool call suggestion), or ``None`` (model failure).
    """
    return agent.generate_reply(messages=messages)


async def execute_tool_on_agent(
    agent: Any,
    fn_name: str,
    fn_args: dict,
) -> str:
    """Look up a registered tool function on the agent and execute it.

    AG2's ``register_for_execution()`` stores callables in
    ``agent._function_map``.  The MCP tool wrappers from
    ``mcp_tool_registry`` are async coroutines — we ``await`` them
    directly.  Any sync functions are dispatched via ``to_thread``.

    Always returns a string (JSON-serialized result or error).
    """
    import inspect

    fn = agent._function_map.get(fn_name)
    if fn is None:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "ToolNotFound",
                "message": f"No registered execution function for '{fn_name}'",
            },
        })

    try:
        if inspect.iscoroutinefunction(fn):
            result = await fn(**fn_args)
        else:
            result = await asyncio.to_thread(fn, **fn_args)
    except Exception as e:
        log.exception("Tool execution failed: %s", fn_name)
        return json.dumps({
            "ok": False,
            "error": {
                "type": type(e).__name__,
                "message": str(e)[:500],
            },
        })

    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


async def run_multi_turn_tool_loop(
    agent: Any,
    messages: list[dict],
    *,
    max_tool_rounds: int,
    max_consecutive_repeats: int,
    polling_tools: frozenset[str] = frozenset(),
    polling_max_consecutive_repeats: int = 30,
    context_limit: int,
    compaction_threshold: float,
    compaction_preserve_recent: int,
    task_id: Any,
    common: dict,
    agent_name: str,
    after_tool_round: Optional[Any] = None,
    task: Optional[task_store.AgentTask] = None,
) -> tuple[str, Any, list[dict], str, dict | None]:
    """Async multi-turn tool-execution loop for agents.

    Implements the loop engineering pattern:
      1. Call LLM (sync via to_thread)
      2. If text response → exit (success)
      3. If tool calls → execute each tool → feed results back → next round
      4. Exit conditions: max rounds, consecutive repeats, or errors

    Args:
        after_tool_round: Optional async callback invoked after all tool
            calls in a round are executed and before the next LLM call.
            Used by the orchestrator to drain ``_pending_executions`` and
            start specialist tasks immediately so ``check_task_status``
            sees them as running in subsequent rounds.

    Returns:
        Tuple of (reply_text, raw_reply, tool_rounds_audit, exit_reason,
        final_context_metrics) where final_context_metrics is a dict with
        context_tokens, context_utilization_pct, and context_limit (or None
        if no tool rounds executed).
    """
    tool_rounds: list[dict] = []
    last_tool_signature: str | None = None
    consecutive_repeats = 0
    final_context_metrics: dict | None = None

    for round_num in range(1, max_tool_rounds + 1):
        # ---- Context pressure tracking (B3) -------------------------------
        token_count = estimate_token_count(messages)
        utilization_pct = round(token_count / context_limit, 4) if context_limit > 0 else 0.0
        log.info(
            "%s task %s round %d: ~%d tokens, %.1f%% utilization (%d limit)",
            agent_name, task_id, round_num,
            token_count, utilization_pct * 100, context_limit,
        )

        # ---- Compaction trigger (B4) --------------------------------------
        if utilization_pct >= compaction_threshold:
            messages, compacted_count = compact_tool_results(
                messages, preserve_recent=compaction_preserve_recent,
            )
            if compacted_count > 0:
                new_token_count = estimate_token_count(messages)
                tokens_freed = token_count - new_token_count
                log.warning(
                    "%s task %s round %d: COMPACTION triggered at %.1f%% — "
                    "compacted %d tool results, freed ~%d tokens (now ~%d)",
                    agent_name, task_id, round_num,
                    utilization_pct * 100, compacted_count,
                    tokens_freed, new_token_count,
                )
                token_count = new_token_count
                utilization_pct = round(token_count / context_limit, 4) if context_limit > 0 else 0.0
        else:
            compacted_count = 0
            tokens_freed = 0

        # ---- Persist token ledger entry (B5, fire-and-forget) -------------
        trace_store.schedule_token_ledger_insert(
            task_id=task_id,
            agent_name=agent_name,
            loop_iteration=round_num,
            prompt_tokens=token_count,
            utilization_pct=utilization_pct,
            compaction_triggered=compacted_count > 0,
            compacted_count=compacted_count,
            tokens_freed=tokens_freed,
        )

        # ---- LLM call (sync AG2) ------------------------------------------
        reply = await asyncio.to_thread(generate_reply_sync, agent, messages)

        # ---- Exit: text response (success) --------------------------------
        if isinstance(reply, str):
            return reply, reply, tool_rounds, "text_response", final_context_metrics

        # ---- Exit: None / empty (model failure) ---------------------------
        if reply is None:
            return "", None, tool_rounds, "null_reply", final_context_metrics

        # ---- Dict response: check for tool calls -------------------------
        if isinstance(reply, dict):
            tool_calls = reply.get("tool_calls")
            if not tool_calls:
                content = reply.get("content", "")
                text = content if isinstance(content, str) else str(content) if content else ""
                return text, reply, tool_rounds, "text_response", final_context_metrics

            # ---- Execute tool calls ---------------------------------------
            messages.append(reply)
            round_audit: dict = {
                "round": round_num,
                "tool_calls": [],
                "results": [],
            }

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args_raw = tc["function"]["arguments"]
                fn_args = (
                    json.loads(fn_args_raw)
                    if isinstance(fn_args_raw, str)
                    else fn_args_raw
                )
                tc_id = tc["id"]

                round_audit["tool_calls"].append({"name": fn_name, "args": fn_args})

                # ---- Repetition detection ---------------------------------
                tool_sig = json.dumps(
                    {"name": fn_name, "args": fn_args}, sort_keys=True,
                )
                if tool_sig == last_tool_signature:
                    consecutive_repeats += 1
                else:
                    consecutive_repeats = 1
                    last_tool_signature = tool_sig

                is_polling = fn_name in polling_tools
                effective_limit = (
                    polling_max_consecutive_repeats
                    if is_polling
                    else max_consecutive_repeats
                )
                if consecutive_repeats >= effective_limit:
                    limit_label = "polling " if is_polling else ""
                    warning = (
                        f"[Loop exited: tool '{fn_name}' called with identical "
                        f"arguments {effective_limit} times consecutively "
                        f"({limit_label}threshold)]"
                    )
                    log.warning(
                        "%s task %s: %s", agent_name, task_id, warning,
                    )
                    return warning, reply, tool_rounds, "consecutive_repeat_limit", final_context_metrics

                # ---- Per-tool HITL gate (write actions) -------------------
                # Runs only when the caller passed ``task`` (mid-loop
                # task-aware mode). When ``task`` is None (legacy callers
                # such as the orchestrator's own tool loop), skip and
                # preserve prior behavior — the task-start gate remains
                # the only enforcement point.
                if task is not None:
                    async def _tool_progress(message: str) -> None:
                        await _broadcast(
                            task_id,
                            TaskEvent(status="running", progress=message, **common),
                        )

                    rejection = await hitl_gates.enforce_at_tool_call(
                        task, fn_name, fn_args, on_progress=_tool_progress,
                    )
                    if rejection is not None:
                        # Surface the rejection as a synthetic tool
                        # "result" so the LLM sees it in the next round
                        # and can react (typically: apologize + stop).
                        # Do NOT execute the tool. Continue the loop so
                        # the LLM has a chance to produce a final text
                        # reply.
                        rejection_payload = json.dumps({
                            "ok": False,
                            "error": {
                                "type": "HITLRejected",
                                "message": rejection,
                                "tool": fn_name,
                            },
                        })
                        round_audit["results"].append({
                            "tool": fn_name,
                            "ok": False,
                            "snippet": rejection_payload[:200],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": rejection_payload,
                        })
                        continue

                # ---- Execute the tool -------------------------------------
                t0 = time.perf_counter_ns()
                result_str = await execute_tool_on_agent(agent, fn_name, fn_args)
                latency_ms = trace_store.measure_latency_ms(t0)

                result_ok = True
                trace_error = None
                try:
                    parsed = json.loads(result_str)
                    if isinstance(parsed, dict) and parsed.get("ok") is False:
                        result_ok = False
                        trace_error = parsed.get("error")
                except (json.JSONDecodeError, TypeError):
                    pass

                # ---- Persist to tool_call_traces (fire-and-forget) --------
                trace_store.schedule_trace_insert(
                    task_id=task_id,
                    agent_name=agent_name,
                    tool_name=fn_name,
                    args=fn_args,
                    result=result_str,
                    error=trace_error,
                    latency_ms=latency_ms,
                )

                round_audit["results"].append({
                    "tool": fn_name,
                    "ok": result_ok,
                    "snippet": result_str[:200],
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_str,
                })

            tool_rounds.append(round_audit)

            # ---- Post-round callback (e.g. drain pending executions) ------
            if after_tool_round is not None:
                await after_tool_round()

            # ---- SSE progress broadcast -----------------------------------
            tool_names = [tc["name"] for tc in round_audit["tool_calls"]]
            final_context_metrics = {
                "context_tokens": token_count,
                "context_utilization_pct": round(utilization_pct * 100, 1),
                "context_limit": context_limit,
            }

            await _broadcast(
                task_id,
                TaskEvent(
                    status="running",
                    progress=f"tool_round_{round_num}_of_{max_tool_rounds}",
                    result={
                        "round": round_num,
                        "tools_called": tool_names,
                        "agent": agent_name,
                        **final_context_metrics,
                    },
                    **common,
                ),
            )
            continue

        # ---- Exit: unexpected reply type ----------------------------------
        return str(reply), reply, tool_rounds, "unexpected_reply_type", final_context_metrics

    # ---- Exit: max rounds reached -----------------------------------------
    log.warning(
        "%s task %s: max_tool_rounds (%d) reached",
        agent_name, task_id, max_tool_rounds,
    )
    return (
        f"[Max tool rounds ({max_tool_rounds}) reached — task may be incomplete]",
        None,
        tool_rounds,
        "max_rounds_reached",
        final_context_metrics,
    )

