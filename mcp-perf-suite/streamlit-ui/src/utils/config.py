"""
UI configuration loader with platform-aware override support.

Loads config.yaml from the streamlit-ui/ directory. If a platform-specific
override exists (config.windows.yaml or config.mac.yaml), those values
take precedence.
"""

import os
import platform
from pathlib import Path

import yaml


def _get_config_dir() -> Path:
    """Return the streamlit-ui/ directory (where config files live)."""
    return Path(__file__).resolve().parent.parent.parent


def _get_mcp_suite_root() -> Path:
    """
    Determine the MCP suite root directory.

    Priority:
    1. MCP_SUITE_ROOT environment variable (set by app.py launcher)
    2. Parent of the streamlit-ui/ directory
    """
    env_root = os.environ.get("MCP_SUITE_ROOT")
    if env_root:
        return Path(env_root)
    return _get_config_dir().parent


def load_config() -> dict:
    """
    Load the UI configuration from config.yaml with optional platform overrides.

    Returns:
        dict: Merged configuration dictionary.
    """
    config_dir = _get_config_dir()
    config = {}

    # Load base config
    base_config_path = config_dir / "config.yaml"
    if base_config_path.exists():
        with open(base_config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # Check for platform-specific override
    platform_map = {
        "Windows": "config.windows.yaml",
        "Darwin": "config.mac.yaml",
        "Linux": "config.linux.yaml",
    }
    platform_file = platform_map.get(platform.system())
    if platform_file:
        platform_path = config_dir / platform_file
        if platform_path.exists():
            with open(platform_path, "r", encoding="utf-8") as f:
                platform_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, platform_config)

    # Set defaults
    config.setdefault("general", {})
    config.setdefault("ui", {})
    config.setdefault("migration", {})

    # Resolve MCP suite root
    if not config["general"].get("mcp_suite_root"):
        config["general"]["mcp_suite_root"] = str(_get_mcp_suite_root())

    # Resolve artifacts path
    if not config["general"].get("artifacts_path"):
        config["general"]["artifacts_path"] = str(
            Path(config["general"]["mcp_suite_root"]) / "artifacts"
        )

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep merge two dictionaries. Override values take precedence.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
