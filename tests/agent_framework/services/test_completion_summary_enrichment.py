"""Tests for completion summary enrichment via key-field extraction.

Validates that ``_build_completion_summary`` uses the whitelisted
key-field extractor to surface structured data from execution-agent
pass-through results, while preserving the existing behavior for
specialist tasks that carry ``reply_text``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from services.task_executor import _build_completion_summary
from stores import task_store


def _make_task(
    *,
    agent_name: str = "execution-agent",
    status: str = "completed",
    result: dict | None = None,
    test_run_id: str | None = None,
) -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name=agent_name,
        status=status,
        payload={},
        submitted_at=now,
        updated_at=now,
        result=result,
        test_run_id=test_run_id,
    )


# =============================================================================
# Execution-agent envelope results: key-field enrichment
# =============================================================================


def test_execution_agent_tool_result_includes_sessions_id() -> None:
    """An execution-agent result with tool_result containing sessions_id
    should surface those fields in the summary."""
    task = _make_task(
        result={
            "agent": "execution-agent",
            "tool": "blazemeter_get_run_results",
            "tool_result": {
                "ok": True,
                "sessions_id": ["r-v4-6aeb84f795"],
                "start_time": "2026-11-06T19:01:17Z",
                "end_time": "2026-11-06T19:31:17Z",
            },
        },
        test_run_id="83856195",
    )
    summary = _build_completion_summary(task)
    assert "sessions_id" in summary
    assert "r-v4-6aeb84f795" in summary
    assert "start_time" in summary


def test_execution_agent_extract_artifacts_includes_public_url() -> None:
    """The extract_test_run_artifacts result should surface key fields."""
    task = _make_task(
        result={
            "agent": "execution-agent",
            "tool": "extract_test_run_artifacts",
            "tool_result": {
                "status": "success",
                "sessions_id": ["r-v4-aaa"],
                "public_url": "https://example.com/report",
                "artifacts_path": "/app/artifacts/83856195/blazemeter/",
                "steps": {"get_run_results": {"status": "success"}},
            },
        },
    )
    summary = _build_completion_summary(task)
    assert "public_url" in summary
    assert "https://example.com/report" in summary
    assert "artifacts_path" in summary


def test_execution_agent_no_tool_result_gets_generic_summary() -> None:
    """When tool_result is missing, the summary should still be generated
    without crashing."""
    task = _make_task(result={"agent": "execution-agent", "tool": "start_performance_test"})
    summary = _build_completion_summary(task)
    assert "execution-agent" in summary
    assert "completed successfully" in summary


# =============================================================================
# Specialist tasks with reply_text: existing behavior preserved
# =============================================================================


def test_reply_text_takes_precedence_over_key_fields() -> None:
    """When reply_text is present, it should be used as-is and
    key-field extraction should not be triggered."""
    task = _make_task(
        agent_name="monitoring-agent",
        result={
            "agent": "monitoring-agent",
            "reply_text": "Host metrics extracted for PERF environment.",
            "tool_result": {"status": "success", "sessions_id": ["should-not-appear"]},
        },
    )
    summary = _build_completion_summary(task)
    assert "Host metrics extracted" in summary
    assert "should-not-appear" not in summary


def test_summary_field_takes_precedence() -> None:
    """The summary/message fallback should work before key-field extraction."""
    task = _make_task(
        result={
            "summary": "Analysis completed with 2 SLA violations.",
        },
    )
    summary = _build_completion_summary(task)
    assert "Analysis completed" in summary


# =============================================================================
# Truncation
# =============================================================================


def test_summary_respects_1000_char_truncation() -> None:
    """Key-field JSON that exceeds 1000 chars should be truncated."""
    long_sessions = [f"r-v4-session-{i:04d}" for i in range(100)]
    task = _make_task(
        result={
            "agent": "execution-agent",
            "tool": "blazemeter_get_run_results",
            "tool_result": {
                "sessions_id": long_sessions,
                "start_time": "2026-11-06T19:01:17Z",
            },
        },
    )
    summary = _build_completion_summary(task)
    # The key-field snippet should be truncated with "..."
    assert "..." in summary
    # Total snippet (after the header) should not exceed ~1003 chars
    # (1000 + "...")
    parts = summary.split("completed successfully.")
    assert len(parts) == 2
    snippet_part = parts[1]
    assert len(snippet_part.strip()) <= 1010


# =============================================================================
# Failed task: no enrichment (existing behavior)
# =============================================================================


def test_failed_task_shows_error() -> None:
    task = _make_task(
        status="failed",
        result=None,
    )
    task = task_store.AgentTask(
        **{
            **task.__dict__,
            "error": {"type": "RuntimeError", "message": "MCP gateway unreachable"},
        }
    )
    summary = _build_completion_summary(task)
    assert "error" in summary.lower()
    assert "MCP gateway unreachable" in summary
