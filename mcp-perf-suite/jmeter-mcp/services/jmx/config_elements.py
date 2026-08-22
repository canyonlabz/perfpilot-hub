"""
services/jmx/config_elements.py

This module contains functions to create JMeter config elements.
For example, it includes functions to generate "User Defined Variables", "CSV Data Set Config" elements, etc.
End-users and developers can extend or modify these functions independently
of the core JMeter component utilities.
"""
import xml.etree.ElementTree as ET
import datetime
import os
import urllib.parse
from xml.dom import minidom

# === User Defined Variables Element ===
def create_user_defined_variables(udv_config):
    """
    Creates a User Defined Variables element based on configuration.
    udv_config should be a dictionary with keys:
      - enabled: bool
      - variables: dict (e.g., {"auth_token": "your_token", "user": "test"})
    Returns the Arguments element (or None if not enabled).
    """
    if not udv_config.get("enabled", False):
        return None
    udv = ET.Element("Arguments", attrib={
        "guiclass": "ArgumentsPanel",
        "testclass": "Arguments",
        "testname": "User Defined Variables",
        "enabled": "true"
    })
    collection = ET.SubElement(udv, "collectionProp", attrib={"name": "Arguments.arguments"})
    variables = udv_config.get("variables", {})
    for var, value in variables.items():
        var_elem = ET.SubElement(collection, "elementProp", attrib={
            "name": var,
            "elementType": "Argument"
        })
        ET.SubElement(var_elem, "stringProp", attrib={"name": "Argument.name"}).text = var
        ET.SubElement(var_elem, "stringProp", attrib={"name": "Argument.value"}).text = str(value)
        ET.SubElement(var_elem, "stringProp", attrib={"name": "Argument.metadata"}).text = "="
    return udv

# === CSV Data Set Config Element ===
def create_csv_data_set_config(csv_config):
    """
    Creates a CSV Data Set Config element based on configuration.
    csv_config should be a dictionary with keys:
      - enabled: bool
      - filename: path to CSV file (string)
      - ignore_first_line: bool
      - variable_names: comma separated string (e.g., "username,password")
      - delimiter: string (e.g., ",")
      - recycle_on_end: bool
      - stop_thread_on_error: bool
      - sharing_mode: string (e.g., "shareMode.all")
    Returns the CSVDataSet element (or None if not enabled).
    """
    if not csv_config.get("enabled", False):
        return None
    csv_data = ET.Element("CSVDataSet", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "CSVDataSet",
        "testname": "CSV Data Set Config",
        "enabled": "true"
    })
    ET.SubElement(csv_data, "stringProp", attrib={"name": "delimiter"}).text = csv_config.get("delimiter", ",")
    ET.SubElement(csv_data, "stringProp", attrib={"name": "fileEncoding"}).text = ""
    ET.SubElement(csv_data, "stringProp", attrib={"name": "filename"}).text = csv_config.get("filename", "test_data.csv")
    ET.SubElement(csv_data, "boolProp", attrib={"name": "ignoreFirstLine"}).text = str(csv_config.get("ignore_first_line", True)).lower()
    ET.SubElement(csv_data, "boolProp", attrib={"name": "quotedData"}).text = "false"
    ET.SubElement(csv_data, "boolProp", attrib={"name": "recycle"}).text = str(csv_config.get("recycle_on_end", True)).lower()
    ET.SubElement(csv_data, "stringProp", attrib={"name": "shareMode"}).text = csv_config.get("sharing_mode", "shareMode.all")
    ET.SubElement(csv_data, "boolProp", attrib={"name": "stopThread"}).text = str(csv_config.get("stop_thread_on_error", False)).lower()
    ET.SubElement(csv_data, "stringProp", attrib={"name": "variableNames"}).text = csv_config.get("variable_names", "")
    return csv_data

# === HTTP Header Manager Element ===
# Headers to exclude from HTTP Header Manager (handled by other JMeter components)
EXCLUDED_HEADERS = {
    "cookie",          # Handled by HTTP Cookie Manager
    "content-length",  # Automatically calculated by JMeter
}

# HTTP/2 pseudo-headers that may need to be excluded
# JMeter uses HTTP/1.1 by default; these headers cause errors on non-HTTP/2 backends
HTTP2_PSEUDO_HEADERS = {
    ":method",     # HTTP/2 method pseudo-header
    ":path",       # HTTP/2 path pseudo-header
    ":scheme",     # HTTP/2 scheme pseudo-header (http/https)
    ":authority",  # HTTP/2 authority pseudo-header (host:port)
    ":status",     # HTTP/2 status pseudo-header (response only)
}

# Headers that should have hostname parameterization applied
# These headers contain hostnames that should be replaced with JMeter variables
HOSTNAME_HEADERS = {
    "host",        # e.g., app.example.com
    "origin",      # e.g., https://login.example.com
    "referer",     # e.g., https://login.example.com/login/?goto=...
    ":authority",  # HTTP/2 equivalent of Host header
    ":path",       # HTTP/2 path (may contain hostname in query params)
}

# OAuth token headers that need special substitution (standard OAuth headers only)
# Note: Company-specific headers should be configured via oauth_config in jmeter_config.yaml
OAUTH_TOKEN_HEADERS = {
    "authorization",  # Bearer token: Authorization: Bearer eyJ0eXA...
}

# Default variable names for OAuth token headers
OAUTH_TOKEN_VAR_DEFAULTS = {
    "authorization": "bearer_token",
}

def _substitute_hostname_in_header_value(header_name: str, header_value: str, hostname_var_map: dict) -> str:
    """
    Substitute hostnames in header values with JMeter variables.
    
    Handles multiple levels of URL encoding for OAuth redirect chains where
    hostnames appear in nested query parameters like 'goto=' or 'redirect_uri='.
    
    Args:
        header_name: The header name (lowercase)
        header_value: The original header value
        hostname_var_map: Mapping from hostname to JMeter variable name
        
    Returns:
        The header value with hostnames replaced by JMeter variables
        
    Encoding patterns handled:
        - Direct: ://hostname/
        - Single-encoded: %3A%2F%2Fhostname (://hostname encoded)
        - Mixed: :%2F%2Fhostname (only // encoded, common in OAuth redirects)
        - Double-encoded: %253A%252F%252Fhostname (for URL-within-URL)
        - With ports: hostname:443, hostname%3A443, hostname%253A443
    """
    if not hostname_var_map or not header_value:
        return header_value
    
    result = header_value
    
    for hostname, var_name in hostname_var_map.items():
        jmeter_var = f"${{{var_name}}}"
        
        if header_name in ("host", ":authority"):
            # These headers contain just the hostname (no scheme)
            # Direct replacement if exact match
            if result == hostname:
                result = jmeter_var
            # Also handle port suffix (e.g., hostname:443)
            elif result.startswith(hostname + ":"):
                result = jmeter_var + result[len(hostname):]
                
        elif header_name in ("origin", "referer", ":path"):
            # These headers contain URLs or paths with hostnames
            # Process from most-encoded to least-encoded to avoid partial replacements
            
            # URL-encode the hostname for pattern matching
            encoded_hostname = urllib.parse.quote(hostname, safe='')
            
            # === Double-encoded patterns (URL within URL within URL) ===
            # Pattern: %253A%252F%252Fhostname (://hostname double-encoded)
            result = result.replace(f"%253A%252F%252F{hostname}", f"%253A%252F%252F{jmeter_var}")
            # Pattern: %252F%252Fhostname (//hostname double-encoded)
            result = result.replace(f"%252F%252F{hostname}", f"%252F%252F{jmeter_var}")
            
            # === Single-encoded patterns (nested URL in query param) ===
            # Pattern: %3A%2F%2Fhostname (://hostname single-encoded, hostname raw)
            # This is the most common pattern in OAuth redirect chains
            result = result.replace(f"%3A%2F%2F{hostname}", f"%3A%2F%2F{jmeter_var}")
            
            # Pattern: %3A%2F%2F{encoded_hostname} (://hostname single-encoded, hostname also encoded)
            if encoded_hostname != hostname:
                result = result.replace(f"%3A%2F%2F{encoded_hostname}", f"%3A%2F%2F{jmeter_var}")
            
            # Pattern: %2F%2Fhostname (//hostname single-encoded, without colon)
            result = result.replace(f"%2F%2F{hostname}", f"%2F%2F{jmeter_var}")
            if encoded_hostname != hostname:
                result = result.replace(f"%2F%2F{encoded_hostname}", f"%2F%2F{jmeter_var}")
            
            # === Mixed encoding pattern (common in OAuth) ===
            # Pattern: :%2F%2Fhostname (only // is encoded, : is raw)
            # Example: https:%2F%2Flogin.example.com:443
            result = result.replace(f":%2F%2F{hostname}", f":%2F%2F{jmeter_var}")
            
            # === Direct (non-encoded) patterns ===
            # Pattern: ://hostname/ or ://hostname? or ://hostname: (with port)
            result = result.replace(f"://{hostname}/", f"://{jmeter_var}/")
            result = result.replace(f"://{hostname}?", f"://{jmeter_var}?")
            result = result.replace(f"://{hostname}:", f"://{jmeter_var}:")  # hostname:port
            result = result.replace(f"://{hostname}&", f"://{jmeter_var}&")  # hostname followed by query param
            
            # Handle end of string
            if result.endswith(f"://{hostname}"):
                result = result[:-len(hostname)] + jmeter_var
    
    # === OAuth token substitution in header values ===
    # Substitute cdssotoken in referer/origin headers (same as URL substitution)
    if header_name in ("referer", "origin", ":path"):
        import re
        result = re.sub(
            r'cdssotoken=([^&\s]+)',
            r'cdssotoken=${cdssotoken}',
            result
        )
    
    return result


def _substitute_oauth_token_in_header_value(
    header_name: str, 
    header_value: str,
    oauth_token_var_map: dict = None
) -> str:
    """
    Substitute OAuth tokens in specific header values with JMeter variables.
    
    Handles standard OAuth headers:
    - Authorization: Bearer eyJ0eXA... -> Authorization: Bearer ${bearer_token}
    
    Custom SSO headers (like nonce headers) should be handled via correlation
    configuration, not hardcoded here.
    
    Args:
        header_name: The header name (lowercase)
        header_value: The original header value
        oauth_token_var_map: Optional custom mapping of header -> variable name
        
    Returns:
        The header value with tokens replaced by JMeter variables
    """
    if not header_value:
        return header_value
    
    header_lower = header_name.lower()
    
    # Merge default mapping with custom mapping
    var_map = OAUTH_TOKEN_VAR_DEFAULTS.copy()
    if oauth_token_var_map:
        var_map.update(oauth_token_var_map)
    
    # Handle Authorization header (standard OAuth)
    if header_lower == "authorization":
        # Pattern: Bearer <token>
        if header_value.lower().startswith("bearer "):
            var_name = var_map.get("authorization", "bearer_token")
            return f"Bearer ${{{var_name}}}"
        # Pattern: Basic <credentials> - usually not dynamic, but handle if needed
        elif header_value.lower().startswith("basic "):
            # Don't substitute Basic auth - typically from CSV data
            return header_value
    
    # Handle custom token headers via oauth_token_var_map
    # This allows users to configure company-specific headers in config
    elif header_lower in var_map:
        var_name = var_map[header_lower]
        return f"${{{var_name}}}"
    
    return header_value


def _substitute_oauth_token_in_url(url: str) -> str:
    """
    Substitute OAuth tokens in URL query parameters with JMeter variables.
    
    Handles:
    - ?cdssotoken=Lcdf6RsVK8B... -> ?cdssotoken=${cdssotoken}
    
    Args:
        url: The URL string to process
        
    Returns:
        The URL with OAuth tokens replaced by JMeter variables
    """
    import re
    
    if not url:
        return url
    
    result = url
    
    # Pattern: cdssotoken=VALUE (where VALUE ends at & or end of string)
    # Handle both encoded and non-encoded values
    result = re.sub(
        r'cdssotoken=([^&\s]+)',
        r'cdssotoken=${cdssotoken}',
        result
    )
    
    return result


def create_header_manager(headers, hostname_var_map=None, exclude_http2_pseudo_headers=True, oauth_token_var_map=None):
    """
    Creates a Header Manager element with the provided headers.
    Excludes headers that are handled by other JMeter components (e.g., 'cookie' is handled by Cookie Manager).
    Optionally parameterizes hostname-related headers and OAuth token headers.
    
    Args:
        headers: Dictionary of header name -> value pairs
        hostname_var_map: Optional mapping from hostname to JMeter variable name for parameterization
        exclude_http2_pseudo_headers: If True, exclude HTTP/2 pseudo-headers (:method, :path, :scheme, :authority, :status).
                                      JMeter uses HTTP/1.1 by default, and these headers cause errors on non-HTTP/2 backends.
                                      Default is True for compatibility with public websites.
        oauth_token_var_map: Optional mapping from OAuth header name to JMeter variable name.
                            Default mappings: authorization -> bearer_token, x-cdsso-nonce -> cdsso_nonce
        
    Returns:
        The HeaderManager XML element.
    """
    header_manager = ET.Element("HeaderManager", attrib={
        "guiclass": "HeaderPanel",
        "testclass": "HeaderManager",
        "testname": "HTTP Header Manager"
    })
    collection = ET.SubElement(header_manager, "collectionProp", attrib={"name": "HeaderManager.headers"})
    
    # Build the set of headers to exclude
    headers_to_exclude = set(EXCLUDED_HEADERS)
    if exclude_http2_pseudo_headers:
        headers_to_exclude.update(HTTP2_PSEUDO_HEADERS)
    
    for name, value in headers.items():
        # Skip excluded headers (case-insensitive comparison)
        if name.lower() in headers_to_exclude:
            continue
        
        final_value = value
        
        # Apply hostname parameterization for relevant headers
        if hostname_var_map and name.lower() in HOSTNAME_HEADERS:
            final_value = _substitute_hostname_in_header_value(name.lower(), final_value, hostname_var_map)
        
        # Apply OAuth token substitution for relevant headers
        if name.lower() in OAUTH_TOKEN_HEADERS:
            final_value = _substitute_oauth_token_in_header_value(name.lower(), final_value, oauth_token_var_map)
            
        header_element = ET.SubElement(collection, "elementProp", attrib={
            "name": "",
            "elementType": "Header"
        })
        ET.SubElement(header_element, "stringProp", attrib={"name": "Header.name"}).text = name
        ET.SubElement(header_element, "stringProp", attrib={"name": "Header.value"}).text = final_value
    
    return header_manager

# === HTTP Cookie Manager Element ===
def create_cookie_manager():
    """
    Creates a default Cookie Manager element.
    """
    cookie_manager = ET.Element("CookieManager", attrib={
       "guiclass": "CookiePanel",
       "testclass": "CookieManager",
       "testname": "HTTP Cookie Manager",
       "enabled": "true"
    })
    ET.SubElement(cookie_manager, "collectionProp", attrib={"name": "CookieManager.cookies"})
    ET.SubElement(cookie_manager, "boolProp", attrib={"name": "CookieManager.clearEachIteration"}).text = "true"
    ET.SubElement(cookie_manager, "boolProp", attrib={"name": "CookieManager.controlledByThreadGroup"}).text = "false"
    
    return cookie_manager


# === HTTP Request Defaults Element ===
def create_http_request_defaults(
    testname: str = "HTTP Request Defaults",
    protocol: str = "https",
    domain: str = "",
    port: str = "",
    connect_timeout: str = "30000",
    response_timeout: str = "30000"
):
    """
    Creates an HTTP Request Defaults (ConfigTestElement) element.

    Centralises protocol, domain, port, and timeout settings so individual
    HTTP samplers don't need to repeat them. Placed at the Thread Group or
    Test Plan level.

    Args:
        testname: Display name in JMeter.
        protocol: Default protocol ("https" or "http").
        domain: Default server name or IP. Supports variables like "${envHostname}".
        port: Default port number (empty string uses protocol default).
        connect_timeout: Connection timeout in milliseconds.
        response_timeout: Response timeout in milliseconds.

    Returns:
        The ConfigTestElement XML element.
    """
    defaults = ET.Element("ConfigTestElement", attrib={
        "guiclass": "HttpDefaultsGui",
        "testclass": "ConfigTestElement",
        "testname": testname,
        "enabled": "true"
    })

    args_prop = ET.SubElement(defaults, "elementProp", attrib={
        "name": "HTTPsampler.Arguments",
        "elementType": "Arguments",
        "guiclass": "HTTPArgumentsPanel",
        "testclass": "Arguments",
        "testname": "User Defined Variables",
        "enabled": "true"
    })
    ET.SubElement(args_prop, "collectionProp", attrib={
        "name": "Arguments.arguments"
    })

    ET.SubElement(defaults, "stringProp", attrib={
        "name": "HTTPSampler.domain"
    }).text = domain
    ET.SubElement(defaults, "stringProp", attrib={
        "name": "HTTPSampler.port"
    }).text = str(port)
    ET.SubElement(defaults, "stringProp", attrib={
        "name": "HTTPSampler.protocol"
    }).text = protocol
    ET.SubElement(defaults, "stringProp", attrib={
        "name": "HTTPSampler.connect_timeout"
    }).text = str(connect_timeout)
    ET.SubElement(defaults, "stringProp", attrib={
        "name": "HTTPSampler.response_timeout"
    }).text = str(response_timeout)

    return defaults


# === HTTP Authorization Manager Element ===
def create_auth_manager(
    testname: str = "HTTP Authorization Manager",
    entries: list = None
):
    """
    Creates an HTTP Authorization Manager element.

    Manages Basic/Digest authentication credentials for HTTP requests.

    Args:
        testname: Display name in JMeter.
        entries: List of auth entry dicts, each containing:
            - base_url (str): URL pattern to match (e.g., "https://${envHostname}")
            - username (str): Username or variable (e.g., "${username}")
            - password (str): Password or variable (e.g., "${password}")
            - domain (str): NTLM domain (empty for Basic/Digest)
            - realm (str): Auth realm (empty string for most cases)
            - mechanism (str): "BASIC", "DIGEST", or "KERBEROS"

    Returns:
        The AuthManager XML element.
    """
    if entries is None:
        entries = []

    auth_manager = ET.Element("AuthManager", attrib={
        "guiclass": "AuthPanel",
        "testclass": "AuthManager",
        "testname": testname,
        "enabled": "true"
    })

    collection = ET.SubElement(auth_manager, "collectionProp", attrib={
        "name": "AuthManager.auth_list"
    })

    for entry in entries:
        auth_elem = ET.SubElement(collection, "elementProp", attrib={
            "name": "",
            "elementType": "Authorization"
        })
        ET.SubElement(auth_elem, "stringProp", attrib={
            "name": "Authorization.url"
        }).text = entry.get("base_url", "")
        ET.SubElement(auth_elem, "stringProp", attrib={
            "name": "Authorization.username"
        }).text = entry.get("username", "")
        ET.SubElement(auth_elem, "stringProp", attrib={
            "name": "Authorization.password"
        }).text = entry.get("password", "")
        ET.SubElement(auth_elem, "stringProp", attrib={
            "name": "Authorization.domain"
        }).text = entry.get("domain", "")
        ET.SubElement(auth_elem, "stringProp", attrib={
            "name": "Authorization.realm"
        }).text = entry.get("realm", "")
        ET.SubElement(auth_elem, "stringProp", attrib={
            "name": "Authorization.mechanism"
        }).text = entry.get("mechanism", "BASIC")

    return auth_manager


# === Keystore Configuration Element ===
def create_keystore_config(
    testname: str = "Keystore Configuration",
    keystore_path: str = "",
    keystore_password: str = "",
    keystore_type: str = "PKCS12",
    alias: str = "",
    key_password: str = ""
):
    """
    Creates a Keystore Configuration element for client certificate / SSL support.

    Used when the target server requires mutual TLS (mTLS) client certificates.

    Args:
        testname: Display name in JMeter.
        keystore_path: Path to the keystore file (e.g., "certs/client.p12").
        keystore_password: Password for the keystore. Supports variables.
        keystore_type: Keystore format - "PKCS12", "JKS", etc.
        alias: Certificate alias within the keystore (empty uses first).
        key_password: Password for the private key (empty uses keystore password).

    Returns:
        The KeystoreConfig XML element.
    """
    keystore = ET.Element("KeystoreConfig", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "KeystoreConfig",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(keystore, "stringProp", attrib={
        "name": "preload"
    }).text = "true"
    ET.SubElement(keystore, "stringProp", attrib={
        "name": "clientCertAliasVarName"
    }).text = alias
    ET.SubElement(keystore, "stringProp", attrib={
        "name": "endIndex"
    }).text = ""
    ET.SubElement(keystore, "stringProp", attrib={
        "name": "startIndex"
    }).text = ""

    return keystore
