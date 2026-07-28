# orphan_helpers.py
import os
import json
import urllib.parse
from typing import Dict, List, Optional, Any
from fastmcp import Context  # ✅ FastMCP 2.x import

from utils.config import load_config, load_jmeter_config

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
JMETER_CONFIG = load_jmeter_config()

# ============================================================
# Helper Functions - Orphan Variable Handling (Phase D)
# ============================================================

def _extract_static_header_config(
    correlation_spec: Dict[str, Any],
    correlation_naming: Dict[str, Any]
) -> tuple:
    """
    Extract static header correlations for UDV parameterization and header substitution.

    Scans the correlation spec for type ``static_header`` entries and resolves
    variable names from the correlation naming file.

    Args:
        correlation_spec: The loaded correlation_spec.json data
        correlation_naming: The loaded correlation_naming.json data

    Returns:
        Tuple of (udv_vars, header_sub_map) where:
        - udv_vars: variable_name -> header_value (for User Defined Variables)
        - header_sub_map: header_name_lower -> variable_name (for header substitution)
    """
    udv_vars: Dict[str, str] = {}
    header_sub_map: Dict[str, str] = {}

    naming_lookup: Dict[str, str] = {}
    for var in correlation_naming.get("variables", []):
        cid = var.get("correlation_id", "")
        vname = var.get("variable_name", "")
        if cid and vname:
            naming_lookup[cid] = vname

    for corr in correlation_spec.get("correlations", []):
        if corr.get("type") != "static_header":
            continue

        corr_id = corr.get("correlation_id", "")
        source = corr.get("source", {})
        header_name = source.get("source_key", "")
        value = source.get("response_example_value", "")

        if not header_name or not value:
            continue

        hint = corr.get("parameterization_hint", {})
        var_name = (
            naming_lookup.get(corr_id)
            or hint.get("suggested_var_name")
            or header_name.lower().replace("-", "_")
        )

        udv_vars[var_name] = str(value)
        header_sub_map[header_name.lower()] = var_name

    return udv_vars, header_sub_map


def _extract_orphan_values(correlation_spec: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract orphan ID values from correlation_spec.json.
    
    Orphan IDs are correlations where correlation_found is false.
    They have a value in source.response_example_value but no source response.
    
    Args:
        correlation_spec: The loaded correlation_spec.json data
        
    Returns:
        Dictionary mapping correlation_id to the actual value
    """
    orphan_values = {}
    
    for corr in correlation_spec.get("correlations", []):
        if not corr.get("correlation_found", True):
            corr_id = corr.get("correlation_id", "")
            source = corr.get("source", {})
            value = source.get("response_example_value", "")
            
            if corr_id and value:
                orphan_values[corr_id] = str(value)
    
    return orphan_values


def _get_orphan_udv_variables(
    correlation_naming: Dict[str, Any],
    orphan_values: Dict[str, str]
) -> Dict[str, str]:
    """
    Build User Defined Variables dictionary from orphan variables.
    
    Filters orphan variables where parameterization_strategy is "user_defined_variable"
    and joins with actual values from correlation_spec.
    
    Special handling:
    - SignalR timestamps (variable names containing 'signalr_timestamp' or 'signalr_ts'):
      Use JMeter's ${__time()} function instead of literal value
    
    Args:
        correlation_naming: The loaded correlation_naming.json data
        orphan_values: Mapping from correlation_id to actual value
        
    Returns:
        Dictionary of variable_name -> value suitable for UDV
    """
    udv_vars = {}
    
    for orphan in correlation_naming.get("orphan_variables", []):
        strategy = orphan.get("parameterization_strategy", "")
        
        # Only process user_defined_variable strategy
        if strategy != "user_defined_variable":
            continue
        
        corr_id = orphan.get("correlation_id", "")
        var_name = orphan.get("variable_name", "")
        
        if not var_name:
            continue
        
        # Get the actual value from orphan_values
        value = orphan_values.get(corr_id, "")
        
        # Special handling for SignalR timestamps
        # These should use ${__time()} function for dynamic generation
        if "signalr" in var_name.lower() and "timestamp" in var_name.lower():
            value = "${__time()}"
        elif "signalr" in var_name.lower() and "_ts" in var_name.lower():
            value = "${__time()}"
        
        if var_name and value:
            udv_vars[var_name] = value
    
    return udv_vars


def _merge_udv_config(
    base_config: Dict[str, Any],
    orphan_variables: Dict[str, str]
) -> Dict[str, Any]:
    """
    Merge orphan variables into the base UDV configuration.
    
    If there are orphan variables to add, ensures enabled=true and merges
    the variables dictionaries. Orphan variables do not override existing
    variables with the same name (config takes precedence).
    
    Args:
        base_config: The UDV configuration from jmeter_config.yaml
        orphan_variables: Dictionary of orphan variable_name -> value
        
    Returns:
        Merged configuration dictionary
    """
    if not orphan_variables:
        return base_config
    
    # Create a copy of base config
    merged = dict(base_config)
    
    # Enable UDV if we have orphan variables to add
    merged["enabled"] = True
    
    # Get existing variables (or empty dict)
    existing_vars = dict(merged.get("variables", {}))
    
    # Add orphan variables (don't override existing)
    for var_name, value in orphan_variables.items():
        if var_name not in existing_vars:
            existing_vars[var_name] = value
    
    merged["variables"] = existing_vars
    
    return merged


def _build_orphan_substitution_map(
    correlation_naming: Dict[str, Any],
    orphan_values: Dict[str, str]
) -> List[Dict]:
    """
    Build a list of orphan value substitutions.
    
    Creates substitution entries for orphan variables that should be replaced
    in HTTP requests. Handles context-aware substitution based on source_location.
    
    Args:
        correlation_naming: The loaded correlation_naming.json data
        orphan_values: Mapping from correlation_id to actual value
        
    Returns:
        List of substitution dictionaries with keys:
        - variable_name: JMeter variable name
        - value: The actual value to replace
        - source_key: The parameter/field name (for context-aware matching)
        - source_location: Where the value appears (request_query_param, request_url_path, etc.)
    """
    substitutions = []
    
    for orphan in correlation_naming.get("orphan_variables", []):
        corr_id = orphan.get("correlation_id", "")
        var_name = orphan.get("variable_name", "")
        
        if not var_name or not corr_id:
            continue
        
        # Get the actual value
        value = orphan_values.get(corr_id, "")
        if not value:
            continue
        
        # Skip very short numeric values (1, 2, 3, etc.) as they cause false positives
        # Unless they have a specific source_key context
        if value.isdigit() and len(value) <= 2:
            # These need context-aware matching only
            continue
        
        substitutions.append({
            "variable_name": var_name,
            "value": value,
            "correlation_id": corr_id
        })
    
    return substitutions


def _apply_orphan_substitutions_to_entry(
    entry: Dict,
    orphan_substitutions: List[Dict]
) -> bool:
    """
    Apply orphan variable substitutions to a network capture entry.
    
    Replaces hardcoded orphan values (like GUIDs, timestamps) with JMeter variables.
    
    Args:
        entry: The network capture entry (will be modified in place)
        orphan_substitutions: List of orphan substitution configs
        
    Returns:
        True if any substitution was applied, False otherwise
    """
    if not orphan_substitutions:
        return False
    
    url = entry.get("url", "")
    body = entry.get("post_data", "")
    modified = False
    
    for sub in orphan_substitutions:
        value = sub.get("value", "")
        var_name = sub.get("variable_name", "")
        
        if not value or not var_name:
            continue
        
        jmeter_var = f"${{{var_name}}}"
        
        # Substitute in URL (both encoded and raw)
        if url and value in url:
            entry["url"] = url.replace(value, jmeter_var)
            url = entry["url"]  # Update for next iteration
            modified = True
        
        # Also check URL-encoded version
        encoded_value = urllib.parse.quote(value, safe='')
        if url and encoded_value in url and encoded_value != value:
            entry["url"] = entry["url"].replace(encoded_value, jmeter_var)
            url = entry["url"]
            modified = True
        
        # Substitute in body
        if body and value in body:
            entry["post_data"] = body.replace(value, jmeter_var)
            body = entry["post_data"]
            modified = True
        
        # Also check URL-encoded in body (for form data)
        if body and encoded_value in body and encoded_value != value:
            entry["post_data"] = entry["post_data"].replace(encoded_value, jmeter_var)
            body = entry["post_data"]
            modified = True

        # Substitute in headers (e.g., orphan values in referer URLs)
        headers = entry.get("headers")
        if headers and isinstance(headers, dict):
            for hdr_name, hdr_value in headers.items():
                if not hdr_value or not isinstance(hdr_value, str):
                    continue
                if value in hdr_value:
                    headers[hdr_name] = hdr_value.replace(value, jmeter_var)
                    modified = True
                elif encoded_value in hdr_value and encoded_value != value:
                    headers[hdr_name] = hdr_value.replace(encoded_value, jmeter_var)
                    modified = True

    return modified
