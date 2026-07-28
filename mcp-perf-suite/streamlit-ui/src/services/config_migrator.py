"""
Config Migrator - Transfer settings between MCP suite repo instances.

Extracts user-customized settings from a source repo (by diffing against
example templates) and applies them to a destination repo with automatic
path adjustment and flagged-field detection.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from deepdiff import DeepDiff
from ruamel.yaml import YAML

from src.utils.state import MCP_SERVERS
from src.utils.config import load_config
from src.utils.path_utils import replace_path_root, is_path_placeholder

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.default_flow_style = False


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_source(source_path: str) -> dict:
    """
    Scan a source MCP suite repo and discover configured files.

    Returns:
        dict: server_name -> list of file info dicts:
            file, path, has_example, example_path, is_json
    """
    root = Path(source_path)
    if not root.exists():
        return {}

    results = {}
    for server_name, server_info in MCP_SERVERS.items():
        server_dir = root / server_info["directory"]
        if not server_dir.exists():
            continue

        configs = []
        for config_file in server_info["config_files"]:
            config_path = server_dir / config_file
            if not config_path.exists():
                continue

            example_path = _find_example(server_dir, config_file)
            configs.append({
                "file": config_file,
                "path": str(config_path),
                "has_example": example_path is not None,
                "example_path": str(example_path) if example_path else None,
                "is_json": config_file.endswith(".json"),
            })

        if configs:
            results[server_name] = configs

    return results


def scan_destination(dest_path: str) -> dict:
    """
    Scan a destination repo and discover example/template files.

    Returns:
        dict: server_name -> list of template info dicts.
    """
    root = Path(dest_path)
    if not root.exists():
        return {}

    results = {}
    for server_name, server_info in MCP_SERVERS.items():
        server_dir = root / server_info["directory"]
        if not server_dir.exists():
            continue

        templates = []
        for config_file in server_info["config_files"]:
            config_path = server_dir / config_file
            example_path = _find_example(server_dir, config_file)

            templates.append({
                "file": config_file,
                "config_exists": config_path.exists(),
                "example_exists": example_path is not None,
                "example_path": str(example_path) if example_path else None,
                "dest_path": str(config_path),
                "is_json": config_file.endswith(".json"),
            })

        results[server_name] = templates

    return results


# ---------------------------------------------------------------------------
# Delta Computation
# ---------------------------------------------------------------------------

def compute_deltas(source_results: dict, source_path: str) -> dict:
    """
    For each configured file in the source, compute the delta between
    the source's config and its corresponding example.

    For files without examples (e.g., slas.yaml, workflow.yaml, environments.json),
    the entire content is treated as user-customized.

    Returns:
        dict: server_name -> list of delta dicts:
            file, deltas (list of changes), source_data, has_example
    """
    all_deltas = {}

    for server_name, configs in source_results.items():
        server_deltas = []

        for cfg in configs:
            source_data = _load_file(cfg["path"])
            if source_data is None:
                continue

            if cfg["has_example"] and cfg["example_path"]:
                example_data = _load_file(cfg["example_path"])
                if example_data is not None:
                    diff = DeepDiff(example_data, source_data, ignore_order=True)
                    changes = _diff_to_changes(diff)
                else:
                    changes = [{"type": "full_copy", "reason": "Example could not be loaded"}]
            else:
                # No example - treat entire file as customized
                changes = [{"type": "full_copy", "reason": "No example template exists"}]

            server_deltas.append({
                "file": cfg["file"],
                "source_path": cfg["path"],
                "deltas": changes,
                "source_data": source_data,
                "has_example": cfg["has_example"],
                "is_json": cfg["is_json"],
            })

        if server_deltas:
            all_deltas[server_name] = server_deltas

    return all_deltas


def _diff_to_changes(diff: DeepDiff) -> list[dict]:
    """Convert DeepDiff output to a simplified list of changes."""
    changes = []

    # Values changed
    for path, change in diff.get("values_changed", {}).items():
        changes.append({
            "type": "modified",
            "path": path,
            "old_value": change.get("old_value"),
            "new_value": change.get("new_value"),
        })

    # Items added
    for path, value in diff.get("dictionary_item_added", {}).items():
        changes.append({"type": "added", "path": path, "new_value": value})

    # Items removed
    for path, value in diff.get("dictionary_item_removed", {}).items():
        changes.append({"type": "removed", "path": path, "old_value": value})

    # Type changes
    for path, change in diff.get("type_changes", {}).items():
        changes.append({
            "type": "type_changed",
            "path": path,
            "old_value": change.get("old_value"),
            "new_value": change.get("new_value"),
        })

    return changes


# ---------------------------------------------------------------------------
# Path Adjustment
# ---------------------------------------------------------------------------

def adjust_paths(
    deltas: dict,
    source_root: str,
    dest_root: str,
    path_keys: Optional[list[str]] = None,
) -> dict:
    """
    Adjust path-like values in the deltas from source root to dest root.

    Modifies the source_data in-place for each delta entry.
    """
    if path_keys is None:
        ui_config = load_config()
        path_keys = ui_config.get("migration", {}).get("path_keys", [])

    for server_name, server_deltas in deltas.items():
        for delta in server_deltas:
            _adjust_paths_recursive(
                delta["source_data"], source_root, dest_root, path_keys
            )

    return deltas


def _adjust_paths_recursive(data, src_root: str, dst_root: str, path_keys: list[str]):
    """Recursively adjust path values in a config dict."""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str) and key in path_keys:
                if not is_path_placeholder(value):
                    data[key] = replace_path_root(value, src_root, dst_root)
            elif isinstance(value, dict):
                _adjust_paths_recursive(value, src_root, dst_root, path_keys)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _adjust_paths_recursive(item, src_root, dst_root, path_keys)


# ---------------------------------------------------------------------------
# Flagged Fields Detection
# ---------------------------------------------------------------------------

def detect_flagged_fields(data: dict, flagged_keys: Optional[list[str]] = None) -> list[dict]:
    """
    Detect fields that should be flagged for manual review during migration.

    Returns:
        list of dicts: key, value, reason
    """
    if flagged_keys is None:
        ui_config = load_config()
        flagged_keys = ui_config.get("migration", {}).get("flagged_keys", [])

    flagged = []
    _scan_flagged_recursive(data, flagged_keys, flagged, prefix="")
    return flagged


def _scan_flagged_recursive(data, flagged_keys: list[str], results: list, prefix: str):
    """Recursively find flagged fields."""
    if isinstance(data, dict):
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if key in flagged_keys:
                results.append({
                    "key": full_key,
                    "value": value,
                    "reason": "Environment-specific ID - may differ between instances",
                })
            if isinstance(value, dict):
                _scan_flagged_recursive(value, flagged_keys, results, full_key)


# ---------------------------------------------------------------------------
# Preview & Apply
# ---------------------------------------------------------------------------

def preview_migration(deltas: dict, dest_results: dict) -> list[dict]:
    """
    Generate a preview of what migration will do.

    Returns:
        list of action dicts: server, file, action, details
    """
    actions = []

    for server_name, server_deltas in deltas.items():
        dest_templates = dest_results.get(server_name, [])
        dest_map = {t["file"]: t for t in dest_templates}

        for delta in server_deltas:
            dest_info = dest_map.get(delta["file"], {})
            change_count = len(delta["deltas"])

            if delta["deltas"] and delta["deltas"][0].get("type") == "full_copy":
                action = "copy"
                details = f"Full file copy ({delta['deltas'][0].get('reason', '')})"
            elif change_count > 0:
                action = "merge"
                details = f"{change_count} setting(s) changed"
            else:
                action = "skip"
                details = "No changes detected"

            # Check for flagged fields
            flagged = detect_flagged_fields(delta["source_data"])

            actions.append({
                "server": server_name,
                "file": delta["file"],
                "action": action,
                "details": details,
                "change_count": change_count,
                "dest_exists": dest_info.get("config_exists", False),
                "flagged_fields": flagged,
            })

    return actions


def apply_migration(deltas: dict, dest_path: str) -> list[dict]:
    """
    Apply migration by writing adjusted source configs to the destination.

    Creates backups of any existing files in the destination.

    Returns:
        list of result dicts: server, file, status, message
    """
    results = []
    dest_root = Path(dest_path)

    for server_name, server_deltas in deltas.items():
        server_info = MCP_SERVERS.get(server_name)
        if not server_info:
            continue

        server_dir = dest_root / server_info["directory"]

        for delta in server_deltas:
            config_file = delta["file"]
            dest_file = server_dir / config_file

            try:
                # Backup existing
                if dest_file.exists():
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_path = dest_file.with_suffix(f".{timestamp}.bak")
                    shutil.copy2(dest_file, backup_path)

                # Write new config
                server_dir.mkdir(parents=True, exist_ok=True)

                if delta["is_json"]:
                    with open(dest_file, "w", encoding="utf-8") as f:
                        json.dump(delta["source_data"], f, indent=2, ensure_ascii=False)
                        f.write("\n")
                else:
                    with open(dest_file, "w", encoding="utf-8") as f:
                        _yaml.dump(delta["source_data"], f)

                results.append({
                    "server": server_name,
                    "file": config_file,
                    "status": "success",
                    "message": f"Written to {dest_file}",
                })

            except Exception as e:
                results.append({
                    "server": server_name,
                    "file": config_file,
                    "status": "error",
                    "message": str(e),
                })

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_example(server_dir: Path, config_file: str) -> Optional[Path]:
    """Find the example/template file corresponding to a config file."""
    if config_file == "config.yaml":
        example = server_dir / "config.example.yaml"
    elif config_file.endswith(".json"):
        example = server_dir / config_file.replace(".json", ".example.json")
    else:
        return None
    return example if example.exists() else None


def _load_file(path_str: str) -> Optional[dict]:
    """Load a YAML or JSON file and return as dict."""
    path = Path(path_str)
    if not path.exists():
        return None

    try:
        if path.suffix in (".yaml", ".yml"):
            with open(path, "r", encoding="utf-8") as f:
                data = _yaml.load(f)
            return dict(data) if data else {}
        elif path.suffix == ".json":
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None

    return None
