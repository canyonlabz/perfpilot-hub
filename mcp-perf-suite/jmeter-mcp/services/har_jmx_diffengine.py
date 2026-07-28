"""
services/har_jmx_diffengine.py

Diff engine that cross-compares a HAR file against an existing JMeter JMX
script to identify API changes requiring script updates.

This module is diagnostic only — it identifies what changed and produces a report.
It does NOT modify the JMX. Actual fixes are applied via the existing
edit_jmeter_component / add_jmeter_component HITL tools.

Phases:
  A — Parse & extract (HAR entries + JMX samplers)
  B — Multi-pass matching algorithm
  C — Difference analysis
  D — Report generation (JSON + Markdown)
"""

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from services import network_capture
from services.jmx_editor import load_jmx, build_node_index, discover_jmx_file
from utils.config import load_config

logger = logging.getLogger(__name__)

CONFIG = load_config()
NETWORK_CAPTURE_CONFIG = CONFIG.get("network_capture", {})

_BINARY_MIME_PREFIXES = (
    "image/",
    "font/",
    "video/",
    "audio/",
    "application/octet-stream",
)

_MAX_HAR_FILE_SIZE_BYTES = 200 * 1024 * 1024
_WARN_HAR_FILE_SIZE_BYTES = 50 * 1024 * 1024


def _get_comparison_config() -> dict:
    """Load har_jmx_comparison config with safe defaults."""
    try:
        cfg = load_config()
        return cfg.get("har_jmx_comparison", {})
    except Exception:
        return {}


def _get_schema_depth() -> int:
    return _get_comparison_config().get("schema_comparison_depth", 3)


# ============================================================
# Phase A — HAR Extraction
# ============================================================

def load_and_validate_har(har_path: str) -> Dict[str, Any]:
    """
    Load and validate a HAR file, returning the parsed JSON dict.

    Replicates the same validation logic as har_adapter._load_har_file
    without importing the private function.

    Raises:
        FileNotFoundError: If HAR file does not exist.
        ValueError: If file exceeds size limit or is not valid HAR JSON.
    """
    if not os.path.isfile(har_path):
        raise FileNotFoundError(f"HAR file not found: {har_path}")

    file_size = os.path.getsize(har_path)

    if file_size > _MAX_HAR_FILE_SIZE_BYTES:
        raise ValueError(
            f"HAR file too large ({file_size / (1024 * 1024):.1f} MB). "
            f"Maximum supported size is {_MAX_HAR_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB."
        )

    if file_size > _WARN_HAR_FILE_SIZE_BYTES:
        logger.warning(
            "Large HAR file: %.1f MB — parsing may take a moment",
            file_size / (1024 * 1024),
        )

    try:
        with open(har_path, "r", encoding="utf-8", errors="replace") as f:
            har_data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in HAR file: {exc}") from exc

    if not isinstance(har_data, dict) or "log" not in har_data:
        raise ValueError(
            "Invalid HAR structure: top-level object must contain a 'log' key"
        )

    log_obj = har_data["log"]
    if "entries" not in log_obj:
        raise ValueError(
            "Invalid HAR structure: 'log' object must contain an 'entries' array"
        )

    return har_data


def _should_include_har_entry(entry: Dict, config: Dict) -> bool:
    """
    Determine if a HAR entry should be included in the comparison.

    Applies the same filtering logic as har_adapter._should_include_entry:
    skip OPTIONS, non-HTTP, failed/aborted, binary MIME types, and
    config-driven domain/path exclusions.
    """
    request = entry.get("request", {})
    response = entry.get("response", {})

    method = (request.get("method") or "").upper()
    if method == "OPTIONS":
        return False

    url = request.get("url", "")
    if not url:
        return False

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False

    status = response.get("status", 0)
    if status in (0, -1):
        return False

    content = response.get("content", {})
    mime_type = (content.get("mimeType") or "").lower()
    for prefix in _BINARY_MIME_PREFIXES:
        if mime_type.startswith(prefix):
            return False

    try:
        if not network_capture.should_capture_url(url, config):
            return False
    except Exception:
        pass

    return True


def _extract_json_schema(data: Any, max_depth: int, current_depth: int = 0) -> Any:
    """
    Extract the key structure of a JSON value up to max_depth levels.

    Returns a schema-like representation:
    - dict  -> {key: <nested schema>, ...}
    - list  -> [<schema of first element>] (or [] for empty lists)
    - str/int/float/bool/None -> the type name as a string
    """
    if current_depth >= max_depth:
        return "..."

    if isinstance(data, dict):
        return {
            k: _extract_json_schema(v, max_depth, current_depth + 1)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        if data:
            return [_extract_json_schema(data[0], max_depth, current_depth + 1)]
        return []
    elif isinstance(data, bool):
        return "bool"
    elif isinstance(data, int):
        return "int"
    elif isinstance(data, float):
        return "float"
    elif isinstance(data, str):
        return "string"
    elif data is None:
        return "null"
    return "unknown"


def _parse_body_schema(
    body_text: Optional[str],
    content_type: str,
    max_depth: int,
) -> Any:
    """
    Parse a request or response body into a schema representation.

    Returns:
    - JSON key schema (dict) if Content-Type is JSON and body parses
    - "form-encoded" if Content-Type indicates form data
    - "raw" for everything else or if parsing fails
    - None if body is empty
    """
    if not body_text or not body_text.strip():
        return None

    ct_lower = content_type.lower()

    if "application/json" in ct_lower or "+json" in ct_lower:
        try:
            parsed = json.loads(body_text)
            return _extract_json_schema(parsed, max_depth)
        except (json.JSONDecodeError, TypeError):
            return "raw"

    if "application/x-www-form-urlencoded" in ct_lower:
        return "form-encoded"

    if "multipart/form-data" in ct_lower:
        return "form-encoded"

    return "raw"


def extract_har_entries(har_path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Parse a HAR file and extract comparison-relevant fields from each entry.

    Returns:
        Tuple of (entries, metadata) where:
        - entries: list of normalized entry dicts
        - metadata: dict with har_file, entries_total, entries_after_filter
    """
    har_data = load_and_validate_har(har_path)
    raw_entries = har_data["log"].get("entries", [])
    entries_total = len(raw_entries)

    capture_config = dict(NETWORK_CAPTURE_CONFIG)
    schema_depth = _get_schema_depth()

    extracted: List[Dict[str, Any]] = []

    for idx, entry in enumerate(raw_entries):
        if not _should_include_har_entry(entry, capture_config):
            continue

        request = entry.get("request", {})
        response = entry.get("response", {})

        url = request.get("url", "")
        parsed_url = urlparse(url)
        url_path = parsed_url.path.rstrip("/").lower() or "/"

        method = (request.get("method") or "GET").upper()

        query_params = sorted(set(
            p.get("name", "") for p in request.get("queryString", [])
            if p.get("name")
        ))

        req_headers = {}
        for h in request.get("headers", []):
            name = (h.get("name") or "").lower()
            if name in ("content-type", "authorization", "accept"):
                req_headers[name] = h.get("value", "")

        request_content_type = req_headers.get("content-type", "")
        post_data_text = request.get("postData", {}).get("text", "")
        request_body_schema = _parse_body_schema(
            post_data_text, request_content_type, schema_depth,
        )

        response_status = response.get("status", 0)

        resp_content = response.get("content", {})
        resp_content_type = (resp_content.get("mimeType") or "").lower()
        resp_body_text = resp_content.get("text", "")
        response_body_schema = _parse_body_schema(
            resp_body_text, resp_content_type, schema_depth,
        )

        extracted.append({
            "method": method,
            "url": url,
            "url_path": url_path,
            "query_params": query_params,
            "request_headers": req_headers,
            "request_body_schema": request_body_schema,
            "response_status": response_status,
            "response_body_schema": response_body_schema,
            "har_entry_index": idx,
        })

    metadata = {
        "har_file": os.path.basename(har_path),
        "har_entries_total": entries_total,
        "har_entries_after_filter": len(extracted),
    }

    logger.info(
        "HAR extraction: %d total entries, %d after filtering",
        entries_total, len(extracted),
    )

    return extracted, metadata


# ============================================================
# Phase A — JMX Extraction
# ============================================================

_EXTRACTOR_TYPES = {
    "JSONPostProcessor",
    "RegexExtractor",
    "BoundaryExtractor",
    "XPathExtractor",
    "XPath2Extractor",
    "HtmlExtractor",
}

_ASSERTION_TYPES = {
    "ResponseAssertion",
    "JSONPathAssertion",
    "DurationAssertion",
    "SizeAssertion",
}

_VAR_PLACEHOLDER_RE = re.compile(r'\$\{([^}]+)\}')
_NAME_PLACEHOLDER_RE = re.compile(r'\{([^}]+)\}|\{\{([^}]+)\}\}')


def _extract_request_body(elem: ET.Element) -> Optional[str]:
    """
    Extract the raw request body from an HTTPSamplerProxy XML element.

    JMeter stores the body under:
      elementProp[name='HTTPsampler.Arguments'] >
        collectionProp > elementProp > stringProp[name='Argument.value']
    """
    args_prop = None
    for child in elem:
        if child.tag == "elementProp" and child.get("name") in (
            "HTTPsampler.Arguments", "HTTPSampler.Arguments",
        ):
            args_prop = child
            break

    if args_prop is None:
        return None

    for coll in args_prop.iter("collectionProp"):
        for arg_elem in coll.iter("elementProp"):
            for sp in arg_elem:
                if sp.tag == "stringProp" and sp.get("name") == "Argument.value":
                    return sp.text or ""

    return None


def _extract_child_extractors(
    children: list,
    node_index: Dict[str, dict],
) -> List[Dict[str, Any]]:
    """
    Collect extractors from the hierarchy children of an HTTP sampler.

    Returns a list of dicts with extractor metadata.
    """
    extractors: List[Dict[str, Any]] = []
    for child in children:
        if child["type"] in _EXTRACTOR_TYPES:
            nid = child["node_id"]
            info = node_index.get(nid, {})
            props = info.get("props", {})
            extractors.append({
                "node_id": nid,
                "type": child["type"],
                "testname": child["testname"],
                "json_path": props.get("jsonpath", ""),
                "regex": props.get("regex", ""),
                "refname": props.get("refname", ""),
            })
    return extractors


def _extract_child_assertions(
    children: list,
    node_index: Dict[str, dict],
) -> List[Dict[str, Any]]:
    """
    Collect assertions from the hierarchy children of an HTTP sampler.

    Returns a list of dicts with assertion metadata.
    """
    assertions: List[Dict[str, Any]] = []
    for child in children:
        if child["type"] in _ASSERTION_TYPES:
            nid = child["node_id"]
            info = node_index.get(nid, {})
            props = info.get("props", {})
            assertions.append({
                "node_id": nid,
                "type": child["type"],
                "testname": child["testname"],
                "props": props,
            })
    return assertions


def _extract_url_path_from_name(testname: str) -> Optional[str]:
    """
    Extract the URL path fragment from a sampler testname.

    Sampler names typically follow patterns like:
      TC01_TS02_POST_/api/v1/shoppingcart
      TC01_TS02_/api/v1/customer/{customerId}

    Returns the path portion (starting with /) or None if not found.
    """
    match = re.search(r'(/[^\s]+)', testname)
    if match:
        return match.group(1).lower().rstrip("/") or "/"
    return None


def _build_url_regex_from_pattern(url_pattern: str) -> Optional[str]:
    """
    Convert a JMX URL pattern with ${...} placeholders into a regex.

    Example: /api/v1/customer/${customerId} -> ^/api/v1/customer/[^/]+$
    """
    if "${" not in url_pattern:
        return None

    regex_parts = []
    last_end = 0
    for m in _VAR_PLACEHOLDER_RE.finditer(url_pattern):
        regex_parts.append(re.escape(url_pattern[last_end:m.start()]))
        regex_parts.append("[^/]+")
        last_end = m.end()
    regex_parts.append(re.escape(url_pattern[last_end:]))

    return "^" + "".join(regex_parts) + "$"


def _build_url_regex_from_name(url_from_name: str) -> Optional[str]:
    """
    Convert a URL path from sampler name with {param} or {{param}} into a regex.

    Example: /api/v1/customer/{customerId} -> ^/api/v1/customer/[^/]+$
    """
    if "{" not in url_from_name:
        return None

    regex_parts = []
    last_end = 0
    for m in _NAME_PLACEHOLDER_RE.finditer(url_from_name):
        regex_parts.append(re.escape(url_from_name[last_end:m.start()]))
        regex_parts.append("[^/]+")
        last_end = m.end()
    regex_parts.append(re.escape(url_from_name[last_end:]))

    return "^" + "".join(regex_parts) + "$"


def _walk_hierarchy_for_samplers(
    hierarchy: list,
    node_index: Dict[str, dict],
    root: ET.Element,
    parent_controller: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Recursively walk the hierarchy to extract HTTP sampler details.

    Collects sampler metadata including URL patterns, bodies, child
    extractors/assertions, and parent controller context.
    """
    samplers: List[Dict[str, Any]] = []

    for node in hierarchy:
        ntype = node["type"]
        nid = node["node_id"]
        info = node_index.get(nid, {})
        props = info.get("props", {})

        current_controller = parent_controller
        if ntype in ("TransactionController", "GenericController", "LoopController"):
            current_controller = node["testname"]

        if ntype == "HTTPSamplerProxy":
            testname = node["testname"]
            method = props.get("method", "GET").upper()
            url_pattern = props.get("path", "")
            domain = props.get("domain", "")
            url_path_from_name = _extract_url_path_from_name(testname)

            url_regex = _build_url_regex_from_pattern(url_pattern)
            name_regex = _build_url_regex_from_name(
                url_path_from_name
            ) if url_path_from_name else None

            body = None
            elem_result = _find_element_by_node_id_lightweight(root, nid, node_index)
            if elem_result is not None:
                body = _extract_request_body(elem_result)

            children = node.get("children", [])
            extractors = _extract_child_extractors(children, node_index)
            assertions = _extract_child_assertions(children, node_index)

            samplers.append({
                "node_id": nid,
                "testname": testname,
                "enabled": node.get("enabled", True),
                "method": method,
                "url_pattern": url_pattern,
                "url_pattern_normalized": url_pattern.rstrip("/").lower() or "/",
                "url_path_from_name": url_path_from_name,
                "url_regex": url_regex,
                "name_regex": name_regex,
                "domain": domain,
                "request_body": body,
                "parent_controller": current_controller,
                "child_extractors": extractors,
                "child_assertions": assertions,
            })

        if node.get("children"):
            samplers.extend(_walk_hierarchy_for_samplers(
                node["children"], node_index, root, current_controller,
            ))

    return samplers


def _find_element_by_node_id_lightweight(
    root: ET.Element,
    target_node_id: str,
    node_index: Dict[str, dict],
) -> Optional[ET.Element]:
    """
    Lightweight element lookup that returns just the XML element (not the full
    triple) for body extraction. Uses the same walking logic as
    jmx_editor.find_element_by_node_id but only returns the element itself.
    """
    from services.jmx_editor import find_element_by_node_id

    result = find_element_by_node_id(root, target_node_id, node_index)
    if result is not None:
        return result[0]
    return None


def extract_jmx_samplers(
    jmx_path: str,
    jmx_structure_file: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Parse a JMX file and extract HTTP sampler details for comparison.

    If jmx_structure_file is provided and its jmx_last_modified matches the
    JMX file's current mtime, the pre-parsed node_index from the structure
    file is used. Otherwise, the JMX is parsed from scratch.

    Args:
        jmx_path: Absolute path to the JMX file.
        jmx_structure_file: Optional path to a jmx_structure_*.json file.

    Returns:
        Tuple of (samplers, metadata) where:
        - samplers: list of sampler dicts with full comparison context
        - metadata: dict with jmx_file, jmx_samplers_total
    """
    tree, root = load_jmx(jmx_path)

    use_structure_file = False
    if jmx_structure_file and os.path.isfile(jmx_structure_file):
        try:
            with open(jmx_structure_file, "r", encoding="utf-8") as f:
                structure_data = json.load(f)
            jmx_mtime = os.path.getmtime(jmx_path)
            recorded_mtime = structure_data.get("jmx_last_modified", "")
            current_mtime_str = datetime.utcfromtimestamp(jmx_mtime).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if recorded_mtime == current_mtime_str:
                use_structure_file = True
                logger.info("Using pre-parsed structure file: %s", jmx_structure_file)
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read structure file, parsing JMX from scratch")

    node_index, hierarchy = build_node_index(root)

    # Even when using structure file, we rebuild from raw XML to get full
    # props (including body extraction which requires XML element access)
    samplers = _walk_hierarchy_for_samplers(hierarchy, node_index, root)

    metadata = {
        "jmx_file": os.path.basename(jmx_path),
        "jmx_samplers_total": len(samplers),
        "used_structure_file": use_structure_file,
    }

    logger.info(
        "JMX extraction: %d HTTP samplers found in %s",
        len(samplers), os.path.basename(jmx_path),
    )

    return samplers, metadata


# ============================================================
# Phase B — Multi-Pass Matching Algorithm
# ============================================================


def _pass1_exact_match(
    har_entries: List[Dict[str, Any]],
    jmx_samplers: List[Dict[str, Any]],
    matched_har: set,
    matched_jmx: set,
) -> List[Dict[str, Any]]:
    """
    Pass 1 — Exact match on method + URL path.

    Only considers JMX samplers whose url_pattern contains no ${...}
    placeholders. Matching is case-insensitive on the normalized path.
    """
    matches: List[Dict[str, Any]] = []

    static_samplers = [
        s for s in jmx_samplers
        if "${" not in s["url_pattern"] and s["node_id"] not in matched_jmx
    ]

    for h_idx, har in enumerate(har_entries):
        if h_idx in matched_har:
            continue
        for sampler in static_samplers:
            if sampler["node_id"] in matched_jmx:
                continue
            if (
                har["method"] == sampler["method"]
                and har["url_path"] == sampler["url_pattern_normalized"]
            ):
                matches.append(_build_match(
                    har, sampler, h_idx, confidence="high", match_pass=1,
                ))
                matched_har.add(h_idx)
                matched_jmx.add(sampler["node_id"])
                break

    return matches


def _pass2_parameterized_match(
    har_entries: List[Dict[str, Any]],
    jmx_samplers: List[Dict[str, Any]],
    matched_har: set,
    matched_jmx: set,
) -> List[Dict[str, Any]]:
    """
    Pass 2 — Parameterized regex match.

    For JMX samplers with ${...} in the URL pattern (url_regex) or
    {param}/{{param}} in the sampler name (name_regex), match against
    HAR url_path + method.
    """
    matches: List[Dict[str, Any]] = []

    param_samplers = [
        s for s in jmx_samplers
        if (s["url_regex"] or s["name_regex"]) and s["node_id"] not in matched_jmx
    ]

    for h_idx, har in enumerate(har_entries):
        if h_idx in matched_har:
            continue

        candidates: List[Dict[str, Any]] = []
        for sampler in param_samplers:
            if sampler["node_id"] in matched_jmx:
                continue
            if har["method"] != sampler["method"]:
                continue

            matched_via = None
            if sampler["url_regex"]:
                try:
                    if re.match(sampler["url_regex"], har["url_path"], re.IGNORECASE):
                        matched_via = "url_regex"
                except re.error:
                    pass

            if not matched_via and sampler["name_regex"]:
                try:
                    if re.match(sampler["name_regex"], har["url_path"], re.IGNORECASE):
                        matched_via = "name_regex"
                except re.error:
                    pass

            if matched_via:
                candidates.append(sampler)

        if len(candidates) == 1:
            sampler = candidates[0]
            matches.append(_build_match(
                har, sampler, h_idx, confidence="high", match_pass=2,
            ))
            matched_har.add(h_idx)
            matched_jmx.add(sampler["node_id"])
        elif len(candidates) > 1:
            best = _pick_best_parameterized_candidate(har, candidates)
            matches.append(_build_match(
                har, best, h_idx, confidence="medium", match_pass=2,
            ))
            matched_har.add(h_idx)
            matched_jmx.add(best["node_id"])

    return matches


def _pick_best_parameterized_candidate(
    har_entry: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    When multiple JMX samplers match a HAR entry in Pass 2, pick the best.

    Scoring heuristic:
    1. Prefer url_regex match over name_regex (direct URL pattern is stronger)
    2. Prefer samplers with more static segments (more specific pattern)
    3. Fall back to first candidate
    """
    scored = []
    for s in candidates:
        score = 0
        pattern = s["url_pattern_normalized"]
        static_segments = len([
            seg for seg in pattern.split("/") if seg and "${" not in seg
        ])
        score += static_segments * 10
        if s["url_regex"]:
            score += 5
        scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _pass3_fuzzy_segment_match(
    har_entries: List[Dict[str, Any]],
    jmx_samplers: List[Dict[str, Any]],
    matched_har: set,
    matched_jmx: set,
) -> List[Dict[str, Any]]:
    """
    Pass 3 — Path-segment fuzzy match.

    Tokenizes URL paths into segments and scores overlap, allowing for
    version number differences (v1 vs v2). Skipped when strict_matching=True.

    Confidence: "medium" (overlap > 80%), "low" (overlap 50-80%).
    """
    matches: List[Dict[str, Any]] = []

    unmatched_samplers = [
        s for s in jmx_samplers if s["node_id"] not in matched_jmx
    ]

    for h_idx, har in enumerate(har_entries):
        if h_idx in matched_har:
            continue

        har_segments = _tokenize_path(har["url_path"])
        if not har_segments:
            continue

        best_sampler = None
        best_score = 0.0

        for sampler in unmatched_samplers:
            if sampler["node_id"] in matched_jmx:
                continue

            jmx_path = sampler["url_pattern_normalized"]
            if sampler["url_path_from_name"]:
                jmx_path = sampler["url_path_from_name"]

            jmx_segments = _tokenize_path(jmx_path)
            if not jmx_segments:
                continue

            score = _segment_overlap_score(har_segments, jmx_segments)

            if score > best_score and score >= 0.50:
                best_score = score
                best_sampler = sampler

        if best_sampler is not None and best_score >= 0.50:
            confidence = "medium" if best_score > 0.80 else "low"
            matches.append(_build_match(
                har, best_sampler, h_idx,
                confidence=confidence, match_pass=3,
            ))
            matched_har.add(h_idx)
            matched_jmx.add(best_sampler["node_id"])

    return matches


def _tokenize_path(url_path: str) -> List[str]:
    """Split a URL path into non-empty, lowercase segments."""
    return [s.lower() for s in url_path.split("/") if s]


_VERSION_RE = re.compile(r'^v\d+$', re.IGNORECASE)


def _segment_overlap_score(
    har_segments: List[str],
    jmx_segments: List[str],
) -> float:
    """
    Compute overlap score between HAR and JMX path segments.

    Rules:
    - Exact segment match = 1.0 point
    - Version number difference (v1 vs v2) = 0.5 point (still a match, just different version)
    - JMX segment that is a ${...} or {param} placeholder = 0.8 point (expected to differ)
    - No match = 0.0 points

    Score = total points / max(len(har_segments), len(jmx_segments))
    """
    max_len = max(len(har_segments), len(jmx_segments))
    if max_len == 0:
        return 0.0

    points = 0.0
    for i in range(min(len(har_segments), len(jmx_segments))):
        h_seg = har_segments[i]
        j_seg = jmx_segments[i]

        if h_seg == j_seg:
            points += 1.0
        elif _VERSION_RE.match(h_seg) and _VERSION_RE.match(j_seg):
            points += 0.5
        elif "${" in j_seg or ("{" in j_seg and "}" in j_seg):
            points += 0.8
        else:
            points += 0.0

    return points / max_len


def _pass4_unmatched_classification(
    har_entries: List[Dict[str, Any]],
    jmx_samplers: List[Dict[str, Any]],
    matched_har: set,
    matched_jmx: set,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Pass 4 — Classify unmatched entries.

    Returns:
        (new_endpoints, removed_endpoints) where:
        - new_endpoints: HAR entries with no JMX match
        - removed_endpoints: JMX samplers with no HAR match (possibly removed)
    """
    new_endpoints: List[Dict[str, Any]] = []
    for h_idx, har in enumerate(har_entries):
        if h_idx not in matched_har:
            new_endpoints.append({
                "method": har["method"],
                "url_path": har["url_path"],
                "url": har["url"],
                "har_entry_index": har["har_entry_index"],
                "query_params": har.get("query_params", []),
                "request_body_schema": har.get("request_body_schema"),
                "response_status": har.get("response_status", 0),
            })

    removed_endpoints: List[Dict[str, Any]] = []
    for sampler in jmx_samplers:
        if sampler["node_id"] not in matched_jmx:
            removed_endpoints.append({
                "node_id": sampler["node_id"],
                "testname": sampler["testname"],
                "method": sampler["method"],
                "url_pattern": sampler["url_pattern"],
                "enabled": sampler["enabled"],
                "parent_controller": sampler.get("parent_controller"),
                "classification": "possibly_removed",
                "note": (
                    "Not present in HAR — may not have been exercised "
                    "in this capture"
                ),
            })

    return new_endpoints, removed_endpoints


def _build_match(
    har_entry: Dict[str, Any],
    jmx_sampler: Dict[str, Any],
    har_idx: int,
    confidence: str,
    match_pass: int,
) -> Dict[str, Any]:
    """Build a standardized match record."""
    return {
        "match_id": f"m_{har_idx:04d}",
        "confidence": confidence,
        "match_pass": match_pass,
        "har_entry": {
            "method": har_entry["method"],
            "url_path": har_entry["url_path"],
            "url": har_entry["url"],
            "har_entry_index": har_entry["har_entry_index"],
            "query_params": har_entry.get("query_params", []),
            "request_headers": har_entry.get("request_headers", {}),
            "request_body_schema": har_entry.get("request_body_schema"),
            "response_status": har_entry.get("response_status", 0),
            "response_body_schema": har_entry.get("response_body_schema"),
        },
        "jmx_sampler": {
            "node_id": jmx_sampler["node_id"],
            "testname": jmx_sampler["testname"],
            "method": jmx_sampler["method"],
            "url_pattern": jmx_sampler["url_pattern"],
            "domain": jmx_sampler.get("domain", ""),
            "enabled": jmx_sampler.get("enabled", True),
            "parent_controller": jmx_sampler.get("parent_controller"),
            "request_body": jmx_sampler.get("request_body"),
            "child_extractors": jmx_sampler.get("child_extractors", []),
            "child_assertions": jmx_sampler.get("child_assertions", []),
        },
        "differences": [],
    }


def run_matching(
    har_entries: List[Dict[str, Any]],
    jmx_samplers: List[Dict[str, Any]],
    strict_matching: bool = False,
) -> Dict[str, Any]:
    """
    Execute the multi-pass matching algorithm.

    Args:
        har_entries: Normalized HAR entries from extract_har_entries().
        jmx_samplers: Extracted JMX samplers from extract_jmx_samplers().
        strict_matching: When True, skip Pass 3 (fuzzy segment matching).

    Returns:
        Dict with keys:
        - "matches": list of match records (from Passes 1-3)
        - "new_endpoints": HAR entries with no JMX match
        - "removed_endpoints": JMX samplers with no HAR match
        - "match_stats": summary counts per pass and confidence level
    """
    matched_har: set = set()
    matched_jmx: set = set()

    pass1 = _pass1_exact_match(har_entries, jmx_samplers, matched_har, matched_jmx)
    pass2 = _pass2_parameterized_match(har_entries, jmx_samplers, matched_har, matched_jmx)

    pass3: List[Dict[str, Any]] = []
    if not strict_matching:
        pass3 = _pass3_fuzzy_segment_match(
            har_entries, jmx_samplers, matched_har, matched_jmx,
        )

    new_endpoints, removed_endpoints = _pass4_unmatched_classification(
        har_entries, jmx_samplers, matched_har, matched_jmx,
    )

    all_matches = pass1 + pass2 + pass3

    match_stats = {
        "pass_1_exact": len(pass1),
        "pass_2_parameterized": len(pass2),
        "pass_3_fuzzy": len(pass3),
        "total_matched": len(all_matches),
        "new_endpoints": len(new_endpoints),
        "removed_endpoints": len(removed_endpoints),
        "confidence_high": sum(1 for m in all_matches if m["confidence"] == "high"),
        "confidence_medium": sum(1 for m in all_matches if m["confidence"] == "medium"),
        "confidence_low": sum(1 for m in all_matches if m["confidence"] == "low"),
        "strict_matching": strict_matching,
    }

    logger.info(
        "Matching complete: %d matched (P1=%d, P2=%d, P3=%d), "
        "%d new endpoints, %d possibly removed",
        len(all_matches), len(pass1), len(pass2), len(pass3),
        len(new_endpoints), len(removed_endpoints),
    )

    return {
        "matches": all_matches,
        "new_endpoints": new_endpoints,
        "removed_endpoints": removed_endpoints,
        "match_stats": match_stats,
    }


# ============================================================
# Phase C — Difference Analysis
# ============================================================


def _diff_url(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect URL path changes (relevant for fuzzy Pass-3 matches)."""
    diffs: List[Dict[str, Any]] = []

    har_path = match["har_entry"]["url_path"]
    jmx_pattern = match["jmx_sampler"]["url_pattern"]
    jmx_normalized = jmx_pattern.rstrip("/").lower() or "/"

    if match["match_pass"] == 3:
        diffs.append({
            "category": "url_change",
            "severity": "high",
            "har_url_path": har_path,
            "jmx_url_pattern": jmx_pattern,
            "description": (
                f"URL path differs (fuzzy match): "
                f"HAR '{har_path}' vs JMX '{jmx_pattern}'"
            ),
        })
    elif match["match_pass"] in (1, 2):
        if "${" not in jmx_normalized and har_path != jmx_normalized:
            diffs.append({
                "category": "url_change",
                "severity": "high",
                "har_url_path": har_path,
                "jmx_url_pattern": jmx_pattern,
                "description": (
                    f"URL path mismatch: "
                    f"HAR '{har_path}' vs JMX '{jmx_pattern}'"
                ),
            })

    return diffs


def _diff_method(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect HTTP method changes between HAR and JMX."""
    har_method = match["har_entry"]["method"]
    jmx_method = match["jmx_sampler"]["method"]

    if har_method != jmx_method:
        return [{
            "category": "method_change",
            "severity": "high",
            "har_method": har_method,
            "jmx_method": jmx_method,
            "description": (
                f"HTTP method changed: JMX uses {jmx_method}, "
                f"HAR shows {har_method}"
            ),
        }]
    return []


def _parse_jmx_body_keys(body: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Attempt to parse a JMX request body as JSON and extract its key schema.

    JMX bodies may contain ${...} placeholders in values, which break
    JSON parsing. We replace them with placeholder strings before parsing.
    """
    if not body or not body.strip():
        return None

    sanitised = _VAR_PLACEHOLDER_RE.sub('"__jmx_var__"', body)
    try:
        parsed = json.loads(sanitised)
        schema_depth = _get_schema_depth()
        return _extract_json_schema(parsed, schema_depth)
    except (json.JSONDecodeError, TypeError):
        return None


def _collect_schema_keys(schema: Any, prefix: str = "") -> set:
    """Flatten a schema dict into a set of dot-notation key paths."""
    keys: set = set()
    if isinstance(schema, dict):
        for k, v in schema.items():
            full_key = f"{prefix}.{k}" if prefix else k
            keys.add(full_key)
            keys.update(_collect_schema_keys(v, full_key))
    elif isinstance(schema, list) and schema:
        keys.update(_collect_schema_keys(schema[0], f"{prefix}[]"))
    return keys


def _diff_payload(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Detect request payload differences.

    Categories: payload_field_added, payload_field_removed,
    payload_field_type_changed.
    """
    diffs: List[Dict[str, Any]] = []

    har_schema = match["har_entry"].get("request_body_schema")
    jmx_body = match["jmx_sampler"].get("request_body")
    jmx_schema = _parse_jmx_body_keys(jmx_body)

    if har_schema is None and jmx_schema is None:
        return diffs

    if har_schema is not None and jmx_schema is None and jmx_body:
        diffs.append({
            "category": "payload_field_type_changed",
            "severity": "medium",
            "description": (
                "HAR has structured JSON body but JMX body could not be "
                "parsed as JSON — possible format change"
            ),
        })
        return diffs

    if har_schema is not None and jmx_schema is None:
        if isinstance(har_schema, dict) and har_schema:
            diffs.append({
                "category": "payload_field_added",
                "severity": "medium",
                "description": (
                    "HAR request contains a JSON body but JMX sampler has "
                    "no request body"
                ),
                "added_keys": sorted(_collect_schema_keys(har_schema)),
            })
        return diffs

    if har_schema is None and jmx_schema is not None:
        if isinstance(jmx_schema, dict) and jmx_schema:
            diffs.append({
                "category": "payload_field_removed",
                "severity": "medium",
                "description": (
                    "JMX sampler has a JSON body but HAR request has no body"
                ),
                "removed_keys": sorted(_collect_schema_keys(jmx_schema)),
            })
        return diffs

    if not isinstance(har_schema, dict) or not isinstance(jmx_schema, dict):
        return diffs

    har_keys = _collect_schema_keys(har_schema)
    jmx_keys = _collect_schema_keys(jmx_schema)

    added = har_keys - jmx_keys
    removed = jmx_keys - har_keys

    for key in sorted(added):
        diffs.append({
            "category": "payload_field_added",
            "severity": "medium",
            "field": key,
            "description": f"Field '{key}' present in HAR request body but not in JMX",
        })

    for key in sorted(removed):
        diffs.append({
            "category": "payload_field_removed",
            "severity": "medium",
            "field": key,
            "description": f"Field '{key}' present in JMX body but not in HAR request",
        })

    common = har_keys & jmx_keys
    for key in sorted(common):
        har_type = _get_type_at_path(har_schema, key)
        jmx_type = _get_type_at_path(jmx_schema, key)
        if har_type and jmx_type and har_type != jmx_type:
            if jmx_type == "__jmx_var__" or jmx_type == "string":
                continue
            diffs.append({
                "category": "payload_field_type_changed",
                "severity": "medium",
                "field": key,
                "har_type": har_type,
                "jmx_type": jmx_type,
                "description": (
                    f"Type changed for '{key}': "
                    f"JMX has {jmx_type}, HAR has {har_type}"
                ),
            })

    return diffs


def _get_type_at_path(schema: Any, dot_path: str) -> Optional[str]:
    """Resolve a dot-notation path in a schema to its type/value."""
    parts = dot_path.replace("[]", "").split(".")
    current = schema
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and current:
            current = current[0]
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        else:
            return None

    if isinstance(current, str):
        return current
    if isinstance(current, dict):
        return "object"
    if isinstance(current, list):
        return "array"
    return None


def _diff_response_schema(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Detect response body schema changes.

    Compares the HAR response body schema against the JMX sampler's child
    extractors to identify structural changes.
    """
    diffs: List[Dict[str, Any]] = []
    har_resp_schema = match["har_entry"].get("response_body_schema")
    if not isinstance(har_resp_schema, dict) or not har_resp_schema:
        return diffs

    har_keys = _collect_schema_keys(har_resp_schema)
    if not har_keys:
        return diffs

    extractors = match["jmx_sampler"].get("child_extractors", [])
    if not extractors:
        return diffs

    for ext in extractors:
        json_path = ext.get("json_path", "")
        if not json_path:
            continue

        normalized = _normalize_jsonpath_to_dot(json_path)
        if normalized and normalized not in har_keys:
            close_match = _find_closest_key(normalized, har_keys)
            desc = (
                f"Response schema change: extractor path '{json_path}' "
                f"not found in HAR response structure"
            )
            diff_entry: Dict[str, Any] = {
                "category": "response_schema_change",
                "severity": "medium",
                "extractor_node_id": ext.get("node_id", ""),
                "extractor_name": ext.get("testname", ""),
                "json_path": json_path,
                "description": desc,
            }
            if close_match:
                diff_entry["suggested_path"] = close_match
                diff_entry["description"] += f" (closest: '{close_match}')"
            diffs.append(diff_entry)

    return diffs


def _normalize_jsonpath_to_dot(json_path: str) -> Optional[str]:
    """
    Convert a JSONPath expression to dot notation for key lookup.

    Examples:
        $.auth_token       -> auth_token
        $.data.user.name   -> data.user.name
        $.items[0].id      -> items[].id
    """
    if not json_path:
        return None
    path = json_path.lstrip("$").lstrip(".")
    path = re.sub(r'\[\d+\]', '[]', path)
    return path if path else None


def _find_closest_key(target: str, keys: set) -> Optional[str]:
    """Find the key that ends with the same leaf as target."""
    target_leaf = target.rsplit(".", 1)[-1] if "." in target else target
    candidates = [k for k in keys if k.endswith(f".{target_leaf}") or k == target_leaf]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _diff_correlation_drift(
    match: Dict[str, Any],
    correlation_spec: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Detect correlation drift — extractors whose paths no longer match
    the HAR response structure.

    If correlation_spec is provided, also checks for variable source
    field movement or renaming.
    """
    diffs: List[Dict[str, Any]] = []
    har_resp_schema = match["har_entry"].get("response_body_schema")
    extractors = match["jmx_sampler"].get("child_extractors", [])

    if not extractors:
        return diffs

    har_keys = set()
    if isinstance(har_resp_schema, dict):
        har_keys = _collect_schema_keys(har_resp_schema)

    har_resp_text = None
    for ext in extractors:
        ext_type = ext.get("type", "")
        refname = ext.get("refname", "")

        if ext_type == "JSONPostProcessor":
            json_path = ext.get("json_path", "")
            if not json_path or not har_keys:
                continue
            normalized = _normalize_jsonpath_to_dot(json_path)
            if normalized and normalized not in har_keys:
                suggested = _find_closest_key(normalized, har_keys)
                diff_entry: Dict[str, Any] = {
                    "category": "correlation_drift",
                    "severity": "high",
                    "extractor_node_id": ext.get("node_id", ""),
                    "extractor_name": ext.get("testname", ""),
                    "extractor_type": ext_type,
                    "current_json_path": json_path,
                    "refname": refname,
                    "description": (
                        f"Correlation drift: JSONPath '{json_path}' "
                        f"not found in HAR response"
                    ),
                }
                if suggested:
                    diff_entry["suggested_json_path"] = suggested
                    diff_entry["description"] += f" — suggested: '{suggested}'"
                diffs.append(diff_entry)

        elif ext_type == "RegexExtractor":
            regex = ext.get("regex", "")
            if not regex:
                continue
            if har_resp_text is None:
                har_resp_text = _reconstruct_response_text(match)
            if har_resp_text and not _regex_has_match(regex, har_resp_text):
                diffs.append({
                    "category": "correlation_drift",
                    "severity": "high",
                    "extractor_node_id": ext.get("node_id", ""),
                    "extractor_name": ext.get("testname", ""),
                    "extractor_type": ext_type,
                    "current_regex": regex,
                    "refname": refname,
                    "description": (
                        f"Correlation drift: regex '{regex}' does not match "
                        f"HAR response content"
                    ),
                })

        if correlation_spec and refname:
            spec_entry = correlation_spec.get(refname)
            if spec_entry:
                spec_source = spec_entry.get("source_field", "")
                if spec_source and isinstance(har_resp_schema, dict):
                    normalized_source = spec_source.lstrip("$.").replace("[0]", "[]")
                    if normalized_source and normalized_source not in har_keys:
                        suggested = _find_closest_key(normalized_source, har_keys)
                        diff_entry = {
                            "category": "correlation_drift",
                            "severity": "high",
                            "extractor_node_id": ext.get("node_id", ""),
                            "extractor_name": ext.get("testname", ""),
                            "refname": refname,
                            "spec_source_field": spec_source,
                            "description": (
                                f"Correlation spec source field '{spec_source}' "
                                f"for variable '{refname}' not found in HAR response"
                            ),
                        }
                        if suggested:
                            diff_entry["suggested_source_field"] = suggested
                            diff_entry["description"] += f" — suggested: '{suggested}'"
                        diffs.append(diff_entry)

    return diffs


def _reconstruct_response_text(match: Dict[str, Any]) -> Optional[str]:
    """
    Build a text representation of the HAR response for regex matching.

    Uses the response body schema keys since we don't carry the raw body.
    For regex extractors, the key structure itself may be sufficient for
    common patterns like header/boundary extraction.
    """
    schema = match["har_entry"].get("response_body_schema")
    if isinstance(schema, dict):
        try:
            return json.dumps(schema)
        except (TypeError, ValueError):
            return None
    return None


def _regex_has_match(pattern: str, text: str) -> bool:
    """Safely test if a regex matches anywhere in text."""
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return False


def _diff_status_code(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect response status code mismatches via JMX assertions."""
    diffs: List[Dict[str, Any]] = []
    har_status = match["har_entry"].get("response_status", 0)
    if not har_status:
        return diffs

    assertions = match["jmx_sampler"].get("child_assertions", [])
    for assertion in assertions:
        if assertion.get("type") != "ResponseAssertion":
            continue
        props = assertion.get("props", {})
        test_strings = props.get("test_strings", [])
        if not test_strings:
            continue

        expected_statuses = []
        for ts in test_strings:
            ts_stripped = str(ts).strip()
            if ts_stripped.isdigit():
                expected_statuses.append(int(ts_stripped))

        if expected_statuses and har_status not in expected_statuses:
            diffs.append({
                "category": "status_code_change",
                "severity": "low",
                "assertion_node_id": assertion.get("node_id", ""),
                "expected_statuses": expected_statuses,
                "har_status": har_status,
                "description": (
                    f"Status code mismatch: JMX asserts "
                    f"{expected_statuses}, HAR returned {har_status}"
                ),
            })

    return diffs


def _diff_query_params(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Detect query parameter key differences."""
    diffs: List[Dict[str, Any]] = []
    har_params = set(match["har_entry"].get("query_params", []))
    jmx_pattern = match["jmx_sampler"].get("url_pattern", "")

    jmx_params: set = set()
    if "?" in jmx_pattern:
        query_str = jmx_pattern.split("?", 1)[1]
        for pair in query_str.split("&"):
            key = pair.split("=", 1)[0].strip()
            if key:
                jmx_params.add(key)

    if not har_params and not jmx_params:
        return diffs

    added = har_params - jmx_params
    removed = jmx_params - har_params

    if added:
        diffs.append({
            "category": "query_param_change",
            "severity": "low",
            "change_type": "added",
            "params": sorted(added),
            "description": (
                f"Query params in HAR but not in JMX: {', '.join(sorted(added))}"
            ),
        })
    if removed:
        diffs.append({
            "category": "query_param_change",
            "severity": "low",
            "change_type": "removed",
            "params": sorted(removed),
            "description": (
                f"Query params in JMX but not in HAR: {', '.join(sorted(removed))}"
            ),
        })

    return diffs


def _diff_headers(match: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Detect significant header differences.

    Only compares Content-Type and Authorization scheme (not values).
    """
    diffs: List[Dict[str, Any]] = []
    har_headers = match["har_entry"].get("request_headers", {})
    har_ct = har_headers.get("content-type", "").split(";")[0].strip().lower()

    jmx_body = match["jmx_sampler"].get("request_body")
    if har_ct and jmx_body:
        if "application/json" in har_ct:
            try:
                json.loads(
                    _VAR_PLACEHOLDER_RE.sub('"__jmx_var__"', jmx_body)
                )
            except (json.JSONDecodeError, TypeError):
                diffs.append({
                    "category": "header_change",
                    "severity": "low",
                    "header": "content-type",
                    "har_value": har_ct,
                    "description": (
                        f"HAR Content-Type is '{har_ct}' but JMX body "
                        f"does not parse as JSON — possible format mismatch"
                    ),
                })
        elif "form" in har_ct and jmx_body.strip().startswith("{"):
            diffs.append({
                "category": "header_change",
                "severity": "low",
                "header": "content-type",
                "har_value": har_ct,
                "description": (
                    f"HAR Content-Type is '{har_ct}' but JMX body "
                    f"looks like JSON — Content-Type mismatch"
                ),
            })

    return diffs


# ============================================================
# Phase C — Orchestrator
# ============================================================


def analyze_differences(
    matching_result: Dict[str, Any],
    correlation_spec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run all difference checks on every matched pair.

    Mutates each match's "differences" list in-place and returns
    an enriched copy of matching_result with a diff_summary added.

    Args:
        matching_result: Output from run_matching().
        correlation_spec: Optional parsed correlation_spec.json indexed
            by variable refname.

    Returns:
        The enriched matching_result dict with per-match differences
        populated and a top-level "diff_summary" added.
    """
    diff_summary: Dict[str, int] = {
        "url_change": 0,
        "method_change": 0,
        "payload_field_added": 0,
        "payload_field_removed": 0,
        "payload_field_type_changed": 0,
        "response_schema_change": 0,
        "correlation_drift": 0,
        "status_code_change": 0,
        "query_param_change": 0,
        "header_change": 0,
        "matched_no_changes": 0,
    }

    for match in matching_result["matches"]:
        all_diffs: List[Dict[str, Any]] = []

        all_diffs.extend(_diff_url(match))
        all_diffs.extend(_diff_method(match))
        all_diffs.extend(_diff_payload(match))
        all_diffs.extend(_diff_response_schema(match))
        all_diffs.extend(_diff_correlation_drift(match, correlation_spec))
        all_diffs.extend(_diff_status_code(match))
        all_diffs.extend(_diff_query_params(match))
        all_diffs.extend(_diff_headers(match))

        match["differences"] = all_diffs

        if not all_diffs:
            diff_summary["matched_no_changes"] += 1
        else:
            for d in all_diffs:
                cat = d.get("category", "")
                if cat in diff_summary:
                    diff_summary[cat] += 1

    matching_result["diff_summary"] = diff_summary

    total_diffs = sum(
        v for k, v in diff_summary.items() if k != "matched_no_changes"
    )
    logger.info(
        "Difference analysis complete: %d differences across %d matches, "
        "%d matches with no changes",
        total_diffs,
        len(matching_result["matches"]),
        diff_summary["matched_no_changes"],
    )

    return matching_result
