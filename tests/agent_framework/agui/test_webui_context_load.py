"""Tests for the Web-UI next-turn context load.

Covers two helpers introduced to give the Web UI parity with the A2A
ingress resolver:

  * ``services.helpers.thread_prefetch.list_known_test_run_ids_for_thread``
    — prefetches distinct ``test_run_id`` values from prior tasks on a
    thread.
  * ``services.helpers.webui_resolve.resolve_test_run_id_for_webui`` —
    applies the two Web-UI policies on top of the pure classifier:
    implicit REUSE on single-candidate threads, and ask-user hint on
    multi-candidate threads.

Also pins the ``_caller_identity`` dict to carry ``known_test_run_ids``
(a new field consumed by ``delegate_to_specialist``) and that the
``set_known_test_run_ids`` setter preserves order while filtering
non-strings.

The tests do not require a running database. ``task_store.list_tasks_for_thread``
is monkeypatched to an in-memory fake so each test can control the
exact candidate list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from services.helpers.thread_prefetch import list_known_test_run_ids_for_thread
from services.helpers.webui_resolve import (
    WebUIResolveResult,
    resolve_test_run_id_for_webui,
)
from stores import task_store


# =============================================================================
# Fake task_store.list_tasks_for_thread backing
# =============================================================================
# The prefetch helper calls ``task_store.list_tasks_for_thread(thread_id)``
# and iterates the returned ``AgentTask`` rows. We fake the function so
# tests can hand it a tailored list of task rows with the exact
# ``test_run_id`` sequence we want to exercise.


class _FakeTaskList:
    """Mutable state for the monkeypatched ``list_tasks_for_thread``."""

    def __init__(self) -> None:
        self.by_thread: dict[str, list[task_store.AgentTask]] = {}
        self.raise_on_lookup: bool = False
        self.lookup_calls: list[tuple[str, int]] = []

    def set(self, thread_id: str, tasks: list[task_store.AgentTask]) -> None:
        self.by_thread[thread_id] = tasks

    async def list_tasks_for_thread(
        self, thread_id: str, *, limit: int = 50,
    ) -> list[task_store.AgentTask]:
        self.lookup_calls.append((thread_id, limit))
        if self.raise_on_lookup:
            raise RuntimeError("simulated DB failure")
        return list(self.by_thread.get(thread_id, []))


@pytest.fixture
def fake_tasks(monkeypatch: pytest.MonkeyPatch) -> _FakeTaskList:
    """Replace ``task_store.list_tasks_for_thread`` with an in-memory fake.

    Each test configures ``fake_tasks.by_thread[thread_id]`` with the
    exact list of ``AgentTask`` rows it wants the prefetch helper to
    see. Tests that want to simulate a transient DB failure set
    ``fake_tasks.raise_on_lookup = True``.
    """
    store = _FakeTaskList()
    monkeypatch.setattr(
        task_store, "list_tasks_for_thread", store.list_tasks_for_thread,
    )
    return store


def _make_task(
    *,
    test_run_id: Optional[str],
    thread_id: str = "thread-abc",
    task_id: Optional[UUID] = None,
) -> task_store.AgentTask:
    """Fabricate an ``AgentTask`` row for the fake prefetch backing."""
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=task_id or uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status="completed",
        payload={},
        submitted_at=now,
        updated_at=now,
        external_session_id="ext-session",
        test_run_id=test_run_id,
        thread_id=thread_id,
        result=None,
        error=None,
        subscriber_endpoints=[],
    )


# =============================================================================
# list_known_test_run_ids_for_thread
# =============================================================================


async def test_prefetch_returns_empty_when_no_prior_tasks(
    fake_tasks: _FakeTaskList,
) -> None:
    """A fresh thread with no tasks returns an empty list."""
    fake_tasks.set("thread-fresh", [])
    result = await list_known_test_run_ids_for_thread("thread-fresh")
    assert result == []
    assert fake_tasks.lookup_calls == [("thread-fresh", 50)]


async def test_prefetch_returns_empty_when_thread_id_is_none(
    fake_tasks: _FakeTaskList,
) -> None:
    """``thread_id=None`` short-circuits — no DB lookup, no result."""
    result = await list_known_test_run_ids_for_thread(None)
    assert result == []
    assert fake_tasks.lookup_calls == []


async def test_prefetch_returns_distinct_ids_newest_first(
    fake_tasks: _FakeTaskList,
) -> None:
    """Prefetch preserves first-seen order and deduplicates repeats."""
    fake_tasks.set(
        "thread-abc",
        [
            _make_task(test_run_id="2026-10-07-14-00-00"),  # newest
            _make_task(test_run_id="2026-10-06-10-00-00"),
            _make_task(test_run_id="2026-10-07-14-00-00"),  # duplicate
            _make_task(test_run_id="2026-10-05-09-30-00"),  # oldest
        ],
    )
    result = await list_known_test_run_ids_for_thread("thread-abc")
    assert result == [
        "2026-10-07-14-00-00",
        "2026-10-06-10-00-00",
        "2026-10-05-09-30-00",
    ]


async def test_prefetch_skips_rows_with_null_or_empty_test_run_id(
    fake_tasks: _FakeTaskList,
) -> None:
    """Only rows with a non-empty string ``test_run_id`` contribute."""
    fake_tasks.set(
        "thread-abc",
        [
            _make_task(test_run_id="2026-10-07-14-00-00"),
            _make_task(test_run_id=None),
            _make_task(test_run_id=""),
            _make_task(test_run_id="2026-10-06-10-00-00"),
        ],
    )
    result = await list_known_test_run_ids_for_thread("thread-abc")
    assert result == ["2026-10-07-14-00-00", "2026-10-06-10-00-00"]


async def test_prefetch_tolerates_lookup_failure(
    fake_tasks: _FakeTaskList,
) -> None:
    """Transient DB failures do not block the resolver — the helper
    logs and returns an empty list so the caller proceeds normally."""
    fake_tasks.raise_on_lookup = True
    result = await list_known_test_run_ids_for_thread("thread-abc")
    assert result == []


async def test_prefetch_respects_custom_limit(
    fake_tasks: _FakeTaskList,
) -> None:
    """The ``limit`` kwarg is forwarded to ``list_tasks_for_thread``."""
    fake_tasks.set("thread-abc", [])
    await list_known_test_run_ids_for_thread("thread-abc", limit=10)
    assert fake_tasks.lookup_calls == [("thread-abc", 10)]


# =============================================================================
# resolve_test_run_id_for_webui — pass-through outcomes (REUSE / MINT)
# =============================================================================


async def test_resolve_passthrough_reuse_from_user_text(
    fake_tasks: _FakeTaskList,
) -> None:
    """When the user names a ``test_run_id`` in prose, the classifier
    returns REUSE regardless of the thread history — no hint, no
    override."""
    fake_tasks.set(
        "thread-abc",
        [_make_task(test_run_id="2026-10-05-09-30-00")],
    )
    result = await resolve_test_run_id_for_webui(
        user_text="Please analyze test_run_id = 2026-10-07-14-00-00",
        thread_id="thread-abc",
    )
    assert result.test_run_id == "2026-10-07-14-00-00"
    assert result.outcome == "reuse"
    assert result.system_hint is None
    assert result.known_test_run_ids == ["2026-10-05-09-30-00"]


async def test_resolve_passthrough_mint_for_webui_prose_hint(
    fake_tasks: _FakeTaskList,
) -> None:
    """A pre-script-creation prose hint still mints even when the thread
    has prior candidates — minting a fresh ID is more useful than
    reusing a stale one in that scenario."""
    fake_tasks.set(
        "thread-abc",
        [_make_task(test_run_id="2026-10-05-09-30-00")],
    )
    result = await resolve_test_run_id_for_webui(
        user_text=(
            "Record a Playwright script for the login flow "
            "and generate a JMeter test plan."
        ),
        thread_id="thread-abc",
    )
    assert result.outcome == "mint"
    assert result.test_run_id is not None
    # Minted IDs follow the YYYY-MM-DD-HH-MM-SS shape.
    assert len(result.test_run_id.split("-")) == 6
    assert result.system_hint is None


# =============================================================================
# resolve_test_run_id_for_webui — implicit REUSE policy
# =============================================================================


async def test_prefetch_returns_single_id_sets_caller_identity_test_run_id(
    fake_tasks: _FakeTaskList,
) -> None:
    """When the thread has exactly one known ID and the classifier would
    otherwise REJECT, apply implicit REUSE — this is the authoritative
    ID returned to the caller for ``_caller_identity``."""
    fake_tasks.set(
        "thread-abc",
        [_make_task(test_run_id="2026-10-07-14-00-00")],
    )
    result = await resolve_test_run_id_for_webui(
        user_text="Show me the response time breakdown",
        thread_id="thread-abc",
    )
    assert result.test_run_id == "2026-10-07-14-00-00"
    assert result.outcome == "reject"  # classifier outcome unchanged
    assert result.system_hint is None
    assert result.known_test_run_ids == ["2026-10-07-14-00-00"]


# =============================================================================
# resolve_test_run_id_for_webui — ask-user hint policy
# =============================================================================


async def test_prefetch_returns_multiple_ids_injects_system_instruction(
    fake_tasks: _FakeTaskList,
) -> None:
    """When the thread has multiple known IDs and no explicit signal,
    the resolver returns a one-line hint for the caller to inject as a
    SystemMessage so the orchestrator LLM asks the user to pick one."""
    fake_tasks.set(
        "thread-abc",
        [
            _make_task(test_run_id="2026-10-07-14-00-00"),
            _make_task(test_run_id="2026-10-06-10-00-00"),
            _make_task(test_run_id="2026-10-05-09-30-00"),
        ],
    )
    result = await resolve_test_run_id_for_webui(
        user_text="How did the test go?",
        thread_id="thread-abc",
    )
    assert result.test_run_id is None
    assert result.outcome == "reject"
    assert result.system_hint is not None
    assert "2026-10-07-14-00-00" in result.system_hint
    assert "2026-10-06-10-00-00" in result.system_hint
    assert "ask the user" in result.system_hint.lower()


async def test_user_text_naming_specific_id_wins_over_prefetch_list(
    fake_tasks: _FakeTaskList,
) -> None:
    """Explicit beats implicit: when the user names an ID in prose, the
    resolver returns that exact value and skips the ask-user hint even
    though the thread has multiple candidates."""
    fake_tasks.set(
        "thread-abc",
        [
            _make_task(test_run_id="2026-10-07-14-00-00"),
            _make_task(test_run_id="2026-10-06-10-00-00"),
        ],
    )
    result = await resolve_test_run_id_for_webui(
        user_text="Analyze test_run_id 2026-10-05-09-30-00 specifically",
        thread_id="thread-abc",
    )
    # The user's explicit mention is REUSEd verbatim; the thread's
    # pre-existing candidates do not override it.
    assert result.test_run_id == "2026-10-05-09-30-00"
    assert result.outcome == "reuse"
    assert result.system_hint is None


# =============================================================================
# resolve_test_run_id_for_webui — edge / no-thread cases
# =============================================================================


async def test_resolve_no_thread_id_returns_empty_candidates(
    fake_tasks: _FakeTaskList,
) -> None:
    """When ``thread_id`` is ``None``, the prefetch is skipped and the
    policy branch sees no candidates — pure classifier behaviour."""
    result = await resolve_test_run_id_for_webui(
        user_text="How did the test go?",
        thread_id=None,
    )
    assert result.test_run_id is None
    assert result.outcome == "reject"
    assert result.known_test_run_ids == []
    assert result.system_hint is None


async def test_resolve_reject_with_zero_candidates_has_no_hint(
    fake_tasks: _FakeTaskList,
) -> None:
    """A reject on a fresh thread (no prior tasks) returns no hint —
    there is nothing to tell the LLM to ask about."""
    fake_tasks.set("thread-abc", [])
    result = await resolve_test_run_id_for_webui(
        user_text="hello",
        thread_id="thread-abc",
    )
    assert result.test_run_id is None
    assert result.outcome == "reject"
    assert result.system_hint is None
    assert result.known_test_run_ids == []


# =============================================================================
# _caller_identity + set_known_test_run_ids wiring
# =============================================================================


def test_known_test_run_ids_forwarded_via_caller_identity() -> None:
    """``set_known_test_run_ids`` populates ``_caller_identity`` so
    ``delegate_to_specialist`` can forward the list to specialists."""
    from agents.orchestrator import agent as orch

    orch.set_known_test_run_ids([
        "2026-10-07-14-00-00",
        "2026-10-06-10-00-00",
    ])
    assert orch._caller_identity["known_test_run_ids"] == [
        "2026-10-07-14-00-00",
        "2026-10-06-10-00-00",
    ]
    orch.clear_caller_identity()
    assert orch._caller_identity["known_test_run_ids"] == []


def test_set_known_test_run_ids_filters_non_strings_and_blanks() -> None:
    """The setter rejects non-string entries, strips whitespace, and
    treats a falsy input as a clear."""
    from agents.orchestrator import agent as orch

    orch.set_known_test_run_ids([
        " 2026-10-07-14-00-00 ",
        "",
        "   ",
        None,  # type: ignore[list-item]
        42,  # type: ignore[list-item]
        "2026-10-06-10-00-00",
    ])
    assert orch._caller_identity["known_test_run_ids"] == [
        "2026-10-07-14-00-00",
        "2026-10-06-10-00-00",
    ]
    orch.set_known_test_run_ids(None)
    assert orch._caller_identity["known_test_run_ids"] == []
    orch.set_known_test_run_ids([])
    assert orch._caller_identity["known_test_run_ids"] == []


def test_clear_caller_identity_resets_known_test_run_ids() -> None:
    """``clear_caller_identity`` must also reset the new slot so a stale
    list cannot leak into the next request on the same process."""
    from agents.orchestrator import agent as orch

    orch.set_caller_identity(
        user_id="u1", thread_id="t1", session_id="s1",
        test_run_id="2026-10-07-14-00-00",
    )
    orch.set_known_test_run_ids(["2026-10-07-14-00-00"])
    orch.clear_caller_identity()
    assert orch._caller_identity["user_id"] is None
    assert orch._caller_identity["known_test_run_ids"] == []


# =============================================================================
# Hint shape — stable regression guard
# =============================================================================


async def test_system_hint_shape_matches_plan_wording(
    fake_tasks: _FakeTaskList,
) -> None:
    """Regression guard: the hint string is phrased per the plan and
    carries the IDs as backtick-wrapped tokens so the LLM sees them as
    code-like identifiers (not prose)."""
    fake_tasks.set(
        "thread-abc",
        [
            _make_task(test_run_id="2026-10-07-14-00-00"),
            _make_task(test_run_id="2026-10-06-10-00-00"),
        ],
    )
    result = await resolve_test_run_id_for_webui(
        user_text="How did it go?",
        thread_id="thread-abc",
    )
    assert result.system_hint is not None
    hint = result.system_hint
    assert "Known `test_run_id`s" in hint
    assert "`2026-10-07-14-00-00`" in hint
    assert "`2026-10-06-10-00-00`" in hint
    assert hint.endswith("Ask the user which one applies if not stated.")
