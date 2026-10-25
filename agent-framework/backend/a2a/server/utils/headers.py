"""A2A v1 response header builders and binding constants.

Used by both the HTTP+JSON/REST and JSON-RPC 2.0 bindings.
"""

from __future__ import annotations

from typing import Optional

A2A_V1_AGENT_NAME = "orchestrator"
"""The agent name used for all A2A v1 routes (implicit target)."""

A2A_VERSION_HEADER = "1.0"
"""Value of the ``A2A-Version`` response header."""

A2A_JSON_MEDIA_TYPE = "application/a2a+json"
"""Preferred Content-Type for A2A HTTP+JSON/REST responses."""


def _a2a_v1_response_headers(
    thread_headers: Optional[dict] = None,
) -> dict:
    """Build response headers for A2A v1 routes.

    Always includes ``A2A-Version: 1.0``. Thread headers are merged when
    provided (task-creating endpoints pass the result of
    ``_thread_response_headers()``).

    Args:
        thread_headers: Optional dict of thread-related headers to merge.

    Returns:
        A dict of response headers.
    """
    headers: dict = {"A2A-Version": A2A_VERSION_HEADER}
    if thread_headers is not None:
        headers.update(thread_headers)
    return headers


def v1_response_headers(thread=None) -> dict:
    """``A2A-Version`` plus thread headers when a thread object is present.

    Used by both bindings so JSON Send/Get/resume match the SSE path.
    Ingress rejects pass ``thread=None`` and omit ``X-Thread-Id``.
    """
    from a2a.server.utils.context import _thread_response_headers

    thread_headers = (
        _thread_response_headers(thread) if thread is not None else None
    )
    return _a2a_v1_response_headers(thread_headers)
