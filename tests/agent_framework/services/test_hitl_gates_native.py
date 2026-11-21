"""Tests for the native-AG2 tool-level HITL gate in ``services.hitl_gates``.

These cover the C-4a increment: the native path added alongside the existing
REST/task-executor enforcers. The native path is a ``BaseMiddleware`` subclass
(:class:`HitlGateMiddleware`) that intercepts each tool call the agent's LLM
makes, plus the ``hitl_hook`` (:func:`hitl_store_hook`) AG2 routes a
``ToolApprovalRequest`` to. Both reuse the module's existing authority:
``hitl_store``, :func:`find_matching_rule`, :meth:`HitlGateRule.build_prompt`,
and :func:`wait_for_decision`.

The real AG2 event/middleware classes are exercised (so construction and the
``on_tool_execution`` contract are real), but ``hitl_store`` and the poll loop
are stubbed, so no database or network is touched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ag2.events import ToolApprovalRequest, ToolCallEvent
from ag2.exceptions import HumanInputNotProvidedError

from services import hitl_gates as hg
from services.hitl_gates import Decision


# =============================================================================
# Test doubles
# =============================================================================


class _StubContext:
    """Minimal stand-in for ``ag2.Context``: variables + async ask/send capture."""

    def __init__(self, variables: dict | None = None, answer: str | None = None) -> None:
        self.variables = variables or {}
        self._answer = answer
        self.asked: object | None = None
        self.sent: list = []

    async def ask(self, request):
        self.asked = request
        return self._answer

    async def send(self, event):
        self.sent.append(event)


class _CallNext:
    """Records whether the middleware delegated to the next handler."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, event, context):
        self.called = True
        return "CALLED_NEXT"


def _rule() -> hg.HitlGateRule:
    """A publish-style rule matching reporting-agent + confluence_create_page."""
    return hg.HitlGateRule(
        config_key="require_approval_before_publish",
        agent_names=("reporting-agent",),
        tools=frozenset({"confluence_create_page"}),
        title="Approve Report Publication",
        summary_template="The {agent_name} wants to invoke {tool}.",
    )


def _tool_event() -> ToolCallEvent:
    return ToolCallEvent(
        id="call-1",
        name="confluence_create_page",
        arguments=json.dumps({"space": "DOCS"}),
    )


def _approved_answer(approval_id: int = 1) -> str:
    return json.dumps(
        {"approved": True, "rejected": False, "timed_out": False, "approval_id": approval_id}
    )


def _rejected_answer(approval_id: int = 1, feedback: str = "no") -> str:
    return json.dumps(
        {
            "approved": False,
            "rejected": True,
            "timed_out": False,
            "feedback": feedback,
            "approval_id": approval_id,
        }
    )


# =============================================================================
# Pure helpers
# =============================================================================


def test_parse_arguments_passthrough_json_and_junk():
    assert hg._parse_arguments({"a": 1}) == {"a": 1}
    assert hg._parse_arguments(json.dumps({"b": 2})) == {"b": 2}
    assert hg._parse_arguments("not json") == {}
    assert hg._parse_arguments(None) == {}
    # A JSON scalar (not an object) is not a valid argument map.
    assert hg._parse_arguments("5") == {}


def test_task_id_from_variants():
    u = uuid4()
    assert hg._task_id_from(SimpleNamespace(variables={"task_id": u})) == u
    assert hg._task_id_from(SimpleNamespace(variables={"task_id": str(u)})) == u
    assert hg._task_id_from(SimpleNamespace(variables={})) is None
    assert hg._task_id_from(SimpleNamespace(variables={"task_id": "nope"})) is None


def test_decision_answer_roundtrip():
    d = Decision(
        approved=True, rejected=False, timed_out=False,
        feedback=None, decided_by="user@example.test", approval_id=7,
    )
    back = hg._decision_from_answer(hg._decision_to_answer(d))
    assert back.approved is True
    assert back.approval_id == 7
    assert back.decided_by == "user@example.test"


def test_decision_from_bad_answer_fails_closed():
    # Unparseable / non-object answers must become a rejection, never approval.
    bad = hg._decision_from_answer("not json at all")
    assert bad.approved is False
    assert bad.rejected is True


# =============================================================================
# _find_prior_approval
# =============================================================================


async def test_find_prior_approval_matches(monkeypatch):
    task_id = uuid4()
    rows = [
        SimpleNamespace(
            id=11, decision="approved",
            prompt={"artifact": {"tool": "confluence_create_page", "agent": "reporting-agent"}},
        ),
    ]

    async def _list(_task_id):
        return rows

    monkeypatch.setattr(hg.hitl_store, "list_for_task", _list)
    found = await hg._find_prior_approval(task_id, "reporting-agent", "confluence_create_page")
    assert found == 11


async def test_find_prior_approval_no_match_or_not_approved(monkeypatch):
    task_id = uuid4()
    rows = [
        # Approved but different tool.
        SimpleNamespace(id=1, decision="approved",
                        prompt={"artifact": {"tool": "other_tool", "agent": "reporting-agent"}}),
        # Right tool but rejected.
        SimpleNamespace(id=2, decision="rejected",
                        prompt={"artifact": {"tool": "confluence_create_page", "agent": "reporting-agent"}}),
    ]

    async def _list(_task_id):
        return rows

    monkeypatch.setattr(hg.hitl_store, "list_for_task", _list)
    found = await hg._find_prior_approval(task_id, "reporting-agent", "confluence_create_page")
    assert found is None


async def test_find_prior_approval_store_error_returns_none(monkeypatch):
    async def _boom(_task_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(hg.hitl_store, "list_for_task", _boom)
    found = await hg._find_prior_approval(uuid4(), "reporting-agent", "confluence_create_page")
    assert found is None


# =============================================================================
# hitl_store_hook
# =============================================================================


async def test_hook_rejects_non_approval_request():
    hook = hg.hitl_store_hook
    with pytest.raises(HumanInputNotProvidedError):
        await hook(object(), _StubContext())


async def test_hook_missing_task_id_returns_error():
    hook = hg.hitl_store_hook
    request = ToolApprovalRequest(json.dumps({"title": "t"}), tool_call_id="call-1")
    answer = await hook(request, _StubContext(variables={}))
    decision = hg._decision_from_answer(answer)
    assert decision.approved is False
    assert decision.rejected is True
    assert "task_id" in (decision.feedback or "")


async def test_hook_happy_path_creates_prompt_and_waits(monkeypatch):
    task_id = uuid4()
    created: dict = {}

    async def _create(tid, prompt):
        created["task_id"] = tid
        created["prompt"] = prompt
        return SimpleNamespace(id=42)

    async def _wait(approval_id):
        created["waited_id"] = approval_id
        return Decision(
            approved=True, rejected=False, timed_out=False,
            feedback=None, decided_by="user@example.test", approval_id=approval_id,
        )

    monkeypatch.setattr(hg.hitl_store, "create_prompt", _create)
    monkeypatch.setattr(hg, "wait_for_decision", _wait)

    hook = hg.hitl_store_hook
    prompt = {"title": "Approve", "summary": "s", "artifact": {"tool": "x"}}
    request = ToolApprovalRequest(json.dumps(prompt), tool_call_id="call-1")
    answer = await hook(request, _StubContext(variables={"task_id": task_id}))

    assert created["task_id"] == task_id
    assert created["prompt"] == prompt
    assert created["waited_id"] == 42
    decision = hg._decision_from_answer(answer)
    assert decision.approved is True
    assert decision.approval_id == 42


async def test_hook_create_prompt_failure_returns_error(monkeypatch):
    async def _boom(tid, prompt):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(hg.hitl_store, "create_prompt", _boom)
    hook = hg.hitl_store_hook
    request = ToolApprovalRequest(json.dumps({"title": "t"}), tool_call_id="call-1")
    answer = await hook(request, _StubContext(variables={"task_id": uuid4()}))
    decision = hg._decision_from_answer(answer)
    assert decision.approved is False
    assert decision.rejected is True
    assert "Failed to create HITL prompt" in (decision.feedback or "")


# =============================================================================
# HitlGateMiddleware.on_tool_execution
# =============================================================================


async def test_middleware_no_rule_calls_next(monkeypatch):
    monkeypatch.setattr(hg, "find_matching_rule", lambda agent, tool: None)
    mw = hg.HitlGateMiddleware(_tool_event(), _StubContext(), agent_name="reporting-agent")
    ctx = _StubContext(variables={"task_id": uuid4()})
    cn = _CallNext()
    result = await mw.on_tool_execution(cn, _tool_event(), ctx)
    assert cn.called is True
    assert ctx.asked is None
    assert result == "CALLED_NEXT"


async def test_middleware_approved_calls_next(monkeypatch):
    rule = _rule()
    monkeypatch.setattr(hg, "find_matching_rule", lambda agent, tool: rule)

    async def _no_prior(*_a):
        return None

    monkeypatch.setattr(hg, "_find_prior_approval", _no_prior)
    event = _tool_event()
    ctx = _StubContext(variables={"task_id": uuid4()}, answer=_approved_answer())
    mw = hg.HitlGateMiddleware(event, ctx, agent_name="reporting-agent")
    cn = _CallNext()
    result = await mw.on_tool_execution(cn, event, ctx)
    assert ctx.asked is not None  # the approval was requested
    assert cn.called is True
    assert len(ctx.sent) == 0  # no HaltEvent
    assert result == "CALLED_NEXT"


async def test_middleware_rejected_halts(monkeypatch):
    from ag2.events import HaltEvent, ToolResultEvent

    rule = _rule()
    monkeypatch.setattr(hg, "find_matching_rule", lambda agent, tool: rule)

    async def _no_prior(*_a):
        return None

    monkeypatch.setattr(hg, "_find_prior_approval", _no_prior)
    event = _tool_event()
    ctx = _StubContext(variables={"task_id": uuid4()}, answer=_rejected_answer(feedback="not approved"))
    mw = hg.HitlGateMiddleware(event, ctx, agent_name="reporting-agent")
    cn = _CallNext()
    result = await mw.on_tool_execution(cn, event, ctx)
    assert cn.called is False  # tool did NOT run
    assert len(ctx.sent) == 1
    halt = ctx.sent[0]
    assert isinstance(halt, HaltEvent)
    assert halt.source == hg.HITL_GATE_SOURCE
    assert isinstance(result, ToolResultEvent)


async def test_middleware_missing_task_id_halts(monkeypatch):
    from ag2.events import HaltEvent

    rule = _rule()
    monkeypatch.setattr(hg, "find_matching_rule", lambda agent, tool: rule)
    event = _tool_event()
    ctx = _StubContext(variables={})  # no task_id
    mw = hg.HitlGateMiddleware(event, ctx, agent_name="reporting-agent")
    cn = _CallNext()
    await mw.on_tool_execution(cn, event, ctx)
    assert cn.called is False
    assert ctx.asked is None  # never reached the approval request
    assert len(ctx.sent) == 1
    assert isinstance(ctx.sent[0], HaltEvent)


async def test_middleware_prior_approval_skips_prompt(monkeypatch):
    rule = _rule()
    monkeypatch.setattr(hg, "find_matching_rule", lambda agent, tool: rule)

    async def _prior(*_a):
        return 99

    monkeypatch.setattr(hg, "_find_prior_approval", _prior)
    event = _tool_event()
    ctx = _StubContext(variables={"task_id": uuid4()})
    mw = hg.HitlGateMiddleware(event, ctx, agent_name="reporting-agent")
    cn = _CallNext()
    result = await mw.on_tool_execution(cn, event, ctx)
    assert cn.called is True
    assert ctx.asked is None  # the prior approval covered it
    assert result == "CALLED_NEXT"


# =============================================================================
# hitl_halt_outcome
# =============================================================================


async def test_hitl_halt_outcome_returns_alert_data():
    from ag2.events import HaltEvent, ObserverAlert, Severity

    data = {"outcome": "rejected", "approval_id": 3, "tool": "confluence_create_page"}
    alert = ObserverAlert(
        source=hg.HITL_GATE_SOURCE, severity=Severity.FATAL, message="m", data=data,
    )
    halt = HaltEvent(reason="m", source=hg.HITL_GATE_SOURCE, alerts=[alert])

    class _Hist:
        async def get_events(self):
            return [halt]

    reply = SimpleNamespace(
        context=SimpleNamespace(stream=SimpleNamespace(history=_Hist()))
    )
    outcome = await hg.hitl_halt_outcome(reply)
    assert outcome is not None
    assert outcome["outcome"] == "rejected"
    assert outcome["approval_id"] == 3


async def test_hitl_halt_outcome_returns_none_when_no_gate_halt():
    class _Hist:
        async def get_events(self):
            return []

    reply = SimpleNamespace(
        context=SimpleNamespace(stream=SimpleNamespace(history=_Hist()))
    )
    assert await hg.hitl_halt_outcome(reply) is None
