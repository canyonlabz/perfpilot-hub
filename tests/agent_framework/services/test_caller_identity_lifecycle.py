"""Tests for caller-identity snapshot/restore lifecycle.

Pins the fix for the ``NoSession`` race where a child specialist task's
``_clear_web_ui_caller()`` exit wiped the orchestrator's live
``_caller_identity`` dict during inline-drain, killing subsequent
delegations in the same orchestrator turn.

The fix replaces the set-then-wipe pattern with snapshot-restore
semantics:

  * ``_restore_web_ui_caller`` captures a snapshot before any mutation
    and skips the restore when a live ``session_id`` is already present.
  * ``_restore_caller_snapshot`` restores the pre-entry snapshot instead
    of wiping all fields to ``None``.

These tests exercise the helpers directly (no LLM, no DB, no MCP).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import pytest

from agents.orchestrator.agent import (
    _caller_identity,
    clear_caller_identity,
    set_caller_identity,
    set_caller_surface,
    set_known_test_run_ids,
    snapshot_caller_identity,
    restore_caller_identity,
)
from services.specialist_agents import (
    _restore_web_ui_caller,
    _restore_caller_snapshot,
)
from stores import task_store


# =============================================================================
# Helpers
# =============================================================================


def _make_task(
    *,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> task_store.AgentTask:
    """Build a minimal AgentTask for testing (no DB)."""
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4() if session_id is None else session_id,
        agent_name="execution-agent",
        status="running",
        payload=payload or {},
        submitted_at=now,
        updated_at=now,
        thread_id=thread_id,
    )


def _make_web_ui_payload(
    *,
    user_id: str = "u-test",
    test_run_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """Build a payload with ``caller_surface=web_ui`` stamps."""
    d: dict = {
        "caller_surface": "web_ui",
        "caller_user_id": user_id,
        "user_message": "test",
    }
    if test_run_id:
        d["test_run_id"] = test_run_id
    if thread_id:
        d["thread_id"] = thread_id
    return d


@pytest.fixture(autouse=True)
def _reset_identity():
    """Ensure a clean ``_caller_identity`` before and after every test."""
    clear_caller_identity()
    yield
    clear_caller_identity()


# =============================================================================
# snapshot_caller_identity / restore_caller_identity unit tests
# =============================================================================


def test_snapshot_returns_copy_of_current_state() -> None:
    """Snapshot must return a dict with all current field values."""
    set_caller_identity(
        user_id="u-1",
        thread_id="t-1",
        session_id="s-1",
        task_id="tk-1",
        test_run_id="tr-1",
    )
    set_caller_surface("web_ui")
    set_known_test_run_ids(["tr-1", "tr-2"])

    snap = snapshot_caller_identity()

    assert snap["user_id"] == "u-1"
    assert snap["thread_id"] == "t-1"
    assert snap["session_id"] == "s-1"
    assert snap["task_id"] == "tk-1"
    assert snap["test_run_id"] == "tr-1"
    assert snap["surface"] == "web_ui"
    assert snap["known_test_run_ids"] == ["tr-1", "tr-2"]


def test_snapshot_is_independent_of_live_dict() -> None:
    """Mutations to the snapshot must not affect the live dict."""
    set_caller_identity(user_id="u-1", thread_id="t-1", session_id="s-1")
    set_known_test_run_ids(["tr-1"])

    snap = snapshot_caller_identity()
    snap["user_id"] = "MUTATED"
    snap["known_test_run_ids"].append("MUTATED")

    assert _caller_identity["user_id"] == "u-1"
    assert _caller_identity["known_test_run_ids"] == ["tr-1"]


def test_restore_overwrites_live_dict_from_snapshot() -> None:
    """Restore must bring the live dict back to the snapshot's state."""
    set_caller_identity(user_id="u-1", thread_id="t-1", session_id="s-1")
    set_caller_surface("web_ui")
    set_known_test_run_ids(["tr-1"])
    snap = snapshot_caller_identity()

    # Mutate the live dict (simulating a child context overwriting it)
    set_caller_identity(
        user_id="u-child", thread_id="t-child", session_id="s-child",
        task_id="tk-child", test_run_id="tr-child",
    )
    set_caller_surface("web_ui")
    set_known_test_run_ids([])

    restore_caller_identity(snap)

    assert _caller_identity["user_id"] == "u-1"
    assert _caller_identity["thread_id"] == "t-1"
    assert _caller_identity["session_id"] == "s-1"
    assert _caller_identity["task_id"] is None
    assert _caller_identity["test_run_id"] is None
    assert _caller_identity["surface"] == "web_ui"
    assert _caller_identity["known_test_run_ids"] == ["tr-1"]


# =============================================================================
# _restore_web_ui_caller — inline-drain race (live identity present)
# =============================================================================


def test_restore_skips_when_live_session_present() -> None:
    """When the orchestrator's live identity has a session_id (inline-drain
    scenario), ``_restore_web_ui_caller`` must skip the restore and return
    ``(False, None)`` so the live identity is preserved."""
    set_caller_identity(
        user_id="u-orchestrator",
        thread_id="t-orch",
        session_id="s-live",
        task_id="tk-orch",
        test_run_id="tr-orch",
    )
    set_caller_surface("web_ui")
    expected_state = dict(_caller_identity)

    task = _make_task(thread_id="t-orch")
    payload = _make_web_ui_payload(user_id="u-child", test_run_id="tr-child")

    restored, snapshot = _restore_web_ui_caller(task, payload)

    assert restored is False
    assert snapshot is None
    # Live dict must be completely untouched
    for key, val in expected_state.items():
        assert _caller_identity[key] == val, (
            f"_caller_identity[{key!r}] was mutated: {_caller_identity[key]!r} != {val!r}"
        )


def test_restore_caller_snapshot_after_inline_drain_preserves_identity() -> None:
    """Full inline-drain simulation: set live identity, enter + exit a
    child context, assert the live identity is identical afterwards."""
    set_caller_identity(
        user_id="u-orch",
        thread_id="t-orch",
        session_id="s-orch",
        task_id="tk-orch",
        test_run_id="tr-orch",
    )
    set_caller_surface("web_ui")
    set_known_test_run_ids(["tr-orch", "tr-prev"])
    pre_state = snapshot_caller_identity()

    task = _make_task(thread_id="t-orch")
    payload = _make_web_ui_payload(user_id="u-child", test_run_id="tr-child")

    # Simulate the try/finally pattern
    restored, snapshot = _restore_web_ui_caller(task, payload)
    try:
        # Simulate specialist work...
        pass
    finally:
        if restored:
            _restore_caller_snapshot(snapshot)

    # The dict must match the pre-entry state exactly
    for key in pre_state:
        assert _caller_identity[key] == pre_state[key], (
            f"After exit: _caller_identity[{key!r}] = {_caller_identity[key]!r}, "
            f"expected {pre_state[key]!r}"
        )


# =============================================================================
# _restore_web_ui_caller — post-dispatch specialist (no live identity)
# =============================================================================


def test_restore_applies_when_no_live_session() -> None:
    """When the SSE stream has already ended (session_id is None), the
    specialist must restore from the payload so JWT minting works."""
    # No live identity — all fields are None (post-SSE clear)
    clear_caller_identity()

    task = _make_task(thread_id="t-specialist")
    payload = _make_web_ui_payload(
        user_id="u-specialist",
        test_run_id="tr-specialist",
        thread_id="t-specialist",
    )

    restored, snapshot = _restore_web_ui_caller(task, payload)

    assert restored is True
    assert snapshot is not None
    # The live dict now has the child's identity
    assert _caller_identity["user_id"] == "u-specialist"
    assert _caller_identity["surface"] == "web_ui"
    assert _caller_identity["session_id"] == str(task.session_id)


def test_restore_then_snapshot_restore_clears_child_identity() -> None:
    """After a post-dispatch specialist exits, the snapshot restore must
    bring the dict back to the pre-entry state (all-None)."""
    clear_caller_identity()
    pre_state = snapshot_caller_identity()

    task = _make_task(thread_id="t-specialist")
    payload = _make_web_ui_payload(user_id="u-specialist")

    restored, snapshot = _restore_web_ui_caller(task, payload)
    try:
        # Verify the child identity was applied
        assert _caller_identity["user_id"] == "u-specialist"
    finally:
        if restored:
            _restore_caller_snapshot(snapshot)

    # Back to the pre-entry state
    for key in pre_state:
        assert _caller_identity[key] == pre_state[key], (
            f"After restore: _caller_identity[{key!r}] = {_caller_identity[key]!r}, "
            f"expected {pre_state[key]!r}"
        )


# =============================================================================
# Nested entry (two child contexts)
# =============================================================================


def test_nested_restore_exits_in_correct_order() -> None:
    """Two overlapping child contexts must restore in stack order.

    This scenario occurs if two specialist tasks start concurrently
    after the SSE stream has ended — each captures its own snapshot.
    """
    clear_caller_identity()

    # --- First child enters ---
    task_a = _make_task(thread_id="t-a")
    payload_a = _make_web_ui_payload(user_id="u-a", test_run_id="tr-a")
    restored_a, snap_a = _restore_web_ui_caller(task_a, payload_a)
    assert restored_a is True

    # --- Second child enters (overwrites first child) ---
    # snap_b captures child-A's identity since that is the current state
    task_b = _make_task(thread_id="t-b")
    payload_b = _make_web_ui_payload(user_id="u-b", test_run_id="tr-b")
    # Second child sees session_id from child A's restore, so it will
    # skip (live session present). This is the correct behavior — the
    # second child should not clobber the first child's identity.
    restored_b, snap_b = _restore_web_ui_caller(task_b, payload_b)

    # --- Second child exits ---
    if restored_b:
        _restore_caller_snapshot(snap_b)

    # --- First child exits ---
    if restored_a:
        _restore_caller_snapshot(snap_a)

    # Back to all-None
    assert _caller_identity["user_id"] is None
    assert _caller_identity["session_id"] is None
    assert _caller_identity["surface"] is None


# =============================================================================
# Guard: _restore_caller_snapshot(None) is a no-op
# =============================================================================


def test_restore_caller_snapshot_none_is_noop() -> None:
    """Passing None must not raise or mutate the dict."""
    set_caller_identity(
        user_id="u-keep", thread_id="t-keep", session_id="s-keep",
    )
    expected = dict(_caller_identity)

    _restore_caller_snapshot(None)

    for key, val in expected.items():
        assert _caller_identity[key] == val


# =============================================================================
# Guard: non-web_ui payloads are no-ops
# =============================================================================


def test_non_web_ui_payload_returns_false_none() -> None:
    """A payload without ``caller_surface=web_ui`` must not trigger any
    restore logic."""
    set_caller_identity(user_id="u-x", thread_id="t-x", session_id="s-x")
    expected = dict(_caller_identity)

    task = _make_task()
    payload = {"user_message": "test"}  # no caller_surface

    restored, snapshot = _restore_web_ui_caller(task, payload)

    assert restored is False
    assert snapshot is None
    for key, val in expected.items():
        assert _caller_identity[key] == val
