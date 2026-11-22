"""Specialist agent runtimes (execution, stub, and MCP specialists).

Owns agent-name constants, module loading, and the ``_run_*_agent``
entry points formerly hosted in ``services.task_executor``.

Shared utilities live elsewhere:
  * ``core.agent_harness`` — round cap, repeat throttle, context budget, HITL
  * ``services.helpers.mcp_passthrough`` — MCP gateway pass-through
  * ``services.helpers.token_context_window`` — context-budget resolver

Imports the in-process subscription bus from ``services.subscriptions``
so this module never depends on ``task_executor`` (avoids import cycles).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Any, Optional

from services.subscriptions import TaskEvent, broadcast as _broadcast
from stores import task_store

log = logging.getLogger(__name__)

STUB_TOTAL_RUNTIME_SECONDS = 3.0


def _restore_web_ui_caller(
    task: task_store.AgentTask,
    payload: dict,
) -> tuple[bool, dict | None]:
    """Re-apply Web-UI surface for JWT minting during specialist MCP calls.

    Uses **snapshot-restore semantics** so overlapping child contexts
    (e.g. inline-drain starting a specialist task while the orchestrator
    is still mid-turn) cannot wipe the parent's live identity.

    Behaviour:

    1. Capture a snapshot of the current ``_caller_identity`` dict.
    2. If the dict already has a live ``session_id`` (the orchestrator's
       SSE stream is still active), **skip the restore** — the live
       identity is authoritative. Return ``(False, None)``.
    3. If the dict has no live ``session_id`` (the specialist is running
       after the orchestrator's SSE stream ended, e.g. a HITL-gated task
       that resumes later), apply the restore-from-payload logic and
       return ``(True, snapshot)``.

    The caller passes the returned snapshot to
    ``_restore_caller_snapshot`` in its ``finally`` block.

    Returns:
        ``(restored, snapshot)`` — ``restored`` is True when the identity
        was written (and must be undone on exit); ``snapshot`` is the
        pre-entry state to restore, or None when no restore occurred.
    """
    if payload.get("caller_surface") != "web_ui":
        return False, None
    user_id = payload.get("caller_user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        return False, None
    try:
        from agents.orchestrator.agent import (
            set_caller_identity,
            set_caller_surface,
            snapshot_caller_identity,
        )
    except Exception:
        log.debug("web_ui caller restore import failed", exc_info=True)
        return False, None

    # Snapshot BEFORE any mutation so we can restore on exit.
    snapshot = snapshot_caller_identity()

    # If the orchestrator's live identity is still active (session_id is
    # set), skip the restore — the parent owns the dict for this turn.
    if snapshot.get("session_id"):
        log.info(
            "web_ui_caller.skip_restore: live session_id present; "
            "preserving orchestrator identity for task %s",
            task.task_id,
        )
        return False, None

    # No live identity (post-SSE specialist task). Restore from payload.
    thread_id = None
    raw_thread_id = getattr(task, "thread_id", None)
    if isinstance(raw_thread_id, str) and raw_thread_id.strip():
        thread_id = raw_thread_id.strip()
    else:
        for key in ("thread_id", "external_thread_id"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                thread_id = raw.strip()
                break
    set_caller_identity(
        user_id=user_id.strip(),
        thread_id=thread_id,
        session_id=str(task.session_id) if task.session_id else None,
        task_id=str(task.task_id),
        test_run_id=(
            payload.get("test_run_id")
            if isinstance(payload.get("test_run_id"), str)
            else None
        ),
    )
    set_caller_surface("web_ui")
    return True, snapshot


def _restore_caller_snapshot(snapshot: dict | None) -> None:
    """Restore ``_caller_identity`` from a prior snapshot.

    Called in ``finally`` blocks after ``_restore_web_ui_caller``
    returned ``(True, snapshot)``. When ``snapshot`` is ``None``
    (entry was skipped), this is a no-op.
    """
    if snapshot is None:
        return
    try:
        from agents.orchestrator.agent import restore_caller_identity
    except Exception:
        log.debug("caller snapshot restore import failed", exc_info=True)
        return
    restore_caller_identity(snapshot)
    log.info("web_ui_caller.snapshot_restored")


EXECUTION_AGENT_NAME = "execution-agent"
MONITORING_AGENT_NAME = "monitoring-agent"
MONITORING_AGENT_MCP_PREFIXES: tuple[str, ...] = ("datadog_",)
ANALYSIS_AGENT_NAME = "analysis-agent"
ANALYSIS_AGENT_MCP_PREFIXES: tuple[str, ...] = ("perfanalysis_",)
REPORTING_AGENT_NAME = "reporting-agent"
REPORTING_AGENT_MCP_PREFIXES: tuple[str, ...] = ("perfreport_", "confluence_")
SCRIPT_AGENT_NAME = "script-agent"
SCRIPT_AGENT_MCP_PREFIXES: tuple[str, ...] = ("jmeter_",)
EXECUTION_AGENT_TOOL_NAMES: tuple[str, ...] = (
    "start_performance_test",
    "wait_for_completion",
    "extract_test_run_artifacts",
    "provision_performance_test",
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
# =============================================================================
# Generalized specialist module loader (PBI 3.10.2 Part A)
# =============================================================================
# Loads `agents/<agent-name>/agent.py` via `importlib.util` because
# hyphenated folder names make `from agents.execution-agent.agent import ...`
# a Python syntax error. Modules are cached per agent name to avoid
# re-paying the module import cost on every task.
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

        from utils.paths import get_framework_dir

        module_path = (
            get_framework_dir()
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


async def _adopt_vendor_run_id_after_start(
    task: task_store.AgentTask,
    tool_result: dict,
) -> Optional[str]:
    """Persist vendor ``run_id`` as framework ``test_run_id`` after start.

    Returns the adopted ID, or ``None`` when the tool result is not a
    successful start with a usable ``run_id``.
    """
    if not tool_result.get("ok"):
        return None
    run_id = tool_result.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return None
    adopted = run_id.strip()
    try:
        await task_store.set_test_run_id(task.task_id, adopted)
    except Exception:
        log.exception(
            "execution_agent.adopt_vendor_run_id.failed",
            extra={"task_id": str(task.task_id), "test_run_id": adopted},
        )
        # Still return the ID so the envelope carries it even if the
        # DB write failed — downstream LLM context can use it.
        return adopted
    log.info(
        "execution_agent.adopt_vendor_run_id",
        extra={"task_id": str(task.task_id), "test_run_id": adopted},
    )
    return adopted


def _apply_load_testing_defaults(args: dict, payload: dict) -> None:
    """Fill BlazeMeter ids from the stamped load-testing block.

    Explicit args win. A non-BlazeMeter provider is left untouched so
    the tool does not send another vendor's fields to BlazeMeter.
    """
    block = payload.get("_resolved_load_testing")
    if not isinstance(block, dict):
        return
    provider = block.get("provider")
    if provider not in (None, "", "blazemeter"):
        return
    for key in ("workspace_id", "project_id", "workspace_name", "project_name"):
        current = args.get(key)
        incoming = block.get(key)
        if (current is None or current == "") and incoming not in (None, ""):
            args[key] = str(incoming)
    resolved_env = payload.get("_resolved_environment")
    if isinstance(resolved_env, dict):
        if not args.get("environment") and resolved_env.get("environment"):
            args["environment"] = resolved_env["environment"]
        if not args.get("project") and resolved_env.get("project"):
            args["project"] = resolved_env["project"]


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
    from a "retest" that reuses an existing run_id). After a successful
    `start_performance_test`, the vendor `run_id` **is** the framework
    `test_run_id` (same value; artifact folders use that ID).

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

    # ---- Payload validation & routing ----------------------------------
    is_composite_tool = isinstance(tool, str) and tool in EXECUTION_AGENT_TOOL_NAMES
    is_mcp_name = (
        isinstance(tool, str)
        and tool
        and any(tool.startswith(p) for p in EXECUTION_AGENT_MCP_PREFIXES)
    )
    is_intent_based = not isinstance(tool, str) or not tool

    # Intent-based delegation (no tool field) or an explicit MCP tool
    # name: route through the execution-agent's LLM with its full
    # FastMCP tool catalog so it can validate args against live schemas.
    if is_intent_based or is_mcp_name:
        log.info(
            "execution-agent LLM dispatch: tool=%s intent_based=%s task=%s",
            tool, is_intent_based, task.task_id,
        )
        return await _run_mcp_specialist_agent(task, common, EXECUTION_AGENT_NAME)

    if not is_composite_tool:
        envelope["tool_result"] = {
            "ok": False,
            "error": {
                "type": "UnknownTool",
                "message": (
                    f"Unknown tool {tool!r}. Valid composite tools: "
                    f"{list(EXECUTION_AGENT_TOOL_NAMES)}. "
                    "For individual MCP operations, omit the 'tool' field "
                    "and describe the intent in 'user_message'."
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
    if tool == "provision_performance_test":
        _apply_load_testing_defaults(args, payload)

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

    # ``tool`` is guaranteed a non-empty str here: intent-based (non-str/empty)
    # payloads return early above, and non-composite names are rejected, so only
    # a known composite tool name reaches this point. Assert it so the static
    # checker narrows ``tool`` to ``str`` for the ``getattr`` lookup.
    assert isinstance(tool, str)
    fn: Any = getattr(module, tool, None)
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
        # ``callable(fn)`` narrows ``fn`` to ``(...) -> object``, so the call
        # result would be typed ``object`` (not awaitable). Tool functions are
        # async coroutines, so hold the result as ``Any`` to keep it awaitable.
        coro: Any = fn(**args)
        tool_result = await coro
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

    # After a successful start, the vendor run_id IS the framework
    # test_run_id. Persist it on the task row and echo it in the
    # envelope so later turns (Web UI thread prefetch / A2A context)
    # reuse the same ID for artifact extraction.
    if tool == "start_performance_test" and isinstance(tool_result, dict):
        adopted = await _adopt_vendor_run_id_after_start(task, tool_result)
        if adopted:
            envelope["test_run_id"] = adopted

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

    message_keys = {
        "user_message",
        "message",
        "text",
        "prompt",
        "webui_secrets_hint",
        "caller_surface",
        "caller_user_id",
    }
    context = {
        k: v for k, v in payload.items()
        if k not in message_keys
        and v is not None
        and (
            not k.startswith("_")
            or k == "_cert_profile"
            or k.startswith("_resolved_")
        )
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
# Native AG2 turn — same envelope the specialist and orchestrator callers read
# =============================================================================
# HaltEvent source -> exit_reason. Sources match the harness and the HITL gate.
_HALT_EXIT_REASONS = {
    "round-limit": "max_rounds_reached",
    "tool-repeat-throttle": "consecutive_repeat_limit",
    "hitl-gate": "hitl_halted",
}


def _native_result_fields(result_event: Any) -> tuple[bool, str]:
    """Return ``(ok, result_text)`` for one native tool-result event.

    A ``ToolErrorEvent``, or a JSON body with ``ok: false``, is a failure.
    The text is the joined string parts of the result, which the round audit
    stores as a short snippet.
    """
    from ag2.events import ToolErrorEvent

    if isinstance(result_event, ToolErrorEvent):
        error = result_event.error
        payload = {
            "ok": False,
            "error": {
                "type": type(error).__name__,
                "message": str(error)[:500],
            },
        }
        return False, json.dumps(payload)

    result = getattr(result_event, "result", None)
    parts = getattr(result, "parts", None) or []
    chunks: list[str] = []
    for part in parts:
        content = getattr(part, "content", None)
        if isinstance(content, str):
            chunks.append(content)
    result_str = "".join(chunks)
    ok = True
    try:
        parsed = json.loads(result_str)
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            ok = False
    except (json.JSONDecodeError, TypeError):
        ok = True
    return ok, result_str


def _reconstruct_tool_rounds(events: list[Any]) -> list[dict[str, Any]]:
    """Rebuild the per-round tool audit from a native event stream.

    Each ``ToolCallsEvent`` is one round. Member calls are matched to their
    ``ToolCallEvent`` (name and arguments) and ``ToolResultEvent`` (by
    ``parent_id``). Result snippets stay at 200 characters so the
    comparison-id scan keeps seeing the same prefix it did before.
    """
    from ag2.events import ToolCallEvent, ToolCallsEvent, ToolResultEvent

    calls_by_id: dict[str, Any] = {}
    results_by_parent: dict[str, Any] = {}
    for event in events:
        if isinstance(event, ToolCallsEvent):
            for member in event.calls:
                member_id = getattr(member, "id", None)
                if isinstance(member_id, str):
                    calls_by_id.setdefault(member_id, member)
        elif isinstance(event, ToolCallEvent):
            calls_by_id[event.id] = event
        elif isinstance(event, ToolResultEvent):
            parent_id = event.parent_id
            if isinstance(parent_id, str):
                results_by_parent.setdefault(parent_id, event)

    rounds: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, ToolCallsEvent):
            continue
        audit: dict[str, Any] = {
            "round": len(rounds) + 1,
            "tool_calls": [],
            "results": [],
        }
        for member in event.calls:
            member_id = getattr(member, "id", None)
            if not isinstance(member_id, str):
                continue
            call = calls_by_id.get(member_id)
            if call is None:
                continue
            raw_args = getattr(call, "arguments", None)
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            name = getattr(call, "name", "")
            if not isinstance(name, str):
                name = str(name)
            audit["tool_calls"].append({"name": name, "args": args})
            result_event = results_by_parent.get(member_id)
            if result_event is None:
                continue
            ok, result_str = _native_result_fields(result_event)
            audit["results"].append({
                "tool": name,
                "ok": ok,
                "snippet": result_str[:200],
            })
        rounds.append(audit)
    return rounds


def _native_exit_reason(events: list[Any], reply_text: str) -> str:
    """Derive ``exit_reason`` from the latest known halt, or from the reply."""
    from ag2.events import HaltEvent

    for event in reversed(events):
        if not isinstance(event, HaltEvent):
            continue
        source = event.source
        if isinstance(source, str) and source in _HALT_EXIT_REASONS:
            return _HALT_EXIT_REASONS[source]
    return "text_response" if reply_text else "null_reply"


def _native_ctx_metrics(events: list[Any], context_budget: Any) -> dict[str, Any]:
    """Build the envelope context fields from the stream's token usage.

    Uses the last main-loop ``UsageEvent`` (``kind == "model_call"`` and no
    delegated ``task_id``) as the prompt size. ``context_limit`` stays the
    model window from the budget resolver, and utilization is a percentage,
    matching the fields the previous runner stored on the envelope.
    """
    from ag2.events import UsageEvent

    limit = int(context_budget.context_limit)
    prompt_tokens = 0
    for event in events:
        if not isinstance(event, UsageEvent):
            continue
        if event.kind != "model_call" or event.task_id is not None:
            continue
        usage = event.usage
        prompt_tokens = int(usage.prompt_tokens or 0)
    utilization = (prompt_tokens / limit) if limit else 0.0
    return {
        "context_tokens": prompt_tokens,
        "context_utilization_pct": round(utilization * 100, 1),
        "context_limit": limit,
    }


async def _run_native_agent(
    agent: Any,
    *,
    user_prompt: str,
    secrets_hint: str | None,
    task_id: Any,
    common: dict[str, Any],
    agent_name: str,
    max_tool_rounds: int,
    context_budget: Any,
    extra_prompt: tuple[str, ...] = (),
    extra_observers: tuple[Any, ...] = (),
) -> tuple[str, str, list[dict[str, Any]], str, dict[str, Any]]:
    """Run one native ``agent.ask`` and shape the specialist envelope tuple.

    The agent already carries the harness, so this only runs the turn and
    rebuilds ``(reply_text, raw_reply, tool_rounds, exit_reason, ctx_metrics)``
    from the reply history. Per-round progress events are emitted after the
    turn so ``tool_round_N_of_M`` still reaches SSE subscribers.

    ``secrets_hint`` is a names-only briefing. It is passed as an extra
    prompt string, separate from the user message. ``extra_prompt`` holds
    earlier thread rows for the orchestrator. ``extra_observers`` are added
    to the harness observers for this turn only.
    """
    ask_kwargs: dict[str, Any] = {"variables": {"task_id": str(task_id)}}
    prompt_parts: list[str] = []
    if secrets_hint:
        prompt_parts.append(secrets_hint)
    prompt_parts.extend(extra_prompt)
    if prompt_parts:
        ask_kwargs["prompt"] = tuple(prompt_parts)
    if extra_observers:
        ask_kwargs["observers"] = extra_observers

    reply = await agent.ask(user_prompt, **ask_kwargs)
    history = reply.context.stream.history
    events = list(await history.get_events())
    body = reply.body
    reply_text = body if isinstance(body, str) else (str(body) if body else "")
    tool_rounds = _reconstruct_tool_rounds(events)
    exit_reason = _native_exit_reason(events, reply_text)
    ctx_metrics = _native_ctx_metrics(events, context_budget)

    for audit in tool_rounds:
        tool_calls = audit["tool_calls"]
        names = [
            tc["name"]
            for tc in tool_calls
            if isinstance(tc, dict) and isinstance(tc.get("name"), str)
        ]
        round_num = audit["round"]
        await _broadcast(
            task_id,
            TaskEvent(
                status="running",
                progress=f"tool_round_{round_num}_of_{max_tool_rounds}",
                result={
                    "round": round_num,
                    "tools_called": names,
                    "agent": agent_name,
                    **ctx_metrics,
                },
                **common,
            ),
        )

    return reply_text, reply_text, tool_rounds, exit_reason, ctx_metrics


# =============================================================================
# Generic MCP specialist dispatch
# =============================================================================
# Used by specialist agents whose MCP tools are registered at build time.
# The native agent carries those tool schemas. ``agent.ask`` runs the turn;
# the harness enforces the round cap, repeat throttle, context budget, and
# per-tool approval gate.


async def _run_mcp_specialist_agent(
    task: task_store.AgentTask,
    common: dict,
    agent_name: str,
) -> dict:
    """Build and invoke a specialist's native AG2 agent with MCP tools.

    The specialist's ``build_*_agent()`` factory creates an ``ag2.Agent``
    with the shared harness and MCP tools registered via
    ``mcp_tool_registry``. ``agent.ask`` runs the turn. The harness
    enforces the round cap, repeat throttle, context budget, and
    per-tool approval gate. This function translates the reply into the
    envelope below.

    The payload carries the user's original message (``user_message``)
    plus any contextual data the orchestrator included (test_run_id,
    environment, timestamps, etc.). The specialist's LLM uses all
    available information to fulfill the request.

    Return envelope::

        {
          "agent":        "<agent_name>",
          "test_run_id":  "<echoed from payload, or None>",
          "user_message": "<echoed for audit trail>",
          "reply_text":   "<specialist LLM response text>",
          "reply_raw":    "<specialist LLM response text>",
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
    # Reuse-only resolver: the orchestrator-level resolver (A2A ingress
    # and Web-UI ingress) is responsible for minting on first contact.
    # By the time a specialist envelope runs, the authoritative ID must
    # already be present on either the task row or the payload.
    #
    # Last-resort fallback: script-agent is the sole exception — if the
    # upstream resolver failed to populate the ID (e.g. a legacy caller
    # that bypassed the ingress plumbing), mint locally so artifact
    # folders don't fail opaquely. All other specialists proceed with
    # test_run_id=None (not every tool call requires one).
    from core.test_run_id import mint_test_run_id, resolve_test_run_id

    payload = dict(payload) if isinstance(payload, dict) else {}
    test_run_id = resolve_test_run_id(
        getattr(task, "test_run_id", None),
        payload=payload,
    )
    if test_run_id:
        # resolve_test_run_id does not write back to the payload (that's
        # the orchestrator-level resolver's job). Mirror the previous
        # behaviour here so prompt composition sees the ID.
        payload["test_run_id"] = test_run_id
    elif agent_name == SCRIPT_AGENT_NAME:
        test_run_id = mint_test_run_id()
        payload["test_run_id"] = test_run_id
        log.warning(
            "specialist_envelope.last_resort_mint",
            extra={
                "agent_name": agent_name,
                "task_id": str(task.task_id),
                "test_run_id": test_run_id,
                "note": (
                    "upstream resolver should have populated test_run_id "
                    "before this envelope ran; minting defensively"
                ),
            },
        )

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
    # Web-UI secrets briefing (names only) is injected by
    # delegate_to_specialist. Pass it as an extra prompt string so
    # specialists do not re-ask cloud vs onprem when confluence_mode is set.
    raw_hint = payload.get("webui_secrets_hint")
    secrets_hint = raw_hint.strip() if isinstance(raw_hint, str) and raw_hint.strip() else None

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

    from utils.paths import get_framework_dir
    framework_dir = get_framework_dir()
    if str(framework_dir) not in sys.path:
        sys.path.insert(0, str(framework_dir))

    from core.agent_harness import load_harness_settings
    from services.helpers.token_context_window import resolve_context_budget_for_agent
    from utils import config_loader

    agent_config = config_loader.load_agent_config(agent_name)
    # The harness enforces loop limits and the prompt budget. These reads
    # only label SSE progress and fill the envelope's context fields.
    harness_settings = load_harness_settings(agent_name)
    context_budget = resolve_context_budget_for_agent(agent_name)

    # ---- Detect stateful namespaces -----------------------------------
    from .mcp_tool_registry import STATEFUL_NAMESPACES

    all_namespaces = list(
        agent_config.get("mcp_tools", {}).get("allowed_namespaces", [])
    )
    playwright_cfg = agent_config.get("playwright_mcp", {})
    # Only opt in to the stateful Playwright browser namespace when the
    # agent's config explicitly sets `playwright_mcp.enabled: true`. A
    # missing / empty / null `playwright_mcp:` section MUST NOT flag the
    # agent as stateful — doing so causes the executor to pass a
    # `stateful_client_holder=` kwarg to the factory, which only
    # script-agent's factory accepts.
    if isinstance(playwright_cfg, dict) and playwright_cfg.get("enabled", False):
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

    agent: Any = None
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

    if isinstance(agent, dict):
        envelope["error"] = {
            "type": "AgentDisabled",
            "message": f"{agent_name} is disabled in its config.",
        }
        return envelope

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

    # ---- Run one native agent turn ------------------------------------
    restored_web_ui, web_ui_snapshot = _restore_web_ui_caller(task, payload)
    try:
        reply_text, raw_reply, tool_rounds, exit_reason, ctx_metrics = (
            await _run_native_agent(
                agent,
                user_prompt=user_prompt,
                secrets_hint=secrets_hint,
                task_id=task.task_id,
                common=common,
                agent_name=agent_name,
                max_tool_rounds=harness_settings.max_tool_rounds,
                context_budget=context_budget,
            )
        )
    except Exception as exc:
        log.exception("%s agent invocation failed for task %s", agent_name, task.task_id)
        raise RuntimeError(f"{agent_name} agent invocation failed: {exc}") from exc
    finally:
        if restored_web_ui:
            _restore_caller_snapshot(web_ui_snapshot)
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
        try:
            from core.base_agent import aclose_agent

            await aclose_agent(agent)
        except Exception:
            log.warning("Failed to close %s agent", agent_name, exc_info=True)

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
    if ctx_metrics:
        envelope["context_tokens"] = ctx_metrics["context_tokens"]
        envelope["context_utilization_pct"] = ctx_metrics["context_utilization_pct"]
        envelope["context_limit"] = ctx_metrics["context_limit"]

    # ── comparison_id capture (reporting-agent only) ─────────────
    # The PerfReport MCP mints its own ``comparison_id`` on the way
    # back from ``comparison_generate_report`` and sibling tools. The
    # Phase 2 resolver returns SKIP_MINT for comparison intents (A2A
    # payloads carrying ``task_type == "generate_comparison_report"``
    # or Web-UI payloads carrying ``comparison.test_run_ids``), so no
    # framework-side ID is persisted on the task row at that point.
    # Capture the authoritative ``comparison_id`` from the tool result
    # here and persist it to ``agent_tasks.test_run_id`` so the task
    # row's foreign key links back to the correct artifact bundle.
    #
    # Why a regex scan: ``round_audit["results"][*]["snippet"]`` is
    # truncated to 200 chars during audit capture, but PerfReport MCP
    # tool JSON reliably places ``comparison_id`` near the start of
    # the payload (first-level field, before any large nested arrays).
    # A tolerant string scan is good enough until Phase 7 adds a
    # structured envelope field.
    if agent_name == REPORTING_AGENT_NAME:
        comparison_id = _extract_comparison_id_from_tool_rounds(tool_rounds)
        if comparison_id:
            envelope["comparison_id"] = comparison_id
            try:
                await task_store.set_test_run_id(task.task_id, comparison_id)
                log.info(
                    "specialist_envelope.comparison_id_captured",
                    extra={
                        "agent_name": agent_name,
                        "task_id": str(task.task_id),
                        "comparison_id": comparison_id,
                    },
                )
            except Exception:
                log.exception(
                    "specialist_envelope.comparison_id_persist_failed",
                    extra={
                        "agent_name": agent_name,
                        "task_id": str(task.task_id),
                        "comparison_id": comparison_id,
                    },
                )

    return envelope


# A tolerant regex that scans tool-result snippets for a JSON
# ``"comparison_id": "..."`` field. Matches cover:
#   - top-level: {"comparison_id": "cmp-xxx", ...}
#   - nested one level: {"data": {"comparison_id": "cmp-xxx", ...}}
# Snippet truncation at 200 chars still leaves this field visible for
# typical PerfReport responses. Returns the first match (loop-order).
_COMPARISON_ID_RE = re.compile(
    r'"comparison_id"\s*:\s*"([^"]+)"'
)


def _extract_comparison_id_from_tool_rounds(
    tool_rounds: list[dict],
) -> Optional[str]:
    """Scan tool-result snippets for the first ``comparison_id`` value.

    Walks every round's ``results`` entries newest-first (round order
    reflects LLM call order) and returns the first non-empty
    ``comparison_id`` found. Returns ``None`` when no snippet contains
    the field — the caller treats this as "no comparison report was
    generated during this specialist run" and makes no persistence
    change.

    Args:
        tool_rounds: The per-round audit list produced by
            the native agent turn. Each round carries
            ``results: [{"tool", "ok", "snippet"}, ...]``.

    Returns:
        The first captured ``comparison_id`` string, or ``None`` when
        the scan finds no match.
    """
    for round_entry in tool_rounds:
        results = round_entry.get("results") if isinstance(round_entry, dict) else None
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            if result.get("ok") is False:
                # Failed tool calls do not carry a usable comparison_id.
                continue
            snippet = result.get("snippet")
            if not isinstance(snippet, str) or not snippet:
                continue
            match = _COMPARISON_ID_RE.search(snippet)
            if match:
                captured = match.group(1).strip()
                if captured:
                    return captured
    return None
