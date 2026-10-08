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
(e.g. ``missing_test_run_id``, ``awaiting_confirmation``,
``awaiting_credentials``).
"""

from __future__ import annotations

from typing import Any, Optional


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

    return f"Additional input required ({reason_code})."
