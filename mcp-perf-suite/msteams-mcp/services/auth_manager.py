"""
Authentication orchestration for MS Teams MCP.

Three-layer token resolution:
  1. Token cache — fast path, no I/O
  2. Session state — re-extract tokens from encrypted Playwright state
  3. Browser login — launch Playwright, attempt SSO, fall back to manual login

All entry points are async and guarded by an asyncio.Lock to prevent
concurrent browser launches or token refresh races.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import session_store, token_extractor
from .browser_context import (
    _browser_lock,
    create_browser_context,
    close_browser,
    BrowserManager,
)
from .browser_auth import ensure_authenticated
from .errors import ErrorCode, McpError, Result, ok, err, create_error

logger = logging.getLogger("msteams-mcp.auth-manager")

TOKEN_REFRESH_THRESHOLD_SEC = 600  # 10 minutes


@dataclass
class AuthState:
    """Cached in-memory auth state — updated after each successful auth."""
    substrate_token: str | None = None
    substrate_expiry: float = 0
    skype_token: str | None = None
    auth_token: str | None = None
    user_mri: str | None = None
    user_name: str = "Unknown User"
    last_refresh: float = 0


_auth_state = AuthState()
_auth_lock = asyncio.Lock()


def _token_needs_refresh(expiry: float) -> bool:
    return expiry <= 0 or (expiry - time.time()) < TOKEN_REFRESH_THRESHOLD_SEC


def _update_auth_state_from_session(state: session_store.SessionState | None = None) -> bool:
    """Try to populate _auth_state from session state. Returns True on success."""
    sub = token_extractor.extract_substrate_token(state)
    if sub:
        _auth_state.substrate_token = sub.token
        _auth_state.substrate_expiry = sub.expiry

    msg_auth = token_extractor.extract_message_auth(state)
    if msg_auth:
        _auth_state.skype_token = msg_auth.skype_token
        _auth_state.auth_token = msg_auth.auth_token
        _auth_state.user_mri = msg_auth.user_mri

    _auth_state.user_name = token_extractor.get_user_display_name(state)
    _auth_state.last_refresh = time.time()

    return sub is not None


async def get_substrate_token() -> Result[str]:
    """Get a valid Substrate search token (cache → session → browser)."""
    async with _auth_lock:
        # Layer 1: In-memory cache
        if (
            _auth_state.substrate_token
            and not _token_needs_refresh(_auth_state.substrate_expiry)
        ):
            return ok(_auth_state.substrate_token)

        # Layer 2: Session state
        if session_store.has_session_state() and not session_store.is_session_likely_expired():
            if _update_auth_state_from_session():
                if not _token_needs_refresh(_auth_state.substrate_expiry):
                    return ok(_auth_state.substrate_token)

        # Layer 3: Headless browser refresh
        result = await _browser_login(headless=True)
        if result.ok and _auth_state.substrate_token:
            return ok(_auth_state.substrate_token)

        return err(create_error(
            ErrorCode.AUTH_REQUIRED,
            "No valid Substrate token — interactive login required",
        ))


async def get_message_auth() -> Result[dict[str, str]]:
    """Get skype_token + auth_token for messaging APIs."""
    async with _auth_lock:
        if _auth_state.skype_token:
            return ok({
                "skype_token": _auth_state.skype_token,
                "auth_token": _auth_state.auth_token or "",
                "user_mri": _auth_state.user_mri or "",
            })

        if session_store.has_session_state():
            _update_auth_state_from_session()
            if _auth_state.skype_token:
                return ok({
                    "skype_token": _auth_state.skype_token,
                    "auth_token": _auth_state.auth_token or "",
                    "user_mri": _auth_state.user_mri or "",
                })

        return err(create_error(
            ErrorCode.AUTH_REQUIRED,
            "No message auth tokens — call teams_login first",
        ))


async def login(*, force: bool = False) -> Result[dict[str, Any]]:
    """
    Full login flow. If force=True, skip cache and go straight to browser.

    Returns a status dict on success.
    """
    async with _auth_lock:
        if not force:
            # Check if we already have valid tokens
            if (
                _auth_state.substrate_token
                and not _token_needs_refresh(_auth_state.substrate_expiry)
            ):
                return ok({
                    "status": "already_authenticated",
                    "user": _auth_state.user_name,
                    "message": "Already authenticated with valid tokens",
                })

            # Try session state first
            if session_store.has_session_state() and not session_store.is_session_likely_expired():
                if _update_auth_state_from_session():
                    if not _token_needs_refresh(_auth_state.substrate_expiry):
                        return ok({
                            "status": "restored_session",
                            "user": _auth_state.user_name,
                            "message": "Restored tokens from cached session",
                        })

        # Try headless first, fall back to visible
        result = await _browser_login(headless=True)
        if result.ok:
            return result

        # Headless failed — try visible browser for manual login
        return await _browser_login(headless=False)


async def _browser_login(*, headless: bool) -> Result[dict[str, Any]]:
    """
    Launch Playwright, navigate to Teams, authenticate.

    Returns Ok with status dict on success, Err on failure.
    """
    manager: BrowserManager | None = None
    auth_result: dict | None = None
    try:
        async with _browser_lock:
            manager = await create_browser_context(headless=headless)
            auth_result = await ensure_authenticated(
                manager.page, manager.context, headless=headless,
            )

        if auth_result["success"]:
            _update_auth_state_from_session()
            return ok({
                "status": "authenticated",
                "method": auth_result["method"],
                "user": _auth_state.user_name,
                "message": auth_result["message"],
            })

        if auth_result.get("method") == "needs_visible_login":
            return err(create_error(
                ErrorCode.AUTH_REQUIRED,
                "SSO failed — interactive login required",
            ))

        return err(create_error(
            ErrorCode.BROWSER_ERROR,
            auth_result.get("message", "Browser login failed"),
        ))

    except Exception as exc:
        logger.exception("Browser login error")
        return err(create_error(
            ErrorCode.BROWSER_ERROR,
            f"Browser error: {exc}",
        ))
    finally:
        if manager:
            try:
                save = bool(auth_result and auth_result.get("success"))
                await close_browser(manager, save_session=save)
            except Exception:
                pass


def get_status() -> dict[str, Any]:
    """Return a diagnostic snapshot of auth state (no I/O)."""
    has_session = session_store.has_session_state()
    session_age = session_store.get_session_age_hours()

    return {
        "hasSession": has_session,
        "sessionAgeHours": round(session_age, 1) if session_age else None,
        "isSessionExpired": session_store.is_session_likely_expired(),
        "substrateToken": token_extractor.get_substrate_token_status(),
        "messageAuth": token_extractor.get_message_auth_status(),
        "user": _auth_state.user_name,
        "lastRefresh": _auth_state.last_refresh,
    }
