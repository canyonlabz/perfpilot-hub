"""Tests for the context-budget resolver in ``services.helpers.token_context_window``.

Covers the C-4b increment (context_management schema): the resolver merges the
global ``context_management`` block (``config/agents.yaml``) with an agent's
optional per-agent override (keys present per-agent win, omitted keys inherit
global), derives ``effective_prompt_budget = context_limit −
reserved_completion_tokens − overhead_reserve_tokens``, and falls back per key
(with a WARNING) when a key is missing / malformed / out of range.

``resolve_context_budget_for_agent`` is covered via stubbed
``config_loader.load_global_config`` / ``load_agent_config`` so no config file
is read.
"""

from __future__ import annotations

import logging

import pytest

from services.helpers import token_context_window as tcw
from services.helpers.token_context_window import ContextBudget


@pytest.fixture(autouse=True)
def _reset_warning_dedup():
    """Clear the per-process warn-once set so fallback warnings re-fire per test."""
    tcw._reset_budget_warning_dedup()
    yield
    tcw._reset_budget_warning_dedup()


def _full_global() -> dict:
    """A complete global context_management block (mirrors config/agents.yaml)."""
    return {
        "context_limit": 400000,
        "reserved_completion_tokens": 8000,
        "overhead_reserve_tokens": 12000,
        "compaction_threshold": 0.80,
        "compaction_preserve_recent": 5,
    }


# =============================================================================
# Global-only + effective-budget derivation
# =============================================================================


def test_global_only_resolves_and_derives_effective():
    budget = tcw.resolve_context_budget(None, global_cm=_full_global())
    assert budget == ContextBudget(
        context_limit=400000,
        reserved_completion_tokens=8000,
        overhead_reserve_tokens=12000,
        compaction_threshold=0.80,
        compaction_preserve_recent=5,
        effective_prompt_budget=400000 - 8000 - 12000,
    )
    assert budget.effective_prompt_budget == 380000


# =============================================================================
# Global <- per-agent merge
# =============================================================================


def test_per_agent_overrides_win_and_recompute_effective():
    budget = tcw.resolve_context_budget(
        {"context_limit": 1000000, "reserved_completion_tokens": 20000},
        global_cm=_full_global(),
    )
    assert budget.context_limit == 1000000           # per-agent
    assert budget.reserved_completion_tokens == 20000  # per-agent
    assert budget.overhead_reserve_tokens == 12000     # inherited
    assert budget.compaction_threshold == 0.80         # inherited
    assert budget.compaction_preserve_recent == 5      # inherited
    assert budget.effective_prompt_budget == 1000000 - 20000 - 12000


def test_omitted_per_agent_keys_inherit_global():
    budget = tcw.resolve_context_budget(
        {"compaction_threshold": 0.5}, global_cm=_full_global()
    )
    assert budget.compaction_threshold == 0.5   # per-agent
    assert budget.context_limit == 400000       # inherited
    assert budget.reserved_completion_tokens == 8000
    assert budget.overhead_reserve_tokens == 12000


# =============================================================================
# Fallbacks (missing / malformed / out-of-range)
# =============================================================================


def test_empty_both_uses_fallbacks():
    budget = tcw.resolve_context_budget(None, global_cm=None)
    assert budget.context_limit == 128000
    assert budget.reserved_completion_tokens == 8000
    assert budget.overhead_reserve_tokens == 12000
    assert budget.compaction_threshold == 0.80
    assert budget.compaction_preserve_recent == 5
    assert budget.effective_prompt_budget == 128000 - 8000 - 12000


def test_string_numeric_values_are_coerced():
    budget = tcw.resolve_context_budget(
        {"context_limit": "200000", "reserved_completion_tokens": "1000"},
        global_cm=None,
    )
    assert budget.context_limit == 200000
    assert budget.reserved_completion_tokens == 1000


def test_malformed_and_out_of_range_fall_back_per_key():
    budget = tcw.resolve_context_budget(
        {
            "context_limit": "not-a-number",
            "compaction_threshold": 5.0,      # > 1 -> out of range
            "overhead_reserve_tokens": -3,    # < 0 -> out of range
            "compaction_preserve_recent": None,
        },
        global_cm=None,
    )
    assert budget.context_limit == 128000
    assert budget.compaction_threshold == 0.80
    assert budget.overhead_reserve_tokens == 12000
    assert budget.compaction_preserve_recent == 5


def test_zero_reserves_allowed():
    budget = tcw.resolve_context_budget(
        {"reserved_completion_tokens": 0, "overhead_reserve_tokens": 0},
        global_cm=_full_global(),
    )
    assert budget.reserved_completion_tokens == 0
    assert budget.overhead_reserve_tokens == 0
    assert budget.effective_prompt_budget == 400000


def test_effective_clamps_to_one_when_reserves_exceed_limit():
    budget = tcw.resolve_context_budget(
        {
            "context_limit": 10000,
            "reserved_completion_tokens": 8000,
            "overhead_reserve_tokens": 12000,
        },
        global_cm=None,
    )
    assert budget.effective_prompt_budget == 1


def test_missing_key_emits_warning(caplog):
    partial_global = {
        "context_limit": 400000,
        "reserved_completion_tokens": 8000,
        "overhead_reserve_tokens": 12000,
        "compaction_preserve_recent": 5,
        # compaction_threshold intentionally absent
    }
    with caplog.at_level(logging.WARNING, logger="services.helpers.token_context_window"):
        budget = tcw.resolve_context_budget(None, global_cm=partial_global)
    assert budget.compaction_threshold == 0.80
    assert any("compaction_threshold" in rec.message for rec in caplog.records)


# =============================================================================
# resolve_context_budget_for_agent (global + per-agent IO)
# =============================================================================


def test_resolve_for_agent_merges_global_and_override(monkeypatch):
    from utils import config_loader

    monkeypatch.setattr(
        config_loader, "load_global_config",
        lambda: {"context_management": _full_global()},
    )
    monkeypatch.setattr(
        config_loader, "load_agent_config",
        lambda name: {"context_management": {"context_limit": 1000000}},
    )
    budget = tcw.resolve_context_budget_for_agent("reporting-agent")
    assert budget.context_limit == 1000000          # per-agent override
    assert budget.reserved_completion_tokens == 8000  # inherited global
    assert budget.overhead_reserve_tokens == 12000
    assert budget.effective_prompt_budget == 1000000 - 8000 - 12000


def test_resolve_for_agent_no_override_inherits_global(monkeypatch):
    from utils import config_loader

    monkeypatch.setattr(
        config_loader, "load_global_config",
        lambda: {"context_management": _full_global()},
    )
    monkeypatch.setattr(config_loader, "load_agent_config", lambda name: {})
    budget = tcw.resolve_context_budget_for_agent("monitoring-agent")
    assert budget.context_limit == 400000
    assert budget.effective_prompt_budget == 380000
