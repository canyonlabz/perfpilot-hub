"""Tests for the per-request CopilotKit orchestrator mount.

Confirms the server module does not import the Classic AG2 package, and
that one ``/copilotkit`` turn builds an orchestrator, passes the replaced
transcript plus a delegation observer to ``dispatch``, and closes both
the stream and the agent.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request


_CLASSIC_PACKAGE = "auto" + "gen"


def test_agui_server_import_skips_the_classic_package() -> None:
    assert _CLASSIC_PACKAGE not in sys.modules
    import agui_server

    assert _CLASSIC_PACKAGE not in sys.modules
    assert "AGUIStream" in agui_server._mount_copilotkit.__code__.co_names
    assert _CLASSIC_PACKAGE not in agui_server._mount_copilotkit.__code__.co_names


@pytest.mark.asyncio
async def test_copilotkit_observer_calls_the_web_drain() -> None:
    import agui_server

    calls = {"n": 0}

    def _drain() -> None:
        calls["n"] += 1

    observer = agui_server._copilotkit_delegation_observer(_drain)
    await observer.process([], None)

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_copilotkit_request_builds_and_closes_one_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agui_server
    from services.helpers.webui_resolve import WebUIResolveResult

    agent = object()
    closed: list[Any] = []
    created: dict[str, Any] = {}

    class _Stream:
        def __init__(self, bound: Any) -> None:
            self.agent = bound
            self.closed = False
            self.incoming = None
            self.kwargs: dict[str, Any] = {}
            created["stream"] = self

        async def dispatch(self, incoming: Any, **kwargs: Any):
            self.incoming = incoming
            self.kwargs = kwargs
            yield 'data: {"type": "TEXT_MESSAGE_CONTENT", "delta": "ok"}\n\n'

        async def aclose(self) -> None:
            self.closed = True

    async def _close(target: Any) -> None:
        closed.append(target)

    monkeypatch.setattr(agui_server.thread_store, "get_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(
        agui_server.thread_store,
        "create_thread",
        AsyncMock(return_value=SimpleNamespace(user_id="user-1")),
    )
    monkeypatch.setattr(
        agui_server.conversation_store,
        "list_for_thread",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(agui_server.conversation_store, "append_message", AsyncMock())
    monkeypatch.setattr(agui_server.thread_store, "touch_thread", AsyncMock())
    monkeypatch.setattr(
        agui_server.task_store,
        "list_tasks_for_thread",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(agui_server, "_apply_active_project_context", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "services.helpers.webui_resolve.resolve_test_run_id_for_webui",
        AsyncMock(return_value=WebUIResolveResult(test_run_id=None)),
    )
    monkeypatch.setattr(
        "services.mcp_client.build_webui_secrets_hint",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("agents.orchestrator.agent.build_orchestrator", lambda: agent)
    monkeypatch.setattr("agents.orchestrator.agent.drain_pending_executions", lambda: [])
    monkeypatch.setattr("agents.orchestrator.agent.set_caller_identity", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.set_caller_surface", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.set_known_test_run_ids", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.set_project_context", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.clear_caller_identity", MagicMock())
    monkeypatch.setattr("core.base_agent.aclose_agent", _close)

    raw = json.dumps({
        "threadId": "thread-1",
        "runId": "run-1",
        "messages": [{"id": "m1", "role": "user", "content": "hello"}],
    }).encode()

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/copilotkit/",
        "raw_path": b"/copilotkit/",
        "query_string": b"",
        "headers": [(b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    request = Request(scope, _receive)
    request.state.user_id = "user-1"
    request.state.session_id = "session-1"

    endpoint_cls = agui_server._build_history_aware_copilotkit_endpoint(_Stream)
    endpoint = endpoint_cls(scope, _receive, AsyncMock())
    response = await endpoint.post(request)
    chunks = [chunk async for chunk in response.body_iterator]

    stream = created["stream"]
    assert stream.agent is agent
    assert stream.closed is True
    assert closed == [agent]
    assert stream.kwargs["observers"]
    assert len(stream.kwargs["observers"]) == 1
    roles = [message.role for message in stream.incoming.messages]
    assert "user" in roles
    assert any(getattr(message, "content", None) == "hello" for message in stream.incoming.messages)
    assert chunks


@pytest.mark.asyncio
async def test_disabled_orchestrator_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agui_server
    from fastapi import HTTPException
    from services.helpers.webui_resolve import WebUIResolveResult

    opened: list[Any] = []

    class _Stream:
        def __init__(self, agent: Any) -> None:
            opened.append(agent)

    monkeypatch.setattr(agui_server.thread_store, "get_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(
        agui_server.thread_store,
        "create_thread",
        AsyncMock(return_value=SimpleNamespace(user_id="user-1")),
    )
    monkeypatch.setattr(
        agui_server.conversation_store,
        "list_for_thread",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(agui_server, "_apply_active_project_context", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "services.helpers.webui_resolve.resolve_test_run_id_for_webui",
        AsyncMock(return_value=WebUIResolveResult(test_run_id=None)),
    )
    monkeypatch.setattr(
        "services.mcp_client.build_webui_secrets_hint",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "agents.orchestrator.agent.build_orchestrator",
        lambda: {"status": "disabled"},
    )
    monkeypatch.setattr("agents.orchestrator.agent.set_caller_identity", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.set_caller_surface", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.set_known_test_run_ids", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.set_project_context", MagicMock())
    monkeypatch.setattr("agents.orchestrator.agent.clear_caller_identity", MagicMock())

    raw = json.dumps({
        "threadId": "thread-1",
        "runId": "run-1",
        "messages": [],
    }).encode()

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/copilotkit/",
        "raw_path": b"/copilotkit/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    request = Request(scope, _receive)
    request.state.user_id = "user-1"

    endpoint_cls = agui_server._build_history_aware_copilotkit_endpoint(_Stream)
    endpoint = endpoint_cls(scope, _receive, AsyncMock())

    with pytest.raises(HTTPException) as exc_info:
        await endpoint.post(request)

    assert exc_info.value.status_code == 503
    assert opened == []
