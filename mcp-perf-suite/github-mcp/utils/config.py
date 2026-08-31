import yaml
import os
import platform
from pathlib import Path


def _get_mcp_suite_root() -> str:
    """Resolve the mcp-perf-suite repo root from this file's location."""
    return str(Path(__file__).resolve().parent.parent.parent)


def load_config():
    """Load the github-mcp config file.

    Priority order:
      1. Platform-specific file (``config.windows.yaml`` on Windows,
         ``config.mac.yaml`` on Darwin).
      2. ``config.yaml`` (gitignored, user override).
      3. ``config.example.yaml`` (checked-in fallback).
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    config_map = {
        'Darwin': 'config.mac.yaml',
        'Windows': 'config.windows.yaml',
    }

    system = platform.system()
    platform_config = config_map.get(system)

    candidate_files = []
    if platform_config:
        candidate_files.append(platform_config)
    candidate_files.append('config.yaml')
    candidate_files.append('config.example.yaml')

    config = None
    for filename in candidate_files:
        config_path = os.path.join(repo_root, filename)
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8-sig') as file:
                try:
                    config = yaml.safe_load(file)
                    break
                except yaml.YAMLError as e:
                    raise Exception(f"Error parsing '{filename}': {e}")

    if config is None:
        raise FileNotFoundError(
            "No valid configuration file found for github-mcp "
            "(checked platform-specific, config.yaml, and config.example.yaml)."
        )

    if not config.get("artifacts", {}).get("artifacts_path"):
        config.setdefault("artifacts", {})
        config["artifacts"]["artifacts_path"] = str(
            Path(_get_mcp_suite_root()) / "artifacts"
        )

    return config


if __name__ == '__main__':
    cfg = load_config()
    print("Loaded github-mcp configuration:")
    print(cfg)
