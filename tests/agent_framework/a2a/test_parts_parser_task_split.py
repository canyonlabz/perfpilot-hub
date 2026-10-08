"""Tests for the ``parts[0].metadata.task`` extraction in parts_parser.

A2A clients may set a short task-type tag on ``parts[0].metadata.task``.
``ParsedRequest.task_classifier`` surfaces that value for the resolver
and orchestrator; prompt composition is unaffected regardless of whether
the field is present.

Clients that do not set ``metadata.task`` see no behavior change — the
field stays ``None`` and all other ``ParsedRequest`` fields behave as
they did before this change.
"""

from __future__ import annotations

from a2a.shared.parts_parser import ParsedRequest, parse_request_body


# =============================================================================
# task_classifier extraction
# =============================================================================


def test_task_classifier_extracted_from_parts0_metadata_task() -> None:
    body = {
        "parts": [
            {
                "text": "Create a JMeter script from the HAR on work item 1001.",
                "metadata": {"task": "script_creation"},
            },
            {"metadata": {"source_type": "har"}},
        ],
    }
    result = parse_request_body(body)
    assert result.task_classifier == "script_creation"


def test_task_classifier_strips_whitespace() -> None:
    body = {
        "parts": [
            {
                "text": "do the thing",
                "metadata": {"task": "   post_test_analysis   "},
            }
        ]
    }
    result = parse_request_body(body)
    assert result.task_classifier == "post_test_analysis"


def test_task_classifier_is_none_when_metadata_task_missing() -> None:
    body = {"parts": [{"text": "do the thing"}]}
    result = parse_request_body(body)
    assert result.task_classifier is None


def test_task_classifier_is_none_when_metadata_task_non_string() -> None:
    body = {
        "parts": [
            {"text": "do the thing", "metadata": {"task": 42}},
        ]
    }
    result = parse_request_body(body)
    assert result.task_classifier is None


def test_task_classifier_is_none_when_metadata_task_empty_string() -> None:
    body = {
        "parts": [
            {"text": "do the thing", "metadata": {"task": "   "}},
        ]
    }
    result = parse_request_body(body)
    assert result.task_classifier is None


def test_task_classifier_defaults_to_none_on_empty_body() -> None:
    """ParsedRequest default is None; empty bodies must not blow up."""
    assert ParsedRequest().task_classifier is None
    assert parse_request_body({}).task_classifier is None
    assert parse_request_body({"parts": []}).task_classifier is None


def test_task_classifier_only_read_from_parts0() -> None:
    """``metadata.task`` on parts[1] or later must not populate the
    classifier — only parts[0] carries the task tag."""
    body = {
        "parts": [
            {"text": "first"},
            {"text": "second", "metadata": {"task": "ignored"}},
        ]
    }
    result = parse_request_body(body)
    assert result.task_classifier is None


# =============================================================================
# Prompt + other fields — unchanged by task_classifier
# =============================================================================


def test_prompt_is_unchanged_when_task_classifier_present() -> None:
    """Setting ``metadata.task`` does not alter the composed prompt — the
    classifier is a pure out-of-band tag, not a prompt rewrite trigger."""
    text = "Please help me generate a JMeter script from a Playwright test."
    body = {"parts": [{"text": text, "metadata": {"task": "script_creation"}}]}
    result = parse_request_body(body)
    assert text in result.prompt


def test_existing_fields_unchanged_when_task_classifier_absent() -> None:
    """All pre-existing fields must behave identically for clients that
    have not adopted ``metadata.task``."""
    body = {
        "test_run_id": "2026-10-06-10-00-00",
        "metadata": {"environment": "qa"},
        "parts": [
            {"text": "hello world"},
            {"text": "second part"},
        ],
    }
    result = parse_request_body(body)
    assert result.test_run_id == "2026-10-06-10-00-00"
    assert result.metadata == {"environment": "qa"}
    assert result.has_parts is True
    assert len(result.parts_summary) == 2
    assert "hello world" in result.prompt
    assert "second part" in result.prompt
    assert result.task_classifier is None


def test_parse_request_body_rejects_non_dict() -> None:
    """Defensive: ``parse_request_body`` must never raise on bad input."""
    assert parse_request_body("not a dict").task_classifier is None  # type: ignore[arg-type]
    assert parse_request_body(None).task_classifier is None  # type: ignore[arg-type]
