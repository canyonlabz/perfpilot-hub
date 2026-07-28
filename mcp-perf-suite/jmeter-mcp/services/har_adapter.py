"""
har_adapter.py

Adapter module that reads HAR (HTTP Archive) files and converts them
into the canonical, step-aware network capture JSON used for JMeter script
generation.

High-level responsibilities:
- Parse HAR 1.2 format JSON files
- Apply config-driven filters (static assets, third-party, domains, etc.)
- Group entries into logical steps (by page, time gap, or single step)
- Emit network_capture_<timestamp>.json under:
    artifacts/<run_id>/jmeter/network-capture/
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from utils.config import load_config
from services import network_capture

logger = logging.getLogger(__name__)

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
NETWORK_CAPTURE_CONFIG = CONFIG.get("network_capture", {})

# HAR-specific MIME type prefixes to exclude (binary content)
_BINARY_MIME_PREFIXES = (
    "image/",
    "font/",
    "video/",
    "audio/",
    "application/octet-stream",
)

_VALID_STRATEGIES = {"auto", "page", "time_gap", "single_step"}

# File size thresholds (safeguard #4)
_MAX_HAR_FILE_SIZE_BYTES = 200 * 1024 * 1024   # 200 MB — reject
_WARN_HAR_FILE_SIZE_BYTES = 50 * 1024 * 1024   # 50 MB — warn

# Required fields in each canonical capture entry (safeguard #3)
_REQUIRED_ENTRY_FIELDS = {
    "request_id": str,
    "method": str,
    "url": str,
    "headers": dict,
    "post_data": str,
    "step": dict,
    "response": str,
    "log_timestamp": str,
    "status": int,
    "response_headers": dict,
}


# ============================================================
# Public API
# ============================================================

def convert_har_to_capture(
    har_path: str,
    test_run_id: str,
    step_strategy: str = "auto",
    time_gap_threshold_ms: int = 3000,
    step_prefix: str = "Step",
) -> str:
    """
    Convert a HAR file to network capture JSON format.

    Args:
        har_path: Full path to the HAR file.
        test_run_id: Unique identifier for the test run.
        step_strategy: Grouping strategy (auto/page/time_gap/single_step).
        time_gap_threshold_ms: Gap threshold for time_gap strategy in ms.
        step_prefix: Prefix for step labels (default: "Step").

    Returns:
        Path to the generated network capture JSON file.

    Raises:
        FileNotFoundError: If HAR file doesn't exist.
        ValueError: If HAR file is invalid or contains no usable entries.
    """
    har_data = _load_har_file(har_path)
    log_obj = har_data["log"]
    pages = log_obj.get("pages", [])
    raw_entries = log_obj.get("entries", [])

    entries_total = len(raw_entries)
    logger.info("HAR loaded: %d entries, %d pages", entries_total, len(pages))

    capture_config = dict(NETWORK_CAPTURE_CONFIG)
    filtered_entries = [
        e for e in raw_entries if _should_include_entry(e, capture_config)
    ]
    entries_filtered = entries_total - len(filtered_entries)

    if not filtered_entries:
        raise ValueError(
            f"No usable entries after filtering ({entries_total} total, "
            f"{entries_filtered} filtered out). Check config.yaml filters."
        )

    logger.info(
        "After filtering: %d entries kept, %d filtered out",
        len(filtered_entries), entries_filtered,
    )

    strategy = _detect_step_strategy(filtered_entries, step_strategy)
    logger.info("Step strategy resolved: '%s'", strategy)

    if strategy == "page":
        grouped = _group_entries_by_page(filtered_entries, pages, step_prefix)
    elif strategy == "time_gap":
        grouped = _group_entries_by_time_gap(
            filtered_entries, time_gap_threshold_ms, step_prefix,
        )
    else:
        grouped = _group_entries_single_step(filtered_entries, step_prefix)

    exclude_pseudo = capture_config.get("exclude_pseudo_headers", True)
    per_step: Dict[str, List[Dict[str, Any]]] = {}
    for step_idx, (step_label, entries_in_step) in enumerate(grouped.items(), start=1):
        converted = []
        for entry in entries_in_step:
            capture_entry = _convert_entry_to_capture_format(
                entry, step_idx, step_label, exclude_pseudo,
            )
            converted.append(capture_entry)
        per_step[step_label] = converted

    _validate_capture_output(per_step)

    output_path = _write_step_network_capture(per_step, test_run_id)
    logger.info("Network capture written: %s", output_path)

    har_version = log_obj.get("version", "unknown")
    creator_obj = log_obj.get("creator", {})
    har_creator = creator_obj.get("name", "unknown")
    _write_capture_manifest(
        run_id=test_run_id,
        source_file=os.path.basename(har_path),
        har_version=har_version,
        har_creator=har_creator,
        step_strategy=strategy,
        entries_total=entries_total,
        entries_filtered=entries_filtered,
        entries_captured=len(filtered_entries),
    )

    return output_path


def validate_har_file(har_path: str) -> Dict[str, Any]:
    """
    Validate a HAR file and return summary statistics without converting.

    Args:
        har_path: Full path to the HAR file.

    Returns:
        Dict with keys: valid (bool), version (str), entry_count (int),
        page_count (int), has_pages (bool), filtered_count (int),
        errors (list).
    """
    errors: List[str] = []

    try:
        har_data = _load_har_file(har_path)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "valid": False,
            "version": "",
            "entry_count": 0,
            "page_count": 0,
            "has_pages": False,
            "filtered_count": 0,
            "errors": [str(exc)],
        }

    log_obj = har_data.get("log", {})
    version = log_obj.get("version", "unknown")
    pages = log_obj.get("pages", [])
    entries = log_obj.get("entries", [])

    if version not in ("1.2", "1.1"):
        errors.append(f"Unexpected HAR version: {version}")

    capture_config = dict(NETWORK_CAPTURE_CONFIG)
    filtered_entries = [
        e for e in entries if _should_include_entry(e, capture_config)
    ]
    filtered_count = len(entries) - len(filtered_entries)

    has_pages = any(e.get("pageref") for e in entries)

    return {
        "valid": len(errors) == 0,
        "version": version,
        "entry_count": len(entries),
        "page_count": len(pages),
        "has_pages": has_pages,
        "filtered_count": filtered_count,
        "errors": errors,
    }


# ============================================================
# Internal Functions — File Loading
# ============================================================

def _load_har_file(har_path: str) -> Dict[str, Any]:
    """
    Load, parse, and validate basic HAR JSON structure.

    Includes file size guard (safeguard #4):
    - Warns if file > 50MB
    - Rejects if file > 200MB

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file exceeds size limit or is not valid HAR JSON.
    """
    if not os.path.isfile(har_path):
        raise FileNotFoundError(f"HAR file not found: {har_path}")

    file_size = os.path.getsize(har_path)

    if file_size > _MAX_HAR_FILE_SIZE_BYTES:
        raise ValueError(
            f"HAR file too large ({file_size / (1024*1024):.1f} MB). "
            f"Maximum supported size is {_MAX_HAR_FILE_SIZE_BYTES / (1024*1024):.0f} MB."
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


# ============================================================
# Internal Functions — Field Conversion
# ============================================================

def _headers_list_to_dict(
    headers_list: List[Dict],
    exclude_pseudo_headers: bool = True,
) -> Dict[str, str]:
    """
    Convert HAR headers array [{name, value}] to a flat dict.

    - Uses lowercase keys; last occurrence wins on duplicates.
    - When exclude_pseudo_headers is True (configurable via
      config.yaml network_capture.exclude_pseudo_headers),
      filters out HTTP/2 pseudo-headers (names starting with ':').
    """
    result: Dict[str, str] = {}
    for h in headers_list or []:
        name = h.get("name")
        value = h.get("value")
        if name is None or value is None:
            continue
        if exclude_pseudo_headers and name.startswith(":"):
            continue
        result[name.lower()] = value
    return result


def _extract_page_label(page_title: str, max_length: int = 60) -> str:
    """
    Extract a readable step label from a HAR page title.

    Real-world page titles are typically full URLs (often very long
    OAuth redirect URLs). This extracts hostname + first path segment.

    Logic:
        1. If title is a URL -> extract hostname + first path segment
        2. If extracted label exceeds max_length -> truncate to hostname only
        3. If title is not a URL -> use as-is
        4. If title is empty -> return "Page" as fallback
    """
    if not page_title or not page_title.strip():
        return "Page"

    parsed = urlparse(page_title.strip())
    if parsed.scheme in ("http", "https") and parsed.netloc:
        hostname = parsed.netloc
        path = parsed.path.strip("/")
        first_segment = path.split("/")[0] if path else ""

        if first_segment:
            label = f"{hostname}/{first_segment}"
        else:
            label = hostname

        if len(label) > max_length:
            return hostname
        return label

    return page_title.strip()


def _parse_har_datetime(dt_string: str) -> Optional[datetime]:
    """
    Parse ISO 8601 datetime string from HAR startedDateTime.

    HAR uses ISO 8601 format (e.g., "2026-01-12T23:47:41.253Z").
    Returns None if parsing fails.
    """
    if not dt_string:
        return None
    try:
        cleaned = dt_string.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


# ============================================================
# Internal Functions — Filtering
# ============================================================

def _should_include_entry(entry: Dict, config: Dict) -> bool:
    """
    Determine if a HAR entry should be included in the capture.

    Applies URL-based filters via network_capture.should_capture_url()
    plus HAR-specific filters:
    - Skip failed/aborted requests (status 0 or -1)
    - Skip OPTIONS preflight requests
    - Skip binary content types (image/*, font/*, video/*, audio/*)
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


# ============================================================
# Internal Functions — Step Grouping
# ============================================================

def _detect_step_strategy(entries: List[Dict], requested: str) -> str:
    """
    Resolve 'auto' strategy: returns 'page' if entries have pageref,
    else 'time_gap'. For explicit strategies, returns as-is after
    validation.

    Valid strategies: auto, page, time_gap, single_step.

    Raises:
        ValueError: If requested strategy is not recognized.
    """
    requested = requested.lower().strip()
    if requested not in _VALID_STRATEGIES:
        raise ValueError(
            f"Unknown step strategy '{requested}'. "
            f"Valid options: {', '.join(sorted(_VALID_STRATEGIES))}"
        )

    if requested != "auto":
        return requested

    has_pageref = any(e.get("pageref") for e in entries)
    return "page" if has_pageref else "time_gap"


def _group_entries_by_page(
    entries: List[Dict],
    pages: List[Dict],
    step_prefix: str,
) -> Dict[str, List[Dict]]:
    """
    Group HAR entries by pageref, using page titles as step labels.
    Entries without pageref go into a catch-all step.
    """
    page_id_to_title: Dict[str, str] = {}
    page_order: List[str] = []
    for page in pages:
        pid = page.get("id", "")
        title = page.get("title", "")
        page_id_to_title[pid] = title
        page_order.append(pid)

    buckets: Dict[str, List[Dict]] = {}
    orphan_entries: List[Dict] = []

    for entry in entries:
        pageref = entry.get("pageref", "")
        if pageref and pageref in page_id_to_title:
            buckets.setdefault(pageref, []).append(entry)
        else:
            orphan_entries.append(entry)

    grouped: Dict[str, List[Dict]] = {}
    step_num = 1
    for pid in page_order:
        if pid not in buckets:
            continue
        raw_title = page_id_to_title.get(pid, "")
        label = _extract_page_label(raw_title)
        step_label = f"{step_prefix} {step_num}: {label}"
        grouped[step_label] = buckets[pid]
        step_num += 1

    if orphan_entries:
        step_label = f"{step_prefix} {step_num}: Uncategorized"
        grouped[step_label] = orphan_entries

    return grouped


def _group_entries_by_time_gap(
    entries: List[Dict],
    threshold_ms: int,
    step_prefix: str,
) -> Dict[str, List[Dict]]:
    """
    Group entries by time gaps exceeding threshold_ms.
    Entries must be sorted by startedDateTime first.
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: e.get("startedDateTime", ""),
    )

    if not sorted_entries:
        return {}

    groups: List[List[Dict]] = [[sorted_entries[0]]]

    for entry in sorted_entries[1:]:
        prev_entry = groups[-1][-1]
        prev_dt = _parse_har_datetime(prev_entry.get("startedDateTime", ""))
        curr_dt = _parse_har_datetime(entry.get("startedDateTime", ""))

        if prev_dt and curr_dt:
            gap_ms = (curr_dt - prev_dt).total_seconds() * 1000
            if gap_ms > threshold_ms:
                groups.append([])

        groups[-1].append(entry)

    grouped: Dict[str, List[Dict]] = {}
    for idx, group in enumerate(groups, start=1):
        step_label = f"{step_prefix} {idx}: Request Group"
        grouped[step_label] = group

    return grouped


def _group_entries_single_step(
    entries: List[Dict],
    step_prefix: str,
) -> Dict[str, List[Dict]]:
    """Place all entries into a single step."""
    return {f"{step_prefix} 1: All Requests": entries}


# ============================================================
# Internal Functions — Entry Conversion
# ============================================================

def _convert_entry_to_capture_format(
    entry: Dict,
    step_number: int,
    step_label: str,
    exclude_pseudo_headers: bool = True,
) -> Dict[str, Any]:
    """
    Convert a single HAR entry to the canonical capture format.

    Generates request_id via uuid4, maps HAR fields to the canonical
    schema, and creates step metadata.
    """
    request = entry.get("request", {})
    response = entry.get("response", {})
    content = response.get("content", {})

    method = (request.get("method") or "GET").upper()
    url = request.get("url", "")

    req_headers = _headers_list_to_dict(
        request.get("headers", []), exclude_pseudo_headers,
    )
    resp_headers = _headers_list_to_dict(
        response.get("headers", []), exclude_pseudo_headers,
    )

    post_data = ""
    post_data_obj = request.get("postData")
    if post_data_obj and isinstance(post_data_obj, dict):
        post_data = post_data_obj.get("text", "")

    response_body = content.get("text", "")
    if response_body is None:
        response_body = ""

    status = response.get("status", 0)
    if not isinstance(status, int):
        try:
            status = int(status)
        except (ValueError, TypeError):
            status = 0

    return {
        "request_id": str(uuid.uuid4()),
        "method": method,
        "url": url,
        "headers": req_headers,
        "post_data": post_data,
        "step": _create_step_metadata(step_number, step_label),
        "response": response_body,
        "log_timestamp": datetime.utcnow().isoformat(),
        "status": status,
        "response_headers": resp_headers,
    }


# ============================================================
# Internal Functions — Output (duplicated from playwright_adapter
# to avoid coupling to private internals — safeguard #2)
# ============================================================

def _create_step_metadata(step_number: int, instructions: str) -> Dict[str, Any]:
    """
    Build the 'step' metadata payload used for each network entry.

    Returns:
        {"step_number": int, "instructions": str, "timestamp": str}
    """
    return {
        "step_number": step_number,
        "instructions": instructions,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _get_network_capture_output_path(
    run_id: str,
    timestamp: Optional[str] = None,
) -> str:
    """
    Compute the path for the network capture JSON file.

    Layout:
        artifacts/<run_id>/jmeter/network-capture/network_capture_<timestamp>.json
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    base_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter", "network-capture")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"network_capture_{timestamp}.json")


def _write_step_network_capture(
    per_step_requests: Dict[str, List[Dict[str, Any]]],
    run_id: str,
    timestamp: Optional[str] = None,
) -> str:
    """
    Write the step-aware mapping to a network capture JSON file.

    Returns:
        The full path to the written JSON file.
    """
    output_path = _get_network_capture_output_path(run_id, timestamp)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(per_step_requests, f, indent=2, ensure_ascii=False)
    return output_path


# ============================================================
# Internal Functions — Validation (safeguard #3)
# ============================================================

def _validate_capture_output(per_step_data: Dict[str, List[Dict]]) -> None:
    """
    Validate that the output matches the canonical network capture schema
    before writing to disk.

    Checks:
    - Top-level keys are strings (step labels)
    - Each step contains a list of entry dicts
    - Each entry has all required fields with correct types

    Raises:
        ValueError: With a descriptive message if the schema is invalid.
    """
    if not isinstance(per_step_data, dict):
        raise ValueError("Capture output must be a dict (step_label -> entries)")

    for step_label, entries in per_step_data.items():
        if not isinstance(step_label, str):
            raise ValueError(f"Step label must be a string, got: {type(step_label)}")

        if not isinstance(entries, list):
            raise ValueError(
                f"Entries for '{step_label}' must be a list, got: {type(entries)}"
            )

        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Entry {idx} in '{step_label}' must be a dict, got: {type(entry)}"
                )
            for field, expected_type in _REQUIRED_ENTRY_FIELDS.items():
                if field not in entry:
                    raise ValueError(
                        f"Entry {idx} in '{step_label}' missing required field: '{field}'"
                    )
                if not isinstance(entry[field], expected_type):
                    raise ValueError(
                        f"Entry {idx} in '{step_label}' field '{field}' "
                        f"expected {expected_type.__name__}, "
                        f"got {type(entry[field]).__name__}"
                    )


# ============================================================
# Internal Functions — Manifest
# ============================================================

def _write_capture_manifest(
    run_id: str,
    source_file: str,
    har_version: str,
    har_creator: str,
    step_strategy: str,
    entries_total: int,
    entries_filtered: int,
    entries_captured: int,
) -> str:
    """
    Write a capture_manifest.json alongside the network capture file.

    Records provenance: source type, source file, conversion tool,
    timestamp, strategy used, and entry counts.

    Returns:
        The full path to the written manifest file.
    """
    manifest = {
        "source_type": "har",
        "source_file": source_file,
        "conversion_tool": "convert_har_to_capture",
        "conversion_timestamp": datetime.utcnow().isoformat(),
        "step_strategy": step_strategy,
        "entries_total": entries_total,
        "entries_filtered": entries_filtered,
        "entries_captured": entries_captured,
        "har_version": har_version,
        "har_creator": har_creator,
    }

    base_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter")
    os.makedirs(base_dir, exist_ok=True)
    manifest_path = os.path.join(base_dir, "capture_manifest.json")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info("Capture manifest written: %s", manifest_path)
    return manifest_path
