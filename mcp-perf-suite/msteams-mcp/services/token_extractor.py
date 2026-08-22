"""
Token extraction from Playwright session state.

Extracts authentication tokens from Teams' localStorage (MSAL tokens)
and cookies (skypetoken_asm, authtoken) for use with Substrate search,
chatsvc messaging, and people APIs.
"""

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from . import session_store

logger = logging.getLogger("msteams-mcp.token-extractor")

# MRI identity prefixes
MRI_TYPE_PREFIX = "8:"
ORGID_PREFIX = "orgid:"
MRI_ORGID_PREFIX = f"{MRI_TYPE_PREFIX}{ORGID_PREFIX}"


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode a JWT payload without signature verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        # JWT base64url → standard base64
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


# ---------------------------------------------------------------------------
# localStorage helpers
# ---------------------------------------------------------------------------

def _get_teams_local_storage(
    state: session_store.SessionState | None = None,
) -> list[session_store.LocalStorageEntry] | None:
    """Resolve session state and return the Teams origin's localStorage."""
    st = state or session_store.read_session_state()
    if not st:
        return None
    origin = session_store.get_teams_origin(st)
    if not origin:
        return None
    return origin.get("localStorage", [])


# ---------------------------------------------------------------------------
# Token data types
# ---------------------------------------------------------------------------

@dataclass
class SubstrateTokenInfo:
    token: str
    expiry: float  # Unix timestamp (seconds)


@dataclass
class MessageAuthInfo:
    skype_token: str
    auth_token: str
    user_mri: str


@dataclass
class UserProfile:
    display_name: str
    email: str
    object_id: str
    tenant_id: str


# ---------------------------------------------------------------------------
# Substrate token extraction (search / people APIs)
# ---------------------------------------------------------------------------

def extract_substrate_token(
    state: session_store.SessionState | None = None,
) -> SubstrateTokenInfo | None:
    """Extract the best valid Substrate search token from localStorage."""
    ls = _get_teams_local_storage(state)
    if not ls:
        return None

    best: SubstrateTokenInfo | None = None

    for item in ls:
        try:
            entry = json.loads(item.get("value", ""))
            target = entry.get("target", "")
            if "substrate.office.com" not in target:
                continue
            if "SubstrateSearch" not in target:
                continue

            secret = entry.get("secret", "")
            if not _is_jwt(secret):
                continue

            expiry = get_jwt_expiry(secret)
            if expiry is None or expiry <= time.time():
                continue

            if best is None or expiry > best.expiry:
                best = SubstrateTokenInfo(token=secret, expiry=expiry)
        except Exception:
            continue

    return best


def get_valid_substrate_token() -> str | None:
    """Get a valid Substrate token from cache or session state."""
    cache = session_store.read_token_cache()
    if cache and cache.get("substrateTokenExpiry", 0) / 1000.0 > time.time():
        return cache["substrateToken"]

    extracted = extract_substrate_token()
    if not extracted:
        return None

    # Cache the token (store expiry in ms to match TS format)
    session_store.write_token_cache({
        "substrateToken": extracted.token,
        "substrateTokenExpiry": extracted.expiry * 1000.0,
        "extractedAt": time.time() * 1000.0,
    })
    return extracted.token


def get_substrate_token_status() -> dict[str, Any]:
    """Diagnostic info about the Substrate token."""
    token = get_valid_substrate_token()
    if not token:
        return {"hasToken": False, "remainingMinutes": 0}

    expiry = get_jwt_expiry(token)
    remaining = (expiry - time.time()) / 60.0 if expiry else 0
    return {
        "hasToken": True,
        "remainingMinutes": round(remaining, 1),
    }


# ---------------------------------------------------------------------------
# Message auth extraction (skypetoken_asm + authtoken cookies)
# ---------------------------------------------------------------------------

def extract_message_auth(
    state: session_store.SessionState | None = None,
) -> MessageAuthInfo | None:
    """Extract cookie-based auth for chatsvc messaging APIs."""
    st = state or session_store.read_session_state()
    if not st:
        return None

    cookies = st.get("cookies", [])
    skype_token = ""
    auth_token = ""

    for cookie in cookies:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if not value:
            continue
        expires = cookie.get("expires", 0)
        if isinstance(expires, (int, float)) and expires > 0 and expires < time.time():
            continue

        if name == "skypetoken_asm" and not skype_token:
            skype_token = value
        elif name == "authtoken" and not auth_token:
            auth_token = value

    if not skype_token:
        return None

    # Extract user MRI from skype token JWT
    user_mri = ""
    payload = decode_jwt_payload(skype_token)
    if payload:
        skype_id = payload.get("skypeid", "")
        if skype_id and not skype_id.startswith(MRI_TYPE_PREFIX):
            user_mri = f"{MRI_TYPE_PREFIX}{skype_id}"
        elif skype_id:
            user_mri = skype_id

    return MessageAuthInfo(
        skype_token=skype_token,
        auth_token=auth_token,
        user_mri=user_mri,
    )


def get_message_auth_status() -> dict[str, Any]:
    """Diagnostic info about message auth tokens."""
    auth = extract_message_auth()
    if not auth:
        return {"hasSkypeToken": False, "hasAuthToken": False}

    result: dict[str, Any] = {
        "hasSkypeToken": bool(auth.skype_token),
        "hasAuthToken": bool(auth.auth_token),
    }

    expiry = get_jwt_expiry(auth.skype_token)
    if expiry:
        result["skypeTokenRemainingMinutes"] = round((expiry - time.time()) / 60.0, 1)

    return result


# ---------------------------------------------------------------------------
# User profile extraction
# ---------------------------------------------------------------------------

def get_user_profile(
    state: session_store.SessionState | None = None,
) -> UserProfile | None:
    """Extract user profile from JWT claims in localStorage tokens."""
    ls = _get_teams_local_storage(state)
    if not ls:
        return None

    for item in ls:
        try:
            entry = json.loads(item.get("value", ""))
            secret = entry.get("secret", "")
            if not _is_jwt(secret):
                continue

            payload = decode_jwt_payload(secret)
            if not payload:
                continue

            name = payload.get("name", "")
            email = (
                payload.get("preferred_username", "")
                or payload.get("upn", "")
                or payload.get("unique_name", "")
            )
            oid = payload.get("oid", "")
            tid = payload.get("tid", "")

            if name and oid:
                return UserProfile(
                    display_name=name,
                    email=email,
                    object_id=oid,
                    tenant_id=tid,
                )
        except Exception:
            continue

    return None


def get_user_display_name(state: session_store.SessionState | None = None) -> str:
    """Get the current user's display name, or 'Unknown User'."""
    profile = get_user_profile(state)
    return profile.display_name if profile else "Unknown User"


# ---------------------------------------------------------------------------
# CSA token extraction (chatsvcagg — for teams/channels/favorites APIs)
# ---------------------------------------------------------------------------

def extract_csa_token(
    state: session_store.SessionState | None = None,
) -> str | None:
    """
    Extract the CSA bearer token from localStorage.

    Searches ALL origins (not just Teams) for an entry whose name
    contains 'chatsvcagg.teams.microsoft.com'. This token is required
    for CSA API calls (teams list, favorites, etc.).
    """
    st = state or session_store.read_session_state()
    if not st:
        return None

    for origin_entry in st.get("origins", []):
        for item in origin_entry.get("localStorage", []):
            name = item.get("name", "")
            if name.startswith("tmp."):
                continue
            if "chatsvcagg.teams.microsoft.com" not in name:
                continue
            try:
                entry = json.loads(item.get("value", ""))
                secret = entry.get("secret", "")
                if secret:
                    return secret
            except Exception:
                continue

    return None


# ---------------------------------------------------------------------------
# Region config extraction (DISCOVER-REGION-GTM)
# ---------------------------------------------------------------------------

def extract_region_config(
    state: session_store.SessionState | None = None,
) -> dict[str, Any] | None:
    """Extract the DISCOVER-REGION-GTM config from localStorage."""
    ls = _get_teams_local_storage(state)
    if not ls:
        return None

    for item in ls:
        if item.get("name") == "DISCOVER-REGION-GTM":
            try:
                return json.loads(item.get("value", ""))
            except Exception:
                return None

    return None
