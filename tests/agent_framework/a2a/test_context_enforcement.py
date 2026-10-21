"""Required ``contextId`` and optional ``X-External-Thread-Id`` match."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
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
    _thread_response_headers,
    require_context_id,
    require_matching_external_thread_header,
)
from stores import task_store, thread_store


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    request = Request({
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/message:send",
        "raw_path": b"/message:send",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    })
    request.state.external_session_id = None
    request.state.session_id = None
    return request


def test_require_context_id_rejects_missing() -> None:
    with pytest.raises(ContextResolutionError, match="contextId is required"):
        require_context_id({"message": "flat legacy body"})


def test_require_context_id_rejects_blank() -> None:
    with pytest.raises(ContextResolutionError, match="contextId is required"):
        require_context_id({"_a2a_v1_context_id": "   "})


def test_require_context_id_persists_stripped_value() -> None:
    body = {"_a2a_v1_context_id": "  ctx-001  "}
    assert require_context_id(body) == "ctx-001"
    assert body["_a2a_v1_context_id"] == "ctx-001"


def test_require_context_id_reads_unnormalized_message() -> None:
    body = {"message": {"contextId": "ctx-raw", "role": "ROLE_USER", "parts": []}}
    assert require_context_id(body) == "ctx-raw"


def test_header_absent_is_ok() -> None:
    assert require_matching_external_thread_header(_request(), "ctx-001") is None


def test_header_match_is_ok() -> None:
    request = _request({"X-External-Thread-Id": "ctx-001"})
    assert require_matching_external_thread_header(request, "ctx-001") == "ctx-001"


def test_header_mismatch_is_rejected() -> None:
    request = _request({"X-External-Thread-Id": "other-thread"})
    with pytest.raises(ContextResolutionError, match="must match"):
        require_matching_external_thread_header(request, "ctx-001")


@pytest.mark.asyncio
async def test_resolve_uses_context_id_and_does_not_mint(monkeypatch) -> None:
    created: dict[str, str] = {}

    async def fake_get(external_thread_id: str):
        return None

    async def fake_create(**kwargs):
        created["external_thread_id"] = kwargs["external_thread_id"]
        return thread_store.AgentThread(
            thread_id="internal-thread-1",
            source="a2a_external",
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            external_thread_id=kwargs["external_thread_id"],
        )

    monkeypatch.setattr(thread_store, "get_by_external_thread_id", fake_get)
    monkeypatch.setattr(thread_store, "create_thread", fake_create)

    body = {"_a2a_v1_context_id": "ctx-client"}
    thread = await _resolve_a2a_thread(_request(), "orchestrator", body)
    assert created["external_thread_id"] == "ctx-client"
    assert thread.thread_id == "internal-thread-1"
    assert not created["external_thread_id"].startswith("auto-")


@pytest.mark.asyncio
async def test_resolve_reuses_existing_thread_for_same_context(monkeypatch) -> None:
    existing = thread_store.AgentThread(
        thread_id="internal-thread-9",
        source="a2a_external",
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        external_thread_id="ctx-reuse",
    )
    create_calls = {"n": 0}

    async def fake_get(external_thread_id: str):
        assert external_thread_id == "ctx-reuse"
        return existing

    async def fake_create(**kwargs):
        create_calls["n"] += 1
        raise AssertionError("must not create when the thread already exists")

    monkeypatch.setattr(thread_store, "get_by_external_thread_id", fake_get)
    monkeypatch.setattr(thread_store, "create_thread", fake_create)

    thread = await _resolve_a2a_thread(
        _request(), "orchestrator", {"_a2a_v1_context_id": "ctx-reuse"},
    )
    assert thread.thread_id == "internal-thread-9"
    assert create_calls["n"] == 0


def test_thread_response_headers_always_include_internal_thread_id() -> None:
    thread = SimpleNamespace(thread_id="tid-1", external_thread_id="ctx-001")
    headers = _thread_response_headers(thread)
    assert headers["X-Thread-Id"] == "tid-1"
    assert headers["X-External-Thread-Id"] == "ctx-001"


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
