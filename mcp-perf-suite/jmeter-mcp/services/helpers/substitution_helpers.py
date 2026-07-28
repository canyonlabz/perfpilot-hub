# substitution_helpers.py
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

from .extractor_helpers import _normalize_url  # Import the URL normalization function

# ============================================================
# Helper Functions - Variable Substitution (Phase C)
# ============================================================

def _build_variable_name_map(correlation_naming: Dict[str, Any]) -> Dict[str, str]:
    """
    Build a mapping from correlation_id to variable_name.
    
    Args:
        correlation_naming: The loaded correlation_naming.json data
        
    Returns:
        Dictionary mapping correlation_id to variable_name
    """
    var_map = {}
    for var in correlation_naming.get("variables", []):
        corr_id = var.get("correlation_id", "")
        var_name = var.get("variable_name", "")
        if corr_id and var_name:
            var_map[corr_id] = var_name
    return var_map


def _build_substitution_map(
    correlation_spec: Dict[str, Any],
    variable_name_map: Dict[str, str]
) -> Dict[str, List[Dict]]:
    """
    Build a mapping from request URL to list of substitutions.
    
    For each usage in correlation_spec, creates a substitution entry
    that includes the value to replace and the variable name to use.
    
    Args:
        correlation_spec: The loaded correlation_spec.json data
        variable_name_map: Mapping from correlation_id to variable_name
        
    Returns:
        Dictionary mapping normalized request URLs to lists of substitutions.
        Each substitution contains:
        - value: The actual value to replace
        - variable_name: The JMeter variable name (e.g., "product_id")
        - location_type: Where to substitute (request_url_path, request_query_param, etc.)
        - location_key: The parameter/field name (e.g., "id", "engagementId")
    """
    sub_map = {}
    
    for corr in correlation_spec.get("correlations", []):
        corr_id = corr.get("correlation_id", "")
        variable_name = variable_name_map.get(corr_id)
        
        if not variable_name:
            continue  # Skip if no variable name defined
        
        # Get the actual value from source
        source = corr.get("source", {})
        value = source.get("response_example_value", "")
        
        if not value:
            continue  # Skip if no value to substitute
        
        # Process each usage
        for usage in corr.get("usages", []):
            request_url = usage.get("request_url", "")
            if not request_url:
                continue
            
            # Normalize the URL for matching
            normalized_url = _normalize_url(request_url)
            
            substitution = {
                "value": str(value),
                "variable_name": variable_name,
                "location_type": usage.get("location_type", ""),
                "location_key": usage.get("location_key", ""),
                "location_json_path": usage.get("location_json_path"),
                "request_id": usage.get("request_id", ""),
                "entry_index": usage.get("entry_index"),
            }
            
            # Store by both exact URL and normalized URL
            if normalized_url not in sub_map:
                sub_map[normalized_url] = []
            sub_map[normalized_url].append(substitution)
            
            # Also store exact URL if different
            if request_url != normalized_url:
                if request_url not in sub_map:
                    sub_map[request_url] = []
                # Avoid duplicates
                if substitution not in sub_map[request_url]:
                    sub_map[request_url].append(substitution)
    
    return sub_map


def _find_substitutions_for_url(url: str, sub_map: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Find substitutions that should be applied to a given URL.
    
    Args:
        url: The URL of the current HTTP request entry
        sub_map: The pre-built substitution mapping
        
    Returns:
        List of substitution configurations for this URL
    """
    # Try exact match first
    if url in sub_map:
        return sub_map[url]
    
    # Try normalized URL match
    normalized = _normalize_url(url)
    if normalized in sub_map:
        return sub_map[normalized]
    
    return []


def _substitute_in_url(url: str, substitutions: List[Dict]) -> str:
    """
    Apply variable substitutions to a URL.
    
    Handles:
    - request_url_path: Replace value in URL path
    - request_query_param: Replace value in query parameters
    
    Args:
        url: The original URL
        substitutions: List of substitutions to apply
        
    Returns:
        The URL with hardcoded values replaced by ${variable_name}
    """
    result_url = url
    
    for sub in substitutions:
        value = sub.get("value", "")
        var_name = sub.get("variable_name", "")
        location_type = sub.get("location_type", "")
        
        if not value or not var_name:
            continue
        
        jmeter_var = f"${{{var_name}}}"
        
        if location_type in ("request_url_path", "request_query_param"):
            # URL-encoded version of the value
            encoded_value = urllib.parse.quote(value, safe='')
            
            # Replace both encoded and non-encoded versions
            result_url = result_url.replace(encoded_value, jmeter_var)
            result_url = result_url.replace(value, jmeter_var)
    
    return result_url


def _substitute_in_body(body: str, substitutions: List[Dict]) -> str:
    """
    Apply variable substitutions to a request body.
    
    Handles:
    - request_body_json: Replace value in JSON body
    - request_body_form: Replace value in form-encoded body
    
    Args:
        body: The original request body
        substitutions: List of substitutions to apply
        
    Returns:
        The body with hardcoded values replaced by ${variable_name}
    """
    if not body:
        return body
    
    result_body = body
    
    for sub in substitutions:
        value = sub.get("value", "")
        var_name = sub.get("variable_name", "")
        location_type = sub.get("location_type", "")
        
        if not value or not var_name:
            continue
        
        jmeter_var = f"${{{var_name}}}"
        
        if location_type in ("request_body_json", "request_body_form"):
            # For JSON bodies, handle both quoted and unquoted values
            # Numeric values in JSON are unquoted
            if value.isdigit():
                # Replace as number: "field": 123 -> "field": ${var}
                result_body = result_body.replace(f": {value}", f": {jmeter_var}")
                result_body = result_body.replace(f":{value}", f":{jmeter_var}")
            
            # Also replace as string: "field": "value" -> "field": "${var}"
            result_body = result_body.replace(f'"{value}"', f'"{jmeter_var}"')
            result_body = result_body.replace(f"'{value}'", f"'{jmeter_var}'")
            
            # Form-encoded: field=value -> field=${var}
            encoded_value = urllib.parse.quote(value, safe='')
            result_body = result_body.replace(f"={encoded_value}", f"={jmeter_var}")
            result_body = result_body.replace(f"={value}", f"={jmeter_var}")
    
    return result_body


def _substitute_in_headers(headers: Dict[str, str], substitutions: List[Dict]) -> Dict[str, str]:
    """
    Apply variable substitutions to request headers.
    
    Handles:
    - request_header: Replace value in header value
    
    Args:
        headers: The original headers dictionary
        substitutions: List of substitutions to apply
        
    Returns:
        The headers with hardcoded values replaced by ${variable_name}
    """
    if not headers:
        return headers
    
    result_headers = dict(headers)
    
    for sub in substitutions:
        value = sub.get("value", "")
        var_name = sub.get("variable_name", "")
        location_type = sub.get("location_type", "")
        location_key = sub.get("location_key", "")
        
        if not value or not var_name:
            continue
        
        if location_type != "request_header":
            continue
        
        jmeter_var = f"${{{var_name}}}"
        encoded_value = urllib.parse.quote(value, safe='')

        # If location_key specified, only substitute in that header
        if location_key and location_key in result_headers:
            hdr_val = result_headers[location_key]
            hdr_val = hdr_val.replace(value, jmeter_var)
            if encoded_value != value:
                hdr_val = hdr_val.replace(encoded_value, jmeter_var)
            result_headers[location_key] = hdr_val
        else:
            # Otherwise, substitute in all headers
            for key in result_headers:
                hdr_val = result_headers[key]
                hdr_val = hdr_val.replace(value, jmeter_var)
                if encoded_value != value:
                    hdr_val = hdr_val.replace(encoded_value, jmeter_var)
                result_headers[key] = hdr_val

    return result_headers


def _apply_substitutions_to_entry(entry: Dict, sub_map: Dict[str, List[Dict]]) -> Dict:
    """
    Apply all variable substitutions to a network capture entry.
    
    Modifies the entry's URL, body, and headers to replace hardcoded
    correlation values with JMeter variable references.
    
    Args:
        entry: The network capture entry (will be modified in place)
        sub_map: The pre-built substitution mapping
        
    Returns:
        The modified entry (also modified in place)
    """
    url = entry.get("url", "")
    if not url:
        return entry
    
    # Find substitutions for this URL
    substitutions = _find_substitutions_for_url(url, sub_map)
    
    if not substitutions:
        return entry
    
    # Apply substitutions to URL
    entry["url"] = _substitute_in_url(url, substitutions)
    
    # Apply substitutions to body
    if "post_data" in entry and entry["post_data"]:
        entry["post_data"] = _substitute_in_body(entry["post_data"], substitutions)
    
    # Apply substitutions to headers
    if "headers" in entry and entry["headers"]:
        entry["headers"] = _substitute_in_headers(entry["headers"], substitutions)
    
    return entry


# ============================================================
# Helper Functions - Static Header Substitution
# ============================================================

def _substitute_static_headers_in_entry(
    entry: Dict,
    static_header_map: Dict[str, str]
) -> bool:
    """
    Replace static header values with JMeter variable references.

    Performs case-insensitive matching on header names.  When a match is
    found the entire header value is replaced with ``${variable_name}``.

    Args:
        entry: The network capture entry (modified in place)
        static_header_map: Mapping from header_name_lower -> variable_name

    Returns:
        True if any substitution was applied
    """
    headers = entry.get("headers")
    if not headers or not static_header_map:
        return False

    modified = False
    for hdr_name in list(headers.keys()):
        var_name = static_header_map.get(hdr_name.lower())
        if var_name:
            jmeter_var = f"${{{var_name}}}"
            if headers[hdr_name] != jmeter_var:
                headers[hdr_name] = jmeter_var
                modified = True

    return modified


# ============================================================
# Helper Functions - PKCE Substitution (Sprint C-2)
# ============================================================

def _apply_pkce_substitutions_to_entry(
    entry: Dict,
    pkce_flow: Dict[str, Any]
) -> bool:
    """
    Replace hardcoded PKCE values with JMeter variable references.

    Substitutes the recorded code_challenge and code_verifier values with
    ${code_challenge} and ${code_verifier} so the PKCE PreProcessor generates
    fresh values on each iteration.

    Handles three locations where PKCE values appear:
    - Request URLs (code_challenge in authorize request, including nested goto= params)
    - POST bodies (code_verifier in token exchange)
    - Headers (code_challenge in referer headers carrying the authorize URL)

    Base64URL values are URL-safe (use - and _ instead of + and /), so the raw
    value appears literally even inside percent-encoded nested URL parameters.

    Args:
        entry: The network capture entry (modified in place)
        pkce_flow: Detection result from detect_pkce_flow()

    Returns:
        True if any substitution was applied
    """
    changed = False

    cc_val = pkce_flow.get("code_challenge_value", "")
    cv_val = pkce_flow.get("code_verifier_value", "")

    if cc_val:
        url = entry.get("url", "")
        if cc_val in url:
            entry["url"] = url.replace(cc_val, "${code_challenge}")
            changed = True

    if cv_val:
        body = entry.get("post_data", "")
        if body and cv_val in body:
            entry["post_data"] = body.replace(cv_val, "${code_verifier}")
            changed = True

    # Substitute in headers (referer headers carry the authorize URL
    # with code_challenge embedded in nested goto= query params)
    headers = entry.get("headers")
    if headers and (cc_val or cv_val):
        for hdr_name, hdr_value in headers.items():
            if not hdr_value:
                continue
            new_value = hdr_value
            if cc_val and cc_val in new_value:
                new_value = new_value.replace(cc_val, "${code_challenge}")
            if cv_val and cv_val in new_value:
                new_value = new_value.replace(cv_val, "${code_verifier}")
            if new_value != hdr_value:
                headers[hdr_name] = new_value
                changed = True

    return changed
