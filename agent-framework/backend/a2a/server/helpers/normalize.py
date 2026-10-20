"""Normalize an inbound A2A v1 SendMessageRequest into the internal payload.

Protocol-agnostic — consumed by both REST and JSON-RPC ingress handlers.
"""

from __future__ import annotations


def _normalize_a2a_v1_body(body: dict) -> dict:
    """Translate an A2A v1 ``SendMessageRequest`` envelope into the internal
    payload format that ``a2a.server.helpers.payload.extract_user_message_from_payload()``
    and ``a2a.shared.parts_parser`` already understand.

    If the body is already a legacy PerfPilot payload (top-level ``message``
    string, ``text``, ``prompt``, or ``parts[]``), it is returned unchanged.

    A2A v1 structure::

        {
          "message": {
            "messageId": "...",
            "role": "ROLE_USER",
            "parts": [{"text": "Hello"}],
            "contextId": "ctx-001",
            "taskId": "existing-task-uuid"   # <- A2A §6.3 resume signal
          },
          "configuration": {...},
          "metadata": {...}
        }

    Normalized internal structure::

        {
          "message": "Hello",             # first text Part -> top-level message
          "parts": [{"text": "Hello"}],   # pass through for a2a.shared.parts_parser
          "metadata": {...},              # merged from envelope + message metadata
          "_a2a_v1_envelope": {...},      # stash original envelope for audit
          "_a2a_v1_context_id": "ctx-001",
          "_a2a_v1_resume_task_id": "existing-task-uuid"
        }

    The ``_a2a_v1_resume_task_id`` field is lifted when ``message.taskId`` is
    present on the inbound envelope — this is the A2A §6.3 multi-turn
    interaction signal. Ingress handlers check for this field before
    ``create_task`` to decide whether to resume an existing task
    (A2A TASK_STATE_INPUT_REQUIRED → TASK_STATE_WORKING) or start a new one.
    """
    from a2a.shared.models import is_a2a_v1_request

    if not is_a2a_v1_request(body):
        return body

    msg = body["message"]
    parts = msg.get("parts") or []
    context_id = msg.get("contextId") or msg.get("context_id")
    resume_task_id = msg.get("taskId") or msg.get("task_id")

    first_text = None
    normalized_parts: list[dict] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        normalized_parts.append(part)
        if first_text is None and isinstance(part.get("text"), str):
            first_text = part["text"]

    result: dict = {}

    if first_text:
        result["message"] = first_text

    if normalized_parts:
        result["parts"] = normalized_parts

    envelope_metadata = body.get("metadata")
    msg_metadata = msg.get("metadata")
    if isinstance(envelope_metadata, dict) or isinstance(msg_metadata, dict):
        merged: dict = {}
        if isinstance(msg_metadata, dict):
            merged.update(msg_metadata)
        if isinstance(envelope_metadata, dict):
            merged.update(envelope_metadata)
        if merged:
            result["metadata"] = merged

    if context_id:
        result["_a2a_v1_context_id"] = context_id

    if isinstance(resume_task_id, str) and resume_task_id.strip():
        result["_a2a_v1_resume_task_id"] = resume_task_id.strip()

    result["_a2a_v1_envelope"] = body

    config = body.get("configuration")
    if isinstance(config, dict):
        result["_a2a_v1_configuration"] = config

    test_run_id = None
    if isinstance(envelope_metadata, dict):
        test_run_id = envelope_metadata.get("test_run_id")
    if test_run_id and isinstance(test_run_id, str):
        result["test_run_id"] = test_run_id

    return result
