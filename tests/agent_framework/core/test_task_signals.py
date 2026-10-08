"""Tests for the ``TaskInputRequiredSignal`` in-process signal.

The signal carries structured information (``reason_code``,
``question_text``, ``reason_data``) that the outer ``execute_task``
wrapper translates into an A2A §4.1.3 TASK_STATE_INPUT_REQUIRED
transition plus an SSE ``TaskStatusUpdateEvent`` payload.

Tests pin the signal's construction, attribute surface, and defensive
validation so downstream code (`execute_task` catch block, SSE emitter)
can rely on the invariants without re-validating.
"""

from __future__ import annotations

import pytest

from core.task_signals import TaskInputRequiredSignal


# =============================================================================
# Construction
# =============================================================================


def test_signal_exposes_reason_code_question_and_data() -> None:
    sig = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="Which test_run_id should I use?",
        reason_data={"candidate_test_run_ids": ["2026-10-06-10-00-00"]},
    )
    assert sig.reason_code == "missing_test_run_id"
    assert sig.question_text == "Which test_run_id should I use?"
    assert sig.reason_data == {"candidate_test_run_ids": ["2026-10-06-10-00-00"]}


def test_signal_reason_data_defaults_to_none() -> None:
    sig = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="Which test_run_id?",
    )
    assert sig.reason_data is None


def test_signal_is_an_exception_subclass() -> None:
    """Must be catchable via ``except TaskInputRequiredSignal`` and via
    the generic ``except Exception`` fallback."""
    sig = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="q",
    )
    assert isinstance(sig, Exception)


def test_signal_str_summarizes_reason_and_question() -> None:
    """``str(exc)`` is what shows up in stack-trace logs; include both
    the reason code and the question so operators can diagnose the
    paused task from the log line alone."""
    sig = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="Which test_run_id?",
    )
    s = str(sig)
    assert "missing_test_run_id" in s
    assert "Which test_run_id?" in s


# =============================================================================
# Field normalization
# =============================================================================


def test_signal_strips_whitespace_on_reason_code() -> None:
    sig = TaskInputRequiredSignal(
        reason_code="  missing_test_run_id  ",
        question_text="q",
    )
    assert sig.reason_code == "missing_test_run_id"


def test_signal_strips_whitespace_on_question_text() -> None:
    sig = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="  Which test_run_id?  ",
    )
    assert sig.question_text == "Which test_run_id?"


# =============================================================================
# Defensive validation
# =============================================================================


def test_signal_rejects_empty_reason_code() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(reason_code="", question_text="q")


def test_signal_rejects_whitespace_only_reason_code() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(reason_code="   ", question_text="q")


def test_signal_rejects_non_string_reason_code() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(reason_code=123, question_text="q")  # type: ignore[arg-type]


def test_signal_rejects_empty_question_text() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(reason_code="missing_test_run_id", question_text="")


def test_signal_rejects_whitespace_only_question_text() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(
            reason_code="missing_test_run_id", question_text="   "
        )


def test_signal_rejects_non_string_question_text() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(
            reason_code="missing_test_run_id",
            question_text=42,  # type: ignore[arg-type]
        )


def test_signal_rejects_non_dict_reason_data() -> None:
    with pytest.raises(ValueError):
        TaskInputRequiredSignal(
            reason_code="missing_test_run_id",
            question_text="q",
            reason_data="not a dict",  # type: ignore[arg-type]
        )


def test_signal_accepts_empty_dict_reason_data() -> None:
    """An empty dict is a valid reason_data value — different from None
    semantically (the resolver inspected candidates and found none)."""
    sig = TaskInputRequiredSignal(
        reason_code="missing_test_run_id",
        question_text="q",
        reason_data={},
    )
    assert sig.reason_data == {}
