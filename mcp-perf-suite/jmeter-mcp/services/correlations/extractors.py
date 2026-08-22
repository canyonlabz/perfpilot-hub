"""
Phase 1: Source extraction from response data.

Extracts candidate values from:
- Response headers (correlation IDs)
- Redirect URLs (Location header params)
- JSON response bodies (ID-like fields)
- Set-Cookie headers (edge cases - stub)
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .classifiers import classify_value_type, is_id_like_value
from .constants import (
    API_KEY_HEADER_RE,
    CORRELATION_HEADER_SUFFIXES,
    ENTRA_CONFIG_FIELDS,
    ENTRA_CREDENTIAL_TYPE_FIELDS,
    NONCE_COOKIE_KEYWORDS,
    OAUTH_BODY_PARAM_VALUE_TYPES,
    OAUTH_BODY_PARAMS,
    OAUTH_GRANT_TYPES,
    OAUTH_INTEREST_HEADERS,
    OAUTH_INTEREST_HEADER_VALUE_TYPES,
    OAUTH_NESTED_URL_PARAMS,
    OAUTH_PARAM_VALUE_TYPES,
    OAUTH_PARAMS,
    OAUTH_TOKEN_FIELDS,
    OAUTH_URL_PARAMS,
    PKCE_PARAMS,
    SAML_PARAMS,
    SAML_PARAM_VALUE_TYPES,
    OPENAM_TOKEN_FIELDS,
    SKIP_HEADERS_SOURCE,
    WSFED_FORM_FIELDS,
)
from .utils import walk_json


def extract_from_response_headers(
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Extract correlation IDs from response headers."""
    candidates = []
    
    for raw_key, value in (response_headers or {}).items():
        if raw_key is None or value is None:
            continue
        key = str(raw_key).lower()
        
        # Skip known non-ID headers
        if key in SKIP_HEADERS_SOURCE:
            continue
        
        # Check if header name suggests a correlation ID
        if any(key.endswith(suffix) for suffix in CORRELATION_HEADER_SUFFIXES):
            value_str = str(value)
            if value_str and len(value_str) > 0:
                candidates.append({
                    "entry_index": entry_index,
                    "step_number": step_number,
                    "step_label": step_label,
                    "request_id": entry.get("request_id"),
                    "request_method": entry.get("method", "GET"),
                    "request_url": entry.get("url", ""),
                    "response_status": entry.get("status"),
                    "source_location": "response_header",
                    "source_key": raw_key,
                    "source_json_path": None,
                    "value": value_str,
                    "value_type": classify_value_type(value_str),
                    "candidate_type": "correlation_id",
                })
    
    return candidates


def extract_from_redirect_url(
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Extract values from Location header redirect URL."""
    candidates = []
    location = response_headers.get("location") if response_headers else None
    
    if not location:
        return candidates
    
    try:
        parsed = urlparse(location)
        query_params = parse_qs(parsed.query)
        
        for param_name, values in query_params.items():
            param_lower = param_name.lower()
            for val in values:
                is_oauth = param_lower in OAUTH_PARAMS
                is_saml = param_lower in SAML_PARAMS

                if is_id_like_value(val) or is_oauth or is_saml:
                    if is_saml:
                        value_type = SAML_PARAM_VALUE_TYPES.get(
                            param_lower, "saml_param"
                        )
                    elif is_oauth:
                        value_type = OAUTH_PARAM_VALUE_TYPES.get(
                            param_lower, "oauth_param"
                        )
                    else:
                        value_type = classify_value_type(val)

                    if is_saml:
                        candidate_type = "saml_param"
                    elif is_oauth:
                        candidate_type = "oauth_param"
                    else:
                        candidate_type = "business_id"

                    candidates.append({
                        "entry_index": entry_index,
                        "step_number": step_number,
                        "step_label": step_label,
                        "request_id": entry.get("request_id"),
                        "request_method": entry.get("method", "GET"),
                        "request_url": entry.get("url", ""),
                        "response_status": entry.get("status"),
                        "source_location": "response_redirect_url",
                        "source_key": param_name,
                        "source_json_path": None,
                        "value": val,
                        "value_type": value_type,
                        "candidate_type": candidate_type,
                    })
    except Exception:
        pass  # Skip malformed URLs
    
    return candidates


def extract_from_json_body(
    response: str,
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Extract ID-like values and OAuth tokens from JSON response body."""
    candidates = []
    
    # Check content-type
    content_type = (response_headers or {}).get("content-type", "")
    if "json" not in content_type.lower():
        return candidates
    
    if not response or not response.strip():
        return candidates
    
    try:
        json_data = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        return candidates
    
    # Build combined set of token field names for top-level scan (lowercase)
    # Includes OAuth tokens (access_token, id_token, etc.) and
    # OpenAM fields (authId, nonce, cdssoToken) for /json/authenticate responses
    oauth_token_fields_lower = {f.lower() for f in OAUTH_TOKEN_FIELDS}
    openam_fields_lower = {f.lower() for f in OPENAM_TOKEN_FIELDS}
    all_token_fields_lower = oauth_token_fields_lower | openam_fields_lower
    
    # First, explicitly extract token fields from top-level JSON
    # (These may not match ID_KEY_PATTERNS used by walk_json)
    if isinstance(json_data, dict):
        for key_name, value in json_data.items():
            if key_name.lower() in all_token_fields_lower:
                value_str = str(value) if value is not None else ""
                if value_str:
                    suggested_var = _get_oauth_token_var_name(key_name)
                    candidates.append({
                        "entry_index": entry_index,
                        "step_number": step_number,
                        "step_label": step_label,
                        "request_id": entry.get("request_id"),
                        "request_method": entry.get("method", "GET"),
                        "request_url": entry.get("url", ""),
                        "response_status": entry.get("status"),
                        "source_location": "response_json",
                        "source_key": key_name,
                        "source_json_path": f"$.{key_name}",
                        "value": value_str,
                        "value_type": "oauth_token",
                        "candidate_type": "oauth_param",
                        "suggested_var_name": suggested_var,
                    })
    
    # Then walk JSON for ID-like and token values at nested depth
    for json_path, value, key_name in walk_json(json_data):
        # Skip if already added as a top-level token field
        if key_name.lower() in all_token_fields_lower:
            continue

        if value is None:
            continue

        value_str = str(value) if value is not None else ""
        
        if is_id_like_value(value):
            candidates.append({
                "entry_index": entry_index,
                "step_number": step_number,
                "step_label": step_label,
                "request_id": entry.get("request_id"),
                "request_method": entry.get("method", "GET"),
                "request_url": entry.get("url", ""),
                "response_status": entry.get("status"),
                "source_location": "response_json",
                "source_key": key_name,
                "source_json_path": json_path,
                "value": value_str,
                "value_type": classify_value_type(value),
                "candidate_type": "business_id",
            })
    
    return candidates


def _get_oauth_token_var_name(field_name: str) -> str:
    """Map OAuth token field names to suggested JMeter variable names."""
    field_lower = field_name.lower()
    
    # Map common OAuth token field names
    mapping = {
        "cdssotoken": "cdssotoken",
        "tokenid": "token_id",
        "access_token": "access_token",
        "accesstoken": "access_token",
        "id_token": "bearer_token",  # id_token is used as Bearer
        "idtoken": "bearer_token",
        "refresh_token": "refresh_token",
        "refreshtoken": "refresh_token",
    }
    
    return mapping.get(field_lower, field_lower)


def extract_from_html_form_post(
    response: str,
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Extract OAuth tokens and WS-Federation values from HTML form_post responses.
    
    OAuth 2.0 form_post response mode returns tokens in an HTML page with
    hidden form fields that auto-submit:
    
    <form method="post" action="...">
        <input type="hidden" name="id_token" value="eyJ0eXAi..."/>
        <input type="hidden" name="code" value="..."/>
        <input type="hidden" name="state" value="..."/>
    </form>
    
    Also handles WS-Federation form posts with fields like wresult, wctx,
    and parses form action URLs for federation tenant GUIDs (e.g.,
    action="https://login.microsoftonline.com/GUID/wsfed").
    
    Returns:
        List of candidate values for correlation.
    """
    import re
    from html import unescape
    candidates = []
    
    # Check content-type is HTML
    content_type = (response_headers or {}).get("content-type", "")
    if "html" not in content_type.lower():
        return candidates
    
    if not response or not response.strip():
        return candidates
    
    # Pattern to extract hidden input values: <input type="hidden" name="NAME" value="VALUE"/>
    # Handle various HTML quote styles and attribute orders
    hidden_input_pattern = re.compile(
        r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\'][^>]*/?>',
        re.IGNORECASE
    )
    
    # Also handle reversed attribute order (name before type)
    hidden_input_pattern_alt = re.compile(
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*type=["\']hidden["\'][^>]*value=["\']([^"\']*)["\'][^>]*/?>',
        re.IGNORECASE
    )
    
    # Combined fields: OAuth + WS-Fed
    oauth_form_fields = {"id_token", "code", "state", "access_token", "token_type"}
    wsfed_form_fields_lower = {f.lower() for f in WSFED_FORM_FIELDS}
    all_form_fields = oauth_form_fields | wsfed_form_fields_lower
    
    for pattern in [hidden_input_pattern, hidden_input_pattern_alt]:
        for match in pattern.finditer(response):
            field_name = match.group(1)
            field_value = match.group(2)
            
            if field_name.lower() in all_form_fields and field_value:
                is_wsfed = field_name.lower() in wsfed_form_fields_lower
                is_token = field_name.lower() in {"id_token", "access_token"}

                var_name_map = {
                    "id_token": "bearer_token",
                    "code": "oauth_code",
                    "state": "oauth_state",
                    "access_token": "access_token",
                    "token_type": "token_type",
                    "wresult": "wresult",
                    "wctx": "wctx",
                    "wa": "wa",
                    "wtrealm": "wtrealm",
                }
                suggested_var = var_name_map.get(field_name.lower(), field_name.lower())

                source_loc = "response_wsfed_form" if is_wsfed else "response_html_form"

                if is_wsfed:
                    value_type = "wsfed_param"
                elif is_token:
                    value_type = "oauth_token"
                else:
                    value_type = "oauth_param"

                candidates.append({
                    "entry_index": entry_index,
                    "step_number": step_number,
                    "step_label": step_label,
                    "request_id": entry.get("request_id"),
                    "request_method": entry.get("method", "GET"),
                    "request_url": entry.get("url", ""),
                    "response_status": entry.get("status"),
                    "source_location": source_loc,
                    "source_key": field_name,
                    "source_json_path": None,
                    "value": field_value,
                    "value_type": value_type,
                    "candidate_type": "wsfed_param" if is_wsfed else "oauth_param",
                    "suggested_var_name": suggested_var,
                })

    # Parse form action URLs for WS-Fed federation tenant GUID.
    # Pattern: action="https://login.microsoftonline.com/TENANT_GUID/wsfed"
    # The URL may be HTML-entity-encoded (e.g., &#x2f; instead of /)
    form_action_pattern = re.compile(
        r'<form[^>]*action=["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    for match in form_action_pattern.finditer(response):
        raw_action = match.group(1)
        decoded_action = unescape(raw_action)

        wsfed_guid_pattern = re.compile(
            r'/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
            r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/wsfed',
            re.IGNORECASE
        )
        guid_match = wsfed_guid_pattern.search(decoded_action)
        if guid_match:
            tenant_guid = guid_match.group(1)
            candidates.append({
                "entry_index": entry_index,
                "step_number": step_number,
                "step_label": step_label,
                "request_id": entry.get("request_id"),
                "request_method": entry.get("method", "GET"),
                "request_url": entry.get("url", ""),
                "response_status": entry.get("status"),
                "source_location": "response_wsfed_form",
                "source_key": "wsfed_tenant_guid",
                "source_json_path": None,
                "value": tenant_guid,
                "value_type": "wsfed_tenant_guid",
                "candidate_type": "wsfed_param",
                "suggested_var_name": "wsfed_tenant_guid",
            })

    return candidates


def extract_from_entra_config(
    response: str,
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Extract dynamic values from EntraID $Config JavaScript objects.

    Microsoft Entra ID (login.microsoftonline.com) embeds a $Config object
    in HTML responses containing tokens needed for subsequent requests:

        <script>$Config={sFT:"flowToken...",sCtx:"context...",canary:"...",...};</script>

    Also handles GetCredentialType JSON responses which contain fields like
    FederationRedirectUrl that may embed URL-encoded params (e.g. estsrequest).

    Returns candidates with source_location="response_entra_config".
    """
    import re
    candidates = []

    if not response or not response.strip():
        return candidates

    request_url = entry.get("url", "")
    content_type = (response_headers or {}).get("content-type", "")

    # Determine which fields to look for based on content type
    all_fields = ENTRA_CONFIG_FIELDS | ENTRA_CREDENTIAL_TYPE_FIELDS

    is_html = "html" in content_type.lower()
    is_json = "json" in content_type.lower()

    # For HTML responses: look for $Config JS object fields via regex
    # For JSON responses: try JSON parsing first (GetCredentialType API)
    if is_json:
        try:
            json_data = json.loads(response)
            if isinstance(json_data, dict):
                candidates.extend(
                    _extract_entra_fields_from_dict(
                        json_data, all_fields, entry_index,
                        step_number, step_label, entry
                    )
                )
        except (json.JSONDecodeError, TypeError):
            pass
    elif is_html:
        # Check for $Config object in HTML
        if "$Config=" not in response and "$Config =" not in response:
            return candidates

        # Extract field values using regex (JS object may not be valid JSON)
        for field_name in all_fields:
            pattern = re.compile(
                r'["\']?' + re.escape(field_name) + r'["\']?\s*[:=]\s*["\']([^"\']*?)["\']',
                re.DOTALL
            )
            match = pattern.search(response)
            if match:
                value = match.group(1).strip()
                if not value:
                    continue

                candidates.append({
                    "entry_index": entry_index,
                    "step_number": step_number,
                    "step_label": step_label,
                    "request_id": entry.get("request_id"),
                    "request_method": entry.get("method", "GET"),
                    "request_url": request_url,
                    "response_status": entry.get("status"),
                    "source_location": "response_entra_config",
                    "source_key": field_name,
                    "source_json_path": None,
                    "value": value,
                    "value_type": "entra_config",
                    "candidate_type": "oauth_param",
                })

                # If FederationRedirectUrl, parse the URL for embedded params
                if field_name == "FederationRedirectUrl":
                    candidates.extend(
                        _extract_params_from_federation_url(
                            value, entry_index, step_number,
                            step_label, entry
                        )
                    )
    else:
        # Non-HTML, non-JSON: still check for $Config in case content-type
        # is missing or generic (some responses lack proper content-type)
        if "$Config=" in response or "$Config =" in response:
            for field_name in all_fields:
                pattern = re.compile(
                    r'["\']?' + re.escape(field_name) + r'["\']?\s*[:=]\s*["\']([^"\']*?)["\']',
                    re.DOTALL
                )
                match = pattern.search(response)
                if match:
                    value = match.group(1).strip()
                    if not value:
                        continue

                    candidates.append({
                        "entry_index": entry_index,
                        "step_number": step_number,
                        "step_label": step_label,
                        "request_id": entry.get("request_id"),
                        "request_method": entry.get("method", "GET"),
                        "request_url": request_url,
                        "response_status": entry.get("status"),
                        "source_location": "response_entra_config",
                        "source_key": field_name,
                        "source_json_path": None,
                        "value": value,
                        "value_type": "entra_config",
                        "candidate_type": "oauth_param",
                    })

                    if field_name == "FederationRedirectUrl":
                        candidates.extend(
                            _extract_params_from_federation_url(
                                value, entry_index, step_number,
                                step_label, entry
                            )
                        )

    return candidates


def _extract_entra_fields_from_dict(
    data: Dict[str, Any],
    fields: set,
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Extract known EntraID fields from a parsed JSON dict (GetCredentialType response)."""
    candidates = []

    for key, value in data.items():
        if key not in fields:
            continue

        value_str = str(value) if value is not None else ""
        if not value_str:
            continue

        candidates.append({
            "entry_index": entry_index,
            "step_number": step_number,
            "step_label": step_label,
            "request_id": entry.get("request_id"),
            "request_method": entry.get("method", "GET"),
            "request_url": entry.get("url", ""),
            "response_status": entry.get("status"),
            "source_location": "response_entra_config",
            "source_key": key,
            "source_json_path": f"$.{key}",
            "value": value_str,
            "value_type": "entra_config",
            "candidate_type": "oauth_param",
        })

        if key == "FederationRedirectUrl":
            candidates.extend(
                _extract_params_from_federation_url(
                    value_str, entry_index, step_number,
                    step_label, entry
                )
            )

    return candidates


def _extract_params_from_federation_url(
    url_value: str,
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Parse a FederationRedirectUrl for embedded URL-encoded parameters.

    The URL may be URL-encoded itself and can contain params like:
        estsrequest=<base64blob>&...
    These need separate correlation entries.
    """
    candidates = []

    try:
        decoded_url = unquote(url_value)
        parsed = urlparse(decoded_url)
        query_params = parse_qs(parsed.query, keep_blank_values=False)

        for param_name, values in query_params.items():
            for val in values:
                if not val or not val.strip():
                    continue

                candidates.append({
                    "entry_index": entry_index,
                    "step_number": step_number,
                    "step_label": step_label,
                    "request_id": entry.get("request_id"),
                    "request_method": entry.get("method", "GET"),
                    "request_url": entry.get("url", ""),
                    "response_status": entry.get("status"),
                    "source_location": "response_entra_config",
                    "source_key": param_name,
                    "source_json_path": None,
                    "value": val,
                    "value_type": "entra_federation_param",
                    "candidate_type": "oauth_param",
                })
    except Exception:
        pass

    return candidates


def extract_from_body_redirect_urls(
    response: str,
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Extract OAuth parameters from redirect URLs embedded in response bodies.

    The standard extract_from_redirect_url() only checks the Location header.
    However, some flows embed redirect URLs in the body:
    - <a href="https://...?code=ABC123&state=XYZ">
    - <meta http-equiv="refresh" content="0;url=https://...?code=ABC123">
    - window.location = "https://...?code=ABC123"
    - Kerberos SSO interrupt pages with href links containing auth codes

    Returns candidates with source_location="response_body_url".
    """
    import re
    candidates = []

    if not response or not response.strip():
        return candidates

    # Patterns that capture URLs from common body redirect mechanisms
    url_patterns = [
        re.compile(r'href=["\']([^"\']*\?[^"\']*)["\']', re.IGNORECASE),
        re.compile(r'content=["\'][^"\']*url=([^"\';\s]+)', re.IGNORECASE),
        re.compile(r'(?:window\.location|location\.href)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE),
    ]

    seen_values: Dict[str, bool] = {}

    for pattern in url_patterns:
        for match in pattern.finditer(response):
            url = match.group(1)
            if not url:
                continue

            try:
                decoded_url = unquote(url)
                parsed = urlparse(decoded_url)

                # Also check fragment for response_mode=fragment flows
                for qs in [parsed.query, parsed.fragment]:
                    if not qs:
                        continue

                    query_params = parse_qs(qs, keep_blank_values=False)
                    for param_name, values in query_params.items():
                        param_lower = param_name.lower()

                        if param_lower not in OAUTH_PARAMS and param_lower not in SAML_PARAMS:
                            continue

                        for val in values:
                            if not val or not val.strip():
                                continue

                            dedup_key = f"{param_lower}={val}"
                            if dedup_key in seen_values:
                                continue
                            seen_values[dedup_key] = True

                            if param_lower in SAML_PARAMS:
                                value_type = SAML_PARAM_VALUE_TYPES.get(
                                    param_lower, "saml_param"
                                )
                                candidate_type = "saml_param"
                            else:
                                value_type = OAUTH_PARAM_VALUE_TYPES.get(
                                    param_lower, "oauth_param"
                                )
                                candidate_type = "oauth_param"

                            candidates.append({
                                "entry_index": entry_index,
                                "step_number": step_number,
                                "step_label": step_label,
                                "request_id": entry.get("request_id"),
                                "request_method": entry.get("method", "GET"),
                                "request_url": entry.get("url", ""),
                                "response_status": entry.get("status"),
                                "source_location": "response_body_url",
                                "source_key": param_name,
                                "source_json_path": None,
                                "value": val,
                                "value_type": value_type,
                                "candidate_type": candidate_type,
                            })
            except Exception:
                continue

    return candidates


def extract_from_set_cookie(
    response_headers: Dict[str, Any],
    entry_index: int,
    step_number: int,
    step_label: str,
    entry: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Extract nonce values from Set-Cookie response headers.
    
    JMeter's Cookie Manager handles most cookies automatically, but some
    OAuth/SSO nonce values in cookies are used in subsequent request headers
    (like x-cdsso-nonce) and need to be correlated.
    
    Uses generic detection based on cookie names containing keywords like
    "nonce" or "csrf" (case-insensitive). Does NOT use company-specific patterns.
    
    Returns:
        List of candidate values for correlation.
    """
    import re
    candidates = []
    set_cookie = response_headers.get("set-cookie") if response_headers else None
    
    if not set_cookie:
        return candidates
    
    # Handle both string and list of cookies
    cookies = [set_cookie] if isinstance(set_cookie, str) else set_cookie
    
    for cookie_str in cookies:
        if not isinstance(cookie_str, str):
            continue
        
        # Parse cookie: NAME=VALUE; attribute; attribute...
        # Extract the name=value part (before first ;)
        cookie_parts = cookie_str.split(";")
        if not cookie_parts:
            continue
            
        name_value_part = cookie_parts[0].strip()
        if "=" not in name_value_part:
            continue
            
        cookie_name, cookie_value = name_value_part.split("=", 1)
        cookie_name = cookie_name.strip()
        cookie_value = cookie_value.strip()
        
        if not cookie_name or not cookie_value:
            continue
        
        # Check if cookie name contains any nonce keywords (generic detection)
        cookie_name_lower = cookie_name.lower()
        is_nonce_cookie = any(keyword in cookie_name_lower for keyword in NONCE_COOKIE_KEYWORDS)
        
        if is_nonce_cookie:
            # Generate a suggested variable name from the cookie name
            # Convert CamelCase/PascalCase to snake_case, remove company-specific prefixes
            suggested_var = _sanitize_cookie_name_to_var(cookie_name)
            
            candidates.append({
                "entry_index": entry_index,
                "step_number": step_number,
                "step_label": step_label,
                "request_id": entry.get("request_id"),
                "request_method": entry.get("method", "GET"),
                "request_url": entry.get("url", ""),
                "response_status": entry.get("status"),
                "source_location": "response_set_cookie",
                "source_key": cookie_name,
                "source_json_path": None,
                "value": cookie_value,
                "value_type": "oauth_nonce",
                "candidate_type": "oauth_param",
                "suggested_var_name": suggested_var,
            })
    
    return candidates


def _sanitize_cookie_name_to_var(cookie_name: str) -> str:
    """
    Convert a cookie name to a sanitized JMeter variable name.
    
    Examples:
    - "GlobalNonce" -> "global_nonce"
    - "SomeCompanyNonce" -> "nonce" 
    - "csrf_token" -> "csrf_token"
    - "NonceValue" -> "nonce_value"
    """
    import re
    
    # If it contains "nonce" (case-insensitive), extract just the relevant part
    name_lower = cookie_name.lower()
    
    if "nonce" in name_lower:
        # Simple: just use "nonce" or add context
        # Find where "nonce" appears and take surrounding context
        idx = name_lower.find("nonce")
        # Take from "nonce" onwards, remove environment suffixes
        suffix_patterns = ["_stg", "_stage", "_prod", "_dev", "_uat", "_qa"]
        result = cookie_name[idx:].lower()
        for suffix in suffix_patterns:
            if result.endswith(suffix):
                result = result[:-len(suffix)]
        # Convert to snake_case if needed
        result = re.sub(r'([a-z])([A-Z])', r'\1_\2', result).lower()
        return result if result else "nonce"
    
    if "csrf" in name_lower:
        return "csrf_token"
    
    # Fallback: convert the whole name to snake_case
    result = re.sub(r'([a-z])([A-Z])', r'\1_\2', cookie_name).lower()
    # Remove environment suffixes
    suffix_patterns = ["_stg", "_stage", "_prod", "_dev", "_uat", "_qa"]
    for suffix in suffix_patterns:
        if result.endswith(suffix):
            result = result[:-len(suffix)]
    return result


def extract_oauth_params_from_request_urls(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Scan request URLs for OAuth/PKCE parameters, including nested URL-encoded
    query strings (e.g., goto=, redirect_uri=).

    This is a request-first detection pass that identifies OAuth parameters by
    their parameter names in request URLs, regardless of whether a source
    response was captured. Complements the existing response-first pipeline.

    Handles:
    - Direct OAuth params in request URL query strings
      (e.g., /oauth2/authorize?client_id=X&state=Y&code_challenge=Z)
    - Nested URL-encoded params inside goto=, redirect_uri= values
      (e.g., /login/?goto=https%3A...%3Fclient_id%3DX%26state%3DY)
    - Improperly encoded nested URLs where params leak to top-level
      (e.g., /authenticate?goto=https://...?client_id=X&state=Y)

    Returns candidates with source_location="request_url" and candidate_type
    of "oauth_param" or "pkce_param".
    """
    candidates: List[Dict[str, Any]] = []
    seen_values: Dict[str, bool] = {}

    for entry_index, step_number, step_label, entry in entries:
        url = entry.get("url", "")
        if not url:
            continue

        found_params = _extract_oauth_from_url(url)

        for param_name, param_value in found_params:
            if not param_value or not param_value.strip():
                continue

            # Dedup by value — keep first occurrence only
            if param_value in seen_values:
                continue
            seen_values[param_value] = True

            param_lower = param_name.lower()
            value_type = OAUTH_PARAM_VALUE_TYPES.get(param_lower, "oauth_param")
            candidate_type = "pkce_param" if param_lower in PKCE_PARAMS else "oauth_param"

            candidates.append({
                "entry_index": entry_index,
                "step_number": step_number,
                "step_label": step_label,
                "request_id": entry.get("request_id"),
                "request_method": entry.get("method", "GET"),
                "request_url": url,
                "response_status": entry.get("status"),
                "source_location": "request_url",
                "source_key": param_name,
                "source_json_path": None,
                "value": param_value,
                "value_type": value_type,
                "candidate_type": candidate_type,
            })

    return candidates


def _extract_oauth_from_url(
    url: str,
    max_depth: int = 3
) -> List[Tuple[str, str]]:
    """
    Parse a URL and extract OAuth parameter (name, value) pairs from its
    query string. Recursively URL-decodes nested URL values (goto=,
    redirect_uri=, etc.) up to max_depth levels.

    Python's parse_qs automatically URL-decodes values one level, so
    recursion handles multi-level encoding (common in SSO redirect chains).

    Returns:
        List of (param_name, param_value) tuples for OAuth-related params.
    """
    if max_depth <= 0 or not url:
        return []

    results: List[Tuple[str, str]] = []

    try:
        parsed = urlparse(url)
        if not parsed.query:
            return results
        # parse_qs automatically URL-decodes one level
        query_params = parse_qs(parsed.query, keep_blank_values=False)
    except Exception:
        return results

    for param_name, values in query_params.items():
        param_lower = param_name.lower()

        for val in values:
            if param_lower in OAUTH_URL_PARAMS or param_lower in SAML_PARAMS:
                results.append((param_name, val))

            # Recursively parse nested URLs (goto=, redirect_uri=, etc.)
            if param_lower in OAUTH_NESTED_URL_PARAMS and val:
                if val.startswith("http") or "?" in val:
                    results.extend(_extract_oauth_from_url(val, max_depth - 1))

    return results


def extract_oauth_params_from_request_body(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Parse form-urlencoded POST request bodies for OAuth token exchange parameters.

    Detects token endpoint requests by looking for grant_type in the POST body,
    then extracts dynamic values that need correlation:
    - grant_type=authorization_code → code, code_verifier (PKCE), client_id, redirect_uri
    - grant_type=token-exchange → subject_token (JWT from prior response)
    - grant_type=refresh_token → refresh_token

    The grant_type itself is NOT emitted as a candidate (it's static metadata),
    but it IS attached to each candidate as detected_grant_type for downstream
    flow classification (Sprint C/D).

    Skips JSON POST bodies (those starting with '{' or '[') and empty bodies.

    Returns candidates with source_location="request_body" and appropriate
    candidate_type ("oauth_param" or "pkce_param").
    """
    candidates: List[Dict[str, Any]] = []
    seen_values: Dict[str, bool] = {}

    for entry_index, step_number, step_label, entry in entries:
        post_data = entry.get("post_data") or ""
        if not post_data or not post_data.strip():
            continue

        stripped = post_data.strip()

        # Skip JSON bodies — only parse form-urlencoded
        if stripped.startswith("{") or stripped.startswith("["):
            continue

        try:
            body_params = parse_qs(stripped, keep_blank_values=False)
        except Exception:
            continue

        # Must contain grant_type to be an OAuth token endpoint request
        grant_types = body_params.get("grant_type", [])
        if not grant_types:
            continue

        grant_type_raw = grant_types[0]
        detected_flow = OAUTH_GRANT_TYPES.get(grant_type_raw, "unknown")

        url = entry.get("url", "")

        for param_name, values in body_params.items():
            param_lower = param_name.lower()

            # Skip grant_type itself (static) and non-OAuth params
            if param_lower == "grant_type":
                continue
            if param_lower not in OAUTH_BODY_PARAMS:
                continue

            for val in values:
                if not val or not val.strip():
                    continue

                if val in seen_values:
                    continue
                seen_values[val] = True

                value_type = OAUTH_BODY_PARAM_VALUE_TYPES.get(
                    param_lower, "oauth_param"
                )
                candidate_type = (
                    "pkce_param" if param_lower in PKCE_PARAMS else "oauth_param"
                )

                candidates.append({
                    "entry_index": entry_index,
                    "step_number": step_number,
                    "step_label": step_label,
                    "request_id": entry.get("request_id"),
                    "request_method": entry.get("method", "POST"),
                    "request_url": url,
                    "response_status": entry.get("status"),
                    "source_location": "request_body",
                    "source_key": param_name,
                    "source_json_path": None,
                    "value": val,
                    "value_type": value_type,
                    "candidate_type": candidate_type,
                    "detected_grant_type": grant_type_raw,
                    "detected_flow": detected_flow,
                })

    return candidates


def extract_oauth_from_request_headers(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Extract dynamic OAuth/SSO values from request headers.

    Scans request headers for names listed in OAUTH_INTEREST_HEADERS (e.g.,
    x-cdsso-nonce, x-csrf-token). These carry nonces and tokens that need
    correlation but whose response-side source may be missing from the capture.

    Returns a list of candidate dicts with source_location="request_header".
    Deduplicates by value — keeps the first occurrence.
    """
    candidates: List[Dict[str, Any]] = []
    seen_values: Dict[str, bool] = {}

    for entry_index, step_number, step_label, entry in entries:
        headers = entry.get("headers") or {}
        url = entry.get("url", "")

        for header_name, header_value in headers.items():
            if header_value is None:
                continue

            name_lower = header_name.lower()
            if name_lower not in OAUTH_INTEREST_HEADERS:
                continue

            val = str(header_value).strip()
            if not val:
                continue

            if val in seen_values:
                continue
            seen_values[val] = True

            value_type = OAUTH_INTEREST_HEADER_VALUE_TYPES.get(
                name_lower, "oauth_header_value"
            )

            candidates.append({
                "entry_index": entry_index,
                "step_number": step_number,
                "step_label": step_label,
                "request_id": entry.get("request_id"),
                "request_method": entry.get("method", "GET"),
                "request_url": url,
                "response_status": entry.get("status"),
                "source_location": "request_header",
                "source_key": header_name,
                "source_json_path": None,
                "value": val,
                "value_type": value_type,
                "candidate_type": "oauth_param",
            })

    return candidates


def detect_pkce_flow(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    """
    Detect whether the captured traffic contains a PKCE flow.

    Scans request URLs (including nested URL-encoded params like goto=) for
    code_challenge / code_challenge_method, and request POST bodies for
    code_verifier.

    Returns None if no PKCE indicators are found, otherwise a dict:
        {
            "detected": True,
            "code_challenge_method": "S256" | "plain" | None,
            "code_challenge_value": str,       # recorded value for substitution
            "code_verifier_value": str | None, # recorded value (from POST body)
            "authorize_entry_index": int,      # first entry with code_challenge
            "authorize_request_url": str,
            "token_entry_index": int | None,   # entry that POSTs code_verifier
            "token_request_url": str | None,
        }
    """
    challenge_info: Optional[Dict[str, Any]] = None
    verifier_info: Optional[Dict[str, Any]] = None

    for entry_index, step_number, step_label, entry in entries:
        url = entry.get("url", "")
        post_data = entry.get("post_data") or ""

        # --- Scan URL (and nested URLs) for code_challenge ---
        if challenge_info is None:
            url_params = _extract_oauth_from_url(url)
            param_map = {name.lower(): val for name, val in url_params}

            if "code_challenge" in param_map:
                challenge_info = {
                    "entry_index": entry_index,
                    "request_url": url,
                    "code_challenge_value": param_map["code_challenge"],
                    "code_challenge_method": param_map.get(
                        "code_challenge_method", "S256"
                    ),
                }

        # --- Scan POST body for code_verifier ---
        if verifier_info is None and post_data and "code_verifier" in post_data:
            try:
                body_params = parse_qs(post_data, keep_blank_values=False)
                cv_list = body_params.get("code_verifier")
                if cv_list:
                    verifier_info = {
                        "entry_index": entry_index,
                        "request_url": url,
                        "code_verifier_value": cv_list[0],
                    }
            except Exception:
                pass

        if challenge_info and verifier_info:
            break

    if not challenge_info:
        return None

    return {
        "detected": True,
        "code_challenge_method": challenge_info["code_challenge_method"],
        "code_challenge_value": challenge_info["code_challenge_value"],
        "code_verifier_value": (
            verifier_info["code_verifier_value"] if verifier_info else None
        ),
        "authorize_entry_index": challenge_info["entry_index"],
        "authorize_request_url": challenge_info["request_url"],
        "token_entry_index": (
            verifier_info["entry_index"] if verifier_info else None
        ),
        "token_request_url": (
            verifier_info["request_url"] if verifier_info else None
        ),
    }


def detect_entra_flow(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    """
    Detect whether the captured traffic contains a Microsoft Entra ID flow.

    Scans entries for EntraID indicators:
    - Host login.microsoftonline.com
    - $Config JavaScript objects in HTML responses
    - WS-Federation form posts (hidden input wresult)
    - OAuth authorize URLs on login.microsoftonline.com

    Returns None if no EntraID indicators are found, otherwise a dict:
        {
            "detected": True,
            "microsoft_host": str,
            "authorize_entry_index": int,
            "authorize_request_url": str,
            "wsfed_entry_index": int | None,
            "wsfed_request_url": str | None,
        }
    """
    from urllib.parse import urlparse

    authorize_info: Optional[Dict[str, Any]] = None
    wsfed_info: Optional[Dict[str, Any]] = None
    microsoft_host: Optional[str] = None

    for entry_index, step_number, step_label, entry in entries:
        url = entry.get("url", "")
        response = entry.get("response", "")

        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except Exception:
            host = ""

        # Detect Microsoft host
        if not microsoft_host and "login.microsoftonline.com" in host:
            microsoft_host = host

        # Detect authorize request (first occurrence)
        if authorize_info is None and microsoft_host:
            if "/oauth2/v2.0/authorize" in url or "/oauth2/authorize" in url:
                authorize_info = {
                    "entry_index": entry_index,
                    "request_url": url,
                }

        # Detect WS-Fed form post step (response containing wresult hidden input)
        if wsfed_info is None and response:
            if 'name="wresult"' in response or "name='wresult'" in response:
                wsfed_info = {
                    "entry_index": entry_index,
                    "request_url": url,
                }

        # Detect $Config in response (alternative authorize detection)
        if authorize_info is None and response:
            if "$Config=" in response or "$Config =" in response:
                if "login.microsoftonline" in url:
                    authorize_info = {
                        "entry_index": entry_index,
                        "request_url": url,
                    }
                    if not microsoft_host:
                        microsoft_host = host

        if authorize_info and wsfed_info:
            break

    if not authorize_info and not microsoft_host:
        return None

    return {
        "detected": True,
        "microsoft_host": microsoft_host or "login.microsoftonline.com",
        "authorize_entry_index": (
            authorize_info["entry_index"] if authorize_info else None
        ),
        "authorize_request_url": (
            authorize_info["request_url"] if authorize_info else None
        ),
        "wsfed_entry_index": (
            wsfed_info["entry_index"] if wsfed_info else None
        ),
        "wsfed_request_url": (
            wsfed_info["request_url"] if wsfed_info else None
        ),
    }


def detect_token_exchanges(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Detect and classify all OAuth token exchange requests in the traffic.

    Scans POST bodies for ``grant_type`` to identify token endpoint calls,
    then classifies each by flow type and extracts key parameters.

    The returned list is ordered by ``entry_index`` (sequential as they
    appeared in the capture) so D-2 can chain them: each token-exchange
    request's ``subject_token`` should be extracted from the *prior*
    exchange's response.

    Returns an empty list when no token exchanges are found (e.g.
    OpenID Connect Hybrid flow which obtains tokens via
    HTML form_post, not explicit /oauth/token calls).

    Each item in the list:
        {
            "entry_index": int,
            "step_number": int,
            "step_label": str,
            "request_url": str,
            "grant_type_raw": str,
            "detected_flow": str,
            "params": {
                "code": "...",
                "code_verifier": "...",
                "subject_token": "eyJ...",
                "client_id": "...",
                ...
            },
            "has_subject_token": bool,
            "sequence_position": int,   # 0-based position in the chain
        }
    """
    exchanges: List[Dict[str, Any]] = []

    for entry_index, step_number, step_label, entry in entries:
        method = (entry.get("method") or "").upper()
        if method != "POST":
            continue

        post_data = entry.get("post_data") or ""
        if not post_data.strip():
            continue

        stripped = post_data.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            continue

        try:
            body_params = parse_qs(stripped, keep_blank_values=False)
        except Exception:
            continue

        grant_types = body_params.get("grant_type", [])
        if not grant_types:
            continue

        grant_type_raw = unquote(grant_types[0])
        detected_flow = OAUTH_GRANT_TYPES.get(grant_type_raw, "unknown")

        params: Dict[str, str] = {}
        for param_name, values in body_params.items():
            param_lower = param_name.lower()
            if param_lower == "grant_type":
                continue
            if param_lower in OAUTH_BODY_PARAMS and values:
                params[param_lower] = values[0]

        exchanges.append({
            "entry_index": entry_index,
            "step_number": step_number,
            "step_label": step_label,
            "request_url": entry.get("url", ""),
            "grant_type_raw": grant_type_raw,
            "detected_flow": detected_flow,
            "params": params,
            "has_subject_token": "subject_token" in params,
            "sequence_position": len(exchanges),
        })

    return exchanges


def detect_static_api_key_headers(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Detect request headers that carry static API keys or subscription keys.

    Scans all request headers for names matching ``API_KEY_HEADER_RE``
    (e.g. ``x-api-key``, anything ending in ``-key``).  
    For each matching header name, collects all distinct
    values observed.  If the header has exactly one consistent value
    across all requests, it is flagged as a static API key suitable for
    User Defined Variable parameterization.

    Returns one item per unique (header_name, value) pair:
        {
            "header_name": str,         # original-cased header name
            "value": str,               # the static header value
            "occurrence_count": int,    # how many requests carry it
            "first_entry_index": int,   # first entry where seen
            "first_step_number": int,
            "first_step_label": str,
            "first_request_url": str,
        }

    Returns an empty list when no static API key headers are found.
    """
    # Collect: header_name_lower -> { "values": {value: count}, "first": ..., "original_name": ... }
    header_stats: Dict[str, Dict[str, Any]] = {}

    for entry_index, step_number, step_label, entry in entries:
        headers = entry.get("headers") or {}
        for hdr_name, hdr_value in headers.items():
            if not hdr_value or not API_KEY_HEADER_RE.search(hdr_name):
                continue

            key = hdr_name.lower()
            if key not in header_stats:
                header_stats[key] = {
                    "original_name": hdr_name,
                    "values": {},
                    "total_count": 0,
                    "first_entry_index": entry_index,
                    "first_step_number": step_number,
                    "first_step_label": step_label,
                    "first_request_url": entry.get("url", ""),
                }

            stats = header_stats[key]
            stats["values"][hdr_value] = stats["values"].get(hdr_value, 0) + 1
            stats["total_count"] += 1

    results: List[Dict[str, Any]] = []
    for _key, stats in header_stats.items():
        # Only flag headers with a single consistent value (static key)
        if len(stats["values"]) != 1:
            continue

        value = next(iter(stats["values"]))
        results.append({
            "header_name": stats["original_name"],
            "value": value,
            "occurrence_count": stats["total_count"],
            "first_entry_index": stats["first_entry_index"],
            "first_step_number": stats["first_step_number"],
            "first_step_label": stats["first_step_label"],
            "first_request_url": stats["first_request_url"],
        })

    return results


def _collect_all_candidates(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Run the full source extraction pipeline across all entries.

    Returns the raw, un-deduplicated list of candidates from every response
    location (headers, redirect URLs, JSON bodies, Set-Cookie, HTML form_post).

    This is the shared extraction core used by both extract_sources() and
    extract_sources_multi().
    """
    all_candidates = []

    for entry_index, step_number, step_label, entry in entries:
        response_headers = entry.get("response_headers") or {}
        response = entry.get("response", "")

        all_candidates.extend(extract_from_response_headers(
            response_headers, entry_index, step_number, step_label, entry
        ))

        all_candidates.extend(extract_from_redirect_url(
            response_headers, entry_index, step_number, step_label, entry
        ))

        all_candidates.extend(extract_from_json_body(
            response, response_headers, entry_index, step_number, step_label, entry
        ))

        all_candidates.extend(extract_from_set_cookie(
            response_headers, entry_index, step_number, step_label, entry
        ))

        all_candidates.extend(extract_from_html_form_post(
            response, response_headers, entry_index, step_number, step_label, entry
        ))

        all_candidates.extend(extract_from_entra_config(
            response, response_headers, entry_index, step_number, step_label, entry
        ))

        all_candidates.extend(extract_from_body_redirect_urls(
            response, response_headers, entry_index, step_number, step_label, entry
        ))

    return all_candidates


def extract_sources(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Phase 1: Extract all candidate source values from response data.
    
    Sources are extracted from:
    - Response headers (correlation IDs)
    - Redirect URLs (Location header params)
    - JSON response bodies (ID-like fields, OAuth tokens)
    - Set-Cookie headers (OAuth nonce values)
    - HTML form_post responses (OAuth tokens: id_token, code)
    
    Deduplicates by value, keeping the FIRST occurrence (earliest source).
    """
    all_candidates = _collect_all_candidates(entries)
    
    # Deduplicate by value - keep the FIRST occurrence (earliest source)
    seen_values: Dict[str, Dict[str, Any]] = {}
    for candidate in all_candidates:
        value_key = str(candidate.get("value", ""))
        if value_key not in seen_values:
            seen_values[value_key] = candidate
    
    return list(seen_values.values())


def extract_sources_multi(
    entries: List[Tuple[int, int, str, Dict[str, Any]]]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Phase 1 (multi-source variant): Extract all candidate source values,
    preserving semantically distinct sources grouped by value.

    Unlike extract_sources() which keeps only the first occurrence per value,
    this function retains multiple sources when they have different field names
    (source_key), enabling downstream resolution logic (resolve_best_source)
    to pick the semantically correct source using field-name affinity.

    Within each value group, sources with the same source_key are deduplicated
    to the first chronological occurrence. This prevents duplicate extractors
    when the same API is called multiple times returning the same value.

    Returns:
        Dict mapping value string -> list of candidate dicts (one per unique
        source_key, ordered by entry_index).
    """
    all_candidates = _collect_all_candidates(entries)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in all_candidates:
        value_key = str(candidate.get("value", ""))
        if value_key not in grouped:
            grouped[value_key] = []
        grouped[value_key].append(candidate)

    # Within each value group, keep only the first occurrence per source_key.
    # Different source_keys are preserved (e.g. countryId vs industryId for
    # value "10"), but duplicate extractions of the same field from repeated
    # API calls are collapsed to the earliest.
    for value_key, candidates in grouped.items():
        seen_keys: Dict[str, bool] = {}
        deduped: List[Dict[str, Any]] = []
        for c in candidates:
            sk = (c.get("source_key") or "").lower()
            if sk not in seen_keys:
                seen_keys[sk] = True
                deduped.append(c)
        grouped[value_key] = deduped

    return grouped
