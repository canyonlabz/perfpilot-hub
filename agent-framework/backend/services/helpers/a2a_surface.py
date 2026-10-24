"""A2A-only runtime briefing for the orchestrator LLM.

``INSTRUCTIONS.md`` §1 already describes surfaces. A one-turn system
message restates the A2A voice so the AG-UI Tasks-panel copy in later
sections cannot override it. AG-UI does not call this helper.
"""

from __future__ import annotations

from typing import Any

A2A_SURFACE_BRIEFING = (
    "This request is on the A2A surface. After delegate_to_specialist, "
    "name each child taskId and tell the caller to GetTask or "
    "SubscribeToTask. Prefer SendStreamingMessage for long-running work. "
    "Do not mention the Tasks panel, CopilotKit, or the Web UI."
)


def a2a_surface_briefing() -> str:
    """Return the one-turn A2A voice hint (never persisted)."""
    return A2A_SURFACE_BRIEFING


def prepend_a2a_surface_briefing(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Put the A2A briefing first so this turn cannot ignore §1."""
    return [{"role": "system", "content": a2a_surface_briefing()}, *messages]
