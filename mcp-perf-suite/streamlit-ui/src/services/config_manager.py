"""
YAML Configuration Manager - Read, write, validate, and manage config files.

Uses ruamel.yaml for round-trip editing that preserves comments and formatting.
Provides config discovery, validation, and backup functionality.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from src.utils.path_utils import get_mcp_suite_root, detect_path_fields
from src.utils.state import MCP_SERVERS


# Initialize ruamel.yaml with round-trip (comment-preserving) mode
_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.default_flow_style = False


def discover_configs(mcp_root: Optional[Path] = None) -> dict:
    """
    Scan all MCP server directories and discover config files.

    Returns:
        dict: Map of server_name -> list of dicts with:
            - file: config filename
            - path: full Path to the file
            - exists: whether the file exists
            - example_path: path to example file (if applicable)
            - example_exists: whether the example file exists
    """
    if mcp_root is None:
        mcp_root = get_mcp_suite_root()

    results = {}

    for server_name, server_info in MCP_SERVERS.items():
        server_dir = mcp_root / server_info["directory"]
        configs = []

        for config_file in server_info["config_files"]:
            config_path = server_dir / config_file
            example_path = None
            example_exists = False

            # Check for example file
            if config_file == "config.yaml":
                example_path = server_dir / "config.example.yaml"
                example_exists = example_path.exists() if example_path else False
            elif config_file.endswith(".json"):
                example_name = config_file.replace(".json", ".example.json")
                example_path = server_dir / example_name
                example_exists = example_path.exists() if example_path else False

            configs.append({
                "file": config_file,
                "path": config_path,
                "exists": config_path.exists(),
                "example_path": example_path,
                "example_exists": example_exists,
            })

        results[server_name] = configs

    return results


def load_config(path: Path) -> dict:
    """
    Load a YAML or JSON config file preserving structure.

    Args:
        path: Path to the config file.

    Returns:
        dict: Parsed configuration data.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file format is unsupported.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    suffix = path.suffix.lower()

    if suffix in (".yaml", ".yml"):
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.load(f)
        return dict(data) if data else {}

    elif suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    else:
        raise ValueError(f"Unsupported config file format: {suffix}")


def load_config_raw(path: Path) -> str:
    """
    Load a config file as raw text.

    Args:
        path: Path to the config file.

    Returns:
        str: Raw file content.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    return path.read_text(encoding="utf-8")


def save_config(path: Path, data: dict, backup: bool = True):
    """
    Save configuration data to a YAML or JSON file.

    Creates a .bak backup of the existing file before overwriting.

    Args:
        path: Target file path.
        data: Configuration data to write.
        backup: Whether to create a backup of the existing file.

    Raises:
        ValueError: If the file format is unsupported.
    """
    suffix = path.suffix.lower()

    # Create backup
    if backup and path.exists():
        backup_path = path.with_suffix(f"{path.suffix}.bak")
        shutil.copy2(path, backup_path)

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    if suffix in (".yaml", ".yml"):
        with open(path, "w", encoding="utf-8") as f:
            _yaml.dump(data, f)

    elif suffix == ".json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    else:
        raise ValueError(f"Unsupported config file format: {suffix}")


def save_config_raw(path: Path, content: str, backup: bool = True):
    """
    Save raw text content to a config file.

    Creates a .bak backup of the existing file before overwriting.

    Args:
        path: Target file path.
        content: Raw text content to write.
        backup: Whether to create a backup of the existing file.
    """
    # Create backup
    if backup and path.exists():
        backup_path = path.with_suffix(f"{path.suffix}.bak")
        shutil.copy2(path, backup_path)

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")


def validate_config(server_name: str, data: dict) -> list[str]:
    """
    Validate a configuration dictionary for a given MCP server.

    Performs:
    - Required field checks
    - Type validation
    - Path existence checks

    Args:
        server_name: Name of the MCP server.
        data: Configuration data to validate.

    Returns:
        list[str]: List of validation error messages. Empty if valid.
    """
    errors = []

    if not data:
        errors.append("Configuration is empty.")
        return errors

    # Common required sections
    common_sections = ["server", "general", "logging", "artifacts"]
    for section in common_sections:
        if section not in data:
            errors.append(f"Missing required section: '{section}'")

    # Validate path fields exist on disk
    path_fields = detect_path_fields(data)
    for field in path_fields:
        if field["is_placeholder"]:
            errors.append(
                f"Path field '{field['key']}' contains placeholder value: {field['value']}"
            )
        elif field["exists"] is False:
            errors.append(
                f"Path field '{field['key']}' does not exist: {field['value']}"
            )

    return errors


def reset_to_example(server_name: str, config_file: str = "config.yaml") -> bool:
    """
    Reset a config file to its example template.

    Args:
        server_name: MCP server name.
        config_file: Name of the config file to reset.

    Returns:
        bool: True if reset was successful.
    """
    mcp_root = get_mcp_suite_root()
    server_info = MCP_SERVERS.get(server_name)

    if not server_info:
        return False

    server_dir = mcp_root / server_info["directory"]
    config_path = server_dir / config_file

    # Find example file
    if config_file == "config.yaml":
        example_path = server_dir / "config.example.yaml"
    elif config_file.endswith(".json"):
        example_name = config_file.replace(".json", ".example.json")
        example_path = server_dir / example_name
    else:
        return False

    if not example_path.exists():
        return False

    # Backup existing and copy example
    if config_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = config_path.with_suffix(f".{timestamp}.bak")
        shutil.copy2(config_path, backup_path)

    shutil.copy2(example_path, config_path)
    return True
