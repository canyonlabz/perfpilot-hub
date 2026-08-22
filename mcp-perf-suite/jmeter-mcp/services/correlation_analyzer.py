# services/correlation_analyzer.py
"""
Correlation Analyzer for JMeter MCP - Version 0.2.0

BACKWARD COMPATIBILITY WRAPPER
==============================
This module now imports from services.correlations package.
Direct imports continue to work for existing code.

New recommended usage:
    from services.correlations import analyze_traffic

Legacy usage (still supported):
    from services.correlation_analyzer import analyze_traffic

Open Source - MIT License
Repository: https://github.com/canyonlabz/mcp-perf-suite
"""

# Re-export from the new package structure
from services.correlations import analyze_traffic

# Also export commonly used internal functions for testing
from services.correlations.classifiers import (
    classify_parameterization_strategy,
    classify_value_type,
    is_id_like_value,
)
from services.correlations.constants import (
    CORRELATION_HEADER_SUFFIXES,
    GUID_RE,
    ID_KEY_PATTERNS,
    JWT_RE,
    MAX_JSON_DEPTH,
    MIN_NUMERIC_ID_LENGTH,
    NUMERIC_ID_RE,
    OAUTH_PARAMS,
    SKIP_HEADERS_SOURCE,
    SKIP_HEADERS_USAGE,
)
from services.correlations.extractors import (
    extract_from_json_body,
    extract_from_redirect_url,
    extract_from_response_headers,
    extract_from_set_cookie,
    extract_sources,
)
from services.correlations.matchers import (
    detect_orphan_ids,
    extract_ids_from_request_url,
    find_usage_in_body,
    find_usage_in_headers,
    find_usage_in_url,
    find_usages,
)
from services.correlations.utils import (
    get_exclude_domains,
    is_excluded_url,
    normalize_for_comparison,
    value_matches,
    walk_json,
    walk_json_all_values,
)

__all__ = [
    # Main API
    "analyze_traffic",
    # Classifiers
    "classify_value_type",
    "is_id_like_value",
    "classify_parameterization_strategy",
    # Extractors
    "extract_from_response_headers",
    "extract_from_redirect_url",
    "extract_from_json_body",
    "extract_from_set_cookie",
    "extract_sources",
    # Matchers
    "find_usage_in_url",
    "find_usage_in_headers",
    "find_usage_in_body",
    "find_usages",
    "extract_ids_from_request_url",
    "detect_orphan_ids",
    # Utils
    "normalize_for_comparison",
    "value_matches",
    "walk_json",
    "walk_json_all_values",
    "is_excluded_url",
    "get_exclude_domains",
    # Constants
    "NUMERIC_ID_RE",
    "GUID_RE",
    "JWT_RE",
    "ID_KEY_PATTERNS",
    "CORRELATION_HEADER_SUFFIXES",
    "SKIP_HEADERS_SOURCE",
    "SKIP_HEADERS_USAGE",
    "MIN_NUMERIC_ID_LENGTH",
    "MAX_JSON_DEPTH",
    "OAUTH_PARAMS",
]

__version__ = "0.2.0"
