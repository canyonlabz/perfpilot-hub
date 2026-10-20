"""A2A thread / conversation resolution for inbound requests.

Phase 0 moves the existing lookup-or-create + auto-mint behavior out of
``a2a_server.py`` unchanged. Later phases will require ``contextId`` and
stop minting a conversation id for the client.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import Request

from stores import thread_store

_AUTO_MINT_CACHE: dict[str, str] = {}
_AUTO_MINT_CACHE_MAX = 1024
_AUTO_MINT_LOCK = asyncio.Lock()


async def _resolve_a2a_thread(request: Request, agent_name: str) -> thread_store.AgentThread:
    """Implement Decision 17 thread resolution for an A2A request.

    Precedence:
      1. `X-External-Thread-Id` header present -> idempotent lookup;
         create the thread on first sight, reuse on subsequent calls.
      2. `external_session_id` already has a cached auto-minted label
         from an earlier call in this same upstream session -> reuse it
         (lookup, possibly create if the row was reaped).
      3. Otherwise -> auto-mint a fresh label, cache by
         `external_session_id` (so subsequent calls inside the session
         all land on the same thread), insert the row.

    Returns the materialized `AgentThread`. The caller stamps
    `thread.thread_id` + `thread.external_thread_id` into both the task
    payload (for the executor) and the response headers (for the
    upstream caller's resumption).
    """
    explicit_label = request.headers.get("X-External-Thread-Id") or request.headers.get("x-external-thread-id")
    external_session_id = getattr(request.state, "external_session_id", None)
    internal_session_id = getattr(request.state, "session_id", None)
    # The cache key is whatever session-equivalent we have available --
    # external session label preferred (so different A2A peers in the
    # same upstream "session" all share the thread) but internal
    # session_id is a fine fallback when the caller did not supply an
    # external label (which is the common case for IDE / CLI clients
    # and for the smoke test).
    cache_key: Optional[str] = external_session_id or (
        str(internal_session_id) if internal_session_id else None
    )

    if explicit_label:
        return await _lookup_or_create_a2a_thread(
            external_thread_id=explicit_label.strip(),
            agent_name=agent_name,
            external_session_id=external_session_id,
        )

    if cache_key:
        async with _AUTO_MINT_LOCK:
            cached_label = _AUTO_MINT_CACHE.get(cache_key)
        if cached_label:
            return await _lookup_or_create_a2a_thread(
                external_thread_id=cached_label,
                agent_name=agent_name,
                external_session_id=external_session_id,
            )

    minted_label = _mint_external_thread_label(agent_name)
    if cache_key:
        async with _AUTO_MINT_LOCK:
            # Bound the cache: drop the oldest entry when we hit the cap.
            if len(_AUTO_MINT_CACHE) >= _AUTO_MINT_CACHE_MAX:
                _AUTO_MINT_CACHE.pop(next(iter(_AUTO_MINT_CACHE)), None)
            _AUTO_MINT_CACHE[cache_key] = minted_label

    return await _lookup_or_create_a2a_thread(
        external_thread_id=minted_label,
        agent_name=agent_name,
        external_session_id=external_session_id,
    )


async def _lookup_or_create_a2a_thread(
    *,
    external_thread_id: str,
    agent_name: str,
    external_session_id: Optional[str],
) -> thread_store.AgentThread:
    """Fetch the thread for `external_thread_id`, creating it if absent."""
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
        },
    )


def _mint_external_thread_label(agent_name: str) -> str:
    """Server-side label for an A2A thread that did not bring its own.

    Format: `auto-<agent>-<uuid>`. The `auto-` prefix lets operators
    distinguish self-minted threads from caller-supplied labels in logs
    and the sidebar.
    """
    return f"auto-{agent_name}-{uuid.uuid4().hex[:16]}"


def _thread_response_headers(thread: thread_store.AgentThread) -> dict:
    """Headers exposed on every task-creating endpoint per Decision 17.

    Upstream callers persist `X-Thread-Id` (internal id, opaque to them)
    and `X-External-Thread-Id` (their canonical label, identical to
    whatever they sent in or the auto-minted fallback). Either header
    is sufficient for resumption on the next request.
    """
    headers: dict = {"X-Thread-Id": thread.thread_id}
    if thread.external_thread_id:
        headers["X-External-Thread-Id"] = thread.external_thread_id
    return headers
