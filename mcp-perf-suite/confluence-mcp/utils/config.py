import yaml
import os
import platform
from pathlib import Path


def _get_mcp_suite_root() -> str:
    """Resolve the mcp-perf-suite repo root from this file's location."""
    return str(Path(__file__).resolve().parent.parent.parent)


def load_config():
    # Assuming this file is at 'repo/<mcp-server>/utils/config.py', we go up one level.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # Platform-specific config mapping
    config_map = {
        'Darwin': 'config.mac.yaml',
        'Windows': 'config.windows.yaml'
    }

    system = platform.system()
    platform_config = config_map.get(system)
    
    # Use platform-specific config if it exists, otherwise fall back to config.yaml
    candidate_files = [platform_config, 'config.yaml'] if platform_config else ['config.yaml']
    
    config = None
    for filename in candidate_files:
        config_path = os.path.join(repo_root, filename)
        if os.path.exists(config_path):
            with open(config_path, 'r') as file:
                try:
                    config = yaml.safe_load(file)
                    break
                except yaml.YAMLError as e:
                    raise Exception(f"Error parsing '{filename}': {e}")
    
    if config is None:
        raise FileNotFoundError("No valid configuration file found (checked platform-specific and default).")

    # Dynamically resolve artifacts_path if not explicitly set
    if not config.get("artifacts", {}).get("artifacts_path"):
        config.setdefault("artifacts", {})
        config["artifacts"]["artifacts_path"] = str(
            Path(_get_mcp_suite_root()) / "artifacts"
        )

    return config

if __name__ == '__main__':
    # For testing purposes, print both configurations.
    config = load_config()
    print("Loaded general configuration:")
    print(config)
