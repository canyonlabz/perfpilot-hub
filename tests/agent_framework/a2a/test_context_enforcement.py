"""A2A conversation resolution: ``contextId`` is the internal ``thread_id``.

``contextId`` is optional. Absent -> stateless (no thread, no persistence);
present -> stateful, user-scoped thread whose ``thread_id`` equals the
``contextId``. The server never mints a conversation id, and a ``contextId``
owned by another user is rejected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from starlette.requests import Request

from a2a.server.helpers.converters import (
    _context_id_from_task_or_event,
    _task_to_a2a_v1,
)
from a2a.server.utils.context import (
    ContextResolutionError,
    _resolve_a2a_thread,
    context_id_from_payload,
)
from a2a.server.utils.headers import v1_response_headers
from stores import task_store, thread_store


def _request(user_id: str | None = "user-1") -> Request:
    request = Request({
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/message:send",
        "raw_path": b"/message:send",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    })
    request.state.external_session_id = None
    request.state.session_id = None
    request.state.user_id = user_id
    return request


def _thread(thread_id: str, user_id: str | None) -> thread_store.AgentThread:
    now = datetime.now(timezone.utc)
    return thread_store.AgentThread(
        thread_id=thread_id,
        source="a2a_external",
        status="active",
        created_at=now,
        updated_at=now,
        user_id=user_id,
    )


# -- context_id_from_payload --------------------------------------------------

def test_context_id_from_payload_reads_normalized_value() -> None:
    assert context_id_from_payload({"_a2a_v1_context_id": "  ctx-1  "}) == "ctx-1"


def test_context_id_from_payload_reads_unnormalized_message() -> None:
    body = {"message": {"contextId": "ctx-raw", "role": "ROLE_USER", "parts": []}}
    assert context_id_from_payload(body) == "ctx-raw"


def test_context_id_from_payload_none_when_absent_or_blank() -> None:
    assert context_id_from_payload({"message": "flat legacy body"}) is None
    assert context_id_from_payload({"_a2a_v1_context_id": "   "}) is None


# -- _resolve_a2a_thread ------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_context_id(monkeypatch) -> None:
    """A stateless request (no contextId) resolves to no thread."""

    async def fail(*args, **kwargs):
        raise AssertionError("must not touch the store for a stateless request")

    monkeypatch.setattr(thread_store, "get_thread", fail)
    monkeypatch.setattr(thread_store, "create_thread", fail)

    thread = await _resolve_a2a_thread(_request(), "orchestrator", {})
    assert thread is None


@pytest.mark.asyncio
async def test_resolve_uses_context_id_as_thread_id_and_does_not_mint(
    monkeypatch,
) -> None:
    created: dict[str, object] = {}

    async def fake_get(thread_id: str):
        return None

    async def fake_create(**kwargs):
        created.update(kwargs)
        return _thread(kwargs["thread_id"], kwargs["user_id"])

    monkeypatch.setattr(thread_store, "get_thread", fake_get)
    monkeypatch.setattr(thread_store, "create_thread", fake_create)

    body = {"_a2a_v1_context_id": "ctx-client"}
    thread = await _resolve_a2a_thread(_request("user-1"), "orchestrator", body)
    # The internal thread_id *is* the client contextId — never minted.
    assert created["thread_id"] == "ctx-client"
    assert created["user_id"] == "user-1"
    assert thread.thread_id == "ctx-client"
    assert thread.user_id == "user-1"


@pytest.mark.asyncio
async def test_resolve_reuses_existing_thread_for_same_context(monkeypatch) -> None:
    existing = _thread("ctx-reuse", "user-1")

    async def fake_get(thread_id: str):
        assert thread_id == "ctx-reuse"
        return existing

    async def fake_create(**kwargs):
        raise AssertionError("must not create when the thread already exists")

    monkeypatch.setattr(thread_store, "get_thread", fake_get)
    monkeypatch.setattr(thread_store, "create_thread", fake_create)

    thread = await _resolve_a2a_thread(
        _request("user-1"), "orchestrator", {"_a2a_v1_context_id": "ctx-reuse"},
    )
    assert thread is existing


@pytest.mark.asyncio
async def test_resolve_rejects_context_id_owned_by_another_user(monkeypatch) -> None:
    existing = _thread("ctx-shared", "someone-else")

    async def fake_get(thread_id: str):
        return existing

    monkeypatch.setattr(thread_store, "get_thread", fake_get)

    with pytest.raises(ContextResolutionError, match="another user"):
        await _resolve_a2a_thread(
            _request("user-1"), "orchestrator",
            {"_a2a_v1_context_id": "ctx-shared"},
        )


# -- headers ------------------------------------------------------------------

def test_v1_response_headers_only_version() -> None:
    headers = v1_response_headers()
    assert headers == {"A2A-Version": "1.0"}
    assert "X-Thread-Id" not in headers
    assert "X-External-Thread-Id" not in headers


# -- converters ---------------------------------------------------------------

def test_task_converter_echoes_context_id() -> None:
    now = datetime.now(timezone.utc)
    task = task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="pending",
        payload={"_a2a_v1_context_id": "ctx-echo"},
        submitted_at=now,
        updated_at=now,
    )
    dumped = _task_to_a2a_v1(task)
    assert dumped["contextId"] == "ctx-echo"


def test_task_converter_omits_blank_context_id() -> None:
    now = datetime.now(timezone.utc)
    task = task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="pending",
        payload={"_a2a_v1_context_id": ""},
        submitted_at=now,
        updated_at=now,
    )
    dumped = _task_to_a2a_v1(task)
    assert "contextId" not in dumped


def test_event_context_id_never_empty_string() -> None:
    with pytest.raises(ValueError, match="contextId is required"):
        _context_id_from_task_or_event(context_id="")
    assert _context_id_from_task_or_event(context_id="ctx-1") == "ctx-1"
