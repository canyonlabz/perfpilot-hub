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
         available; tool calls back into the local A2A surface use the
         `PERFPILOT_A2A_BASE_URL` env var.
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
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from . import task_store
from . import trace_store

log = logging.getLogger(__name__)

WEBHOOK_RETRY_ATTEMPTS = 3
WEBHOOK_BACKOFF_BASE_SECONDS = 1.0
WEBHOOK_TIMEOUT_SECONDS = 15.0
STUB_TOTAL_RUNTIME_SECONDS = 3.0


@dataclass
class TaskEvent:
    """One state-transition snapshot delivered to in-process subscribers.

    Mirrors the shape we will send over SSE in the A2A
    `tasks/sendSubscribe` endpoint. Includes both IDs from V2 Section 4.3
    so SSE consumers can correlate to the broader session.
    """

    task_id: str
    session_id: Optional[str]
    external_session_id: Optional[str]
    agent_name: str
    status: str
    progress: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[dict] = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_sse_data(self) -> str:
        """Render a JSON line suitable for an SSE `data:` field."""
        return json.dumps(asdict(self))


# =============================================================================
# Subscription bus (in-process)
# =============================================================================
# Each task_id maps to a list of asyncio.Queue subscribers. SSE consumers
# subscribe before submitting the task (or right after) and unsubscribe on
# disconnect. This is intentionally in-process - cross-process pub/sub is an
# Epic 4 concern (Redis / Postgres LISTEN / etc.).

_subscribers: dict[UUID, list[asyncio.Queue]] = {}
_subscribers_lock = asyncio.Lock()


async def subscribe(task_id: UUID) -> asyncio.Queue:
    """Register a queue for state events on `task_id`. Returns the queue."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    async with _subscribers_lock:
        _subscribers.setdefault(task_id, []).append(queue)
    return queue


async def unsubscribe(task_id: UUID, queue: asyncio.Queue) -> None:
    """Remove a queue from the subscriber list. Safe to call twice."""
    async with _subscribers_lock:
        queues = _subscribers.get(task_id)
        if queues and queue in queues:
            queues.remove(queue)
        if queues is not None and not queues:
            _subscribers.pop(task_id, None)


async def _broadcast(task_id: UUID, event: TaskEvent) -> None:
    """Push `event` to every queue subscribed to `task_id`. Drops on full queue."""
    async with _subscribers_lock:
        queues = list(_subscribers.get(task_id, ()))
    for queue in queues:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("Dropping SSE event for task %s; subscriber queue full", task_id)


# =============================================================================
# Public API
# =============================================================================

def _find_matching_hitl_rule(
    agent_name: str,
    tool: Optional[str],
) -> Optional[HitlGateRule]:
    """Return the first HITL gate rule that matches (agent, tool) AND is
    enabled in config, or ``None`` if no gate applies.
    """
    hitl_cfg = _get_hitl_config()
    for rule in _HITL_GATE_RULES:
        if rule.matches(agent_name, tool) and hitl_cfg.get(rule.config_key, False):
            return rule
    return None


async def _enforce_hitl_gate(
    task: task_store.AgentTask,
    common: dict,
) -> Optional[str]:
    """Check config-driven HITL gates and wait for human decision if required.

    Iterates ``_HITL_GATE_RULES`` looking for a rule that matches the
    task's ``(agent_name, payload.tool)`` AND whose config key is enabled.
    If a match is found, creates a HITL prompt and polls until the human
    decides (approve / reject) or the configured timeout expires.

    Returns ``None`` when no gate applies or when the human approved.
    Returns a rejection reason string when the human rejected or the gate
    timed out — the caller should cancel the task.

    Polling interval and timeout are read from the orchestrator's
    ``config.yaml`` under ``pipeline.poll_interval_seconds`` and
    ``pipeline.poll_timeout_seconds`` respectively.
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    tool = payload.get("tool") if isinstance(payload.get("tool"), str) else None

    rule = _find_matching_hitl_rule(task.agent_name, tool)
    if rule is None:
        return None

    from . import hitl_store

    prompt = rule.build_prompt(task.agent_name, payload)

    try:
        approval = await hitl_store.create_prompt(task.task_id, prompt)
    except Exception as exc:
        log.exception("_enforce_hitl_gate: failed to create HITL prompt")
        return f"Failed to create HITL prompt: {exc}"

    log.info(
        "HITL gate active for task %s (approval_id=%d, rule=%s)",
        task.task_id, approval.id, rule.config_key,
    )

    await _broadcast(
        task.task_id,
        TaskEvent(status="running", progress="Waiting for human approval...", **common),
    )

    hitl_cfg = _get_hitl_config()
    poll_interval = float(hitl_cfg.get("poll_interval_seconds", 2.0))
    timeout = float(hitl_cfg.get("timeout_seconds", 300.0))
    deadline = asyncio.get_event_loop().time() + max(0.0, timeout)

    while asyncio.get_event_loop().time() < deadline:
        try:
            current = await hitl_store.get_approval(approval.id)
        except Exception:
            log.exception("_enforce_hitl_gate: poll error (will retry)")
            await asyncio.sleep(poll_interval)
            continue

        if current and current.decision != "pending":
            if current.decision == "approved":
                log.info("HITL gate approved for task %s", task.task_id)
                await _broadcast(
                    task.task_id,
                    TaskEvent(
                        status="running",
                        progress="Human approval granted — proceeding with execution",
                        **common,
                    ),
                )
                return None
            else:
                reason = current.feedback or "Rejected by user"
                log.info("HITL gate rejected for task %s: %s", task.task_id, reason)
                return reason

        await asyncio.sleep(poll_interval)

    log.warning("HITL gate timed out for task %s after %.0fs", task.task_id, timeout)
    return f"HITL approval timed out after {int(timeout)}s"


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
    from utils import conversation_store, thread_store

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
        hitl_rejection = await _enforce_hitl_gate(task, common)
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
EXECUTION_AGENT_NAME = "execution-agent"
MONITORING_AGENT_NAME = "monitoring-agent"
MONITORING_AGENT_MCP_PREFIXES: tuple[str, ...] = ("datadog_",)
ANALYSIS_AGENT_NAME = "analysis-agent"
ANALYSIS_AGENT_MCP_PREFIXES: tuple[str, ...] = ("perfanalysis_",)
REPORTING_AGENT_NAME = "reporting-agent"
REPORTING_AGENT_MCP_PREFIXES: tuple[str, ...] = ("perfreport_", "confluence_")
SCRIPT_AGENT_NAME = "script-agent"
SCRIPT_AGENT_MCP_PREFIXES: tuple[str, ...] = ("jmeter_",)

# =============================================================================
# HITL gate infrastructure (config-driven, extensible)
# =============================================================================
# Each HitlGateRule declares a mapping from (agent, tool) to a config key
# under `hitl.*` in the orchestrator's config.yaml.  Adding a new HITL gate
# is two steps:
#   1. Add a `require_approval_before_<name>: true` key in config.yaml
#   2. Add a HitlGateRule to _HITL_GATE_RULES below
# The framework handles prompt creation, polling, and approval/rejection.


@dataclass
class HitlGateRule:
    """Declares when a HITL gate should fire and what prompt to show."""

    config_key: str
    agent_names: tuple[str, ...]
    tools: Optional[frozenset[str]]
    title: str
    summary_template: str

    def matches(self, agent_name: str, tool: Optional[str]) -> bool:
        if agent_name not in self.agent_names:
            return False
        if self.tools is not None and (tool is None or tool not in self.tools):
            return False
        return True

    def build_prompt(self, agent_name: str, payload: dict) -> dict:
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        tool = payload.get("tool", "unknown")

        template_vars = {
            "agent_name": agent_name,
            "tool": tool,
            **{k: str(v) for k, v in args.items()},
        }
        summary = self.summary_template.format_map(
            _SafeFormatMap(template_vars)
        )

        artifact: dict[str, Any] = {"tool": tool, "agent": agent_name}
        artifact.update({k: str(v) for k, v in args.items()})
        if payload.get("action"):
            artifact["action"] = payload["action"]

        return {"title": self.title, "summary": summary, "artifact": artifact}


class _SafeFormatMap(dict):
    """Dict subclass that returns '{key}' for missing keys in str.format_map."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


_HITL_GATE_RULES: list[HitlGateRule] = [
    HitlGateRule(
        config_key="require_approval_before_test_start",
        agent_names=(EXECUTION_AGENT_NAME,),
        tools=frozenset({"start_performance_test"}),
        title="Approve Performance Test Start",
        summary_template=(
            "The {agent_name} wants to start BlazeMeter test {test_id}. "
            "Approve to proceed or reject to cancel."
        ),
    ),
    HitlGateRule(
        config_key="require_approval_before_publish",
        agent_names=("reporting-agent",),
        tools=None,
        title="Approve Report Publication",
        summary_template=(
            "The {agent_name} wants to publish content. "
            "Approve to proceed or reject to cancel."
        ),
    ),
]

# Orchestrator config cache (loaded once per process)
_orchestrator_config: Optional[dict] = None
_orchestrator_config_loaded = False


def _load_orchestrator_config() -> dict:
    """Load the full orchestrator config.yaml (cached after first load)."""
    global _orchestrator_config, _orchestrator_config_loaded
    if _orchestrator_config_loaded:
        return _orchestrator_config or {}

    try:
        from pathlib import Path

        import yaml

        from .base_agent import resolve_agent_config_path

        orchestrator_dir = Path(__file__).resolve().parent.parent / "agents" / "orchestrator"
        config_path = resolve_agent_config_path(orchestrator_dir)
        if config_path is None:
            log.warning("_load_orchestrator_config: no config found")
            _orchestrator_config = {}
        else:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                _orchestrator_config = yaml.safe_load(f) or {}
            if not isinstance(_orchestrator_config, dict):
                _orchestrator_config = {}
            log.info("Orchestrator config loaded (hitl=%s)", _orchestrator_config.get("hitl"))
    except Exception:
        log.exception("_load_orchestrator_config: failed")
        _orchestrator_config = {}

    _orchestrator_config_loaded = True
    return _orchestrator_config or {}


def _get_hitl_config() -> dict:
    """Return the ``hitl:`` section of the orchestrator config."""
    cfg = _load_orchestrator_config()
    hitl = cfg.get("hitl")
    return hitl if isinstance(hitl, dict) else {}


def _get_pipeline_config() -> dict:
    """Return the ``pipeline:`` section of the orchestrator config."""
    cfg = _load_orchestrator_config()
    pipeline = cfg.get("pipeline")
    return pipeline if isinstance(pipeline, dict) else {}

# F3.8 execution-agent tool surface (INSTRUCTIONS.md §3). Kept here as a
# tuple of authoritative names; `_run_execution_agent` validates incoming
# `payload.tool` against this list. When PBI 3.8.7 flips the agent card
# to `available`, the same names land in `agent_card.json::skills[]`.
EXECUTION_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "start_performance_test",
    "wait_for_completion",
    "extract_test_run_artifacts",
)

# MCP tool prefixes the execution-agent may call directly (pass-through).
# Any tool whose name starts with one of these prefixes is routed to the
# MCP gateway instead of the execution-agent Python module. This lets
# end-users request specific MCP operations (e.g. blazemeter_get_public_report)
# without going through the composite agent tools above.
EXECUTION_AGENT_MCP_PREFIXES: tuple[str, ...] = (
    "blazemeter_",
    "jmeter_",
)


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
      5. Run `agent.generate_reply(messages=...)` on a worker thread so
         the executor's event loop is never blocked by the (synchronous)
         tool calls the agent may issue.
      6. Persist the assistant reply.
      7. Return a structured result dict the A2A response wraps verbatim.

    The orchestrator's outbound tool calls (delegate_to_specialist,
    check_task_status) hit `PERFPILOT_A2A_BASE_URL` (default
    `http://127.0.0.1:8001`). When running INSIDE the A2A server, that
    URL points back at the same process -- the network round-trip is
    intentional so the tool surface stays uniform whether the
    orchestrator runs in-process or in a separate process.
    """
    thread_id = _extract_thread_id_from_payload(task.payload)
    user_message = _extract_user_message_from_payload(task.payload)

    # Phase markers so SSE consumers see liveness signals during a
    # potentially long LLM call.
    await _broadcast(task.task_id, TaskEvent(status="running", progress="loading_history", **common))

    history = await _load_thread_history_as_ag2_messages(thread_id) if thread_id else []

    if user_message and thread_id:
        try:
            from . import conversation_store

            await conversation_store.append_message(
                thread_id,
                agent_name="user",
                role="user",
                content={"text": user_message, "payload": task.payload},
            )
        except Exception:
            log.exception("_run_orchestrator: failed to persist user message; continuing")

    messages_for_llm = list(history)
    if user_message:
        messages_for_llm.append({"role": "user", "content": user_message})

    await _broadcast(task.task_id, TaskEvent(status="running", progress="invoking_llm", **common))

    # Propagate the task owner's user_id and request source into the
    # orchestrator's tool execution context so `delegate_to_specialist`
    # stamps outbound requests correctly. Setting source to "a2a"
    # tells the orchestrator to delegate via the A2A surface (existing
    # behaviour) and use X-External-Thread-Id / X-External-Session-Id
    # headers for OpenTelemetry traceability.
    from agents.orchestrator.agent import (
        agent_user_id_var,
        agent_thread_id_var,
        agent_request_source_var,
    )
    agent_request_source_var.set("a2a")

    if task.session_id:
        try:
            from . import session_store as _ss
            _session = await _ss.get_session(task.session_id)
            if _session and _session.user_id:
                agent_user_id_var.set(_session.user_id)
        except Exception:
            log.debug("_run_orchestrator: could not resolve user_id from session; continuing")
    if thread_id:
        agent_thread_id_var.set(thread_id)

    try:
        assistant_text, raw_reply = await asyncio.to_thread(
            _invoke_orchestrator_sync, messages_for_llm,
        )
    except Exception as exc:
        log.exception("_run_orchestrator: LLM invocation failed")
        raise RuntimeError(f"Orchestrator agent invocation failed: {exc}") from exc

    if thread_id and assistant_text:
        try:
            from . import conversation_store, thread_store

            await conversation_store.append_message(
                thread_id,
                agent_name=ORCHESTRATOR_AGENT_NAME,
                role="assistant",
                content={"text": assistant_text, "raw": raw_reply},
            )
            await thread_store.touch_thread(thread_id)
        except Exception:
            log.exception("_run_orchestrator: failed to persist assistant reply; continuing")

    return {
        "agent": ORCHESTRATOR_AGENT_NAME,
        "thread_id": thread_id,
        "messages_processed": len(messages_for_llm),
        "history_loaded": len(history),
        "reply_text": assistant_text,
        "reply_raw": raw_reply,
    }


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
    framework_dir = Path(__file__).resolve().parent.parent
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


async def _load_thread_history_as_ag2_messages(thread_id: str) -> list[dict]:
    """Load conversation_messages for `thread_id` shaped for AG2's `messages=`.

    Each row's `content.text` becomes the message content; non-string
    payloads (tool calls) are stringified to a sensible fallback so AG2
    never sees None.
    """
    from . import conversation_store

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


def _extract_thread_id_from_payload(payload: Any) -> Optional[str]:
    """Return the resolved A2A thread_id stamped by `a2a_server` at request time.

    The A2A server stamps `payload["_perfpilot_thread"] = {"thread_id":
    "<internal>", "external_thread_id": "<label>"}` after resolving
    Decision 17 thread lookup-or-create. Older callers / smoke clients
    that bypass the resolver get `None` and the orchestrator runs single-
    turn (no history load, no history persist).
    """
    if not isinstance(payload, dict):
        return None
    block = payload.get("_perfpilot_thread")
    if isinstance(block, dict):
        tid = block.get("thread_id")
        if isinstance(tid, str) and tid:
            return tid
    return None


def _extract_user_message_from_payload(payload: Any) -> Optional[str]:
    """Pull the user's prompt out of the A2A task body.

    Accepts a handful of common shapes so naive callers do not need to
    learn one canonical key:
      - `payload["text"]`
      - `payload["message"]`
      - `payload["prompt"]`
      - whole payload (stringified) as a last resort
    """
    if not isinstance(payload, dict):
        return str(payload) if payload is not None else None
    for key in ("text", "message", "prompt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    # Strip metadata keys then stringify the remainder so payload-as-body
    # callers (e.g. PBI 3.7.9 smoke without a `text` field) still get a
    # coherent prompt.
    public = {k: v for k, v in payload.items() if not k.startswith("_")}
    if public:
        return json.dumps(public)
    return None


# =============================================================================
# Generalized specialist module loader (PBI 3.10.2 Part A)
# =============================================================================
# Loads `agents/<agent-name>/agent.py` via `importlib.util` because
# hyphenated folder names make `from agents.execution-agent.agent import ...`
# a Python syntax error. Modules are cached per agent name to avoid
# re-paying the `fastmcp` / `autogen` import cost on every task.
# Replaces the old execution-agent-specific `_load_execution_agent_module()`.

_specialist_module_cache: dict[str, Any] = {}
_specialist_module_locks: dict[str, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())


async def _load_specialist_module(agent_name: str) -> Any:
    """Return the loaded ``agents/<agent_name>/agent.py`` module (cached).

    Thread-safe under asyncio: the first concurrent loader for a given
    ``agent_name`` wins; subsequent callers wait on the per-agent lock
    and see the cached module. Subsequent calls after the cache is
    populated return the cached value without touching the lock.

    The module alias registered in ``sys.modules`` uses underscores
    (``agents_execution_agent_dynamic``) to stay a valid Python
    identifier.

    Args:
        agent_name: The hyphenated folder name under ``agents/``
            (e.g. ``"execution-agent"``, ``"monitoring-agent"``).

    Returns:
        The loaded module object.

    Raises:
        FileNotFoundError: when ``agents/<agent_name>/agent.py`` does
            not exist on disk.
        ImportError: when ``importlib`` cannot build a module spec.
    """
    if agent_name in _specialist_module_cache:
        return _specialist_module_cache[agent_name]

    async with _specialist_module_locks[agent_name]:
        if agent_name in _specialist_module_cache:
            return _specialist_module_cache[agent_name]

        import importlib.util
        import sys
        from pathlib import Path

        module_path = (
            Path(__file__).resolve().parent.parent
            / "agents"
            / agent_name
            / "agent.py"
        )
        if not module_path.exists():
            raise FileNotFoundError(
                f"Agent module not found at {module_path}"
            )

        module_alias = f"agents_{agent_name.replace('-', '_')}_dynamic"
        spec = importlib.util.spec_from_file_location(
            module_alias, str(module_path),
        )
        if spec is None or spec.loader is None:
            raise ImportError(
                f"Could not build import spec for {module_path}"
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_alias] = module
        spec.loader.exec_module(module)
        _specialist_module_cache[agent_name] = module
        log.info("Loaded specialist module: %s -> %s", agent_name, module_path)
        return module


async def _run_execution_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Dispatch the task payload's `tool` to the matching execution-agent function.

    Payload contract (INSTRUCTIONS.md §5)::

        {
          "tool":        "start_performance_test" | "wait_for_completion" | "extract_test_run_artifacts",
          "action":      "fresh_run" | "retest" | "poll" | "extract" | "full_pipeline" | ...,
          "args":        { ...tool-specific kwargs... },
          "test_run_id": "<PerfPilot artifact-folder key>"
        }

    Unlike `_run_orchestrator`, NO LLM loop is involved -- the `tool`
    field is the explicit dispatch key read directly here. The `action`
    field is a free-form course-of-action label echoed into the result
    envelope for audit / traceability (e.g. distinguishing a "fresh_run"
    from a "retest" that reuses an existing run_id). `test_run_id` is
    the PerfPilot artifact-folder key that travels through the whole
    pipeline (NOT the BlazeMeter run_id, which the tools mint themselves).

    Return envelope (always the same shape, success or failure)::

        {
          "agent":       "execution-agent",
          "tool":        "<echoed from payload, or None>",
          "action":      "<echoed from payload, or None>",
          "test_run_id": "<echoed from payload, or None>",
          "tool_result": <dict returned by the agent tool, OR a structured error>
        }

    `tool_result` semantics:
      - On valid dispatch + successful tool execution: the tool's own
        documented return shape (see INSTRUCTIONS.md §3.1 / §3.2 / §6).
      - On invalid payload (missing `tool`, unknown `tool`, malformed
        `args`): a `{"ok": False, "error": {"type": ..., "message": ...}}`
        dict mirroring the agent-tool error convention.
      - On unexpected tool exception: this function re-raises so
        `execute_task` marks the task as `failed`. The agent tools are
        documented to NEVER raise for tool-side failures, so reaching the
        re-raise path indicates a real programmer error.
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    tool = payload.get("tool")
    action = payload.get("action")
    test_run_id = payload.get("test_run_id")
    args_raw = payload.get("args")
    args = args_raw if isinstance(args_raw, dict) else None

    envelope: dict = {
        "agent": EXECUTION_AGENT_NAME,
        "tool": tool,
        "action": action,
        "test_run_id": test_run_id,
    }

    # ---- Payload validation -------------------------------------------
    if not isinstance(tool, str) or not tool:
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "InvalidPayload",
                "message": (
                    "Payload is missing required field 'tool' (must be one "
                    f"of {list(EXECUTION_AGENT_TOOL_NAMES)})."
                ),
            },
        }
        return envelope

    is_composite_tool = tool in EXECUTION_AGENT_TOOL_NAMES
    is_mcp_passthrough = any(tool.startswith(p) for p in EXECUTION_AGENT_MCP_PREFIXES)

    if not is_composite_tool and not is_mcp_passthrough:
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "UnknownTool",
                "message": (
                    f"Unknown tool {tool!r}. Valid composite tools: "
                    f"{list(EXECUTION_AGENT_TOOL_NAMES)}. "
                    f"MCP pass-through prefixes: {list(EXECUTION_AGENT_MCP_PREFIXES)}."
                ),
            },
        }
        return envelope

    if args_raw is not None and args is None:
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "InvalidPayload",
                "message": (
                    "Payload field 'args' must be a dict (or omitted); got "
                    f"{type(args_raw).__name__}."
                ),
            },
        }
        return envelope

    args = args or {}

    # ---- MCP pass-through dispatch ------------------------------------
    if is_mcp_passthrough:
        await _broadcast(
            task.task_id,
            TaskEvent(status="running", progress=f"mcp_passthrough:{tool}", **common),
        )
        try:
            mcp_result = await _call_mcp_tool_passthrough(tool, args)
        except Exception as exc:
            log.exception("MCP pass-through failed for tool %s", tool)
            envelope["tool_result"] = {
                "ok": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": f"MCP pass-through call failed: {exc}",
                },
            }
            return envelope

        await _broadcast(
            task.task_id,
            TaskEvent(status="running", progress=f"tool_complete:{tool}", **common),
        )
        envelope["tool_result"] = mcp_result
        return envelope

    # ---- Module load + function resolution ----------------------------
    await _broadcast(
        task.task_id,
        TaskEvent(status="running", progress="loading_execution_agent", **common),
    )
    try:
        module = await _load_specialist_module(EXECUTION_AGENT_NAME)
    except Exception as exc:
        log.exception("execution-agent module load failed for task %s", task.task_id)
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": f"Failed to load execution-agent module: {exc}",
            },
        }
        return envelope

    fn = getattr(module, tool, None)
    if not callable(fn):
        # Defends against drift between EXECUTION_AGENT_TOOL_NAMES and the
        # actual module (e.g., someone renames a function but forgets the
        # tuple). The orchestrator-side `list_available_specialists` would
        # still advertise the agent as available, so we want a clean error
        # surface rather than an AttributeError.
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "InternalError",
                "message": (
                    f"Tool {tool!r} is listed in EXECUTION_AGENT_TOOL_NAMES "
                    "but is not callable on the execution-agent module."
                ),
            },
        }
        return envelope

    # ---- Dispatch -----------------------------------------------------
    await _broadcast(
        task.task_id,
        TaskEvent(status="running", progress=f"dispatching_tool:{tool}", **common),
    )

    # BUG-05 fix: Create a progress callback that broadcasts intermediate
    # SSE events during long-running tools (wait_for_completion polls,
    # extract_test_run_artifacts steps).
    async def _on_progress(message: str) -> None:
        await _broadcast(
            task.task_id,
            TaskEvent(status="running", progress=message, **common),
        )

    # Inject the callback for tools that accept it. The `on_progress`
    # kwarg is Optional[Any] with a default of None, so tools that don't
    # declare it (e.g. start_performance_test) won't break — we only
    # inject when the tool's signature accepts it.
    import inspect
    sig = inspect.signature(fn)
    if "on_progress" in sig.parameters:
        args["on_progress"] = _on_progress

    try:
        tool_result = await fn(**args)
    except TypeError as exc:
        # `fn(**args)` raised TypeError -- argument mismatch. Surface as a
        # structured error rather than re-raising; the tool itself never
        # crashed, the dispatch did.
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "InvalidArgs",
                "message": f"Tool {tool!r} rejected args {args!r}: {exc}",
            },
        }
        return envelope

    await _broadcast(
        task.task_id,
        TaskEvent(status="running", progress=f"tool_complete:{tool}", **common),
    )
    envelope["tool_result"] = tool_result
    return envelope


# =============================================================================
# Stub agent (kept for non-orchestrator agent names until F3.9 lights them up)
# =============================================================================

async def _run_stub_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Simulate an agent doing work in three phases.

    Still used for every agent_name except `"orchestrator"`. F3.9+
    promotes specialists one at a time -- each gets its own
    `_run_<specialist>()` branch in `_dispatch_agent`.
    """
    phases = (
        ("planning", 1.0),
        ("executing", 1.5),
        ("finalizing", 0.5),
    )
    for phase, delay in phases:
        await asyncio.sleep(delay)
        await _broadcast(task.task_id, TaskEvent(status="running", progress=phase, **common))
    return {
        "stub": True,
        "agent": task.agent_name,
        "echo": task.payload,
        "note": "F3.5 stub executor; replaced by real AG2 dispatch in F3.7+",
    }


# =============================================================================
# Promoted specialist dispatch wrappers
# =============================================================================
# Each promoted specialist's _run_<agent>() is a thin wrapper around
# _run_mcp_specialist_agent(). Adding a new agent is two lines: the
# wrapper function + its _dispatch_agent branch.


async def _run_monitoring_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Dispatch to monitoring-agent (Datadog MCP tools)."""
    return await _run_mcp_specialist_agent(task, common, MONITORING_AGENT_NAME)


async def _run_analysis_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Dispatch to analysis-agent (PerfAnalysis MCP tools)."""
    return await _run_mcp_specialist_agent(task, common, ANALYSIS_AGENT_NAME)


async def _run_reporting_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Dispatch to reporting-agent (PerfReport + Confluence MCP tools)."""
    return await _run_mcp_specialist_agent(task, common, REPORTING_AGENT_NAME)


async def _run_script_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Dispatch to script-agent (JMeter MCP tools)."""
    return await _run_mcp_specialist_agent(task, common, SCRIPT_AGENT_NAME)


# =============================================================================
# Specialist prompt composition
# =============================================================================

def _compose_specialist_prompt(payload: dict) -> str:
    """Compose a natural LLM prompt from the delegation payload.

    The orchestrator passes the user's original message plus whatever
    contextual data it has gathered (test_run_id, environment,
    timestamps, etc.). This function assembles all available
    information into a coherent prompt for the specialist's LLM.

    Falls back to stringifying the payload when no explicit message
    field is found (backward compatibility with older callers).
    """
    user_msg = None
    for key in ("user_message", "message", "text", "prompt"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            user_msg = val
            break

    message_keys = {"user_message", "message", "text", "prompt"}
    context = {
        k: v for k, v in payload.items()
        if k not in message_keys and not k.startswith("_") and v is not None
    }

    if not user_msg:
        if context:
            user_msg = json.dumps(context)
        else:
            return ""

    parts = [user_msg]
    if context:
        lines = []
        for k, v in context.items():
            if isinstance(v, dict):
                lines.append(f"- {k}: {json.dumps(v)}")
            else:
                lines.append(f"- {k}: {v}")
        parts.append("\nContext provided by the orchestrator:\n" + "\n".join(lines))

    return "\n".join(parts)


# =============================================================================
# Specialist AG2 agent invocation (sync, runs inside asyncio.to_thread)
# =============================================================================

def _generate_reply_sync(
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


async def _execute_tool_on_agent(
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


# ---------------------------------------------------------------------------
# Token estimation for context pressure tracking (B3)
# ---------------------------------------------------------------------------

_tiktoken_encoder: Any = None
_tiktoken_available: Optional[bool] = None


def _get_tiktoken_encoder() -> Any:
    """Lazy-load tiktoken encoder. Returns None if tiktoken is unavailable."""
    global _tiktoken_encoder, _tiktoken_available
    if _tiktoken_available is False:
        return None
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.encoding_for_model("gpt-4o")
        _tiktoken_available = True
        return _tiktoken_encoder
    except (ImportError, Exception):
        _tiktoken_available = False
        log.info("tiktoken not available; using character-based token estimation")
        return None


def _estimate_token_count(messages: list[dict]) -> int:
    """Estimate total tokens in a message list.

    Uses tiktoken for accuracy when available, falls back to
    character_count / 4 as a rough heuristic.
    """
    enc = _get_tiktoken_encoder()
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, default=str)
        if enc is not None:
            total += len(enc.encode(content))
        else:
            total += len(content) // 4
        # Overhead per message (role, name, structural tokens)
        total += 4
    return total


async def _run_multi_turn_tool_loop(
    agent: Any,
    messages: list[dict],
    *,
    max_tool_rounds: int,
    max_consecutive_repeats: int,
    context_limit: int,
    task_id: Any,
    common: dict,
    agent_name: str,
) -> tuple[str, Any, list[dict], str]:
    """Async multi-turn tool-execution loop for specialist agents.

    Implements the loop engineering pattern:
      1. Call LLM (sync via to_thread)
      2. If text response → exit (success)
      3. If tool calls → execute each tool → feed results back → next round
      4. Exit conditions: max rounds, consecutive repeats, or errors

    Returns:
        Tuple of (reply_text, raw_reply, tool_rounds_audit, exit_reason).
    """
    tool_rounds: list[dict] = []
    last_tool_signature: str | None = None
    consecutive_repeats = 0

    for round_num in range(1, max_tool_rounds + 1):
        # ---- Context pressure tracking (B3) -------------------------------
        token_count = _estimate_token_count(messages)
        utilization_pct = round(token_count / context_limit, 4) if context_limit > 0 else 0.0
        log.info(
            "%s task %s round %d: ~%d tokens, %.1f%% utilization (%d limit)",
            agent_name, task_id, round_num,
            token_count, utilization_pct * 100, context_limit,
        )

        # ---- LLM call (sync AG2) ------------------------------------------
        reply = await asyncio.to_thread(_generate_reply_sync, agent, messages)

        # ---- Exit: text response (success) --------------------------------
        if isinstance(reply, str):
            return reply, reply, tool_rounds, "text_response"

        # ---- Exit: None / empty (model failure) ---------------------------
        if reply is None:
            return "", None, tool_rounds, "null_reply"

        # ---- Dict response: check for tool calls -------------------------
        if isinstance(reply, dict):
            tool_calls = reply.get("tool_calls")
            if not tool_calls:
                content = reply.get("content", "")
                text = content if isinstance(content, str) else str(content) if content else ""
                return text, reply, tool_rounds, "text_response"

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

                if consecutive_repeats >= max_consecutive_repeats:
                    warning = (
                        f"[Loop exited: tool '{fn_name}' called with identical "
                        f"arguments {max_consecutive_repeats} times consecutively]"
                    )
                    log.warning(
                        "%s task %s: %s", agent_name, task_id, warning,
                    )
                    return warning, reply, tool_rounds, "consecutive_repeat_limit"

                # ---- Execute the tool -------------------------------------
                t0 = time.perf_counter_ns()
                result_str = await _execute_tool_on_agent(agent, fn_name, fn_args)
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

            # ---- SSE progress broadcast -----------------------------------
            tool_names = [tc["name"] for tc in round_audit["tool_calls"]]
            await _broadcast(
                task_id,
                TaskEvent(
                    status="running",
                    progress=f"tool_round_{round_num}_of_{max_tool_rounds}",
                    result={
                        "round": round_num,
                        "tools_called": tool_names,
                        "agent": agent_name,
                        "context_tokens": token_count,
                        "context_utilization_pct": round(utilization_pct * 100, 1),
                        "context_limit": context_limit,
                    },
                    **common,
                ),
            )
            continue

        # ---- Exit: unexpected reply type ----------------------------------
        return str(reply), reply, tool_rounds, "unexpected_reply_type"

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
    )


# =============================================================================
# Generic MCP specialist dispatch (AG2 LLM loop)
# =============================================================================
# Used by promoted specialist agents whose MCP tools are auto-discovered
# at build time. The specialist's ConversableAgent has full JSON schemas
# for every tool in its namespace. The LLM autonomously selects tools
# and parameters based on the user's request and contextual data.


async def _run_mcp_specialist_agent(
    task: task_store.AgentTask,
    common: dict,
    agent_name: str,
) -> dict:
    """Build and invoke a specialist's AG2 agent with MCP tools.

    The specialist's ``build_*_agent()`` factory creates a
    ``ConversableAgent`` with MCP tools auto-discovered and registered
    via ``mcp_tool_registry``. The agent's LLM sees the full tool
    schemas and autonomously selects the right tool(s) based on the
    user's request.

    The payload carries the user's original message (``user_message``)
    plus any contextual data the orchestrator included (test_run_id,
    environment, timestamps, etc.). The specialist's LLM uses all
    available information to fulfill the request.

    Uses a multi-turn tool-execution loop: the LLM may
    return tool calls which are executed and fed back until the LLM
    produces a final text response or an exit condition is reached.
    Loop parameters (``max_tool_rounds``, ``max_consecutive_repeats``)
    are read from the agent's ``config.yaml``.

    Return envelope::

        {
          "agent":        "<agent_name>",
          "test_run_id":  "<echoed from payload, or None>",
          "user_message": "<echoed for audit trail>",
          "reply_text":   "<specialist LLM response text>",
          "reply_raw":    <raw LLM reply object>,
          "tool_rounds":  [<per-round audit dicts>],
          "total_rounds": <int>,
          "exit_reason":  "<text_response|max_rounds_reached|...>"
        }

    Args:
        task: The ``AgentTask`` row from the task store.
        common: SSE broadcast fields (task_id, session_id, etc.).
        agent_name: The specialist's name (e.g. ``"monitoring-agent"``).
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    test_run_id = payload.get("test_run_id")
    if not test_run_id:
        test_run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")

    user_message = None
    for key in ("user_message", "message", "text", "prompt"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            user_message = val
            break

    envelope: dict = {
        "agent": agent_name,
        "test_run_id": test_run_id,
        "user_message": user_message,
    }

    user_prompt = _compose_specialist_prompt(payload)
    if not user_prompt.strip():
        envelope["error"] = {
            "type": "InvalidPayload",
            "message": "Payload contains no actionable message or data.",
        }
        return envelope

    # ---- Module load --------------------------------------------------
    await _broadcast(
        task.task_id,
        TaskEvent(
            status="running",
            progress=f"building_{agent_name}",
            **common,
        ),
    )

    try:
        module = await _load_specialist_module(agent_name)
    except Exception as exc:
        log.exception("%s module load failed for task %s", agent_name, task.task_id)
        envelope["error"] = {
            "type": type(exc).__name__,
            "message": f"Failed to load {agent_name} module: {exc}",
        }
        return envelope

    # ---- Agent invocation ---------------------------------------------
    messages_for_llm = [{"role": "user", "content": user_prompt}]

    await _broadcast(
        task.task_id,
        TaskEvent(
            status="running",
            progress=f"invoking_{agent_name}_llm",
            **common,
        ),
    )

    # ---- Load agent config early (needed for stateful detection) ------
    import sys
    from pathlib import Path

    import yaml

    framework_dir = Path(__file__).resolve().parent.parent
    if str(framework_dir) not in sys.path:
        sys.path.insert(0, str(framework_dir))

    from utils.base_agent import resolve_agent_config_path

    agent_dir = framework_dir / "agents" / agent_name
    agent_config: dict = {}
    config_path = resolve_agent_config_path(agent_dir)
    if config_path:
        try:
            with open(config_path, encoding="utf-8-sig") as fh:
                agent_config = yaml.safe_load(fh) or {}
        except Exception:
            log.warning("Could not load config for %s, using defaults", agent_name)

    max_tool_rounds = int(agent_config.get("max_tool_rounds", 7))
    max_consecutive_repeats = int(agent_config.get("max_consecutive_repeats", 3))
    context_limit = int(agent_config.get("context_limit", 128000))

    # ---- Detect stateful namespaces -----------------------------------
    from utils.mcp_tool_registry import STATEFUL_NAMESPACES

    all_namespaces = list(
        agent_config.get("mcp_tools", {}).get("allowed_namespaces", [])
    )
    playwright_cfg = agent_config.get("playwright_mcp", {})
    if isinstance(playwright_cfg, dict) and playwright_cfg.get("enabled", True):
        all_namespaces.append("browser")

    has_stateful = any(ns in STATEFUL_NAMESPACES for ns in all_namespaces)
    stateful_client_holder: dict | None = {"client": None} if has_stateful else None

    # ---- Build the specialist agent -----------------------------------
    factory_name = f"build_{agent_name.replace('-', '_')}"
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        envelope["error"] = {
            "type": "RuntimeError",
            "message": (
                f"Specialist module for '{agent_name}' does not export "
                f"a '{factory_name}()' factory function."
            ),
        }
        return envelope

    try:
        if stateful_client_holder is not None:
            agent = await asyncio.to_thread(
                factory, stateful_client_holder=stateful_client_holder,
            )
        else:
            agent = await asyncio.to_thread(factory)
    except Exception as exc:
        log.exception("%s agent build failed for task %s", agent_name, task.task_id)
        raise RuntimeError(f"{agent_name} agent build failed: {exc}") from exc

    # ---- Open persistent client for stateful tools --------------------
    stateful_client_ctx = None
    if has_stateful and stateful_client_holder is not None:
        resolve_fn = getattr(module, "_resolve_playwright_url", None)
        playwright_url = resolve_fn(agent_config) if resolve_fn else None
        if playwright_url:
            from fastmcp import Client

            try:
                stateful_client_ctx = Client(playwright_url)
                stateful_client_holder["client"] = (
                    await stateful_client_ctx.__aenter__()
                )
                log.info(
                    "Opened persistent MCP client for %s -> %s",
                    agent_name, playwright_url,
                )
            except Exception as exc:
                log.exception(
                    "Failed to open persistent MCP client for %s at %s",
                    agent_name, playwright_url,
                )
                stateful_client_ctx = None
                stateful_client_holder["client"] = None

    # ---- Run multi-turn tool execution loop ---------------------------
    try:
        reply_text, raw_reply, tool_rounds, exit_reason = (
            await _run_multi_turn_tool_loop(
                agent=agent,
                messages=messages_for_llm,
                max_tool_rounds=max_tool_rounds,
                max_consecutive_repeats=max_consecutive_repeats,
                context_limit=context_limit,
                task_id=task.task_id,
                common=common,
                agent_name=agent_name,
            )
        )
    except Exception as exc:
        log.exception("%s agent invocation failed for task %s", agent_name, task.task_id)
        raise RuntimeError(f"{agent_name} agent invocation failed: {exc}") from exc
    finally:
        if stateful_client_ctx is not None:
            try:
                await stateful_client_ctx.__aexit__(None, None, None)
                log.info("Closed persistent MCP client for %s", agent_name)
            except Exception:
                log.warning(
                    "Failed to cleanly close persistent MCP client for %s",
                    agent_name,
                )
            if stateful_client_holder is not None:
                stateful_client_holder["client"] = None

    await _broadcast(
        task.task_id,
        TaskEvent(
            status="running",
            progress=f"{agent_name}_complete",
            **common,
        ),
    )

    envelope["reply_text"] = reply_text
    envelope["reply_raw"] = raw_reply
    envelope["tool_rounds"] = tool_rounds
    envelope["total_rounds"] = len(tool_rounds)
    envelope["exit_reason"] = exit_reason
    return envelope


async def _call_mcp_tool_passthrough_for_specialist(
    tool_name: str,
    args: dict,
    allowed_namespaces: list[str],
) -> dict:
    """Call an MCP tool via the gateway with namespace-scoped retry policy.

    Generalizes the execution-agent's ``_call_mcp_tool_passthrough()``
    for any specialist. Retry classification uses the registry's
    ``_is_api_based()`` helper so the policy stays consistent with the
    auto-discovery wrappers.

    Args:
        tool_name: Fully-qualified gateway tool name.
        args: Tool arguments dict.
        allowed_namespaces: Bare namespace list for ``MCPClient`` config
            (e.g. ``["datadog"]``).

    Returns:
        ``{ok: True, tool, result, attempts}`` on success.
        ``{ok: False, tool, error, attempts}`` on failure.
    """
    from utils.mcp_client import MCPClient, build_client_config
    from utils.mcp_tool_registry import _is_api_based

    is_api = _is_api_based(tool_name)
    max_attempts = 3 if is_api else 1
    retry_delay = 5.0

    config = build_client_config(allowed_namespaces)
    async with MCPClient(config) as client:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await client.call_tool(tool_name, args)
                data = getattr(result, "data", None)
                if data is not None:
                    payload = data
                else:
                    content = getattr(result, "content", None) or []
                    payload = getattr(content[0], "text", None) if content else str(result)
                return {"ok": True, "tool": tool_name, "result": payload, "attempts": attempt}
            except PermissionError:
                raise
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(retry_delay)

        return {
            "ok": False,
            "tool": tool_name,
            "error": {
                "type": type(last_error).__name__,
                "message": str(last_error)[:500],
            },
            "attempts": max_attempts,
        }


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


# =============================================================================
# MCP pass-through
# =============================================================================

async def _call_mcp_tool_passthrough(tool_name: str, args: dict) -> dict:
    """Call an MCP tool directly and return a structured result dict.

    Used for MCP pass-through dispatch when the orchestrator delegates a
    specific MCP tool (e.g. ``blazemeter_get_public_report``) rather than
    a composite agent tool. Follows the same retry policy as the
    execution-agent's API-based MCP calls: up to 3 attempts with 5s
    back-off for ``blazemeter_*`` tools; single attempt for ``jmeter_*``
    (code-based, deterministic).
    """
    from utils.mcp_client import MCPClient, build_client_config

    is_code_based = tool_name.startswith("jmeter_")
    max_attempts = 1 if is_code_based else 3
    retry_delay = 5.0

    config = build_client_config(["blazemeter", "jmeter"])
    async with MCPClient(config) as client:
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await client.call_tool(tool_name, args)
                data = getattr(result, "data", None)
                if data is not None:
                    payload = data
                else:
                    content = getattr(result, "content", None) or []
                    payload = getattr(content[0], "text", None) if content else str(result)
                return {"ok": True, "tool": tool_name, "result": payload, "attempts": attempt}
            except PermissionError:
                raise
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    await asyncio.sleep(retry_delay)

        return {
            "ok": False,
            "tool": tool_name,
            "error": {
                "type": type(last_error).__name__,
                "message": str(last_error)[:500],
            },
            "attempts": max_attempts,
        }
