"""A2A v1.0.0 HTTP+JSON/REST binding routes.

Registers discovery plus message/task endpoints. Shared request logic
lives in ``a2a.server.helpers`` and ``a2a.server.utils``.

Receives a ``ctx`` dict from ``a2a_server.create_app()`` so this module
never imports ``a2a_server`` directly.
"""

from __future__ import annotations

import asyncio
import json
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
from a2a.server.helpers.normalize import _normalize_a2a_v1_body
from a2a.server.helpers.wait import (
    return_immediately_from_payload,
    start_task_and_maybe_wait,
)
from a2a.server.utils.context import ContextResolutionError
from a2a.server.utils.headers import (
    A2A_JSON_MEDIA_TYPE,
    A2A_V1_AGENT_NAME,
    _a2a_v1_response_headers,
)

log = logging.getLogger(__name__)


# =============================================================================
# A2A v1.0.0 HTTP+JSON/REST Binding (Spec Section 11)
# =============================================================================


def register_a2a_v1_routes(app: FastAPI, ctx: dict) -> None:
    """Register A2A v1.0.0 HTTP+JSON/REST binding routes.

    All routes implicitly target the orchestrator agent. Catalog
    discovery under ``/agents/{name}/.well-known/agent.json`` remains
    registered separately by ``a2a_server._register_routes``.

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

    from a2a.shared.errors import (
        http_bad_request,
        http_not_found,
        http_push_notification_not_supported,
        http_task_not_found,
        http_unsupported_operation,
        httpexception_to_a2a,
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

    def _rest_json(content: dict, *, status_code: int = 200, thread=None) -> JSONResponse:
        return JSONResponse(
            content=content,
            status_code=status_code,
            media_type=A2A_JSON_MEDIA_TYPE,
            headers=_v1_headers(thread),
        )

    def _resume_error_to_rest_response(
        result: ResumeResult, thread=None,
    ) -> JSONResponse:
        """Translate a resume ``error`` outcome into a REST A2A error.

        Maps the resume helper's string error codes to the REST
        ``google.rpc.Status`` builders:

          * ``TASK_NOT_FOUND`` â†’ ``http_task_not_found`` (404)
          * ``UNSUPPORTED_OPERATION`` â†’ ``http_bad_request``
            (400 with ``FAILED_PRECONDITION``-style reason)
          * ``INVALID_PARAMS`` â†’ ``http_bad_request`` (400)
        """
        metadata = {
            k: str(v) for k, v in (result.error_metadata or {}).items()
            if v is not None
        }
        if result.error_code == RESUME_ERROR_TASK_NOT_FOUND:
            task_id = metadata.get("taskId", "")
            return http_task_not_found(task_id)
        if result.error_code == RESUME_ERROR_UNSUPPORTED_OPERATION:
            return http_bad_request(
                result.error_message or "Unsupported operation",
                reason="UNSUPPORTED_OPERATION",
                metadata=metadata,
            )
        # Default: INVALID_PARAMS â†’ 400
        return http_bad_request(
            result.error_message or "Invalid parameters",
            reason="INVALID_ARGUMENT",
            metadata=metadata,
        )

    def _context_error_to_rest(exc: ContextResolutionError) -> JSONResponse:
        return http_bad_request(
            exc.detail,
            reason="INVALID_ARGUMENT",
            metadata=exc.metadata,
        )

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
        return _rest_json(card)

    # -- SendMessage (A2A v1 Section 11) -------------------------------------
    @app.post("/message:send", status_code=202, tags=["a2a-v1", "tasks"])
    async def a2a_v1_send_message(request: Request) -> JSONResponse:
        """Submit a message to the orchestrator (A2A v1 REST binding).

        Returns a ``SendMessageResponse`` wrapper ``{"task": Task}``.
        Blocking is the default (``returnImmediately`` omitted or false).
        """
        try:
            agent_name = A2A_V1_AGENT_NAME
            await _require_enabled_agent(agent_name)
            session_id = _require_session(request)
            body = await _read_json_body(request)
        except HTTPException as exc:
            return httpexception_to_a2a(exc.status_code, exc.detail)
        body = _normalize_a2a_v1_body(body)

        try:
            thread = await _resolve_a2a_thread(request, agent_name, body)
        except ContextResolutionError as exc:
            return _context_error_to_rest(exc)
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }
        body.setdefault("metadata", {})["request_mode"] = "send"

        # A2A Â§6.3 resume check â€” if the inbound envelope carried
        # message.taskId, treat it as a follow-up to an existing
        # input_required task rather than minting a new one.
        resume = await try_handle_resume(body)
        if resume.kind == RESUME_KIND_ERROR:
            return _resume_error_to_rest_response(resume, thread=thread)

        immediate = return_immediately_from_payload(body)
        if resume.kind == RESUME_KIND_RESUMED:
            resumed_task = await start_task_and_maybe_wait(
                resume.task, task_executor, immediate,
            )
            return _rest_json(
                {"task": _task_to_a2a_v1(resumed_task)},
                status_code=202 if immediate else 200,
                thread=thread,
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
        return _rest_json(
            {"task": _task_to_a2a_v1(task)},
            status_code=202 if immediate else 200,
            thread=thread,
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

        try:
            thread = await _resolve_a2a_thread(request, agent_name, body)
        except ContextResolutionError as exc:
            return _context_error_to_rest(exc)
        body["_perfpilot_thread"] = {
            "thread_id": thread.thread_id,
            "external_thread_id": thread.external_thread_id,
        }
        body.setdefault("metadata", {})["request_mode"] = "streaming"

        # A2A Â§6.3 resume check â€” same classifier as send; the SSE
        # subscription + re-enqueue happens after the resume validates.
        resume = await try_handle_resume(body)
        if resume.kind == RESUME_KIND_ERROR:
            return _resume_error_to_rest_response(resume, thread=thread)

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

        async def _stream():
            context_id = _payload_context_id(task.payload)
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
                    for frame in _yield_task_event_sse(
                        event, context_id=context_id,
                    ):
                        yield frame
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

        No ``agent_name`` in the path â€” looks up by ``task_id`` only.
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

        return _rest_json(_task_to_a2a_v1(task))

    # -- ListTasks (A2A v1 Section 11) ---------------------------------------
    @app.get("/tasks", tags=["a2a-v1", "tasks"])
    async def a2a_v1_list_tasks() -> JSONResponse:
        """List tasks (A2A REST binding). Not implemented yet."""
        return http_unsupported_operation(
            "ListTasks",
            detail="Filtered task listing is not yet implemented",
        )

    # -- SubscribeToTask (A2A v1 Section 11) ---------------------------------
    @app.get("/tasks/{task_id}:subscribe", tags=["a2a-v1", "tasks"])
    async def a2a_v1_subscribe_to_task(task_id: str, request: Request):
        """Subscribe to an existing task as SSE (A2A REST binding)."""
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

        if task.status in task_store.TERMINAL_STATUSES:
            return http_unsupported_operation(
                "SubscribeToTask",
                detail=f"Task is already in terminal state: {task.status}",
            )

        context_id = _payload_context_id(task.payload)
        if context_id is None:
            return http_bad_request(
                "Task has no contextId",
                reason="INVALID_ARGUMENT",
                metadata={"taskId": task_id},
            )

        queue = await task_executor.subscribe(task.task_id)

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
                    for frame in _yield_task_event_sse(
                        event, context_id=context_id,
                    ):
                        yield frame
                    if event.status in task_store.TERMINAL_STATUSES:
                        break
            finally:
                await task_executor.unsubscribe(task.task_id, queue)

        return EventSourceResponse(
            _stream(),
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

        return _rest_json(_task_to_a2a_v1(refreshed))

    # -- Push notifications (advertised as unsupported) ----------------------
    @app.post("/tasks/{task_id}/pushNotificationConfigs", tags=["a2a-v1", "push"])
    async def a2a_v1_create_push_config(task_id: str) -> JSONResponse:
        return http_push_notification_not_supported(
            "CreateTaskPushNotificationConfig",
        )

    @app.get("/tasks/{task_id}/pushNotificationConfigs", tags=["a2a-v1", "push"])
    async def a2a_v1_list_push_configs(task_id: str) -> JSONResponse:
        return http_push_notification_not_supported(
            "ListTaskPushNotificationConfigs",
        )

    @app.get(
        "/tasks/{task_id}/pushNotificationConfigs/{config_id}",
        tags=["a2a-v1", "push"],
    )
    async def a2a_v1_get_push_config(task_id: str, config_id: str) -> JSONResponse:
        return http_push_notification_not_supported(
            "GetTaskPushNotificationConfig",
        )

    @app.delete(
        "/tasks/{task_id}/pushNotificationConfigs/{config_id}",
        tags=["a2a-v1", "push"],
    )
    async def a2a_v1_delete_push_config(task_id: str, config_id: str) -> JSONResponse:
        return http_push_notification_not_supported(
            "DeleteTaskPushNotificationConfig",
        )

