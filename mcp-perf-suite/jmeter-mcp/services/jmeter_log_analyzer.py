"""
jmeter_log_analyzer.py

Service module for deep analysis of JMeter/BlazeMeter log files.

Responsibilities:
- Orchestrate log file discovery, parsing, grouping, and output generation
- Parse multi-line error blocks (JSR223 Post-Processor, stack traces)
- Categorize and classify errors by type, severity, API, and response code
- Group similar errors with deduplication via normalized signatures
- Capture first-occurrence request/response details per error group
- Correlate with JTL data for response code enrichment
- Format and delegate output writing (CSV, JSON, Markdown) to file_utils

Output location: artifacts/<test_run_id>/analysis/

Note: This module delegates low-level extraction to utils/log_utils.py
and all file I/O to utils/file_utils.py.
"""

import csv
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from utils.config import load_config
from utils.file_utils import (
    get_analysis_output_dir,
    get_source_artifacts_dir,
    discover_files_by_extension,
    save_csv_file,
    save_json_file,
    save_markdown_file,
)
from utils.log_utils import (
    is_new_log_entry,
    is_error_level,
    extract_timestamp,
    extract_log_level,
    extract_thread_name,
    extract_sampler_name,
    extract_api_endpoint,
    extract_response_code,
    extract_error_message,
    extract_request_details,
    extract_response_details,
    extract_stack_trace,
    normalize_error_message,
    generate_error_signature,
    truncate,
    sanitize_for_csv,
    RE_JSR223_ERROR,
    RE_LOG_ENTRY,
)

# === Configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
LOG_CONFIG = CONFIG.get("jmeter_log", {})

MAX_DESCRIPTION_LENGTH = LOG_CONFIG.get("max_description_length", 200)
MAX_REQUEST_LENGTH = LOG_CONFIG.get("max_request_length", 500)
MAX_RESPONSE_LENGTH = LOG_CONFIG.get("max_response_length", 500)
MAX_STACK_TRACE_LINES = LOG_CONFIG.get("max_stack_trace_lines", 50)
ERROR_LEVELS = LOG_CONFIG.get("error_levels", ["ERROR", "FATAL"])

TOOL_VERSION = "1.0.0"


# ============================================================
# Error Category & Severity Constants
# ============================================================

# Severity levels ordered from most to least severe
SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2}

# Error categories with their detection keywords and default severity
ERROR_CATEGORIES = {
    "HTTP 5xx Error": {
        "severity": "Critical",
        "code_range": range(500, 600),
    },
    "HTTP 4xx Error": {
        "severity": "High",
        "code_range": range(400, 500),
    },
    "Script Execution Failure": {
        "severity": "Critical",
        "keywords": [
            "JSR223Sampler", "ScriptException", "MissingPropertyException",
            "CompilationFailedException", "GroovyRuntimeException",
        ],
    },
    "Connection Error": {
        "severity": "Critical",
        "keywords": [
            "Connection refused", "Connection reset", "ConnectException",
            "SocketException",
        ],
    },
    "Timeout Error": {
        "severity": "High",
        "keywords": [
            "timeout", "timed out", "SocketTimeoutException",
            "ReadTimeoutException",
        ],
    },
    "SSL/TLS Error": {
        "severity": "High",
        "keywords": [
            "SSLException", "SSLHandshakeException", "certificate",
        ],
    },
    "Thread/Concurrency Error": {
        "severity": "Critical",
        "keywords": [
            "OutOfMemoryError", "StackOverflowError",
        ],
    },
    "DNS Resolution Error": {
        "severity": "Critical",
        "keywords": [
            "UnknownHostException", "DNS resolution failed",
        ],
    },
    "Authentication Error": {
        "severity": "High",
        "keywords": [
            "401 Unauthorized", "403 Forbidden",
        ],
    },
}


# ============================================================
# Public API
# ============================================================

def analyze_logs(
    test_run_id: str,
    log_source: str = "blazemeter",
) -> dict:
    """
    Main entry point. Discovers, parses, groups, correlates, and outputs
    analysis for all .log files under the specified source folder.

    Orchestration flow:
      1. Validate inputs (test_run_id, log_source)
      2. Discover log files → if none found, return { status: "NO_LOGS" }
      3. Discover JTL file (optional, single file)
      4. For each log file:
         a. Parse log file → list of error entries
         b. Collect file metadata (size, line counts)
      5. Merge all error entries across log files
      6. Categorize each error entry
      7. Group errors by signature
      8. If JTL file found:
         a. Parse JTL file
         b. Correlate with error groups
         c. Identify JTL-only failures
      9. Format and write CSV output (via file_utils)
     10. Format and write JSON output (via file_utils)
     11. Format and write Markdown output (via file_utils)
     12. Return result dict with status, paths, and summary

    Args:
        test_run_id: Unique test run identifier.
        log_source: "jmeter" or "blazemeter".

    Returns:
        dict with:
          - test_run_id: The test run identifier
          - log_source: Which source was analyzed
          - status: "OK", "NO_LOGS", or "ERROR"
          - log_files_analyzed: List of log file names processed
          - total_issues: Total unique issues found
          - total_occurrences: Sum of all error occurrences
          - issues_by_severity: Breakdown by severity level
          - output_files: Dict with paths to CSV, JSON, and Markdown outputs
          - message: Human-readable summary
    """
    # ------------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------------
    if not test_run_id:
        return {
            "test_run_id": test_run_id,
            "log_source": log_source,
            "status": "ERROR",
            "message": "test_run_id is required.",
        }

    log_source = log_source.lower().strip()
    if log_source not in ("jmeter", "blazemeter"):
        return {
            "test_run_id": test_run_id,
            "log_source": log_source,
            "status": "ERROR",
            "message": (
                f"Invalid log_source '{log_source}'. "
                "Must be 'jmeter' or 'blazemeter'."
            ),
        }

    # ------------------------------------------------------------------
    # 2. Discover log files
    # ------------------------------------------------------------------
    source_dir = get_source_artifacts_dir(test_run_id, log_source)
    if not os.path.isdir(source_dir):
        return {
            "test_run_id": test_run_id,
            "log_source": log_source,
            "status": "NO_LOGS",
            "message": f"Source directory not found: {source_dir}",
        }

    log_files = discover_files_by_extension(source_dir, ".log")
    if not log_files:
        return {
            "test_run_id": test_run_id,
            "log_source": log_source,
            "status": "NO_LOGS",
            "message": f"No .log files found in {source_dir}",
        }

    # ------------------------------------------------------------------
    # 3. Discover JTL file (optional, single file)
    # ------------------------------------------------------------------
    jtl_file_path = _discover_jtl_file(test_run_id, log_source)

    # ------------------------------------------------------------------
    # 4. Parse each log file → error entries + metadata
    # ------------------------------------------------------------------
    all_error_entries: List[dict] = []
    log_file_metadata: List[dict] = []

    for log_file in log_files:
        entries, metadata = _parse_log_file(log_file)
        all_error_entries.extend(entries)
        log_file_metadata.append(metadata)

    # ------------------------------------------------------------------
    # 5. (Entries are already merged from step 4)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 6. Categorize each error entry (skip 2xx/3xx — not real errors)
    # ------------------------------------------------------------------
    categorized_entries: List[dict] = []
    skipped_per_file: Dict[str, int] = defaultdict(int)
    for entry in all_error_entries:
        result = _categorize_error(entry)
        if result is None:
            # 2xx/3xx response code — skip (script assertion issue, not app error)
            skipped_per_file[entry.get("log_file", "")] += 1
            continue
        category, severity = result
        entry["error_category"] = category
        entry["severity"] = severity
        categorized_entries.append(entry)
    all_error_entries = categorized_entries

    # Enrich log file metadata with skipped/relevant counts
    for meta in log_file_metadata:
        filename = meta.get("filename", "")
        total_blocks = meta.get("error_lines", 0)
        skipped = skipped_per_file.get(filename, 0)
        meta["error_blocks_total"] = total_blocks
        meta["error_blocks_skipped"] = skipped
        meta["error_blocks_relevant"] = total_blocks - skipped
        if skipped > 0:
            meta["skip_reason"] = (
                "2xx/3xx response codes filtered "
                "(script/assertion issues, not application errors)"
            )

    # ------------------------------------------------------------------
    # 7. Group errors by signature
    # ------------------------------------------------------------------
    grouped_errors = _group_errors(all_error_entries)

    # ------------------------------------------------------------------
    # 8. JTL correlation (if JTL file found)
    # ------------------------------------------------------------------
    jtl_data: List[dict] = []
    jtl_file_metadata: Optional[dict] = None
    jtl_only_failures: List[dict] = []
    jtl_correlation_stats: dict = {}

    if jtl_file_path:
        jtl_data, jtl_meta = _parse_jtl_file(jtl_file_path)
        jtl_file_metadata = jtl_meta

        if jtl_data:
            grouped_errors, jtl_only_failures, jtl_correlation_stats = (
                _correlate_with_jtl(grouped_errors, jtl_data)
            )

    # ------------------------------------------------------------------
    # 9. Format and write CSV output
    # ------------------------------------------------------------------
    output_dir = get_analysis_output_dir(test_run_id)
    prefix = f"{log_source}_log_analysis"

    csv_path = os.path.join(output_dir, f"{prefix}.csv")
    fieldnames, csv_rows = _format_csv_rows(grouped_errors)
    save_csv_file(csv_path, fieldnames, csv_rows)

    # ------------------------------------------------------------------
    # 10. Format and write JSON output
    # ------------------------------------------------------------------
    json_path = os.path.join(output_dir, f"{prefix}.json")
    json_data = _format_json_output(
        test_run_id=test_run_id,
        log_source=log_source,
        grouped_errors=grouped_errors,
        log_file_metadata=log_file_metadata,
        jtl_file_metadata=jtl_file_metadata,
        jtl_only_failures=jtl_only_failures,
        jtl_correlation_stats=jtl_correlation_stats,
    )
    save_json_file(json_path, json_data)

    # ------------------------------------------------------------------
    # 11. Format and write Markdown output
    # ------------------------------------------------------------------
    md_path = os.path.join(output_dir, f"{prefix}.md")
    md_content = _format_markdown_output(
        test_run_id=test_run_id,
        log_source=log_source,
        grouped_errors=grouped_errors,
        log_file_metadata=log_file_metadata,
        jtl_file_metadata=jtl_file_metadata,
        jtl_only_failures=jtl_only_failures,
        jtl_correlation_stats=jtl_correlation_stats,
    )
    save_markdown_file(md_path, md_content)

    # ------------------------------------------------------------------
    # 12. Build and return result dict
    # ------------------------------------------------------------------
    total_occurrences = sum(g["error_count"] for g in grouped_errors)
    issues_by_severity: Dict[str, int] = defaultdict(int)
    for g in grouped_errors:
        issues_by_severity[g["severity"]] += 1

    return {
        "test_run_id": test_run_id,
        "log_source": log_source,
        "status": "OK",
        "log_files_analyzed": [m.get("filename", "") for m in log_file_metadata],
        "jtl_file_analyzed": jtl_file_metadata.get("filename") if jtl_file_metadata else None,
        "total_issues": len(grouped_errors),
        "total_occurrences": total_occurrences,
        "issues_by_severity": dict(issues_by_severity),
        "output_files": {
            "csv": csv_path,
            "json": json_path,
            "markdown": md_path,
        },
        "message": (
            f"Analyzed {len(log_file_metadata)} log file(s) from '{log_source}'. "
            f"Found {len(grouped_errors)} unique issue(s) "
            f"({total_occurrences} total occurrence(s)). "
            f"Critical: {issues_by_severity.get('Critical', 0)}, "
            f"High: {issues_by_severity.get('High', 0)}, "
            f"Medium: {issues_by_severity.get('Medium', 0)}."
        ),
    }


# ============================================================
# Log Parsing (high-level)
# ============================================================

def _parse_log_file(file_path: str) -> Tuple[List[dict], dict]:
    """
    Parse a single log file and return a list of structured error entries
    along with file metadata.

    Handles multi-line error blocks and stack traces by:
      1. Reading line-by-line (streaming, not loading full file into memory)
      2. Using is_new_log_entry() to detect block boundaries
      3. Accumulating continuation lines into the current block
      4. Parsing completed blocks via _parse_error_block()

    Args:
        file_path: Absolute path to the .log file.

    Returns:
        Tuple of:
          - List of structured error entry dicts
          - File metadata dict with: filename, path, size_bytes,
            total_lines, error_lines
    """
    error_entries = []
    total_lines = 0
    error_block_count = 0

    # Current block being accumulated
    current_block_lines: List[str] = []
    current_block_start_line = 0
    current_block_is_error = False

    def _finalize_block():
        """Process the accumulated block if it's an error block."""
        nonlocal error_block_count
        if current_block_lines and current_block_is_error:
            entry = _parse_error_block(
                current_block_lines,
                current_block_start_line,
                file_path,
            )
            if entry:
                error_entries.append(entry)
                error_block_count += 1

    file_size = os.path.getsize(file_path)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, raw_line in enumerate(f, start=1):
            total_lines += 1
            line = raw_line.rstrip("\n\r")

            if is_new_log_entry(line):
                # Finalize previous block before starting new one
                _finalize_block()

                # Start a new block
                current_block_lines = [line]
                current_block_start_line = line_num

                # Determine if this block qualifies as an error:
                # - JMeter log level is ERROR or FATAL, OR
                # - Content contains [ERROR]: (JSR223 Post-Processor marker)
                log_level = extract_log_level(line)
                level_is_error = (
                    log_level is not None
                    and is_error_level(log_level, ERROR_LEVELS)
                )
                has_jsr223_error = "[ERROR]:" in line

                current_block_is_error = level_is_error or has_jsr223_error
            else:
                # Continuation line — append to current block
                if current_block_lines:
                    current_block_lines.append(line)
                    # A continuation line with [ERROR]: also marks block as error
                    if not current_block_is_error and "[ERROR]:" in line:
                        current_block_is_error = True

    # Finalize the last block in the file
    _finalize_block()

    metadata = {
        "filename": os.path.basename(file_path),
        "path": file_path,
        "size_bytes": file_size,
        "total_lines": total_lines,
        "error_lines": error_block_count,
    }

    return error_entries, metadata


def _parse_error_block(
    lines: List[str],
    start_line_num: int,
    file_path: str,
) -> Optional[dict]:
    """
    Parse a multi-line error block into a structured entry.

    Delegates field extraction to log_utils functions.

    Args:
        lines: List of lines comprising the error block.
        start_line_num: Line number where the block starts in the file.
        file_path: Source log file path (for metadata).

    Returns:
        dict with: timestamp, log_level, thread_name, sampler_name,
        api_endpoint, response_code, error_message, request_details,
        response_details, stack_trace, line_number, log_file, raw_block.
        Returns None if block cannot be parsed.
    """
    if not lines:
        return None

    raw_block = "\n".join(lines)
    first_line = lines[0]

    # Extract base fields from the first (timestamped) line
    timestamp = extract_timestamp(first_line)
    log_level = extract_log_level(first_line) or "ERROR"

    # Check if this is a JSR223 Post-Processor error block
    # These have [ERROR]:[ThreadName]: SamplerName: pattern
    is_jsr223 = "[ERROR]:" in raw_block

    if is_jsr223:
        # Delegate to JSR223-specific parser
        jsr223_data = _extract_jsr223_error_block(lines)
        thread_name = jsr223_data.get("thread_name")
        sampler_name = jsr223_data.get("sampler_name")
        error_message = jsr223_data.get("error_message", "")
        request_details = jsr223_data.get("request_details")
        response_details = jsr223_data.get("response_details")
    else:
        # Standard error — extract from full block text
        thread_name = extract_thread_name(raw_block)
        sampler_name = extract_sampler_name(first_line)
        error_message = extract_error_message(raw_block)
        request_details = extract_request_details(raw_block)
        response_details = extract_response_details(raw_block)

    # Fields common to both JSR223 and standard errors
    api_endpoint = extract_api_endpoint(raw_block)
    response_code = extract_response_code(raw_block)
    stack_trace = extract_stack_trace(lines, MAX_STACK_TRACE_LINES)

    return {
        "timestamp": timestamp,
        "log_level": log_level,
        "thread_name": thread_name,
        "sampler_name": sampler_name,
        "api_endpoint": api_endpoint or "N/A",
        "response_code": response_code or "N/A",
        "error_message": error_message,
        "request_details": request_details,
        "response_details": response_details,
        "stack_trace": stack_trace,
        "line_number": start_line_num,
        "log_file": os.path.basename(file_path),
        "raw_block": raw_block,
        # Category and severity are populated later by _categorize_error()
        "error_category": None,
        "severity": None,
    }


def _extract_jsr223_error_block(lines: List[str]) -> dict:
    """
    Extract structured data from a JSR223 Post-Processor error block.

    Uses Request=[...] and Response=[...] as reliable boundaries.
    Custom lines between error message and Request= are captured
    as part of the error context/description.

    Args:
        lines: List of lines from the JSR223 error block.

    Returns:
        dict with: thread_name, sampler_name, error_message,
        request_details, response_details, context_lines
    """
    thread_name = None
    sampler_name = None
    error_message = ""
    context_lines = []
    request_lines = []
    response_lines = []

    # Parse state: "message" → "context" → "request" → "response"
    state = "message"
    in_request = False
    in_response = False

    for line in lines:
        # Check for JSR223 error marker to extract thread/sampler from first match
        match = RE_JSR223_ERROR.search(line)

        if in_response:
            # Accumulate response continuation lines
            response_lines.append(line)
            # Check if response block closes on this line
            stripped = line.rstrip()
            if stripped.endswith("]") and "Response=[" not in line:
                in_response = False
            continue

        if in_request:
            # Check if request block closes and response starts
            stripped = line.rstrip()
            if "Response=[" in line:
                # Request block ended, response block starts
                in_request = False
                in_response = True
                response_lines.append(line)
                # Check for single-line response: Response=[...]
                if stripped.endswith("]"):
                    in_response = False
            elif stripped.endswith("]") and "Request=[" not in line:
                # Request block closes
                in_request = False
                request_lines.append(line)
            else:
                request_lines.append(line)
            continue

        # Check for Request=[...] boundary
        if "Request=[" in line:
            state = "request"
            in_request = True
            request_lines.append(line)
            # Check for single-line request: Request=[...] on one line
            stripped = line.rstrip()
            if stripped.endswith("]") and stripped.count("[") == stripped.count("]"):
                in_request = False
            continue

        # Check for Response=[...] boundary (may appear without Request=)
        if "Response=[" in line:
            state = "response"
            in_response = True
            response_lines.append(line)
            stripped = line.rstrip()
            if stripped.endswith("]") and "Response=[" in line:
                # Single-line response like Response=[] or Response=[content]
                # Count brackets to see if it closes
                after_marker = line[line.index("Response=[") + len("Response=["):]
                if "]" in after_marker:
                    in_response = False
            continue

        # Extract thread_name and sampler_name from first [ERROR]: match
        if match and thread_name is None:
            thread_name = match.group(1).strip()
            sampler_name = match.group(2).strip()
            error_message = match.group(3).strip()
            state = "context"
            continue

        if match and state == "context":
            # Additional [ERROR]: lines before Request= are context
            context_content = match.group(3).strip()
            context_lines.append(context_content)
            continue

        # Non-[ERROR]: lines in context are also captured
        if state == "context":
            context_lines.append(line.strip())

    # Build request_details from collected lines
    request_details = None
    if request_lines:
        request_text = "\n".join(request_lines)
        request_details = extract_request_details(request_text)

    # Build response_details from collected lines
    response_details = None
    if response_lines:
        response_text = "\n".join(response_lines)
        response_details = extract_response_details(response_text)
    else:
        response_details = "[not available]"

    return {
        "thread_name": thread_name,
        "sampler_name": sampler_name,
        "error_message": error_message,
        "request_details": request_details,
        "response_details": response_details,
        "context_lines": context_lines,
    }


# ============================================================
# Error Categorization
# ============================================================

def _categorize_error(entry: dict) -> Optional[Tuple[str, str]]:
    """
    Determine error category and severity for a parsed error entry.

    Classification priority:
      0. HTTP 2xx/3xx response codes → None (skip — not application errors)
      1. FATAL log level → "Fatal JMeter Error" (Critical)
      2. HTTP response code → "HTTP 5xx Error" or "HTTP 4xx Error"
      3. Keyword matching against ERROR_CATEGORIES
      4. JSR223 [ERROR]: marker → "Custom Logic Error" (High)
      5. Fallback → "General Error" (Medium)

    Args:
        entry: Parsed error entry dict (must contain at minimum:
               log_level, response_code, error_message, raw_block).

    Returns:
        Tuple of (error_category, severity), or None if the entry
        should be skipped (e.g., 2xx/3xx response codes that are
        not real application errors).
    """
    log_level = (entry.get("log_level") or "").upper()
    response_code = entry.get("response_code", "N/A")
    error_message = entry.get("error_message", "")
    raw_block = entry.get("raw_block", "")
    search_text = f"{error_message} {raw_block}"

    # 0. Skip 2xx/3xx response codes — these are valid HTTP responses,
    #    not application errors. They appear in logs when a JMeter
    #    Response Assertion is overly strict (e.g., expecting 200 but
    #    receiving 202 Accepted or 302 Redirect). This is a script
    #    issue, not an application or environment issue.
    if response_code != "N/A":
        try:
            code_int = int(response_code)
            if 200 <= code_int < 400:
                return None
        except (ValueError, TypeError):
            pass

    # 1. FATAL log level
    if log_level == "FATAL":
        return ("Fatal JMeter Error", "Critical")

    # 2. HTTP response code classification
    if response_code != "N/A":
        try:
            code_int = int(response_code)
            for category_name, category_def in ERROR_CATEGORIES.items():
                if "code_range" in category_def and code_int in category_def["code_range"]:
                    return (category_name, category_def["severity"])
        except (ValueError, TypeError):
            pass

    # 3. Keyword matching against ERROR_CATEGORIES (non-HTTP-code categories)
    for category_name, category_def in ERROR_CATEGORIES.items():
        if "keywords" not in category_def:
            continue
        for keyword in category_def["keywords"]:
            if keyword.lower() in search_text.lower():
                return (category_name, category_def["severity"])

    # 4. JSR223 [ERROR]: marker → Custom Logic Error
    if "[ERROR]:" in raw_block:
        return ("Custom Logic Error", "High")

    # 5. Fallback
    return ("General Error", "Medium")


# ============================================================
# Grouping & Deduplication
# ============================================================

def _group_errors(entries: List[dict]) -> List[dict]:
    """
    Group error entries by signature and aggregate statistics.

    For each unique signature (via log_utils.generate_error_signature):
      - Count occurrences
      - Collect distinct thread names
      - Track first and last occurrence timestamps
      - Select first occurrence details (description, request, response)
      - Collect sample line numbers (first 10)

    Groups are sorted by severity (Critical → Medium), then by
    error_count (descending).

    Error IDs are assigned sequentially: ERR-001, ERR-002, etc.

    Args:
        entries: List of categorized error entry dicts.

    Returns:
        List of grouped error dicts, each containing:
          error_id, error_category, severity, response_code,
          api_endpoint, error_count, affected_threads,
          first_occurrence, last_occurrence,
          first_occurrence_description, first_occurrence_request,
          first_occurrence_response, log_file, sample_line_numbers
    """
    if not entries:
        return []

    # Group entries by signature
    signature_groups: Dict[str, List[dict]] = defaultdict(list)
    for entry in entries:
        sig = generate_error_signature(
            entry.get("error_category", "General Error"),
            entry.get("response_code", "N/A"),
            entry.get("api_endpoint", "N/A"),
            entry.get("error_message", ""),
        )
        signature_groups[sig].append(entry)

    # Build grouped results
    grouped = []
    for sig, group_entries in signature_groups.items():
        # Sort entries by timestamp (earliest first) for consistent ordering
        group_entries.sort(key=lambda e: e.get("timestamp") or "")

        # Use first entry for category/severity/endpoint metadata
        first = group_entries[0]

        # Collect distinct thread names
        threads = set()
        for e in group_entries:
            if e.get("thread_name"):
                threads.add(e["thread_name"])

        # Collect line numbers (first 10)
        line_numbers = [
            e["line_number"] for e in group_entries[:10]
            if e.get("line_number")
        ]

        # Collect log files (could span multiple if multiple .log files)
        log_files = set()
        for e in group_entries:
            if e.get("log_file"):
                log_files.add(e["log_file"])

        # Get first and last timestamps
        timestamps = [
            e["timestamp"] for e in group_entries
            if e.get("timestamp")
        ]
        first_occurrence = timestamps[0] if timestamps else None
        last_occurrence = timestamps[-1] if timestamps else None

        # Get first occurrence details (description, request, response)
        first_details = _select_first_occurrence_details(group_entries)

        grouped.append({
            "error_id": None,  # Assigned after sorting
            "error_category": first.get("error_category", "General Error"),
            "severity": first.get("severity", "Medium"),
            "response_code": first.get("response_code", "N/A"),
            "api_endpoint": first.get("api_endpoint", "N/A"),
            "error_count": len(group_entries),
            "affected_threads": sorted(threads),
            "first_occurrence": first_occurrence,
            "last_occurrence": last_occurrence,
            "first_occurrence_description": first_details["first_occurrence_description"],
            "first_occurrence_request": first_details["first_occurrence_request"],
            "first_occurrence_response": first_details["first_occurrence_response"],
            "first_occurrence_thread": first_details["first_occurrence_thread"],
            "log_file": "; ".join(sorted(log_files)),
            "sample_line_numbers": line_numbers,
        })

    # Sort by severity (Critical → Medium), then by error_count (descending)
    grouped.sort(
        key=lambda g: (
            SEVERITY_ORDER.get(g["severity"], 99),
            -g["error_count"],
        )
    )

    # Assign sequential error IDs
    for idx, group in enumerate(grouped, start=1):
        group["error_id"] = f"ERR-{idx:03d}"

    return grouped


def _select_first_occurrence_details(entries: List[dict]) -> dict:
    """
    Select the first occurrence from a list of same-signature entries
    and extract its description, request, and response details.

    Description, request, and response are truncated per config limits.

    Args:
        entries: List of error entries sharing the same signature,
                 sorted by timestamp (earliest first).

    Returns:
        dict with: first_occurrence_description, first_occurrence_request,
        first_occurrence_response, first_occurrence_thread
    """
    if not entries:
        return {
            "first_occurrence_description": "",
            "first_occurrence_request": "[not available]",
            "first_occurrence_response": "[not available]",
            "first_occurrence_thread": None,
        }

    first = entries[0]

    # Build description from error message
    description = first.get("error_message", "")
    description = truncate(description, MAX_DESCRIPTION_LENGTH)

    # Request details — truncated
    request = first.get("request_details")
    if request:
        request = truncate(request, MAX_REQUEST_LENGTH)
    else:
        request = "[not available]"

    # Response details — truncated
    response = first.get("response_details")
    if response:
        response = truncate(response, MAX_RESPONSE_LENGTH)
    else:
        response = "[not available]"

    return {
        "first_occurrence_description": description,
        "first_occurrence_request": request,
        "first_occurrence_response": response,
        "first_occurrence_thread": first.get("thread_name"),
    }


# ============================================================
# JTL Correlation
# ============================================================

def _discover_jtl_file(test_run_id: str, log_source: str) -> Optional[str]:
    """
    Find the single JTL/CSV result file for correlation.

    Discovery order (stop at first match):
      1. test-results.csv (BlazeMeter convention)
      2. <test_run_id>.jtl (JMeter convention)
      3. Any single *.jtl file (fallback)

    Args:
        test_run_id: Test run identifier.
        log_source: Source folder ("jmeter" or "blazemeter").

    Returns:
        Absolute file path, or None if no JTL file found.
    """
    source_dir = get_source_artifacts_dir(test_run_id, log_source)

    if not os.path.isdir(source_dir):
        return None

    # 1. Check for BlazeMeter convention: test-results.csv
    csv_path = os.path.join(source_dir, "test-results.csv")
    if os.path.isfile(csv_path):
        return csv_path

    # 2. Check for JMeter convention: <test_run_id>.jtl
    jtl_path = os.path.join(source_dir, f"{test_run_id}.jtl")
    if os.path.isfile(jtl_path):
        return jtl_path

    # 3. Fallback: any single .jtl file
    jtl_files = discover_files_by_extension(source_dir, ".jtl")
    if jtl_files:
        return jtl_files[0]

    return None


def _parse_jtl_file(file_path: str) -> Tuple[List[dict], dict]:
    """
    Parse a JTL/CSV result file into a list of result dicts
    and file metadata.

    Both .jtl and .csv files are CSV format — Python's csv module
    handles both identically.

    Expected headers:
      timeStamp, elapsed, label, responseCode, responseMessage,
      threadName, success, bytes, ...

    Args:
        file_path: Absolute path to the JTL/CSV file.

    Returns:
        Tuple of:
          - List of result dicts (one per sampler row)
          - File metadata dict with: filename, path,
            total_samples, failed_samples
    """
    results = []
    total_samples = 0
    failed_samples = 0

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_samples += 1
            # Normalize the success field
            success_val = (row.get("success") or "").strip().lower()
            is_success = success_val == "true"
            if not is_success:
                failed_samples += 1

            results.append({
                "timeStamp": row.get("timeStamp", ""),
                "elapsed": row.get("elapsed", ""),
                "label": row.get("label", ""),
                "responseCode": row.get("responseCode", ""),
                "responseMessage": row.get("responseMessage", ""),
                "threadName": row.get("threadName", ""),
                "success": is_success,
                "bytes": row.get("bytes", ""),
            })

    metadata = {
        "filename": os.path.basename(file_path),
        "path": file_path,
        "total_samples": total_samples,
        "failed_samples": failed_samples,
    }

    return results, metadata


def _correlate_with_jtl(
    error_groups: List[dict],
    jtl_data: List[dict],
) -> Tuple[List[dict], List[dict], dict]:
    """
    Enrich grouped errors with JTL data and identify JTL-only failures.

    Matching strategy:
      - Match by sampler label + thread name + time window (±2 seconds)
      - Enrich matched groups with: jtl_response_code, jtl_response_message,
        jtl_elapsed_ms
      - Identify JTL rows with success=false not matched to any log error

    Args:
        error_groups: List of grouped error dicts.
        jtl_data: List of parsed JTL result dicts.

    Returns:
        Tuple of:
          - Enriched error groups (with JTL fields added)
          - JTL-only failures (list of dicts)
          - Correlation stats dict with: log_errors_matched_to_jtl,
            jtl_only_failures, unmatched_log_errors
    """
    matched_count = 0
    unmatched_count = 0
    matched_jtl_indices = set()

    # Build a lookup of failed JTL rows for quick access
    # Key: (label_lower, threadName_lower) → list of (index, row)
    jtl_failure_index: Dict[tuple, List[Tuple[int, dict]]] = defaultdict(list)
    for idx, row in enumerate(jtl_data):
        if not row["success"]:
            key = (
                row["label"].strip().lower(),
                row["threadName"].strip().lower(),
            )
            jtl_failure_index[key].append((idx, row))

    # For each error group, try to find a matching JTL failure
    for group in error_groups:
        # Initialize JTL fields with empty defaults
        group["jtl_response_code"] = ""
        group["jtl_response_message"] = ""
        group["jtl_elapsed_ms"] = ""

        # Try matching by sampler name (from log) against label (from JTL)
        # and thread name overlap
        sampler = (group.get("api_endpoint") or "").strip().lower()
        group_threads = [t.lower() for t in group.get("affected_threads", [])]

        best_match = None

        # Strategy 1: Match by thread name from the group
        for thread in group_threads:
            for (label_lower, thread_lower), entries in jtl_failure_index.items():
                # Check if any thread from the group matches the JTL thread
                if thread in thread_lower or thread_lower in thread:
                    # Check if the label relates to the API endpoint or sampler
                    if (sampler and sampler != "n/a" and
                            (sampler in label_lower or label_lower in sampler)):
                        # Good match — take the first one
                        if entries:
                            best_match = entries[0]
                            break
            if best_match:
                break

        # Strategy 2: Fallback — match by API endpoint in label only
        if not best_match and sampler and sampler != "n/a":
            for (label_lower, thread_lower), entries in jtl_failure_index.items():
                if sampler in label_lower or label_lower in sampler:
                    if entries:
                        best_match = entries[0]
                        break

        if best_match:
            idx, row = best_match
            group["jtl_response_code"] = row.get("responseCode", "")
            group["jtl_response_message"] = row.get("responseMessage", "")
            group["jtl_elapsed_ms"] = row.get("elapsed", "")
            matched_jtl_indices.add(idx)
            matched_count += 1
        else:
            unmatched_count += 1

    # Identify JTL-only failures (failed rows not matched to any log error)
    jtl_only_failures = []
    for idx, row in enumerate(jtl_data):
        if not row["success"] and idx not in matched_jtl_indices:
            jtl_only_failures.append({
                "label": row.get("label", ""),
                "responseCode": row.get("responseCode", ""),
                "responseMessage": row.get("responseMessage", ""),
                "threadName": row.get("threadName", ""),
                "timeStamp": row.get("timeStamp", ""),
                "elapsed": row.get("elapsed", ""),
            })

    correlation_stats = {
        "log_errors_matched_to_jtl": matched_count,
        "jtl_only_failures": len(jtl_only_failures),
        "unmatched_log_errors": unmatched_count,
    }

    return error_groups, jtl_only_failures, correlation_stats


# ============================================================
# Output Formatting
# ============================================================

def _format_csv_rows(
    grouped_errors: List[dict],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Build CSV fieldnames and row dicts from grouped errors.

    Values are sanitized for CSV safety (newlines replaced, etc.)
    via log_utils.sanitize_for_csv().

    Actual file write is performed by file_utils.save_csv_file().

    Args:
        grouped_errors: List of grouped error dicts.

    Returns:
        Tuple of (fieldnames list, rows list of dicts).
    """
    fieldnames = [
        "error_id",
        "error_category",
        "severity",
        "response_code",
        "api_endpoint",
        "error_count",
        "affected_threads",
        "first_occurrence",
        "last_occurrence",
        "first_occurrence_description",
        "first_occurrence_request",
        "first_occurrence_response",
        "jtl_response_code",
        "jtl_response_message",
        "jtl_elapsed_ms",
        "log_file",
        "sample_line_numbers",
    ]

    rows = []
    for group in grouped_errors:
        rows.append({
            "error_id": group.get("error_id", ""),
            "error_category": group.get("error_category", ""),
            "severity": group.get("severity", ""),
            "response_code": group.get("response_code", ""),
            "api_endpoint": group.get("api_endpoint", ""),
            "error_count": group.get("error_count", 0),
            "affected_threads": sanitize_for_csv(
                "; ".join(group.get("affected_threads", []))
            ),
            "first_occurrence": group.get("first_occurrence", ""),
            "last_occurrence": group.get("last_occurrence", ""),
            "first_occurrence_description": sanitize_for_csv(
                group.get("first_occurrence_description", "")
            ),
            "first_occurrence_request": sanitize_for_csv(
                group.get("first_occurrence_request", "")
            ),
            "first_occurrence_response": sanitize_for_csv(
                group.get("first_occurrence_response", "")
            ),
            "jtl_response_code": group.get("jtl_response_code", ""),
            "jtl_response_message": group.get("jtl_response_message", ""),
            "jtl_elapsed_ms": group.get("jtl_elapsed_ms", ""),
            "log_file": group.get("log_file", ""),
            "sample_line_numbers": "; ".join(
                str(ln) for ln in group.get("sample_line_numbers", [])
            ),
        })

    return fieldnames, rows


def _format_json_output(
    test_run_id: str,
    log_source: str,
    grouped_errors: List[dict],
    log_file_metadata: List[dict],
    jtl_file_metadata: Optional[dict],
    jtl_only_failures: List[dict],
    jtl_correlation_stats: dict,
) -> dict:
    """
    Build complete JSON output dict with metadata, summary, and issues.

    Actual file write is performed by file_utils.save_json_file().

    Args:
        test_run_id: Test run identifier.
        log_source: Source analyzed ("jmeter" or "blazemeter").
        grouped_errors: List of grouped error dicts.
        log_file_metadata: List of metadata dicts for each log file.
        jtl_file_metadata: Metadata dict for the JTL file, or None.
        jtl_only_failures: List of JTL-only failure dicts.
        jtl_correlation_stats: Correlation statistics dict.

    Returns:
        Complete JSON-serializable dict matching the output schema.
    """
    # Build severity breakdown
    issues_by_severity: Dict[str, int] = defaultdict(int)
    issues_by_category: Dict[str, int] = defaultdict(int)
    total_occurrences = 0

    for group in grouped_errors:
        issues_by_severity[group["severity"]] += 1
        issues_by_category[group["error_category"]] += group["error_count"]
        total_occurrences += group["error_count"]

    # Build top affected APIs
    api_errors: Dict[str, Dict] = defaultdict(
        lambda: {"total_errors": 0, "error_categories": set()}
    )
    for group in grouped_errors:
        endpoint = group.get("api_endpoint", "N/A")
        api_errors[endpoint]["total_errors"] += group["error_count"]
        api_errors[endpoint]["error_categories"].add(group["error_category"])

    top_apis = sorted(
        [
            {
                "api_endpoint": ep,
                "total_errors": data["total_errors"],
                "error_categories": sorted(data["error_categories"]),
            }
            for ep, data in api_errors.items()
        ],
        key=lambda x: -x["total_errors"],
    )[:10]

    # Error timeline
    timestamps = []
    for group in grouped_errors:
        if group.get("first_occurrence"):
            timestamps.append(group["first_occurrence"])
        if group.get("last_occurrence"):
            timestamps.append(group["last_occurrence"])
    timestamps.sort()

    first_error = timestamps[0] if timestamps else None
    last_error = timestamps[-1] if timestamps else None

    # Build issues list for JSON
    issues_list = []
    for group in grouped_errors:
        issues_list.append({
            "error_id": group["error_id"],
            "error_category": group["error_category"],
            "severity": group["severity"],
            "response_code": group["response_code"],
            "api_endpoint": group["api_endpoint"],
            "error_count": group["error_count"],
            "affected_threads": group["affected_threads"],
            "first_occurrence": group["first_occurrence"],
            "last_occurrence": group["last_occurrence"],
            "first_occurrence_description": group["first_occurrence_description"],
            "first_occurrence_request": group["first_occurrence_request"],
            "first_occurrence_response": group["first_occurrence_response"],
            "jtl_response_code": group.get("jtl_response_code", ""),
            "jtl_response_message": group.get("jtl_response_message", ""),
            "jtl_elapsed_ms": group.get("jtl_elapsed_ms", ""),
            "log_file": group["log_file"],
            "sample_line_numbers": group["sample_line_numbers"],
        })

    return {
        "test_run_id": test_run_id,
        "log_source": log_source,
        "analysis_timestamp": datetime.now().isoformat(),
        "tool_version": TOOL_VERSION,
        "configuration": {
            "max_description_length": MAX_DESCRIPTION_LENGTH,
            "max_request_length": MAX_REQUEST_LENGTH,
            "max_response_length": MAX_RESPONSE_LENGTH,
            "error_levels": ERROR_LEVELS,
        },
        "log_files_analyzed": log_file_metadata,
        "jtl_files_analyzed": [jtl_file_metadata] if jtl_file_metadata else [],
        "summary": {
            "total_unique_issues": len(grouped_errors),
            "total_occurrences": total_occurrences,
            "issues_by_severity": dict(issues_by_severity),
            "issues_by_category": dict(issues_by_category),
            "top_affected_apis": top_apis,
            "error_timeline": {
                "first_error": first_error,
                "last_error": last_error,
            },
            "jtl_correlation": jtl_correlation_stats,
        },
        "issues": issues_list,
    }


def _format_markdown_output(
    test_run_id: str,
    log_source: str,
    grouped_errors: List[dict],
    log_file_metadata: List[dict],
    jtl_file_metadata: Optional[dict],
    jtl_only_failures: List[dict],
    jtl_correlation_stats: dict,
) -> str:
    """
    Build complete Markdown report string.

    Sections:
      1. Header (test run ID, log source, date, files analyzed)
      2. Executive Summary (totals, severity breakdown, time window)
      3. Issues by Severity (tables for Critical, High, Medium)
      4. Top Affected APIs
      5. Error Category Breakdown
      6. First Occurrence Details (per issue with request/response)
      7. JTL Correlation Summary
      8. Log Files Analyzed

    Actual file write is performed by file_utils.save_markdown_file().

    Args:
        test_run_id: Test run identifier.
        log_source: Source analyzed ("jmeter" or "blazemeter").
        grouped_errors: List of grouped error dicts.
        log_file_metadata: List of metadata dicts for each log file.
        jtl_file_metadata: Metadata dict for the JTL file, or None.
        jtl_only_failures: List of JTL-only failure dicts.
        jtl_correlation_stats: Correlation statistics dict.

    Returns:
        Complete Markdown report as a string.
    """
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- Compute summary stats ---
    total_occurrences = sum(g["error_count"] for g in grouped_errors)
    severity_counts: Dict[str, int] = defaultdict(int)
    category_counts: Dict[str, Dict] = defaultdict(
        lambda: {"occurrences": 0, "unique": 0}
    )
    for g in grouped_errors:
        severity_counts[g["severity"]] += 1
        category_counts[g["error_category"]]["occurrences"] += g["error_count"]
        category_counts[g["error_category"]]["unique"] += 1

    timestamps = []
    for g in grouped_errors:
        if g.get("first_occurrence"):
            timestamps.append(g["first_occurrence"])
        if g.get("last_occurrence"):
            timestamps.append(g["last_occurrence"])
    timestamps.sort()
    first_error = timestamps[0] if timestamps else "N/A"
    last_error = timestamps[-1] if timestamps else "N/A"

    # === 1. Header ===
    lines.append("# JMeter Log Analysis Report")
    lines.append("")
    lines.append(f"**Test Run ID:** {test_run_id}  ")
    lines.append(f"**Log Source:** {log_source}  ")
    lines.append(f"**Analysis Date:** {now}  ")
    lines.append(f"**Log Files Analyzed:** {len(log_file_metadata)} file(s)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # === 2. Executive Summary ===
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- **Total Unique Issues:** {len(grouped_errors)}")
    lines.append(f"- **Total Error Occurrences:** {total_occurrences}")
    severity_str = " | ".join(
        f"{sev}: {severity_counts.get(sev, 0)}"
        for sev in ["Critical", "High", "Medium"]
    )
    lines.append(f"- **Severity Breakdown:** {severity_str}")
    lines.append(f"- **Error Time Window:** {first_error} to {last_error}")
    if jtl_correlation_stats:
        matched = jtl_correlation_stats.get("log_errors_matched_to_jtl", 0)
        total_groups = len(grouped_errors)
        lines.append(
            f"- **JTL Correlation:** {matched} of {total_groups} "
            f"error groups matched to JTL entries"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # === 3. Issues by Severity ===
    lines.append("## Issues by Severity")
    lines.append("")

    for severity in ["Critical", "High", "Medium"]:
        severity_errors = [
            g for g in grouped_errors if g["severity"] == severity
        ]
        count = len(severity_errors)
        lines.append(f"### {severity} Issues ({count})")
        lines.append("")

        if severity_errors:
            lines.append(
                "| # | Error Category | Code | Count | API Endpoint | Description |"
            )
            lines.append(
                "|---|----------------|------|-------|--------------|-------------|"
            )
            for g in severity_errors:
                desc = sanitize_for_csv(g.get("first_occurrence_description", ""))
                # Truncate for table readability
                desc = truncate(desc, 80)
                endpoint = truncate(g.get("api_endpoint", "N/A"), 50)
                lines.append(
                    f"| {g['error_id']} "
                    f"| {g['error_category']} "
                    f"| {g['response_code']} "
                    f"| {g['error_count']} "
                    f"| {endpoint} "
                    f"| {desc} |"
                )
            lines.append("")
        else:
            lines.append("No issues at this severity level.")
            lines.append("")

    lines.append("---")
    lines.append("")

    # === 4. Top Affected APIs ===
    lines.append("## Top Affected APIs")
    lines.append("")

    api_errors: Dict[str, Dict] = defaultdict(
        lambda: {"total": 0, "categories": set(), "codes": set()}
    )
    for g in grouped_errors:
        ep = g.get("api_endpoint", "N/A")
        api_errors[ep]["total"] += g["error_count"]
        api_errors[ep]["categories"].add(g["error_category"])
        if g["response_code"] != "N/A":
            api_errors[ep]["codes"].add(g["response_code"])

    sorted_apis = sorted(api_errors.items(), key=lambda x: -x[1]["total"])[:10]

    if sorted_apis:
        lines.append(
            "| API Endpoint | Total Errors | Error Categories | Response Codes |"
        )
        lines.append("|---|---|---|---|")
        for ep, data in sorted_apis:
            cats = ", ".join(sorted(data["categories"]))
            codes = ", ".join(sorted(data["codes"])) if data["codes"] else "N/A"
            lines.append(f"| {truncate(ep, 50)} | {data['total']} | {cats} | {codes} |")
        lines.append("")
    else:
        lines.append("No affected APIs identified.")
        lines.append("")

    lines.append("---")
    lines.append("")

    # === 5. Error Category Breakdown ===
    lines.append("## Error Category Breakdown")
    lines.append("")
    lines.append("| Category | Occurrences | Unique Issues | Severity |")
    lines.append("|---|---|---|---|")

    # Sort categories by occurrences descending
    sorted_cats = sorted(
        category_counts.items(), key=lambda x: -x[1]["occurrences"]
    )
    for cat_name, cat_data in sorted_cats:
        # Find the severity for this category
        cat_severity = "Medium"
        for g in grouped_errors:
            if g["error_category"] == cat_name:
                cat_severity = g["severity"]
                break
        lines.append(
            f"| {cat_name} | {cat_data['occurrences']} "
            f"| {cat_data['unique']} | {cat_severity} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")

    # === 6. First Occurrence Details ===
    lines.append("## First Occurrence Details")
    lines.append("")

    for g in grouped_errors:
        lines.append(
            f"### {g['error_id']}: {g['error_category']} "
            f"— {truncate(g['api_endpoint'], 60)} ({g['response_code']})"
        )
        lines.append("")
        lines.append(f"**First Seen:** {g.get('first_occurrence', 'N/A')}  ")
        lines.append(
            f"**Thread:** {g.get('first_occurrence_thread', 'N/A')}  "
        )
        lines.append(f"**Occurrences:** {g['error_count']}")
        lines.append("")

        lines.append("**Error Message:**")
        desc = g.get("first_occurrence_description", "N/A")
        lines.append(f"> {sanitize_for_csv(desc)}")
        lines.append("")

        req = g.get("first_occurrence_request", "[not available]")
        lines.append("**Request:**")
        lines.append(f"> {sanitize_for_csv(req)}")
        lines.append("")

        resp = g.get("first_occurrence_response", "[not available]")
        lines.append("**Response:**")
        lines.append(f"> {sanitize_for_csv(resp)}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # === 7. JTL Correlation Summary ===
    lines.append("## JTL Correlation Summary")
    lines.append("")

    if jtl_correlation_stats:
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(
            f"| Log errors matched to JTL "
            f"| {jtl_correlation_stats.get('log_errors_matched_to_jtl', 0)} |"
        )
        lines.append(
            f"| JTL-only failures (no log entry) "
            f"| {jtl_correlation_stats.get('jtl_only_failures', 0)} |"
        )
        lines.append(
            f"| Unmatched log errors "
            f"| {jtl_correlation_stats.get('unmatched_log_errors', 0)} |"
        )
        lines.append("")

        if jtl_only_failures:
            lines.append("### JTL-Only Failures")
            lines.append("")
            lines.append("| Label | Response Code | Thread | Timestamp |")
            lines.append("|---|---|---|---|")
            for failure in jtl_only_failures[:20]:  # Limit to first 20
                lines.append(
                    f"| {truncate(failure.get('label', ''), 50)} "
                    f"| {failure.get('responseCode', '')} "
                    f"| {truncate(failure.get('threadName', ''), 40)} "
                    f"| {failure.get('timeStamp', '')} |"
                )
            if len(jtl_only_failures) > 20:
                lines.append(
                    f"| ... | ... | ... | "
                    f"({len(jtl_only_failures) - 20} more not shown) |"
                )
            lines.append("")
    else:
        lines.append("No JTL file found for correlation.")
        lines.append("")

    lines.append("---")
    lines.append("")

    # === 8. Log Files Analyzed ===
    lines.append("## Log Files Analyzed")
    lines.append("")
    lines.append(
        "| File | Size | Total Lines "
        "| Error Blocks (Total) | Skipped (2xx/3xx) | Relevant |"
    )
    lines.append("|---|---|---|---|---|---|")

    for meta in log_file_metadata:
        size_mb = meta.get("size_bytes", 0) / (1024 * 1024)
        size_str = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{meta.get('size_bytes', 0):,} bytes"
        total_blocks = meta.get("error_blocks_total", meta.get("error_lines", 0))
        skipped = meta.get("error_blocks_skipped", 0)
        relevant = meta.get("error_blocks_relevant", total_blocks)
        lines.append(
            f"| {meta.get('filename', '')} "
            f"| {size_str} "
            f"| {meta.get('total_lines', 0):,} "
            f"| {total_blocks:,} "
            f"| {skipped:,} "
            f"| {relevant:,} |"
        )

    # Add footnote if any files had skipped blocks
    has_skipped = any(
        meta.get("error_blocks_skipped", 0) > 0 for meta in log_file_metadata
    )
    if has_skipped:
        lines.append("")
        lines.append(
            "> **Note:** Skipped error blocks contain HTTP 2xx/3xx response codes "
            "which are valid responses, not application errors. These typically "
            "result from overly strict JMeter Response Assertions in the test script."
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Generated by JMeter MCP — analyze_jmeter_log v{TOOL_VERSION}*")
    lines.append("")

    return "\n".join(lines)
