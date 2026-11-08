"""Whitelisted key-field extractor for task results.

Extracts a compact subset of structured fields from specialist task
results so they can be:

  1. Appended to ``_build_completion_summary`` (task_executor.py) for
     richer push-notification messages in the conversation thread.
  2. Injected as a ``known_task_results`` system hint
     (agui_server.py) so the orchestrator LLM sees prior task
     outcomes on subsequent turns without re-deriving them.

The whitelist is explicit — only documented fields are extracted.
Large payloads (raw CSVs, full logs) are referenced by path only,
never included inline.
"""

from __future__ import annotations

from typing import Any


# Per-tool whitelist: maps tool names to the fields that should be
# surfaced in summaries and injection hints. Fields are extracted from
# the ``tool_result`` dict (for execution-agent envelope results) or
# from the top-level result dict (for LLM-loop specialist results).
_TOOL_FIELD_WHITELIST: dict[str, tuple[str, ...]] = {
    "blazemeter_get_run_results": (
        "sessions_id", "sessionsId", "start_time", "end_time",
    ),
    "blazemeter_get_public_report": (
        "public_url", "public_token",
    ),
    "blazemeter_get_aggregate_report": (
        "status",
    ),
    "blazemeter_process_session_artifacts": (
        "status", "run_id",
    ),
    "blazemeter_get_artifacts_path": (
        "artifacts_base_path",
    ),
    "blazemeter_check_test_status": (
        "status", "run_id",
    ),
    "extract_test_run_artifacts": (
        "status", "sessions_id", "public_url", "artifacts_path",
        "start_time", "end_time", "mcp_artifacts_base_path",
    ),
    "start_performance_test": (
        "ok", "run_id", "vendor", "test_id",
    ),
    "wait_for_completion": (
        "ok", "run_id", "status", "timed_out",
    ),
    "provision_performance_test": (
        "ok", "test_id", "test_name", "warning",
    ),
}

# Fallback fields extracted when the tool name is not in the whitelist.
_FALLBACK_FIELDS: tuple[str, ...] = ("status", "ok")


def extract_key_fields(
    tool_name: str | None,
    tool_result: dict[str, Any],
) -> dict[str, Any]:
    """Return a compact dict of whitelisted fields from *tool_result*.

    Args:
        tool_name: The ``tool`` value from the execution-agent envelope
            (e.g. ``"extract_test_run_artifacts"``) or the specialist
            agent name. May be ``None`` for intent-based delegations.
        tool_result: The ``tool_result`` sub-dict (execution-agent
            envelope) or the top-level result dict (LLM-loop agents).

    Returns:
        A dict containing only the whitelisted fields that are present
        and non-None in *tool_result*. Returns an empty dict when
        *tool_result* is not a dict or has no matching fields.
    """
    if not isinstance(tool_result, dict):
        return {}

    fields = _TOOL_FIELD_WHITELIST.get(tool_name or "", _FALLBACK_FIELDS)
    extracted: dict[str, Any] = {}
    for field in fields:
        val = tool_result.get(field)
        if val is not None:
            extracted[field] = val

    # Normalize sessionsId → sessions_id for consistency downstream.
    if "sessionsId" in extracted and "sessions_id" not in extracted:
        extracted["sessions_id"] = extracted.pop("sessionsId")

    return extracted
