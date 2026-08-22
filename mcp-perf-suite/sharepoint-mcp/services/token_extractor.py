"""
Token extraction for SharePoint MCP.

Extracts SharePoint Bearer tokens captured during browser navigation
and parses JWT claims for user profile and token expiry.

Unlike msteams-mcp which extracts multiple token types (Substrate, Skype,
CSA, Auth) from localStorage entries, SharePoint MCP works with a single
Bearer token captured via network request interception in browser_auth.py.
The token is scoped to https://{tenant}.sharepoint.com and used for all
_api/ REST calls.
"""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from . import session_store

logger = logging.getLogger("sharepoint-mcp.token-extractor")


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode a JWT payload without signature verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None


def get_jwt_expiry(token: str) -> float | None:
    """Return expiry as a Unix timestamp (seconds), or None."""
    payload = decode_jwt_payload(token)
    if not payload or not isinstance(payload.get("exp"), (int, float)):
        return None
    return float(payload["exp"])


def _is_jwt(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("ey")


def validate_token_audience(token: str, tenant: str = "") -> bool:
    """Check if a JWT's audience (aud) claim is scoped to SharePoint.

    Rejects tokens scoped to Graph, WebShell, or other services that
    happen to be issued on *.sharepoint.com URLs during MSAL refresh flows.
    """
    payload = decode_jwt_payload(token)
    if not payload:
        return False

    aud = payload.get("aud", "")
    if not aud:
        return False

    # Accept tokens scoped to *.sharepoint.com
    if "sharepoint.com" in aud:
        return True

    # Accept if aud matches the tenant's SP resource exactly
    if tenant and aud == f"https://{tenant}.sharepoint.com":
        return True

    logger.debug(
        "Rejected token with wrong audience: aud=%s (expected *sharepoint.com*)",
        aud,
    )
    return False


# ---------------------------------------------------------------------------
# Token data types
# ---------------------------------------------------------------------------

@dataclass
class BearerTokenInfo:
    token: str
    expiry: float  # Unix timestamp (seconds)
    tenant: str


@dataclass
class UserProfile:
    display_name: str
    email: str
    object_id: str
    tenant_id: str


# ---------------------------------------------------------------------------
# Bearer token management
# ---------------------------------------------------------------------------

def get_valid_bearer_token() -> BearerTokenInfo | None:
    """Get a valid SharePoint Bearer token from the encrypted token cache.

    Returns None if no cached token, if the token has expired, or if
    the token's audience is not scoped to SharePoint.
    """
    cache = session_store.read_token_cache()
    if not cache:
        return None

    expiry = cache.get("bearerTokenExpiry", 0)
    if expiry <= time.time():
        return None

    token = cache.get("bearerToken", "")
    if not token or not _is_jwt(token):
        return None

    tenant = cache.get("tenant", "")
    if not validate_token_audience(token, tenant):
        logger.warning("Cached token has wrong audience — clearing stale cache")
        session_store.clear_token_cache()
        return None

    return BearerTokenInfo(
        token=token,
        expiry=expiry,
        tenant=tenant,
    )


def save_bearer_token(token: str, tenant: str = "") -> BearerTokenInfo | None:
    """Save a captured Bearer token to the encrypted token cache.

    Extracts the expiry from the JWT claims and validates the audience.
    Returns the token info on success, None if the token is invalid,
    expired, or scoped to the wrong audience (e.g. Graph instead of SP).
    """
    if not token or not _is_jwt(token):
        return None

    if not validate_token_audience(token, tenant):
        logger.warning("Rejecting Bearer token — audience is not SharePoint-scoped")
        return None

    expiry = get_jwt_expiry(token)
    if expiry is None or expiry <= time.time():
        logger.warning("Bearer token is already expired or has no expiry claim")
        return None

    info = BearerTokenInfo(token=token, expiry=expiry, tenant=tenant)

    session_store.write_token_cache({
        "bearerToken": token,
        "bearerTokenExpiry": expiry,
        "tenant": tenant,
        "extractedAt": time.time(),
    })

    return info


def get_bearer_token_status() -> dict[str, Any]:
    """Diagnostic info about the cached Bearer token."""
    info = get_valid_bearer_token()
    if not info:
        return {"hasToken": False, "remainingMinutes": 0, "tenant": ""}

    remaining = (info.expiry - time.time()) / 60.0
    return {
        "hasToken": True,
        "remainingMinutes": round(remaining, 1),
        "tenant": info.tenant,
    }


# ---------------------------------------------------------------------------
# User profile extraction from Bearer JWT
# ---------------------------------------------------------------------------

def get_user_profile_from_token(token: str | None = None) -> UserProfile | None:
    """Extract user profile from JWT claims in the Bearer token.

    If no token is provided, reads from the cached token.
    """
    if token is None:
        info = get_valid_bearer_token()
        if not info:
            return None
        token = info.token

    payload = decode_jwt_payload(token)
    if not payload:
        return None

    name = payload.get("name", "")
    email = (
        payload.get("preferred_username", "")
        or payload.get("upn", "")
        or payload.get("unique_name", "")
    )
    oid = payload.get("oid", "")
    tid = payload.get("tid", "")

    if not (name or email):
        return None

    return UserProfile(
        display_name=name or email,
        email=email,
        object_id=oid,
        tenant_id=tid,
    )


def get_user_display_name(token: str | None = None) -> str:
    """Get the current user's display name from the Bearer token, or 'Unknown User'."""
    profile = get_user_profile_from_token(token)
    return profile.display_name if profile else "Unknown User"
