# services/artifact_manager.py
"""
Artifact Manager - Helper and utility functions for BlazeMeter session artifact processing.

This module provides:
- Session manifest management (create, load, save)
- JTL file concatenation with header deduplication
- Artifact ZIP download with built-in retry logic

These helpers are consumed by the core orchestration function `session_artifact_processor`
in blazemeter_api.py.
"""
import os
import json
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Optional, Union

from utils.config import (
    load_config,
    get_artifact_download_max_retries,
    get_artifact_download_retry_delay,
)

# Load config at module level (same pattern as blazemeter_api.py)
config = load_config()
artifacts_base = config['artifacts']['artifacts_path']

MANIFEST_FILENAME = "session_manifest.json"


# ===============================================
# Session Manifest Helpers
# ===============================================

def get_manifest_path(run_id: str) -> str:
    """Returns the full path to the session manifest file for a given run."""
    return os.path.join(
        artifacts_base, str(run_id), "blazemeter", "sessions", MANIFEST_FILENAME
    )


def load_manifest(run_id: str) -> Optional[dict]:
    """
    Loads the session manifest if it exists.

    Args:
        run_id: BlazeMeter run/master ID.

    Returns:
        Manifest dict if found, None otherwise.
    """
    path = get_manifest_path(run_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_manifest(run_id: str, manifest: dict) -> str:
    """
    Saves the session manifest to disk and updates the `updated_at` timestamp.

    Args:
        run_id: BlazeMeter run/master ID.
        manifest: The manifest dict to persist.

    Returns:
        Full path to the saved manifest file.
    """
    path = get_manifest_path(run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return path


def create_manifest(run_id: str, sessions_id: list) -> dict:
    """
    Creates a new session manifest for the given run and session list.

    Args:
        run_id: BlazeMeter run/master ID.
        sessions_id: List of BlazeMeter session IDs (one per load generator/engine).

    Returns:
        A fresh manifest dict with all sessions in 'pending' state.
    """
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "run_id": str(run_id),
        "total_sessions": len(sessions_id),
        "created_at": now,
        "updated_at": now,
        "sessions": {},
        "combined_csv": {
            "path": "test-results.csv",
            "total_rows": 0,
            "sessions_included": []
        }
    }
    for i, sid in enumerate(sessions_id, start=1):
        key = f"session-{i}"
        manifest["sessions"][key] = {
            "session_id": sid,
            "session_index": i,
            "status": "pending",
            "stages": {
                "download": {"status": "pending", "attempts": 0},
                "extract": {"status": "pending"},
                "process": {"status": "pending"}
            }
        }
    return manifest


# ===============================================
# JTL Concatenation Helper
# ===============================================

def append_jtl_to_csv(jtl_path: str, csv_path: str, is_first: bool = False) -> int:
    """
    Appends a kpi.jtl file to the combined test-results.csv.

    - If is_first=True: writes the entire file (including header row)
    - If is_first=False: skips the first line (header) before appending

    Args:
        jtl_path: Path to the source kpi.jtl file.
        csv_path: Path to the destination test-results.csv.
        is_first: Whether this is the first session being written (include header).

    Returns:
        Number of data rows written (excludes header).
    """
    mode = "w" if is_first else "a"
    row_count = 0
    with open(jtl_path, "r", encoding="utf-8") as src:
        with open(csv_path, mode, encoding="utf-8") as dest:
            for i, line in enumerate(src):
                if i == 0 and not is_first:
                    continue  # Skip header for subsequent sessions
                dest.write(line)
                if i > 0:
                    row_count += 1  # Count data rows (not header)
    return row_count


# ===============================================
# Download with Retry
# ===============================================

async def download_with_retry(
    artifact_zip_url: str,
    dest_path: str,
    ssl_verify_setting: Union[str, bool] = False,
    auth_headers_func: callable = None,
    max_retries: int = None,
    retry_delay: int = None,
    ctx=None,
) -> dict:
    """
    Downloads an artifact ZIP file with built-in retry logic.

    Uses a two-tier header strategy: first tries minimal headers (like a browser),
    then falls back to BlazeMeter auth headers if the first attempt fails.

    Args:
        artifact_zip_url: Signed S3 URL for the artifacts.zip download.
        dest_path: Local file path to write the downloaded ZIP to.
        ssl_verify_setting: SSL verification setting (False to disable, str for CA bundle path).
        auth_headers_func: Callable that returns BlazeMeter auth headers dict.
            Signature: auth_headers_func(extra: dict = None) -> dict.
            Passed from blazemeter_api.get_headers to avoid circular imports.
        max_retries: Max download attempts. Defaults to config value or 3.
        retry_delay: Seconds between retries. Defaults to config value or 2.
        ctx: FastMCP context for logging (optional).

    Returns:
        dict with keys:
            - "status": "completed" or "failed"
            - "path": dest_path (only if completed)
            - "attempts": number of attempts made
            - "error": error message (only if failed)
    """
    if max_retries is None:
        max_retries = get_artifact_download_max_retries(config)
    if retry_delay is None:
        retry_delay = get_artifact_download_retry_delay(config)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(verify=ssl_verify_setting) as client:
                minimal_headers = {
                    "Accept": "*/*",
                    "User-Agent": "Mozilla/5.0 (compatible; BlazeMeter-MCP/1.0)",
                }
                # First try: minimal headers (works for signed S3 URLs)
                try:
                    response = await client.get(artifact_zip_url, headers=minimal_headers)
                    response.raise_for_status()
                except Exception:
                    # Fallback: BlazeMeter auth headers
                    if auth_headers_func:
                        response = await client.get(
                            artifact_zip_url,
                            headers=auth_headers_func({"Accept": "*/*"}),
                        )
                        response.raise_for_status()
                    else:
                        raise

                with open(dest_path, "wb") as f:
                    f.write(response.content)

                if ctx:
                    await ctx.info(f"Download succeeded on attempt {attempt}: {os.path.basename(dest_path)}")
                return {"status": "completed", "path": dest_path, "attempts": attempt}

        except Exception as e:
            if ctx:
                await ctx.warning(f"Download attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)

    return {
        "status": "failed",
        "error": f"Download failed after {max_retries} attempts",
        "attempts": max_retries,
    }
