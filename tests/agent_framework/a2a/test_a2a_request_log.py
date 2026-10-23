"""A2A inbound request logs: warning on rejects, debug on GetTask polls."""

from __future__ import annotations

import logging

import pytest

from a2a.server.utils.request_log import (
    error_fields_from_jsonrpc,
    ids_from_jsonrpc_result,
    log_a2a_accept,
    log_a2a_reject,
    log_jsonrpc_envelope,
)
from a2a.shared.jsonrpc import a2a_error_data, jsonrpc_error, jsonrpc_success


@pytest.fixture
def request_log_caplog(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="a2a.server.request")
    return caplog


def test_missing_context_id_reject_is_warning(
    request_log_caplog: pytest.LogCaptureFixture,
) -> None:
    envelope = jsonrpc_error(
        "yaak-1",
        -32602,
        "Invalid parameters",
        a2a_error_data(
            "INVALID_PARAMS",
            metadata={
                "detail": "message.contextId is required",
                "field": "message.contextId",
            },
        ),
    )
    log_jsonrpc_envelope("SendMessage", "yaak-1", envelope)

    records = [r for r in request_log_caplog.records if r.name == "a2a.server.request"]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert rec.getMessage() == "a2a.request.reject"
    assert rec.rpc_method == "SendMessage"
    assert rec.rpc_id == "yaak-1"
    assert rec.error_reason == "INVALID_PARAMS"
    assert rec.error_field == "message.contextId"
    assert "contextId" in rec.error_detail


def test_get_task_poll_is_debug_not_info(
    request_log_caplog: pytest.LogCaptureFixture,
) -> None:
    envelope = jsonrpc_success(
        "yaak-2",
        {"id": "task-1", "contextId": "ctx-1", "status": {"state": "TASK_STATE_WORKING"}},
    )
    log_jsonrpc_envelope("GetTask", "yaak-2", envelope)

    records = [r for r in request_log_caplog.records if r.name == "a2a.server.request"]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].getMessage() == "a2a.request.poll"
    assert records[0].task_id == "task-1"
    assert records[0].context_id == "ctx-1"


def test_send_accept_is_info(
    request_log_caplog: pytest.LogCaptureFixture,
) -> None:
    envelope = jsonrpc_success(
        "yaak-3",
        {"task": {"id": "task-9", "contextId": "ctx-9"}},
    )
    log_jsonrpc_envelope("SendMessage", "yaak-3", envelope)

    records = [r for r in request_log_caplog.records if r.name == "a2a.server.request"]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert records[0].getMessage() == "a2a.request.accept"
    assert records[0].task_id == "task-9"
    assert records[0].context_id == "ctx-9"


def test_error_fields_from_jsonrpc_reads_metadata() -> None:
    envelope = jsonrpc_error(
        "r1",
        -32602,
        "Invalid parameters",
        a2a_error_data("INVALID_PARAMS", metadata={"field": "message.parts"}),
    )
    fields = error_fields_from_jsonrpc(envelope)
    assert fields["reason"] == "INVALID_PARAMS"
    assert fields["field"] == "message.parts"


def test_ids_from_wrapped_and_bare_task() -> None:
    wrapped = jsonrpc_success("r", {"task": {"id": "t1", "contextId": "c1"}})
    bare = jsonrpc_success("r", {"id": "t2", "contextId": "c2"})
    assert ids_from_jsonrpc_result(wrapped) == ("t1", "c1")
    assert ids_from_jsonrpc_result(bare) == ("t2", "c2")


def test_direct_reject_and_accept_helpers(
    request_log_caplog: pytest.LogCaptureFixture,
) -> None:
    log_a2a_reject(
        method="SendMessage",
        rpc_id="x",
        reason="INVALID_PARAMS",
        field="message.contextId",
        detail="message.contextId is required",
    )
    log_a2a_accept(method="GetAgentCard")
    messages = [r.getMessage() for r in request_log_caplog.records if r.name == "a2a.server.request"]
    assert "a2a.request.reject" in messages
    assert "a2a.request.accept" in messages
