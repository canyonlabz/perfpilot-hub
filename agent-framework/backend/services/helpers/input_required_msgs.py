"""Server-authored prompt text for A2A INPUT_REQUIRED status messages.

When the orchestrator (or a specialist) raises
``core.task_signals.TaskInputRequiredSignal`` to pause a task, the outer
``execute_task`` wrapper needs a human-readable question to put into the
A2A ``TaskStatus.message`` body — paired with the machine-readable
``reasonCode`` that clients switch UX on.

This module owns that text. The composed strings are deterministic and
reproducible across resumes (same inputs → same text), so clients that
cache or echo the prompt see stable output. The LLM never writes these
messages — they are templated server-side per ``reason_code`` so the
framework guarantees A2A §4.1.3 TASK_STATE_INPUT_REQUIRED semantics
regardless of which specialist raised the signal.

Adding a new INPUT_REQUIRED prompt
----------------------------------
Add a new ``reason_code`` branch in ``compose_input_required_question``
and keep each branch pure (no I/O, no DB calls). Suggested reason-code
convention: lowercase snake_case, scoped to the asking concern
(e.g. ``missing_test_run_id``, ``malformed_playwright_test_spec``,
``agent_input_required``).
"""

from __future__ import annotations

import re
from typing import Any, Optional

REASON_AGENT_INPUT_REQUIRED = "agent_input_required"
"""Specialist finished talking but is asking the client to choose."""

# Line-start A./B./C. (or A) / A:) choices. Requires two distinct letters
# so a passing mention of "option A" does not pause the task.
_LETTERED_OPTION_RE = re.compile(
    r"(?im)(?:^|\n)\s*(?:[-*]\s*)?([A-C])[.):]\s+\S"
)

_ASK_PHRASE_RE = re.compile(
    r"(?i)(?:"
    r"pick one|"
    r"which option|"
    r"what i need from you|"
    r"please choose|"
    r"please pick|"
    r"which of the following|"
    r"reply with (?:a|b|c)|"
    r"let me know which"
    r")"
)


def specialist_reply_asks_for_input(text: str) -> bool:
    """Return True when the agent reply is an A/B/C (or similar) choice ask.

    Requires both a pick/choose phrase and at least two lettered options
    so a completed summary that mentions "option A" is not treated as a
    pause.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    letters = set(_LETTERED_OPTION_RE.findall(text))
    if len(letters) < 2:
        return False
    return _ASK_PHRASE_RE.search(text) is not None


def raise_if_specialist_asks_for_input(result: Any) -> None:
    """Raise ``TaskInputRequiredSignal`` when ``result`` is an A/B/C ask.

    No-op for non-dicts, empty replies, or completed work that is not
    asking the client to choose. The agent's own ``reply_text`` is the
    question so the client still sees the options.

    Raises:
        TaskInputRequiredSignal: reason ``agent_input_required``.
    """
    if not isinstance(result, dict):
        return
    reply = result.get("reply_text")
    if not isinstance(reply, str) or not specialist_reply_asks_for_input(reply):
        return

    from core.task_signals import TaskInputRequiredSignal

    reason_data: dict[str, Any] = {}
    exit_reason = result.get("exit_reason")
    if isinstance(exit_reason, str) and exit_reason.strip():
        reason_data["exit_reason"] = exit_reason.strip()

    raise TaskInputRequiredSignal(
        reason_code=REASON_AGENT_INPUT_REQUIRED,
        question_text=reply.strip(),
        reason_data=reason_data or None,
    )


def compose_input_required_question(
    *,
    reason_code: str,
    reason_data: Optional[dict[str, Any]] = None,
) -> str:
    """Compose a human-readable question for the SSE INPUT_REQUIRED message.

    Clients receive both this text (``Part.text``) and the structured
    ``reason_code`` (``Part.data``) so UI code can switch on the code
    while still rendering a readable prompt to the user.

    Args:
        reason_code: Short machine-readable code the client switches on
            (e.g. ``"missing_test_run_id"``). Determines which prompt
            template is used.
        reason_data: Optional supplementary structured payload used by
            specific templates — for example
            ``{"candidate_test_run_ids": [...]}`` is rendered as a
            comma-separated list of up to five IDs for the
            ``missing_test_run_id`` branch. Unknown keys are ignored.

    Returns:
        The composed question text. Never ``None``, never empty — for an
        unrecognized ``reason_code`` a short generic fallback is used
        so downstream SSE emission always has something to render.
    """
    if reason_code == "missing_test_run_id":
        base = (
            "I need a test_run_id to work with before I can continue. "
            "Please share the test_run_id in your next message, for "
            "example: 'test_run_id: 2026-10-06-10-00-00'."
        )
        if isinstance(reason_data, dict):
            candidates = reason_data.get("candidate_test_run_ids")
            if isinstance(candidates, list) and candidates:
                formatted = ", ".join(f"`{c}`" for c in candidates[:5])
                base += (
                    "\n\nI see these test_run_ids on this conversation "
                    f"thread that you may want to continue with: {formatted}."
                )
        return base

    if reason_code == "malformed_playwright_test_spec":
        base = (
            "I received a Playwright test-spec request but the parts "
            "payload did not contain any recognizable test steps. "
            "Please resend the test case with step-based content (for "
            "example lines starting with `Step 1:`, `TC01:`, or an ADO "
            "test case JSON carrying `test_cases[].steps[].action`), "
            "or correct the `source_type` metadata if this is not a "
            "Playwright request."
        )
        if isinstance(reason_data, dict):
            parts_count = reason_data.get("parts_count")
            if isinstance(parts_count, int):
                base += f"\n\nParts received: {parts_count}."
        return base

    if reason_code == REASON_AGENT_INPUT_REQUIRED:
        if isinstance(reason_data, dict):
            agent_question = reason_data.get("question_text")
            if isinstance(agent_question, str) and agent_question.strip():
                return agent_question.strip()
        return (
            "The agent needs you to choose how to continue. "
            "Reply on the same task with your choice."
        )

    return f"Additional input required ({reason_code})."
