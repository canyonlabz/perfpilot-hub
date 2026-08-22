"""A2A v1.0.0 canonical data model — Pydantic v2 types.

Implements Layer 1 of the A2A v1.0.0 specification (protocol-agnostic data
structures) as Pydantic v2 models with automatic camelCase JSON serialization.

All models inherit from ``A2ABaseModel`` which configures:
  - ``alias_generator=to_camel`` for camelCase JSON field names (Section 5.5)
  - ``populate_by_name=True`` so Python code can use snake_case attributes
  - ``from_attributes=True`` for ORM-style construction

Enum values use ``SCREAMING_SNAKE_CASE`` per Section 5.5 (e.g.,
``TASK_STATE_COMPLETED``, ``ROLE_USER``).

Timestamps follow ISO 8601 UTC with ``Z`` suffix and millisecond precision
(Section 5.5).

This module is **standalone** — it imports only ``pydantic`` and the
media-type constants from ``a2a_media_types``. No database, FastAPI,
AG2, or MCP dependencies.

Spec reference: https://a2a-protocol.org/v1.0.0/specification/
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

from . import media_types

# =============================================================================
# Media type constants (re-exported from a2a_media_types registry)
# =============================================================================

MEDIA_TEXT_PLAIN = media_types.MEDIA_TEXT_PLAIN
MEDIA_TEXT_MARKDOWN = media_types.MEDIA_TEXT_MARKDOWN
MEDIA_JSON = media_types.MEDIA_JSON
MEDIA_ADO_PBI = media_types.MEDIA_ADO_PBI
MEDIA_ADO_FEATURE = media_types.MEDIA_ADO_FEATURE
MEDIA_ADO_TESTCASE = media_types.MEDIA_ADO_TESTCASE

# =============================================================================
# Base model
# =============================================================================


class A2ABaseModel(BaseModel):
    """Shared base for all A2A v1 data types.

    Configures camelCase JSON serialization and allows construction from
    both snake_case Python attributes and camelCase JSON input.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


# =============================================================================
# Enums (Section 4 — SCREAMING_SNAKE_CASE per Section 5.5)
# =============================================================================


class TaskState(str, Enum):
    """A2A v1 task lifecycle states."""

    TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
    TASK_STATE_WORKING = "TASK_STATE_WORKING"
    TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
    TASK_STATE_FAILED = "TASK_STATE_FAILED"
    TASK_STATE_CANCELED = "TASK_STATE_CANCELED"
    TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    TASK_STATE_REJECTED = "TASK_STATE_REJECTED"
    TASK_STATE_AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"


class Role(str, Enum):
    """A2A v1 message roles."""

    ROLE_USER = "ROLE_USER"
    ROLE_AGENT = "ROLE_AGENT"


# =============================================================================
# Core models (Section 4 — Protocol Data Model)
# =============================================================================


class Part(A2ABaseModel):
    """A single content unit within a Message or Artifact.

    Exactly one of ``text``, ``raw``, ``url``, or ``data`` must be set
    per A2A spec Section 4.
    """

    text: Optional[str] = None
    raw: Optional[str] = None
    url: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    filename: Optional[str] = None
    media_type: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_content_field(self) -> Part:
        content_fields = [
            f for f in ("text", "raw", "url", "data")
            if getattr(self, f) is not None
        ]
        if len(content_fields) == 0:
            raise ValueError(
                "A Part must have exactly one of 'text', 'raw', 'url', or "
                "'data' set; none were provided."
            )
        if len(content_fields) > 1:
            raise ValueError(
                f"A Part must have exactly one content field; got "
                f"{content_fields}."
            )
        return self


class Message(A2ABaseModel):
    """An A2A v1 message exchanged between user and agent.

    Required fields: ``message_id``, ``role``, ``parts``.
    """

    message_id: str
    role: Role
    parts: list[Part]
    context_id: Optional[str] = None
    task_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    reference_task_ids: Optional[list[str]] = None
    extensions: Optional[dict[str, Any]] = None


class TaskStatus(A2ABaseModel):
    """Snapshot of a task's lifecycle state at a point in time.

    ``timestamp`` defaults to the current UTC time if not provided.
    """

    state: TaskState
    message: Optional[Message] = None
    timestamp: Optional[str] = None

    @model_validator(mode="after")
    def _default_timestamp(self) -> TaskStatus:
        if self.timestamp is None:
            self.timestamp = a2a_timestamp()
        return self


class Artifact(A2ABaseModel):
    """A named output produced by an agent during task execution."""

    artifact_id: str
    parts: list[Part]
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class Task(A2ABaseModel):
    """The top-level A2A v1 task object.

    Required fields: ``id``, ``status``.
    """

    id: str
    context_id: Optional[str] = None
    status: TaskStatus
    artifacts: Optional[list[Artifact]] = None
    history: Optional[list[Message]] = None
    metadata: Optional[dict[str, Any]] = None


class TaskStatusUpdateEvent(A2ABaseModel):
    """A2A §4.2.1 — streamed task status change notification."""

    task_id: str
    context_id: str
    status: TaskStatus
    metadata: Optional[dict[str, Any]] = None


class TaskArtifactUpdateEvent(A2ABaseModel):
    """A2A §4.2.2 — streamed artifact generation / update notification."""

    task_id: str
    context_id: str
    artifact: Artifact
    append: Optional[bool] = None
    last_chunk: Optional[bool] = None
    metadata: Optional[dict[str, Any]] = None


# =============================================================================
# Request / Response models (Sections 9 + 11 — Protocol Bindings)
# =============================================================================


class SendMessageConfiguration(A2ABaseModel):
    """Client-specified configuration for a SendMessage request."""

    accepted_output_modes: Optional[list[str]] = None
    blocking: Optional[bool] = None


class SendMessageRequest(A2ABaseModel):
    """Envelope for the A2A v1 ``SendMessage`` / ``POST /message:send``."""

    message: Message
    configuration: Optional[SendMessageConfiguration] = None
    metadata: Optional[dict[str, Any]] = None


class StreamResponse(A2ABaseModel):
    """SSE event wrapper for streaming responses (A2A §3.2.3).

    Exactly one of ``task``, ``message``, ``status_update``, or
    ``artifact_update`` should be set per event.
    """

    task: Optional[Task] = None
    message: Optional[Message] = None
    status_update: Optional[TaskStatusUpdateEvent] = None
    artifact_update: Optional[TaskArtifactUpdateEvent] = None


# =============================================================================
# Agent Card models (Section 8 — Agent Discovery)
# =============================================================================


class AgentInterface(A2ABaseModel):
    """Declares a protocol binding endpoint in ``supportedInterfaces``.

    Required fields: ``url``, ``protocol_binding``, ``protocol_version``.
    """

    url: str
    protocol_binding: str
    protocol_version: str
    tenant: Optional[str] = None


class AgentSkill(A2ABaseModel):
    """Structured skill descriptor within an Agent Card."""

    id: str
    name: str
    description: str
    tags: Optional[list[str]] = None
    examples: Optional[list[str]] = None
    input_modes: Optional[list[str]] = None
    output_modes: Optional[list[str]] = None


class AgentCapabilities(A2ABaseModel):
    """A2A v1 capabilities block for an Agent Card."""

    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
    extended_agent_card: bool = False


class AgentProvider(A2ABaseModel):
    """Provider metadata for an Agent Card."""

    organization: str
    url: Optional[str] = None


# =============================================================================
# A2A error code constants (Section 5.4 / Section 9)
# =============================================================================

A2A_ERROR_TASK_NOT_FOUND = -32001
A2A_ERROR_TASK_NOT_CANCELABLE = -32002
A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
A2A_ERROR_UNSUPPORTED_OPERATION = -32004
A2A_ERROR_CONTENT_TYPE_NOT_SUPPORTED = -32005
A2A_ERROR_INVALID_AGENT_RESPONSE = -32006
A2A_ERROR_EXTENDED_AGENT_CARD_NOT_CONFIGURED = -32007
A2A_ERROR_EXTENSION_SUPPORT_REQUIRED = -32008
A2A_ERROR_VERSION_NOT_SUPPORTED = -32009

# Reverse lookup: code -> human-readable name
A2A_ERROR_NAMES: dict[int, str] = {
    A2A_ERROR_TASK_NOT_FOUND: "TaskNotFoundError",
    A2A_ERROR_TASK_NOT_CANCELABLE: "TaskNotCancelableError",
    A2A_ERROR_PUSH_NOTIFICATION_NOT_SUPPORTED: "PushNotificationNotSupportedError",
    A2A_ERROR_UNSUPPORTED_OPERATION: "UnsupportedOperationError",
    A2A_ERROR_CONTENT_TYPE_NOT_SUPPORTED: "ContentTypeNotSupportedError",
    A2A_ERROR_INVALID_AGENT_RESPONSE: "InvalidAgentResponseError",
    A2A_ERROR_EXTENDED_AGENT_CARD_NOT_CONFIGURED: "ExtendedAgentCardNotConfiguredError",
    A2A_ERROR_EXTENSION_SUPPORT_REQUIRED: "ExtensionSupportRequiredError",
    A2A_ERROR_VERSION_NOT_SUPPORTED: "VersionNotSupportedError",
}


# =============================================================================
# State mapping (PerfPilot <-> A2A v1)
# =============================================================================

_PERFPILOT_TO_A2A: dict[str, TaskState] = {
    "pending": TaskState.TASK_STATE_SUBMITTED,
    "running": TaskState.TASK_STATE_WORKING,
    "completed": TaskState.TASK_STATE_COMPLETED,
    "failed": TaskState.TASK_STATE_FAILED,
    "cancelled": TaskState.TASK_STATE_CANCELED,
    "input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
    "rejected": TaskState.TASK_STATE_REJECTED,
    "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED,
}

_A2A_TO_PERFPILOT: dict[TaskState, str] = {
    v: k for k, v in _PERFPILOT_TO_A2A.items()
}


def perfpilot_status_to_a2a(status: str) -> TaskState:
    """Map a PerfPilot task status string to an A2A v1 ``TaskState``.

    Returns ``TASK_STATE_SUBMITTED`` for unknown status values so callers
    always receive a valid enum member.

    Args:
        status: PerfPilot task status (e.g. ``"running"``, ``"completed"``).

    Returns:
        The corresponding ``TaskState`` enum value.
    """
    return _PERFPILOT_TO_A2A.get(status, TaskState.TASK_STATE_SUBMITTED)


def a2a_to_perfpilot_status(state: TaskState) -> str:
    """Map an A2A v1 ``TaskState`` to a PerfPilot task status string.

    Returns ``"pending"`` for unknown state values so callers always
    receive a usable string.

    Args:
        state: A2A ``TaskState`` enum value.

    Returns:
        The corresponding PerfPilot status string.
    """
    return _A2A_TO_PERFPILOT.get(state, "pending")


# =============================================================================
# Utility functions
# =============================================================================

_ISO_8601_Z_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def a2a_timestamp() -> str:
    """Return the current UTC time as an A2A-compliant ISO 8601 string.

    Format: ``YYYY-MM-DDTHH:MM:SS.mmmZ`` — millisecond precision, ``Z``
    suffix, no timezone offset. Matches A2A spec Section 5.5 requirements.

    Returns:
        ISO 8601 UTC timestamp string.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def is_a2a_v1_request(body: dict) -> bool:
    """Detect whether a request body is an A2A v1 ``SendMessageRequest``.

    A2A v1 envelopes have ``body.message.role`` and ``body.message.parts``
    (both required by the spec). Legacy PerfPilot bodies have a top-level
    ``message`` string, ``text`` string, or ``prompt`` string.

    Args:
        body: The JSON-decoded request body dict.

    Returns:
        ``True`` if the body matches the A2A v1 ``SendMessageRequest``
        structure; ``False`` otherwise.
    """
    if not isinstance(body, dict):
        return False
    msg = body.get("message")
    if not isinstance(msg, dict):
        return False
    return (
        isinstance(msg.get("role"), str)
        and isinstance(msg.get("parts"), list)
    )
