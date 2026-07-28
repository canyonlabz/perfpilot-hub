# hostname_helpers.py
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

# === Domain Exclusion (APM, analytics, etc.) ===
from services.correlations.utils import init_exclude_domains, is_excluded_url
init_exclude_domains(CONFIG)

# Import necessary modules for JMeter JMX generation
import xml.etree.ElementTree as ET  # Needed for creating empty hashTree elements

# ============================================================
# Helper Functions - Hostname Parameterization (obs-1)
# ============================================================

def _extract_unique_hostnames(network_data: Any) -> set:
    """
    Extract all unique hostnames from network capture data.
    
    Handles both dict format (step_name -> entries) and list format.
    Filters out excluded domains.
    
    Args:
        network_data: The loaded network capture JSON data
        
    Returns:
        Set of unique hostnames
    """
    hostnames = set()
    
    # Handle dict format (step_name -> list of entries)
    if isinstance(network_data, dict):
        for step_name, entries in network_data.items():
            if isinstance(entries, list):
                for entry in entries:
                    url = entry.get("url", "")
                    if url:
                        parsed = urllib.parse.urlparse(url)
                        if parsed.netloc and not is_excluded_url(url):
                            hostnames.add(parsed.netloc)
            elif isinstance(entries, dict):
                # Single entry
                url = entries.get("url", "")
                if url:
                    parsed = urllib.parse.urlparse(url)
                    if parsed.netloc and not is_excluded_url(url):
                        hostnames.add(parsed.netloc)
    # Handle list format
    elif isinstance(network_data, list):
        for entry in network_data:
            url = entry.get("url", "")
            if url:
                parsed = urllib.parse.urlparse(url)
                if parsed.netloc and not is_excluded_url(url):
                    hostnames.add(parsed.netloc)
    
    return hostnames


def _categorize_hostname(hostname: str, patterns_config: Dict[str, Any]) -> str:
    """
    Categorize a hostname based on pattern matching.
    
    Applies patterns in order of specificity:
    1. auth_internal (most specific)
    2. auth
    3. static
    4. app (default catch-all)
    
    Args:
        hostname: The hostname to categorize
        patterns_config: The default_patterns configuration
        
    Returns:
        Category name: "auth_internal", "auth", "static", or "app"
    """
    hostname_lower = hostname.lower()
    
    # Check categories in order of specificity
    category_order = ["auth_internal", "auth", "static"]
    
    for category in category_order:
        cat_config = patterns_config.get(category, {})
        patterns = cat_config.get("patterns", [])
        
        for pattern in patterns:
            if pattern.lower() in hostname_lower:
                return category
    
    # Default to app category
    return "app"


def _build_hostname_variable_map(
    hostnames: set,
    patterns_config: Dict[str, Any]
) -> Dict[str, str]:
    """
    Build a mapping from hostname to JMeter variable name.
    
    Categorizes each hostname and assigns variable names with
    underscore suffix for multiple hosts in the same category.
    
    Args:
        hostnames: Set of unique hostnames
        patterns_config: The default_patterns configuration
        
    Returns:
        Dictionary mapping hostname to variable name
    """
    # Group hostnames by category
    categories: Dict[str, List[str]] = {
        "auth_internal": [],
        "auth": [],
        "static": [],
        "app": []
    }
    
    for hostname in sorted(hostnames):  # Sort for consistent ordering
        category = _categorize_hostname(hostname, patterns_config)
        categories[category].append(hostname)
    
    # Build variable map
    hostname_to_var: Dict[str, str] = {}
    
    for category, hosts in categories.items():
        if not hosts:
            continue
        
        # Get the variable prefix for this category
        cat_config = patterns_config.get(category, {})
        prefix = cat_config.get("variable_prefix", f"{category}Hostname")
        
        if len(hosts) == 1:
            # Single host in category: no suffix
            hostname_to_var[hosts[0]] = prefix
        else:
            # Multiple hosts: add underscore suffix
            for i, host in enumerate(hosts, start=1):
                hostname_to_var[host] = f"{prefix}_{i}"
    
    return hostname_to_var


def _substitute_hostname_in_entry(entry: Dict, hostname_var_map: Dict[str, str]) -> bool:
    """
    Substitute hostnames in an entry's URL with JMeter variables.
    
    Handles both the main URL hostname AND nested hostnames in query parameters
    like 'goto=' which contain URL-encoded redirect chains (common in OAuth flows).
    
    Modifies the entry in place.
    
    Args:
        entry: The network capture entry (modified in place)
        hostname_var_map: Mapping from hostname to variable name
        
    Returns:
        True if any substitution was made, False otherwise
    """
    url = entry.get("url", "")
    if not url:
        return False
    
    original_url = url
    new_url = url
    
    # Process each hostname in the map
    for hostname, var_name in hostname_var_map.items():
        jmeter_var = f"${{{var_name}}}"
        
        # === Direct (non-encoded) patterns ===
        # Main URL hostname: ://hostname/ or ://hostname?
        new_url = new_url.replace(f"://{hostname}/", f"://{jmeter_var}/")
        new_url = new_url.replace(f"://{hostname}?", f"://{jmeter_var}?")
        new_url = new_url.replace(f"://{hostname}:", f"://{jmeter_var}:")  # hostname:port
        new_url = new_url.replace(f"://{hostname}&", f"://{jmeter_var}&")  # hostname followed by &
        
        # Handle URLs that end with hostname (no trailing slash)
        if new_url.endswith(f"://{hostname}"):
            new_url = new_url[:-len(hostname)] + jmeter_var
        
        # === Nested URL-encoded patterns (for OAuth redirect chains) ===
        # These patterns appear in query params like goto=, redirect_uri=, etc.
        
        # URL-encode the hostname for pattern matching
        encoded_hostname = urllib.parse.quote(hostname, safe='')
        
        # Double-encoded patterns (URL within URL within URL)
        # Pattern: %253A%252F%252Fhostname (://hostname double-encoded)
        new_url = new_url.replace(f"%253A%252F%252F{hostname}", f"%253A%252F%252F{jmeter_var}")
        new_url = new_url.replace(f"%252F%252F{hostname}", f"%252F%252F{jmeter_var}")
        
        # Single-encoded patterns (nested URL in query param)
        # Pattern: %3A%2F%2Fhostname (://hostname single-encoded, hostname raw)
        new_url = new_url.replace(f"%3A%2F%2F{hostname}", f"%3A%2F%2F{jmeter_var}")
        
        # Pattern: %3A%2F%2F{encoded_hostname} (both encoded)
        if encoded_hostname != hostname:
            new_url = new_url.replace(f"%3A%2F%2F{encoded_hostname}", f"%3A%2F%2F{jmeter_var}")
        
        # Pattern: %2F%2Fhostname (//hostname single-encoded)
        new_url = new_url.replace(f"%2F%2F{hostname}", f"%2F%2F{jmeter_var}")
        if encoded_hostname != hostname:
            new_url = new_url.replace(f"%2F%2F{encoded_hostname}", f"%2F%2F{jmeter_var}")
        
        # Mixed encoding pattern (common in OAuth)
        # Pattern: :%2F%2Fhostname (only // is encoded, : is raw)
        # Example: https:%2F%2Flogin.example.com:443
        new_url = new_url.replace(f":%2F%2F{hostname}", f":%2F%2F{jmeter_var}")
    
    # === OAuth token substitution ===
    # Substitute cdssotoken in URLs (regardless of hostname map)
    # Pattern: cdssotoken=VALUE (value ends at & or end of string)
    import re
    new_url = re.sub(
        r'cdssotoken=([^&\s]+)',
        r'cdssotoken=${cdssotoken}',
        new_url
    )
    
    if new_url != original_url:
        entry["url"] = new_url
        return True
    
    return False
