"""
Browser-based authentication for SharePoint.

Handles navigating to SharePoint, detecting login pages vs authenticated
content, intercepting Bearer tokens from network requests, extracting
the tenant from the URL, and waiting for manual login when SSO fails.

Key difference from msteams-mcp browser_auth:
  - Teams relies on localStorage MSAL entries + UI selectors for detection
  - SharePoint relies on network request interception for Bearer tokens
    plus FedAuth/rtFa cookie detection as a secondary signal
"""

import asyncio
import logging
import re
from urllib.parse import urlparse

from playwright.async_api import Page, BrowserContext, Request

from .browser_context import save_session_state
from .token_extractor import validate_token_audience, decode_jwt_payload

logger = logging.getLogger("sharepoint-mcp.browser-auth")

LOGIN_URL_PATTERNS = [
    "login.microsoftonline.com",
    "login.live.com",
    "login.microsoft.com",
]

POLL_INTERVAL_SEC = 2
LOGIN_TIMEOUT_SEC = 300  # 5 minutes
NAV_TIMEOUT_SEC = 30

_TENANT_PATTERN = re.compile(r"https://([a-zA-Z0-9_-]+)\.sharepoint\.com")


def _is_login_url(url: str) -> bool:
    return any(p in url for p in LOGIN_URL_PATTERNS)


def _is_sharepoint_url(url: str) -> bool:
    return ".sharepoint.com" in url


def extract_tenant_from_url(url: str) -> str | None:
    """Extract the tenant name from a SharePoint URL.

    Example: 'https://contoso.sharepoint.com/sites/...' -> 'contoso'
    Returns None if the URL doesn't match the expected pattern.
    """
    match = _TENANT_PATTERN.search(url)
    if match:
        return match.group(1)
    return None


class TokenInterceptor:
    """Captures Bearer tokens from SharePoint network requests.

    Attached to a Playwright page via page.on("request", interceptor.on_request).
    Collects the most recent Bearer token seen on requests to *.sharepoint.com.
    """

    def __init__(self) -> None:
        self.bearer_token: str | None = None
        self.tenant: str | None = None

    def on_request(self, request: Request) -> None:
        url = request.url
        if not _is_sharepoint_url(url):
            return

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer ") and auth_header[7:].startswith("ey"):
            candidate_token = auth_header[7:]
            tenant = extract_tenant_from_url(url) or self.tenant or ""

            if not validate_token_audience(candidate_token, tenant):
                payload = decode_jwt_payload(candidate_token)
                aud = payload.get("aud", "unknown") if payload else "unknown"
                logger.debug("Ignoring non-SP token (aud=%s) on %s", aud, url[:80])
                return

            self.bearer_token = candidate_token
            if not self.tenant:
                self.tenant = tenant or extract_tenant_from_url(url)

    @property
    def has_token(self) -> bool:
        return self.bearer_token is not None


async def _has_fedauth_cookies(context: BrowserContext) -> bool:
    """Check if FedAuth and rtFa cookies are present (secondary auth signal)."""
    try:
        cookies = await context.cookies()
        has_fedauth = any(
            c.get("name") == "FedAuth" and c.get("value")
            for c in cookies
        )
        has_rtfa = any(
            c.get("name") == "rtFa" and c.get("value")
            for c in cookies
        )
        return has_fedauth or has_rtfa
    except Exception:
        return False


async def navigate_to_sharepoint(
    page: Page,
    interceptor: TokenInterceptor,
    sharepoint_url: str,
) -> dict:
    """Navigate to SharePoint and detect whether we land on login or authenticated content.

    Returns a dict with:
      - is_authenticated: bool
      - is_on_login_page: bool
      - current_url: str
      - tenant: str | None
    """
    try:
        await page.goto(
            sharepoint_url,
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_SEC * 1000,
        )
    except Exception as exc:
        logger.warning("Navigation to SharePoint failed: %s", exc)
        return {
            "is_authenticated": False,
            "is_on_login_page": False,
            "current_url": page.url,
            "tenant": None,
        }

    # Wait for redirects to settle
    await asyncio.sleep(3)
    current_url = page.url

    if _is_login_url(current_url):
        return {
            "is_authenticated": False,
            "is_on_login_page": True,
            "current_url": current_url,
            "tenant": None,
        }

    if _is_sharepoint_url(current_url):
        # Wait for SharePoint to load and make API calls (triggers token intercept)
        await asyncio.sleep(3)

        tenant = extract_tenant_from_url(current_url) or interceptor.tenant

        if interceptor.has_token:
            return {
                "is_authenticated": True,
                "is_on_login_page": False,
                "current_url": current_url,
                "tenant": tenant,
            }

        # Bearer token not intercepted yet — check cookies as fallback
        if await _has_fedauth_cookies(page.context):
            return {
                "is_authenticated": True,
                "is_on_login_page": False,
                "current_url": current_url,
                "tenant": tenant,
            }

    return {
        "is_authenticated": False,
        "is_on_login_page": _is_login_url(current_url),
        "current_url": current_url,
        "tenant": None,
    }


async def wait_for_manual_login(
    page: Page,
    context: BrowserContext,
    interceptor: TokenInterceptor,
    timeout_sec: int = LOGIN_TIMEOUT_SEC,
) -> bool:
    """Poll until the user completes manual login.

    Detection strategy (in priority order each cycle):
      1. Bearer token intercepted from a network request to *.sharepoint.com
      2. URL is on SharePoint domain AND FedAuth/rtFa cookies are present

    Returns True if login succeeded within timeout, False otherwise.
    """
    elapsed = 0
    heartbeat_interval = 30
    next_heartbeat = heartbeat_interval

    while elapsed < timeout_sec:
        current_url = page.url

        if interceptor.has_token:
            await save_session_state(context)
            return True

        if _is_sharepoint_url(current_url) and not _is_login_url(current_url):
            if await _has_fedauth_cookies(context):
                await save_session_state(context)
                return True

        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC

        if elapsed >= next_heartbeat:
            logger.info("Waiting for login... %ds / %ds", elapsed, timeout_sec)
            next_heartbeat += heartbeat_interval

    return False


async def wait_for_token_refresh(
    page: Page,
    context: BrowserContext,
    interceptor: TokenInterceptor,
    timeout_sec: int = 30,
) -> bool:
    """Wait for a Bearer token to appear via network intercept after headless navigation.

    Used for headless SSO where the browser silently refreshes tokens
    through redirect flows without user interaction.
    """
    elapsed = 0
    while elapsed < timeout_sec:
        if interceptor.has_token:
            await save_session_state(context)
            return True

        # Also check cookies — some SSO flows may not trigger _api calls
        if _is_sharepoint_url(page.url) and await _has_fedauth_cookies(context):
            await save_session_state(context)
            return True

        await asyncio.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC

    return False


async def ensure_authenticated(
    page: Page,
    context: BrowserContext,
    interceptor: TokenInterceptor,
    sharepoint_url: str,
    *,
    headless: bool = True,
) -> dict:
    """Full authentication flow:
    1. Navigate to SharePoint
    2. If already authenticated -> save session, return success
    3. If on login page and headless -> return needs_visible_login
    4. If on login page and visible -> wait for manual login

    Returns a dict with:
      - success: bool
      - method: str (sso | manual | needs_visible_login | failed)
      - message: str
      - tenant: str | None
    """
    nav_result = await navigate_to_sharepoint(page, interceptor, sharepoint_url)

    if nav_result["is_authenticated"]:
        await save_session_state(context)
        return {
            "success": True,
            "method": "sso",
            "message": "Authenticated via SSO (session cookies)",
            "tenant": nav_result["tenant"],
        }

    if nav_result["is_on_login_page"] and headless:
        token_found = await wait_for_token_refresh(
            page, context, interceptor, timeout_sec=10,
        )
        if token_found:
            tenant = interceptor.tenant or extract_tenant_from_url(page.url)
            return {
                "success": True,
                "method": "sso",
                "message": "Authenticated via headless SSO",
                "tenant": tenant,
            }
        return {
            "success": False,
            "method": "needs_visible_login",
            "message": "Headless SSO failed — interactive login required",
            "tenant": None,
        }

    # On login page or unknown state — wait for user to complete login
    logger.info("Waiting for login (up to %d seconds)...", LOGIN_TIMEOUT_SEC)
    logged_in = await wait_for_manual_login(page, context, interceptor)
    if logged_in:
        tenant = interceptor.tenant or extract_tenant_from_url(page.url)
        return {
            "success": True,
            "method": "manual",
            "message": "Authenticated via manual login",
            "tenant": tenant,
        }
    return {
        "success": False,
        "method": "failed",
        "message": "Login timed out after 5 minutes",
        "tenant": None,
    }
