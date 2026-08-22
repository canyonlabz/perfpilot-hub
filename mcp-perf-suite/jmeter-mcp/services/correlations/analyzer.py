"""
Main correlation analysis orchestrator.

Coordinates the three phases of correlation analysis:
- Phase 1: Source extraction (extractors.py)
- Phase 2: Usage detection (matchers.py)
- Phase 3: Orphan ID detection (matchers.py)
- Phase 1.5: Parameterization strategy (classifiers.py)
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

from fastmcp import Context

from utils.config import load_config, load_jmeter_config
from utils.file_utils import get_jmeter_artifacts_dir, save_correlation_spec, save_json_file

from .classifiers import classify_parameterization_strategy
from .constants import SKIP_VALUES
from .extractors import (
    detect_static_api_key_headers,
    detect_token_exchanges,
    extract_oauth_from_request_headers,
    extract_oauth_params_from_request_body,
    extract_oauth_params_from_request_urls,
    extract_sources_multi,
)
from .matchers import detect_orphan_ids, detect_progressive_auth_tokens, find_usages, resolve_best_source
from .naming import (
    generate_correlation_naming_entry,
    generate_variable_name,
    reset_name_counter,
)
from .utils import init_exclude_domains, is_excluded_url


# Parameterization hints for request-side OAuth/PKCE candidates by value_type.
# These guide downstream JMX generation on how to handle each parameter.
_REQUEST_SIDE_STRATEGIES: Dict[str, Dict[str, str]] = {
    "pkce_code_verifier": {
        "strategy": "pkce_preprocessor",
        "reason": "PKCE value - generate via JSR223 PreProcessor",
    },
    "pkce_code_challenge": {
        "strategy": "pkce_preprocessor",
        "reason": "PKCE value - generate via JSR223 PreProcessor",
    },
    "pkce_code_challenge_method": {
        "strategy": "pkce_preprocessor",
        "reason": "PKCE value - set alongside code_challenge",
    },
    "oauth_code": {
        "strategy": "infer_from_prior_response",
        "reason": "Auth code from redirect - extract from prior Location header or form_post",
    },
    "oauth_subject_token": {
        "strategy": "infer_from_prior_response",
        "reason": "Token from prior /oauth/token response - extract $.access_token",
    },
    "oauth_refresh_token": {
        "strategy": "infer_from_prior_response",
        "reason": "Refresh token from prior /oauth/token response",
    },
    "sso_nonce": {
        "strategy": "extract_and_reuse",
        "reason": "Nonce from prior Set-Cookie response header",
    },
    "sso_token": {
        "strategy": "infer_from_prior_response",
        "reason": "SSO token from prior authentication response",
    },
    "csrf_token": {
        "strategy": "extract_and_reuse",
        "reason": "CSRF token from prior response",
    },
    "oauth_state": {
        "strategy": "infer_from_prior_response",
        "reason": "Generated state param - extract from prior response or SDK",
    },
    "oauth_nonce": {
        "strategy": "infer_from_prior_response",
        "reason": "Generated nonce param - extract from prior response or SDK",
    },
    "oauth_token": {
        "strategy": "infer_from_prior_response",
        "reason": "Token from prior OAuth response",
    },
    "oauth_client_id": {
        "strategy": "user_defined_variable",
        "reason": "Static per environment - parameterize via UDV",
    },
    "oauth_redirect_uri": {
        "strategy": "user_defined_variable",
        "reason": "Static per environment - parameterize via UDV",
    },
    "oauth_scope": {
        "strategy": "user_defined_variable",
        "reason": "Static per environment - parameterize via UDV",
    },
    "oauth_response_type": {
        "strategy": "user_defined_variable",
        "reason": "Static per flow - parameterize via UDV",
    },
    "oauth_response_mode": {
        "strategy": "user_defined_variable",
        "reason": "Static per flow - parameterize via UDV",
    },
    "oauth_client_secret": {
        "strategy": "user_defined_variable",
        "reason": "Static secret - parameterize via UDV or CSV",
    },
    "oauth_assertion": {
        "strategy": "infer_from_prior_response",
        "reason": "Assertion token from prior authentication step",
    },
}

_REQUEST_SIDE_DEFAULT_STRATEGY: Dict[str, str] = {
    "strategy": "user_defined_variable",
    "reason": "OAuth parameter - parameterize via UDV or extract from prior response",
}


# === Configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
JMETER_CONFIG = load_jmeter_config()

# Initialize domain exclusion from config
init_exclude_domains(CONFIG)


# === File Loading ===

def _get_network_capture_path(run_id: str) -> str:
    """Resolve path to network capture JSON for given run_id."""
    base_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter", "network-capture")
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(f"Network capture directory not found: {base_dir}")

    candidates = [
        os.path.join(base_dir, f)
        for f in os.listdir(base_dir)
        if f.lower().startswith("network_capture_") and f.lower().endswith(".json")
    ]
    if not candidates:
        raise FileNotFoundError(f"No network capture JSON files found in: {base_dir}")

    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def _load_network_data(path: str) -> Dict[str, Any]:
    """Load and parse network capture JSON."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_entries(
    network_data: Dict[str, Any],
    exclude_domains: bool = True
) -> List[Tuple[int, int, str, Dict[str, Any]]]:
    """
    Flatten step-grouped data into ordered list of (entry_index, step_number, step_label, entry).
    
    Args:
        network_data: The loaded network capture data
        exclude_domains: If True, filter out entries matching excluded domains (APM, analytics, etc.)
    
    Returns entries in sequential order with global entry_index for forward-only searching.
    """
    flattened: List[Tuple[int, int, str, Dict[str, Any]]] = []
    excluded_count = 0
    global_index = 0
    
    for step_label, entries in network_data.items():
        for entry in entries:
            # Filter out excluded domains (APM, analytics, etc.)
            if exclude_domains:
                url = entry.get("url", "")
                if is_excluded_url(url):
                    excluded_count += 1
                    continue
            
            step_meta = entry.get("step") or {}
            step_number = step_meta.get("step_number")
            if step_number is None:
                match = re.match(r"Step\s+(\d+)", step_label)
                step_number = int(match.group(1)) if match else 0
            flattened.append((global_index, step_number, step_label, entry))
            global_index += 1
    
    if excluded_count > 0:
        print(f"[INFO] Excluded {excluded_count} entries from non-essential domains (APM, analytics, etc.)")

    # Sort by step_number, preserving order within steps
    flattened.sort(key=lambda x: (x[1], x[0]))
    
    # Re-index after sorting
    return [(i, sn, sl, e) for i, (_, sn, sl, e) in enumerate(flattened)]


# === Main Correlation Logic ===

def _find_correlations(network_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Main correlation detection driver.
    
    Orchestrates:
    1. Phase 1: Extract sources from responses
    2. Phase 2: Find usages for each source
    3. Phase 3: Detect orphan IDs
    4. Phase 1.5: Classify parameterization strategies
    
    Returns:
        Tuple of (correlations list, summary dict)
    """
    entries = _iter_entries(network_data)
    if not entries:
        return [], {}
    
    # Phase 1: Extract sources from responses (multi-source aware)
    # Preserves all source candidates per value so ambiguous values
    # (e.g. countryId=10 vs stateId=10) can be resolved correctly.
    source_groups = extract_sources_multi(entries)

    # Track known source values for orphan detection
    known_source_values: Set[str] = set(source_groups.keys())

    correlations: List[Dict[str, Any]] = []
    correlation_counter = 0

    # Phase 2: Find usages and resolve best source for ambiguous values
    for value_str, sources in source_groups.items():
        # Find usages forward from the earliest source for this value
        earliest = min(sources, key=lambda s: s.get("entry_index", 0))
        all_usages = find_usages(earliest, entries)

        # Check for OAuth form_post tokens (always emit, even without usages)
        form_post_source = next(
            (s for s in sources
             if s.get("source_location") == "response_html_form"
             and s.get("source_key", "").lower() in {"id_token", "code", "access_token"}),
            None,
        )

        if not all_usages and not form_post_source:
            continue

        # Special case: form_post token without detected usages
        if not all_usages and form_post_source:
            correlation_counter += 1
            correlation = {
                "correlation_id": f"corr_{correlation_counter}",
                "type": form_post_source.get("candidate_type", "unknown"),
                "value_type": form_post_source.get("value_type", "unknown"),
                "confidence": "medium",
                "correlation_found": False,
                "source": {
                    "step_number": form_post_source.get("step_number"),
                    "step_label": form_post_source.get("step_label"),
                    "entry_index": form_post_source.get("entry_index"),
                    "request_id": form_post_source.get("request_id"),
                    "request_method": form_post_source.get("request_method"),
                    "request_url": form_post_source.get("request_url"),
                    "response_status": form_post_source.get("response_status"),
                    "source_location": form_post_source.get("source_location"),
                    "source_key": form_post_source.get("source_key"),
                    "source_json_path": form_post_source.get("source_json_path"),
                    "response_example_value": form_post_source.get("value"),
                },
                "usages": [],
                "parameterization_hint": {
                    "strategy": "extract_for_bearer",
                    "extractor_type": "regex",
                    "reason": "OAuth form_post token - typically used as Authorization: Bearer header",
                },
                "notes": (
                    "OAuth token extracted from form_post response. "
                    "Typically used in subsequent requests as 'Authorization: Bearer {token}' header. "
                    "Usage not detected in captured traffic - verify and parameterize manually if needed."
                ),
            }
            if form_post_source.get("suggested_var_name"):
                correlation["suggested_var_name"] = form_post_source["suggested_var_name"]
            correlations.append(correlation)
            continue

        # Resolve best source per usage and group by resolved source.
        # For values with a single source this is a no-op pass-through;
        # for ambiguous values it applies field-name affinity.
        source_usage_map: Dict[int, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
        for usage in all_usages:
            # Only consider sources chronologically before this usage
            prior = [
                s for s in sources
                if s.get("entry_index", 0) < usage.get("entry_index", float("inf"))
            ]
            if not prior:
                prior = sources
            best = resolve_best_source(prior, usage)
            src_key = id(best)
            if src_key not in source_usage_map:
                source_usage_map[src_key] = (best, [])
            source_usage_map[src_key][1].append(usage)

        # Emit one correlation per resolved source
        for _src_key, (candidate, grouped_usages) in source_usage_map.items():
            # Re-number usages sequentially within this correlation
            for i, u in enumerate(grouped_usages, 1):
                u["usage_number"] = i

            correlation_counter += 1

            extractor_type = "regex"
            if candidate.get("source_location") == "response_json":
                extractor_type = "json"

            param_hint = classify_parameterization_strategy(True, len(grouped_usages))
            param_hint["extractor_type"] = extractor_type

            correlation = {
                "correlation_id": f"corr_{correlation_counter}",
                "type": candidate.get("candidate_type", "unknown"),
                "value_type": candidate.get("value_type", "unknown"),
                "confidence": "high",
                "correlation_found": True,
                "source": {
                    "step_number": candidate.get("step_number"),
                    "step_label": candidate.get("step_label"),
                    "entry_index": candidate.get("entry_index"),
                    "request_id": candidate.get("request_id"),
                    "request_method": candidate.get("request_method"),
                    "request_url": candidate.get("request_url"),
                    "response_status": candidate.get("response_status"),
                    "source_location": candidate.get("source_location"),
                    "source_key": candidate.get("source_key"),
                    "source_json_path": candidate.get("source_json_path"),
                    "response_example_value": candidate.get("value"),
                },
                "usages": grouped_usages,
                "parameterization_hint": param_hint,
            }

            if candidate.get("suggested_var_name"):
                correlation["suggested_var_name"] = candidate["suggested_var_name"]

            correlations.append(correlation)
    
    # Phase 1b: Request-side OAuth/PKCE extraction
    # Detects OAuth parameters in request URLs, POST bodies, and custom headers
    # whose response-side source may be missing from the capture.
    request_side_candidates: List[Dict[str, Any]] = []
    request_side_candidates.extend(extract_oauth_params_from_request_urls(entries))
    request_side_candidates.extend(extract_oauth_params_from_request_body(entries))
    request_side_candidates.extend(extract_oauth_from_request_headers(entries))

    # Dedup: skip values already covered by response-side extraction
    request_side_seen: Set[str] = set()
    for candidate in request_side_candidates:
        value_str = str(candidate.get("value", ""))
        if not value_str or value_str in known_source_values or value_str in request_side_seen:
            continue
        request_side_seen.add(value_str)

        # Prevent Phase 3 orphan detection from re-flagging this value
        known_source_values.add(value_str)

        correlation_counter += 1
        correlation_id = f"corr_{correlation_counter}"

        value_type = candidate.get("value_type", "oauth_param")
        param_hint = dict(_REQUEST_SIDE_STRATEGIES.get(
            value_type, _REQUEST_SIDE_DEFAULT_STRATEGY
        ))

        correlation = {
            "correlation_id": correlation_id,
            "type": candidate.get("candidate_type", "oauth_param"),
            "value_type": value_type,
            "confidence": "medium",
            "correlation_found": False,
            "source": {
                "step_number": candidate.get("step_number"),
                "step_label": candidate.get("step_label"),
                "entry_index": candidate.get("entry_index"),
                "request_id": candidate.get("request_id"),
                "request_method": candidate.get("request_method"),
                "request_url": candidate.get("request_url"),
                "response_status": candidate.get("response_status"),
                "source_location": candidate.get("source_location"),
                "source_key": candidate.get("source_key"),
                "source_json_path": candidate.get("source_json_path"),
                "response_example_value": candidate.get("value"),
            },
            "usages": [],
            "parameterization_hint": param_hint,
        }

        # Preserve grant-type metadata from POST body extraction
        if candidate.get("detected_grant_type"):
            correlation["detected_grant_type"] = candidate["detected_grant_type"]
            correlation["detected_flow"] = candidate.get("detected_flow")

        correlations.append(correlation)

    # Phase 1c: Token chain analysis
    # Detects sequential /oauth/token exchanges and links each token-exchange
    # request's subject_token to the inferred $.access_token from the prior
    # exchange's response (which is typically empty in browser captures).
    token_exchanges = detect_token_exchanges(entries)
    token_chain_count = 0

    for ex in token_exchanges:
        if not ex["has_subject_token"] or ex["sequence_position"] == 0:
            continue

        prior_ex = token_exchanges[ex["sequence_position"] - 1]
        correlation_counter += 1
        correlation_id = f"corr_{correlation_counter}"

        prior_url = prior_ex["request_url"]
        prior_flow = prior_ex["detected_flow"]
        cur_flow = ex["detected_flow"]

        notes = (
            f"Token chain: subject_token in this {cur_flow} request "
            f"should be extracted from the prior /oauth/token response "
            f"(entry {prior_ex['entry_index']}, {prior_flow} grant). "
            f"Inferred JSONPath: $.access_token"
        )

        correlation = {
            "correlation_id": correlation_id,
            "type": "token_chain",
            "value_type": "oauth_subject_token",
            "confidence": "medium",
            "correlation_found": False,
            "source": {
                "step_number": prior_ex["step_number"],
                "step_label": prior_ex["step_label"],
                "entry_index": prior_ex["entry_index"],
                "request_id": None,
                "request_method": "POST",
                "request_url": prior_url,
                "response_status": None,
                "source_location": "inferred_response_json",
                "source_key": "access_token",
                "source_json_path": "$.access_token",
                "response_example_value": None,
            },
            "target": {
                "entry_index": ex["entry_index"],
                "step_number": ex["step_number"],
                "step_label": ex["step_label"],
                "request_url": ex["request_url"],
                "grant_type": ex["grant_type_raw"],
                "detected_flow": cur_flow,
            },
            "usages": [],
            "parameterization_hint": {
                "strategy": "infer_from_prior_response",
                "inferred_json_path": "$.access_token",
                "reason": (
                    f"Extract $.access_token from prior token endpoint response "
                    f"(entry {prior_ex['entry_index']}) and use as subject_token"
                ),
            },
            "notes": notes,
            "token_chain_position": ex["sequence_position"],
        }

        correlations.append(correlation)
        token_chain_count += 1

    # Phase 1d: Static API key header detection
    # Detects headers like ocp-apim-subscription-key, x-api-key, etc.
    # that carry a single consistent value across all requests and should
    # be parameterized via User Defined Variables.
    static_key_headers = detect_static_api_key_headers(entries)

    for key_info in static_key_headers:
        header_value = key_info["value"]
        # Prevent Phase 3 from re-flagging this value as an orphan
        known_source_values.add(header_value)

        correlation_counter += 1
        correlation_id = f"corr_{correlation_counter}"

        # Derive a variable name from the header name (e.g. my-api-key -> my_api_key)
        var_name = key_info["header_name"].lower().replace("-", "_")

        correlation = {
            "correlation_id": correlation_id,
            "type": "static_header",
            "value_type": "api_key",
            "confidence": "high",
            "correlation_found": True,
            "source": {
                "step_number": key_info["first_step_number"],
                "step_label": key_info["first_step_label"],
                "entry_index": key_info["first_entry_index"],
                "request_id": None,
                "request_method": None,
                "request_url": key_info["first_request_url"],
                "response_status": None,
                "source_location": "request_header",
                "source_key": key_info["header_name"],
                "source_json_path": None,
                "response_example_value": header_value,
            },
            "usages": [],
            "parameterization_hint": {
                "strategy": "user_defined_variable",
                "suggested_var_name": var_name,
                "reason": (
                    f"Static API key header '{key_info['header_name']}' "
                    f"found in {key_info['occurrence_count']} request(s) with same value"
                ),
            },
            "notes": (
                f"Static API key detected: {key_info['header_name']} appears in "
                f"{key_info['occurrence_count']} request(s). Add to User Defined Variables "
                f"as '{var_name}' and substitute with ${{{var_name}}} in headers."
            ),
        }

        correlations.append(correlation)

    # Phase 3: Detect orphan IDs
    orphan_candidates = detect_orphan_ids(entries, known_source_values)
    
    for orphan in orphan_candidates:
        # Count occurrences of this value across all entries
        value_str = str(orphan["value"])
        if value_str.strip().lower() in SKIP_VALUES:
            continue
        occurrence_count = 1
        for _, _, _, entry in entries:
            url = entry.get("url", "")
            post_data = entry.get("post_data") or ""
            if value_str in url or value_str in post_data:
                occurrence_count += 1
        
        correlation_counter += 1
        correlation_id = f"corr_{correlation_counter}"
        
        orphan_value_type = orphan.get("value_type", "unknown")

        if orphan_value_type == "timestamp":
            param_hint = {
                "strategy": "timestamp_preprocessor",
                "reason": "Epoch millisecond timestamp - generate via JSR223 PreProcessor",
            }
            notes = (
                "Epoch millisecond timestamp (e.g. SignalR cache-busting). "
                "Use create_timestamp_preprocessor() to generate dynamically."
            )
        else:
            param_hint = classify_parameterization_strategy(False, occurrence_count)
            notes = (
                "Value found in request URL but not in any prior response. "
                "Recommend parameterization via CSV Data Set or User Defined Variables."
            )

        correlation = {
            "correlation_id": correlation_id,
            "type": "orphan_id",
            "value_type": orphan_value_type,
            "confidence": "low",
            "correlation_found": False,
            "source": {
                "step_number": orphan.get("step_number"),
                "step_label": orphan.get("step_label"),
                "entry_index": orphan.get("entry_index"),
                "request_id": orphan.get("request_id"),
                "request_method": orphan.get("request_method"),
                "request_url": orphan.get("request_url"),
                "response_status": None,
                "source_location": orphan.get("source_location"),
                "source_key": orphan.get("source_key"),
                "source_json_path": None,
                "response_example_value": orphan.get("value"),
            },
            "usages": [],
            "parameterization_hint": param_hint,
            "notes": notes,
        }
        
        correlations.append(correlation)

    # Phase 3b: Detect progressive auth tokens (JWTs in POST bodies with empty responses)
    progressive_orphans = detect_progressive_auth_tokens(entries, known_source_values)

    for orphan in progressive_orphans:
        correlation_counter += 1
        correlation_id = f"corr_{correlation_counter}"

        correlation = {
            "correlation_id": correlation_id,
            "type": "progressive_auth",
            "value_type": "progressive_auth",
            "confidence": "low",
            "correlation_found": False,
            "source": {
                "step_number": orphan.get("step_number"),
                "step_label": orphan.get("step_label"),
                "entry_index": orphan.get("entry_index"),
                "request_id": orphan.get("request_id"),
                "request_method": orphan.get("request_method"),
                "request_url": orphan.get("request_url"),
                "response_status": None,
                "source_location": orphan.get("source_location"),
                "source_key": orphan.get("source_key"),
                "source_json_path": None,
                "response_example_value": "[REDACTED_JWT]",
            },
            "usages": [],
            "parameterization_hint": {
                "strategy": "extract_and_reuse",
                "reason": (
                    "Progressive auth token — response body was likely empty in capture. "
                    "Add a RegEx extractor on the prior step's response to capture this JWT."
                ),
            },
            "notes": orphan.get("note", ""),
        }

        correlations.append(correlation)

    # Build summary
    summary = {
        "total_correlations": len(correlations),
        "business_ids": sum(1 for c in correlations if c.get("type") == "business_id"),
        "correlation_ids": sum(1 for c in correlations if c.get("type") == "correlation_id"),
        "oauth_params": sum(1 for c in correlations if c.get("type") == "oauth_param"),
        "pkce_params": sum(1 for c in correlations if c.get("type") == "pkce_param"),
        "token_chains": sum(1 for c in correlations if c.get("type") == "token_chain"),
        "static_headers": sum(1 for c in correlations if c.get("type") == "static_header"),
        "orphan_ids": sum(1 for c in correlations if c.get("type") == "orphan_id"),
        "progressive_auth": sum(1 for c in correlations if c.get("type") == "progressive_auth"),
        "high_confidence": sum(1 for c in correlations if c.get("confidence") == "high"),
        "medium_confidence": sum(1 for c in correlations if c.get("confidence") == "medium"),
        "low_confidence": sum(1 for c in correlations if c.get("confidence") == "low"),
    }

    return correlations, summary


# === Public API ===

async def analyze_traffic(test_run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Main entry point for the analyze_network_traffic MCP tool.
    
    Analyzes network capture for correlations and writes correlation_spec.json.
    
    Args:
        test_run_id: Unique identifier for the test run.
        ctx: FastMCP context for logging.
    
    Returns:
        dict with status, message, correlation_spec_path, count, and summary.
    """
    try:
        capture_path = _get_network_capture_path(test_run_id)
    except FileNotFoundError as e:
        msg = str(e)
        await ctx.error(msg)
        return {
            "status": "ERROR",
            "message": msg,
            "test_run_id": test_run_id,
            "correlation_spec_path": None,
            "count": 0,
            "summary": {},
        }

    try:
        network_data = _load_network_data(capture_path)
        await ctx.info(f"Loaded network capture: {capture_path}")

        correlations, summary = _find_correlations(network_data)

        correlation_spec = {
            "capture_file": os.path.basename(capture_path),
            "application": JMETER_CONFIG.get("application_name", "unknown"),
            "spec_version": "2.0",
            "analyzer_version": "0.2.0",
            "analysis_timestamp": datetime.utcnow().isoformat(),
            "total_steps": len(network_data),
            "total_entries": sum(len(v) for v in network_data.values()),
            "correlations": correlations,
            "summary": summary,
        }

        output_path = save_correlation_spec(test_run_id, correlation_spec)
        msg = f"Correlation analysis complete: {len(correlations)} correlations found"
        await ctx.info(f"{msg}: {output_path}")

        # Phase 4: Algorithmic naming → write correlation_naming.json
        reset_name_counter()
        naming_variables = []
        naming_orphans = []

        for corr in correlations:
            source = corr.get("source", {}) or {}
            is_static_header = corr.get("type") == "static_header"
            if corr.get("correlation_found") and not is_static_header:
                entry = generate_correlation_naming_entry(corr)
                entry["jmeter_scope"] = "thread"
                naming_variables.append(entry)
            else:
                var_name = generate_variable_name(
                    source_key=source.get("source_key"),
                    value_type=corr.get("value_type"),
                    source_location=source.get("source_location"),
                    request_url=source.get("request_url"),
                )
                strategy = corr.get("parameterization_hint", {}).get(
                    "strategy", "user_defined_variable"
                )
                orphan_entry = {
                    "correlation_id": corr.get("correlation_id"),
                    "variable_name": var_name,
                    "parameterization_strategy": strategy,
                    "source_request_id": source.get("request_id"),
                }
                if corr.get("type") == "progressive_auth":
                    orphan_entry["note"] = corr.get("notes", "")
                naming_orphans.append(orphan_entry)

        naming_output = {
            "application": JMETER_CONFIG.get("application_name", "unknown"),
            "spec_version": "2.0",
            "naming_conventions": {"case_style": "snake_case"},
            "variables": naming_variables,
            "orphan_variables": naming_orphans,
        }

        naming_dir = get_jmeter_artifacts_dir(test_run_id)
        naming_path = os.path.join(naming_dir, "correlation_naming.json")
        save_json_file(naming_path, naming_output)
        await ctx.info(f"Correlation naming generated: {naming_path}")

        return {
            "status": "OK",
            "message": msg,
            "test_run_id": test_run_id,
            "correlation_spec_path": output_path,
            "correlation_naming_path": naming_path,
            "count": len(correlations),
            "summary": summary,
        }

    except Exception as e:
        msg = f"Error during correlation analysis: {e}"
        await ctx.error(msg)
        return {
            "status": "ERROR",
            "message": msg,
            "test_run_id": test_run_id,
            "correlation_spec_path": None,
            "count": 0,
            "summary": {},
        }
