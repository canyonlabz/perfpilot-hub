"""Tests for the native AG2 harness in ``core.agent_harness``.

These cover the C-4c increment: the shared composer that turns an agent's
existing loop-limit config keys and ``context_management`` budget into native
AG2 middleware and observers, plus the HITL hook. The harness reuses the C-4a
HITL path (``services.hitl_gates``), the C-4b budget resolver
(``resolve_context_budget_for_agent``), and the existing fire-and-forget
``stores.trace_store`` inserts.

The real AG2 middleware/observer/event classes are exercised so construction
and the ``on_tool_execution`` / ``on_llm_call`` / ``process`` contracts are
real, but ``trace_store`` persistence, the config loader, and the budget
resolver are stubbed, so no database, config file, or network is touched.
AG2's own ``LoopDetector`` repeat-detection algorithm is an external dependency
and is not re-tested here; only the throttle's per-tool reshaping is.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ag2.events import ObserverAlert, Severity, ToolCallEvent, ToolErrorEvent
from ag2.middleware import Middleware, TokenLimiter
from ag2.observers import LoopDetector, TokenMonitor

from core import agent_harness as ah
from core.agent_harness import (
    AgentHarness,
    HarnessSettings,
    TokenLedgerObserver,
    ToolRepeatThrottle,
    ToolRoundLimit,
    ToolTraceMiddleware,
    build_agent_harness,
    load_harness_settings,
)
from services import hitl_gates


# =============================================================================
# Test doubles
# =============================================================================


class _StubContext:
    """Minimal stand-in for ``ag2.Context``: variables + async send capture."""

    def __init__(self, variables: dict | None = None) -> None:
        self.variables = variables or {}
        self.sent: list = []

    async def send(self, event):
        self.sent.append(event)


def _envelope(source_event):
    """Observers receive wrapped envelopes exposing ``source_event``."""
    return SimpleNamespace(source_event=source_event)


def _tool_event(name: str = "foo_tool", args: dict | None = None) -> ToolCallEvent:
    return ToolCallEvent(
        id="call-1",
        name=name,
        arguments=json.dumps(args or {"k": "v"}),
    )


def _result_event(payload: str):
    """A ToolResultEvent-shaped stub: ``.result.parts[].content`` is read."""
    return SimpleNamespace(result=SimpleNamespace(parts=[SimpleNamespace(content=payload)]))


def _budget_stub(effective: int = 380000, threshold: float = 0.80):
    return SimpleNamespace(effective_prompt_budget=effective, compaction_threshold=threshold)


def _settings(
    *,
    max_tool_rounds: int = 7,
    max_consecutive_repeats: int = 3,
    polling_tools=frozenset({"check_status"}),
    polling_max_consecutive_repeats: int = 30,
    effective_prompt_budget: int = 380000,
    compaction_threshold: float = 0.80,
) -> HarnessSettings:
    return HarnessSettings(
        max_tool_rounds=max_tool_rounds,
        max_consecutive_repeats=max_consecutive_repeats,
        polling_tools=frozenset(polling_tools),
        polling_max_consecutive_repeats=polling_max_consecutive_repeats,
        effective_prompt_budget=effective_prompt_budget,
        compaction_threshold=compaction_threshold,
    )


# =============================================================================
# load_harness_settings
# =============================================================================


def test_load_harness_settings_specialist_defaults(monkeypatch):
    monkeypatch.setattr(ah, "resolve_context_budget_for_agent", lambda _n: _budget_stub())
    from utils import config_loader
    monkeypatch.setattr(config_loader, "load_agent_config", lambda _n: {})

    s = load_harness_settings("reporting-agent")
    assert s.max_tool_rounds == 7
    assert s.max_consecutive_repeats == 3
    assert s.polling_max_consecutive_repeats == 30
    assert s.polling_tools == frozenset()
    assert s.effective_prompt_budget == 380000
    assert s.compaction_threshold == 0.80


def test_load_harness_settings_orchestrator_defaults(monkeypatch):
    monkeypatch.setattr(ah, "resolve_context_budget_for_agent", lambda _n: _budget_stub())
    from utils import config_loader
    monkeypatch.setattr(config_loader, "load_agent_config", lambda _n: {})

    s = load_harness_settings("orchestrator")
    assert s.max_tool_rounds == 10
    assert s.polling_max_consecutive_repeats == 15


def test_load_harness_settings_overrides_and_polling_coercion(monkeypatch):
    monkeypatch.setattr(ah, "resolve_context_budget_for_agent", lambda _n: _budget_stub())
    from utils import config_loader
    cfg = {
        "max_tool_rounds": 4,
        "max_consecutive_repeats": 2,
        "polling_max_consecutive_repeats": 12,
        "polling_tools": ["check_status", "poll_run"],
    }
    monkeypatch.setattr(config_loader, "load_agent_config", lambda _n: cfg)

    s = load_harness_settings("script-agent")
    assert s.max_tool_rounds == 4
    assert s.max_consecutive_repeats == 2
    assert s.polling_max_consecutive_repeats == 12
    assert s.polling_tools == frozenset({"check_status", "poll_run"})


def test_load_harness_settings_non_list_polling_is_empty(monkeypatch):
    monkeypatch.setattr(ah, "resolve_context_budget_for_agent", lambda _n: _budget_stub())
    from utils import config_loader
    monkeypatch.setattr(config_loader, "load_agent_config", lambda _n: {"polling_tools": "nope"})

    s = load_harness_settings("reporting-agent")
    assert s.polling_tools == frozenset()


# =============================================================================
# HarnessSettings threshold arithmetic
# =============================================================================


def test_token_thresholds_scale_with_rounds_and_budget():
    s = _settings(max_tool_rounds=5, effective_prompt_budget=1000, compaction_threshold=0.80)
    # alert = budget * rounds; warn = threshold * budget * rounds
    assert s.token_alert_threshold == 5000
    assert s.token_warn_threshold == 4000


# =============================================================================
# ToolRepeatThrottle
# =============================================================================


def test_throttle_limit_for_polling_vs_default():
    t = ToolRepeatThrottle(
        max_consecutive_repeats=3,
        polling_tools=["check_status"],
        polling_max_consecutive_repeats=30,
    )
    assert t.limit_for("check_status") == 30
    assert t.limit_for("write_file") == 3


async def test_throttle_skips_non_tool_call_envelope(monkeypatch):
    t = ToolRepeatThrottle(max_consecutive_repeats=3, polling_max_consecutive_repeats=30)

    async def _super(self, events, ctx):  # pragma: no cover - must not be called
        raise AssertionError("super().process should not run for non-tool events")

    monkeypatch.setattr(LoopDetector, "process", _super)
    out = await t.process([_envelope(SimpleNamespace())], ctx=None)
    assert out is None


async def test_throttle_returns_none_when_super_finds_no_repeat(monkeypatch):
    t = ToolRepeatThrottle(max_consecutive_repeats=3, polling_max_consecutive_repeats=30)

    async def _super(self, events, ctx):
        return None

    monkeypatch.setattr(LoopDetector, "process", _super)
    out = await t.process([_envelope(_tool_event("foo_tool"))], ctx=None)
    assert out is None


async def test_throttle_reshapes_super_alert_to_fatal_with_default_limit(monkeypatch):
    t = ToolRepeatThrottle(max_consecutive_repeats=3, polling_max_consecutive_repeats=30)
    seen_thresholds: list[int] = []

    async def _super(self, events, ctx):
        # Record the per-call threshold the throttle set before delegating.
        seen_thresholds.append(self._repeat_threshold)
        return ObserverAlert(source="x", severity=Severity.WARNING, message="raw", data={"a": 1})

    monkeypatch.setattr(LoopDetector, "process", _super)
    out = await t.process([_envelope(_tool_event("write_file"))], ctx=None)

    assert out is not None
    assert out.severity == Severity.FATAL
    assert "write_file" in out.message
    assert "(threshold)" in out.message  # non-polling label is empty
    assert out.data["tool"] == "write_file"
    assert out.data["limit"] == 3
    assert out.data["a"] == 1  # original alert data is preserved
    assert seen_thresholds == [3]


async def test_throttle_uses_polling_limit_for_polling_tool(monkeypatch):
    t = ToolRepeatThrottle(
        max_consecutive_repeats=3,
        polling_tools=["check_status"],
        polling_max_consecutive_repeats=30,
    )
    seen_thresholds: list[int] = []

    async def _super(self, events, ctx):
        seen_thresholds.append(self._repeat_threshold)
        return ObserverAlert(source="x", severity=Severity.WARNING, message="raw", data={})

    monkeypatch.setattr(LoopDetector, "process", _super)
    out = await t.process([_envelope(_tool_event("check_status"))], ctx=None)

    assert out is not None
    assert out.data["limit"] == 30
    assert "polling threshold" in out.message
    assert seen_thresholds == [30]


# =============================================================================
# ToolRoundLimit
# =============================================================================


async def test_round_limit_allows_then_halts():
    ctx = _StubContext()
    mw = ToolRoundLimit(_tool_event(), ctx, max_tool_rounds=2)
    calls: list[int] = []

    async def _next(events, context):
        calls.append(1)
        return "MODEL_REPLY"

    # First two rounds delegate to the model.
    assert await mw.on_llm_call(_next, [], ctx) == "MODEL_REPLY"
    assert await mw.on_llm_call(_next, [], ctx) == "MODEL_REPLY"
    assert len(calls) == 2
    assert ctx.sent == []

    # Third round is capped: no model call, a HaltEvent, a terminal reply.
    reply = await mw.on_llm_call(_next, [], ctx)
    assert len(calls) == 2
    assert len(ctx.sent) == 1
    assert ctx.sent[0].source == ah.ROUND_LIMIT_SOURCE
    assert "Max tool rounds (2)" in reply.message.content


# =============================================================================
# ToolTraceMiddleware
# =============================================================================


def _capture_traces(monkeypatch) -> list[dict]:
    rows: list[dict] = []
    monkeypatch.setattr(
        ah.trace_store, "schedule_trace_insert", lambda **kw: rows.append(kw)
    )
    return rows


async def test_trace_records_successful_ok_result(monkeypatch):
    rows = _capture_traces(monkeypatch)
    ctx = _StubContext(variables={"task_id": uuid4()})
    event = _tool_event("do_thing", {"x": 1})
    mw = ToolTraceMiddleware(event, ctx, agent_name="script-agent")

    async def _next(ev, context):
        return _result_event('{"ok": true, "value": 5}')

    out = await mw.on_tool_execution(_next, event, ctx)
    assert out is not None
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "do_thing"
    assert rows[0]["agent_name"] == "script-agent"
    assert rows[0]["args"] == {"x": 1}
    assert rows[0]["error"] is None


async def test_trace_extracts_error_from_ok_false_result(monkeypatch):
    rows = _capture_traces(monkeypatch)
    ctx = _StubContext(variables={"task_id": uuid4()})
    event = _tool_event("do_thing")
    mw = ToolTraceMiddleware(event, ctx, agent_name="script-agent")

    async def _next(ev, context):
        return _result_event('{"ok": false, "error": {"type": "Boom", "message": "no"}}')

    await mw.on_tool_execution(_next, event, ctx)
    assert len(rows) == 1
    assert rows[0]["error"] == {"type": "Boom", "message": "no"}


async def test_trace_records_tool_error_event_result(monkeypatch):
    rows = _capture_traces(monkeypatch)
    ctx = _StubContext(variables={"task_id": uuid4()})
    event = _tool_event("do_thing")
    mw = ToolTraceMiddleware(event, ctx, agent_name="script-agent")

    async def _next(ev, context):
        return ToolErrorEvent(error=ValueError("boom"))

    await mw.on_tool_execution(_next, event, ctx)
    assert len(rows) == 1
    assert rows[0]["error"]["type"] == "ValueError"
    assert rows[0]["error"]["message"] == "boom"


async def test_trace_records_on_raised_exception_then_reraises(monkeypatch):
    rows = _capture_traces(monkeypatch)
    ctx = _StubContext(variables={"task_id": uuid4()})
    event = _tool_event("do_thing")
    mw = ToolTraceMiddleware(event, ctx, agent_name="script-agent")

    async def _next(ev, context):
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        await mw.on_tool_execution(_next, event, ctx)
    assert len(rows) == 1
    assert rows[0]["error"]["type"] == "RuntimeError"
    assert rows[0]["error"]["message"] == "kaboom"
    assert json.loads(rows[0]["result"])["ok"] is False


async def test_trace_skips_persistence_without_task_id(monkeypatch):
    rows = _capture_traces(monkeypatch)
    ctx = _StubContext(variables={})  # no task_id run variable
    event = _tool_event("do_thing")
    mw = ToolTraceMiddleware(event, ctx, agent_name="script-agent")

    async def _next(ev, context):
        return _result_event('{"ok": true}')

    out = await mw.on_tool_execution(_next, event, ctx)
    assert out is not None
    assert rows == []


# =============================================================================
# TokenLedgerObserver
# =============================================================================


def _capture_ledger(monkeypatch) -> list[dict]:
    rows: list[dict] = []
    monkeypatch.setattr(
        ah.trace_store, "schedule_token_ledger_insert", lambda **kw: rows.append(kw)
    )
    return rows


def _usage(event_id="u1", kind="model_call", task_id=None, prompt=100, completion=20):
    return SimpleNamespace(
        event_id=event_id,
        kind=kind,
        task_id=task_id,
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def test_ledger_records_model_call_with_utilization(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    obs = TokenLedgerObserver(agent_name="orchestrator", effective_prompt_budget=1000)
    run_task = uuid4()
    obs._record(_usage(prompt=250, completion=40), run_task)

    assert len(rows) == 1
    assert rows[0]["loop_iteration"] == 1
    assert rows[0]["prompt_tokens"] == 250
    assert rows[0]["completion_tokens"] == 40
    assert rows[0]["utilization_pct"] == pytest.approx(0.25)


def test_ledger_dedupes_same_event_id(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    obs = TokenLedgerObserver(agent_name="orchestrator", effective_prompt_budget=1000)
    run_task = uuid4()
    obs._record(_usage(event_id="dup"), run_task)
    obs._record(_usage(event_id="dup"), run_task)
    assert len(rows) == 1


def test_ledger_skips_non_model_call_and_subagent_usage(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    obs = TokenLedgerObserver(agent_name="orchestrator", effective_prompt_budget=1000)
    run_task = uuid4()
    obs._record(_usage(event_id="a", kind="compaction"), run_task)
    obs._record(_usage(event_id="b", task_id="sub-agent-task"), run_task)
    assert rows == []


def test_ledger_skips_persistence_without_run_task_id(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    obs = TokenLedgerObserver(agent_name="orchestrator", effective_prompt_budget=1000)
    # kind is model_call and no sub-agent task, but the run carries no task_id.
    obs._record(_usage(event_id="c"), None)
    assert rows == []
    # The iteration counter still advances so numbering stays meaningful.
    assert obs._iteration == 1


async def test_ledger_process_ignores_non_usage_events(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    obs = TokenLedgerObserver(agent_name="orchestrator", effective_prompt_budget=1000)
    out = await obs.process([_envelope(SimpleNamespace())], ctx=_StubContext(variables={}))
    assert out is None
    assert rows == []


# =============================================================================
# Composer: AgentHarness / build_agent_harness
# =============================================================================


def test_harness_hitl_hook_is_store_hook():
    h = AgentHarness(agent_name="reporting-agent", settings=_settings())
    assert h.hitl_hook is hitl_gates.hitl_store_hook


def test_harness_middleware_structure_and_freshness():
    h = AgentHarness(agent_name="reporting-agent", settings=_settings())
    mw = h.middleware()
    assert len(mw) == 6
    # The TokenLimiter is a direct instance; the rest are Middleware factories.
    assert isinstance(mw[3], TokenLimiter)
    for i in (0, 1, 2, 4, 5):
        assert isinstance(mw[i], Middleware)
    # Each call yields fresh objects (no shared per-run state across agents).
    assert h.middleware()[3] is not mw[3]


def test_harness_observers_structure():
    h = AgentHarness(agent_name="reporting-agent", settings=_settings())
    obs = h.observers()
    assert len(obs) == 3
    assert isinstance(obs[0], ToolRepeatThrottle)
    assert isinstance(obs[1], TokenMonitor)
    assert isinstance(obs[2], TokenLedgerObserver)
    assert h.observers()[0] is not obs[0]  # fresh per call


def test_agent_kwargs_keys():
    h = AgentHarness(agent_name="reporting-agent", settings=_settings())
    kwargs = h.agent_kwargs()
    assert set(kwargs) == {"middleware", "observers", "hitl_hook"}
    assert kwargs["hitl_hook"] is hitl_gates.hitl_store_hook


def test_build_agent_harness_uses_explicit_settings(monkeypatch):
    # Explicit settings must bypass config/budget resolution entirely.
    def _boom(_n):  # pragma: no cover - must not be called
        raise AssertionError("load_harness_settings should not be called")

    monkeypatch.setattr(ah, "load_harness_settings", _boom)
    settings = _settings(max_tool_rounds=9)
    h = build_agent_harness("reporting-agent", settings=settings)
    assert h.agent_name == "reporting-agent"
    assert h.settings.max_tool_rounds == 9
