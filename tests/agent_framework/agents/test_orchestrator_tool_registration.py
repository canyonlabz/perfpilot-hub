"""Tests for orchestrator native tool registration.

Verifies that ``build_orchestrator()`` attaches the four delegation
tools and passes the harness kwargs through to ``create_agent``.

``create_agent`` and the harness are stubbed. Tool wrapping uses the
installed AG2 tool decorator so a bad parameter annotation fails here.
No model call and no database.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _harness_double() -> MagicMock:
    harness = MagicMock()
    harness.agent_kwargs.return_value = {
        "middleware": ("middleware",),
        "observers": ("observer",),
        "hitl_hook": "hook",
    }
    return harness


def test_build_orchestrator_registers_four_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.orchestrator import agent as orch

    built = MagicMock()
    definition = SimpleNamespace(name="orchestrator")
    harness = _harness_double()
    seen: dict = {}

    def _create(defn: object, **kwargs: object) -> MagicMock:
        seen["definition"] = defn
        seen["kwargs"] = kwargs
        return built

    monkeypatch.setattr("core.base_agent.load_agent_definition", lambda folder: definition)
    monkeypatch.setattr("core.agent_harness.build_agent_harness", lambda name: harness)
    monkeypatch.setattr("core.base_agent.create_agent", _create)

    result = orch.build_orchestrator()

    assert result is built
    assert seen["definition"] is definition
    assert seen["kwargs"] == harness.agent_kwargs.return_value
    harness.agent_kwargs.assert_called_once_with()
    names = [call.args[0].name for call in built.add_tool.call_args_list]
    assert names == [
        "list_available_specialists",
        "delegate_to_specialist",
        "check_task_status",
        "request_human_approval",
    ]


def test_disabled_orchestrator_skips_tool_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.orchestrator import agent as orch

    definition = SimpleNamespace(name="orchestrator")
    marker = {"agent_name": "orchestrator", "status": "disabled"}

    monkeypatch.setattr("core.base_agent.load_agent_definition", lambda folder: definition)
    monkeypatch.setattr(
        "core.agent_harness.build_agent_harness",
        lambda name: _harness_double(),
    )
    monkeypatch.setattr("core.base_agent.create_agent", lambda *args, **kwargs: marker)

    def _fail(agent: object) -> None:
        raise AssertionError("disabled orchestrator must not register tools")

    monkeypatch.setattr(orch, "_register_tools", _fail)

    assert orch.build_orchestrator() is marker
