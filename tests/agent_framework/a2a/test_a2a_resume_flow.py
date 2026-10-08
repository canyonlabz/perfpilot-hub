"""Tests for the A2A §6.3 multi-turn resume flow.

Covers two layers:

  1. ``a2a.server.v1_helpers._normalize_a2a_v1_body`` — must lift
     ``message.taskId`` from the inbound envelope to the top-level
     ``_a2a_v1_resume_task_id`` field so ingress handlers can detect
     the resume signal.
  2. ``a2a.server.v1_resume.try_handle_resume`` — the classifier that
     validates the resume request against the stored task row, appends
     the follow-up message, and returns a discriminated
     ``ResumeResult`` for the ingress handlers to translate.

The classifier is backed by ``stores.task_store`` (``get_task`` +
``append_resume_message``), which are monkeypatched to in-memory fakes
so these tests run DB-free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from a2a.server.v1_helpers import _normalize_a2a_v1_body
from a2a.server.v1_resume import (
    RESUME_ERROR_INVALID_PARAMS,
    RESUME_ERROR_TASK_NOT_FOUND,
    RESUME_ERROR_UNSUPPORTED_OPERATION,
    RESUME_KIND_ERROR,
    RESUME_KIND_PASS_THROUGH,
    RESUME_KIND_RESUMED,
    try_handle_resume,
)
from stores import task_store


# =============================================================================
# Fake task_store backing for try_handle_resume
# =============================================================================
# ``try_handle_resume`` interacts with task_store through two entry points
# (``get_task`` and ``append_resume_message``). We replace both with fakes
# that mirror asyncpg behaviour (``append_resume_message`` returns bool,
# ``get_task`` returns Optional[AgentTask]) without needing a running
# database.


class _FakeStore:
    """Mutable in-memory substitute for a slice of ``stores.task_store``.

    Only the subset of behaviours needed by ``try_handle_resume`` is
    modelled. The ``append_calls`` list records every call to
    ``append_resume_message`` so tests can assert on arguments.
    """

    def __init__(self) -> None:
        # Keyed by UUID; value is the task_store.AgentTask-like dict.
        self._tasks: dict[UUID, task_store.AgentTask] = {}
        self.append_calls: list[tuple[UUID, dict]] = []
        # If set, ``append_resume_message`` returns this value instead
        # of the realistic condition-based outcome (used by the race
        # test to simulate a concurrent transition).
        self.force_append_result: Optional[bool] = None

    def add_task(self, task: task_store.AgentTask) -> None:
        self._tasks[task.task_id] = task

    async def get_task(self, task_id: UUID) -> Optional[task_store.AgentTask]:
        return self._tasks.get(task_id)

    async def append_resume_message(
        self, task_id: UUID, message_part: dict,
    ) -> bool:
        self.append_calls.append((task_id, message_part))
        if self.force_append_result is not None:
            return self.force_append_result
        task = self._tasks.get(task_id)
        if task is None or task.status != "input_required":
            return False
        # Mutate the fake row to mirror the SQL side-effects:
        #   - status flipped to running
        #   - result cleared
        #   - resume_messages array appended on payload
        payload = dict(task.payload or {})
        existing = list(payload.get("resume_messages") or [])
        existing.append(message_part)
        payload["resume_messages"] = existing
        updated = task_store.AgentTask(
            task_id=task.task_id,
            session_id=task.session_id,
            agent_name=task.agent_name,
            status="running",
            payload=payload,
            submitted_at=task.submitted_at,
            updated_at=datetime.now(timezone.utc),
            external_session_id=task.external_session_id,
            test_run_id=task.test_run_id,
            thread_id=task.thread_id,
            result=None,
            error=None,
            subscriber_endpoints=task.subscriber_endpoints,
        )
        self._tasks[task_id] = updated
        return True


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    """Monkeypatch ``stores.task_store.get_task`` + ``append_resume_message``.

    Also patches the names re-imported into the ``a2a.server.v1_resume``
    module namespace so the classifier sees the fakes.
    """
    import a2a.server.v1_resume as v1_resume_mod

    store = _FakeStore()
    monkeypatch.setattr(task_store, "get_task", store.get_task)
    monkeypatch.setattr(
        task_store, "append_resume_message", store.append_resume_message,
    )
    # The resume module imports ``task_store`` as a module reference,
    # so patching the attribute on the module itself is sufficient. No
    # second monkeypatch needed on v1_resume_mod.
    _ = v1_resume_mod
    return store


def _make_task(
    *,
    task_id: Optional[UUID] = None,
    status: str = "input_required",
    context_id: Optional[str] = "ctx-001",
    payload_extra: Optional[dict[str, Any]] = None,
    thread_id: Optional[str] = "thread-abc",
) -> task_store.AgentTask:
    """Fabricate an ``AgentTask`` for the classifier to look up."""
    payload: dict[str, Any] = {}
    if context_id:
        payload["_a2a_v1_context_id"] = context_id
    if payload_extra:
        payload.update(payload_extra)
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=task_id or uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status=status,
        payload=payload,
        submitted_at=now,
        updated_at=now,
        external_session_id="ext-session",
        test_run_id=None,
        thread_id=thread_id,
        result=None,
        error=None,
        subscriber_endpoints=[],
    )


# =============================================================================
# _normalize_a2a_v1_body — resume taskId lift
# =============================================================================


def test_resume_message_lifted_from_v1_envelope_during_normalization() -> None:
    """``_normalize_a2a_v1_body`` must lift ``message.taskId`` to
    ``_a2a_v1_resume_task_id`` so ingress handlers can detect the A2A
    §6.3 resume signal before ``create_task``."""
    envelope = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "Here is my test_run_id: 2026-10-07-14-00-00"}],
            "contextId": "ctx-001",
            "taskId": "11111111-2222-3333-4444-555555555555",
        },
    }
    normalized = _normalize_a2a_v1_body(envelope)
    assert normalized["_a2a_v1_resume_task_id"] == (
        "11111111-2222-3333-4444-555555555555"
    )
    assert normalized["_a2a_v1_context_id"] == "ctx-001"
    assert normalized["message"] == "Here is my test_run_id: 2026-10-07-14-00-00"


def test_normalize_v1_body_omits_resume_task_id_when_absent() -> None:
    """Legacy A2A v1 envelopes without ``message.taskId`` must not inject
    an empty ``_a2a_v1_resume_task_id`` — the resume classifier short-
    circuits on absence, so an empty-string sentinel would mis-fire."""
    envelope = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "fresh task"}],
            "contextId": "ctx-999",
        },
    }
    normalized = _normalize_a2a_v1_body(envelope)
    assert "_a2a_v1_resume_task_id" not in normalized


def test_normalize_v1_body_strips_whitespace_only_task_id() -> None:
    """Whitespace-only ``message.taskId`` is treated as absent — the
    resume path should not engage for malformed envelopes."""
    envelope = {
        "message": {
            "role": "ROLE_USER",
            "parts": [{"text": "x"}],
            "contextId": "c",
            "taskId": "   ",
        },
    }
    normalized = _normalize_a2a_v1_body(envelope)
    assert "_a2a_v1_resume_task_id" not in normalized


# =============================================================================
# try_handle_resume — pass_through outcome
# =============================================================================


async def test_resume_pass_through_when_no_resume_task_id(
    fake_store: _FakeStore,
) -> None:
    """A body with no ``_a2a_v1_resume_task_id`` returns ``pass_through``
    so the caller continues with the standard new-task flow."""
    body = {"message": "hello", "parts": []}
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_PASS_THROUGH
    assert fake_store.append_calls == []


# =============================================================================
# try_handle_resume — error outcomes
# =============================================================================


async def test_resume_lookup_task_not_found_returns_a2a_error(
    fake_store: _FakeStore,
) -> None:
    """When ``_a2a_v1_resume_task_id`` references a task not in the DB,
    the classifier returns an ``error`` outcome tagged ``TASK_NOT_FOUND``
    (HTTP 404). This maps to A2A ``TaskNotFoundError`` (code -32001)."""
    body = {
        "message": "resume text",
        "_a2a_v1_resume_task_id": str(uuid4()),
        "_a2a_v1_context_id": "ctx-001",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_ERROR
    assert result.error_code == RESUME_ERROR_TASK_NOT_FOUND
    assert result.http_status == 404
    assert result.error_metadata is not None
    assert "taskId" in result.error_metadata
    assert fake_store.append_calls == []


async def test_resume_task_not_in_input_required_state_returns_a2a_error(
    fake_store: _FakeStore,
) -> None:
    """When the stored task's status is not ``input_required``, the
    classifier returns ``UNSUPPORTED_OPERATION`` with the current state
    on ``error_metadata`` so clients can diagnose the mismatch."""
    task = _make_task(status="completed")
    fake_store.add_task(task)

    body = {
        "message": "resume text",
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-001",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_ERROR
    assert result.error_code == RESUME_ERROR_UNSUPPORTED_OPERATION
    assert result.http_status == 400
    assert result.error_metadata is not None
    assert result.error_metadata.get("currentState") == "completed"
    assert fake_store.append_calls == []


async def test_resume_task_running_state_returns_unsupported_operation(
    fake_store: _FakeStore,
) -> None:
    """A task in ``running`` is not awaiting input; resume must error
    rather than racing with the active execution."""
    task = _make_task(status="running")
    fake_store.add_task(task)

    body = {
        "message": "early resume",
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-001",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_ERROR
    assert result.error_code == RESUME_ERROR_UNSUPPORTED_OPERATION
    assert result.error_metadata is not None
    assert result.error_metadata.get("currentState") == "running"


async def test_resume_mismatched_context_id_returns_a2a_error(
    fake_store: _FakeStore,
) -> None:
    """A2A §3.4.3: a resume request's ``contextId`` must match the
    task's stored ``contextId``. A mismatch is an ``INVALID_PARAMS``
    error ("Mismatching contextId and taskId")."""
    task = _make_task(status="input_required", context_id="ctx-correct")
    fake_store.add_task(task)

    body = {
        "message": "resume text",
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-wrong",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_ERROR
    assert result.error_code == RESUME_ERROR_INVALID_PARAMS
    assert result.http_status == 400
    assert result.error_metadata is not None
    assert "Mismatching" in result.error_metadata.get("detail", "")
    assert fake_store.append_calls == []


async def test_resume_malformed_uuid_returns_invalid_params(
    fake_store: _FakeStore,
) -> None:
    """A non-UUID ``_a2a_v1_resume_task_id`` is a client-side protocol
    error — surface ``INVALID_PARAMS`` without any DB round-trip."""
    body = {
        "message": "resume text",
        "_a2a_v1_resume_task_id": "not-a-uuid",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_ERROR
    assert result.error_code == RESUME_ERROR_INVALID_PARAMS
    assert fake_store.append_calls == []


async def test_resume_race_concurrent_append_returns_unsupported_operation(
    fake_store: _FakeStore,
) -> None:
    """``append_resume_message`` returning False (concurrent resume
    already won the race) surfaces as an ``UNSUPPORTED_OPERATION`` error
    rather than a false-success response."""
    task = _make_task(status="input_required", context_id="ctx-1")
    fake_store.add_task(task)
    fake_store.force_append_result = False

    body = {
        "message": "resume text",
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-1",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_ERROR
    assert result.error_code == RESUME_ERROR_UNSUPPORTED_OPERATION
    # The append call was still issued; it's the SQL that short-circuited.
    assert len(fake_store.append_calls) == 1


# =============================================================================
# try_handle_resume — success outcome
# =============================================================================


async def test_resume_appends_message_and_transitions_to_running(
    fake_store: _FakeStore,
) -> None:
    """Happy path: valid taskId + matching contextId + input_required
    status → ``resumed`` outcome with the task row reflecting status
    ``running`` and ``resume_messages`` appended on the payload."""
    task = _make_task(status="input_required", context_id="ctx-1")
    fake_store.add_task(task)

    body = {
        "message": "Here is my test_run_id: 2026-10-07-14-00-00",
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-1",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_RESUMED
    assert result.task is not None
    assert result.task.status == "running"

    # append_resume_message was called with a well-formed message_part.
    assert len(fake_store.append_calls) == 1
    _, message_part = fake_store.append_calls[0]
    assert message_part["role"] == "ROLE_USER"
    assert "received_at" in message_part
    assert message_part["message_text"] == (
        "Here is my test_run_id: 2026-10-07-14-00-00"
    )


async def test_resume_appends_parts_and_metadata_when_present(
    fake_store: _FakeStore,
) -> None:
    """When the resume body carries structured parts and metadata, both
    are persisted on the resume_message dict so the orchestrator can
    inspect them on the next pass."""
    task = _make_task(status="input_required", context_id="ctx-1")
    fake_store.add_task(task)

    body = {
        "message": "with parts",
        "parts": [{"text": "with parts"}, {"data": {"k": "v"}}],
        "metadata": {"test_run_id": "2026-10-07-14-00-00"},
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-1",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_RESUMED

    _, message_part = fake_store.append_calls[0]
    assert message_part["parts"] == body["parts"]
    assert message_part["metadata"] == {"test_run_id": "2026-10-07-14-00-00"}


async def test_resume_tolerates_missing_context_id_on_body(
    fake_store: _FakeStore,
) -> None:
    """When the resume body omits ``_a2a_v1_context_id``, the classifier
    does not reject — only an *explicit mismatch* is a §3.4.3 error.
    This matches the spec's "SHOULD match" wording and keeps clients
    that routinely omit the field (e.g. basic REST clients) working."""
    task = _make_task(status="input_required", context_id="ctx-1")
    fake_store.add_task(task)

    body = {
        "message": "no context id provided",
        "_a2a_v1_resume_task_id": str(task.task_id),
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_RESUMED


async def test_resume_tolerates_missing_context_id_on_task(
    fake_store: _FakeStore,
) -> None:
    """A task that was created without an ``_a2a_v1_context_id`` (legacy
    non-A2A ingress) can still be resumed; the classifier only fires
    the mismatch error when BOTH sides have a value."""
    task = _make_task(status="input_required", context_id=None)
    fake_store.add_task(task)

    body = {
        "message": "resume",
        "_a2a_v1_resume_task_id": str(task.task_id),
        "_a2a_v1_context_id": "ctx-anything",
    }
    result = await try_handle_resume(body)
    assert result.kind == RESUME_KIND_RESUMED


# =============================================================================
# Integration-ish: resume message reaches the resolver via the executor
# =============================================================================
# This test checks the executor helper ``_resume_message_to_text`` picks
# up ``message_text`` and (fallback) first-text part, mirroring the
# contract the resolver relies on when a client answers an
# ``input_required`` prompt with the missing ``test_run_id``.


def test_resume_message_text_contains_test_run_id_reaches_resolver() -> None:
    """``_resume_message_to_text`` returns the ``message_text`` so the
    orchestrator's effective_user_text (passed to the resolver) contains
    the test_run_id supplied in the resume turn — this is the critical
    behaviour that lets a REJECT outcome succeed on the next pass."""
    from services.task_executor import (
        _resume_message_to_text,
        _extract_resume_messages_from_payload,
    )

    payload = {
        "resume_messages": [
            {
                "role": "ROLE_USER",
                "message_text": (
                    "Use test_run_id 2026-10-07-14-00-00 for the analysis."
                ),
            },
        ],
    }
    messages = _extract_resume_messages_from_payload(payload)
    assert len(messages) == 1
    text = _resume_message_to_text(messages[0])
    assert text is not None
    assert "2026-10-07-14-00-00" in text


def test_resume_message_text_falls_back_to_first_text_part() -> None:
    """When a resume message carries only ``parts`` (no top-level
    ``message_text``), ``_resume_message_to_text`` returns the first
    text Part so the resolver still sees the content."""
    from services.task_executor import _resume_message_to_text

    rm = {
        "role": "ROLE_USER",
        "parts": [
            {"data": {"k": "v"}},
            {"text": "test_run_id = 2026-10-07-14-00-00"},
        ],
    }
    text = _resume_message_to_text(rm)
    assert text is not None
    assert "2026-10-07-14-00-00" in text


def test_resume_message_text_returns_none_for_empty_message() -> None:
    """A resume message with neither ``message_text`` nor text Parts
    returns ``None`` so the executor skips the empty turn rather than
    appending a blank user message to the LLM context."""
    from services.task_executor import _resume_message_to_text

    rm = {"role": "ROLE_USER", "parts": [{"data": {"k": "v"}}]}
    assert _resume_message_to_text(rm) is None


def test_extract_resume_messages_from_non_dict_payload_returns_empty() -> None:
    """Guard: a non-dict payload must not crash the executor; the
    extractor returns ``[]`` so the resume branch cleanly skips."""
    from services.task_executor import _extract_resume_messages_from_payload

    assert _extract_resume_messages_from_payload(None) == []
    assert _extract_resume_messages_from_payload("string") == []
    assert _extract_resume_messages_from_payload([1, 2, 3]) == []


def test_extract_resume_messages_from_payload_without_field_returns_empty() -> None:
    """Guard: a payload without ``resume_messages`` returns ``[]``."""
    from services.task_executor import _extract_resume_messages_from_payload

    assert _extract_resume_messages_from_payload({"message": "x"}) == []
