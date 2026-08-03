"""AG-UI / CopilotKit bridge for PerfPilot Agents (V2 doc Section 10, port 8002).

A separate FastAPI ASGI app from the A2A surface (port 8001). It exists to
support the human-driven HITL experience through a browser-based React UI
built on CopilotKit. The two surfaces deliberately stay in different
processes so:

  - The A2A surface stays minimal and protocol-pure (no UI concerns).
  - The AG-UI surface can carry conversation, session, run, and HITL
    affordances that browsers need (cookies, SSE, CORS).

URL conventions:

  /copilotkit/                - AG-UI / CopilotKit endpoint, served by AG2's
                                 native `AGUIStream(agent).build_asgi()`.
                                 The Next.js CopilotKit React frontend points
                                 its `runtimeUrl` here. AG2 speaks AG-UI
                                 natively, so no CopilotKit Python SDK is
                                 needed in the middle. NOTE: the trailing
                                 slash is canonical (Starlette `Mount` 307s
                                 from `/copilotkit` -> `/copilotkit/`).
  /api/sessions[/{id}]        - Session info
  /api/runs[/{test_run_id}]   - Test-run grouping over agent_tasks
  /api/hitl/{approve,reject}  - HITL CRUD over hitl_approvals
  /api/events                 - AG-UI SSE event stream from task_executor
  /health                     - Liveness probe

Deliberately NOT prefixed with `/api/perfpilot/*`. The shorter `/api/*`
prefix matches industry convention; the brand stays in the front-end
(CopilotKit page title, manifest, etc.), not in the URL.

Run locally:

    cd agent-framework
    python agui_server.py

Or via uvicorn from the agent-framework folder:

    uvicorn agui_server:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from uuid import UUID

if __package__ is None:
    # Allow `python agui_server.py` from inside backend/.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncio
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel, Field

from utils import (
    agents_config,
    auth,
    conversation_store,
    db,
    hitl_store,
    session_store,
    task_executor,
    task_store,
    thread_store,
)
from utils.paths import get_framework_dir
from utils.session_middleware import SessionMiddleware

log = logging.getLogger(__name__)

FRAMEWORK_DIR = get_framework_dir()
SERVER_VERSION = "0.1.0"
SERVER_TITLE = "PerfPilot Agents - AG-UI Bridge"

# Load .env at module scope so environment variables (LLM credentials, ports,
# TLS paths) are available when create_app() → build_orchestrator() runs at
# import time — before main() has a chance to call load_dotenv().
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(FRAMEWORK_DIR / ".env", override=False)
    del _load_dotenv
except ImportError:
    pass


# =============================================================================
# App lifespan
# =============================================================================

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Warm the asyncpg pool on startup so the first browser request is fast."""
    log.info("AG-UI bridge starting; warming asyncpg pool")
    try:
        await db.get_pool()
        log.info("AG-UI bridge ready (port=%s)", os.environ.get("AGUI_PORT", "8002"))
    except Exception:
        log.exception(
            "Failed to warm asyncpg pool at startup; routes will retry per request"
        )
    try:
        yield
    finally:
        log.info("AG-UI bridge shutting down; closing asyncpg pool")
        await db.close_pool()


# =============================================================================
# CORS configuration
# =============================================================================
# CORS is permissive by default for local dev. Operators tighten via env
# vars. Epic 4 will move this to `config/agents.yaml -> cors:` and pin a
# strict origin list when running on Azure Container Apps. The Next.js
# frontend in F3.6.7 runs on its own port (3000 by default) so it MUST
# come from a different origin than this server.

def _resolve_cors_origins() -> list[str]:
    raw = os.environ.get("AGUI_CORS_ORIGINS", "").strip()
    if not raw:
        # Common local-dev defaults: Next.js dev server on 3000, Vite on 5173,
        # CRA on 3000, and the bridge itself in case anyone hits it directly.
        return [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    if raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# =============================================================================
# App factory
# =============================================================================

def create_app() -> FastAPI:
    """Build the FastAPI app. Factored out so tests can mount it in-process."""
    app = FastAPI(
        title=SERVER_TITLE,
        version=SERVER_VERSION,
        description="Human-facing surface for PerfPilot Agents (CopilotKit + AG-UI). See V2 doc Section 10.",
        lifespan=_lifespan,
    )

    # CORS first so OPTIONS preflights short-circuit before any session work.
    cors_origins = _resolve_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-Id", "X-External-Session-Id"],
    )

    # Session middleware after CORS so preflight requests are not minted as
    # sessions. Cookie tunables come from `agents.yaml -> web_ui.session_cookie`
    # via `utils.agents_config.get_session_cookie_config()`, which always
    # returns a populated config (defaults fill in any missing keys).
    cookie_cfg = agents_config.get_session_cookie_config(FRAMEWORK_DIR)
    log.info(
        "AG-UI session-cookie config: max_age_days=%d secure=%s samesite=%s",
        cookie_cfg.max_age_days, cookie_cfg.secure, cookie_cfg.samesite,
    )
    app.add_middleware(
        SessionMiddleware,
        default_source="web_ui",
        cookie_secure=cookie_cfg.secure,
        cookie_samesite=cookie_cfg.samesite,
        cookie_max_age_days=cookie_cfg.max_age_days,
    )

    _register_routes(app)
    _mount_copilotkit(app)
    return app


def _mount_copilotkit(app: FastAPI) -> None:
    """Mount the AG-UI / CopilotKit endpoint at /copilotkit (PBI 3.6.3, 3.7.7).

    Uses AG2's built-in `AGUIStream(agent).dispatch()` so CopilotKit React
    can talk to us directly via the AG-UI protocol. No CopilotKit Python
    SDK is involved -- AG2 ships AG-UI support natively.

    PBI 3.7.7 upgrades:
      - Stub orchestrator replaced with the real one
        (`agents.orchestrator.agent.build_orchestrator`).
      - DB-as-source-of-truth message history (Decision 14): the endpoint
        loads `conversation_messages WHERE thread_id = ?` and overrides
        `RunAgentInput.messages` so AG2 sees the full transcript rather
        than relying on whatever the browser sent. New user + assistant
        turns are persisted around the dispatch so a refreshed browser
        (or a new device) resumes the conversation seamlessly.
      - Owner-checked thread auto-creation: if the thread does not yet
        exist, the endpoint creates it for the current user; if it
        exists but belongs to another user, the endpoint refuses (403).

    Mount is best-effort: if the agent or AG2 import fails, the rest of
    the server still boots and the other endpoints keep working. A
    subsequent boot attempt with a valid LLM config will mount cleanly.
    """
    try:
        from autogen.ag_ui import AGUIStream  # noqa: WPS433
        from agents.orchestrator.agent import build_orchestrator  # noqa: WPS433
    except Exception:
        log.exception(
            "Could not import AG2 / AGUIStream / orchestrator; /copilotkit will be "
            "unavailable. Install with: pip install \"ag2[ag-ui]==0.13.3\""
        )
        return

    try:
        agent = build_orchestrator()
    except Exception:
        log.exception(
            "Failed to build the stub orchestrator (likely missing/invalid LLM "
            "credentials in backend/.env); /copilotkit will be unavailable."
        )
        return

    try:
        stream = AGUIStream(agent)
        endpoint_cls = _build_history_aware_copilotkit_endpoint(stream)
        app.mount("/copilotkit", endpoint_cls)
        log.info(
            "Mounted AG-UI endpoint at /copilotkit (real orchestrator, "
            "DB-loaded history per Decision 14)"
        )
    except Exception:
        log.exception("Could not mount /copilotkit; AG-UI endpoint will be unavailable")


def _build_history_aware_copilotkit_endpoint(stream: Any) -> Any:
    """Return an ASGI HTTPEndpoint that wraps AGUIStream.dispatch() with DB history.

    The endpoint reproduces the shape of AG2's
    `build_asgi(AGUIStream)` (`StreamingResponse(stream.dispatch(...))`),
    but interleaves three persistence steps around the dispatch:

      1. Before dispatch: load prior `conversation_messages` for
         `RunAgentInput.thread_id`, persist the newest user message,
         and replace `RunAgentInput.messages` with full history.
      2. During dispatch: pass every chunk through to the client AND
         parse SSE `data:` lines to accumulate any TEXT_MESSAGE_CONTENT
         deltas the orchestrator emits.
      3. After dispatch: persist the accumulated assistant text and
         `touch_thread` so the sidebar re-orders.

    All persistence failures are logged and swallowed -- they must never
    break the in-flight SSE stream from the user's point of view.
    """
    from ag_ui.core import RunAgentInput, AssistantMessage, UserMessage, SystemMessage, ToolMessage
    from starlette.endpoints import HTTPEndpoint
    from starlette.responses import StreamingResponse

    class HistoryAwareCopilotKitEndpoint(HTTPEndpoint):
        async def post(self, request: Request) -> StreamingResponse:
            raw_body = await request.body()
            try:
                incoming = RunAgentInput.model_validate_json(raw_body)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid RunAgentInput: {exc}") from exc

            requesting_user = getattr(request.state, "user_id", None)
            if requesting_user is None:
                raise HTTPException(
                    status_code=401,
                    detail="No user identity resolved on this request.",
                )

            thread_id = incoming.thread_id
            # Resolve / create the thread, owner-checked.
            existing_thread = await thread_store.get_thread(thread_id) if thread_id else None
            if existing_thread is None and thread_id:
                # First time we have seen this thread_id (browser-generated). Create it
                # for the current user. CopilotKit's React component is responsible for
                # generating stable threadIds per conversation -- the server simply
                # honors the first one it sees as a Web UI thread owned by this user.
                existing_thread = await thread_store.create_thread(
                    source="web_ui",
                    thread_id=thread_id,
                    user_id=requesting_user,
                    title=None,
                    metadata={"created_by": "agui_server._mount_copilotkit"},
                )
            elif existing_thread is not None:
                # Refuse if the thread exists but belongs to someone else.
                _require_thread_owner(existing_thread, request, "thread")

            # Load prior conversation history from DB (the source of truth per
            # Decision 14). The browser-supplied `incoming.messages` is treated
            # as the bootstrap (new thread) or the newest user turn (existing
            # thread) -- never authoritative.
            prior_history_ag_ui: list = []
            if existing_thread is not None:
                try:
                    rows = await conversation_store.list_for_thread(
                        thread_id, limit=200, ascending=True,
                    )
                    prior_history_ag_ui = [_db_row_to_ag_ui_message(r) for r in rows]
                    prior_history_ag_ui = [m for m in prior_history_ag_ui if m is not None]
                except Exception:
                    log.exception(
                        "/copilotkit: failed to load history for thread %s; "
                        "proceeding with browser-supplied messages only",
                        thread_id,
                    )

            # Persist the newest user message before dispatch, so even if the
            # LLM call fails (network blip, rate limit) the transcript still
            # carries the question.
            new_user_text = _extract_newest_user_text(incoming.messages)
            if existing_thread is not None and new_user_text:
                try:
                    await conversation_store.append_message(
                        thread_id,
                        agent_name="user",
                        role="user",
                        content={"text": new_user_text, "source": "copilotkit"},
                    )
                except Exception:
                    log.exception(
                        "/copilotkit: failed to persist user message for thread %s",
                        thread_id,
                    )

            # Inject history before the browser's messages. AG2 sees the full
            # transcript; the browser remains authoritative for the newest turn
            # only (a sanity check; we just persisted it ourselves).
            combined_messages = prior_history_ag_ui + list(incoming.messages or [])
            modified = incoming.model_copy(update={"messages": combined_messages})

            session_id_val = getattr(request.state, "session_id", None)

            # Shared A2A/Web UI rule: reuse an existing test_run_id when the
            # user supplies one; mint only for pre-script-creation requests.
            # Stash on _caller_identity so delegate_to_specialist prefers the
            # framework ID over any LLM-invented tool argument.
            from utils.test_run_id import ensure_test_run_id_for_inbound
            from agents.orchestrator.agent import (
                set_caller_identity,
                clear_caller_identity,
            )

            ensured_test_run_id = ensure_test_run_id_for_inbound(
                user_text=new_user_text,
            )
            set_caller_identity(
                user_id=requesting_user,
                thread_id=thread_id,
                session_id=str(session_id_val) if session_id_val else None,
                test_run_id=ensured_test_run_id,
            )

            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "/copilotkit caller identity: user_id=%s session_id=%s "
                    "thread_id=%s test_run_id=%s",
                    requesting_user, session_id_val, thread_id, ensured_test_run_id,
                )

            async def _streaming_with_persistence():
                accumulated_text: list[str] = []

                from agents.orchestrator.agent import (
                    drain_pending_executions,
                    _background_tasks,
                )
                from utils import task_executor

                def _drain_inline():
                    """Start background execution for tasks queued by tools
                    during this dispatch. Called after each SSE chunk so
                    delegated tasks begin immediately — not deferred until
                    the full dispatch completes (which would deadlock if the
                    orchestrator polls check_task_status in the same turn).
                    """
                    for pending_task_id in drain_pending_executions():
                        log.debug("Inline-drain: starting task %s", pending_task_id)
                        bg = asyncio.create_task(task_executor.execute_task(pending_task_id))
                        _background_tasks.add(bg)
                        bg.add_done_callback(_background_tasks.discard)

                # AG2's dispatch yields SSE-encoded strings already (one
                # `event: ...\ndata: ...\n\n` block per chunk). We pass each
                # through to the client AND parse the data line to capture
                # assistant text on the side.
                async for chunk in stream.dispatch(
                    modified,
                    accept=request.headers.get("accept"),
                ):
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", errors="ignore")
                    yield chunk
                    # DEBUG: log event types from AG2 stream
                    for _dbg_line in chunk.splitlines():
                        _dbg_stripped = _dbg_line.strip()
                        if _dbg_stripped.startswith("data:"):
                            try:
                                _dbg_data = json.loads(_dbg_stripped[5:].strip())
                                if isinstance(_dbg_data, dict) and "type" in _dbg_data:
                                    log.debug("AG2 SSE event type: %s", _dbg_data["type"])
                            except Exception:
                                pass
                    _capture_assistant_text_from_sse_chunk(chunk, accumulated_text)

                    # Inline drain: start delegated tasks as soon as the
                    # TOOL_CALL_END event is yielded — prevents the deadlock
                    # where the orchestrator polls check_task_status for a
                    # task that can't start until dispatch completes.
                    _drain_inline()

                if existing_thread is not None and accumulated_text:
                    full_text = "".join(accumulated_text)
                    try:
                        await conversation_store.append_message(
                            thread_id,
                            agent_name="orchestrator",
                            role="assistant",
                            content={"text": full_text, "source": "copilotkit"},
                        )
                        await thread_store.touch_thread(thread_id)
                    except Exception:
                        log.exception(
                            "/copilotkit: failed to persist assistant reply for thread %s",
                            thread_id,
                        )

                clear_caller_identity()

                # Safety-net drain: catch any tasks queued in the final
                # tool call whose TOOL_CALL_END event wasn't yielded before
                # the stream closed.
                _drain_inline()

            return StreamingResponse(
                _streaming_with_persistence(),
                media_type="text/event-stream",
            )

    return HistoryAwareCopilotKitEndpoint


def _db_row_to_ag_ui_message(row: Any) -> Any:
    """Convert a `conversation_messages` row to an AG-UI message instance.

    Returns None for malformed rows (unknown role / unrenderable content)
    so the caller can filter them out cleanly.
    """
    from ag_ui.core import AssistantMessage, SystemMessage, UserMessage, ToolMessage

    role = getattr(row, "role", None)
    content = getattr(row, "content", None)
    text = _coerce_text_from_db_content(content)
    msg_id = f"db-{getattr(row, 'id', '')}"
    try:
        if role == "user":
            return UserMessage(id=msg_id, role="user", content=text)
        if role == "assistant":
            return AssistantMessage(id=msg_id, role="assistant", content=text)
        if role == "system":
            return SystemMessage(id=msg_id, role="system", content=text)
        if role == "tool":
            # ToolMessage requires a `tool_call_id`; the rows we persist on
            # the conversational path don't carry one, so omit role==tool
            # rows from replay until 3.7.6 tool-call tracking lands.
            return None
    except Exception:
        log.exception("_db_row_to_ag_ui_message: failed to materialize row %s", msg_id)
    return None


def _coerce_text_from_db_content(content: Any) -> str:
    """Extract a renderable string from a JSONB conversation_messages.content."""
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


def _extract_newest_user_text(messages: Optional[list]) -> Optional[str]:
    """Pull the newest user-role message's textual content from a list of AG-UI messages."""
    if not messages:
        return None
    for msg in reversed(messages):
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list) and content:
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    return text
        return None
    return None


def _capture_assistant_text_from_sse_chunk(chunk: str, accumulator: list) -> None:
    """Parse one SSE chunk emitted by AGUIStream and append any text delta.

    AG-UI text streams arrive as TEXT_MESSAGE_CONTENT events with a
    `delta` field. The chunk format is the standard SSE shape:

        data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "...", ...}

    AG2 may also emit content via TEXT_MESSAGE_CHUNK or carry completed
    text in TEXT_MESSAGE_END. We capture all known patterns.

    Anything that fails to parse is silently ignored -- the chunk is
    still passed through to the client; only our local accumulator
    misses a delta.
    """
    for raw_line in chunk.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        event_type = str(data.get("type", "")).upper()

        if event_type == "TEXT_MESSAGE_CONTENT":
            delta = data.get("delta")
            if isinstance(delta, str):
                accumulator.append(delta)

        elif event_type == "TEXT_MESSAGE_CHUNK":
            delta = data.get("delta") or data.get("content") or data.get("text")
            if isinstance(delta, str):
                accumulator.append(delta)

        elif event_type == "TEXT_MESSAGE_END":
            content = data.get("content") or data.get("text") or data.get("delta")
            if isinstance(content, str) and content:
                accumulator.append(content)


# =============================================================================
# Pydantic request shapes (used by route signatures below)
# =============================================================================

class HitlPromptCreate(BaseModel):
    task_id: str
    prompt: dict = Field(default_factory=dict)


class HitlDecision(BaseModel):
    approval_id: int
    feedback: Optional[str] = None
    decided_by: Optional[str] = None


# -- PBI 3.7.6b: thread-management request shapes --
# Threads on this surface are always Web UI threads (`source='web_ui'`,
# `user_id` set, `external_thread_id` left NULL). A2A threads are
# created by the A2A executor (PBI 3.7.8) and are never visible here
# per Decision 16 (two ownership flavours, never both NULL).

class ThreadCreate(BaseModel):
    """Body for POST /api/threads."""

    title: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    # Optional caller-supplied stable ID (CopilotKit `threadId`). When
    # omitted the DB DEFAULT mints a fresh UUID hex.
    thread_id: Optional[str] = None


class ThreadUpdate(BaseModel):
    """Body for PATCH /api/threads/{thread_id}.

    All fields optional. Empty body = no-op (returns the current row).
    `status` must be one of `thread_store.VALID_STATUSES` when supplied.
    Use the DELETE endpoint to soft-delete -- the PATCH endpoint refuses
    `status='deleted'` so deletion is always an explicit verb.
    """

    title: Optional[str] = None
    status: Optional[str] = None


# =============================================================================
# Route registration
# =============================================================================

def _register_routes(app: FastAPI) -> None:
    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "service": "agui", "version": SERVER_VERSION}

    @app.get("/api/settings", tags=["meta"])
    async def get_settings() -> dict:
        """Return frontend-relevant display preferences from agents.yaml.

        Lightweight, no auth required — only non-sensitive UI toggles.
        """
        return {
            "context_indicator": {
                "enabled": agents_config.get_context_indicator_enabled(FRAMEWORK_DIR),
            },
        }

    # -- AG-UI SSE event stream (PBI 3.6.2) --------------------------------------
    # Browser clients (typically CopilotKit's React UI) follow a task they
    # already started elsewhere by subscribing to this endpoint with the
    # `task_id`. The same `task_executor` broadcast bus that powers the
    # A2A server's `tasks/sendSubscribe` is reused here - we just translate
    # to the AG-UI envelope and add CORS-friendly heartbeats.
    @app.get("/api/events", tags=["agui"])
    async def stream_events(request: Request, task_id: str) -> EventSourceResponse:
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        existing = await task_store.get_task(task_uuid)
        if existing is None:
            raise HTTPException(status_code=404, detail="Task not found")

        # PBI 3.7.0b owner-filtering: only the task's owner can subscribe to
        # its event stream. A subscriber that doesn't own the task would
        # otherwise see the task's payload + result via the snapshot event,
        # which is the same data the /api/runs/{id} endpoint protects.
        owner = await auth.owner_of_session(existing.session_id)
        auth.requires_owner(
            resource_owner=owner,
            requesting_user=getattr(request.state, "user_id", None),
            resource_kind="task event stream",
        )

        async def _stream():
            yield {
                "event": "snapshot",
                "data": json.dumps(_task_to_snapshot_payload(existing)),
            }
            if existing.status in task_store.TERMINAL_STATUSES:
                # Already terminal - one snapshot then close. No need to subscribe.
                return
            queue = await task_executor.subscribe(task_uuid)
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {"event": "state", "data": event.to_sse_data()}
                    if event.status in task_store.TERMINAL_STATUSES:
                        break
            finally:
                await task_executor.unsubscribe(task_uuid, queue)

        return EventSourceResponse(_stream())

    # -- Sessions (PBI 3.6.4 + PBI 3.7.0b owner-filtering) ------------------------
    @app.get("/api/sessions", tags=["sessions"])
    async def list_sessions_route(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        source: Optional[str] = None,
        include_ended: bool = False,
    ) -> dict:
        """Return recent sessions owned by the requesting user.

        Owner-filtered in PBI 3.7.0b: only sessions whose `user_id` matches
        `request.state.user_id` are returned. The Web UI session picker is
        per-user; Alice never sees Bob's sessions.
        """
        requesting_user = getattr(request.state, "user_id", None)
        sessions = await session_store.list_sessions(
            limit=limit,
            offset=offset,
            source=source,
            include_ended=include_ended,
            user_id=requesting_user,
        )
        return {
            "sessions": [_session_to_dict(s) for s in sessions],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/sessions/me", tags=["sessions"])
    async def my_session_route(request: Request) -> dict:
        """Return the session attached to the current request by middleware.

        Browsers hit this on first page load to learn their `session_id`
        without needing to send a header. Combined with the `X-Session-Id`
        echo on the response, this is how the React UI bootstraps.

        Not owner-filtered: by definition the session is the requesting
        user's own (the middleware either created it for them or matched
        their own X-Session-Id). The middleware re-uses an existing
        session row when its `session_id` arrives via header -- and we
        still 403 if that row turns out to belong to another user, so a
        client can't hijack someone else's session by spoofing its ID.
        """
        session_id = getattr(request.state, "session_id", None)
        if session_id is None:
            raise HTTPException(
                status_code=503,
                detail="Session could not be established (perfagent_state unavailable).",
            )
        session = await session_store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        auth.requires_owner(
            resource_owner=session.user_id,
            requesting_user=getattr(request.state, "user_id", None),
            resource_kind="session",
        )
        return _session_to_dict(session)

    @app.get("/api/sessions/{session_id}", tags=["sessions"])
    async def get_session_route(session_id: str, request: Request) -> dict:
        try:
            uuid_value = UUID(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed session_id") from exc
        session = await session_store.get_session(uuid_value)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        auth.requires_owner(
            resource_owner=session.user_id,
            requesting_user=getattr(request.state, "user_id", None),
            resource_kind="session",
        )
        return _session_to_dict(session)

    # -- Test runs (PBI 3.6.6 + PBI 3.7.0b owner-filtering) -----------------------
    @app.get("/api/runs", tags=["runs"])
    async def list_runs_route(request: Request, limit: int = 50, offset: int = 0) -> dict:
        """Return recent test runs owned by the requesting user.

        Owner-filtered in PBI 3.7.0b: a run "belongs to" a user when at
        least one of its tasks lives in a session owned by that user.
        Implemented in `task_store.list_runs(user_id=...)` via a JOIN.
        """
        requesting_user = getattr(request.state, "user_id", None)
        runs = await task_store.list_runs(
            limit=limit,
            offset=offset,
            user_id=requesting_user,
        )
        return {
            "runs": [_run_summary_to_dict(r) for r in runs],
            "total": len(runs),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/runs/{test_run_id}", tags=["runs"])
    async def get_run_route(test_run_id: str, request: Request) -> dict:
        """Return every task associated with a single `test_run_id`.

        Owner-filtered: 404 when the user has no tasks under this run,
        which is indistinguishable from "this run does not exist." We
        deliberately avoid 403 here so callers cannot probe for the
        existence of other users' runs by trying random test_run_ids.
        """
        requesting_user = getattr(request.state, "user_id", None)
        tasks = await task_store.list_tasks_for_run(
            test_run_id,
            user_id=requesting_user,
        )
        if not tasks:
            raise HTTPException(status_code=404, detail="No tasks found for this test_run_id")
        return {
            "test_run_id": test_run_id,
            "task_count": len(tasks),
            "tasks": [_task_to_snapshot_payload(t) for t in tasks],
        }

    # -- Thread-scoped tasks -----------------------------------------
    # Discovers tasks by conversation thread regardless of test_run_id.
    # Complements /api/runs (historical, run-scoped) with real-time,
    # conversation-scoped task discovery for the TaskProgressPanel.
    @app.get("/api/tasks", tags=["tasks"])
    async def list_tasks_route(
        request: Request,
        thread_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Return recent tasks for the current user, optionally scoped to a thread.

        When thread_id is provided: returns only tasks from that conversation.
        When thread_id is omitted: returns an empty list with guidance (future:
        all recent tasks for the user regardless of thread).
        """
        requesting_user = getattr(request.state, "user_id", None)

        if thread_id:
            tasks = await task_store.list_tasks_for_thread(
                thread_id,
                user_id=requesting_user,
                limit=limit,
                offset=offset,
            )
        else:
            tasks = []

        return {
            "tasks": [_task_to_snapshot_payload(t) for t in tasks],
            "thread_id": thread_id,
            "limit": limit,
            "offset": offset,
        }

    # -- Single task retrieval -----------------------------------------
    # The orchestrator's `check_task_status` tool queries this endpoint for
    # Web UI-originated tasks. Mirrors A2A's `GET /agents/{name}/tasks/{id}`
    # but runs on the AG-UI surface so the Web UI never touches A2A.
    @app.get("/api/tasks/{task_id}", tags=["tasks"])
    async def get_task_route(task_id: str, request: Request) -> dict:
        """Return a single task by ID (owner-filtered).

        Used by the orchestrator's ``check_task_status`` tool when running
        in a Web UI context (``agent_request_source_var == "web_ui"``).
        """
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        task = await task_store.get_task(task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        task_owner = await auth.owner_of_session(task.session_id)
        auth.requires_owner(
            resource_owner=task_owner,
            requesting_user=getattr(request.state, "user_id", None),
            resource_kind="task",
        )

        return _task_to_snapshot_payload(task)

    # -- Internal delegation ----------------------------------------
    # The orchestrator's `delegate_to_specialist` tool POSTs here for Web
    # UI-originated requests. Mirrors A2A's `POST /agents/{name}/tasks/send`
    # but uses the CopilotKit thread_id directly (no external thread
    # resolution), keeping the delegated task visible in the same
    # conversation the browser user is watching.
    @app.post("/api/delegate", tags=["delegation"], status_code=202)
    async def delegate_task_route(request: Request) -> dict:
        """Create and dispatch a specialist task for Web UI delegation.

        Expects a JSON body::

            {
                "agent_name": "execution-agent",
                "payload": { ... },
                "test_run_id": "optional-correlation-key"
            }

        Identity context comes from headers set by
        ``_agent_outbound_headers()`` in the orchestrator:

        - ``X-Thread-Id``: the CopilotKit thread_id (stored as-is)
        - ``X-Session-Id``: the browser session (resolved by middleware)
        - ``X-PerfPilot-Token``: the browser user (resolved by middleware)
        """
        session_id = getattr(request.state, "session_id", None)
        if session_id is None:
            raise HTTPException(
                status_code=401,
                detail="No session resolved; delegation requires a valid session.",
            )

        body = await request.json()
        agent_name = body.get("agent_name")
        if not agent_name or not isinstance(agent_name, str):
            raise HTTPException(status_code=400, detail="Missing or invalid 'agent_name'")

        if not agents_config.is_agent_enabled(agent_name, FRAMEWORK_DIR):
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{agent_name}' is not enabled or unknown.",
            )

        thread_id = (
            request.headers.get("X-Thread-Id")
            or request.headers.get("x-thread-id")
        )
        payload = body.get("payload") or {}
        test_run_id = body.get("test_run_id") or payload.get("test_run_id")

        task = await task_store.create_task(
            session_id=session_id,
            agent_name=agent_name,
            payload=payload,
            test_run_id=test_run_id,
            thread_id=thread_id,
        )
        asyncio.create_task(task_executor.execute_task(task.task_id))

        return {
            "task_id": str(task.task_id),
            "session_id": str(task.session_id),
            "agent_name": task.agent_name,
            "status": task.status,
            "thread_id": thread_id,
            "submitted_at": task.submitted_at.isoformat(),
        }

    # -- HITL approvals (PBI 3.6.5 + PBI 3.7.0b owner-filtering) -----------------
    @app.post("/api/hitl/prompts", tags=["hitl"], status_code=201)
    async def create_hitl_prompt(body: HitlPromptCreate) -> dict:
        """Open a new HITL prompt for a task.

        Typically called by an agent (server-side trusted code path) rather
        than the browser. Exposed here for symmetry and so the smoke test
        can exercise the full lifecycle without an agent in the loop.

        Owner-filtering: EXEMPT in PBI 3.7.0b. The agent is server-side
        trusted code creating a prompt on behalf of a task it is already
        running. Validating ownership here would require resolving the
        task's session and matching against the requesting user, which is
        not appropriate for server-side callers that don't have a user_id.

        Future hardening (housekeeping item H5 in the status doc): swap
        this exemption for service-principal-only auth in Epic 4, so
        end-users cannot self-create approval requests under forged
        identities. The /approve and /reject endpoints below already
        protect the *decision* side via `requires_owner` (the security
        boundary that actually matters: forging an approval).
        """
        try:
            task_uuid = UUID(body.task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc
        approval = await hitl_store.create_prompt(task_uuid, body.prompt)
        return _approval_to_dict(approval)

    @app.get("/api/hitl/tasks/{task_id}", tags=["hitl"])
    async def list_hitl_for_task(task_id: str, request: Request) -> dict:
        """List HITL approvals for a task, restricted to the task's owner.

        Owner-filtered: the requesting user must own the underlying task's
        session. Otherwise 403 -- same answer they'd get for any other
        user's task, so the read surface doesn't leak existence.
        """
        try:
            task_uuid = UUID(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed task_id") from exc

        task_owner = await auth.owner_of_task(task_uuid)
        if task_owner is None:
            # Distinguish "no such task" from "task exists but not yours"
            # at the *load* layer: if the task doesn't exist at all, return
            # 404 instead of leaking via 403.
            existing_task = await task_store.get_task(task_uuid)
            if existing_task is None:
                raise HTTPException(status_code=404, detail="Task not found")
        auth.requires_owner(
            resource_owner=task_owner,
            requesting_user=getattr(request.state, "user_id", None),
            resource_kind="HITL task",
        )

        approvals = await hitl_store.list_for_task(task_uuid)
        return {
            "task_id": task_id,
            "approvals": [_approval_to_dict(a) for a in approvals],
        }

    @app.post("/api/hitl/approve", tags=["hitl"])
    async def approve_hitl(body: HitlDecision, request: Request) -> dict:
        """Approve a pending HITL prompt. Owner-filtered."""
        await _enforce_hitl_owner(body.approval_id, request)
        return _serialize_decision_response(
            await hitl_store.record_decision(
                body.approval_id,
                decision="approved",
                decided_by=_resolve_decider(request, body.decided_by),
            )
        )

    @app.post("/api/hitl/reject", tags=["hitl"])
    async def reject_hitl(body: HitlDecision, request: Request) -> dict:
        """Reject a pending HITL prompt with optional feedback. Owner-filtered."""
        await _enforce_hitl_owner(body.approval_id, request)
        return _serialize_decision_response(
            await hitl_store.record_decision(
                body.approval_id,
                decision="rejected",
                feedback=body.feedback,
                decided_by=_resolve_decider(request, body.decided_by),
            )
        )

    # -- Threads (PBI 3.7.6b) -----------------------------------------------------
    # ChatGPT-style persistent conversation containers. Each thread is owned
    # by one Web UI / IDE / CLI user; an A2A-originated thread (`user_id`
    # NULL, `external_thread_id` set) is never visible on this surface --
    # the two ownership worlds are intentionally disjoint per Decision 16.
    #
    # Endpoints:
    #   POST   /api/threads                 - create owned by current user
    #   GET    /api/threads                 - list threads owned by current user
    #   GET    /api/threads/{thread_id}     - fetch single (owner-checked)
    #   PATCH  /api/threads/{thread_id}     - rename / archive (owner-checked)
    #   DELETE /api/threads/{thread_id}     - soft-delete (owner-checked)
    #   GET    /api/threads/{tid}/messages  - conversation transcript
    #
    # All owner checks use the established `auth.requires_owner` helper.
    # Existence-vs-permission disambiguation: an unowned thread is reported
    # as 404 (matches the runs convention) so callers cannot probe other
    # users' thread_ids by trial-and-error.

    @app.post("/api/threads", tags=["threads"], status_code=201)
    async def create_thread_route(body: ThreadCreate, request: Request) -> dict:
        """Create a new Web UI thread owned by the current user."""
        requesting_user = getattr(request.state, "user_id", None)
        if requesting_user is None:
            raise HTTPException(
                status_code=401,
                detail="No user identity resolved on this request.",
            )
        thread = await thread_store.create_thread(
            source="web_ui",
            thread_id=body.thread_id,
            user_id=requesting_user,
            title=body.title,
            metadata=body.metadata,
        )
        return _thread_to_dict(thread)

    @app.get("/api/threads", tags=["threads"])
    async def list_threads_route(
        request: Request,
        limit: int = 50,
        offset: int = 0,
        include_archived: bool = False,
    ) -> dict:
        """List threads owned by the current user, freshest first.

        Owner-filtered at the SQL layer via `thread_store.list_for_user`
        -- Alice never sees Bob's threads even if she tries paginating
        deep. Set `include_archived=true` to surface archived alongside
        active (deleted threads are never returned by this helper).
        """
        requesting_user = getattr(request.state, "user_id", None)
        if requesting_user is None:
            raise HTTPException(
                status_code=401,
                detail="No user identity resolved on this request.",
            )
        if include_archived:
            threads = await thread_store.list_for_user(
                requesting_user, limit=limit, offset=offset,
                status=None, include_archived=True,
            )
        else:
            threads = await thread_store.list_for_user(
                requesting_user, limit=limit, offset=offset, status="active",
            )
        return {
            "threads": [_thread_to_dict(t) for t in threads],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/threads/{thread_id}", tags=["threads"])
    async def get_thread_route(thread_id: str, request: Request) -> dict:
        thread = await thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        _require_thread_owner(thread, request, "thread")
        return _thread_to_dict(thread)

    @app.patch("/api/threads/{thread_id}", tags=["threads"])
    async def update_thread_route(
        thread_id: str,
        body: ThreadUpdate,
        request: Request,
    ) -> dict:
        """Rename and/or change status. Empty body = no-op fetch.

        Soft-delete is intentionally NOT exposed here -- use DELETE for
        that so deletion is always an explicit verb. PATCH with
        `status='deleted'` returns 400.
        """
        thread = await thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        _require_thread_owner(thread, request, "thread")

        if body.status is not None and body.status not in ("active", "archived"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "PATCH /api/threads accepts status='active' or 'archived'. "
                    "To delete, use DELETE /api/threads/{thread_id}."
                ),
            )

        updated = thread
        if body.title is not None:
            renamed = await thread_store.set_title(thread_id, body.title)
            if renamed is not None:
                updated = renamed
        if body.status is not None and body.status != updated.status:
            transitioned = await thread_store.set_status(thread_id, body.status)
            if transitioned is not None:
                updated = transitioned
        return _thread_to_dict(updated)

    @app.delete("/api/threads/{thread_id}", tags=["threads"])
    async def delete_thread_route(thread_id: str, request: Request) -> dict:
        """Soft-delete a thread (status -> 'deleted').

        Messages are retained (`conversation_messages` rows untouched) for
        auditability. Use `hard_delete` via a maintenance script for
        unrecoverable removal.
        """
        thread = await thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        _require_thread_owner(thread, request, "thread")
        result = await thread_store.soft_delete_thread(thread_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return _thread_to_dict(result)

    @app.get("/api/threads/{thread_id}/messages", tags=["threads"])
    async def list_thread_messages_route(
        thread_id: str,
        request: Request,
        limit: int = 200,
        offset: int = 0,
        ascending: bool = True,
    ) -> dict:
        """Return paginated conversation messages for a thread.

        Authorization: thread must exist AND be owned by the requesting
        user. The messages table itself has no owner column -- ownership
        flows transitively from the parent thread.
        """
        thread = await thread_store.get_thread(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        _require_thread_owner(thread, request, "thread messages")

        messages = await conversation_store.list_for_thread(
            thread_id, limit=limit, offset=offset, ascending=ascending,
        )
        total = await conversation_store.count_for_thread(thread_id)
        return {
            "thread_id": thread_id,
            "messages": [_message_to_dict(m) for m in messages],
            "limit": limit,
            "offset": offset,
            "total": total,
        }

    # Subsequent PBIs (3.6.3) plug additional routers in here.


def _task_to_snapshot_payload(task: task_store.AgentTask) -> dict:
    """Browser-friendly snapshot of an `agent_tasks` row for AG-UI consumers."""
    return {
        "task_id": str(task.task_id),
        "session_id": str(task.session_id) if task.session_id else None,
        "external_session_id": task.external_session_id,
        "agent_name": task.agent_name,
        "status": task.status,
        "test_run_id": task.test_run_id,
        "result": task.result,
        "error": task.error,
        "submitted_at": task.submitted_at.isoformat() if task.submitted_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _session_to_dict(session: session_store.AgentSession) -> dict:
    """Browser-friendly view of an `agent_sessions` row."""
    return {
        "session_id": str(session.session_id),
        "external_session_id": session.external_session_id,
        "source": session.source,
        "user_id": session.user_id,
        "metadata": session.metadata,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "last_activity_at": session.last_activity_at.isoformat() if session.last_activity_at else None,
    }


def _run_summary_to_dict(run: task_store.RunSummary) -> dict:
    """Browser-friendly view of a `RunSummary`."""
    return {
        "test_run_id": run.test_run_id,
        "task_count": run.task_count,
        "statuses": {
            "completed": run.completed_count,
            "failed": run.failed_count,
            "running": run.active_count,
            "cancelled": run.cancelled_count,
        },
        "agents": run.agent_names,
        "earliest_submitted": run.started_at.isoformat() if run.started_at else None,
        "latest_completed": run.last_activity_at.isoformat() if run.last_activity_at else None,
        "test_name": run.test_name,
        "duration_seconds": run.duration_seconds,
        "max_virtual_users": run.max_virtual_users,
        "samples_total": run.samples_total,
        "avg_response_time_ms": run.avg_response_time_ms,
        "p90_response_time_ms": run.p90_response_time_ms,
        "median_response_time_ms": run.median_response_time_ms,
        "avg_throughput": run.avg_throughput,
        "error_rate": run.error_rate,
        "avg_bandwidth_bytes": run.avg_bandwidth_bytes,
        "public_url": run.public_url,
    }


def _thread_to_dict(thread: thread_store.AgentThread) -> dict:
    """Browser-friendly view of an `agent_threads` row (PBI 3.7.6b).

    The `external_thread_id` is always None on this surface (Web UI
    threads never carry one per Decision 16) -- it is included in the
    payload for shape symmetry with the A2A executor's response in PBI
    3.7.8, where the same field will be populated.
    """
    return {
        "thread_id": thread.thread_id,
        "user_id": thread.user_id,
        "external_thread_id": thread.external_thread_id,
        "source": thread.source,
        "title": thread.title,
        "status": thread.status,
        "metadata": thread.metadata,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
    }


def _message_to_dict(message: conversation_store.ConversationMessage) -> dict:
    """Browser-friendly view of a `conversation_messages` row (PBI 3.7.6b)."""
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "agent_name": message.agent_name,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _require_thread_owner(
    thread: thread_store.AgentThread,
    request: Request,
    resource_kind: str = "thread",
) -> None:
    """403 / 404 unless `request.state.user_id` owns `thread`.

    A Web UI thread carries `user_id` and `external_thread_id IS NULL`.
    The check is straightforward `auth.requires_owner` on `user_id`.

    If a thread arrives without a `user_id` (A2A-originated -- the
    `user_id` column is NULL and `external_thread_id` is set), this
    surface refuses to expose it: return 404 rather than 403 so probing
    other-world thread_ids does not leak existence. This matches the
    `/api/runs` pattern.
    """
    if thread.user_id is None:
        raise HTTPException(status_code=404, detail=f"{resource_kind.capitalize()} not found")
    auth.requires_owner(
        resource_owner=thread.user_id,
        requesting_user=getattr(request.state, "user_id", None),
        resource_kind=resource_kind,
    )


def _approval_to_dict(approval: hitl_store.HitlApproval) -> dict:
    """Browser-friendly view of a `hitl_approvals` row (PBI 3.6.5)."""
    return {
        "id": approval.id,
        "task_id": str(approval.task_id),
        "prompt": approval.prompt,
        "decision": approval.decision,
        "feedback": approval.feedback,
        "decided_by": approval.decided_by,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
    }


def _serialize_decision_response(approval: Optional[hitl_store.HitlApproval]) -> dict:
    """Either return the updated row or 404 if it was missing/already terminal."""
    if approval is None:
        raise HTTPException(
            status_code=404,
            detail="No pending HITL approval found for that id (already decided or absent).",
        )
    return _approval_to_dict(approval)


def _resolve_decider(request: Request, body_value: Optional[str]) -> Optional[str]:
    """Decide who recorded a HITL decision.

    Precedence: explicit body field > middleware-resolved `user_id`
    (which itself follows the four-step chain in `utils.user_identity`).
    """
    if body_value:
        return body_value
    state_user = getattr(request.state, "user_id", None)
    if state_user:
        return state_user
    return None


async def _enforce_hitl_owner(approval_id: int, request: Request) -> None:
    """Raise 403/404 unless the requesting user owns the HITL approval.

    "Owns" walks `hitl_approvals -> agent_tasks -> agent_sessions ->
    user_id`. The flow:

      1. Look up the approval row; 404 if absent (no ownership leak --
         a wrong approval_id and an unowned approval_id both 404 if you
         don't already know which is which).
      2. Resolve the owner of the approval's task.
      3. `auth.requires_owner` enforces equality with `request.state.user_id`.

    Called by both /approve and /reject so the security check is identical.
    """
    approval = await hitl_store.get_approval(approval_id)
    if approval is None:
        # Don't leak "this approval id has been decided by someone else"
        # via a 403. 404 covers both "never existed" and "already terminal."
        raise HTTPException(
            status_code=404,
            detail="No pending HITL approval found for that id (already decided or absent).",
        )
    owner = await auth.owner_of_task(approval.task_id)
    auth.requires_owner(
        resource_owner=owner,
        requesting_user=getattr(request.state, "user_id", None),
        resource_kind="HITL approval",
    )


# =============================================================================
# ASGI entrypoint
# =============================================================================

app = create_app()


def _resolve_port() -> int:
    raw = os.environ.get("AGUI_PORT", "8002")
    try:
        return int(raw)
    except ValueError:
        log.warning("AGUI_PORT=%r is not an int; falling back to 8002", raw)
        return 8002


def main() -> None:
    """`python agui_server.py` entrypoint. Loads .env and runs uvicorn."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Temporary: enable DEBUG on identity-related loggers for token tracing.
    # Remove after BUG-11 investigation is complete.
    logging.getLogger("agui_server").setLevel(logging.DEBUG)
    logging.getLogger("utils.session_middleware").setLevel(logging.DEBUG)
    logging.getLogger("agents.orchestrator.agent").setLevel(logging.DEBUG)
    try:
        from dotenv import load_dotenv
        load_dotenv(FRAMEWORK_DIR / ".env", override=False)
    except ImportError:
        log.debug("python-dotenv not installed; relying on shell env only")

    # Use the OS certificate store (Windows/macOS) instead of certifi's bundle.
    # This is required when Norton 360 / Zscaler / corporate TLS inspection
    # injects a custom root CA that httpx (via certifi) doesn't know about.
    try:
        import truststore
        truststore.inject_into_ssl()
        log.info("truststore: injected OS certificate store into ssl module")
    except ImportError:
        log.debug("truststore not installed; using certifi CA bundle")

    import uvicorn
    uvicorn.run(
        "agui_server:app",
        host=os.environ.get("AGUI_HOST", "0.0.0.0"),
        port=_resolve_port(),
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
