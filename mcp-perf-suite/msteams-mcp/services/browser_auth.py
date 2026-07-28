"""
Browser-based authentication for Microsoft Teams.

Handles navigating to Teams, detecting login pages vs authenticated
content, and waiting for manual login when SSO fails.
"""

import asyncio
import logging
from playwright.async_api import Page, BrowserContext

from . import token_extractor
from .browser_context import save_session_state

logger = logging.getLogger("msteams-mcp.browser-auth")

TEAMS_URL = "https://teams.microsoft.com"

LOGIN_URL_PATTERNS = [
    "login.microsoftonline.com",
    "login.live.com",
    "login.microsoft.com",
]

AUTH_SUCCESS_SELECTORS = [
    '[data-tid="app-bar"]',
    '[data-tid="search-box"]',
    'input[placeholder*="Search"]',
    '[data-tid="chat-list"]',
    '[data-tid="team-list"]',
]

POLL_INTERVAL_SEC = 2
LOGIN_TIMEOUT_SEC = 300  # 5 minutes
NAV_TIMEOUT_SEC = 30


def _is_login_url(url: str) -> bool:
    return any(p in url for p in LOGIN_URL_PATTERNS)


def _is_teams_url(url: str) -> bool:
    return "teams.microsoft" in url or "teams.cloud" in url


async def _has_authenticated_content(page: Page) -> bool:
    """Check if the page shows authenticated Teams UI elements."""
    for selector in AUTH_SUCCESS_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el:
                return True
        except Exception:
            continue
    return False


async def navigate_to_teams(page: Page) -> dict:
    """
    Navigate to Teams and detect whether we land on login or authenticated content.

    Returns a dict with:
      - is_authenticated: bool
      - is_on_login_page: bool
      - current_url: str
    """
    try:
        response = await page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_SEC * 1000)
    except Exception as exc:
        logger.warning("Navigation to Teams failed: %s", exc)
        return {"is_authenticated": False, "is_on_login_page": False, "current_url": page.url}

    # Wait a moment for redirects to settle
    await asyncio.sleep(2)
    current_url = page.url

    if _is_login_url(current_url):
        return {"is_authenticated": False, "is_on_login_page": True, "current_url": current_url}

    if _is_teams_url(current_url):
        # Wait a bit more for Teams to finish loading
        await asyncio.sleep(3)

        # Check for JWT tokens in localStorage (works for both personal and business)
        try:
            has_token = await page.evaluate("""() => {
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    try {
                        const val = JSON.parse(localStorage.getItem(key));
                        if (val && val.secret && val.secret.startsWith('ey')) {
                            return true;
                        }
                    } catch {}
                }
                return false;
            }""")
            if has_token:
                return {"is_authenticated": True, "is_on_login_page": False, "current_url": current_url}
        except Exception:
            pass

        # Check for auth success selectors
        if await _has_authenticated_content(page):
            return {"is_authenticated": True, "is_on_login_page": False, "current_url": current_url}

    return {"is_authenticated": False, "is_on_login_page": _is_login_url(current_url), "current_url": current_url}


async def wait_for_manual_login(
    page: Page,
    context: BrowserContext,
    timeout_sec: int = LOGIN_TIMEOUT_SEC,
) -> bool:
    """
    Poll until the user completes manual login.

    Detection strategy (in priority order each cycle):
      1. URL is on Teams domain AND localStorage contains a valid token
      2. URL is on Teams domain AND a known UI selector is present
    Token-based detection is the primary signal because Teams UI selectors
    change across versions; localStorage tokens are stable.

    Returns True if login succeeded within timeout, False otherwise.
    """
    elapsed = 0
    while elapsed < timeout_sec:
        current_url = page.url

        if _is_teams_url(current_url) and not _is_login_url(current_url):
            # Primary: check for ANY JWT token in localStorage
            # Personal accounts lack Substrate tokens, so we look for
            # any MSAL entry with a JWT secret (covers chatsvcagg, Substrate, etc.)
            try:
                has_token = await page.evaluate("""() => {
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        try {
                            const val = JSON.parse(localStorage.getItem(key));
                            if (val && val.secret && val.secret.startsWith('ey')) {
                                return true;
                            }
                        } catch {}
                    }
                    return false;
                }""")
                if has_token:
                    await save_session_state(context)
                    return True
            except Exception:
                pass

            # Secondary: check for skypetoken_asm cookie via browser
            try:
                cookies = await context.cookies()
                has_skype = any(c.get("name") == "skypetoken_asm" and c.get("value") for c in cookies)
                if has_skype:
                    await save_session_state(context)
                    return True
            except Exception:
                pass

            # Tertiary: check UI selectors
            if await _has_authenticated_content(page):
                await save_session_state(context)
                return True

        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC

    return False


async def wait_for_token_refresh(
    page: Page,
    context: BrowserContext,
    timeout_sec: int = 30,
) -> bool:
    """
    Wait for tokens to appear in browser localStorage after headless navigation.

    Checks localStorage directly in-browser rather than serializing the full
    600KB+ storage state each poll cycle.
    """
    elapsed = 0
    while elapsed < timeout_sec:
        try:
            has_token = await page.evaluate("""() => {
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    try {
                        const val = JSON.parse(localStorage.getItem(key));
                        if (val && val.secret && val.secret.startsWith('ey')) {
                            return true;
                        }
                    } catch {}
                }
                return false;
            }""")
            if has_token:
                await save_session_state(context)
                return True
        except Exception:
            pass

        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC

    return False


async def ensure_authenticated(
    page: Page,
    context: BrowserContext,
    *,
    headless: bool = True,
) -> dict:
    """
    Full authentication flow:
    1. Navigate to Teams
    2. If already authenticated → save session, return success
    3. If on login page and headless → return needs_visible_login
    4. If on login page and visible → wait for manual login

    Returns a dict with:
      - success: bool
      - method: str (sso | manual | failed)
      - message: str
    """
    nav_result = await navigate_to_teams(page)

    if nav_result["is_authenticated"]:
        await save_session_state(context)
        return {"success": True, "method": "sso", "message": "Authenticated via SSO (session cookies)"}

    if nav_result["is_on_login_page"] and headless:
        # Headless can't do interactive login — check if tokens appeared via SSO redirect
        token_found = await wait_for_token_refresh(page, context, timeout_sec=10)
        if token_found:
            return {"success": True, "method": "sso", "message": "Authenticated via headless SSO"}
        return {
            "success": False,
            "method": "needs_visible_login",
            "message": "Headless SSO failed — interactive login required",
        }

    # On login page or unknown state — wait for user to complete login
    logger.info("Waiting for login (up to %d seconds)...", LOGIN_TIMEOUT_SEC)
    logged_in = await wait_for_manual_login(page, context)
    if logged_in:
        return {"success": True, "method": "manual", "message": "Authenticated via manual login"}
    return {"success": False, "method": "failed", "message": "Login timed out after 5 minutes"}
