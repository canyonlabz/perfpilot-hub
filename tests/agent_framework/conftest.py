"""Agent-framework-scoped pytest fixtures.

Provides reusable test doubles and helpers for testing modules under
``agent-framework/backend/``. Any test in ``tests/agent_framework/``
(at any nesting depth) can request these fixtures by name.

Fixtures exported:

- ``reset_hitl_warnings`` (autouse) — clears the process-scoped
  ``_warn_once`` dedup set in ``services.hitl_gates`` before each test,
  so warning-emit tests can assert re-firing without leaking state
  between tests.
- ``hitl_store_mock`` — in-memory async stand-in for
  ``stores.hitl_store.create_prompt`` / ``get_approval``. Lets tests
  exercise ``wait_for_decision`` and the ``enforce_at_*`` functions
  without a real Postgres.
- ``tmp_hitl_yaml`` — writes a temporary ``hitl.yaml`` file and points
  ``utils.config_loader`` at it. Returns a callable that (re-)writes
  the file body and clears the loader cache.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock

import pytest


# =============================================================================
# Warning-dedup reset (autouse — applies to every test in this tree)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_hitl_warnings():
    """Clear the ``_warn_once`` dedup set before every test.

    Without this reset, the FIRST test in a session that hits a fallback
    would emit its WARNING, and every subsequent test asserting the same
    WARNING would see zero emissions (dedup already engaged). Clearing
    per-test isolates warning-emit assertions.

    Safe to run even in tests that do NOT touch hitl_gates — the reset
    is a no-op when the dedup set is already empty.
    """
    # Import here so this fixture is defined even if hitl_gates hasn't
    # loaded yet in the test session.
    from services import hitl_gates
    hitl_gates._reset_warning_dedup()
    yield
    # No teardown needed — the next test's setup runs this fixture again.


# =============================================================================
# In-memory hitl_store stand-in
# =============================================================================


@pytest.fixture
def hitl_store_mock(monkeypatch) -> SimpleNamespace:
    """Patch ``stores.hitl_store`` with in-memory async mocks.

    Provides ``create_prompt`` and ``get_approval`` backed by an
    in-process dict. Tests that need to drive a HITL decision can mutate
    the returned state directly:

    Example::

        async def test_wait_returns_approved(hitl_store_mock):
            # Set up: create a prompt row (simulates the framework
            # opening a HITL card)
            approval = await hitl_store.create_prompt(
                task_id=UUID("00000000-0000-0000-0000-000000000001"),
                prompt={"title": "test", "summary": "test"},
            )
            # Drive: mark the row as approved (simulates the human
            # clicking "Approve" in the Web UI)
            hitl_store_mock.state[approval.id].decision = "approved"
            hitl_store_mock.state[approval.id].decided_by = "test-user"
            # Assert: wait_for_decision returns Decision(approved=True, ...)
            decision = await hitl_gates.wait_for_decision(approval.id)
            assert decision.approved

    Returns a ``SimpleNamespace`` with:
      - ``state``: the underlying dict ``{approval_id: SimpleNamespace}``
        so tests can inspect or mutate it.
      - ``next_id``: single-element list ``[int]`` for ID assignment
        (mutable so the async closures can bump it).
    """
    from stores import hitl_store as real_store

    state: dict[int, SimpleNamespace] = {}
    next_id: list[int] = [1]

    async def _create(task_id, prompt):
        approval = SimpleNamespace(
            id=next_id[0],
            task_id=task_id,
            prompt=prompt,
            decision="pending",
            feedback=None,
            decided_by=None,
        )
        state[approval.id] = approval
        next_id[0] += 1
        return approval

    async def _get(approval_id):
        return state.get(approval_id)

    monkeypatch.setattr(real_store, "create_prompt", AsyncMock(side_effect=_create))
    monkeypatch.setattr(real_store, "get_approval", AsyncMock(side_effect=_get))

    return SimpleNamespace(state=state, next_id=next_id)


# =============================================================================
# Temp hitl.yaml writer
# =============================================================================


@pytest.fixture
def tmp_hitl_yaml(tmp_path, monkeypatch) -> Callable[[str], None]:
    """Write a temporary ``hitl.yaml`` and point config_loader at it.

    Returns a ``write(yaml_str)`` callable. Each call:
      1. (Re-)writes ``<tmp_path>/config/hitl.yaml`` with the given YAML.
      2. Clears the loader cache so the next
         ``config_loader.load_hitl_config()`` picks up the fresh content.

    The loader is monkeypatched so callers who invoke
    ``load_hitl_config()`` (with or without ``framework_dir``) get the
    temp file transparently. This keeps individual test bodies from
    having to thread ``framework_dir`` through every call.

    Example::

        async def test_publish_tools_reads_yaml(tmp_hitl_yaml):
            tmp_hitl_yaml('''
            publish_tools:
              - confluence_create_page
            ''')
            tools = hitl_gates.get_publish_mcp_tools()
            assert tools == frozenset({"confluence_create_page"})
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    from utils import config_loader

    def _write(yaml_str: str) -> None:
        (config_dir / "hitl.yaml").write_text(yaml_str, encoding="utf-8")
        config_loader.clear_hitl_cache()

    # Wrap the loader so every call transparently reads from tmp_path.
    original_load = config_loader.load_hitl_config

    def _wrapper(*, framework_dir=None):
        # If the caller passed framework_dir explicitly, honor it —
        # otherwise redirect to the temp directory.
        return original_load(framework_dir=framework_dir or tmp_path)

    monkeypatch.setattr(config_loader, "load_hitl_config", _wrapper)

    return _write
