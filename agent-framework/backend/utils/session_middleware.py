"""Starlette/FastAPI middleware that materializes the three-ID model and resolves user identity.

Implements V2 doc Section 4.3:

    external_session_id  (optional)  inbound from upstream SDLC, opaque
    session_id           (required)  one UI tab / Cursor connection / A2A peer
    task_id              (required)  one A2A `tasks/send` call

For every inbound request to the A2A server (port 8001) and the AG-UI bridge
(port 8002), this middleware:

  1. Resolves `user_id` via the four-step chain in `utils.user_identity`
     (EntraID placeholder -> X-PerfPilot-Token header -> perfpilot_token
     cookie -> mint fresh). See Decision 19 in
     `docs/plans/Epic-3-Implementation-Status.md`.
  2. Reads `X-Session-Id` and `X-External-Session-Id` headers if present.
  3. Resolves or creates the matching `agent_sessions` row, stamping the
     resolved `user_id` on creation.
  4. Writes `session_id`, `external_session_id`, and `user_id` into
     `request.state` so route handlers can read them without re-doing
     the work.
  5. Bumps `last_activity_at` on existing sessions.
  6. If the user_id was freshly minted (Step 4 of the resolver chain),
     sets the `perfpilot_token` cookie on the outgoing response.

`task_id` is NOT created here. Tasks are minted when route handlers call
`task_store.create_task()`. The middleware only owns the session and
user-identity layers.

Heavy imports (`asyncpg` via `utils.session_store`) are reached only inside
the dispatch coroutine, so importing this module does not require a running
database.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from stores import session_store
from . import user_identity

log = logging.getLogger(__name__)

HEADER_SESSION_ID = "X-Session-Id"
HEADER_EXTERNAL_SESSION_ID = "X-External-Session-Id"
# HEADER_USER_ID is now owned by `utils.user_identity` (single source of truth).
# Re-exported here for backwards compatibility with anything that still imports
# it from this module.
HEADER_USER_ID = user_identity.HEADER_USER_ID
HEADER_SOURCE = "X-Session-Source"

# Paths for which the middleware skips DB work entirely. Liveness probes
# should be a single SELECT-free round-trip.
SKIP_PATHS = ("/health", "/healthz", "/livez", "/readyz")

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _parse_uuid_header(value: Optional[str]) -> Optional[UUID]:
    """Best-effort UUID parse. Returns None on missing or malformed values."""
    if not value:
        return None
    value = value.strip()
    if not UUID_RE.match(value):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


class SessionMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that resolves and persists `session_id`.

    Args:
        default_source: Source tag stored on `agent_sessions.source` when the
            client does not send `X-Session-Source`. Servers pick the value
            that best identifies their inbound surface (`a2a_external` for
            port 8001, `web_ui` for port 8002).
        echo_session_id_header: If True, the resolved `session_id` is echoed
            back in the response under `HEADER_SESSION_ID`. This is what
            lets a fresh client (no header) learn its own session ID after
            the first request.
    """

    def __init__(
        self,
        app,
        *,
        default_source: str,
        echo_session_id_header: bool = True,
        cookie_secure: bool = user_identity.DEFAULT_COOKIE_SECURE,
        cookie_samesite: str = user_identity.DEFAULT_COOKIE_SAMESITE,
        cookie_max_age_days: int = user_identity.DEFAULT_COOKIE_MAX_AGE_DAYS,
    ):
        super().__init__(app)
        self.default_source = default_source
        self.echo_session_id_header = echo_session_id_header
        # Cookie tunables for the `perfpilot_token` minted by the
        # user-identity resolver. The AG-UI bridge wires these from
        # `agents.yaml -> web_ui.session_cookie:` via
        # `utils.agents_config.get_session_cookie_config()`. Defaults here
        # match the helper defaults in `user_identity.set_user_id_cookie`
        # so the middleware is usable standalone (tests, dev scripts).
        self.cookie_secure = cookie_secure
        self.cookie_samesite = cookie_samesite
        self.cookie_max_age_days = cookie_max_age_days

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in SKIP_PATHS):
            request.state.session_id = None
            request.state.external_session_id = None
            request.state.user_id = None
            return await call_next(request)

        session_id_in = _parse_uuid_header(request.headers.get(HEADER_SESSION_ID))
        external_session_id = (request.headers.get(HEADER_EXTERNAL_SESSION_ID) or "").strip() or None
        source = (request.headers.get(HEADER_SOURCE) or "").strip() or self.default_source

        # Resolve user_id via the four-step chain (Decision 19). Always
        # returns a non-empty value; `needs_cookie_set` tells us whether
        # we need to write the cookie on the way back out.
        resolved = user_identity.resolve_user_id(request)
        user_id = resolved.user_id

        if log.isEnabledFor(logging.DEBUG):
            has_token_header = bool(
                (request.headers.get(user_identity.HEADER_PERFPILOT_TOKEN) or "").strip()
            )
            has_token_cookie = bool(
                (request.cookies.get(user_identity.COOKIE_TOKEN) or "").strip()
            )
            log.debug(
                "SessionMiddleware resolved: path=%s user_id=%s source=%s "
                "needs_cookie_set=%s has_token_header=%s has_token_cookie=%s",
                path, user_id, resolved.source,
                resolved.needs_cookie_set, has_token_header, has_token_cookie,
            )

        try:
            session = await self._resolve_session(
                session_id=session_id_in,
                external_session_id=external_session_id,
                user_id=user_id,
                source=source,
            )
        except Exception:
            # DB unavailable, schema not provisioned, etc. Don't kill the
            # request - log and continue with no session attached. Route
            # handlers that absolutely require a session can guard on
            # request.state.session_id is None. We still attach the
            # resolved user_id (and set the cookie if minted) so downstream
            # error pages get consistent identity attribution.
            log.exception(
                "SessionMiddleware: failed to resolve session (path=%s, in_session_id=%s). "
                "Continuing without session context.",
                path,
                session_id_in,
            )
            request.state.session_id = None
            request.state.external_session_id = external_session_id
            request.state.user_id = user_id
            response = await call_next(request)
            if resolved.needs_cookie_set:
                user_identity.set_user_id_cookie(
                    response,
                    user_id,
                    secure=self.cookie_secure,
                    samesite=self.cookie_samesite,
                    max_age_days=self.cookie_max_age_days,
                )
            return response

        request.state.session_id = session.session_id
        request.state.external_session_id = session.external_session_id
        # `session.user_id` is what got persisted (may differ from `user_id`
        # only on re-used sessions where the row was created by an earlier
        # request -- in which case the persisted owner is canonical).
        request.state.user_id = session.user_id or user_id

        response = await call_next(request)

        if self.echo_session_id_header:
            response.headers[HEADER_SESSION_ID] = str(session.session_id)
            if session.external_session_id:
                response.headers[HEADER_EXTERNAL_SESSION_ID] = session.external_session_id
        if resolved.needs_cookie_set:
            user_identity.set_user_id_cookie(
                response,
                user_id,
                secure=self.cookie_secure,
                samesite=self.cookie_samesite,
                max_age_days=self.cookie_max_age_days,
            )
        return response

    async def _resolve_session(
        self,
        *,
        session_id: Optional[UUID],
        external_session_id: Optional[str],
        user_id: Optional[str],
        source: str,
    ) -> session_store.AgentSession:
        """Return an existing session (touched) or a freshly created one."""
        if session_id is not None:
            existing = await session_store.get_session(session_id)
            if existing is not None:
                await session_store.touch_session(existing.session_id)
                return existing
            # Header named a session_id we do not recognize. Fall through and
            # create a new session; we deliberately do not honor caller-supplied
            # IDs to avoid letting an external party squat on session UUIDs.
            log.info(
                "SessionMiddleware: ignoring unknown X-Session-Id %s; minting a new session",
                session_id,
            )

        return await session_store.create_session(
            source=source,
            external_session_id=external_session_id,
            user_id=user_id,
        )
