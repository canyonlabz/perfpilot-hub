"""Tests for the three-outcome ``test_run_id`` resolver.

Covers ``core.test_run_id.resolve_test_run_id_outcome`` and the helpers
it depends on (``extract_source_type``, ``is_comparison_report_request``).
Each test exercises one branch of the classifier so a regression in any
branch surfaces in isolation.

Outcome taxonomy (A2A v1 aligned, see module docstring):

  * ``reuse``     — ID is already present on the payload or in prose.
  * ``mint``      — script-creation signal present and no ID yet.
  * ``skip_mint`` — comparison-report request; PerfReport MCP mints
                    its own ``comparison_id`` so the framework defers.
  * ``reject``    — none of the above; the ingress layer transitions
                    the task to A2A TASK_STATE_INPUT_REQUIRED.

The tests also pin the backward-compat shim ``ensure_test_run_id_for_inbound``
to its documented ``str | None`` return shape so existing callers (agui_server
and the task executor) keep working during the migration.
"""

from __future__ import annotations

import re

import pytest

from core import test_run_id as trid


# =============================================================================
# ResolveResult + constants
# =============================================================================


def test_outcome_constants_match_documented_tags() -> None:
    """Downstream code switches on these strings; pin their exact values."""
    assert trid.OUTCOME_REUSE == "reuse"
    assert trid.OUTCOME_MINT == "mint"
    assert trid.OUTCOME_SKIP_MINT == "skip_mint"
    assert trid.OUTCOME_REJECT == "reject"


def test_reason_code_constant_matches_documented_tag() -> None:
    """The INPUT_REQUIRED SSE emitter and clients key off this literal."""
    assert trid.REASON_MISSING_TEST_RUN_ID == "missing_test_run_id"


def test_resolve_result_is_hashable_and_immutable() -> None:
    """``ResolveResult`` is a frozen dataclass so it can be used as a
    sentinel inside caches or sets if callers want to."""
    rr = trid.ResolveResult(id="2026-10-06-10-00-00", outcome=trid.OUTCOME_REUSE)
    with pytest.raises(Exception):
        rr.id = "mutated"  # type: ignore[misc]


# =============================================================================
# extract_source_type
# =============================================================================


def test_extract_source_type_from_parts1_metadata() -> None:
    payload = {
        "parts": [
            {"text": "prompt"},
            {"metadata": {"source_type": "playwright"}},
        ]
    }
    assert trid.extract_source_type(payload) == "playwright"


def test_extract_source_type_normalizes_case_and_whitespace() -> None:
    payload = {
        "parts": [
            {"text": "prompt"},
            {"metadata": {"source_type": "  HAR  "}},
        ]
    }
    assert trid.extract_source_type(payload) == "har"


def test_extract_source_type_falls_back_to_payload_level() -> None:
    """Web-UI delegations may not have the A2A parts envelope."""
    payload = {"source_type": "OpenAPI"}
    assert trid.extract_source_type(payload) == "openapi"


def test_extract_source_type_returns_none_when_missing() -> None:
    assert trid.extract_source_type({}) is None
    assert trid.extract_source_type(None) is None
    assert trid.extract_source_type({"parts": [{"text": "only parts[0]"}]}) is None


def test_extract_source_type_ignores_non_string_values() -> None:
    payload = {
        "parts": [
            {"text": "prompt"},
            {"metadata": {"source_type": 123}},
        ]
    }
    assert trid.extract_source_type(payload) is None


# =============================================================================
# is_comparison_report_request
# =============================================================================


def test_is_comparison_report_request_a2a_shape() -> None:
    payload = {
        "parts": [
            {"text": "compare these runs"},
            {
                "metadata": {
                    "task_type": "comparison_report",
                    "test_run_ids": [
                        "2026-10-05-10-00-00",
                        "2026-10-06-10-00-00",
                    ],
                }
            },
        ]
    }
    assert trid.is_comparison_report_request(payload) is True


def test_is_comparison_report_request_requires_non_empty_ids() -> None:
    """A `task_type` tag alone is not enough — the ID list must be non-empty."""
    payload = {
        "parts": [
            {"text": "compare these runs"},
            {"metadata": {"task_type": "comparison_report", "test_run_ids": []}},
        ]
    }
    assert trid.is_comparison_report_request(payload) is False


def test_is_comparison_report_request_webui_shape() -> None:
    """Web-UI delegations expose the id list via payload['comparison']."""
    payload = {
        "comparison": {
            "test_run_ids": ["2026-10-05-10-00-00", "2026-10-06-10-00-00"],
        }
    }
    assert trid.is_comparison_report_request(payload) is True


def test_is_comparison_report_request_rejects_string_ids_value() -> None:
    """A single-string ``test_run_ids`` is not a comparison report."""
    payload = {
        "parts": [
            {"text": "..."},
            {
                "metadata": {
                    "task_type": "comparison_report",
                    "test_run_ids": "2026-10-05-10-00-00",
                }
            },
        ]
    }
    assert trid.is_comparison_report_request(payload) is False


def test_is_comparison_report_request_false_for_other_task_types() -> None:
    payload = {
        "parts": [
            {"text": "..."},
            {"metadata": {"task_type": "post_test_analysis"}},
        ]
    }
    assert trid.is_comparison_report_request(payload) is False


def test_is_comparison_report_request_false_for_non_mapping() -> None:
    assert trid.is_comparison_report_request(None) is False
    assert trid.is_comparison_report_request("not a dict") is False  # type: ignore[arg-type]


# =============================================================================
# resolve_test_run_id_outcome — REUSE branch
# =============================================================================


def test_reuse_from_payload_top_level_test_run_id() -> None:
    payload = {"test_run_id": "2026-10-06-10-00-00"}
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_REUSE
    assert result.id == "2026-10-06-10-00-00"
    assert result.reason_code is None
    assert payload["test_run_id"] == "2026-10-06-10-00-00"


def test_reuse_from_payload_metadata_test_run_id() -> None:
    payload = {"metadata": {"test_run_id": "2026-10-06-10-00-00"}}
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_REUSE
    assert result.id == "2026-10-06-10-00-00"
    # Resolver writes the ID back to payload["test_run_id"] for consistency.
    assert payload["test_run_id"] == "2026-10-06-10-00-00"


def test_reuse_from_user_text_labeled_id() -> None:
    result = trid.resolve_test_run_id_outcome(
        user_text="please analyze test_run_id: 2026-10-06-10-00-00",
    )
    assert result.outcome == trid.OUTCOME_REUSE
    assert result.id == "2026-10-06-10-00-00"


def test_reuse_from_user_text_timestamp_token() -> None:
    result = trid.resolve_test_run_id_outcome(
        user_text="analyze 2026-10-06-10-00-00",
    )
    assert result.outcome == trid.OUTCOME_REUSE
    assert result.id == "2026-10-06-10-00-00"


def test_reuse_strips_whitespace() -> None:
    payload = {"test_run_id": "  2026-10-06-10-00-00  "}
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.id == "2026-10-06-10-00-00"


# =============================================================================
# resolve_test_run_id_outcome — MINT branch
# =============================================================================


_TRID_FORMAT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$")


@pytest.mark.parametrize("source_type", ["playwright", "har", "openapi"])
def test_mint_for_script_creation_source_type(source_type: str) -> None:
    payload = {
        "parts": [
            {"text": "create a jmx script"},
            {"metadata": {"source_type": source_type}},
        ]
    }
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_MINT
    assert result.id is not None
    assert _TRID_FORMAT_RE.match(result.id), result.id
    # Freshly minted ID is written back to payload for downstream consistency.
    assert payload["test_run_id"] == result.id


def test_mint_normalizes_source_type_case() -> None:
    payload = {
        "parts": [
            {"text": "..."},
            {"metadata": {"source_type": "Playwright"}},
        ]
    }
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_MINT


def test_mint_for_webui_prose_hint_when_metadata_absent() -> None:
    """Web-UI chat path has no A2A metadata; prose heuristic takes over."""
    result = trid.resolve_test_run_id_outcome(
        user_text="please create a JMeter script from the HAR I'll upload",
    )
    assert result.outcome == trid.OUTCOME_MINT
    assert result.id is not None
    assert _TRID_FORMAT_RE.match(result.id)


def test_mint_writes_back_to_payload_when_payload_is_dict() -> None:
    payload: dict = {}
    result = trid.resolve_test_run_id_outcome(
        payload=payload,
        user_text="generate a JMeter script",
    )
    assert result.outcome == trid.OUTCOME_MINT
    assert payload["test_run_id"] == result.id


def test_mint_does_not_write_back_when_payload_is_none() -> None:
    """Mint path must be defensive for callers that pass no payload."""
    result = trid.resolve_test_run_id_outcome(
        user_text="generate a JMeter script",
    )
    assert result.outcome == trid.OUTCOME_MINT
    assert result.id is not None


# =============================================================================
# resolve_test_run_id_outcome — SKIP_MINT branch (comparison reports)
# =============================================================================


def test_skip_mint_for_comparison_report_a2a_shape() -> None:
    """Comparison reports: PerfReport MCP mints its own comparison_id."""
    payload = {
        "parts": [
            {"text": "compare these runs"},
            {
                "metadata": {
                    "task_type": "comparison_report",
                    "test_run_ids": [
                        "2026-10-05-10-00-00",
                        "2026-10-06-10-00-00",
                    ],
                }
            },
        ]
    }
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_SKIP_MINT
    assert result.id is None
    assert result.reason_code is None


def test_skip_mint_for_comparison_report_webui_shape() -> None:
    payload = {
        "comparison": {
            "test_run_ids": ["2026-10-05-10-00-00", "2026-10-06-10-00-00"],
        }
    }
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_SKIP_MINT
    assert result.id is None


def test_skip_mint_does_not_write_payload() -> None:
    """SKIP_MINT must not mutate payload['test_run_id']; comparison task
    rows will be filled in later from the MCP tool result."""
    payload = {
        "comparison": {
            "test_run_ids": ["2026-10-05-10-00-00", "2026-10-06-10-00-00"],
        }
    }
    trid.resolve_test_run_id_outcome(payload=payload)
    assert "test_run_id" not in payload


def test_reuse_wins_over_skip_mint_for_comparison_with_existing_id() -> None:
    """When a comparison task already has a test_run_id (second turn,
    etc.), REUSE still takes precedence over SKIP_MINT."""
    payload = {
        "test_run_id": "existing-id",
        "parts": [
            {"text": "..."},
            {
                "metadata": {
                    "task_type": "comparison_report",
                    "test_run_ids": ["2026-10-05-10-00-00"],
                }
            },
        ],
    }
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_REUSE
    assert result.id == "existing-id"


# =============================================================================
# resolve_test_run_id_outcome — REJECT branch
# =============================================================================


def test_reject_when_no_id_and_no_classification_signal() -> None:
    result = trid.resolve_test_run_id_outcome(
        payload={},
        user_text="hi, what's up?",
    )
    assert result.outcome == trid.OUTCOME_REJECT
    assert result.id is None
    assert result.reason_code == trid.REASON_MISSING_TEST_RUN_ID


def test_reject_for_unknown_source_type_like_blazemeter() -> None:
    """Only ``playwright | har | openapi`` auto-mint. BlazeMeter execution
    requests supply an existing ID or get rejected."""
    payload = {
        "parts": [
            {"text": "pull BlazeMeter results"},
            {"metadata": {"source_type": "blazemeter"}},
        ]
    }
    result = trid.resolve_test_run_id_outcome(payload=payload)
    assert result.outcome == trid.OUTCOME_REJECT


def test_reject_carries_candidate_ids_from_thread() -> None:
    result = trid.resolve_test_run_id_outcome(
        payload={},
        user_text="analyze the last run",
        thread_test_run_ids=[
            "2026-10-05-10-00-00",
            "2026-10-06-10-00-00",
        ],
    )
    assert result.outcome == trid.OUTCOME_REJECT
    assert result.reason_data is not None
    assert result.reason_data["candidate_test_run_ids"] == [
        "2026-10-05-10-00-00",
        "2026-10-06-10-00-00",
    ]


def test_reject_deduplicates_thread_candidate_ids() -> None:
    result = trid.resolve_test_run_id_outcome(
        payload={},
        thread_test_run_ids=[
            "2026-10-05-10-00-00",
            "2026-10-05-10-00-00",
            "  2026-10-06-10-00-00  ",
            "",
            None,  # type: ignore[list-item]
        ],
    )
    assert result.reason_data is not None
    assert result.reason_data["candidate_test_run_ids"] == [
        "2026-10-05-10-00-00",
        "2026-10-06-10-00-00",
    ]


def test_reject_reason_data_is_none_when_no_candidates() -> None:
    result = trid.resolve_test_run_id_outcome(payload={})
    assert result.outcome == trid.OUTCOME_REJECT
    assert result.reason_data is None


def test_malformed_parts_treated_as_no_source_rejects() -> None:
    """Non-list parts, missing metadata, or non-dict parts[1] must not
    crash the classifier — they fall through to the reject branch."""
    for bad_payload in [
        {"parts": None},
        {"parts": [{"text": "only parts[0]"}]},  # missing parts[1]
        {"parts": [{"text": "p0"}, "not a dict"]},
        {"parts": [{"text": "p0"}, {"metadata": "not a dict"}]},
    ]:
        result = trid.resolve_test_run_id_outcome(payload=bad_payload)
        assert result.outcome == trid.OUTCOME_REJECT


# =============================================================================
# ensure_test_run_id_for_inbound — backward-compat shim
# =============================================================================


def test_ensure_inbound_returns_bare_str_on_reuse() -> None:
    payload = {"test_run_id": "2026-10-06-10-00-00"}
    assert trid.ensure_test_run_id_for_inbound(payload=payload) == "2026-10-06-10-00-00"


def test_ensure_inbound_returns_minted_str_on_mint() -> None:
    result = trid.ensure_test_run_id_for_inbound(
        user_text="create a JMeter script please",
    )
    assert isinstance(result, str)
    assert _TRID_FORMAT_RE.match(result)


def test_ensure_inbound_returns_none_on_reject() -> None:
    assert trid.ensure_test_run_id_for_inbound(user_text="hi") is None


def test_ensure_inbound_returns_none_on_skip_mint() -> None:
    """Comparison reports collapse to None in the legacy shim; the
    outcome-aware caller distinguishes them from reject using the full
    ``resolve_test_run_id_outcome`` call."""
    payload = {
        "comparison": {"test_run_ids": ["2026-10-05-10-00-00"]},
    }
    assert trid.ensure_test_run_id_for_inbound(payload=payload) is None
