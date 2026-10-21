"""A2A FastAPI server for PerfPilot Agents (port 8101).

Public A2A traffic uses the JSON-RPC and REST bindings below so
standards-compliant clients can talk to PerfPilot without adapters.
Agent catalog discovery under ``/agents/{name}/...`` remains for the
Web UI catalog proxy; specialist work is not submitted on those paths.
Branding lives only on port 8102 (the AG-UI bridge).

Discovery / liveness (used by the Web UI catalog proxy):

    GET  /health                                            - liveness probe
    GET  /agents                                            - list discoverable agents
    GET  /agents/{name}/.well-known/agent.json              - agent card (RFC 8615)

A2A v1.0.0 HTTP+JSON/REST binding (Spec Section 11):

    GET  /.well-known/agent-card.json                       - orchestrator card (A2A discovery)
    POST /message:send                                      - send message (returns Task)
    POST /message:stream                                    - send message + SSE stream
    GET  /tasks/{task_id}                                   - get task by ID
    POST /tasks/{task_id}:cancel                            - cancel task

A2A v1.0.0 JSON-RPC 2.0 binding (Spec Section 9):

    POST /a2a/v1                                            - JSON-RPC 2.0 dispatcher

Route registration for A2A v1 is delegated to sub-modules:

    a2a.server.v1_jsonrpc  — register_a2a_v1_jsonrpc_route()
    a2a.server.v1_rest     — register_a2a_v1_routes()
    a2a.server.helpers     — normalize, converters, resume, payload
    a2a.server.utils       — response headers, thread/context resolution
    a2a.shared.errors      — google.rpc.Status error builders (HTTP binding)
    a2a.shared.jsonrpc     — JSON-RPC 2.0 envelope, error helpers

Long-running task model:

    Pattern 1 - Polling: caller submits via SendMessage / POST /message:send,
                         then polls GET /tasks/{id} (or JSON-RPC GetTask).
    Pattern 2 - SSE:     caller submits via SendStreamingMessage /
                         POST /message:stream and holds the SSE stream until
                         the task reaches a terminal state.
    Pattern 3 - Webhook: caller submits with ``subscriber_endpoints``; server
                         POSTs the final body to each URL on completion.

Run locally:

    cd agent-framework
    python a2a_server.py

Or via uvicorn from the agent-framework folder:

    uvicorn a2a_server:app --host 0.0.0.0 --port 8101
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

if __package__ is None:
    # Allow `python a2a_server.py` from inside backend/.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from utils import agents_config
from services import task_executor
from core import base_agent
from stores import db, task_store
from utils.paths import get_framework_dir
from services.session_middleware import SessionMiddleware
from a2a.server.utils import context

log = logging.getLogger(__name__)

FRAMEWORK_DIR = get_framework_dir()
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

# =============================================================================
# App lifespan
# =============================================================================

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Open the asyncpg pool eagerly so the first request is fast."""
    log.info("A2A server starting; warming asyncpg pool")
    try:
        await db.get_pool()
        log.info("A2A server ready (port=%s)", os.environ.get("A2A_PORT", "8101"))
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

    from a2a.server.v1_jsonrpc import register_a2a_v1_jsonrpc_route
    from a2a.server.v1_rest import register_a2a_v1_routes
    _a2a_v1_ctx = {
        "agents_config": agents_config,
        "base_agent": base_agent,
        "task_store": task_store,
        "task_executor": task_executor,
        "FRAMEWORK_DIR": FRAMEWORK_DIR,
        "AGENTS_DIR": AGENTS_DIR,
        "SERVER_VERSION": SERVER_VERSION,
        "require_enabled_agent": _require_enabled_agent,
        "require_session": _require_session,
        "read_json_body": _read_json_body,
        "resolve_a2a_thread": context._resolve_a2a_thread,
        "extract_subscriber_endpoints": _extract_subscriber_endpoints,
        "thread_response_headers": context._thread_response_headers,
    }
    register_a2a_v1_routes(app, _a2a_v1_ctx)
    register_a2a_v1_jsonrpc_route(app, _a2a_v1_ctx)
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


# =============================================================================
# ASGI entrypoint
# =============================================================================

app = create_app()


def _resolve_port() -> int:
    raw = os.environ.get("A2A_PORT", "8101")
    try:
        return int(raw)
    except ValueError:
        log.warning("A2A_PORT=%r is not an int; falling back to 8101", raw)
        return 8101


def main() -> None:
    """`python a2a_server.py` entrypoint. Loads .env and runs uvicorn."""
    from utils.logging_config import configure_logging

    configure_logging()
    try:
        from dotenv import load_dotenv
        load_dotenv(FRAMEWORK_DIR / ".env", override=False)
    except ImportError:
        log.debug("python-dotenv not installed; relying on shell env only")

    try:
        import truststore
        truststore.inject_into_ssl()
        log.info("truststore: injected OS certificate store into ssl module")
    except ImportError:
        log.debug("truststore not installed; using certifi CA bundle")

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
