"""A2A v1.0.0 route registration for HTTP+JSON/REST and JSON-RPC 2.0 bindings.

Contains the two route-registration functions extracted from
``a2a_server.py``:

- ``register_a2a_v1_routes(app, ctx)`` — 5 HTTP+JSON/REST endpoints
- ``register_a2a_v1_jsonrpc_route(app, ctx)`` — single JSON-RPC 2.0
  endpoint dispatching 7 A2A methods

Both functions receive a ``ctx`` dict from ``a2a_server.create_app()``
containing references to shared helpers and module-level state. This
avoids circular imports since this module never imports ``a2a_server``
directly.

Context dict keys::

    ctx = {
        "agents_config": <module>,
        "base_agent": <module>,
        "task_store": <module>,
        "task_executor": <module>,
        "FRAMEWORK_DIR": Path,
        "AGENTS_DIR": Path,
        "SERVER_VERSION": str,
        "require_enabled_agent": async callable,
        "require_session": callable,
        "read_json_body": async callable,
        "resolve_a2a_thread": async callable,
        "extract_subscriber_endpoints": callable,
        "thread_response_headers": callable,
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .a2a_v1_helpers import (
    A2A_V1_AGENT_NAME,
    _a2a_v1_response_headers,
    _normalize_a2a_v1_body,
    _task_event_to_a2a_v1_sse,
    _task_to_a2a_v1,
)

log = logging.getLogger(__name__)


# =============================================================================
# A2A v1.0.0 HTTP+JSON/REST Binding (Spec Section 11)
# =============================================================================


def register_a2a_v1_routes(app: FastAPI, ctx: dict) -> None:
    """Register A2A v1.0.0 HTTP+JSON/REST binding routes.

    All routes implicitly target the orchestrator agent. These are
    additive alongside the existing legacy ``/agents/{name}/...`` routes.

    Args:
        app: The FastAPI application instance.
        ctx: Shared context dict from ``create_app()``.
    """
    agents_config = ctx["agents_config"]
    base_agent = ctx["base_agent"]
    task_store = ctx["task_store"]
    task_executor = ctx["task_executor"]
    FRAMEWORK_DIR = ctx["FRAMEWORK_DIR"]
    AGENTS_DIR = ctx["AGENTS_DIR"]
    SERVER_VERSION = ctx["SERVER_VERSION"]
    _require_enabled_agent = ctx["require_enabled_agent"]
    _require_session = ctx["require_session"]
    _read_json_body = ctx["read_json_body"]
    _resolve_a2a_thread = ctx["resolve_a2a_thread"]
    _extract_subscriber_endpoints = ctx["extract_subscriber_endpoints"]
    _thread_response_headers = ctx["thread_response_headers"]

    from .a2a_errors import (
        http_bad_request,
        http_not_found,
        http_task_not_found,
        httpexception_to_a2a,
    )

    def _v1_headers(thread=None):
        th = _thread_response_headers(thread) if thread is not None else None
        return _a2a_v1_response_headers(th)

    # -- Discovery (A2A v1) --------------------------------------------------
    @app.get("/.well-known/agent-card.json", tags=["a2a-v1", "discovery"])
    async def a2a_v1_agent_card() -> JSONResponse:
        """Return the orchestrator's agent card at the A2A v1 discovery path.

        A2A spec Section 8: public agent card at
        ``/.well-known/agent-card.json``.
        """
        if not agents_config.is_agent_enabled(A2A_V1_AGENT_NAME, FRAMEWORK_DIR):
            return http_not_found(
                f"Agent '{A2A_V1_AGENT_NAME}' is not enabled.",
                reason="NOT_FOUND",
                metadata={"agentName": A2A_V1_AGENT_NAME},
            )
        card = base_agent.read_agent_card(
            AGENTS_DIR / A2A_V1_AGENT_NAME,
            fallback_framework_version=SERVER_VERSION,
        )
        return JSONResponse(card, headers=_v1_headers())

    # -- SendMessage (A2A v1 Section 11) -------------------------------------
    @app.post("/message:send", status_code=202, tags=["a2a-v1", "tasks"])
    async def a2a_v1_send_message(request: Request) -> JSONResponse:
        """Submit a message to the orchestrator (A2A v1 REST binding).

        Accepts both A2A v1 ``SendMessageRequest`` envelopes and legacy
        PerfPilot payloads. Returns an A2A v1 ``Task`` object.
        """
        try:
            agent_name = A2A_V1_AGENT_NAME
            await _require_enabled_agent(agent_name)
            session_id = _require_session(request)
            body = await _read_json_body(request)
        except HTTPException as exc:
            return httpexception_to_a2a(exc.status_code, exc.detail)
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
            headers=_v1_headers(thread),
        )

    # -- SendStreamingMessage (A2A v1 Section 11) ----------------------------
    @app.post("/message:stream", tags=["a2a-v1", "tasks"])
    async def a2a_v1_stream_message(request: Request):
        """Submit a message and receive an SSE stream (A2A v1 REST binding).

        Returns ``StreamResponse`` events with camelCase A2A v1 shapes.
        """
        try:
            agent_name = A2A_V1_AGENT_NAME
            await _require_enabled_agent(agent_name)
            session_id = _require_session(request)
            body = await _read_json_body(request)
        except HTTPException as exc:
            return httpexception_to_a2a(exc.status_code, exc.detail)
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
            headers=_v1_headers(thread),
        )

    # -- GetTask (A2A v1 Section 11) -----------------------------------------
    @app.get("/tasks/{task_id}", tags=["a2a-v1", "tasks"])
    async def a2a_v1_get_task(task_id: str, request: Request) -> JSONResponse:
        """Retrieve task status by ID (A2A v1 REST binding).

        No ``agent_name`` in the path — looks up by ``task_id`` only.
        """
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            return http_bad_request(
                "Malformed task_id",
                reason="INVALID_ARGUMENT",
                metadata={"taskId": task_id},
            )

        task = await task_store.get_task(task_uuid)
        if task is None:
            return http_task_not_found(task_id)

        return JSONResponse(
            content=_task_to_a2a_v1(task),
            headers=_v1_headers(),
        )

    # -- CancelTask (A2A v1 Section 11) --------------------------------------
    @app.post("/tasks/{task_id}:cancel", tags=["a2a-v1", "tasks"])
    async def a2a_v1_cancel_task(task_id: str, request: Request) -> JSONResponse:
        """Cancel a running task (A2A v1 REST binding)."""
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            return http_bad_request(
                "Malformed task_id",
                reason="INVALID_ARGUMENT",
                metadata={"taskId": task_id},
            )

        task = await task_store.get_task(task_uuid)
        if task is None:
            return http_task_not_found(task_id)

        await task_store.mark_cancelled(task_uuid, reason="cancelled via A2A v1")

        refreshed = await task_store.get_task(task_uuid)
        if refreshed is None:
            refreshed = task

        return JSONResponse(
            content=_task_to_a2a_v1(refreshed),
            headers=_v1_headers(),
        )


# =============================================================================
# A2A v1.0.0 JSON-RPC 2.0 Binding (Spec Section 9)
# =============================================================================


def register_a2a_v1_jsonrpc_route(app: FastAPI, ctx: dict) -> None:
    """Register the ``POST /a2a/v1`` JSON-RPC 2.0 endpoint.

    All seven A2A methods are dispatched through this single route.
    Streaming methods respond with ``Content-Type: text/event-stream``;
    non-streaming methods respond with ``Content-Type: application/json``.

    Args:
        app: The FastAPI application instance.
        ctx: Shared context dict from ``create_app()``.
    """
    task_store = ctx["task_store"]
    task_executor = ctx["task_executor"]
    _require_enabled_agent = ctx["require_enabled_agent"]
    _require_session = ctx["require_session"]
    _resolve_a2a_thread = ctx["resolve_a2a_thread"]
    _extract_subscriber_endpoints = ctx["extract_subscriber_endpoints"]
    _thread_response_headers = ctx["thread_response_headers"]

    from .a2a_jsonrpc import (
        JsonRpcError,
        JsonRpcRequest,
        a2a_error_data,
        internal_error,
        jsonrpc_error,
        jsonrpc_sse_event,
        jsonrpc_success,
        parse_jsonrpc_request,
        task_not_found_error,
        unsupported_operation_error,
        JSONRPC_INTERNAL_ERROR,
        JSONRPC_INVALID_PARAMS,
        JSONRPC_PARSE_ERROR,
    )

    def _v1_headers(thread=None):
        th = _thread_response_headers(thread) if thread is not None else None
        return _a2a_v1_response_headers(th)

    async def _handle_send_message(
        rpc: JsonRpcRequest, request: Request,
    ) -> dict:
        """Dispatch ``SendMessage`` — returns a JSON-RPC success with Task."""
        agent_name = A2A_V1_AGENT_NAME
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)

        body = rpc.params
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

        return jsonrpc_success(rpc.id, {"task": _task_to_a2a_v1(task)})

    async def _handle_get_task(rpc: JsonRpcRequest) -> dict:
        """Dispatch ``GetTask`` — returns a JSON-RPC success with Task."""
        task_id = rpc.params.get("id")
        if not isinstance(task_id, str) or not task_id:
            return jsonrpc_error(
                rpc.id,
                JSONRPC_INVALID_PARAMS,
                "Invalid parameters",
                a2a_error_data("INVALID_PARAMS", metadata={
                    "detail": "Field 'params.id' is required and must be a string",
                }),
            )
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            return jsonrpc_error(
                rpc.id,
                JSONRPC_INVALID_PARAMS,
                "Invalid parameters",
                a2a_error_data("INVALID_PARAMS", metadata={
                    "detail": f"Malformed task id: {task_id}",
                }),
            )
        task = await task_store.get_task(task_uuid)
        if task is None:
            return task_not_found_error(rpc.id, task_id)
        return jsonrpc_success(rpc.id, {"task": _task_to_a2a_v1(task)})

    async def _handle_cancel_task(rpc: JsonRpcRequest) -> dict:
        """Dispatch ``CancelTask`` — cancels and returns updated Task."""
        task_id = rpc.params.get("id")
        if not isinstance(task_id, str) or not task_id:
            return jsonrpc_error(
                rpc.id,
                JSONRPC_INVALID_PARAMS,
                "Invalid parameters",
                a2a_error_data("INVALID_PARAMS", metadata={
                    "detail": "Field 'params.id' is required and must be a string",
                }),
            )
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            return jsonrpc_error(
                rpc.id,
                JSONRPC_INVALID_PARAMS,
                "Invalid parameters",
                a2a_error_data("INVALID_PARAMS", metadata={
                    "detail": f"Malformed task id: {task_id}",
                }),
            )
        task = await task_store.get_task(task_uuid)
        if task is None:
            return task_not_found_error(rpc.id, task_id)

        await task_store.mark_cancelled(task_uuid, reason="cancelled via A2A v1 JSON-RPC")
        refreshed = await task_store.get_task(task_uuid)
        if refreshed is None:
            refreshed = task
        return jsonrpc_success(rpc.id, {"task": _task_to_a2a_v1(refreshed)})

    async def _stream_send_message(
        rpc: JsonRpcRequest, request: Request,
    ):
        """Dispatch ``SendStreamingMessage`` — yields JSON-RPC-wrapped SSE."""
        agent_name = A2A_V1_AGENT_NAME
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)

        body = rpc.params
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

        async def _sse():
            yield {
                "event": "task",
                "data": jsonrpc_sse_event(rpc.id, {"task": _task_to_a2a_v1(task)}),
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
                        "data": jsonrpc_sse_event(
                            rpc.id,
                            {"statusUpdate": _task_event_to_a2a_v1_sse(event)},
                        ),
                    }
                    if event.status in task_store.TERMINAL_STATUSES:
                        break
            finally:
                await task_executor.unsubscribe(task.task_id, queue)

        return EventSourceResponse(
            _sse(), headers=_v1_headers(thread),
        )

    async def _stream_subscribe_to_task(
        rpc: JsonRpcRequest, request: Request,
    ):
        """Dispatch ``SubscribeToTask`` — SSE for an existing task."""
        task_id = rpc.params.get("id")
        if not isinstance(task_id, str) or not task_id:
            return JSONResponse(
                content=jsonrpc_error(
                    rpc.id,
                    JSONRPC_INVALID_PARAMS,
                    "Invalid parameters",
                    a2a_error_data("INVALID_PARAMS", metadata={
                        "detail": "Field 'params.id' is required and must be a string",
                    }),
                ),
                headers=_v1_headers(),
            )
        try:
            task_uuid = UUID(task_id)
        except ValueError:
            return JSONResponse(
                content=jsonrpc_error(
                    rpc.id,
                    JSONRPC_INVALID_PARAMS,
                    "Invalid parameters",
                    a2a_error_data("INVALID_PARAMS", metadata={
                        "detail": f"Malformed task id: {task_id}",
                    }),
                ),
                headers=_v1_headers(),
            )

        task = await task_store.get_task(task_uuid)
        if task is None:
            return JSONResponse(
                content=task_not_found_error(rpc.id, task_id),
                headers=_v1_headers(),
            )

        if task.status in task_store.TERMINAL_STATUSES:
            return JSONResponse(
                content=unsupported_operation_error(
                    rpc.id,
                    "SubscribeToTask",
                    detail=f"Task is already in terminal state: {task.status}",
                ),
                headers=_v1_headers(),
            )

        queue = await task_executor.subscribe(task.task_id)

        async def _sse():
            yield {
                "event": "task",
                "data": jsonrpc_sse_event(rpc.id, {"task": _task_to_a2a_v1(task)}),
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
                        "data": jsonrpc_sse_event(
                            rpc.id,
                            {"statusUpdate": _task_event_to_a2a_v1_sse(event)},
                        ),
                    }
                    if event.status in task_store.TERMINAL_STATUSES:
                        break
            finally:
                await task_executor.unsubscribe(task.task_id, queue)

        return EventSourceResponse(
            _sse(), headers=_v1_headers(),
        )

    # -- Route handler --------------------------------------------------------
    @app.post("/a2a/v1", tags=["a2a-v1", "jsonrpc"])
    async def a2a_v1_jsonrpc(request: Request):
        """A2A v1.0.0 JSON-RPC 2.0 endpoint (spec Section 9).

        Dispatches all A2A methods through a single route. The method
        name is specified in the ``method`` field of the JSON-RPC request
        body. Streaming methods respond with ``text/event-stream``.
        """
        try:
            raw_body = await request.json()
        except Exception:
            return JSONResponse(
                content=jsonrpc_error(
                    None,
                    JSONRPC_PARSE_ERROR,
                    "Invalid JSON payload",
                ),
                headers=_v1_headers(),
            )

        try:
            rpc = parse_jsonrpc_request(raw_body)
        except JsonRpcError as exc:
            return JSONResponse(
                content=exc.response,
                headers=_v1_headers(),
            )

        try:
            method = rpc.method

            if method == "SendMessage":
                result = await _handle_send_message(rpc, request)
                return JSONResponse(
                    content=result,
                    status_code=202 if "result" in result else 200,
                    headers=_v1_headers(),
                )

            if method == "SendStreamingMessage":
                return await _stream_send_message(rpc, request)

            if method == "GetTask":
                result = await _handle_get_task(rpc)
                return JSONResponse(
                    content=result,
                    headers=_v1_headers(),
                )

            if method == "CancelTask":
                result = await _handle_cancel_task(rpc)
                return JSONResponse(
                    content=result,
                    headers=_v1_headers(),
                )

            if method == "SubscribeToTask":
                return await _stream_subscribe_to_task(rpc, request)

            if method == "ListTasks":
                return JSONResponse(
                    content=unsupported_operation_error(
                        rpc.id,
                        "ListTasks",
                        detail="Filtered task listing is not yet implemented",
                    ),
                    headers=_v1_headers(),
                )

            if method == "GetExtendedAgentCard":
                return JSONResponse(
                    content=unsupported_operation_error(
                        rpc.id,
                        "GetExtendedAgentCard",
                        detail="Extended agent cards are not configured",
                    ),
                    headers=_v1_headers(),
                )

        except HTTPException as exc:
            return JSONResponse(
                content=internal_error(rpc.id, exc.detail),
                status_code=exc.status_code,
                headers=_v1_headers(),
            )
        except Exception as exc:
            log.exception("JSON-RPC dispatch error for method=%s", rpc.method)
            return JSONResponse(
                content=internal_error(rpc.id, str(exc)),
                status_code=500,
                headers=_v1_headers(),
            )
