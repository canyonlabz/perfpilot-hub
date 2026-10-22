"""Background task runner for the A2A server (V2 doc Section 14).

Owns the lifecycle of an `agent_tasks` row from `pending` through one of the
terminal states (`completed`, `failed`, `cancelled`). Three things are
implemented here:

  1. **State-transition publication.** While a task runs, every status change
     is broadcast to in-process subscribers (`subscribe()` / `unsubscribe()`)
     so the SSE endpoint can stream events to the caller without polling
     the database.

  2. **Webhook delivery.** When a task reaches a terminal state, every URL
     in `agent_tasks.subscriber_endpoints` receives a `POST` of the final
     status payload. Three retries with exponential backoff (per V2
     Section 14.2). Webhook failures are logged but do not change the task
     status - the task is still authoritatively `completed` in the DB.

  3. **Agent dispatch.** `_dispatch_agent()` picks the right runtime per
     `task.agent_name`:
       - `"orchestrator"` (PBI 3.7.8): builds the real AG2 orchestrator
         from `agents/orchestrator/agent.py::build_orchestrator()`, loads
         any prior `conversation_messages` rows tied to the task's
         A2A thread (Decision 14), runs `generate_reply` with the full
         history, then persists the new user + assistant turns. The
         orchestrator's four registered tools (PBIs 3.7.3-3.7.6) are
         available; specialist delegation is in-process via
         `task_store` + `task_executor` (no HTTP hop to the A2A surface).
       - `"execution-agent"` (PBI 3.8.6): runs `_run_execution_agent()`,
         which reads the task payload's `tool` field as an EXPLICIT
         dispatch key (no LLM loop) and routes to one of the three F3.8
         agent tools (`start_performance_test`, `wait_for_completion`,
         `extract_test_run_artifacts`). The `action` field is echoed
         into the result envelope for audit; tool-side failures surface
         as `tool_result.ok = False` rather than raising. Closes the
         F3.7 -> F3.8 contract: when the orchestrator delegates to
         `execution-agent` via `delegate_to_specialist`, the task now
         performs real work instead of the stub 3-phase sleep.
       - Any other agent name: keeps the F3.5 stub workflow
         (`pending -> running -> completed` with a 3-second simulated
         runtime). Specialists ship in F3.9+ and will replace their stub
         dispatch one at a time.

Heavy imports (`httpx`, `asyncpg` via task_store) are reached only inside
the dispatch coroutine so this module imports cleanly in environments
without those packages.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional
from uuid import UUID

from services import hitl_gates
from services.subscriptions import (
    TaskEvent,
    broadcast as _broadcast,
    register_parent_task,
    subscribe,
    unsubscribe,
)
from services.helpers.tool_loop import run_multi_turn_tool_loop
from services.specialist_agents import (
    ANALYSIS_AGENT_NAME,
    EXECUTION_AGENT_NAME,
    EXECUTION_AGENT_TOOL_NAMES,
    MONITORING_AGENT_NAME,
    REPORTING_AGENT_NAME,
    SCRIPT_AGENT_NAME,
    _run_analysis_agent,
    _run_execution_agent,
    _run_monitoring_agent,
    _run_reporting_agent,
    _run_script_agent,
    _run_stub_agent,
)

from stores import task_store

log = logging.getLogger(__name__)

# Subscription-bus symbols (TaskEvent, subscribe, unsubscribe,
# register_parent_task, _broadcast) are re-exported from
# ``services.subscriptions`` via the imports above so existing callers
# keep working unchanged. Canonical home: ``services.subscriptions``.

WEBHOOK_RETRY_ATTEMPTS = 3
WEBHOOK_BACKOFF_BASE_SECONDS = 1.0
WEBHOOK_TIMEOUT_SECONDS = 15.0

# =============================================================================
# Push notification: inject completion message into conversation thread
# =============================================================================

def _build_completion_summary(task: task_store.AgentTask) -> str:
    """Compose a short orchestrator-voice message summarizing task outcome."""
    agent = task.agent_name
    if task.status == "completed":
        result_snippet = ""
        if isinstance(task.result, dict):
            reply_text = task.result.get("reply_text", "")
            if reply_text:
                result_snippet = reply_text
            else:
                result_snippet = task.result.get("summary") or task.result.get("message") or ""
        msg = f"**{agent}** completed successfully."
        if result_snippet:
            # Truncate very long results for the chat bubble
            if len(result_snippet) > 1000:
                result_snippet = result_snippet[:1000] + "..."
            msg += f"\n\n{result_snippet}"
        if task.test_run_id:
            msg += f"\n\n*Test run ID: `{task.test_run_id}`*"
        return msg
    elif task.status == "failed":
        error_msg = ""
        if isinstance(task.error, dict):
            error_msg = task.error.get("message") or str(task.error)
        elif isinstance(task.error, str):
            error_msg = task.error
        return (
            f"**{agent}** encountered an error.\n\n"
            f"**Error:** {error_msg or 'Unknown error — check task logs.'}\n\n"
            f"*Task ID: `{task.task_id}`*"
        )
    return f"**{agent}** reached status `{task.status}`."


async def _inject_completion_message(task: task_store.AgentTask) -> None:
    """Persist the specialist's completion into the conversation thread.

    This is the server-side push notification (Change 1 from the push
    notification enhancement doc). The message appears in the thread's
    history so the next time the user's UI loads messages (or if a
    React polling hook is active), the result is visible without the
    user needing to ask "is it done?".
    """
    from stores import conversation_store, thread_store

    summary = _build_completion_summary(task)
    try:
        await conversation_store.append_message(
            task.thread_id,
            agent_name="orchestrator",
            role="assistant",
            content={"text": summary, "source": "task_executor_callback"},
        )
        await thread_store.touch_thread(task.thread_id)
        log.info(
            "Injected completion message for task %s into thread %s",
            task.task_id, task.thread_id,
        )
    except Exception:
        log.exception(
            "Failed to inject completion message for task %s thread %s",
            task.task_id, task.thread_id,
        )


async def execute_task(task_id: UUID) -> None:
    """Run the task end-to-end. Schedule with `asyncio.create_task(...)`."""
    # Local import matches the module's convention for core.* symbols
    # (keeps the file free of cycles if a future core submodule imports
    # back into services.*).
    from core.task_signals import TaskInputRequiredSignal

    task = await task_store.get_task(task_id)
    if task is None:
        log.error("execute_task: task %s not found", task_id)
        return

    if task.status in task_store.TERMINAL_STATUSES:
        log.info("execute_task: task %s already terminal (%s); nothing to do", task_id, task.status)
        return

    common = dict(
        task_id=str(task.task_id),
        session_id=str(task.session_id) if task.session_id else None,
        external_session_id=task.external_session_id,
        agent_name=task.agent_name,
    )

    try:
        await task_store.mark_running(task.task_id)
        await _broadcast(task.task_id, TaskEvent(status="running", progress="started", **common))

        # Config-driven HITL gate: pause before executing if approval is
        # required. The frontend polls /api/hitl/tasks/{task_id} and renders
        # an inline approval card. The gate blocks here until decided.
        #
        # Callback-injection design: hitl_gates.py stays free of TaskEvent /
        # _broadcast imports so it can be tested in isolation and avoids a
        # circular dependency with this module.
        async def _hitl_progress(message: str) -> None:
            await _broadcast(
                task.task_id,
                TaskEvent(status="running", progress=message, **common),
            )

        hitl_rejection = await hitl_gates.enforce_at_task_start(
            task, on_progress=_hitl_progress,
        )
        if hitl_rejection is not None:
            await task_store.mark_cancelled(task.task_id, reason=hitl_rejection)
            await _broadcast(
                task.task_id,
                TaskEvent(
                    status="cancelled",
                    error={"reason": hitl_rejection},
                    **common,
                ),
            )
            return

        result = await _dispatch_agent(task, common)
        await task_store.mark_completed(task.task_id, result)
        await _broadcast(task.task_id, TaskEvent(status="completed", result=result, **common))
    except asyncio.CancelledError:
        await task_store.mark_cancelled(task.task_id, reason="execution cancelled")
        await _broadcast(
            task.task_id,
            TaskEvent(status="cancelled", error={"reason": "execution cancelled"}, **common),
        )
        raise
    except TaskInputRequiredSignal as sig:
        # A2A §4.1.3 TASK_STATE_INPUT_REQUIRED (non-terminal). The agent
        # cannot proceed without additional client input. Persist the
        # structured prompt + reasonCode so the SSE stream can emit a
        # spec-compliant TaskStatusUpdateEvent with TaskStatus.message,
        # and so the HTTP poll endpoint sees the same info on reload.
        # The client resumes by sending a follow-up message carrying the
        # same taskId + contextId (A2A §6.3 multi-turn interaction).
        log.info(
            "execute_task: task %s needs input (%s)",
            task_id,
            sig.reason_code,
        )
        await task_store.mark_input_required(
            task.task_id,
            question_text=sig.question_text,
            reason_code=sig.reason_code,
            reason_data=sig.reason_data,
        )
        await _broadcast(
            task.task_id,
            TaskEvent(
                status="input_required",
                input_required_text=sig.question_text,
                input_required_reason_code=sig.reason_code,
                input_required_data=sig.reason_data,
                **common,
            ),
        )
    except Exception as exc:
        log.exception("execute_task: task %s failed", task_id)
        error = {"type": type(exc).__name__, "message": str(exc)}
        await task_store.mark_failed(task.task_id, error)
        await _broadcast(task.task_id, TaskEvent(status="failed", error=error, **common))

    # Reload to get the final terminal row (DB is source of truth) and fan out webhooks.
    final = await task_store.get_task(task.task_id)
    if final is not None and final.subscriber_endpoints:
        await _deliver_webhooks(final)

    # ---- Push notification: inject completion message into conversation thread
    # So the user sees the result without needing to ask "is it done?"
    if final is not None and final.thread_id and final.status in task_store.TERMINAL_STATUSES:
        await _inject_completion_message(final)


# =============================================================================
# Agent dispatch
# =============================================================================
# PBI 3.7.8: the orchestrator runs the real AG2 agent; other agent names
# continue to use the F3.5 stub workflow until their own F3.9+ scaffolds
# replace them. The dispatch table is intentionally a simple if/elif --
# at seven agents total a registry pattern would over-engineer it.

ORCHESTRATOR_AGENT_NAME = "orchestrator"

# Specialist agent names, tool lists, and runtimes live in
# ``services.specialist_agents`` and are re-imported above so
# existing ``task_executor.EXECUTION_AGENT_NAME`` / test and smoke
# imports keep working.

# =============================================================================
# HITL gate infrastructure — MOVED
# =============================================================================
# The `HitlGateRule` dataclass, rule list, matcher, enforcers, and
# publish-tool resolver now live in `services/hitl_gates.py`. That
# module is the single source of truth for HITL policy across the
# framework (Web UI, orchestrator delegation, A2A, SDK). Adding a new
# HITL gate is documented at `docs/agent-framework/hitl-configuration.md`.


def _load_orchestrator_config() -> dict:
    """Load the full orchestrator config.yaml (cached by config_loader)."""
    from utils import config_loader

    return config_loader.load_agent_config("orchestrator")


def _get_pipeline_config() -> dict:
    """Return the ``pipeline:`` section of the orchestrator config."""
    cfg = _load_orchestrator_config()
    pipeline = cfg.get("pipeline")
    return pipeline if isinstance(pipeline, dict) else {}


async def _dispatch_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Route to the right runtime based on `task.agent_name`."""
    if task.agent_name == ORCHESTRATOR_AGENT_NAME:
        return await _run_orchestrator(task, common)
    if task.agent_name == EXECUTION_AGENT_NAME:
        return await _run_execution_agent(task, common)
    if task.agent_name == MONITORING_AGENT_NAME:
        return await _run_monitoring_agent(task, common)
    if task.agent_name == ANALYSIS_AGENT_NAME:
        return await _run_analysis_agent(task, common)
    if task.agent_name == REPORTING_AGENT_NAME:
        return await _run_reporting_agent(task, common)
    if task.agent_name == SCRIPT_AGENT_NAME:
        return await _run_script_agent(task, common)
    return await _run_stub_agent(task, common)


async def _run_orchestrator(task: task_store.AgentTask, common: dict) -> dict:
    """Run the real PerfPilot Orchestrator agent against the task payload.

    Behavior (PBI 3.7.8 + Decision 14):
      1. Resolve the A2A thread for this task. The thread label was
         resolved by `a2a_server` at request time and stored in
         `task.payload["_perfpilot_thread"]`. If absent (e.g. a legacy
         caller that bypasses the resolver), fall back to "no thread"
         single-turn behavior.
      2. Extract the user's message from the payload. Accepts
         `payload["text"]`, `payload["message"]`, or the whole payload
         as a fallback (whatever the caller sent gets stringified so the
         agent always receives a coherent prompt).
      3. Load prior conversation history for the thread (when known).
      4. Persist the new user message.
      5. Run the multi-turn tool-execution loop via
         ``_invoke_orchestrator_with_tool_loop()``. The orchestrator's
         four tools (``delegate_to_specialist``, ``check_task_status``,
         ``list_available_specialists``, ``request_human_approval``) are
         executed within the loop. After each tool round, any specialist
         tasks created by ``delegate_to_specialist`` are started
         immediately so ``check_task_status`` sees them as running.
      6. Persist the assistant reply.
      7. Return a structured result dict the A2A response wraps verbatim,
         including the tool rounds audit trail and exit reason.

    A2A identity propagation: ``session_id``, ``thread_id``, and
    ``user_id`` from the parent orchestrator task are propagated to child
    specialist tasks via ContextVars (``agent_session_id_var``,
    ``agent_thread_id_var``, ``agent_user_id_var``).
    """
    from a2a.server.helpers.payload import (
        extract_resume_messages_from_payload,
        extract_thread_id_from_payload,
        extract_user_message_from_payload,
        resume_message_to_text,
    )

    thread_id = extract_thread_id_from_payload(task.payload)
    user_message = extract_user_message_from_payload(task.payload)

    # A2A §6.3 resume messages — follow-up user turns appended to
    # payload["resume_messages"] by task_store.append_resume_message
    # when a client answers an input_required prompt. Extract their
    # text once so both the LLM context and the resolver see the new
    # content.
    resume_messages = extract_resume_messages_from_payload(task.payload)
    resume_texts: list[str] = []
    for rm in resume_messages:
        rm_text = resume_message_to_text(rm)
        if rm_text:
            resume_texts.append(rm_text)

    # Build the "effective" user text passed to the test_run_id
    # resolver so a resumed task that previously raised
    # TaskInputRequiredSignal(missing_test_run_id) can satisfy the
    # resolver on its next pass (the resume text typically carries the
    # test_run_id the user supplied).
    if resume_texts:
        if user_message:
            effective_user_text = user_message + "\n\n" + "\n\n".join(resume_texts)
        else:
            effective_user_text = "\n\n".join(resume_texts)
    else:
        effective_user_text = user_message

    # Phase markers so SSE consumers see liveness signals during a
    # potentially long LLM call.
    await _broadcast(task.task_id, TaskEvent(status="running", progress="loading_history", **common))

    history = await _load_thread_history_as_ag2_messages(thread_id) if thread_id else []

    if user_message and thread_id and not resume_texts:
        # Only persist the original user message on the first execution.
        # On resume, the original turn is already in conversation_store
        # from the previous pass, so we only persist the new resume
        # turns below.
        try:
            from stores import conversation_store

            await conversation_store.append_message(
                thread_id,
                agent_name="user",
                role="user",
                content={"text": user_message, "payload": task.payload},
            )
        except Exception:
            log.exception("_run_orchestrator: failed to persist user message; continuing")

    # Persist each resume turn to the conversation thread so later
    # history loads include the follow-up content and so the Web UI
    # renders a coherent transcript across turns.
    if resume_texts and thread_id:
        try:
            from stores import conversation_store

            for rm_text in resume_texts:
                await conversation_store.append_message(
                    thread_id,
                    agent_name="user",
                    role="user",
                    content={"text": rm_text, "payload": task.payload},
                )
        except Exception:
            log.exception("_run_orchestrator: failed to persist resume messages; continuing")

    messages_for_llm = list(history)
    if user_message and not resume_texts:
        # On the first pass, append the original user turn (history
        # does not yet contain it since persistence happens just above).
        messages_for_llm.append({"role": "user", "content": user_message})
    for rm_text in resume_texts:
        messages_for_llm.append({"role": "user", "content": rm_text})

    await _broadcast(task.task_id, TaskEvent(status="running", progress="invoking_llm", **common))

    # ── Normalize and persist test spec from A2A parts ──────────────
    # If the incoming payload contains parts[] with test case content,
    # normalize it to the step-based Markdown format and save it under
    # artifacts/{test_run_id}/test-specs/. The file path is propagated
    # to specialist agents via the agent_spec_file_var ContextVar so
    # delegate_to_specialist auto-injects it into child task payloads.
    from services.helpers.test_specs import persist_test_spec_from_parts
    persist_test_spec_from_parts(task)

    # Propagate the task owner's user_id, session_id, thread_id, and
    # request source into the orchestrator's tool execution context so
    # `delegate_to_specialist` can create child tasks with the correct
    # A2A identity chain. Setting source to "a2a" tells the orchestrator
    # to delegate via the A2A surface.
    from agents.orchestrator.agent import (
        agent_user_id_var,
        agent_thread_id_var,
        agent_session_id_var,
        agent_request_source_var,
        agent_task_id_var,
        agent_test_run_id_var,
    )
    agent_request_source_var.set("a2a")
    agent_task_id_var.set(str(task.task_id))

    # Shared A2A + Web UI ingress: three-outcome resolver with
    # INPUT_REQUIRED signalling. REUSE returns the existing ID, MINT
    # mints + persists a fresh UTC timestamp (script-creation flows),
    # SKIP_MINT returns None for comparison reports (PerfReport MCP
    # mints its own comparison_id), and REJECT raises
    # TaskInputRequiredSignal which execute_task catches to transition
    # the task row to input_required (A2A §4.1.3) rather than failing.
    from services.helpers.test_run_id_resolver import (
        resolve_test_run_id_or_signal_input_required,
    )

    ensured_test_run_id = await resolve_test_run_id_or_signal_input_required(
        task, effective_user_text, thread_id,
    )
    if ensured_test_run_id:
        agent_test_run_id_var.set(ensured_test_run_id)

    if task.session_id:
        agent_session_id_var.set(str(task.session_id))
        try:
            from stores import session_store as _ss
            _session = await _ss.get_session(task.session_id)
            if _session and _session.user_id:
                agent_user_id_var.set(_session.user_id)
        except Exception:
            log.debug("_run_orchestrator: could not resolve user_id from session; continuing")
    if thread_id:
        agent_thread_id_var.set(thread_id)

    try:
        assistant_text, raw_reply, tool_rounds, exit_reason, ctx_metrics = (
            await _invoke_orchestrator_with_tool_loop(
                messages_for_llm, task.task_id, common,
                payload=task.payload,
            )
        )
    except Exception as exc:
        log.exception("_run_orchestrator: LLM invocation failed")
        raise RuntimeError(f"Orchestrator agent invocation failed: {exc}") from exc

    if thread_id and assistant_text:
        try:
            from stores import conversation_store, thread_store

            await conversation_store.append_message(
                thread_id,
                agent_name=ORCHESTRATOR_AGENT_NAME,
                role="assistant",
                content={"text": assistant_text, "raw": raw_reply},
            )
            await thread_store.touch_thread(thread_id)
        except Exception:
            log.exception("_run_orchestrator: failed to persist assistant reply; continuing")

    result: dict = {
        "agent": ORCHESTRATOR_AGENT_NAME,
        "thread_id": thread_id,
        "messages_processed": len(messages_for_llm),
        "history_loaded": len(history),
        "reply_text": assistant_text,
        "reply_raw": raw_reply,
        "tool_rounds": tool_rounds,
        "total_rounds": len(tool_rounds),
        "exit_reason": exit_reason,
    }
    if ctx_metrics:
        result["context_tokens"] = ctx_metrics["context_tokens"]
        result["context_utilization_pct"] = ctx_metrics["context_utilization_pct"]
        result["context_limit"] = ctx_metrics["context_limit"]
    return result


def _invoke_orchestrator_sync(messages: list[dict]) -> tuple[str, Any]:
    """Build a fresh orchestrator and produce a reply for the given messages.

    Synchronous so it can run inside `asyncio.to_thread`. The orchestrator
    is rebuilt per call deliberately: AG2 `ConversableAgent` carries
    per-conversation state in module-level dicts (`_oai_messages`,
    `_function_map`, etc.) that we do not want bleeding across A2A tasks.
    Build cost is dominated by the `LLMProvider.to_ag2_config()` call,
    which is sub-millisecond. The agent factory itself caches nothing.
    """
    import sys
    from pathlib import Path

    # Make `agents.orchestrator.agent` importable when the executor runs
    # from a context that did not put the framework dir on sys.path
    # (e.g. unit tests invoking utils/task_executor.py directly).
    from utils.paths import get_framework_dir
    framework_dir = get_framework_dir()
    if str(framework_dir) not in sys.path:
        sys.path.insert(0, str(framework_dir))

    from agents.orchestrator.agent import build_orchestrator

    agent = build_orchestrator()
    reply = agent.generate_reply(messages=messages)

    if isinstance(reply, str):
        return reply, reply
    if isinstance(reply, dict):
        content = reply.get("content")
        if isinstance(content, str):
            return content, reply
        return str(content) if content is not None else "", reply
    return str(reply) if reply is not None else "", reply


async def _invoke_orchestrator_with_tool_loop(
    messages: list[dict],
    task_id: Any,
    common: dict,
    *,
    payload: Optional[dict] = None,
) -> tuple[str, Any, list[dict], str, dict | None]:
    """Build a fresh orchestrator and run a multi-turn tool-execution loop.

    Replaces the single-shot ``_invoke_orchestrator_sync()`` for the A2A
    path. The orchestrator's four tools (``list_available_specialists``,
    ``delegate_to_specialist``, ``check_task_status``,
    ``request_human_approval``) are executed within the loop so the LLM
    can delegate to specialists and poll their status to completion.

    An ``after_tool_round`` callback drains
    ``agents.orchestrator.agent._pending_executions`` after each round
    and immediately starts the specialist tasks via
    ``asyncio.create_task(execute_task(...))``. This ensures specialist
    tasks are *running* when the orchestrator's next LLM round calls
    ``check_task_status``.

    The orchestrator agent is rebuilt per call (same rationale as
    ``_invoke_orchestrator_sync``).

    Args:
        messages: AG2-shaped message list for the LLM.
        task_id: The orchestrator's own task UUID (for SSE broadcasts).
        common: SSE broadcast fields (task_id, session_id, etc.).

    Returns:
        Tuple of (reply_text, raw_reply, tool_rounds_audit, exit_reason,
        final_context_metrics).
    """
    import sys

    from utils.paths import get_framework_dir
    framework_dir = get_framework_dir()
    if str(framework_dir) not in sys.path:
        sys.path.insert(0, str(framework_dir))

    from agents.orchestrator.agent import build_orchestrator, drain_pending_executions

    agent = await asyncio.to_thread(build_orchestrator)

    # ---- Load loop config from the orchestrator's config.yaml ----------
    from utils import config_loader

    orch_config = config_loader.load_agent_config("orchestrator")

    max_tool_rounds = int(orch_config.get("max_tool_rounds", 10))
    max_consecutive_repeats = int(orch_config.get("max_consecutive_repeats", 3))
    raw_polling_tools = orch_config.get("polling_tools", [])
    polling_tools = frozenset(
        raw_polling_tools if isinstance(raw_polling_tools, list) else []
    )
    polling_max_consecutive_repeats = int(
        orch_config.get("polling_max_consecutive_repeats", 15)
    )
    context_limit = int(orch_config.get("context_limit", 128000))
    compaction_threshold = float(orch_config.get("compaction_threshold", 0.80))
    compaction_preserve_recent = int(orch_config.get("compaction_preserve_recent", 5))

    # ---- Track delegated specialist task IDs for streaming wait ----------
    delegated_task_ids: list[UUID] = []

    proxy_sse_enabled = bool(orch_config.get("proxy_specialist_sse", True))

    # ---- after_tool_round callback: start delegated specialist tasks ----
    async def _drain_and_start_pending() -> None:
        """Drain _pending_executions and fire specialist tasks immediately.

        ``delegate_to_specialist()`` appends task UUIDs to the
        module-level ``_pending_executions`` list. We drain them here
        (between tool rounds) so they are *running* when the next LLM
        round calls ``check_task_status``.

        When ``proxy_specialist_sse`` is enabled, registers each child
        task in ``_parent_task_cache`` so ``_broadcast()`` forwards
        specialist events to the orchestrator's SSE subscribers
        (ENH-003B).
        """
        pending = drain_pending_executions()
        for pending_task_id in pending:
            log.info(
                "Orchestrator task %s: starting delegated specialist task %s",
                task_id, pending_task_id,
            )
            delegated_task_ids.append(pending_task_id)
            if proxy_sse_enabled:
                register_parent_task(pending_task_id, task_id)
            asyncio.create_task(execute_task(pending_task_id))

    reply_text, raw_reply, tool_rounds, exit_reason, ctx_metrics = (
        await run_multi_turn_tool_loop(
            agent=agent,
            messages=messages,
            max_tool_rounds=max_tool_rounds,
            max_consecutive_repeats=max_consecutive_repeats,
            polling_tools=polling_tools,
            polling_max_consecutive_repeats=polling_max_consecutive_repeats,
            context_limit=context_limit,
            compaction_threshold=compaction_threshold,
            compaction_preserve_recent=compaction_preserve_recent,
            task_id=task_id,
            common=common,
            agent_name=ORCHESTRATOR_AGENT_NAME,
            after_tool_round=_drain_and_start_pending,
        )
    )

    # Drain any remaining pending executions from the final round
    final_pending = drain_pending_executions()
    for pending_task_id in final_pending:
        log.info(
            "Orchestrator task %s: starting remaining delegated task %s",
            task_id, pending_task_id,
        )
        delegated_task_ids.append(pending_task_id)
        if proxy_sse_enabled:
            register_parent_task(pending_task_id, task_id)
        asyncio.create_task(execute_task(pending_task_id))

    # ---- Streaming-aware task lifecycle ----------------------
    # When the request arrived via SendStreamingMessage and the config
    # enables waiting, hold the orchestrator task in 'running' state until
    # all delegated specialist tasks reach a terminal state. This keeps
    # the A2A client's SSE stream open for the full lifecycle.
    request_mode = (payload or {}).get("metadata", {}).get("request_mode")
    wait_enabled = bool(orch_config.get("streaming_wait_for_specialists", True))
    poll_interval = float(
        orch_config.get("streaming_wait_poll_interval_seconds", 5)
    )

    if (
        request_mode == "streaming"
        and wait_enabled
        and delegated_task_ids
    ):
        log.info(
            "Orchestrator task %s: streaming mode — waiting for %d "
            "delegated task(s) to reach terminal state",
            task_id, len(delegated_task_ids),
        )
        await _broadcast(
            task_id,
            TaskEvent(
                status="running",
                progress="waiting_for_specialists",
                result={"delegated_task_ids": [str(t) for t in delegated_task_ids]},
                **common,
            ),
        )

        remaining = set(delegated_task_ids)
        while remaining:
            await asyncio.sleep(poll_interval)
            settled = set()
            for child_id in remaining:
                child = await task_store.get_task(child_id)
                if child is None or child.status in task_store.TERMINAL_STATUSES:
                    settled.add(child_id)
            remaining -= settled

        log.info(
            "Orchestrator task %s: all %d delegated task(s) have reached "
            "terminal state",
            task_id, len(delegated_task_ids),
        )

    return reply_text, raw_reply, tool_rounds, exit_reason, ctx_metrics


async def _load_thread_history_as_ag2_messages(thread_id: str) -> list[dict]:
    """Load conversation_messages for `thread_id` shaped for AG2's `messages=`.

    Each row's `content.text` becomes the message content; non-string
    payloads (tool calls) are stringified to a sensible fallback so AG2
    never sees None.
    """
    from stores import conversation_store

    rows = await conversation_store.list_for_thread(thread_id, limit=200, ascending=True)
    shaped: list[dict] = []
    for row in rows:
        text = _coerce_message_text(row.content)
        # AG2 expects `role` in {system, user, assistant, tool}; we already
        # validated this on insert via conversation_store.VALID_ROLES.
        shaped.append({"role": row.role, "content": text})
    return shaped


def _coerce_message_text(content: Any) -> str:
    """Extract a string body from a JSONB conversation_messages.content row."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
        return json.dumps(content)
    return str(content)



# =============================================================================
# Webhook delivery (Pattern 3 from V2 Section 14.2)
# =============================================================================

async def _deliver_webhooks(task: task_store.AgentTask) -> None:
    """POST the final task body to each subscriber URL with retry/backoff."""
    import httpx

    body = {
        "task_id": str(task.task_id),
        "session_id": str(task.session_id) if task.session_id else None,
        "external_session_id": task.external_session_id,
        "agent_name": task.agent_name,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "submitted_at": task.submitted_at.isoformat() if task.submitted_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }

    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        for url in task.subscriber_endpoints or []:
            await _post_with_retry(client, url, body, task.task_id)


async def _post_with_retry(client: Any, url: str, body: dict, task_id: UUID) -> None:
    delay = WEBHOOK_BACKOFF_BASE_SECONDS
    for attempt in range(1, WEBHOOK_RETRY_ATTEMPTS + 1):
        try:
            response = await client.post(url, json=body, headers={"X-Task-Id": str(task_id)})
            if response.status_code < 500:
                # 2xx = success; 4xx = caller's mistake, no point retrying.
                if response.status_code >= 400:
                    log.warning(
                        "Webhook %s returned %d for task %s; not retrying (4xx).",
                        url, response.status_code, task_id,
                    )
                else:
                    log.info("Webhook %s delivered task %s (%d).", url, task_id, response.status_code)
                return
            log.warning(
                "Webhook %s returned %d for task %s (attempt %d/%d).",
                url, response.status_code, task_id, attempt, WEBHOOK_RETRY_ATTEMPTS,
            )
        except Exception as exc:
            log.warning(
                "Webhook %s raised %s for task %s (attempt %d/%d): %s",
                url, type(exc).__name__, task_id, attempt, WEBHOOK_RETRY_ATTEMPTS, exc,
            )
        if attempt < WEBHOOK_RETRY_ATTEMPTS:
            await asyncio.sleep(delay)
            delay *= 2  # exponential backoff
    log.error("Webhook %s exhausted %d attempts for task %s.", url, WEBHOOK_RETRY_ATTEMPTS, task_id)


