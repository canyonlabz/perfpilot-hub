"""
Shared utilities for correlation analysis.

Contains URL normalization, value matching, JSON walking, and domain/path exclusion.
"""

import re
from fnmatch import fnmatch
from typing import Any, List, Set, Tuple
from urllib.parse import unquote, urlparse

from .constants import MAX_JSON_DEPTH

# === Domain & Path Exclusion ===

# Cached exclude lists (loaded once via init_exclude_domains)
_EXCLUDE_DOMAINS: List[str] = []
_EXCLUDE_PATHS: List[str] = []


def init_exclude_domains(config: dict) -> None:
    """Initialize excluded domains and paths from config (APM, analytics, SignalR, etc.)."""
    global _EXCLUDE_DOMAINS, _EXCLUDE_PATHS
    try:
        network_config = config.get("network_capture", {})
        _EXCLUDE_DOMAINS = network_config.get("exclude_domains", [])
        _EXCLUDE_PATHS = network_config.get("exclude_paths", [])
    except Exception:
        _EXCLUDE_DOMAINS = []
        _EXCLUDE_PATHS = []


def get_exclude_domains() -> List[str]:
    """Get the current exclude domains list."""
    return _EXCLUDE_DOMAINS


# TODO(exclude-paths-unify): This function duplicates filtering logic that also
# exists in services/network_capture.py:should_capture_url(). The capture-time
# function handles additional filters (static assets, fonts, video, third-party).
# See docs/plans/exclude-paths-filtering-gap.md Phase 2 for unification plan.
def is_excluded_url(url: str) -> bool:
    """Check if URL should be excluded based on domain and path exclusion lists."""
    if not url:
        return False
    if not _EXCLUDE_DOMAINS and not _EXCLUDE_PATHS:
        return False

    try:
        parsed = urlparse(url)
        hostname = parsed.netloc.lower()
        path = parsed.path.lower()

        for domain in _EXCLUDE_DOMAINS:
            domain_lower = domain.lower()
            # Match exact domain or subdomain
            if hostname == domain_lower or hostname.endswith("." + domain_lower) or domain_lower in hostname:
                return True

        for pattern in _EXCLUDE_PATHS:
            if fnmatch(path, pattern.lower()):
                return True

        return False
    except Exception:
        return False


# === URL Normalization ===

def normalize_for_comparison(value: str) -> Set[str]:
    """Return set of normalized forms for comparison (handles URL encoding)."""
    forms = {value}
    try:
        decoded = unquote(value)
        forms.add(decoded)
    except Exception:
        pass
    return forms


def value_matches(needle: str, haystack: str, exact: bool = False) -> bool:
    """
    Check if needle appears in haystack, considering URL encoding.
    
    Args:
        needle: The value to search for
        haystack: The string to search in
        exact: If True, require exact match. If False, use word boundary matching
               for short values to avoid false positives (e.g., "11" in UUID).
    """
    if not needle or not haystack:
        return False
    
    needle_forms = normalize_for_comparison(needle)
    haystack_forms = normalize_for_comparison(haystack)
    
    for n in needle_forms:
        for h in haystack_forms:
            if exact:
                # Exact match
                if n == h:
                    return True
            elif len(n) <= 4:
                # Short values: use word boundary to avoid false positives
                pattern = r'(?<![a-zA-Z0-9])' + re.escape(n) + r'(?![a-zA-Z0-9])'
                if re.search(pattern, h):
                    return True
            else:
                # Longer values: substring match is safe
                if n in h:
                    return True
    return False


# === JSON Walking ===

def walk_json(obj: Any, path: str = "$", depth: int = 0) -> List[Tuple[str, Any, str]]:
    """
    Recursively walk JSON object, yielding (json_path, value, key_name) tuples.
    
    Extracts values from keys matching ID_KEY_PATTERNS or OAUTH_TOKEN_FIELDS
    (case-insensitive). This ensures token fields like accessToken are detected
    at arbitrary nesting depth (e.g., $.data.currentUser.accessToken).

    Respects MAX_JSON_DEPTH to avoid overly deep traversal.
    
    Used for SOURCE extraction (looking for ID and token fields).
    """
    from .constants import ID_KEY_PATTERNS, OAUTH_TOKEN_FIELDS
    
    oauth_token_fields_lower = {f.lower() for f in OAUTH_TOKEN_FIELDS}

    results = []
    
    if depth > MAX_JSON_DEPTH:
        return results
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}"
            is_id_key = ID_KEY_PATTERNS.search(key)
            is_token_key = key.lower() in oauth_token_fields_lower
            if is_id_key or is_token_key:
                if isinstance(value, (str, int, float, bool)):
                    results.append((new_path, value, key))
            if isinstance(value, (dict, list)):
                results.extend(walk_json(value, new_path, depth + 1))
    
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = f"{path}[{i}]"
            results.extend(walk_json(item, new_path, depth + 1))
    
    return results


def walk_json_all_values(obj: Any, path: str = "$", depth: int = 0) -> List[Tuple[str, Any, str]]:
    """
    Walk JSON and extract ALL primitive values (for usage detection).
    
    Unlike walk_json which only extracts ID-like keys, this extracts
    all values so we can find where known values are being used.
    
    Used for USAGE detection (searching for known values).
    """
    results = []
    
    if depth > MAX_JSON_DEPTH:
        return results
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}"
            # Add all primitive values
            if isinstance(value, (str, int, float, bool)):
                results.append((new_path, value, key))
            # Recurse into nested objects
            if isinstance(value, (dict, list)):
                results.extend(walk_json_all_values(value, new_path, depth + 1))
    
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            new_path = f"{path}[{i}]"
            if isinstance(item, (str, int, float, bool)):
                results.append((new_path, item, f"[{i}]"))
            if isinstance(item, (dict, list)):
                results.extend(walk_json_all_values(item, new_path, depth + 1))
    
    return results
