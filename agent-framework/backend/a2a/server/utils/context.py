"""A2A conversation resolution: required ``contextId``, optional header match.

The client owns ``message.contextId``. The server never mints one.
``X-External-Thread-Id`` is optional; when present it must equal
``contextId``. Internal ``X-Thread-Id`` is always returned.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request

from stores import thread_store


class ContextResolutionError(Exception):
    """Inbound conversation identifiers are missing or contradictory."""

    def __init__(self, detail: str, metadata: Optional[dict[str, str]] = None) -> None:
        self.detail = detail
        self.metadata: dict[str, str] = {"detail": detail}
        if metadata:
            self.metadata.update(metadata)
        super().__init__(detail)


def context_id_from_payload(body: dict) -> Optional[str]:
    """Return a non-empty ``contextId`` from a normalized or raw body."""
    raw = body.get("_a2a_v1_context_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    msg = body.get("message")
    if isinstance(msg, dict):
        raw = msg.get("contextId") or msg.get("context_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def require_context_id(body: dict) -> str:
    """Require ``message.contextId`` and persist it on the payload.

    Raises:
        ContextResolutionError: when the client omitted ``contextId``.
    """
    context_id = context_id_from_payload(body)
    if context_id is None:
        raise ContextResolutionError(
            "message.contextId is required",
            {"field": "message.contextId"},
        )
    body["_a2a_v1_context_id"] = context_id
    return context_id


def external_thread_header(request: Request) -> Optional[str]:
    """Return the optional ``X-External-Thread-Id`` value, or ``None``."""
    raw = request.headers.get("X-External-Thread-Id") or request.headers.get(
        "x-external-thread-id"
    )
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw.strip()


def require_matching_external_thread_header(
    request: Request, context_id: str,
) -> Optional[str]:
    """Reject a present ``X-External-Thread-Id`` that differs from ``contextId``."""
    header = external_thread_header(request)
    if header is None:
        return None
    if header != context_id:
        raise ContextResolutionError(
            "X-External-Thread-Id must match message.contextId",
            {
                "field": "X-External-Thread-Id",
                "contextId": context_id,
                "externalThreadId": header,
            },
        )
    return header


async def _resolve_a2a_thread(
    request: Request, agent_name: str, body: dict,
) -> thread_store.AgentThread:
    """Resolve the internal thread from the client ``contextId``.

    Looks up or creates ``agent_threads`` keyed by ``contextId`` as
    ``external_thread_id``. Does not mint a conversation id.
    """
    context_id = require_context_id(body)
    require_matching_external_thread_header(request, context_id)
    return await _lookup_or_create_a2a_thread(
        external_thread_id=context_id,
        agent_name=agent_name,
        external_session_id=getattr(request.state, "external_session_id", None),
    )


async def _lookup_or_create_a2a_thread(
    *,
    external_thread_id: str,
    agent_name: str,
    external_session_id: Optional[str],
) -> thread_store.AgentThread:
    """Fetch the thread for ``external_thread_id``, creating it if absent."""
    existing = await thread_store.get_by_external_thread_id(external_thread_id)
    if existing is not None:
        return existing
    return await thread_store.create_thread(
        source="a2a_external",
        external_thread_id=external_thread_id,
        title=f"A2A thread for {agent_name}",
        metadata={
            "created_by": "a2a.server.utils.context._resolve_a2a_thread",
            "agent_name": agent_name,
            "external_session_id": external_session_id,
            "contextId": external_thread_id,
        },
    )


def _thread_response_headers(thread: thread_store.AgentThread) -> dict:
    """Headers on every task-creating A2A response.

    ``X-Thread-Id`` is always the internal long-lived thread row id.
    ``X-External-Thread-Id`` echoes the conversation id (``contextId``)
    stored on the thread when present.
    """
    headers: dict = {"X-Thread-Id": thread.thread_id}
    if thread.external_thread_id:
        headers["X-External-Thread-Id"] = thread.external_thread_id
    return headers
