"""A2A v1.0.0 JSON-RPC 2.0 binding route.

Registers POST /a2a/v1. Shared request logic lives in a2a.server.helpers
and a2a.server.utils.

Receives a ctx dict from a2a_server.create_app() so this module never
imports a2a_server directly.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from a2a.server.helpers.converters import (
    _payload_context_id,
    _task_to_a2a_v1,
    _yield_task_event_sse,
)
from a2a.server.helpers.sse import sse_event_closes_stream
from a2a.server.helpers.normalize import (
    IngressValidationError,
    _normalize_a2a_v1_body,
)
from a2a.server.helpers.wait import (
    return_immediately_from_payload,
    start_task_and_maybe_wait,
)
from a2a.server.utils.context import ContextResolutionError
from a2a.server.utils.headers import A2A_V1_AGENT_NAME, _a2a_v1_response_headers

log = logging.getLogger(__name__)


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

    from a2a.shared.jsonrpc import (
        JsonRpcError,
        JsonRpcRequest,
        a2a_error_data,
        internal_error,
        jsonrpc_error,
        jsonrpc_sse_event,
        jsonrpc_success,
        parse_jsonrpc_request,
        push_notification_not_supported_error,
        task_not_found_error,
        unsupported_operation_error,
        PUSH_NOTIFICATION_METHODS,
        JSONRPC_INTERNAL_ERROR,
        JSONRPC_INVALID_PARAMS,
        JSONRPC_PARSE_ERROR,
    )
    from a2a.server.helpers.resume import (
        RESUME_ERROR_INVALID_PARAMS,
        RESUME_ERROR_TASK_NOT_FOUND,
        RESUME_ERROR_UNSUPPORTED_OPERATION,
        RESUME_KIND_ERROR,
        RESUME_KIND_PASS_THROUGH,
        RESUME_KIND_RESUMED,
        ResumeResult,
        try_handle_resume,
    )

    def _v1_headers(thread=None):
        th = _thread_response_headers(thread) if thread is not None else None
        return _a2a_v1_response_headers(th)

    def _resume_error_to_jsonrpc_response(
        result: ResumeResult, rpc_id,
    ) -> dict:
        """Translate a resume ``error`` outcome into a JSON-RPC error dict.

        Maps the resume helper's string error codes to the JSON-RPC
        error builders:

          * ``TASK_NOT_FOUND`` → ``task_not_found_error``
          * ``UNSUPPORTED_OPERATION`` → ``unsupported_operation_error``
          * ``INVALID_PARAMS`` → ``jsonrpc_error`` with
            ``JSONRPC_INVALID_PARAMS``
        """
        metadata = {
            k: str(v) for k, v in (result.error_metadata or {}).items()
            if v is not None
        }
        if result.error_code == RESUME_ERROR_TASK_NOT_FOUND:
            task_id = metadata.get("taskId", "")
            return task_not_found_error(rpc_id, task_id)
        if result.error_code == RESUME_ERROR_UNSUPPORTED_OPERATION:
            detail = metadata.get("detail", result.error_message or "")
            return unsupported_operation_error(
                rpc_id, "message/send", detail=detail,
            )
        # Default: INVALID_PARAMS
        return jsonrpc_error(
            rpc_id,
            JSONRPC_INVALID_PARAMS,
            "Invalid parameters",
            a2a_error_data("INVALID_PARAMS", metadata=metadata),
        )

    def _context_error_to_jsonrpc(
        exc: IngressValidationError | ContextResolutionError, rpc_id,
    ) -> dict:
        return jsonrpc_error(
            rpc_id,
            JSONRPC_INVALID_PARAMS,
            "Invalid parameters",
            a2a_error_data("INVALID_PARAMS", metadata=exc.metadata),
        )

    async def _handle_send_message(
        rpc: JsonRpcRequest, request: Request,
    ) -> dict:
        """Dispatch ``SendMessage`` — returns a JSON-RPC success with Task."""
        agent_name = A2A_V1_AGENT_NAME
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)

        body = rpc.params
        try:
            body = _normalize_a2a_v1_body(body)
            thread = await _resolve_a2a_thread(request, agent_name, body)
        except (IngressValidationError, ContextResolutionError) as exc:
            return _context_error_to_jsonrpc(exc, rpc.id)
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }
        body.setdefault("metadata", {})["request_mode"] = "send"

        # A2A §6.3 resume check — if the inbound envelope carried
        # message.taskId, treat it as a follow-up to an existing
        # input_required task rather than minting a new one.
        resume = await try_handle_resume(body)
        if resume.kind == RESUME_KIND_ERROR:
            return _resume_error_to_jsonrpc_response(resume, rpc.id)
        immediate = return_immediately_from_payload(body)
        if resume.kind == RESUME_KIND_RESUMED:
            resumed_task = await start_task_and_maybe_wait(
                resume.task, task_executor, immediate,
            )
            return jsonrpc_success(
                rpc.id, {"task": _task_to_a2a_v1(resumed_task)},
            )

        task = await task_store.create_task(
            session_id=session_id,
            external_session_id=getattr(request.state, "external_session_id", None),
            agent_name=agent_name,
            payload=body,
            test_run_id=body.get("test_run_id"),
            thread_id=thread.thread_id,
            subscriber_endpoints=_extract_subscriber_endpoints(body),
        )
        task = await start_task_and_maybe_wait(task, task_executor, immediate)
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
        return jsonrpc_success(rpc.id, _task_to_a2a_v1(task))

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
        return jsonrpc_success(rpc.id, _task_to_a2a_v1(refreshed))

    async def _stream_send_message(
        rpc: JsonRpcRequest, request: Request,
    ):
        """Dispatch ``SendStreamingMessage`` — yields JSON-RPC-wrapped SSE."""
        agent_name = A2A_V1_AGENT_NAME
        await _require_enabled_agent(agent_name)
        session_id = _require_session(request)

        body = rpc.params
        try:
            body = _normalize_a2a_v1_body(body)
            thread = await _resolve_a2a_thread(request, agent_name, body)
        except (IngressValidationError, ContextResolutionError) as exc:
            return JSONResponse(
                content=_context_error_to_jsonrpc(exc, rpc.id),
                headers=_v1_headers(),
            )
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }
        body.setdefault("metadata", {})["request_mode"] = "streaming"

        # A2A §6.3 resume check — same classifier as send; the SSE
        # subscription + re-enqueue happens after the resume validates.
        resume = await try_handle_resume(body)
        if resume.kind == RESUME_KIND_ERROR:
            return JSONResponse(
                content=_resume_error_to_jsonrpc_response(resume, rpc.id),
                headers=_v1_headers(thread),
            )

        if resume.kind == RESUME_KIND_RESUMED:
            task = resume.task
        else:
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
            context_id = _payload_context_id(task.payload)
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
                    for frame in _yield_task_event_sse(
                        event, context_id=context_id, jsonrpc_id=rpc.id,
                    ):
                        yield frame
                    if sse_event_closes_stream(event, task.task_id):
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

        context_id = _payload_context_id(task.payload)
        if context_id is None:
            return JSONResponse(
                content=jsonrpc_error(
                    rpc.id,
                    JSONRPC_INVALID_PARAMS,
                    "Invalid parameters",
                    a2a_error_data("INVALID_PARAMS", metadata={
                        "detail": "Task has no contextId",
                        "taskId": task_id,
                    }),
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
                    for frame in _yield_task_event_sse(
                        event, context_id=context_id, jsonrpc_id=rpc.id,
                    ):
                        yield frame
                    if sse_event_closes_stream(event, task.task_id):
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

            if method in PUSH_NOTIFICATION_METHODS:
                return JSONResponse(
                    content=push_notification_not_supported_error(rpc.id, method),
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
