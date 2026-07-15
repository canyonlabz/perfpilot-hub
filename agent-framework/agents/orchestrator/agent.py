"""PerfPilot Orchestrator — AG2 ConversableAgent factory + delegation tools.

This module ships the four-file pattern's `agent.py` slot per V2 doc §7.1:
construct and return the agent on demand, with all configuration loaded
from the sibling `INSTRUCTIONS.md`, `agent_card.json`, and *one of*
`config.yaml` / `config.example.yaml`. Callers (the AG-UI bridge in PBI
3.7.7, the A2A task executor in PBI 3.7.8) import `build_orchestrator()`
and wrap the result.

**What this module ships (PBIs 3.7.1, 3.7.2, 3.7.3, 3.7.4, 3.7.5, 3.7.6):**

- A working `ConversableAgent` with the real long-form system prompt
  loaded from `INSTRUCTIONS.md` (PBI 3.7.2).
- All four delegation tools registered on the agent via AG2's
  `register_for_llm` + `register_for_execution` decorator pair:
    1. `list_available_specialists()`         (PBI 3.7.3)
    2. `delegate_to_specialist(...)`          (PBI 3.7.4)
    3. `check_task_status(...)`               (PBI 3.7.5)
    4. `request_human_approval(...)`          (PBI 3.7.6)
- Per-agent `llm_provider` resolution via the framework's
  `utils.base_agent.resolve_agent_config_path()` candidate walker.

**Tool design notes:**

- Tools are defined as module-level functions so the smoke test can
  import and exercise them directly (`from agents.orchestrator.agent
  import list_available_specialists`) without spinning up an LLM.
- `delegate_to_specialist` and `check_task_status` call back into the
  *local* A2A surface via httpx (default `http://127.0.0.1:8001`,
  overridable via `PERFPILOT_A2A_BASE_URL`). They return structured
  dicts (success or error) rather than raising, so the LLM can narrate
  failures to the user instead of the agent loop crashing.
- `request_human_approval` is `async` (AG2 0.13.3 supports async tools
  natively): it inserts a row via `hitl_store.create_prompt`, polls
  `hitl_store.get_approval` until terminal state or timeout.

**What this module still does NOT do (deferred):**

- MCP client wiring (deferred to F3.8 per Decision 12).
- DB-loaded message history (PBI 3.7.7 + Decision 14).
- A2A `task_executor` dispatch (PBI 3.7.8).

Heavy imports (`autogen`, `httpx`, `yaml`) live inside the functions that
need them so this module is cheap to import in tests / IDE indexing that
do not exercise the agent.

NOTE: This module deliberately does NOT use `from __future__ import
annotations`. AG2 0.13.3 introspects tool function signatures via
pydantic's `TypeAdapter`, which cannot evaluate stringified `Annotated`
annotations. Keeping annotations as live types makes tool registration
work without per-call `.rebuild()` shenanigans.
"""

import asyncio
import json
import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, Optional
from uuid import UUID

log = logging.getLogger(__name__)

AGENT_DIR = Path(__file__).resolve().parent
AGENT_NAME = "orchestrator"
FRAMEWORK_DIR = AGENT_DIR.parent.parent  # agent-framework/
AGENTS_ROOT = AGENT_DIR.parent  # agent-framework/agents/

INSTRUCTIONS_PATH = AGENT_DIR / "INSTRUCTIONS.md"
AGENT_CARD_PATH = AGENT_DIR / "agent_card.json"

# A2A base URL for the orchestrator's delegate / check tools. Defaults to
# the local A2A server on port 8001; operators can override via env var if
# they ever decouple the deployment topology.
DEFAULT_A2A_BASE_URL = "http://127.0.0.1:8001"
A2A_BASE_URL_ENV = "PERFPILOT_A2A_BASE_URL"

# AG-UI base URL for Web UI delegation. Resolution order:
#   1. PERFPILOT_AGUI_BASE_URL  (full URL override)
#   2. http://127.0.0.1:{AGUI_PORT}  (reads same env var agui_server.py uses)
#   3. http://127.0.0.1:8002  (hardcoded fallback)
DEFAULT_AGUI_PORT = "8002"
AGUI_BASE_URL_ENV = "PERFPILOT_AGUI_BASE_URL"
AGUI_PORT_ENV = "AGUI_PORT"

# HITL polling defaults (overridable per-call). 5 minutes is generous for
# Epic 3 smokes; production deployments should raise this for real review
# workflows.
DEFAULT_HITL_TIMEOUT_SECONDS = 300.0
DEFAULT_HITL_POLL_INTERVAL_SECONDS = 2.0

# ── ContextVars (A2A task-executor path only) ──
# AG2 runs tool functions in an isolated thread, so ContextVars set in
# the AG-UI handler do NOT propagate into tool execution. Web UI tool
# functions therefore use HTTP calls to the AG-UI server's own endpoints
# (POST /api/delegate, GET /api/tasks/{id}) instead of direct DB access.
# These ContextVars remain only for the A2A task_executor path, which
# invokes the orchestrator in the same thread where the vars are set.
agent_user_id_var: ContextVar[Optional[str]] = ContextVar("perfpilot_agent_user_id", default=None)
agent_thread_id_var: ContextVar[Optional[str]] = ContextVar("perfpilot_agent_thread_id", default=None)
agent_external_session_id_var: ContextVar[Optional[str]] = ContextVar(
    "perfpilot_agent_external_session_id", default=None
)
agent_request_source_var: ContextVar[Optional[str]] = ContextVar(
    "perfpilot_agent_request_source", default=None
)
agent_session_id_var: ContextVar[Optional[str]] = ContextVar(
    "perfpilot_agent_session_id", default=None
)

# ── Per-request identity for HTTP header construction ──
# AG2's thread isolation means tools can't read ContextVars.
# The AG-UI handler sets these before dispatch so that
# _agent_outbound_headers() can build the correct HTTP headers
# for calls to /api/delegate and /api/tasks/{id}.
# Only identity values -- no event loops, no DB connections.
_caller_identity: dict[str, Optional[str]] = {
    "user_id": None,
    "thread_id": None,
    "session_id": None,
}

# Strong references to background tasks so they aren't garbage collected
# before completion. Standard Python pattern for fire-and-forget tasks.
_background_tasks: set[asyncio.Task] = set()

# Task IDs queued for background execution. The tool function appends here
# (inside AG2's context), and agui_server drains after dispatch completes
# (in normal FastAPI context where asyncio.create_task works reliably).
_pending_executions: list[UUID] = []


def drain_pending_executions() -> list[UUID]:
    """Pop all queued task IDs. Called by agui_server after dispatch."""
    result = list(_pending_executions)
    _pending_executions.clear()
    return result


def set_caller_identity(
    user_id: Optional[str],
    thread_id: Optional[str],
    session_id: Optional[str],
) -> None:
    _caller_identity["user_id"] = user_id
    _caller_identity["thread_id"] = thread_id
    _caller_identity["session_id"] = session_id


def clear_caller_identity() -> None:
    _caller_identity["user_id"] = None
    _caller_identity["thread_id"] = None
    _caller_identity["session_id"] = None


# =============================================================================
# Standalone DB helpers (bypass shared asyncpg pool)
# =============================================================================

async def _get_standalone_conn():
    """Open a fresh asyncpg connection (not from the shared pool).

    AG2's dispatch pipeline corrupts pool connection state across internal
    task boundaries. These helpers use a standalone connection that is
    completely isolated from the pool used by the rest of the AG-UI server.
    """
    import asyncpg
    from utils.db import load_settings_from_env

    settings = load_settings_from_env()
    return await asyncpg.connect(
        host=settings.host,
        port=settings.port,
        database=settings.database,
        user=settings.user,
        password=settings.password,
    )


async def _standalone_create_task(
    session_id: UUID,
    agent_name: str,
    payload: dict,
    test_run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """INSERT a task row using a standalone connection. Returns a plain dict."""
    conn = await _get_standalone_conn()
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_tasks (
                session_id, agent_name, status,
                test_run_id, thread_id, payload
            )
            VALUES ($1, $2, 'pending', $3, $4, $5::jsonb)
            RETURNING task_id, session_id, agent_name, status,
                      test_run_id, thread_id, submitted_at
            """,
            session_id,
            agent_name,
            test_run_id,
            thread_id,
            json.dumps(payload),
        )
        return {
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "agent_name": row["agent_name"],
            "status": row["status"],
            "submitted_at": row["submitted_at"].isoformat(),
        }
    finally:
        await conn.close()


async def _standalone_get_task(task_id: UUID) -> Optional[dict]:
    """SELECT a task row using a standalone connection. Returns a plain dict."""
    conn = await _get_standalone_conn()
    try:
        row = await conn.fetchrow(
            """
            SELECT task_id, session_id, agent_name, status,
                   test_run_id, thread_id, result, error,
                   submitted_at, started_at, completed_at
            FROM agent_tasks
            WHERE task_id = $1
            """,
            task_id,
        )
        if row is None:
            return None
        result_val = row["result"]
        error_val = row["error"]
        if isinstance(result_val, str):
            result_val = json.loads(result_val)
        if isinstance(error_val, str):
            error_val = json.loads(error_val)
        return {
            "task_id": row["task_id"],
            "session_id": row["session_id"],
            "agent_name": row["agent_name"],
            "status": row["status"],
            "test_run_id": row["test_run_id"],
            "result": result_val,
            "error": error_val,
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        }
    finally:
        await conn.close()


# =============================================================================
# Factory
# =============================================================================

def build_orchestrator() -> Any:
    """Construct the PerfPilot Orchestrator AG2 ConversableAgent with all 4 tools.

    Resolution order for the LLM provider:
      1. Per-agent `llm_provider` block in the first existing of
         `config.yaml` (operator-side override, gitignored) or
         `config.example.yaml` (committed default).
      2. Global fallback `config/agents.yaml -> default_llm_provider`
         via `utils.llm_provider.load_default_provider_config()`.

    Returns:
        An `autogen.ConversableAgent` instance with the long-form
        `INSTRUCTIONS.md` as `system_message`, the four delegation tools
        registered for both LLM-visibility and local execution, and
        `human_input_mode="NEVER"` (this is a server agent).

    Raises:
        FileNotFoundError: when one of the four sibling files is missing.
        Anything `LLMProvider.to_ag2_config()` raises when credentials
        are not configured.
    """
    from autogen import ConversableAgent  # type: ignore

    from utils.llm_provider import LLMProvider

    system_message = _load_system_message()
    provider_config = _resolve_provider_config()
    provider = LLMProvider(provider_config)

    log.info(
        "Building %s (provider=%s, model=%s)",
        AGENT_NAME, provider.provider, provider.get_model_name(),
    )

    agent = ConversableAgent(
        name=AGENT_NAME,
        system_message=system_message,
        llm_config=provider.to_ag2_config(),
        # Server-side: never block on stdin.
        human_input_mode="NEVER",
        # Allow the LLM to chain tool-call -> tool-result -> follow-up reply.
        # Typical: list_available_specialists -> delegate_to_specialist ->
        # check_task_status -> final reply. 5 is the headroom; deeper
        # chains likely indicate a tool error loop and should terminate.
        max_consecutive_auto_reply=5,
    )

    _register_tools(agent)
    return agent


def _register_tools(agent: Any) -> None:
    """Wire the four delegation tools onto the ConversableAgent.

    Both decorators are required for each tool:
      - `register_for_llm(...)` advertises the tool in the LLM's tool catalog
        so the model can decide to call it.
      - `register_for_execution()` tells AG2 that THIS agent is the one that
        actually runs the function (vs. a separate executor agent).

    AG2 0.13.3 uses `api_style='tool'` by default, which is what we want
    (the OpenAI-style `tools=` parameter, not legacy `functions=`).
    """
    agent.register_for_llm(
        name="list_available_specialists",
        description=(
            "List PerfPilot specialist agents currently enabled in agents.yaml. "
            "Returns an array of {name, description, status, mcp_namespaces, url} "
            "objects. Excludes the orchestrator itself. Use before delegate_to_specialist "
            "to confirm the target is enabled, or to answer 'what can you do?' user questions."
        ),
    )(list_available_specialists)
    agent.register_for_execution()(list_available_specialists)

    agent.register_for_llm(
        name="delegate_to_specialist",
        description=(
            "Route a task payload to a specific specialist agent via the local A2A "
            "surface (POST /agents/{agent_name}/tasks/send). Returns immediately with "
            "the specialist's task_id; the actual work runs asynchronously. Always include "
            "test_run_id when the work is part of a tracked test run so downstream agents "
            "correlate. Use after list_available_specialists confirms the target is enabled."
        ),
    )(delegate_to_specialist)
    agent.register_for_execution()(delegate_to_specialist)

    agent.register_for_llm(
        name="check_task_status",
        description=(
            "Poll the current status of a previously-delegated task. Returns "
            "{status, result, error, ...} where status is one of pending/running/"
            "completed/failed/cancelled. Use when the user asks 'is it done?' or when "
            "the orchestrator needs a specialist's terminal result before advancing the "
            "pipeline to the next stage. Do not spin-loop; the underlying client throttles."
        ),
    )(check_task_status)
    agent.register_for_execution()(check_task_status)

    agent.register_for_llm(
        name="request_human_approval",
        description=(
            "Open a Human-in-the-Loop approval prompt and BLOCK until the human "
            "decides (approves / rejects-with-feedback / timeout). Returns "
            "{decision, feedback, decided_by, timed_out}. Use ONLY when HITL is "
            "required per the orchestrator config (§4.1 of INSTRUCTIONS.md). "
            "CRITICAL: task_id MUST be a valid UUID from a PRIOR "
            "delegate_to_specialist call — it is the internal task identifier. "
            "Do NOT pass a BlazeMeter test_id, an integer, or any non-UUID value "
            "as task_id. You must delegate first to obtain a task_id before "
            "calling this tool."
        ),
    )(request_human_approval)
    agent.register_for_execution()(request_human_approval)


# =============================================================================
# Tool 1 (PBI 3.7.3): list_available_specialists
# =============================================================================

def list_available_specialists() -> str:
    """Return the catalog of currently-enabled PerfPilot specialist agents.

    Each entry is a dict shaped roughly like an A2A AgentCard plus a few
    convenience fields the orchestrator uses to decide where to delegate:

        {
            "name": "execution-agent",
            "display_name": "PerfPilot Execution Agent",
            "description": "...",
            "status": "available" | "in_development" | "stub",
            "tags": [...],
            "url": "/agents/execution-agent",
            "mcp_namespaces": ["blazemeter_*"],
        }

    Sources:
      - `utils.agents_config.list_enabled_agents()` for the enable/disable
        gate (reads `config/agents.yaml`).
      - `utils.base_agent.read_agent_card()` for each agent's on-disk card
        (with the truthful stub fallback when no card has been scaffolded).
      - The agent's optional `mcp_namespaces` from its on-disk
        `config.example.yaml` / `config.yaml` (when present); the
        orchestrator surfaces this so the LLM can reason about which
        specialist owns which MCP territory.

    Excludes:
      - The orchestrator itself ("never recommend yourself").
      - Any agent disabled in `agents.yaml`.
      - Any agent whose folder does not exist on disk yet (the
        `agents.yaml` may list an agent ahead of its scaffold).

    Returns:
        A JSON string encoding the list of specialist dicts.
        Empty JSON array when no specialists are enabled.
    """
    from utils import agents_config

    enabled_names = agents_config.list_enabled_agents(FRAMEWORK_DIR)
    specialists: list[dict] = []
    for name in enabled_names:
        if name == AGENT_NAME:
            continue
        agent_folder = AGENTS_ROOT / name
        if not agent_folder.is_dir():
            log.debug(
                "list_available_specialists: %s enabled in agents.yaml but folder %s "
                "does not exist on disk; skipping.",
                name, agent_folder,
            )
            continue
        try:
            card = _safe_read_agent_card(agent_folder)
            mcp_namespaces = _read_mcp_namespaces(agent_folder)
        except Exception:
            log.exception(
                "list_available_specialists: failed to read card for %s; skipping",
                name,
            )
            continue
        specialists.append(
            {
                "name": card.get("name", name),
                "display_name": card.get("display_name") or card.get("name", name),
                "description": card.get("description", ""),
                "status": card.get("status", "stub"),
                "tags": list(card.get("tags") or []),
                "url": card.get("url", f"/agents/{name}"),
                "mcp_namespaces": mcp_namespaces,
            }
        )
    return json.dumps(specialists, indent=2)


def _safe_read_agent_card(agent_folder: Path) -> dict:
    """Wrapper that imports the helper lazily so this module stays import-cheap."""
    from utils.base_agent import read_agent_card

    return read_agent_card(agent_folder)


def _read_mcp_namespaces(agent_folder: Path) -> list[str]:
    """Return the agent's `mcp_tools` allowlist from its per-agent config.

    Honors the `config.yaml` -> `config.example.yaml` candidate walker.
    Returns an empty list when the block is absent, commented out, or
    the agent has no per-agent config file at all (legitimate for early
    scaffolds).
    """
    from utils.base_agent import resolve_agent_config_path

    config_path = resolve_agent_config_path(agent_folder)
    if config_path is None:
        return []
    import yaml

    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            parsed = yaml.safe_load(f) or {}
    except Exception:
        log.exception("_read_mcp_namespaces: failed to parse %s", config_path)
        return []
    raw = parsed.get("mcp_tools")
    if isinstance(raw, dict):
        raw = raw.get("allowed_namespaces", [])
    if not raw or not isinstance(raw, list):
        return []
    return [str(entry) for entry in raw if isinstance(entry, (str, int))]


# =============================================================================
# Tool 2 (PBI 3.7.4): delegate_to_specialist
# =============================================================================

async def delegate_to_specialist(
    agent_name: Annotated[str, "Specialist agent name (e.g. 'execution-agent')."],
    payload: Annotated[dict, "JSON-serializable task payload (the body POSTed to A2A)."],
    test_run_id: Annotated[
        Optional[str],
        "Optional test_run_id for correlation across the PTLC pipeline.",
    ] = None,
) -> str:
    """Create and dispatch work on a specialist agent.

    Dual routing: the target surface depends on where the
    orchestrator itself was invoked from.

      - **Web UI** (``agent_request_source_var == "web_ui"``):
        Direct in-process delegation via ``task_store.create_task()``
        + ``task_executor.execute_task()``. The CopilotKit
        ``thread_id`` is stored directly on the task so the browser's
        Tasks panel can discover it. No HTTP hop — avoids the event-
        loop deadlock that occurs when a synchronous tool POSTs back
        to the same ASGI server.
      - **A2A / headless** (default):
        Async HTTP POST to the A2A server
        ``/agents/{name}/tasks/send``. The thread travels as
        ``X-External-Thread-Id``.

    Returns immediately (the work runs asynchronously). Use
    ``check_task_status(agent_name, task_id)`` for polling.

    Returns:
        On success: {"ok": True, "task_id": "<uuid>", "session_id":
            "<uuid>", "agent_name": "<name>", "status": "<status>",
            "submitted_at": "<iso>"}.
        On failure: {"ok": False, "error": {"type": "<...>", "message":
            "<...>"}}.

    The tool never raises -- the LLM gets a structured error dict so it
    can narrate the failure to the user instead of crashing the agent
    loop.
    """
    # AG2's dispatch pipeline corrupts asyncpg pool connection state across
    # internal task boundaries ("cannot perform operation: another operation
    # is in progress"). Bypass the shared pool: use a standalone connection
    # for the INSERT, then fire background execution on the main loop.
    log.debug("delegate_to_specialist: agent=%s", agent_name)

    # Resolution order: module-level _caller_identity (set by AG-UI
    # handler, survives AG2 thread boundary) then ContextVars (set by
    # A2A task_executor before invoking the orchestrator tool loop).
    session_id_str = (
        _caller_identity.get("session_id")
        or agent_session_id_var.get()
    )
    thread_id = (
        _caller_identity.get("thread_id")
        or agent_thread_id_var.get()
    )

    if not session_id_str:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "NoSession",
                "message": "No session available for delegation.",
            },
        })

    body = dict(payload or {})
    if test_run_id is not None:
        body.setdefault("test_run_id", test_run_id)

    try:
        task_row = await _standalone_create_task(
            session_id=UUID(session_id_str),
            agent_name=agent_name,
            payload=body,
            test_run_id=test_run_id,
            thread_id=thread_id,
        )
    except Exception as exc:
        log.warning("delegate_to_specialist create_task error: %s", exc)
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    _pending_executions.append(task_row["task_id"])

    return json.dumps({
        "ok": True,
        "task_id": str(task_row["task_id"]),
        "session_id": str(task_row["session_id"]),
        "agent_name": task_row["agent_name"],
        "status": task_row["status"],
        "thread_id": thread_id,
        "submitted_at": task_row["submitted_at"],
    })


async def _delegate_via_agui(
    agent_name: str,
    payload: Optional[dict],
    test_run_id: Optional[str],
) -> str:
    """HTTP delegation to the AG-UI server for Web UI callers.

    POSTs to ``/api/delegate`` on the AG-UI server. Identity and
    thread context travel as HTTP headers (``X-PerfPilot-Token``,
    ``X-Thread-Id``, ``X-Session-Id``), resolved by
    ``SessionMiddleware`` on the receiving end. No ContextVars,
    no direct DB access, no cross-thread hacks.
    """
    import httpx

    base = _agui_base_url()
    url = f"{base}/api/delegate"
    headers = _agent_outbound_headers()
    body: dict = {"agent_name": agent_name}
    inner_payload = dict(payload or {})
    if test_run_id is not None:
        inner_payload.setdefault("test_run_id", test_run_id)
        body["test_run_id"] = test_run_id
    body["payload"] = inner_payload

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body, headers=headers)
    except Exception as exc:
        log.warning("delegate_to_specialist AG-UI error (%s): %s", url, exc)
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    if response.status_code >= 400:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "HTTPError",
                "status_code": response.status_code,
                "message": response.text[:500],
            },
        })
    body_out = response.json() if response.content else {}
    body_out["ok"] = True
    return json.dumps(body_out)


async def _delegate_via_a2a(
    agent_name: str,
    payload: Optional[dict],
    test_run_id: Optional[str],
) -> str:
    """HTTP delegation to the A2A server for external framework callers."""
    import httpx

    base = _a2a_base_url()
    url = f"{base}/agents/{agent_name}/tasks/send"
    headers = _agent_outbound_headers()
    body = dict(payload or {})
    if test_run_id is not None:
        body.setdefault("test_run_id", test_run_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body, headers=headers)
    except Exception as exc:
        log.warning("delegate_to_specialist HTTP error (%s): %s", url, exc)
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    if response.status_code >= 400:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "HTTPError",
                "status_code": response.status_code,
                "message": response.text[:500],
            },
        })
    body_out = response.json() if response.content else {}
    body_out["ok"] = True
    return json.dumps(body_out)


# =============================================================================
# Tool 3 (PBI 3.7.5): check_task_status
# =============================================================================

async def check_task_status(
    agent_name: Annotated[str, "Specialist agent that owns the task."],
    task_id: Annotated[str, "UUID of a previously-delegated task."],
) -> str:
    """GET the current status of a previously-delegated task.

    Dual routing: mirrors ``delegate_to_specialist``.

      - **Web UI**: direct ``task_store.get_task()`` (no HTTP hop).
      - **A2A**: async HTTP GET to
        ``/agents/{name}/tasks/{id}`` on the A2A surface.

    Returns:
        On success: the full task dict (``task_id``, ``status``,
            ``result``, ``error``, ``submitted_at``, ``started_at``,
            ``completed_at``, ...), augmented with ``ok: True``.
        On failure: {"ok": False, "error": {"type": "<...>", "message":
            "<...>"}}.

    The tool never raises -- the LLM gets a structured error dict.
    """
    log.debug("check_task_status: agent=%s task=%s", agent_name, task_id)

    try:
        task_row = await _standalone_get_task(UUID(task_id))
    except Exception as exc:
        log.warning("check_task_status error: %s", exc)
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    if task_row is None:
        return json.dumps({
            "ok": False,
            "error": {"type": "NotFound", "message": f"Task {task_id} not found."},
        })

    return json.dumps({
        "ok": True,
        "task_id": str(task_row["task_id"]),
        "session_id": str(task_row["session_id"]),
        "agent_name": task_row["agent_name"],
        "status": task_row["status"],
        "test_run_id": task_row.get("test_run_id"),
        "result": task_row.get("result"),
        "error": task_row.get("error"),
        "submitted_at": task_row.get("submitted_at"),
        "started_at": task_row.get("started_at"),
        "completed_at": task_row.get("completed_at"),
    })


async def _check_task_status_agui(task_id: str) -> str:
    """HTTP task status check via the AG-UI server for Web UI callers.

    GETs ``/api/tasks/{task_id}`` on the AG-UI server. Identity
    travels via ``X-PerfPilot-Token`` header; ``SessionMiddleware``
    resolves the user and enforces owner-filtering.
    """
    import httpx

    base = _agui_base_url()
    url = f"{base}/api/tasks/{task_id}"
    headers = _agent_outbound_headers()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
    except Exception as exc:
        log.warning("check_task_status AG-UI error (%s): %s", url, exc)
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    if response.status_code >= 400:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "HTTPError",
                "status_code": response.status_code,
                "message": response.text[:500],
            },
        })
    body_out = response.json() if response.content else {}
    body_out["ok"] = True
    return json.dumps(body_out)


async def _check_task_status_a2a(agent_name: str, task_id: str) -> str:
    """HTTP task status check via the A2A server."""
    import httpx

    base = _a2a_base_url()
    url = f"{base}/agents/{agent_name}/tasks/{task_id}"
    headers = _agent_outbound_headers()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
    except Exception as exc:
        log.warning("check_task_status HTTP error: %s", exc)
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    if response.status_code == 404:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "NotFound",
                "status_code": 404,
                "message": f"Task {task_id} not found for agent {agent_name}.",
            },
        })
    if response.status_code >= 400:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "HTTPError",
                "status_code": response.status_code,
                "message": response.text[:500],
            },
        })
    body = response.json() if response.content else {}
    body["ok"] = True
    return json.dumps(body)


# =============================================================================
# Tool 4 (PBI 3.7.6): request_human_approval
# =============================================================================

async def request_human_approval(
    prompt_payload: Annotated[
        dict,
        "Structured prompt for the UI: {title, summary, artifact, ...}.",
    ],
    task_id: Annotated[
        str,
        "UUID of the agent_tasks row this approval is associated with.",
    ],
    poll_interval_seconds: Annotated[
        float,
        "Seconds between hitl_approvals polls. Default 2.0.",
    ] = DEFAULT_HITL_POLL_INTERVAL_SECONDS,
    timeout_seconds: Annotated[
        float,
        "Maximum seconds to wait for a decision. Default 300.0 (5 min).",
    ] = DEFAULT_HITL_TIMEOUT_SECONDS,
) -> str:
    """Open a HITL approval prompt and block until the human decides.

    Inserts a row in `hitl_approvals` via `utils.hitl_store.create_prompt`,
    then polls every `poll_interval_seconds` (default 2s) for a terminal
    decision. The UI is notified via the existing AG-UI SSE plumbing -- no
    push needed from here.

    Returns:
        {
            "ok": True,
            "approval_id": <int>,
            "decision": "approved" | "rejected" | "timeout",
            "feedback": <str or None>,
            "decided_by": <str or None>,
            "timed_out": <bool>,
        }

    On error (invalid task_id, DB failure):
        {"ok": False, "error": {"type": "<...>", "message": "<...>"}}.
    """
    from utils import hitl_store

    try:
        task_uuid = UUID(task_id)
    except (TypeError, ValueError) as exc:
        return json.dumps({
            "ok": False,
            "error": {
                "type": "ValueError",
                "message": f"task_id is not a valid UUID: {exc}",
            },
        })

    try:
        approval = await hitl_store.create_prompt(task_uuid, dict(prompt_payload or {}))
    except Exception as exc:
        log.exception("request_human_approval: create_prompt failed")
        return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

    deadline = asyncio.get_event_loop().time() + max(0.0, timeout_seconds)
    poll_interval = max(0.1, float(poll_interval_seconds))

    log.info(
        "request_human_approval: blocking on approval_id=%d (task_id=%s, timeout=%.1fs)",
        approval.id, task_id, timeout_seconds,
    )

    while True:
        try:
            current = await hitl_store.get_approval(approval.id)
        except Exception as exc:
            log.exception("request_human_approval: get_approval failed")
            return json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})

        if current is None:
            return json.dumps({
                "ok": False,
                "error": {
                    "type": "RowMissing",
                    "message": f"hitl_approvals row {approval.id} disappeared during poll.",
                },
            })

        if current.decision in ("approved", "rejected"):
            return json.dumps({
                "ok": True,
                "approval_id": current.id,
                "decision": current.decision,
                "feedback": current.feedback,
                "decided_by": current.decided_by,
                "timed_out": False,
            })

        if asyncio.get_event_loop().time() >= deadline:
            log.warning(
                "request_human_approval: timed out waiting on approval_id=%d after %.1fs",
                approval.id, timeout_seconds,
            )
            return json.dumps({
                "ok": True,
                "approval_id": current.id,
                "decision": "timeout",
                "feedback": None,
                "decided_by": None,
                "timed_out": True,
            })

        await asyncio.sleep(poll_interval)


# =============================================================================
# Internal helpers
# =============================================================================

def _load_system_message() -> str:
    """Read `INSTRUCTIONS.md` as the AG2 `system_message`."""
    if not INSTRUCTIONS_PATH.exists():
        raise FileNotFoundError(
            f"Orchestrator INSTRUCTIONS.md not found at {INSTRUCTIONS_PATH}."
        )
    return INSTRUCTIONS_PATH.read_text(encoding="utf-8-sig")


def _resolve_provider_config() -> dict:
    """Return the merged LLM-provider config for the orchestrator.

    Per-agent overrides win over the global default in
    `config/agents.yaml -> default_llm_provider:`. Env credentials are
    merged in by `utils.llm_provider.merge_env_credentials` regardless
    of which YAML block sourced the behavior keys.
    """
    from utils.llm_provider import (
        load_default_provider_config,
        merge_env_credentials,
    )

    agent_block = _load_per_agent_llm_block()
    if agent_block:
        log.debug(
            "Orchestrator using per-agent llm_provider override from %s",
            _resolved_config_filename(),
        )
        return merge_env_credentials(agent_block)

    log.debug("Orchestrator using default_llm_provider from agents.yaml")
    return load_default_provider_config()


def _load_per_agent_llm_block() -> Optional[dict]:
    """Parse the resolved per-agent config and return its `llm_provider:` block.

    Returns None when neither candidate config file exists, the YAML is
    empty, or the `llm_provider:` key is absent / commented out.
    """
    from utils.base_agent import resolve_agent_config_path

    config_path = resolve_agent_config_path(AGENT_DIR)
    if config_path is None:
        log.warning(
            "Orchestrator config not found under %s "
            "(expected config.yaml or config.example.yaml); "
            "using default LLM provider.",
            AGENT_DIR,
        )
        return None

    import yaml

    with open(config_path, "r", encoding="utf-8-sig") as f:
        parsed = yaml.safe_load(f) or {}
    block = parsed.get("llm_provider")
    if not block or not isinstance(block, dict):
        return None
    return dict(block)


def _resolved_config_filename() -> str:
    from utils.base_agent import resolve_agent_config_path

    path = resolve_agent_config_path(AGENT_DIR)
    return path.name if path else "<none>"


def _a2a_base_url() -> str:
    """Return the local A2A surface base URL (env-overridable)."""
    return os.environ.get(A2A_BASE_URL_ENV, DEFAULT_A2A_BASE_URL).rstrip("/")


def _agui_base_url() -> str:
    """Return the local AG-UI surface base URL for Web UI delegation.

    Resolution order:
      1. ``PERFPILOT_AGUI_BASE_URL`` env var (full URL override).
      2. ``http://127.0.0.1:{AGUI_PORT}`` — reads the same ``AGUI_PORT``
         env var that ``agui_server.py`` uses so the orchestrator
         automatically targets the correct port even when 8002 is
         unavailable (e.g. occupied by another service on a work laptop).
      3. ``http://127.0.0.1:8002`` — hardcoded fallback.
    """
    explicit = os.environ.get(AGUI_BASE_URL_ENV)
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get(AGUI_PORT_ENV, DEFAULT_AGUI_PORT)
    return f"http://127.0.0.1:{port}"


def _agent_outbound_headers() -> dict:
    """Propagate orchestrator-as-caller identity headers when available.

    Resolution order (per-field):
      1. Module-level ``_caller_identity`` (survives AG2 thread boundary)
      2. ContextVar (set by A2A task executor in same thread)
      3. Environment variable (headless / CLI fallback)

    Headers are source-aware. Web UI uses ``X-Thread-Id`` /
    ``X-Session-Id``; A2A uses ``X-External-Thread-Id`` /
    ``X-External-Session-Id`` for OpenTelemetry traceability.

    ``X-PerfPilot-Token`` is always sent regardless of source.
    """
    headers: dict = {}
    source = agent_request_source_var.get()
    user_id = (
        _caller_identity.get("user_id")
        or agent_user_id_var.get()
        or os.environ.get("PERFPILOT_AGENT_USER_ID")
    )
    thread_id = (
        _caller_identity.get("thread_id")
        or agent_thread_id_var.get()
        or os.environ.get("PERFPILOT_AGENT_THREAD_ID")
    )
    session_id = (
        _caller_identity.get("session_id")
        or agent_session_id_var.get()
    )

    if user_id:
        headers["X-PerfPilot-Token"] = user_id

    # Default to web_ui when source is None (AG2 thread isolation)
    effective_source = source if source else ("web_ui" if session_id else None)

    if effective_source == "web_ui":
        if thread_id:
            headers["X-Thread-Id"] = thread_id
        if session_id:
            headers["X-Session-Id"] = session_id
    else:
        if thread_id:
            headers["X-External-Thread-Id"] = thread_id
        external_session_id = (
            agent_external_session_id_var.get()
            or os.environ.get("PERFPILOT_AGENT_EXTERNAL_SESSION_ID")
        )
        if external_session_id:
            headers["X-External-Session-Id"] = external_session_id
    return headers
