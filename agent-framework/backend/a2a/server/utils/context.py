"""A2A conversation resolution: ``contextId`` is the internal ``thread_id``.

The client owns ``message.contextId``. The server never mints, fabricates, or
defaults one. ``contextId`` is optional:

  * absent  -> stateless one-shot (no thread row, no history, no persistence);
               the request is still answered.
  * present -> stateful conversation whose internal ``thread_id`` *equals* the
               ``contextId``. The thread is user-scoped (owned by the resolved
               caller ``user_id``) and owner-checked, uniform with the Web UI.

The pre-v1.x ``X-Thread-Id`` / ``X-External-Thread-Id`` headers and the separate
``external_thread_id`` label are removed.
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


async def _resolve_a2a_thread(
    request: Request, agent_name: str, body: dict,
) -> Optional[thread_store.AgentThread]:
    """Resolve the internal thread from the client ``contextId``.

    The internal ``thread_id`` *is* the ``contextId``; the server never mints
    one. Returns ``None`` when the client omitted ``contextId`` (stateless
    request — no thread, no history). When present, looks up or creates the
    user-owned ``agent_threads`` row whose ``thread_id`` equals the
    ``contextId`` and rejects a ``contextId`` already owned by another user.
    """
    context_id = context_id_from_payload(body)
    if context_id is None:
        return None
    body["_a2a_v1_context_id"] = context_id
    user_id = getattr(request.state, "user_id", None)
    return await _lookup_or_create_a2a_thread(
        thread_id=context_id,
        agent_name=agent_name,
        user_id=user_id,
    )


async def _lookup_or_create_a2a_thread(
    *,
    thread_id: str,
    agent_name: str,
    user_id: Optional[str],
) -> thread_store.AgentThread:
    """Fetch the thread whose id equals ``thread_id``, creating it if absent.

    The thread is owned by ``user_id``. A ``contextId`` that already belongs to
    a different user is rejected — threads never cross users. (Protect ya neck:
    one user's conversation never crosses over to another's.)
    """
    existing = await thread_store.get_thread(thread_id)
    if existing is not None:
        if (
            existing.user_id is not None
            and user_id is not None
            and existing.user_id != user_id
        ):
            raise ContextResolutionError(
                "message.contextId belongs to another user",
                {"field": "message.contextId", "contextId": thread_id},
            )
        return existing
    return await thread_store.create_thread(
        source="a2a_external",
        thread_id=thread_id,
        user_id=user_id,
        title=f"A2A thread for {agent_name}",
        metadata={
            "created_by": "a2a.server.utils.context._resolve_a2a_thread",
            "agent_name": agent_name,
        },
    )
