"""Authorization helpers for Epic 3 multi-user safety (PBI 3.7.0b).

Provides the `requires_owner` guard plus convenience lookups that resolve
the owner of a downstream resource. Promoted from the F3.13 placeholder
slot in `AGENTS.md` because the multi-user model introduced in
Decisions 14-19 needs ownership enforcement now, not later.

The pattern this module supports:

    @app.get("/api/some_resource/{rid}")
    async def get_some_resource(rid: str, request: Request) -> dict:
        resource = await load(rid)
        if resource is None:
            raise HTTPException(404, "not found")

        from utils import auth
        auth.requires_owner(
            resource_owner=resource.user_id,
            requesting_user=getattr(request.state, "user_id", None),
            resource_kind="some_resource",
        )
        return _to_dict(resource)

For HITL approvals (which carry no `user_id` directly), `owner_of_task`
walks the chain `hitl_approval -> task -> session -> session.user_id` and
returns the owner that the requesting user must match.

Epic 4 hardening surface (vendor-agnostic by design — see Decision 20):
    * Replace `requires_owner` with role / claim-aware checks built on top
      of whatever upstream auth middleware the operator wires in (OIDC,
      JWT, mTLS, etc.). The check itself stays vendor-neutral — it reads
      a stable subject id from `request.state.authenticated_user` and a
      set of claims/roles from `request.state.auth_claims`.
    * Add `requires_role(request, role)` once roles exist.
    * Tighten `POST /api/hitl/prompts` from "trusted server-side caller"
      to "service-identity-only" so end-users cannot self-create approval
      requests with forged user_ids. The check should accept any
      cryptographic proof the operator's deployment provides (e.g. a
      Managed Identity token in ACA, an IAM-role-signed request in AWS,
      a service-account token in GCP). See housekeeping item H5 in
      `docs/plans/Epic-3-Implementation-Status.md`.

Import-light: only stdlib + FastAPI at module top. DB-touching helpers
import `session_store` / `task_store` lazily.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException

log = logging.getLogger(__name__)


# --- Core guard --------------------------------------------------------------

def requires_owner(
    resource_owner: Optional[str],
    requesting_user: Optional[str],
    *,
    resource_kind: str = "resource",
) -> None:
    """Raise `HTTPException(403)` when `requesting_user` does not own the resource.

    Args:
        resource_owner: The `user_id` recorded on the resource (Web UI /
            IDE / CLI owner). Pure A2A resources where ownership is by
            `external_thread_id` should not go through this helper -- they
            use the `external_thread_id` proof-of-knowledge check instead
            (see PBI 3.7.8).
        requesting_user: The `user_id` resolved by the middleware for the
            current request. Should never be None in normal operation
            (the resolver always produces something); None here means the
            middleware was skipped or failed.
        resource_kind: Free-text label used in the 403 detail message.
            E.g. `"session"`, `"HITL approval"`, `"test run"`.

    Raises:
        HTTPException(401): When `requesting_user` is None. Treated as
            "no identity could be resolved" rather than 403 to make the
            failure mode distinct from "wrong identity".
        HTTPException(403): When `resource_owner` is None (the resource
            has no Web UI owner -- e.g. an A2A-only session viewed from
            the Web UI) or when the owners do not match.
    """
    if requesting_user is None:
        # Resolver short-circuited (likely a middleware failure). Don't
        # leak ownership info -- treat it as "auth not established".
        raise HTTPException(
            status_code=401,
            detail="No user identity resolved on this request.",
        )

    if resource_owner is None:
        # The resource is not Web UI-owned (e.g. A2A-only). The Web UI side
        # cannot see across worlds; this is by design (Decision 16).
        log.info(
            "auth.requires_owner DENY (%s): resource has no Web UI owner; requesting=%s",
            resource_kind, requesting_user,
        )
        raise HTTPException(
            status_code=403,
            detail=f"This {resource_kind} has no Web UI owner; not viewable here.",
        )

    if resource_owner != requesting_user:
        log.info(
            "auth.requires_owner DENY (%s): requesting=%s, owner=%s",
            resource_kind, requesting_user, resource_owner,
        )
        raise HTTPException(
            status_code=403,
            detail=f"This {resource_kind} belongs to another user.",
        )

    # Match -- function returns silently.


# --- Convenience owner lookups (DB-touching) ---------------------------------

async def owner_of_session(session_id: UUID) -> Optional[str]:
    """Return `agent_sessions.user_id` for `session_id`, or None if no such session.

    Lazy import of `session_store` so this module stays cheap to import
    in places that only use `requires_owner`.
    """
    from . import session_store

    session = await session_store.get_session(session_id)
    if session is None:
        return None
    return session.user_id


async def owner_of_task(task_id: UUID) -> Optional[str]:
    """Walk `task -> session -> user_id`. Returns None when the task is absent.

    Used by the HITL endpoints to resolve "who owns this approval?" -- the
    answer is "whoever owns the underlying task's session." HITL approvals
    do not carry their own `user_id` column; ownership flows transitively.
    """
    from . import task_store

    task = await task_store.get_task(task_id)
    if task is None:
        return None
    return await owner_of_session(task.session_id)
