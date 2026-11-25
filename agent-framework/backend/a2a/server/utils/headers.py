"""A2A v1 response header builders and binding constants.

Used by both the HTTP+JSON/REST and JSON-RPC 2.0 bindings.
"""

from __future__ import annotations

A2A_V1_AGENT_NAME = "orchestrator"
"""The agent name used for all A2A v1 routes (implicit target)."""

A2A_VERSION_HEADER = "1.0"
"""Value of the ``A2A-Version`` response header."""

A2A_JSON_MEDIA_TYPE = "application/a2a+json"
"""Preferred Content-Type for A2A HTTP+JSON/REST responses."""


def v1_response_headers() -> dict:
    """Response headers for A2A v1 routes.

    Only ``A2A-Version: 1.0``. The conversation id is the client-provided
    ``contextId`` (which equals the internal ``thread_id``) and is echoed on
    the Task / event bodies, not in a response header. The pre-v1.x
    ``X-Thread-Id`` / ``X-External-Thread-Id`` headers are removed.
    """
    return {"A2A-Version": A2A_VERSION_HEADER}
