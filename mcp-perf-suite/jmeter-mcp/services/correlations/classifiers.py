"""
Value type and parameterization strategy classification.

Handles classification of:
- Value types (numeric ID, GUID, JWT, etc.)
- Parameterization strategies (extract_and_reuse, csv_dataset, udv)
"""

import re
from typing import Any, Dict

from .constants import (
    EMAIL_RE,
    GUID_RE,
    JWT_RE,
    MIN_NUMERIC_ID_LENGTH,
    NUMERIC_ID_RE,
)


# Epoch millisecond timestamp range: 2000-01-01 to 2050-01-01
_EPOCH_MS_MIN = 946_684_800_000
_EPOCH_MS_MAX = 2_524_608_000_000


def _is_epoch_ms_timestamp(value: str) -> bool:
    """Check if a numeric string is a plausible epoch millisecond timestamp."""
    if len(value) != 13:
        return False
    try:
        return _EPOCH_MS_MIN <= int(value) <= _EPOCH_MS_MAX
    except ValueError:
        return False


def classify_value_type(value: Any) -> str:
    """
    Classify value into type category.
    
    Returns one of:
    - timestamp: Epoch millisecond timestamp (SignalR cache-busting, etc.)
    - business_id_numeric: Numeric string or integer
    - business_id_guid: UUID/GUID format
    - oauth_token: JWT format (for Phase 2)
    - email: Email address
    - opaque_id: Long alphanumeric string
    - string_id: Other string identifiers
    - unknown: Unclassifiable
    """
    if isinstance(value, int):
        if _EPOCH_MS_MIN <= value <= _EPOCH_MS_MAX:
            return "timestamp"
        return "business_id_numeric"
    if isinstance(value, str):
        if NUMERIC_ID_RE.match(value):
            if _is_epoch_ms_timestamp(value):
                return "timestamp"
            return "business_id_numeric"
        if GUID_RE.match(value):
            return "business_id_guid"
        if JWT_RE.match(value):
            return "oauth_token"  # Flag for Phase 2
        if EMAIL_RE.match(value):
            return "email"
        if len(value) > 20 and value.isalnum():
            return "opaque_id"
        return "string_id"
    return "unknown"


def is_id_like_value(value: Any) -> bool:
    """
    Check if value looks like an ID that should be correlated.
    
    Filters out:
    - Small integers (likely not meaningful IDs)
    - Very short numeric strings
    - Values that don't look like identifiers
    """
    if isinstance(value, int):
        # Skip very small numbers (likely not meaningful IDs)
        return value >= 10 ** (MIN_NUMERIC_ID_LENGTH - 1)  # e.g., >= 10 for length 2
    if isinstance(value, str):
        # Numeric string - must meet minimum length
        if NUMERIC_ID_RE.match(value):
            return len(value) >= MIN_NUMERIC_ID_LENGTH
        # GUID - always valid
        if GUID_RE.match(value):
            return True
        # Opaque ID (long alphanumeric)
        if len(value) >= 8 and len(value) <= 128 and re.match(r"^[A-Za-z0-9_-]+$", value):
            return True
    return False


def classify_parameterization_strategy(
    correlation_found: bool,
    usage_count: int
) -> Dict[str, Any]:
    """
    Classify parameterization strategy based on correlation status and usage count.
    
    Rules:
    - Has source → extract_and_reuse (needs JSON/Regex Extractor)
    - No source, ≥3 occurrences → csv_dataset
    - No source, 1-2 occurrences → user_defined_variable
    
    Returns:
        dict with strategy, extractor_type (if applicable), and reason
    """
    if correlation_found:
        return {
            "strategy": "extract_and_reuse",
            "extractor_type": "regex",  # May be overridden based on source location
            "reason": "Value found in prior response",
        }
    
    if usage_count >= 3:
        return {
            "strategy": "csv_dataset",
            "reason": f"Value appears {usage_count} times, no source found",
        }
    
    return {
        "strategy": "user_defined_variable",
        "reason": f"Value appears {usage_count} time(s), no source found",
    }
