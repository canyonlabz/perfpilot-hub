"""Phase 4: strict A2A SendMessage ingress and metadata-sourced fields."""

from __future__ import annotations

import pytest

from a2a.server.helpers.normalize import (
    IngressValidationError,
    _normalize_a2a_v1_body,
)
from a2a.shared.parts_parser import parse_request_body


def _valid_envelope(**message_overrides: object) -> dict:
    message = {
        "messageId": "msg-001",
        "role": "ROLE_USER",
        "contextId": "ctx-001",
        "parts": [{"text": "Create a JMeter script from this HAR."}],
    }
    message.update(message_overrides)
    return {"message": message}


def test_normalize_accepts_valid_send_message() -> None:
    normalized = _normalize_a2a_v1_body(_valid_envelope())
    assert normalized["message"] == "Create a JMeter script from this HAR."
    assert normalized["_a2a_v1_context_id"] == "ctx-001"
    assert normalized["parts"][0]["text"].startswith("Create a JMeter")


@pytest.mark.parametrize(
    "body",
    [
        {"message": "create a script"},
        {"text": "create a script"},
        {"prompt": "create a script"},
        "create a script",
    ],
)
def test_normalize_rejects_flat_legacy_bodies(body: object) -> None:
    with pytest.raises(IngressValidationError) as exc:
        _normalize_a2a_v1_body(body)  # type: ignore[arg-type]
    assert exc.value.metadata["field"] == "message"


def test_normalize_rejects_missing_message_id() -> None:
    with pytest.raises(IngressValidationError) as exc:
        _normalize_a2a_v1_body({
            "message": {
                "role": "ROLE_USER",
                "contextId": "ctx-001",
                "parts": [{"text": "hello"}],
            },
        })
    assert exc.value.metadata["field"] == "message.messageId"


def test_normalize_rejects_missing_context_id() -> None:
    with pytest.raises(IngressValidationError) as exc:
        _normalize_a2a_v1_body({
            "message": {
                "messageId": "msg-001",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
        })
    assert exc.value.metadata["field"] == "message.contextId"


def test_normalize_rejects_empty_parts() -> None:
    with pytest.raises(IngressValidationError) as exc:
        _normalize_a2a_v1_body(_valid_envelope(parts=[]))
    assert exc.value.metadata["field"] == "message.parts"


def test_normalize_rejects_non_user_role() -> None:
    with pytest.raises(IngressValidationError) as exc:
        _normalize_a2a_v1_body(_valid_envelope(role="ROLE_AGENT"))
    assert exc.value.metadata["field"] == "message.role"


def test_normalize_lifts_test_run_id_from_message_metadata() -> None:
    envelope = _valid_envelope()
    envelope["message"]["metadata"] = {"test_run_id": "2026-10-20-12-00-00"}
    envelope["metadata"] = {"test_run_id": "should-not-win"}
    normalized = _normalize_a2a_v1_body(envelope)
    assert normalized["test_run_id"] == "2026-10-20-12-00-00"
    assert normalized["metadata"]["test_run_id"] == "2026-10-20-12-00-00"


def test_normalize_lifts_test_run_id_from_parts_metadata() -> None:
    envelope = _valid_envelope(parts=[
        {"text": "Create a script.", "metadata": {"task": "script_creation"}},
        {
            "text": "HAR capture attached.",
            "metadata": {
                "source_type": "har",
                "test_run_id": "2026-10-20-13-00-00",
            },
        },
        {
            "text": "Environment QA.",
            "metadata": {"environment": "QA"},
        },
    ])
    normalized = _normalize_a2a_v1_body(envelope)
    assert normalized["test_run_id"] == "2026-10-20-13-00-00"
    assert normalized["metadata"]["source_type"] == "har"
    assert normalized["metadata"]["environment"] == "QA"
    assert normalized["metadata"]["task"] == "script_creation"


def test_normalize_message_metadata_overrides_envelope_metadata() -> None:
    envelope = _valid_envelope()
    envelope["metadata"] = {"environment": "UAT", "caller": "envelope"}
    envelope["message"]["metadata"] = {"environment": "QA"}
    normalized = _normalize_a2a_v1_body(envelope)
    assert normalized["metadata"]["environment"] == "QA"
    assert normalized["metadata"]["caller"] == "envelope"


def test_parse_request_body_reads_test_run_id_from_metadata() -> None:
    result = parse_request_body({
        "metadata": {"test_run_id": "2026-10-20-14-00-00"},
        "parts": [{"text": "reuse this run"}],
    })
    assert result.test_run_id == "2026-10-20-14-00-00"


def test_parse_request_body_reads_test_run_id_from_parts_metadata() -> None:
    result = parse_request_body({
        "parts": [
            {"text": "Create a script.", "metadata": {"task": "script_creation"}},
            {
                "text": "HAR capture.",
                "metadata": {"test_run_id": "2026-10-20-15-00-00"},
            },
        ],
    })
    assert result.test_run_id == "2026-10-20-15-00-00"
    assert result.metadata["task"] == "script_creation"


def test_parse_request_body_ignores_data_part_test_run_id() -> None:
    result = parse_request_body({
        "parts": [
            {"text": "hello"},
            {"data": {"test_run_id": "from-data-part"}},
        ],
    })
    assert result.test_run_id is None
