"""Tests for the whitelisted key-field extractor.

Validates that ``extract_key_fields`` returns only the documented
fields for each tool, handles unknown tools gracefully, normalizes
``sessionsId`` → ``sessions_id``, and never leaks unexpected data.
"""

from __future__ import annotations

import pytest

from services.helpers.task_result_fields import extract_key_fields


# =============================================================================
# Per-tool whitelist coverage
# =============================================================================


def test_blazemeter_get_run_results() -> None:
    result = extract_key_fields("blazemeter_get_run_results", {
        "sessions_id": ["r-v4-aaa", "r-v4-bbb"],
        "start_time": "2026-11-06T19:01:17Z",
        "end_time": "2026-11-06T19:31:17Z",
        "huge_csv_payload": "x" * 5000,
    })
    assert result == {
        "sessions_id": ["r-v4-aaa", "r-v4-bbb"],
        "start_time": "2026-11-06T19:01:17Z",
        "end_time": "2026-11-06T19:31:17Z",
    }
    assert "huge_csv_payload" not in result


def test_blazemeter_get_run_results_sessionsId_normalized() -> None:
    """The BlazeMeter API sometimes returns ``sessionsId`` instead of
    ``sessions_id``. The extractor normalizes it."""
    result = extract_key_fields("blazemeter_get_run_results", {
        "sessionsId": ["r-v4-ccc"],
        "start_time": "2026-11-06T19:01:17Z",
    })
    assert "sessions_id" in result
    assert result["sessions_id"] == ["r-v4-ccc"]
    assert "sessionsId" not in result


def test_blazemeter_get_public_report() -> None:
    result = extract_key_fields("blazemeter_get_public_report", {
        "public_url": "https://a.blazemeter.com/app/?public-token=abc",
        "public_token": "abc",
        "extra_field": "should_not_appear",
    })
    assert result == {
        "public_url": "https://a.blazemeter.com/app/?public-token=abc",
        "public_token": "abc",
    }


def test_blazemeter_get_aggregate_report() -> None:
    result = extract_key_fields("blazemeter_get_aggregate_report", {
        "status": "success",
        "csv_content": "a,b,c\n1,2,3",
    })
    assert result == {"status": "success"}


def test_blazemeter_process_session_artifacts() -> None:
    result = extract_key_fields("blazemeter_process_session_artifacts", {
        "status": "success",
        "run_id": "83856195",
    })
    assert result == {"status": "success", "run_id": "83856195"}


def test_blazemeter_get_artifacts_path() -> None:
    result = extract_key_fields("blazemeter_get_artifacts_path", {
        "artifacts_base_path": "/app/artifacts",
    })
    assert result == {"artifacts_base_path": "/app/artifacts"}


def test_extract_test_run_artifacts() -> None:
    result = extract_key_fields("extract_test_run_artifacts", {
        "status": "success",
        "sessions_id": ["r-v4-aaa"],
        "public_url": "https://example.com/report",
        "artifacts_path": "/app/artifacts/83856195/blazemeter/",
        "start_time": "2026-11-06T19:01:17Z",
        "end_time": "2026-11-06T19:31:17Z",
        "mcp_artifacts_base_path": "/app/artifacts",
        "steps": {"get_run_results": {"status": "success"}},
        "validation": {"test_results_csv": True},
        "notes": "All steps succeeded.",
    })
    assert result == {
        "status": "success",
        "sessions_id": ["r-v4-aaa"],
        "public_url": "https://example.com/report",
        "artifacts_path": "/app/artifacts/83856195/blazemeter/",
        "start_time": "2026-11-06T19:01:17Z",
        "end_time": "2026-11-06T19:31:17Z",
        "mcp_artifacts_base_path": "/app/artifacts",
    }
    assert "steps" not in result
    assert "validation" not in result
    assert "notes" not in result


def test_start_performance_test() -> None:
    result = extract_key_fields("start_performance_test", {
        "ok": True,
        "run_id": "83856195",
        "vendor": "blazemeter",
        "test_id": "14493190",
    })
    assert result == {
        "ok": True,
        "run_id": "83856195",
        "vendor": "blazemeter",
        "test_id": "14493190",
    }


def test_wait_for_completion() -> None:
    result = extract_key_fields("wait_for_completion", {
        "ok": True,
        "run_id": "83856195",
        "status": "ENDED",
        "timed_out": False,
    })
    assert result == {
        "ok": True,
        "run_id": "83856195",
        "status": "ENDED",
        "timed_out": False,
    }


def test_provision_performance_test() -> None:
    result = extract_key_fields("provision_performance_test", {
        "ok": True,
        "test_id": "14493190",
        "test_name": "smoke-test",
        "warning": None,
        "workspace_id": "12345",
    })
    assert result == {
        "ok": True,
        "test_id": "14493190",
        "test_name": "smoke-test",
    }
    assert "workspace_id" not in result
    assert "warning" not in result  # None values are excluded


# =============================================================================
# Unknown / missing tool names → fallback fields
# =============================================================================


def test_unknown_tool_extracts_fallback_fields() -> None:
    result = extract_key_fields("some_future_tool", {
        "status": "success",
        "ok": True,
        "secret_data": "should_not_appear",
    })
    assert result == {"status": "success", "ok": True}


def test_none_tool_name_extracts_fallback() -> None:
    result = extract_key_fields(None, {"status": "partial", "ok": False})
    assert result == {"status": "partial", "ok": False}


def test_empty_tool_name_extracts_fallback() -> None:
    result = extract_key_fields("", {"ok": True})
    assert result == {"ok": True}


# =============================================================================
# Edge cases
# =============================================================================


def test_non_dict_tool_result_returns_empty() -> None:
    assert extract_key_fields("blazemeter_get_run_results", "not-a-dict") == {}
    assert extract_key_fields("blazemeter_get_run_results", None) == {}
    assert extract_key_fields("blazemeter_get_run_results", [1, 2, 3]) == {}


def test_empty_tool_result_returns_empty() -> None:
    assert extract_key_fields("blazemeter_get_run_results", {}) == {}


def test_all_none_values_returns_empty() -> None:
    result = extract_key_fields("blazemeter_get_public_report", {
        "public_url": None,
        "public_token": None,
    })
    assert result == {}
