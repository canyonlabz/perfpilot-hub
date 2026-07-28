"""
Secure session state storage for SharePoint MCP.

Handles reading and writing Playwright session state with:
- AES-256-GCM encryption at rest
- Restricted file permissions (0o600 on Unix)
- Automatic migration from plaintext to encrypted
"""

import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, TypedDict

from .crypto import encrypt, decrypt, is_encrypted

logger = logging.getLogger("sharepoint-mcp.session-store")

SESSION_EXPIRY_HOURS = 12


# ---------------------------------------------------------------------------
# Type definitions matching Playwright's storage state
# ---------------------------------------------------------------------------

class CookieEntry(TypedDict, total=False):
    name: str
    value: str
    domain: str
    path: str
    expires: float
    httpOnly: bool
    secure: bool
    sameSite: str


class LocalStorageEntry(TypedDict):
    name: str
    value: str


class OriginEntry(TypedDict):
    origin: str
    localStorage: list[LocalStorageEntry]


class SessionState(TypedDict):
    cookies: list[CookieEntry]
    origins: list[OriginEntry]


class TokenCache(TypedDict):
    bearerToken: str
    bearerTokenExpiry: float
    tenant: str
    extractedAt: float


# ---------------------------------------------------------------------------
# Config directory resolution
# ---------------------------------------------------------------------------

def _get_config_dir() -> str:
    """
    User-specific config directory for sharepoint-mcp-server.
    - Windows: %APPDATA%\\sharepoint-mcp-server\\
    - macOS/Linux: ~/.sharepoint-mcp-server/
    """
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            appdata = str(Path.home() / "AppData" / "Roaming")
        return os.path.join(appdata, "sharepoint-mcp-server")
    return os.path.join(str(Path.home()), ".sharepoint-mcp-server")


CONFIG_DIR = _get_config_dir()
USER_DATA_DIR = os.path.join(CONFIG_DIR, ".user-data")
SESSION_STATE_PATH = os.path.join(CONFIG_DIR, "session-state.json")
TOKEN_CACHE_PATH = os.path.join(CONFIG_DIR, "token-cache.json")

SECURE_FILE_MODE = 0o600


def _ensure_config_dir() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _ensure_user_data_dir() -> None:
    _ensure_config_dir()
    os.makedirs(USER_DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Secure read / write helpers
# ---------------------------------------------------------------------------

def _write_secure(file_path: str, data: Any) -> None:
    """Encrypt data and write to file."""
    json_str = json.dumps(data, indent=2)
    encrypted = encrypt(json_str)
    _ensure_config_dir()
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(encrypted, f, indent=2)
    if platform.system() != "Windows":
        os.chmod(file_path, SECURE_FILE_MODE)


def _read_secure(file_path: str) -> Any | None:
    """Read and decrypt data, auto-migrating plaintext to encrypted."""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            parsed = json.load(f)

        if is_encrypted(parsed):
            decrypted = decrypt(parsed)
            return json.loads(decrypted)

        # Legacy plaintext — migrate to encrypted
        _write_secure(file_path, parsed)
        return parsed
    except Exception as exc:
        logger.error("Failed to read %s: %s", file_path, exc)
        return None


# ---------------------------------------------------------------------------
# Session state operations
# ---------------------------------------------------------------------------

def has_session_state() -> bool:
    return os.path.exists(SESSION_STATE_PATH)


def read_session_state() -> SessionState | None:
    return _read_secure(SESSION_STATE_PATH)


def write_session_state(state: SessionState) -> None:
    _write_secure(SESSION_STATE_PATH, state)


def clear_session_state() -> None:
    if os.path.exists(SESSION_STATE_PATH):
        os.unlink(SESSION_STATE_PATH)


def get_session_age_hours() -> float | None:
    """Return session age in hours, or None if no session."""
    if not has_session_state():
        return None
    mtime = os.path.getmtime(SESSION_STATE_PATH)
    return (time.time() - mtime) / 3600.0


def is_session_likely_expired() -> bool:
    age = get_session_age_hours()
    if age is None:
        return True
    return age > SESSION_EXPIRY_HOURS


# ---------------------------------------------------------------------------
# Token cache operations
# ---------------------------------------------------------------------------

def read_token_cache() -> TokenCache | None:
    return _read_secure(TOKEN_CACHE_PATH)


def write_token_cache(cache: TokenCache) -> None:
    _write_secure(TOKEN_CACHE_PATH, cache)


def clear_token_cache() -> None:
    if os.path.exists(TOKEN_CACHE_PATH):
        os.unlink(TOKEN_CACHE_PATH)


# ---------------------------------------------------------------------------
# SharePoint origin helpers
# ---------------------------------------------------------------------------

def get_sharepoint_origin(state: SessionState, tenant: str = "") -> OriginEntry | None:
    """Find the SharePoint origin entry from session state localStorage."""
    origins = state.get("origins", [])

    # If tenant is known, look for exact match first
    if tenant:
        target = f"https://{tenant}.sharepoint.com"
        for o in origins:
            if o.get("origin") == target:
                return o

    # Fallback: any origin containing 'sharepoint.com'
    for o in origins:
        origin_url = o.get("origin", "")
        if "sharepoint.com" in origin_url:
            return o

    return None


def ensure_user_data_dir() -> str:
    """Ensure the browser user data directory exists and return its path."""
    _ensure_user_data_dir()
    return USER_DATA_DIR


# ---------------------------------------------------------------------------
# Auth cookie extraction (FedAuth / rtFa for cookie-based tenants)
# ---------------------------------------------------------------------------

def get_auth_cookies(tenant: str = "") -> dict[str, str] | None:
    """Extract FedAuth and rtFa cookies from the stored Playwright session state.

    These cookies are used by tenants that authenticate _api/ calls via
    session cookies rather than Bearer tokens. Returns a dict with
    cookie name -> value mappings, or None if no valid cookies found.
    """
    state = read_session_state()
    if not state:
        return None

    cookies = state.get("cookies", [])
    if not cookies:
        return None

    # Domains to match: exact tenant domain and the root .sharepoint.com
    match_domains = {".sharepoint.com"}
    if tenant:
        match_domains.add(f"{tenant}.sharepoint.com")
        match_domains.add(f".{tenant}.sharepoint.com")

    result: dict[str, str] = {}
    now = time.time()

    for cookie in cookies:
        name = cookie.get("name", "")
        if name not in ("FedAuth", "rtFa"):
            continue

        value = cookie.get("value", "")
        if not value:
            continue

        # Skip expired cookies (expires=0 or -1 means session cookie — keep those)
        expires = cookie.get("expires", -1)
        if isinstance(expires, (int, float)) and expires > 0 and expires < now:
            continue

        domain = cookie.get("domain", "")
        if not any(domain.endswith(d) or d.endswith(domain) for d in match_domains):
            if "sharepoint.com" not in domain:
                continue

        # Keep the first (most specific) cookie for each name
        if name not in result:
            result[name] = value

    if not result:
        return None

    logger.debug(
        "Extracted auth cookies: %s",
        ", ".join(f"{k}={'...' + v[-8:]}" for k, v in result.items()),
    )
    return result
