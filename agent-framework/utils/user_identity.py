"""PerfPilot token resolution for user identity (Epic 3 PBI 3.7.0b).

The ``perfpilot_token`` is a persistent, opaque, server-minted identifier
carried as an HttpOnly cookie (browsers) or as the ``X-PerfPilot-Token``
header (CLI / IDE / CopilotKit route handler). It is **not** a true user
identity -- it is a pre-auth fingerprint that uniquely tags a client until
a real Identity Provider (EntraID, Okta, Auth0, etc.) is wired in at
Epic 4. Once an IdP is active, Step 1 of the resolver chain provides
the verified identity and this token becomes irrelevant for authenticated
users.

Four-step resolver chain (Decision 19):

    1. request.state.authenticated_user  -- Upstream-auth middleware slot
                                            (Epic 4). Always None in Epic 3.
                                            Vendor-agnostic by design (see
                                            Decision 20): any reverse-proxy
                                            / sidecar / middleware that
                                            verifies an OIDC or JWT token
                                            and writes a stable subject
                                            identifier here is honored
                                            unchanged. Concrete examples:
                                            Azure Container Apps Easy Auth
                                            + EntraID, AWS ALB + Cognito,
                                            GCP IAP, oauth2-proxy in front
                                            of any cluster.
    2. X-PerfPilot-Token header          -- Trusted by convention in Epic 3
                                            (IDE / CLI clients, CopilotKit
                                            route handler). In Epic 4 this
                                            is treated as one input the
                                            verifying middleware can
                                            cross-check (or ignore) when
                                            an upstream identity is present.
    3. perfpilot_token cookie            -- Server-issued opaque token,
                                            HttpOnly + SameSite=Lax + (in
                                            production) Secure. The
                                            canonical Epic 3 identity for
                                            browser users.
    4. Freshly minted opaque token       -- When no upstream identity is
                                            present, the middleware mints a
                                            new token and sets the cookie on
                                            the outgoing response. The token
                                            becomes the client's identity
                                            from then on.

The Epic 3 trust model is "trusted by convention" -- callers that set
X-PerfPilot-Token are believed. Epic 4 hardens Step 1 by adding a
verifying middleware that writes ``request.state.authenticated_user``
from whatever upstream auth the operator has chosen. No schema or
downstream-code changes are needed for that swap; ownership records on
every other table key off the resulting ``user_id`` string regardless of
provenance.

This module is import-light on purpose: nothing here hits the database or
any LLM. The middleware that calls ``resolve_user_id`` owns the side
effect of setting the cookie on the response.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Literal, Optional

from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger(__name__)


# --- Header / cookie / source vocabulary -------------------------------------

#: Inbound header carrying the PerfPilot token.
#: Trusted by convention in Epic 3 (IDE / CLI clients, CopilotKit route
#: handler). In Epic 4, treated as one input the verifying middleware can
#: cross-check when an upstream identity is present.
HEADER_PERFPILOT_TOKEN = "X-PerfPilot-Token"

#: Cookie key for the server-issued opaque token. The value stored here
#: is used as `user_id` in `agent_sessions` and `agent_threads` until a
#: real Identity Provider (Epic 4) supplies a verified subject identifier.
COOKIE_TOKEN = "perfpilot_token"

# Keep a backwards-compatible alias so existing imports that reference
# HEADER_USER_ID (e.g. session_middleware.py re-export) continue to work
# during the transition. New code should use HEADER_PERFPILOT_TOKEN.
HEADER_USER_ID = HEADER_PERFPILOT_TOKEN

#: Possible values for `ResolvedUser.source`. Logged on each resolve, useful
#: when chasing down "why does this request have THIS user_id?" questions.
IdentitySource = Literal[
    "upstream_auth",   # Step 1 - any OIDC/JWT-verifying upstream middleware set request.state.authenticated_user
    "header",          # Step 2 - X-PerfPilot-Token header was present
    "cookie",          # Step 3 - perfpilot_token cookie was present
    "minted_cookie",   # Step 4 - no identity present; minted a fresh token (middleware will set cookie)
    "anonymous",       # Edge case - cookie set failed; deterministic per-session fallback
]


# --- ResolvedUser ------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedUser:
    """Outcome of one `resolve_user_id()` call.

    Attributes:
        user_id: The resolved user identifier. Always non-empty after a
            successful resolve.
        source: Which step of the chain produced the value. Useful for
            structured logging and debugging.
        needs_cookie_set: True only when `source == "minted_cookie"`. The
            middleware should call `set_user_id_cookie(response, user_id)`
            on the outgoing response so the browser carries the token on
            subsequent requests. Other sources leave the cookie alone.
    """

    user_id: str
    source: IdentitySource
    needs_cookie_set: bool


# --- Resolver chain ---------------------------------------------------------

def resolve_user_id(
    request: Request,
    *,
    session_id_for_anonymous_fallback: Optional[str] = None,
) -> ResolvedUser:
    """Run the four-step Epic 3 identity resolver chain (Decision 19).

    Args:
        request: Inbound Starlette/FastAPI request. Read-only here -- the
            middleware is responsible for any response-side side effects
            (cookie setting).
        session_id_for_anonymous_fallback: Provided by the middleware when
            it wants the resolver to use the deterministic
            `anonymous-${session_id}` form for the cookie-blocked edge
            case. In normal operation this is unused -- Step 4 mints a
            fresh token and the middleware sets the cookie.

    Returns:
        `ResolvedUser` with a non-empty `user_id`. Never raises.
    """
    # Step 1 - Upstream auth slot (Epic 4). Any verifying middleware in
    # front of this resolver may attach a stable, verified subject identifier
    # to `request.state.authenticated_user` and we honor it. The contract is
    # vendor-neutral: the attribute is just a string. Concrete adapters our
    # deployment plans to use are ACA Easy Auth + EntraID `oid`; other
    # operators can plug in oauth2-proxy, AWS ALB + Cognito, GCP IAP, etc.
    # In Epic 3 the attribute simply does not exist.
    auth_user = getattr(request.state, "authenticated_user", None)
    if auth_user:
        return ResolvedUser(
            user_id=str(auth_user),
            source="upstream_auth",
            needs_cookie_set=False,
        )

    # Step 2 - X-PerfPilot-Token header. Trusted by convention in Epic 3.
    # The header value is stripped; empty / whitespace-only headers are ignored.
    header_value = (request.headers.get(HEADER_PERFPILOT_TOKEN) or "").strip()
    if header_value:
        return ResolvedUser(
            user_id=header_value,
            source="header",
            needs_cookie_set=False,
        )

    # Step 3 - Server-issued cookie carried by the browser. Set during a
    # previous "minted_cookie" turn.
    cookie_value = (request.cookies.get(COOKIE_TOKEN) or "").strip()
    if cookie_value:
        return ResolvedUser(
            user_id=cookie_value,
            source="cookie",
            needs_cookie_set=False,
        )

    # Step 4 - Mint a fresh opaque token. Middleware sets it as a cookie on
    # the outgoing response. Future requests from the same browser will hit
    # Step 3.
    minted = _mint_user_id()
    return ResolvedUser(
        user_id=minted,
        source="minted_cookie",
        needs_cookie_set=True,
    )


def _mint_user_id() -> str:
    """Generate a fresh opaque user identifier.

    32 URL-safe characters (~144 bits of entropy via `secrets.token_urlsafe`).
    Suitable for cookies and database VARCHARs alike.
    """
    return secrets.token_urlsafe(24)


def make_anonymous_user_id(session_id: str) -> str:
    """Last-resort fallback for the rare case where cookie set is impossible.

    Returns a deterministic `anonymous-${session_id}` identifier so requests
    within the same session at least see consistent ownership. Not normally
    used -- the middleware always tries the cookie path first.

    Args:
        session_id: The session UUID (as a string).

    Returns:
        A user_id of the form `anonymous-<session_id>`.
    """
    return f"anonymous-{session_id}"


# --- Response-side helper (called from middleware) --------------------------

#: Cookie defaults. Mirror `utils.agents_config._SESSION_COOKIE_DEFAULTS` so
#: this helper is usable standalone (e.g. in tests) without loading the YAML.
DEFAULT_COOKIE_MAX_AGE_DAYS = 365
DEFAULT_COOKIE_SECURE = False
DEFAULT_COOKIE_SAMESITE = "lax"
DEFAULT_COOKIE_HTTPONLY = True


def set_user_id_cookie(
    response: Response,
    user_id: str,
    *,
    secure: bool = DEFAULT_COOKIE_SECURE,
    samesite: str = DEFAULT_COOKIE_SAMESITE,
    httponly: bool = DEFAULT_COOKIE_HTTPONLY,
    max_age_days: int = DEFAULT_COOKIE_MAX_AGE_DAYS,
) -> None:
    """Set the `perfpilot_token` cookie on the outgoing response.

    Called by `SessionMiddleware` only when `ResolvedUser.needs_cookie_set`
    is True. The middleware reads these tunables from
    `agents.yaml -> web_ui.session_cookie:` via
    `utils.agents_config.get_session_cookie_config()`, so production
    deployments can adjust the lifetime / security flags without code
    changes. The helper retains the kwarg defaults for standalone test use.

    Args:
        response: The outgoing response to mutate.
        user_id: The user_id value to persist.
        secure: When True, the cookie is sent only over HTTPS. Defaults
            False so local-dev `http://localhost:8002` works; production
            deployments behind TLS (ACA, Cloud Run, AWS Fargate + ALB,
            on-prem reverse-proxy with cert, etc.) should set True via
            `web_ui.session_cookie.secure: true` in `agents.yaml`.
        samesite: One of `"lax"` (default), `"strict"`, or `"none"`.
            `"none"` requires `secure=True` per the browser cookie spec.
        httponly: When True (default), JavaScript cannot read the cookie.
            Recommended for opaque user-ID tokens; flip only if a future
            client genuinely needs JS-side access.
        max_age_days: Cookie lifetime in days. Default 365 to keep "you"
            stable for a long time without server-side bookkeeping.
    """
    response.set_cookie(
        key=COOKIE_TOKEN,
        value=user_id,
        max_age=max_age_days * 24 * 60 * 60,
        httponly=httponly,
        samesite=samesite,
        secure=secure,
        path="/",
    )
