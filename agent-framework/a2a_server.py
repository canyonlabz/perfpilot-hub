"""A2A FastAPI server for PerfPilot Agents (V2 doc Section 9, port 8001).

One ASGI app exposing all seven agents through path-based routing under
`/agents/{name}/...`. Endpoints follow the A2A protocol exactly (no
PerfPilot branding) so off-the-shelf A2A clients integrate without
adapters. Branding lives only on port 8002 (the AG-UI bridge, F3.6).

Legacy PerfPilot endpoints (V2 Section 9.2):

    GET  /health                                            - liveness probe
    GET  /agents                                            - list discoverable agents
    GET  /agents/{name}/.well-known/agent.json              - agent card (RFC 8615)
    POST /agents/{name}/tasks/send                          - submit task (poll/webhook)
    POST /agents/{name}/tasks/sendSubscribe                 - submit task + SSE stream
    GET  /agents/{name}/tasks/{task_id}                     - poll task state
    POST /agents/{name}/tasks/{task_id}/cancel              - cancel a running task

A2A v1.0.0 HTTP+JSON/REST binding (Spec Section 11):

    GET  /.well-known/agent-card.json                       - orchestrator card (A2A discovery)
    POST /message:send                                      - send message (returns Task)
    POST /message:stream                                    - send message + SSE stream
    GET  /tasks/{task_id}                                   - get task by ID
    POST /tasks/{task_id}:cancel                            - cancel task

Long-running task model (V2 Section 14):

    Pattern 1 - Polling: caller submits via tasks/send, polls GET tasks/{id}.
    Pattern 2 - SSE:     caller submits via tasks/sendSubscribe, holds open
                         the SSE stream until the task reaches terminal state.
    Pattern 3 - Webhook: caller submits with `subscriber_endpoints`; server
                         POSTs the final body to each URL on completion.

Run locally:

    cd agent-framework
    python a2a_server.py

Or via uvicorn from the agent-framework folder:

    uvicorn a2a_server:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

if __package__ is None:
    # Allow `python a2a_server.py` from inside agent-framework/.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from utils import agents_config, base_agent, db, task_executor, task_store, thread_store
from utils.session_middleware import SessionMiddleware

log = logging.getLogger(__name__)

FRAMEWORK_DIR = Path(__file__).resolve().parent
AGENTS_DIR = FRAMEWORK_DIR / "agents"
SERVER_VERSION = "0.1.0"
SERVER_TITLE = "PerfPilot Agents - A2A Surface"

# Load .env at module scope so environment variables (LLM credentials, ports,
# TLS paths) are available when create_app() runs at import time — before
# main() has a chance to call load_dotenv().
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(FRAMEWORK_DIR / ".env", override=False)
    del _load_dotenv
except ImportError:
    pass

# PBI 3.7.8 / Decision 17: when an A2A caller omits `X-External-Thread-Id`,
# auto-mint one per (external_session_id) and cache it so all requests in
# the same upstream session share the same thread. Bounded LRU-ish dict
# kept small because A2A external sessions are short-lived. Epic 4 swaps
# this for a Redis / Postgres-LISTEN cache when multi-process deployment
# arrives.
_AUTO_MINT_CACHE: dict[str, str] = {}
_AUTO_MINT_CACHE_MAX = 1024
_AUTO_MINT_LOCK = asyncio.Lock()


# =============================================================================
# App lifespan
# =============================================================================

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Open the asyncpg pool eagerly so the first request is fast."""
    log.info("A2A server starting; warming asyncpg pool")
    try:
        await db.get_pool()
        log.info("A2A server ready (port=%s)", os.environ.get("A2A_PORT", "8001"))
    except Exception:
        log.exception("Failed to warm asyncpg pool at startup; routes will retry per request")
    try:
        yield
    finally:
        log.info("A2A server shutting down; closing asyncpg pool")
        await db.close_pool()


def create_app() -> FastAPI:
    """Build the FastAPI app. Factored out so tests can mount it in-process."""
    app = FastAPI(
        title=SERVER_TITLE,
        version=SERVER_VERSION,
        description="A2A protocol surface for PerfPilot Agents. See V2 doc Section 9.",
        lifespan=_lifespan,
    )
    app.add_middleware(SessionMiddleware, default_source="a2a_external")

    _register_routes(app)
    _register_a2a_v1_routes(app)
    return app


# =============================================================================
# Route registration
# =============================================================================

def _register_routes(app: FastAPI) -> None:

    # -- Liveness ----------------------------------------------------------------
    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "service": "a2a", "version": SERVER_VERSION}

    # -- Discovery ---------------------------------------------------------------
    @app.get("/agents", tags=["discovery"])
    async def list_agents() -> dict:
        """Return the list of agents that are currently enabled.

        Mirrors V2 Section 9.5: only enabled agents appear in discovery.
        Disabled agents return 404 on their well-known card path.
        """
        names = agents_config.list_enabled_agents(FRAMEWORK_DIR)
        return {
            "agents": [
                {
                    "name": name,
                    "agent_card_url": f"/agents/{name}/.well-known/agent.json",
                    "tasks_send_url": f"/agents/{name}/tasks/send",
                }
                for name in names
            ],
            "known_agents": list(agents_config.KNOWN_AGENTS),
        }

    @app.get("/agents/{agent_name}/.well-known/agent.json", tags=["discovery"])
    async def agent_card(agent_name: str) -> JSONResponse:
        if not agents_config.is_agent_enabled(agent_name, FRAMEWORK_DIR):
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' is not enabled or unknown.")

        # F3.7+ ships real cards; until then `read_agent_card` synthesizes a
        # truthful stub per V2 §7.4 / §9.5 ("skills:[] + status:'stub'").
        # Helper lives in `utils.base_agent` so the orchestrator's
        # `list_available_specialists()` tool shares the same code path.
        card = base_agent.read_agent_card(
            AGENTS_DIR / agent_name,
            fallback_framework_version=SERVER_VERSION,
        )
        return JSONResponse(card)

    # -- Task endpoints ----------------------------------------------------------
    @app.post("/agents/{agent_name}/tasks/send", status_code=202, tags=["tasks"])
    async def tasks_send(agent_name: str, request: Request) -> JSONResponse:
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)
        body = await _read_json_body(request)

        thread = await _resolve_a2a_thread(request, agent_name)
        # Stamp the resolved thread into the payload so `task_executor.
        # _run_orchestrator` can load conversation history and persist
        # the new turns. The `_` prefix keeps it out of the way of
        # any caller-supplied keys.
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }

        task = await task_store.create_task(
            session_id=session_id,
            external_session_id=getattr(request.state, "external_session_id", None),
            agent_name=agent_name,
            payload=body,
            test_run_id=body.get("test_run_id"),
            thread_id=thread.thread_id,
            subscriber_endpoints=_extract_subscriber_endpoints(body),
        )
        # Fire-and-forget background execution.
        asyncio.create_task(task_executor.execute_task(task.task_id))

        return JSONResponse(
            content={
                "task_id": str(task.task_id),
                "session_id": str(task.session_id),
                "agent_name": task.agent_name,
                "status": task.status,
                "thread_id": thread.thread_id,
                "external_thread_id": thread.external_thread_id,
                "submitted_at": task.submitted_at.isoformat(),
            },
            status_code=202,
            headers=_thread_response_headers(thread),
        )

    @app.post("/agents/{agent_name}/tasks/sendSubscribe", tags=["tasks"])
    async def tasks_send_subscribe(agent_name: str, request: Request) -> EventSourceResponse:
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)
        body = await _read_json_body(request)

        thread = await _resolve_a2a_thread(request, agent_name)
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }

        task = await task_store.create_task(
            session_id=session_id,
            external_session_id=getattr(request.state, "external_session_id", None),
            agent_name=agent_name,
            payload=body,
            test_run_id=body.get("test_run_id"),
            thread_id=thread.thread_id,
            subscriber_endpoints=_extract_subscriber_endpoints(body),
        )
        queue = await task_executor.subscribe(task.task_id)
        asyncio.create_task(task_executor.execute_task(task.task_id))

        async def _stream():
            # Emit an initial snapshot so consumers immediately know the task_id.
            yield {
                "event": "snapshot",
                "data": json.dumps({
                    "task_id": str(task.task_id),
                    "session_id": str(task.session_id),
                    "agent_name": task.agent_name,
                    "status": task.status,
                    "submitted_at": task.submitted_at.isoformat(),
                }),
            }
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        # Heartbeat keeps proxies from killing the connection.
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {"event": "state", "data": event.to_sse_data()}
                    if event.status in task_store.TERMINAL_STATUSES:
                        break
            finally:
                await task_executor.unsubscribe(task.task_id, queue)

        return EventSourceResponse(_stream(), headers=_thread_response_headers(thread))

    @app.get("/agents/{agent_name}/tasks/{task_id}", tags=["tasks"])
    async def tasks_get(agent_name: str, task_id: str, request: Request) -> dict:
        await _require_enabled_agent(agent_name)
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        task = await task_store.get_task(task_uuid)
        if task is None or task.agent_name != agent_name:
            raise HTTPException(status_code=404, detail="Task not found for this agent")

        return _task_to_dict(task)

    @app.post("/agents/{agent_name}/tasks/{task_id}/cancel", tags=["tasks"])
    async def tasks_cancel(agent_name: str, task_id: str) -> dict:
        await _require_enabled_agent(agent_name)
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        task = await task_store.get_task(task_uuid)
        if task is None or task.agent_name != agent_name:
            raise HTTPException(status_code=404, detail="Task not found for this agent")

        changed = await task_store.mark_cancelled(task_uuid, reason="cancelled via A2A")
        return {"task_id": task_id, "cancelled": changed, "previous_status": task.status}


# =============================================================================
# Helpers
# =============================================================================

async def _require_enabled_agent(agent_name: str) -> None:
    if not agents_config.is_agent_enabled(agent_name, FRAMEWORK_DIR):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' is not enabled or unknown.")


def _require_session(request: Request) -> UUID:
    session_id = getattr(request.state, "session_id", None)
    if session_id is None:
        # SessionMiddleware should always provide one; if it could not (DB
        # outage), refuse to mint tasks rather than orphan them.
        raise HTTPException(
            status_code=503,
            detail="Session could not be established (perfagent_state unavailable).",
        )
    return session_id


async def _read_json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    return body


def _extract_subscriber_endpoints(body: dict) -> list[str]:
    """Locate `subscriber_endpoints` in either the top-level body or a `callbacks` block."""
    callbacks = body.get("callbacks") or {}
    raw = body.get("subscriber_endpoints") or callbacks.get("subscriber_endpoints") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
    # Also accept the singular `webhook_url` from the V2 doc example payload.
    webhook = callbacks.get("webhook_url") or body.get("webhook_url")
    if isinstance(webhook, str) and webhook.strip() and webhook.strip() not in out:
        out.append(webhook.strip())
    return out


async def _resolve_a2a_thread(request: Request, agent_name: str) -> thread_store.AgentThread:
    """Implement Decision 17 thread resolution for an A2A request.

    Precedence:
      1. `X-External-Thread-Id` header present -> idempotent lookup;
         create the thread on first sight, reuse on subsequent calls.
      2. `external_session_id` already has a cached auto-minted label
         from an earlier call in this same upstream session -> reuse it
         (lookup, possibly create if the row was reaped).
      3. Otherwise -> auto-mint a fresh label, cache by
         `external_session_id` (so subsequent calls inside the session
         all land on the same thread), insert the row.

    Returns the materialized `AgentThread`. The caller stamps
    `thread.thread_id` + `thread.external_thread_id` into both the task
    payload (for the executor) and the response headers (for the
    upstream caller's resumption).
    """
    explicit_label = request.headers.get("X-External-Thread-Id") or request.headers.get("x-external-thread-id")
    external_session_id = getattr(request.state, "external_session_id", None)
    internal_session_id = getattr(request.state, "session_id", None)
    # The cache key is whatever session-equivalent we have available --
    # external session label preferred (so different A2A peers in the
    # same upstream "session" all share the thread) but internal
    # session_id is a fine fallback when the caller did not supply an
    # external label (which is the common case for IDE / CLI clients
    # and for the smoke test).
    cache_key: Optional[str] = external_session_id or (
        str(internal_session_id) if internal_session_id else None
    )

    if explicit_label:
        return await _lookup_or_create_a2a_thread(
            external_thread_id=explicit_label.strip(),
            agent_name=agent_name,
            external_session_id=external_session_id,
        )

    if cache_key:
        async with _AUTO_MINT_LOCK:
            cached_label = _AUTO_MINT_CACHE.get(cache_key)
        if cached_label:
            return await _lookup_or_create_a2a_thread(
                external_thread_id=cached_label,
                agent_name=agent_name,
                external_session_id=external_session_id,
            )

    minted_label = _mint_external_thread_label(agent_name)
    if cache_key:
        async with _AUTO_MINT_LOCK:
            # Bound the cache: drop the oldest entry when we hit the cap.
            if len(_AUTO_MINT_CACHE) >= _AUTO_MINT_CACHE_MAX:
                _AUTO_MINT_CACHE.pop(next(iter(_AUTO_MINT_CACHE)), None)
            _AUTO_MINT_CACHE[cache_key] = minted_label

    return await _lookup_or_create_a2a_thread(
        external_thread_id=minted_label,
        agent_name=agent_name,
        external_session_id=external_session_id,
    )


async def _lookup_or_create_a2a_thread(
    *,
    external_thread_id: str,
    agent_name: str,
    external_session_id: Optional[str],
) -> thread_store.AgentThread:
    """Fetch the thread for `external_thread_id`, creating it if absent."""
    existing = await thread_store.get_by_external_thread_id(external_thread_id)
    if existing is not None:
        return existing
    return await thread_store.create_thread(
        source="a2a_external",
        external_thread_id=external_thread_id,
        title=f"A2A thread for {agent_name}",
        metadata={
            "created_by": "a2a_server._resolve_a2a_thread",
            "agent_name": agent_name,
            "external_session_id": external_session_id,
        },
    )


def _mint_external_thread_label(agent_name: str) -> str:
    """Server-side label for an A2A thread that did not bring its own.

    Format: `auto-<agent>-<uuid>`. The `auto-` prefix lets operators
    distinguish self-minted threads from caller-supplied labels in logs
    and the sidebar.
    """
    return f"auto-{agent_name}-{uuid.uuid4().hex[:16]}"


def _thread_response_headers(thread: thread_store.AgentThread) -> dict:
    """Headers exposed on every task-creating endpoint per Decision 17.

    Upstream callers persist `X-Thread-Id` (internal id, opaque to them)
    and `X-External-Thread-Id` (their canonical label, identical to
    whatever they sent in or the auto-minted fallback). Either header
    is sufficient for resumption on the next request.
    """
    headers: dict = {"X-Thread-Id": thread.thread_id}
    if thread.external_thread_id:
        headers["X-External-Thread-Id"] = thread.external_thread_id
    return headers


def _task_to_dict(task: task_store.AgentTask) -> dict:
    return {
        "task_id": str(task.task_id),
        "session_id": str(task.session_id) if task.session_id else None,
        "external_session_id": task.external_session_id,
        "agent_name": task.agent_name,
        "status": task.status,
        "test_run_id": task.test_run_id,
        "result": task.result,
        "error": task.error,
        "subscriber_endpoints": task.subscriber_endpoints,
        "submitted_at": task.submitted_at.isoformat() if task.submitted_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


# =============================================================================
# A2A v1.0.0 HTTP+JSON/REST Binding (Spec Section 11)
# =============================================================================
# New routes that follow A2A v1 path conventions. All routes implicitly
# target the orchestrator agent. Legacy routes above remain unchanged.
# Both route sets share the same internal helpers (task_store,
# task_executor, _resolve_a2a_thread, etc.).

A2A_V1_AGENT_NAME = "orchestrator"
A2A_VERSION_HEADER = "1.0"


def _a2a_v1_response_headers(
    thread: Optional[thread_store.AgentThread] = None,
) -> dict:
    """Build response headers for A2A v1 routes.

    Always includes ``A2A-Version: 1.0``. Thread headers are added when
    a thread is available (task-creating endpoints).
    """
    headers: dict = {"A2A-Version": A2A_VERSION_HEADER}
    if thread is not None:
        headers.update(_thread_response_headers(thread))
    return headers


def _normalize_a2a_v1_body(body: dict) -> dict:
    """Translate an A2A v1 ``SendMessageRequest`` envelope into the internal
    payload format that ``task_executor._extract_user_message_from_payload()``
    and ``a2a_parts_parser`` already understand.

    If the body is already a legacy PerfPilot payload (top-level ``message``
    string, ``text``, ``prompt``, or ``parts[]``), it is returned unchanged.

    A2A v1 structure::

        {
          "message": {
            "messageId": "...",
            "role": "ROLE_USER",
            "parts": [{"text": "Hello"}],
            "contextId": "ctx-001"
          },
          "configuration": {...},
          "metadata": {...}
        }

    Normalized internal structure::

        {
          "message": "Hello",          # first text Part -> top-level message
          "parts": [{"text": "Hello"}], # pass through for a2a_parts_parser
          "metadata": {...},            # merged from envelope + message metadata
          "_a2a_v1_envelope": {...},    # stash original envelope for audit
          "_a2a_v1_context_id": "ctx-001"
        }
    """
    from utils.a2a_models import is_a2a_v1_request

    if not is_a2a_v1_request(body):
        return body

    msg = body["message"]
    parts = msg.get("parts") or []
    context_id = msg.get("contextId") or msg.get("context_id")

    first_text = None
    normalized_parts: list[dict] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        normalized_parts.append(part)
        if first_text is None and isinstance(part.get("text"), str):
            first_text = part["text"]

    result: dict = {}

    if first_text:
        result["message"] = first_text

    if normalized_parts:
        result["parts"] = normalized_parts

    envelope_metadata = body.get("metadata")
    msg_metadata = msg.get("metadata")
    if isinstance(envelope_metadata, dict) or isinstance(msg_metadata, dict):
        merged: dict = {}
        if isinstance(msg_metadata, dict):
            merged.update(msg_metadata)
        if isinstance(envelope_metadata, dict):
            merged.update(envelope_metadata)
        if merged:
            result["metadata"] = merged

    if context_id:
        result["_a2a_v1_context_id"] = context_id

    result["_a2a_v1_envelope"] = body

    config = body.get("configuration")
    if isinstance(config, dict):
        result["_a2a_v1_configuration"] = config

    test_run_id = None
    if isinstance(envelope_metadata, dict):
        test_run_id = envelope_metadata.get("test_run_id")
    if test_run_id and isinstance(test_run_id, str):
        result["test_run_id"] = test_run_id

    return result


def _task_to_a2a_v1(task: task_store.AgentTask) -> dict:
    """Convert an ``AgentTask`` DB row to an A2A v1 ``Task`` response dict.

    Returns a camelCase dict suitable for JSON serialization. Uses the
    Phase 1 Pydantic models for structure and the state mapping for
    PerfPilot-to-A2A status translation.
    """
    from utils.a2a_models import (
        Artifact,
        Part,
        Task,
        TaskStatus,
        a2a_timestamp,
        perfpilot_status_to_a2a,
    )

    a2a_state = perfpilot_status_to_a2a(task.status)

    status_timestamp = a2a_timestamp()
    if task.completed_at:
        status_timestamp = task.completed_at.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{task.completed_at.microsecond // 1000:03d}Z"
    elif task.started_at:
        status_timestamp = task.started_at.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{task.started_at.microsecond // 1000:03d}Z"
    elif task.submitted_at:
        status_timestamp = task.submitted_at.strftime("%Y-%m-%dT%H:%M:%S.") + \
            f"{task.submitted_at.microsecond // 1000:03d}Z"

    task_status = TaskStatus(state=a2a_state, timestamp=status_timestamp)

    context_id = None
    if isinstance(task.payload, dict):
        context_id = task.payload.get("_a2a_v1_context_id")

    artifacts = None
    if task.result and isinstance(task.result, dict):
        reply_text = task.result.get("reply_text")
        if isinstance(reply_text, str) and reply_text.strip():
            artifacts = [
                Artifact(
                    artifact_id=f"{task.task_id}-result",
                    parts=[Part(text=reply_text)],
                    name="Agent response",
                )
            ]

    metadata: dict[str, Any] = {
        "agentName": task.agent_name,
    }
    if task.test_run_id:
        metadata["testRunId"] = task.test_run_id
    if task.error and isinstance(task.error, dict):
        metadata["error"] = task.error

    a2a_task = Task(
        id=str(task.task_id),
        context_id=context_id,
        status=task_status,
        artifacts=artifacts,
        metadata=metadata if metadata else None,
    )

    return a2a_task.model_dump(by_alias=True, exclude_none=True)


def _task_event_to_a2a_v1_sse(event: task_executor.TaskEvent) -> dict:
    """Convert a ``TaskEvent`` to an A2A v1 SSE ``data:`` payload.

    Returns a dict suitable for ``json.dumps()`` as an SSE data field,
    shaped as a ``StreamResponse`` with a ``statusUpdate``.
    """
    from utils.a2a_models import TaskStatus, a2a_timestamp, perfpilot_status_to_a2a

    a2a_state = perfpilot_status_to_a2a(event.status)
    status = TaskStatus(state=a2a_state, timestamp=a2a_timestamp())

    return {
        "statusUpdate": status.model_dump(by_alias=True, exclude_none=True),
        "taskId": event.task_id,
        "progress": event.progress,
    }


def _register_a2a_v1_routes(app: FastAPI) -> None:
    """Register A2A v1.0.0 HTTP+JSON/REST binding routes.

    All routes implicitly target the orchestrator agent. These are
    additive alongside the existing legacy ``/agents/{name}/...`` routes.
    """

    # -- Discovery (A2A v1) --------------------------------------------------
    @app.get("/.well-known/agent-card.json", tags=["a2a-v1", "discovery"])
    async def a2a_v1_agent_card() -> JSONResponse:
        """Return the orchestrator's agent card at the A2A v1 discovery path.

        A2A spec Section 8: public agent card at
        ``/.well-known/agent-card.json``.
        """
        if not agents_config.is_agent_enabled(A2A_V1_AGENT_NAME, FRAMEWORK_DIR):
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{A2A_V1_AGENT_NAME}' is not enabled.",
            )
        card = base_agent.read_agent_card(
            AGENTS_DIR / A2A_V1_AGENT_NAME,
            fallback_framework_version=SERVER_VERSION,
        )
        return JSONResponse(card, headers=_a2a_v1_response_headers())

    # -- SendMessage (A2A v1 Section 11) -------------------------------------
    @app.post("/message:send", status_code=202, tags=["a2a-v1", "tasks"])
    async def a2a_v1_send_message(request: Request) -> JSONResponse:
        """Submit a message to the orchestrator (A2A v1 REST binding).

        Accepts both A2A v1 ``SendMessageRequest`` envelopes and legacy
        PerfPilot payloads. Returns an A2A v1 ``Task`` object.
        """
        agent_name = A2A_V1_AGENT_NAME
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)
        body = await _read_json_body(request)
        body = _normalize_a2a_v1_body(body)

        thread = await _resolve_a2a_thread(request, agent_name)
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }

        task = await task_store.create_task(
            session_id=session_id,
            external_session_id=getattr(request.state, "external_session_id", None),
            agent_name=agent_name,
            payload=body,
            test_run_id=body.get("test_run_id"),
            thread_id=thread.thread_id,
            subscriber_endpoints=_extract_subscriber_endpoints(body),
        )
        asyncio.create_task(task_executor.execute_task(task.task_id))

        return JSONResponse(
            content=_task_to_a2a_v1(task),
            status_code=202,
            headers=_a2a_v1_response_headers(thread),
        )

    # -- SendStreamingMessage (A2A v1 Section 11) ----------------------------
    @app.post("/message:stream", tags=["a2a-v1", "tasks"])
    async def a2a_v1_stream_message(request: Request) -> EventSourceResponse:
        """Submit a message and receive an SSE stream (A2A v1 REST binding).

        Returns ``StreamResponse`` events with camelCase A2A v1 shapes.
        """
        agent_name = A2A_V1_AGENT_NAME
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)
        body = await _read_json_body(request)
        body = _normalize_a2a_v1_body(body)

        thread = await _resolve_a2a_thread(request, agent_name)
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }

        task = await task_store.create_task(
            session_id=session_id,
            external_session_id=getattr(request.state, "external_session_id", None),
            agent_name=agent_name,
            payload=body,
            test_run_id=body.get("test_run_id"),
            thread_id=thread.thread_id,
            subscriber_endpoints=_extract_subscriber_endpoints(body),
        )
        queue = await task_executor.subscribe(task.task_id)
        asyncio.create_task(task_executor.execute_task(task.task_id))

        async def _stream():
            yield {
                "event": "task",
                "data": json.dumps(_task_to_a2a_v1(task)),
            }
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": "status",
                        "data": json.dumps(_task_event_to_a2a_v1_sse(event)),
                    }
                    if event.status in task_store.TERMINAL_STATUSES:
                        break
            finally:
                await task_executor.unsubscribe(task.task_id, queue)

        return EventSourceResponse(
            _stream(),
            headers=_a2a_v1_response_headers(thread),
        )

    # -- GetTask (A2A v1 Section 11) -----------------------------------------
    @app.get("/tasks/{task_id}", tags=["a2a-v1", "tasks"])
    async def a2a_v1_get_task(task_id: str, request: Request) -> JSONResponse:
        """Retrieve task status by ID (A2A v1 REST binding).

        No ``agent_name`` in the path — looks up by ``task_id`` only.
        """
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        task = await task_store.get_task(task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        return JSONResponse(
            content=_task_to_a2a_v1(task),
            headers=_a2a_v1_response_headers(),
        )

    # -- CancelTask (A2A v1 Section 11) --------------------------------------
    @app.post("/tasks/{task_id}:cancel", tags=["a2a-v1", "tasks"])
    async def a2a_v1_cancel_task(task_id: str, request: Request) -> JSONResponse:
        """Cancel a running task (A2A v1 REST binding)."""
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        task = await task_store.get_task(task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        await task_store.mark_cancelled(task_uuid, reason="cancelled via A2A v1")

        refreshed = await task_store.get_task(task_uuid)
        if refreshed is None:
            refreshed = task

        return JSONResponse(
            content=_task_to_a2a_v1(refreshed),
            headers=_a2a_v1_response_headers(),
        )


# =============================================================================
# ASGI entrypoint
# =============================================================================

app = create_app()


def _resolve_port() -> int:
    raw = os.environ.get("A2A_PORT", "8001")
    try:
        return int(raw)
    except ValueError:
        log.warning("A2A_PORT=%r is not an int; falling back to 8001", raw)
        return 8001


def main() -> None:
    """`python a2a_server.py` entrypoint. Loads .env and runs uvicorn."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        from dotenv import load_dotenv
        load_dotenv(FRAMEWORK_DIR / ".env", override=False)
    except ImportError:
        log.debug("python-dotenv not installed; relying on shell env only")

    import uvicorn
    uvicorn.run(
        "a2a_server:app",
        host=os.environ.get("A2A_HOST", "0.0.0.0"),
        port=_resolve_port(),
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
