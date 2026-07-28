"""
YAML parsing, validation, and diff utilities.

Provides helpers for working with YAML content including
syntax validation, structural comparison, and formatting.
"""

from io import StringIO
from typing import Optional

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


_yaml = YAML()
_yaml.preserve_quotes = True


def validate_yaml_syntax(content: str) -> tuple[bool, Optional[str]]:
    """
    Check if a string is valid YAML.

    Args:
        content: YAML string to validate.

    Returns:
        Tuple of (is_valid, error_message).
        error_message is None if valid.
    """
    try:
        _yaml.load(StringIO(content))
        return True, None
    except YAMLError as e:
        return False, str(e)


def parse_yaml(content: str) -> Optional[dict]:
    """
    Parse a YAML string into a dictionary.

    Args:
        content: YAML string.

    Returns:
        dict or None if parsing fails.
    """
    try:
        data = _yaml.load(StringIO(content))
        return dict(data) if data else {}
    except YAMLError:
        return None


def dump_yaml(data: dict) -> str:
    """
    Dump a dictionary to a YAML string.

    Args:
        data: Dictionary to serialize.

    Returns:
        YAML string.
    """
    stream = StringIO()
    _yaml.dump(data, stream)
    return stream.getvalue()


def compare_configs(source: dict, target: dict, prefix: str = "") -> list[dict]:
    """
    Compare two config dictionaries and return the differences.

    Args:
        source: Source (original) configuration.
        target: Target (modified) configuration.
        prefix: Key prefix for nested traversal.

    Returns:
        List of dicts with: key, source_value, target_value, change_type
        (added, removed, modified, unchanged).
    """
    diffs = []
    all_keys = set(list(source.keys()) + list(target.keys()))

    for key in sorted(all_keys):
        full_key = f"{prefix}.{key}" if prefix else key
        in_source = key in source
        in_target = key in target

        if in_source and not in_target:
            diffs.append({
                "key": full_key,
                "source_value": source[key],
                "target_value": None,
                "change_type": "removed",
            })

        elif not in_source and in_target:
            diffs.append({
                "key": full_key,
                "source_value": None,
                "target_value": target[key],
                "change_type": "added",
            })

        elif isinstance(source[key], dict) and isinstance(target[key], dict):
            diffs.extend(compare_configs(source[key], target[key], full_key))

        elif source[key] != target[key]:
            diffs.append({
                "key": full_key,
                "source_value": source[key],
                "target_value": target[key],
                "change_type": "modified",
            })

    return diffs
