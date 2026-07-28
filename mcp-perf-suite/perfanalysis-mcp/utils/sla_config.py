# utils/sla_config.py
"""
SLA Configuration Loader, Resolver, and Validator.

Loads SLA definitions from slas.yaml, resolves per-API SLA thresholds
based on a three-level pattern matching hierarchy, and validates that
configured patterns match actual test result labels.

This module is the single point of access for all SLA-related configuration.
No hardcoded SLA values exist in this module or should exist anywhere in the
codebase. If slas.yaml is missing, a clear error is raised immediately.
"""

import yaml
import os
import re
import fnmatch
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SLA_UNITS = {"P90", "P95", "P99"}
SLA_CONFIG_FILENAME = "slas.yaml"

# Module-level cache for loaded SLA config
_SLA_CONFIG_CACHE: Optional[Dict[str, Any]] = None


# ===========================================================================
# SLA Config Loader (Task 1.2)
# ===========================================================================

def load_sla_config(force_reload: bool = False) -> Dict[str, Any]:
    """
    Load and validate the SLA configuration from slas.yaml.

    The file is expected at the root of the perfanalysis-mcp directory
    (same level as config.yaml).

    Args:
        force_reload: If True, bypass the cache and reload from disk.

    Returns:
        Validated SLA configuration dict.

    Raises:
        FileNotFoundError: If slas.yaml does not exist.
        ValueError: If the configuration schema is invalid.
    """
    global _SLA_CONFIG_CACHE

    if _SLA_CONFIG_CACHE is not None and not force_reload:
        return _SLA_CONFIG_CACHE

    # slas.yaml lives at the MCP root (one level up from utils/)
    mcp_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    sla_config_path = os.path.join(mcp_root, SLA_CONFIG_FILENAME)

    if not os.path.exists(sla_config_path):
        raise FileNotFoundError(
            f"SLA configuration file not found: {sla_config_path}\n"
            f"This file is required for SLA evaluation. "
            f"Copy 'slas.example.yaml' to 'slas.yaml' and configure your SLA profiles.\n"
            f"See docs/sla-configuration-guide.md for details."
        )

    with open(sla_config_path, 'r', encoding='utf-8') as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing '{SLA_CONFIG_FILENAME}': {e}")

    _validate_sla_config(config)

    _SLA_CONFIG_CACHE = config
    logger.info("SLA configuration loaded successfully from '%s'.", sla_config_path)
    return config


# ===========================================================================
# Schema Validation
# ===========================================================================

def _validate_sla_config(config: Dict[str, Any]) -> None:
    """
    Validate the top-level structure and all nested objects in slas.yaml.

    Raises:
        ValueError: If any required field is missing or has an invalid value.
    """
    if not isinstance(config, dict):
        raise ValueError(
            f"{SLA_CONFIG_FILENAME} must contain a YAML mapping (dict) at the root level."
        )

    # -- version (required) --------------------------------------------------
    if "version" not in config:
        raise ValueError(f"{SLA_CONFIG_FILENAME} must include a 'version' field.")

    # -- default_sla (required, all fields mandatory at file level) ----------
    if "default_sla" not in config:
        raise ValueError(f"{SLA_CONFIG_FILENAME} must include a 'default_sla' section.")

    _validate_file_level_default_sla(config["default_sla"])

    # -- slas (optional list of profiles) ------------------------------------
    slas = config.get("slas", [])
    if not isinstance(slas, list):
        raise ValueError("'slas' must be a list of SLA profile objects.")

    seen_ids: set = set()
    for i, profile in enumerate(slas):
        _validate_sla_profile(profile, i, seen_ids)


def _validate_file_level_default_sla(block: Dict[str, Any]) -> None:
    """
    Validate the file-level default_sla block.

    At the file level ALL fields are required because this is the ultimate
    fallback -- there are no hardcoded defaults anywhere in the codebase.
    """
    context = "default_sla"

    if not isinstance(block, dict):
        raise ValueError(f"'{context}' must be a mapping (dict).")

    required_fields = ["response_time_sla_ms", "sla_unit", "error_rate_threshold"]
    for field in required_fields:
        if field not in block:
            raise ValueError(
                f"'{context}' must include '{field}'. "
                f"All fields are required at the file level because there are no "
                f"hardcoded fallback values."
            )

    _validate_response_time(block["response_time_sla_ms"], context)
    _validate_sla_unit(block["sla_unit"], context)
    _validate_error_rate(block["error_rate_threshold"], context)


def _validate_sla_profile(profile: Dict[str, Any], index: int, seen_ids: set) -> None:
    """Validate a single SLA profile entry in the slas list."""
    if not isinstance(profile, dict):
        raise ValueError(f"SLA profile at index {index} must be a mapping (dict).")

    # -- id (required, unique) -----------------------------------------------
    profile_id = profile.get("id")
    if not profile_id or not isinstance(profile_id, str):
        raise ValueError(
            f"SLA profile at index {index} must have a non-empty string 'id' field."
        )

    if profile_id in seen_ids:
        raise ValueError(f"Duplicate SLA profile id: '{profile_id}'.")
    seen_ids.add(profile_id)

    context_prefix = f"slas['{profile_id}']"

    # -- default_sla (required at profile level, response_time_sla_ms mandatory) --
    if "default_sla" not in profile:
        raise ValueError(
            f"{context_prefix} must include a 'default_sla' section with at least "
            f"'response_time_sla_ms'."
        )

    profile_default = profile["default_sla"]
    if not isinstance(profile_default, dict):
        raise ValueError(f"{context_prefix}.default_sla must be a mapping (dict).")

    if "response_time_sla_ms" not in profile_default:
        raise ValueError(
            f"{context_prefix}.default_sla must include 'response_time_sla_ms'."
        )

    _validate_response_time(
        profile_default["response_time_sla_ms"],
        f"{context_prefix}.default_sla"
    )

    # sla_unit and error_rate_threshold are optional (inherit from file default)
    if "sla_unit" in profile_default:
        _validate_sla_unit(profile_default["sla_unit"], f"{context_prefix}.default_sla")

    if "error_rate_threshold" in profile_default:
        _validate_error_rate(
            profile_default["error_rate_threshold"],
            f"{context_prefix}.default_sla"
        )

    # -- api_overrides (optional list) ----------------------------------------
    overrides = profile.get("api_overrides", [])
    if not isinstance(overrides, list):
        raise ValueError(f"{context_prefix}.api_overrides must be a list.")

    for j, override in enumerate(overrides):
        _validate_api_override(override, j, context_prefix)


def _validate_api_override(
    override: Dict[str, Any], index: int, context_prefix: str
) -> None:
    """Validate a single api_override entry."""
    ctx = f"{context_prefix}.api_overrides[{index}]"

    if not isinstance(override, dict):
        raise ValueError(f"{ctx} must be a mapping (dict).")

    if "pattern" not in override:
        raise ValueError(f"{ctx} must include a 'pattern' field.")

    if not isinstance(override["pattern"], str) or not override["pattern"].strip():
        raise ValueError(f"{ctx}.pattern must be a non-empty string.")

    if "response_time_sla_ms" not in override:
        raise ValueError(f"{ctx} must include a 'response_time_sla_ms' field.")

    _validate_response_time(override["response_time_sla_ms"], ctx)

    # sla_unit and error_rate_threshold are optional (inherit from profile/file default)
    if "sla_unit" in override:
        _validate_sla_unit(override["sla_unit"], ctx)

    if "error_rate_threshold" in override:
        _validate_error_rate(override["error_rate_threshold"], ctx)


# ===========================================================================
# Field-Level Validators
# ===========================================================================

def _validate_response_time(value: Any, context: str) -> None:
    """Validate that response_time_sla_ms is a positive number."""
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(
            f"'{context}.response_time_sla_ms' must be a positive number, got: {value}"
        )


def _validate_sla_unit(value: Any, context: str) -> None:
    """Validate that sla_unit is one of the supported percentile options."""
    if value not in VALID_SLA_UNITS:
        raise ValueError(
            f"'{context}.sla_unit' must be one of {sorted(VALID_SLA_UNITS)}, got: '{value}'"
        )


def _validate_error_rate(value: Any, context: str) -> None:
    """Validate that error_rate_threshold is a non-negative number."""
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError(
            f"'{context}.error_rate_threshold' must be a non-negative number, got: {value}"
        )


# ===========================================================================
# SLA Resolver (Task 1.3)
# ===========================================================================

def _classify_pattern_specificity(pattern: str) -> int:
    """
    Classify an api_override pattern into a specificity level.

    Used to enforce the three-level pattern matching precedence:
        1 = Most specific  (full JMeter label match)
        2 = Medium          (TC#_TS# or TC#_S# prefix match)
        3 = Broadest        (TC# prefix match)

    Args:
        pattern: The glob pattern string from api_overrides.

    Returns:
        Integer specificity level (1, 2, or 3).
    """
    # TC#_TS# or TC#_S# followed by a wildcard suffix → medium specificity
    if re.match(r'^TC\d+_(TS|S)\d+[_\-]?\*$', pattern, re.IGNORECASE):
        return 2

    # TC# followed by a wildcard suffix → broadest
    if re.match(r'^TC\d+[_\-]?\*$', pattern, re.IGNORECASE):
        return 3

    # Everything else → most specific (full label match)
    return 1


def _find_sla_profile(
    config: Dict[str, Any], sla_id: str
) -> Optional[Dict[str, Any]]:
    """Look up an SLA profile by its id. Returns None if not found."""
    for profile in config.get("slas", []):
        if profile.get("id") == sla_id:
            return profile
    return None


def _build_sla_result(
    response_time_sla_ms: Any,
    sla_unit: str,
    error_rate_threshold: float,
    source: str,
    reason: Optional[str] = None,
    pattern_matched: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a standardized SLA result dict.

    This is the common return shape for get_sla_for_api().
    """
    result: Dict[str, Any] = {
        "response_time_sla_ms": int(response_time_sla_ms),
        "sla_unit": sla_unit,
        "error_rate_threshold": float(error_rate_threshold),
        "source": source,
    }
    if reason is not None:
        result["reason"] = reason
    if pattern_matched is not None:
        result["pattern_matched"] = pattern_matched
    return result


def get_sla_for_api(sla_id: Optional[str], label: str) -> Dict[str, Any]:
    """
    Resolve the SLA threshold for a given API label.

    Implements the configuration hierarchy:
        1. API-specific override  (pattern match in api_overrides)
        2. SLA profile default_sla
        3. File-level default_sla

    Pattern matching uses three-level specificity precedence:
        1. Full JMeter label match  (most specific)
        2. TC#_TS# or TC#_S# match  (medium specificity)
        3. TC# match                 (broadest)

    Within the same specificity level, the first match in YAML order wins.

    Args:
        sla_id: The SLA profile ID to look up. If None, the file-level
                default_sla is used directly.
        label:  The JMeter label string to match against api_overrides.

    Returns:
        Dict containing:
            - response_time_sla_ms  (int)
            - sla_unit              (str, e.g. "P90")
            - error_rate_threshold  (float)
            - source                (str describing provenance)
            - reason                (str, only if from an api_override with a reason)
            - pattern_matched       (str, only if an api_override matched)

    Raises:
        FileNotFoundError: If slas.yaml does not exist.
        ValueError: If sla_id is provided but not found in slas.yaml.
    """
    config = load_sla_config()
    file_default = config["default_sla"]

    # ----- No sla_id → file-level default -----------------------------------
    if sla_id is None:
        return _build_sla_result(
            response_time_sla_ms=file_default["response_time_sla_ms"],
            sla_unit=file_default["sla_unit"],
            error_rate_threshold=file_default["error_rate_threshold"],
            source=f"{SLA_CONFIG_FILENAME}/default",
        )

    # ----- Look up the SLA profile ------------------------------------------
    profile = _find_sla_profile(config, sla_id)
    if profile is None:
        available = [p.get("id") for p in config.get("slas", [])]
        raise ValueError(
            f"SLA profile '{sla_id}' not found in {SLA_CONFIG_FILENAME}. "
            f"Available profiles: {available}"
        )

    profile_default = profile.get("default_sla", {})

    # Helper: resolve a field through the inheritance chain
    # (override → profile default → file default)
    def _resolve(field: str, override: Optional[Dict] = None) -> Any:
        if override and field in override:
            return override[field]
        if field in profile_default:
            return profile_default[field]
        return file_default[field]

    # ----- Try to match against api_overrides (if any) ----------------------
    overrides = profile.get("api_overrides", [])
    if overrides and label:
        # Classify each override by specificity
        classified: List[tuple] = []
        for idx, override in enumerate(overrides):
            specificity = _classify_pattern_specificity(override["pattern"])
            classified.append((specificity, idx, override))

        # Sort: most specific first (level 1), then by YAML order within level
        classified.sort(key=lambda x: (x[0], x[1]))

        # Try matching in precedence order
        for _specificity, _idx, override in classified:
            if fnmatch.fnmatch(label, override["pattern"]):
                return _build_sla_result(
                    response_time_sla_ms=override["response_time_sla_ms"],
                    sla_unit=_resolve("sla_unit", override),
                    error_rate_threshold=_resolve("error_rate_threshold", override),
                    source=f"{SLA_CONFIG_FILENAME}/{sla_id}/api_override",
                    reason=override.get("reason"),
                    pattern_matched=override["pattern"],
                )

    # ----- No override matched → profile default_sla ------------------------
    if profile_default:
        return _build_sla_result(
            response_time_sla_ms=profile_default["response_time_sla_ms"],
            sla_unit=_resolve("sla_unit"),
            error_rate_threshold=_resolve("error_rate_threshold"),
            source=f"{SLA_CONFIG_FILENAME}/{sla_id}/default",
        )

    # ----- Profile exists but has no default_sla → file-level default -------
    return _build_sla_result(
        response_time_sla_ms=file_default["response_time_sla_ms"],
        sla_unit=file_default["sla_unit"],
        error_rate_threshold=file_default["error_rate_threshold"],
        source=f"{SLA_CONFIG_FILENAME}/default",
    )


def get_sla_for_labels(
    sla_id: Optional[str], labels: List[str]
) -> Dict[str, Dict[str, Any]]:
    """
    Resolve SLAs for multiple labels at once.

    Convenience wrapper around get_sla_for_api() for batch processing
    (e.g., iterating over all APIs in an aggregate report).

    Args:
        sla_id: The SLA profile ID.
        labels: List of JMeter label strings.

    Returns:
        Dict mapping each label to its resolved SLA result dict.
    """
    return {label: get_sla_for_api(sla_id, label) for label in labels}


# ===========================================================================
# SLA Validator (Task 1.4)
# ===========================================================================

async def validate_sla_patterns(
    sla_id: str, labels: List[str], ctx: "Context"
) -> Dict[str, Any]:
    """
    Validate that api_override patterns in an SLA profile match actual test
    result labels.

    This function checks every pattern defined in the profile's api_overrides
    against the provided list of JMeter labels.  Unmatched patterns are
    reported via ``ctx.info`` so the user (or an AI agent like Cursor) can
    diagnose and fix pattern configuration issues, then re-run the analysis.

    This is an informational check — it does NOT block analysis.

    Args:
        sla_id:  The SLA profile ID to validate.
        labels:  List of unique JMeter labels from the test results.
        ctx:     FastMCP Context for reporting informational messages.

    Returns:
        Dict containing:
            - sla_id            (str)
            - total_patterns    (int)
            - matched_patterns  (list of dicts with pattern, count, samples)
            - unmatched_patterns(list of dicts with pattern, reason, sla value)
            - all_matched       (bool)
    """
    config = load_sla_config()
    profile = _find_sla_profile(config, sla_id)

    # Profile not found — report and return early
    if profile is None:
        available = [p.get("id") for p in config.get("slas", [])]
        await ctx.info(
            f"SLA Validation: Profile '{sla_id}' not found in {SLA_CONFIG_FILENAME}. "
            f"Available profiles: {available}"
        )
        return {
            "sla_id": sla_id,
            "total_patterns": 0,
            "matched_patterns": [],
            "unmatched_patterns": [],
            "all_matched": False,
            "error": f"Profile '{sla_id}' not found",
        }

    overrides = profile.get("api_overrides", [])

    # No overrides defined — nothing to validate
    if not overrides:
        return {
            "sla_id": sla_id,
            "total_patterns": 0,
            "matched_patterns": [],
            "unmatched_patterns": [],
            "all_matched": True,
        }

    matched_patterns: List[Dict[str, Any]] = []
    unmatched_patterns: List[Dict[str, Any]] = []

    for override in overrides:
        pattern = override["pattern"]
        matching_labels = [lbl for lbl in labels if fnmatch.fnmatch(lbl, pattern)]

        if matching_labels:
            matched_patterns.append({
                "pattern": pattern,
                "matched_labels_count": len(matching_labels),
                "sample_labels": matching_labels[:5],
            })
        else:
            unmatched_patterns.append({
                "pattern": pattern,
                "reason": override.get("reason", ""),
                "response_time_sla_ms": override.get("response_time_sla_ms"),
            })

    # ----- Report unmatched patterns ----------------------------------------
    if unmatched_patterns:
        pattern_lines = "\n".join(
            f"  - Pattern: '{p['pattern']}' (SLA: {p['response_time_sla_ms']}ms)"
            for p in unmatched_patterns
        )
        sample_labels = labels[:10]
        label_lines = "\n".join(f"  - {lbl}" for lbl in sample_labels)
        more_suffix = (
            f"\n  ... and {len(labels) - 10} more"
            if len(labels) > 10
            else ""
        )

        await ctx.info(
            f"SLA Validation: {len(unmatched_patterns)} of {len(overrides)} "
            f"api_override pattern(s) in profile '{sla_id}' did not match "
            f"any test result labels.\n"
            f"The profile's default SLA will be used for unmatched APIs.\n\n"
            f"Unmatched patterns:\n{pattern_lines}\n\n"
            f"Sample labels from test results:\n{label_lines}{more_suffix}\n\n"
            f"To fix: update the patterns in {SLA_CONFIG_FILENAME} to match "
            f"your test labels, then re-run the analysis."
        )

    # ----- Report matched patterns ------------------------------------------
    if matched_patterns:
        matched_lines = "\n".join(
            f"  - Pattern: '{p['pattern']}' matched {p['matched_labels_count']} label(s)"
            for p in matched_patterns
        )
        await ctx.info(
            f"SLA Validation: {len(matched_patterns)} of {len(overrides)} "
            f"api_override pattern(s) in profile '{sla_id}' matched test "
            f"result labels.\n\n"
            f"Matched patterns:\n{matched_lines}"
        )

    return {
        "sla_id": sla_id,
        "total_patterns": len(overrides),
        "matched_patterns": matched_patterns,
        "unmatched_patterns": unmatched_patterns,
        "all_matched": len(unmatched_patterns) == 0,
    }
