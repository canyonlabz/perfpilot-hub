"""
Playwright browser context management.

Uses the system's installed Chrome/Edge with a persistent profile at
~/.teams-mcp-server/browser-profile/ so that:
- Microsoft session cookies persist across launches
- Headless token refresh can silently re-authenticate
- Visible login retains extensions (e.g. Bitwarden) and form autofill
"""

import asyncio
import logging
import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

from . import session_store
from utils.config import load_config

logger = logging.getLogger("msteams-mcp.browser")

BROWSER_PROFILE_DIR = os.path.join(session_store.CONFIG_DIR, "browser-profile")
SINGLETON_LOCK_PATH = os.path.join(BROWSER_PROFILE_DIR, "SingletonLock")


@dataclass
class BrowserManager:
    playwright: Playwright
    context: BrowserContext
    page: Page
    is_new_session: bool = True


_browser_lock = asyncio.Lock()


def _get_browser_channel() -> str:
    """Get browser channel from config. Defaults to chrome."""
    try:
        cfg = load_config()
        return cfg.get("teams", {}).get("browser_channel", "chrome")
    except Exception:
        return "chrome"


def _cleanup_stale_singleton_lock() -> bool:
    """Remove stale SingletonLock files left by crashed browser sessions."""
    if not os.path.exists(SINGLETON_LOCK_PATH):
        return False

    try:
        # Unix: SingletonLock is a symlink like "hostname-12345"
        target = os.readlink(SINGLETON_LOCK_PATH)
        parts = target.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            pid = int(parts[1])
            try:
                os.kill(pid, 0)
                return False  # process is alive — lock is valid
            except OSError:
                pass
            logger.info("Removing stale SingletonLock (PID %d not running)", pid)
            os.unlink(SINGLETON_LOCK_PATH)
            return True
    except (OSError, ValueError):
        pass

    # Windows / fallback: remove if older than 1 hour
    try:
        age = time.time() - os.path.getmtime(SINGLETON_LOCK_PATH)
        if age > 3600:
            logger.info("Removing old SingletonLock (%d min old)", int(age / 60))
            os.unlink(SINGLETON_LOCK_PATH)
            return True
    except OSError:
        pass

    return False


async def create_browser_context(
    *,
    headless: bool = True,
    viewport: dict | None = None,
) -> BrowserManager:
    """
    Launch a persistent browser context using the system Chrome/Edge.

    Only one process can use the profile at a time (Chromium profile lock).
    Callers should acquire _browser_lock before calling this.
    """
    session_store.ensure_user_data_dir()
    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)

    channel = _get_browser_channel()
    vp = viewport or {"width": 1280, "height": 800}
    _cleanup_stale_singleton_lock()

    pw = await async_playwright().start()

    async def _launch() -> BrowserManager:
        ctx = await pw.chromium.launch_persistent_context(
            BROWSER_PROFILE_DIR,
            headless=headless,
            channel=channel,
            viewport=vp,
            accept_downloads=False,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return BrowserManager(playwright=pw, context=ctx, page=page)

    try:
        return await _launch()
    except Exception as exc:
        msg = str(exc)
        if "ProcessSingleton" in msg or "SingletonLock" in msg:
            logger.warning("Profile lock detected, cleaning up and retrying...")
            if os.path.exists(SINGLETON_LOCK_PATH):
                os.unlink(SINGLETON_LOCK_PATH)
                return await _launch()

        browser_name = "Microsoft Edge" if channel == "msedge" else "Google Chrome"
        await pw.stop()
        raise RuntimeError(
            f"Could not launch {browser_name}. "
            f"Ensure it is installed on this machine.\n\nOriginal error: {msg}"
        ) from exc


async def save_session_state(context: BrowserContext) -> None:
    """Save the browser context's storage state to the encrypted session file."""
    state = await context.storage_state()
    session_store.write_session_state(state)


async def close_browser(manager: BrowserManager, *, save_session: bool = True) -> None:
    """Close browser context, optionally saving session state first."""
    try:
        if save_session:
            await save_session_state(manager.context)
        await manager.context.close()
    finally:
        await manager.playwright.stop()
