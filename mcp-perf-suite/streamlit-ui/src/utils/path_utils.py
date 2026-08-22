"""
Path resolution and adjustment utilities.

Handles MCP suite root detection, path placeholder replacement,
and cross-platform path normalization.
"""

import os
import re
from pathlib import Path
from typing import Optional


def get_mcp_suite_root() -> Path:
    """
    Get the MCP suite root directory.

    Priority:
    1. MCP_SUITE_ROOT environment variable
    2. Parent of the streamlit-ui/ directory
    """
    env_root = os.environ.get("MCP_SUITE_ROOT")
    if env_root:
        return Path(env_root)
    # Assume this file is at streamlit-ui/src/utils/path_utils.py
    return Path(__file__).resolve().parent.parent.parent.parent


def get_artifacts_path(config: Optional[dict] = None) -> Path:
    """Get the artifacts directory path."""
    if config and config.get("general", {}).get("artifacts_path"):
        return Path(config["general"]["artifacts_path"])
    return get_mcp_suite_root() / "artifacts"


def get_server_directory(server_dir_name: str, config: Optional[dict] = None) -> Path:
    """Get the full path to an MCP server directory."""
    root = get_mcp_suite_root()
    if config and config.get("general", {}).get("mcp_suite_root"):
        root = Path(config["general"]["mcp_suite_root"])
    return root / server_dir_name


def is_path_placeholder(value: str) -> bool:
    """
    Check if a string value contains a path placeholder pattern.

    Detects patterns like:
    - C:\\<path_to_project_folder>\\...
    - /path_to_root/...
    - <path_to_jmeter>
    - <site-id>, <team-id>, etc.
    """
    if not isinstance(value, str):
        return False
    return bool(re.search(r"<[^>]+>", value))


def detect_path_fields(data: dict, prefix: str = "") -> list[dict]:
    """
    Recursively scan a config dict and identify fields that look like paths.

    Returns a list of dicts with:
    - key: dot-separated key path (e.g., "logging.log_path")
    - value: current value
    - is_placeholder: whether it contains <placeholder> patterns
    - exists: whether the path exists on disk (None if placeholder)
    """
    results = []
    path_indicators = [
        "path", "dir", "directory", "folder", "home", "bin",
        "root", "log_path", "json_path",
    ]

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            results.extend(detect_path_fields(value, full_key))
        elif isinstance(value, str):
            # Check if the key name suggests it's a path
            key_lower = key.lower()
            is_path_key = any(ind in key_lower for ind in path_indicators)
            has_placeholder = is_path_placeholder(value)

            if is_path_key or has_placeholder:
                exists = None
                if not has_placeholder and value.strip():
                    exists = Path(value).exists()

                results.append({
                    "key": full_key,
                    "value": value,
                    "is_placeholder": has_placeholder,
                    "exists": exists,
                })

    return results


def replace_path_root(value: str, old_root: str, new_root: str) -> str:
    """
    Replace a path root prefix from old_root to new_root.

    Handles both forward and backslash separators.
    """
    if not isinstance(value, str):
        return value

    # Normalize for comparison
    normalized_value = value.replace("\\", "/")
    normalized_old = old_root.replace("\\", "/").rstrip("/")
    normalized_new = new_root.replace("\\", "/").rstrip("/")

    if normalized_value.startswith(normalized_old):
        remainder = normalized_value[len(normalized_old):]
        new_path = normalized_new + remainder

        # Preserve original separator style
        if "\\" in value and "/" not in value:
            new_path = new_path.replace("/", "\\")

        return new_path

    return value
