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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from . import task_store

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


# =============================================================================
# Agent dispatch
# =============================================================================
# PBI 3.7.8: the orchestrator runs the real AG2 agent; other agent names
# continue to use the F3.5 stub workflow until their own F3.9+ scaffolds
# replace them. The dispatch table is intentionally a simple if/elif --
# at seven agents total a registry pattern would over-engineer it.

ORCHESTRATOR_AGENT_NAME = "orchestrator"
EXECUTION_AGENT_NAME = "execution-agent"

# F3.8 execution-agent tool surface (INSTRUCTIONS.md §3). Kept here as a
# tuple of authoritative names; `_run_execution_agent` validates incoming
# `payload.tool` against this list. When PBI 3.8.7 flips the agent card
# to `available`, the same names land in `agent_card.json::skills[]`.
EXECUTION_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "start_performance_test",
    "wait_for_completion",
    "extract_test_run_artifacts",
)


async def _dispatch_agent(task: task_store.AgentTask, common: dict) -> dict:
    """Route to the right runtime based on `task.agent_name`."""
    if task.agent_name == ORCHESTRATOR_AGENT_NAME:
        return await _run_orchestrator(task, common)
    if task.agent_name == EXECUTION_AGENT_NAME:
        return await _run_execution_agent(task, common)
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
# Execution-agent dispatch (PBI 3.8.6)
# =============================================================================
# Loads `agents/execution-agent/agent.py` via `importlib.util` because the
# folder name is hyphenated and `from agents.execution-agent.agent import ...`
# is a Python syntax error. The module is cached on first load to avoid
# re-paying the `fastmcp` / `autogen` import cost on every task. PBI 3.7's
# orchestrator uses the normal `from agents.orchestrator.agent import ...`
# path; future hyphenated specialists (e.g. `script-agent`, `analysis-agent`)
# will reuse this same pattern.

_execution_agent_module: Optional[Any] = None
_execution_agent_module_lock = asyncio.Lock()


async def _load_execution_agent_module() -> Any:
    """Return the loaded `agents/execution-agent/agent.py` module (cached).

    Thread-safe under asyncio: the first concurrent loader wins; subsequent
    callers wait on the lock and see the cached module. Subsequent calls
    after the cache is populated return the cached value without touching
    the lock.
    """
    global _execution_agent_module
    if _execution_agent_module is not None:
        return _execution_agent_module

    async with _execution_agent_module_lock:
        if _execution_agent_module is not None:
            return _execution_agent_module

        import importlib.util
        import sys
        from pathlib import Path

        module_path = (
            Path(__file__).resolve().parent.parent
            / "agents"
            / "execution-agent"
            / "agent.py"
        )
        if not module_path.exists():
            raise FileNotFoundError(
                f"Execution-agent module not found at {module_path}"
            )

        spec = importlib.util.spec_from_file_location(
            "agents_execution_agent_dynamic", str(module_path)
        )
        if spec is None or spec.loader is None:
            raise ImportError(
                f"Could not build import spec for {module_path}"
            )

        module = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec_module so the module's own
        # internal imports (e.g. `from utils.mcp_client import MCPClient`)
        # resolve against the framework package layout.
        sys.modules["agents_execution_agent_dynamic"] = module
        spec.loader.exec_module(module)
        _execution_agent_module = module
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

    if tool not in EXECUTION_AGENT_TOOL_NAMES:
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "UnknownTool",
                "message": (
                    f"Unknown tool {tool!r}. Valid tools: "
                    f"{list(EXECUTION_AGENT_TOOL_NAMES)}."
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

    # ---- Module load + function resolution ----------------------------
    await _broadcast(
        task.task_id,
        TaskEvent(status="running", progress="loading_execution_agent", **common),
    )
    try:
        module = await _load_execution_agent_module()
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
