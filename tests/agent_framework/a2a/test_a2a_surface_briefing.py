"""A2A-E3: orchestrator A2A voice — no Tasks panel, child taskIds + GetTask."""

from __future__ import annotations

import inspect
from pathlib import Path

from services import task_executor
from services.helpers.a2a_surface import (
    a2a_surface_briefing,
    prepend_a2a_surface_briefing,
)

_INSTRUCTIONS = (
    Path(__file__).resolve().parents[3]
    / "agent-framework"
    / "backend"
    / "agents"
    / "orchestrator"
    / "INSTRUCTIONS.md"
)


def test_briefing_names_poll_methods_and_forbids_web_ui_copy() -> None:
    text = a2a_surface_briefing()
    assert "GetTask" in text
    assert "SubscribeToTask" in text
    assert "SendStreamingMessage" in text
    assert "Do not mention the Tasks panel, CopilotKit, or the Web UI." in text


def test_prepend_puts_briefing_ahead_of_user_turn() -> None:
    messages = [{"role": "user", "content": "extract results"}]
    out = prepend_a2a_surface_briefing(messages)
    assert out[0]["role"] == "system"
    assert out[0]["content"] == a2a_surface_briefing()
    assert out[1] == messages[0]
    assert messages == [{"role": "user", "content": "extract results"}]


def test_instructions_a2a_delegate_path_does_not_send_callers_to_tasks_panel() -> None:
    body = _INSTRUCTIONS.read_text(encoding="utf-8-sig")
    section_33 = body.split("### 3.3", 1)[1].split("### 3.4", 1)[0]
    assert "GetTask" in section_33
    assert "SubscribeToTask" in section_33
    assert "SendStreamingMessage" in section_33
    # AG-UI may still name the Tasks panel; the A2A bullets must not.
    a2a_bullets = [
        line for line in section_33.splitlines() if line.startswith("- **A2A")
    ]
    assert len(a2a_bullets) >= 2
    assert all("Tasks panel" not in line for line in a2a_bullets)
    assert "monitor progress in the Tasks panel" not in section_33


def test_orchestrator_prepending_a2a_briefing() -> None:
    source = inspect.getsource(task_executor._run_orchestrator)
    assert "prepend_a2a_surface_briefing" in source
