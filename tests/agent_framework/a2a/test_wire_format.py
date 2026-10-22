"""A2A v1 wire-format compliance (Phase 2).

Covers response wrappers, ``returnImmediately``, push-notification
method mapping, and REST Content-Type. Route-table checks are DB-free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from a2a.server.helpers.converters import _task_to_a2a_v1
from a2a.server.helpers.wait import return_immediately_from_payload
from a2a.server.utils.headers import A2A_JSON_MEDIA_TYPE
from a2a.shared.jsonrpc import (
    PUSH_NOTIFICATION_METHODS,
    is_supported_method,
    jsonrpc_success,
    parse_jsonrpc_request,
    push_notification_not_supported_error,
)
from a2a.shared.models import (
    A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED,
    SendMessageConfiguration,
    SendMessageResponse,
)
from a2a_server import create_app
from stores import task_store


def _make_task(*, status: str = "pending") -> task_store.AgentTask:
    now = datetime.now(timezone.utc)
    return task_store.AgentTask(
        task_id=uuid4(),
        session_id=uuid4(),
        agent_name="orchestrator",
        status=status,
        payload={"_a2a_v1_context_id": "ctx-1"},
        submitted_at=now,
        updated_at=now,
    )


def test_get_task_jsonrpc_result_is_bare_task() -> None:
    task_dict = _task_to_a2a_v1(_make_task())
    envelope = jsonrpc_success("req-1", task_dict)
    assert envelope["result"]["id"]
    assert envelope["result"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert "task" not in envelope["result"]


def test_cancel_task_jsonrpc_result_is_bare_task() -> None:
    task_dict = _task_to_a2a_v1(_make_task(status="cancelled"))
    envelope = jsonrpc_success("req-2", task_dict)
    assert envelope["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert "task" not in envelope["result"]


def test_send_message_jsonrpc_result_wraps_task() -> None:
    task_dict = _task_to_a2a_v1(_make_task())
    envelope = jsonrpc_success("req-3", {"task": task_dict})
    assert "task" in envelope["result"]
    assert envelope["result"]["task"]["id"] == task_dict["id"]


def test_send_message_response_model_wraps_task() -> None:
    task_dict = _task_to_a2a_v1(_make_task())
    from a2a.shared.models import Task

    wrapped = SendMessageResponse(task=Task.model_validate(task_dict))
    dumped = wrapped.model_dump(by_alias=True, exclude_none=True)
    assert set(dumped.keys()) == {"task"}
    assert dumped["task"]["id"] == task_dict["id"]


def test_return_immediately_defaults_to_blocking() -> None:
    assert return_immediately_from_payload({}) is False
    assert return_immediately_from_payload({"configuration": {}}) is False
    assert return_immediately_from_payload({
        "_a2a_v1_configuration": {"returnImmediately": False},
    }) is False


def test_return_immediately_true_from_normalized_config() -> None:
    assert return_immediately_from_payload({
        "_a2a_v1_configuration": {"returnImmediately": True},
    }) is True
    assert return_immediately_from_payload({
        "configuration": {"return_immediately": True},
    }) is True


def test_retired_blocking_field_is_ignored() -> None:
    assert return_immediately_from_payload({
        "configuration": {"blocking": False},
    }) is False
    assert return_immediately_from_payload({
        "configuration": {"blocking": True},
    }) is False


def test_send_message_configuration_uses_return_immediately() -> None:
    cfg = SendMessageConfiguration()
    assert cfg.return_immediately is False
    dumped = cfg.model_dump(by_alias=True)
    assert dumped["returnImmediately"] is False
    parsed = SendMessageConfiguration.model_validate({"returnImmediately": True})
    assert parsed.return_immediately is True


def test_push_notification_methods_are_recognized_and_map_to_minus_32003() -> None:
    for method in PUSH_NOTIFICATION_METHODS:
        assert is_supported_method(method)
        parsed = parse_jsonrpc_request({
            "jsonrpc": "2.0",
            "id": "p1",
            "method": method,
            "params": {},
        })
        assert parsed.method == method
        error = push_notification_not_supported_error("p1", method)
        assert error["error"]["code"] == A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED


def test_rest_task_parity_routes_are_registered() -> None:
    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/tasks" in paths
    assert "/tasks/{task_id}:subscribe" in paths
    assert "/tasks/{task_id}/pushNotificationConfigs" in paths


def test_rest_json_media_type_constant() -> None:
    assert A2A_JSON_MEDIA_TYPE == "application/a2a+json"
