"""Signed Web-UI identity token for MCP credential resolution.

AG-UI mints a short-lived HS256 JWT and sends it as
``Authorization: Bearer``. Downstream MCP servers verify it and load
the caller's active project bindings. The token carries ``user_id``,
display name, and email only — never a PAT, ciphertext, or token name.

The HMAC key is HKDF-derived from ``PERFPLOT_SECRETS_MASTER_KEY``
(see ``utils.secret_crypto``). TTL is ``MCP_JWT_TTL_SECONDS`` so
operators can change the default one-hour lifetime in one place.

A missing token means the caller is not the Web UI (A2A keeps env-var
credentials). A present but invalid token is an error, not an env
fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from utils.secret_crypto import SecretCryptoError, derive_jwt_hmac_key

MCP_JWT_TTL_SECONDS = 3600
MCP_JWT_ISSUER = "perfpilot-agui"
MCP_JWT_AUDIENCE = "perfpilot-mcp"
MCP_JWT_ALGORITHM = "HS256"


class McpJwtError(Exception):
    """Mint or verify failure. Messages must not include the token or key."""


@dataclass(frozen=True)
class McpContextClaims:
    """Verified identity claims from an MCP context JWT."""

    user_id: str
    name: str
    email: str


def mint_mcp_context_jwt(
    *,
    user_id: str,
    name: str = "",
    email: str = "",
    now: Optional[datetime] = None,
    ttl_seconds: int = MCP_JWT_TTL_SECONDS,
) -> str:
    """Return a compact JWT for the Web-UI → MCP hop.

    Raises:
        McpJwtError: if ``user_id`` is empty or the master key is unusable.
    """
    subject = (user_id or "").strip()
    if not subject:
        raise McpJwtError("Cannot mint an MCP context token without user_id.")
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "name": name or "",
        "email": email or "",
        "iss": MCP_JWT_ISSUER,
        "aud": MCP_JWT_AUDIENCE,
        "iat": issued,
        "exp": issued + timedelta(seconds=ttl_seconds),
    }
    try:
        hmac_key = derive_jwt_hmac_key()
    except SecretCryptoError as exc:
        raise McpJwtError(str(exc)) from exc
    return jwt.encode(payload, hmac_key, algorithm=MCP_JWT_ALGORITHM)


def verify_mcp_context_jwt(token: str) -> McpContextClaims:
    """Validate signature, issuer, audience, and expiry.

    Raises:
        McpJwtError: if the token is missing, expired, or forged.
    """
    compact = (token or "").strip()
    if not compact:
        raise McpJwtError("MCP context token is missing.")
    try:
        hmac_key = derive_jwt_hmac_key()
    except SecretCryptoError as exc:
        raise McpJwtError(str(exc)) from exc
    try:
        payload = jwt.decode(
            compact,
            hmac_key,
            algorithms=[MCP_JWT_ALGORITHM],
            issuer=MCP_JWT_ISSUER,
            audience=MCP_JWT_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise McpJwtError("MCP context token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise McpJwtError("MCP context token is invalid.") from exc
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        raise McpJwtError("MCP context token is missing sub.")
    return McpContextClaims(
        user_id=user_id,
        name=str(payload.get("name") or ""),
        email=str(payload.get("email") or ""),
    )
