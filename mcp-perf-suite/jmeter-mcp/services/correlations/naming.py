"""
Algorithmic variable naming and extractor configuration for correlations.

Generates deterministic JMeter variable names and extractor configurations
from correlation spec data, replacing non-deterministic LLM-based naming.

Loads customizable naming rules from correlation_config.yaml (user-local)
or falls back to correlation_config.example.yaml (repo default).
"""

import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# === Config Loading ===

_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def _get_config_dir() -> str:
    """Resolve path to jmeter-mcp/ directory (where config files live)."""
    return str(Path(__file__).resolve().parent.parent.parent)


def load_correlation_config() -> Dict[str, Any]:
    """
    Load correlation naming config from YAML.

    Resolution order:
      1. jmeter-mcp/correlation_config.yaml  (user-local, git-ignored)
      2. jmeter-mcp/correlation_config.example.yaml  (repo default)

    Result is cached for the lifetime of the process.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config_dir = _get_config_dir()
    candidates = [
        os.path.join(config_dir, "correlation_config.yaml"),
        os.path.join(config_dir, "correlation_config.example.yaml"),
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                _CONFIG_CACHE = data
                logger.debug("Loaded correlation config from %s", path)
                return _CONFIG_CACHE
            except yaml.YAMLError as exc:
                logger.warning("Failed to parse %s: %s", path, exc)

    logger.warning("No correlation config file found; using empty defaults")
    _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def reset_config_cache() -> None:
    """Clear the cached config (useful for testing)."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def _naming_section() -> Dict[str, Any]:
    """Return the 'naming' section from the loaded config."""
    return load_correlation_config().get("naming", {})


# === Case Conversion Utilities ===

# Splits on camelCase boundaries, PascalCase, acronyms, hyphens, underscores
_CAMEL_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])"     # lowerUPPER boundary
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # ACRONYMWord boundary
    r"|[-_\s]+"                   # explicit separators
)


def camel_to_snake(name: str) -> str:
    """
    Convert camelCase, PascalCase, kebab-case, or mixed strings to snake_case.

    Examples:
        entityGuid          -> entity_guid
        productDataSourceId -> product_data_source_id
        GlobalNonce         -> global_nonce
        subscription-key    -> subscription_key
        x-client-SKU        -> x_client_sku
        RelayState          -> relay_state
    """
    if not name:
        return name
    parts = _CAMEL_BOUNDARY_RE.split(name)
    parts = [p for p in parts if p]
    return "_".join(parts).lower()


# === Variable Name Generation ===

# Tracks how many times each base name has been assigned, for de-conflicting
_name_counter: Dict[str, int] = {}


def reset_name_counter() -> None:
    """Reset the de-duplication counter (call at the start of each analysis run)."""
    global _name_counter
    _name_counter = {}


def _make_unique(base_name: str) -> str:
    """
    Append a numeric suffix if the base name has already been used.

    First occurrence: "id_token"
    Second: "id_token_2"
    Third: "id_token_3"
    """
    count = _name_counter.get(base_name, 0) + 1
    _name_counter[base_name] = count
    if count == 1:
        return base_name
    return f"{base_name}_{count}"


def generate_variable_name(
    source_key: Optional[str],
    value_type: Optional[str],
    source_location: Optional[str],
    request_url: Optional[str] = None,
    suggested_name: Optional[str] = None,
) -> str:
    """
    Generate a deterministic JMeter variable name for a correlation.

    Resolution order:
      0. Suggested name (from form_post extraction, etc.)
      1. Custom mappings (highest priority user overrides)
      1.5. Set-Cookie sources: camel_to_snake on the full cookie name
      2. OAuth token field lookup (non-incrementing per RFC 6749 s6)
      3. OAuth param lookup (incremented for unique-per-step params)
      4. Timestamp pattern lookup (for orphan epoch timestamps)
      4.5. SAML param handling
      5. Algorithmic camelCase/kebab → snake_case conversion

    Args:
        source_key: The field/parameter name from the response (e.g. "entityGuid")
        value_type: The classified value type (e.g. "oauth_nonce", "timestamp")
        source_location: Where the value was found (e.g. "response_json",
                         "response_set_cookie")
        request_url: The request URL (used for timestamp URL-pattern matching)
        suggested_name: Pre-computed name from upstream extraction (e.g.
                        form_post token). Takes priority over all config lookups.

    Returns:
        A unique snake_case variable name suitable for JMeter.
    """
    # 0. Suggested name from upstream extraction logic
    # Per RFC 6749 s6, OAuth tokens are refreshable — reuse the same name
    # so all extractors write to one variable (e.g. ${bearer_token}).
    if suggested_name:
        if value_type == "oauth_token":
            _name_counter[suggested_name] = _name_counter.get(suggested_name, 0) + 1
            return suggested_name
        return _make_unique(suggested_name)

    cfg = _naming_section()
    custom = cfg.get("custom_mappings", {}) or {}
    oauth_params = cfg.get("oauth_params", {}) or {}
    oauth_tokens = cfg.get("oauth_token_fields", {}) or {}
    ts_patterns = cfg.get("timestamp_patterns", {}) or {}

    key_lower = (source_key or "").lower()

    # 1. Custom mappings (exact match on source_key, case-insensitive)
    for mapping_key, var_name in custom.items():
        if key_lower == mapping_key.lower():
            return _make_unique(var_name)

    # 1.5 Set-Cookie sources: use the full cookie name (more descriptive)
    if source_location == "response_set_cookie" and source_key:
        return _make_unique(camel_to_snake(source_key))

    # 2. OAuth token field lookup (checked BEFORE params so refreshable
    #    tokens like id_token are matched here, not in the param table).
    # Per RFC 6749 s6, refreshed tokens replace the previous value.
    # All extractors write to the same variable so ${bearer_token} always
    # holds the latest valid token.
    for token_key, var_name in oauth_tokens.items():
        if key_lower == token_key.lower():
            _name_counter[var_name] = _name_counter.get(var_name, 0) + 1
            return var_name

    # 3. OAuth param lookup (match on source_key or value_type)
    for param_key, var_name in oauth_params.items():
        if key_lower == param_key.lower():
            return _make_unique(var_name)
    if value_type:
        vt_lower = value_type.lower()
        for param_key, var_name in oauth_params.items():
            if vt_lower == f"oauth_{param_key.lower()}":
                return _make_unique(var_name)

    # 4. Timestamp handling
    if value_type == "timestamp":
        return _make_unique(_resolve_timestamp_name(request_url, ts_patterns))

    # 4.5. SAML param handling — use source_key directly (e.g. SAMLRequest → saml_request)
    if value_type and value_type.startswith("saml_"):
        if source_key:
            return _make_unique(camel_to_snake(source_key))
        return _make_unique(value_type)

    # 5. Algorithmic conversion
    if source_key:
        return _make_unique(camel_to_snake(source_key))

    # Fallback for correlations with no source_key — use value_type for context
    if value_type == "business_id_guid":
        return _make_unique("path_guid")
    if value_type == "business_id_numeric":
        return _make_unique("path_id")
    if value_type == "email":
        return _make_unique("user_email")
    if value_type == "progressive_auth":
        return _make_unique("auth_token")
    return _make_unique("corr_var")


def _resolve_timestamp_name(
    request_url: Optional[str],
    ts_patterns: Dict[str, str],
) -> str:
    """
    Determine timestamp variable prefix based on URL pattern matching.

    If the request URL contains a known substring (e.g. "signalr"),
    use the corresponding prefix; otherwise use the default.
    """
    default_prefix = ts_patterns.get("default", "cache_timestamp")
    if not request_url:
        return default_prefix

    url_lower = request_url.lower()
    for pattern, prefix in ts_patterns.items():
        if pattern == "default":
            continue
        if pattern.lower() in url_lower:
            return prefix

    return default_prefix


# === Extractor Configuration ===

def generate_extractor_config(
    source_location: Optional[str],
    source_key: Optional[str],
    source_json_path: Optional[str],
    variable_name: str,
    candidate_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate JMeter extractor configuration for a correlation.

    Returns a dict with:
      - extractor_type: "json_extractor", "regex_extractor", or "boundary_extractor"
      - expression: The JSONPath or regex expression (empty for boundary)
      - extractor_name: A human-readable extractor label
      - left_boundary / right_boundary: (boundary_extractor only)

    Args:
        source_location: Where the value was found (e.g. "response_json")
        source_key: The field/parameter name
        source_json_path: The JSONPath from walk_json (e.g. "$[0].entityGuid")
        variable_name: The generated variable name for labeling
        candidate_metadata: Optional dict with extra candidate info (e.g.
                            value_type for determining boundary templates)
    """
    cfg = _naming_section()
    extractor_types = cfg.get("extractor_types", {}) or {}
    regex_templates = cfg.get("regex_templates", {}) or {}

    loc = source_location or ""
    ext_type = extractor_types.get(loc, "regex_extractor")
    expression = ""
    ext_name = f"Extract {variable_name}"

    result: Dict[str, Any] = {}

    if ext_type == "boundary_extractor":
        left, right = _resolve_boundary_templates(source_key, loc)
        result["left_boundary"] = left
        result["right_boundary"] = right
    elif ext_type == "json_extractor" and source_json_path:
        expression = source_json_path
    elif loc in regex_templates and source_key:
        template = regex_templates[loc]
        expression = template.format(key=re.escape(source_key))
    elif source_key:
        expression = re.escape(source_key) + r"=([^&;\s]+)"

    result["extractor_type"] = ext_type
    result["expression"] = expression
    result["extractor_name"] = ext_name
    return result


# Default left/right boundary templates for WS-Fed hidden form fields
_WSFED_BOUNDARY_TEMPLATES: Dict[str, Tuple[str, str]] = {
    "wresult": (
        '<input type="hidden" name="wresult" value="',
        '">'
    ),
    "wctx": (
        '<input type="hidden" name="wctx" value="',
        '">'
    ),
    "wa": (
        '<input type="hidden" name="wa" value="',
        '">'
    ),
    "wtrealm": (
        '<input type="hidden" name="wtrealm" value="',
        '">'
    ),
}


def _resolve_boundary_templates(
    source_key: Optional[str],
    source_location: str,
) -> Tuple[str, str]:
    """
    Determine left/right boundary strings for a boundary extractor.

    For WS-Fed form fields, uses known HTML hidden input templates.
    Falls back to generic key-based boundaries.
    """
    key_lower = (source_key or "").lower()

    if source_location == "response_wsfed_form" and key_lower in _WSFED_BOUNDARY_TEMPLATES:
        return _WSFED_BOUNDARY_TEMPLATES[key_lower]

    if source_key:
        return (f'name="{source_key}" value="', '">')

    return ("", "")


# === Full Naming Pipeline (convenience) ===

def generate_correlation_naming_entry(
    correlation: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Produce a single entry for correlation_naming.json from a correlation spec record.

    Reads source_key, value_type, source_location, source_json_path, and request_url
    from the correlation dict and returns the naming + extractor config.
    """
    source = correlation.get("source", {}) or {}

    source_key = source.get("source_key")
    value_type = correlation.get("value_type")
    source_location = source.get("source_location")
    source_json_path = source.get("source_json_path")
    request_url = source.get("request_url")

    suggested = correlation.get("suggested_var_name")
    if source_location == "response_set_cookie":
        suggested = None

    var_name = generate_variable_name(
        source_key=source_key,
        value_type=value_type,
        source_location=source_location,
        request_url=request_url,
        suggested_name=suggested,
    )

    ext_cfg = generate_extractor_config(
        source_location=source_location,
        source_key=source_key,
        source_json_path=source_json_path,
        variable_name=var_name,
    )

    entry = {
        "correlation_id": correlation.get("correlation_id"),
        "variable_name": var_name,
        "jmeter_extractor_type": ext_cfg["extractor_type"],
        "jmeter_extractor_expression": ext_cfg["expression"],
        "jmeter_extractor_name": ext_cfg["extractor_name"],
        "source_request_id": source.get("request_id"),
        "source_request_url": request_url,
    }

    if ext_cfg.get("left_boundary"):
        entry["left_boundary"] = ext_cfg["left_boundary"]
    if ext_cfg.get("right_boundary"):
        entry["right_boundary"] = ext_cfg["right_boundary"]

    return entry
