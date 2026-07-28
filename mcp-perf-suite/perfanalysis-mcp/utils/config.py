import yaml
import os
import platform
import json
from typing import Dict
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

def load_environments_config(environment: str) -> Dict:
    """
    Load the Datadog environments.json configuration and return ONLY the requested
    environment (human-readable alias). If the environment does not exist, raise an error.

    Returns a dict with the same top-level shape used elsewhere in the codebase:
    {
        "schema_version": <optional>,
        "environments": { "<RequestedEnv>": { ...env config... } }
    }
    """
    try:
        # Path should be relative to project root
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        env_file = Path(repo_root) / "datadog-mcp" / "environments.json"    # TODO: Later update to pull 'apm_tool' from config.yaml.
        if not env_file.exists():
            # Fallback - look in current directory
            env_file = Path("environments.json")
            if not env_file.exists():
                return {"environments": {}}  # Keep previous behavior if file not found

        with open(env_file, 'r') as f:
            data = json.load(f)

        all_envs = data.get("environments", {})
        if not isinstance(all_envs, dict):
            raise ValueError("Invalid environments.json format: 'environments' must be a mapping")

        # Try direct lookup first, then case-insensitive match
        selected_name = None
        if environment in all_envs:
            selected_name = environment
        else:
            for name in all_envs.keys():
                if name.lower() == environment.lower():
                    selected_name = name
                    break

        if not selected_name:
            available = ", ".join(sorted(all_envs.keys()))
            raise ValueError(
                f"Environment '{environment}' not found in environments.json. "
                f"Available: {available if available else 'None'}"
            )

        selected_env = all_envs[selected_name]
        # Return only the selected environment, preserving expected shape
        result: Dict = {
            "environments": {selected_name: selected_env}
        }
        # Preserve schema_version if present
        if "schema_version" in data:
            result["schema_version"] = data["schema_version"]

        return result
    except Exception as e:
        # Preserve previous fallback behavior on unexpected errors, but include message
        return {"environments": {}, "error": str(e)}

if __name__ == '__main__':
    # For testing purposes, print both configurations.
    config = load_config()
    print("Loaded general configuration:")
    print(config)
