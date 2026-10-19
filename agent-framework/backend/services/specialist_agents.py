"""Specialist agent runtimes (execution, stub, and MCP specialists).

Owns agent-name constants, module loading, and the ``_run_*_agent``
entry points formerly hosted in ``services.task_executor``.

Shared utilities live elsewhere:
  * ``services.helpers.tool_loop`` — multi-turn AG2 tool loop
  * ``services.helpers.mcp_passthrough`` — MCP gateway pass-through
  * ``services.helpers.token_context_window`` — token estimate / compact

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

from services.helpers.mcp_passthrough import (
    call_mcp_tool_passthrough,
    passthrough_process_session_artifacts,
)
from services.helpers.tool_loop import run_multi_turn_tool_loop
from services.subscriptions import TaskEvent, broadcast as _broadcast
from stores import task_store

log = logging.getLogger(__name__)

STUB_TOTAL_RUNTIME_SECONDS = 3.0


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
            if tool == "blazemeter_process_session_artifacts":
                mcp_result = await passthrough_process_session_artifacts(args)
            else:
                mcp_result = await call_mcp_tool_passthrough(tool, args)
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

    from utils.paths import get_framework_dir
    framework_dir = get_framework_dir()
    if str(framework_dir) not in sys.path:
        sys.path.insert(0, str(framework_dir))

    from utils import config_loader

    agent_config = config_loader.load_agent_config(agent_name)

    max_tool_rounds = int(agent_config.get("max_tool_rounds", 7))
    max_consecutive_repeats = int(agent_config.get("max_consecutive_repeats", 3))
    raw_polling_tools = agent_config.get("polling_tools", [])
    polling_tools = frozenset(
        raw_polling_tools if isinstance(raw_polling_tools, list) else []
    )
    polling_max_consecutive_repeats = int(
        agent_config.get("polling_max_consecutive_repeats", 30)
    )
    context_limit = int(agent_config.get("context_limit", 128000))
    compaction_threshold = float(agent_config.get("compaction_threshold", 0.80))
    compaction_preserve_recent = int(agent_config.get("compaction_preserve_recent", 5))

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
        reply_text, raw_reply, tool_rounds, exit_reason, ctx_metrics = (
            await run_multi_turn_tool_loop(
                agent=agent,
                messages=messages_for_llm,
                max_tool_rounds=max_tool_rounds,
                max_consecutive_repeats=max_consecutive_repeats,
                polling_tools=polling_tools,
                polling_max_consecutive_repeats=polling_max_consecutive_repeats,
                context_limit=context_limit,
                compaction_threshold=compaction_threshold,
                compaction_preserve_recent=compaction_preserve_recent,
                task_id=task.task_id,
                common=common,
                agent_name=agent_name,
                task=task,
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
            ``run_multi_turn_tool_loop``. Each round carries
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
