import yaml
import os
import platform


def load_config():
    """Load gateway configuration with platform-specific override support.

    Resolution order:
    1. config.windows.yaml or config.mac.yaml (platform-specific)
    2. config.yaml (default fallback)
    """
    mcp_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    config_map = {
        'Darwin': 'config.mac.yaml',
        'Windows': 'config.windows.yaml'
    }

    system = platform.system()
    platform_config = config_map.get(system)

    candidate_files = [platform_config, 'config.yaml'] if platform_config else ['config.yaml']

    config = None
    for filename in candidate_files:
        config_path = os.path.join(mcp_root, filename)
        if os.path.exists(config_path):
            with open(config_path, 'r') as file:
                try:
                    config = yaml.safe_load(file)
                    break
                except yaml.YAMLError as e:
                    raise Exception(f"Error parsing '{filename}': {e}")

    if config is None:
        raise FileNotFoundError(
            "No valid configuration file found (checked platform-specific and default)."
        )

    return config


if __name__ == '__main__':
    config = load_config()
    print("Loaded gateway configuration:")
    print(config)
