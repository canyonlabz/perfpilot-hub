"""
services/helpers/diffengine_report_helpers.py

Report generation helpers for the HAR-JMX diff engine.

Builds JSON and Markdown comparison reports from the enriched matching
results produced by har_jmx_diffengine.analyze_differences(), and
persists them as versioned files under artifacts/<run_id>/jmeter/analysis/.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.config import load_config
from utils.file_utils import (
    get_jmeter_analysis_dir,
    rotate_analysis_files,
    save_json_file,
    save_markdown_file,
)

logger = logging.getLogger(__name__)

# ============================================================
# Phase D — Report Generation
# ============================================================

_COMPARISON_FILE_PREFIX = "har_jmx_comparison_"


def _get_max_analysis_files() -> int:
    """Read max_analysis_files from jmx_editing config (default 10)."""
    try:
        cfg = load_config()
        return cfg.get("jmx_editing", {}).get("max_analysis_files", 10)
    except Exception:
        return 10


def _build_report_summary(
    diff_summary: Dict[str, int],
    match_stats: Dict[str, int],
) -> Dict[str, int]:
    """Build the top-level summary section for the JSON report."""
    return {
        "matched_no_changes": diff_summary.get("matched_no_changes", 0),
        "new_endpoints": match_stats.get("new_endpoints", 0),
        "removed_endpoints": match_stats.get("removed_endpoints", 0),
        "url_method_changes": (
            diff_summary.get("url_change", 0)
            + diff_summary.get("method_change", 0)
        ),
        "payload_changes": (
            diff_summary.get("payload_field_added", 0)
            + diff_summary.get("payload_field_removed", 0)
            + diff_summary.get("payload_field_type_changed", 0)
        ),
        "response_schema_changes": diff_summary.get("response_schema_change", 0),
        "correlation_drift": diff_summary.get("correlation_drift", 0),
        "status_code_changes": diff_summary.get("status_code_change", 0),
        "query_param_changes": diff_summary.get("query_param_change", 0),
        "header_changes": diff_summary.get("header_change", 0),
    }


def _suggest_location_for_new_endpoint(
    new_ep: Dict[str, Any],
    matches: List[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """
    Suggest where a new endpoint should be inserted in the JMX.

    Heuristic: find the last matched sampler whose HAR index is before
    this new endpoint's HAR index. That sampler's node_id and parent
    controller provide the insertion context.
    """
    har_idx = new_ep.get("har_entry_index", 0)
    best_match = None
    best_har_idx = -1

    for m in matches:
        m_har_idx = m["har_entry"].get("har_entry_index", 0)
        if m_har_idx < har_idx and m_har_idx > best_har_idx:
            best_har_idx = m_har_idx
            best_match = m

    if best_match:
        return {
            "after_node_id": best_match["jmx_sampler"]["node_id"],
            "parent_controller": best_match["jmx_sampler"].get(
                "parent_controller", ""
            ) or "",
        }
    return None


def build_json_report(
    har_metadata: Dict[str, Any],
    jmx_metadata: Dict[str, Any],
    analysis_result: Dict[str, Any],
    jmx_structure_file: Optional[str] = None,
    strict_matching: bool = False,
) -> Dict[str, Any]:
    """
    Assemble the full JSON comparison report.

    Args:
        har_metadata: From extract_har_entries().
        jmx_metadata: From extract_jmx_samplers().
        analysis_result: From analyze_differences() (enriched matching result).
        jmx_structure_file: Path to structure file used, if any.
        strict_matching: Whether strict matching was enabled.

    Returns:
        Complete report dict ready for JSON serialization.
    """
    diff_summary = analysis_result.get("diff_summary", {})
    match_stats = analysis_result.get("match_stats", {})
    matches = analysis_result.get("matches", [])

    new_endpoints_raw = analysis_result.get("new_endpoints", [])
    new_endpoints = []
    for ep in new_endpoints_raw:
        entry = dict(ep)
        suggested = _suggest_location_for_new_endpoint(ep, matches)
        if suggested:
            entry["suggested_location"] = suggested
        new_endpoints.append(entry)

    return {
        "metadata": {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "har_file": har_metadata.get("har_file", ""),
            "jmx_file": jmx_metadata.get("jmx_file", ""),
            "jmx_structure_file": (
                os.path.basename(jmx_structure_file)
                if jmx_structure_file else None
            ),
            "har_entries_total": har_metadata.get("entries_total", 0),
            "har_entries_after_filter": har_metadata.get("entries_after_filter", 0),
            "jmx_samplers_total": jmx_metadata.get("jmx_samplers_total", 0),
            "strict_matching": strict_matching,
        },
        "summary": _build_report_summary(diff_summary, match_stats),
        "match_stats": match_stats,
        "matches": matches,
        "new_endpoints": new_endpoints,
        "removed_endpoints": analysis_result.get("removed_endpoints", []),
    }


# ============================================================
# Phase D — Markdown Report Builder
# ============================================================


def build_markdown_report(report: Dict[str, Any]) -> str:
    """
    Render a human-readable Markdown summary from the JSON report.

    Sections:
    - Executive Summary table
    - High severity: new endpoints, correlation drift, URL/method changes
    - Medium severity: payload changes, response schema changes
    - Low severity: status code, query param, header changes
    - Possibly removed endpoints
    """
    meta = report.get("metadata", {})
    summary = report.get("summary", {})
    matches = report.get("matches", [])
    new_eps = report.get("new_endpoints", [])
    removed_eps = report.get("removed_endpoints", [])

    generated = meta.get("generated_at", "")
    har_file = meta.get("har_file", "")
    jmx_file = meta.get("jmx_file", "")
    har_filtered = meta.get("har_entries_after_filter", 0)
    jmx_total = meta.get("jmx_samplers_total", 0)

    lines: List[str] = [
        "# HAR vs JMX Comparison Report",
        "",
        f"**Generated:** {generated}",
        f"**HAR File:** {har_file} ({har_filtered} requests after filtering)",
        f"**JMX File:** {jmx_file} ({jmx_total} samplers)",
    ]

    if meta.get("strict_matching"):
        lines.append("**Mode:** Strict matching (fuzzy Pass 3 disabled)")

    lines += [
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        "| Category | Count |",
        "|---|---|",
        f"| Matched (no changes) | {summary.get('matched_no_changes', 0)} |",
        f"| New endpoints (not in JMX) | {summary.get('new_endpoints', 0)} |",
        f"| Possibly removed endpoints | {summary.get('removed_endpoints', 0)} |",
        f"| URL / Method changes | {summary.get('url_method_changes', 0)} |",
        f"| Payload changes | {summary.get('payload_changes', 0)} |",
        f"| Response schema changes | {summary.get('response_schema_changes', 0)} |",
        f"| Correlation drift | {summary.get('correlation_drift', 0)} |",
        f"| Status code changes | {summary.get('status_code_changes', 0)} |",
        f"| Query param changes | {summary.get('query_param_changes', 0)} |",
        f"| Header changes | {summary.get('header_changes', 0)} |",
    ]

    # ---- High Severity ----
    high_section = _md_high_severity(matches, new_eps)
    if high_section:
        lines += ["", "---", "", "## Action Items — High Severity"]
        lines += high_section

    # ---- Medium Severity ----
    medium_section = _md_medium_severity(matches)
    if medium_section:
        lines += ["", "---", "", "## Action Items — Medium Severity"]
        lines += medium_section

    # ---- Low Severity ----
    low_section = _md_low_severity(matches)
    if low_section:
        lines += ["", "---", "", "## Action Items — Low Severity"]
        lines += low_section

    # ---- Removed Endpoints ----
    if removed_eps:
        lines += [
            "",
            "---",
            "",
            "## Possibly Removed Endpoints",
            "",
            "These JMX samplers had no matching HAR entry. They may have been "
            "removed from the application, or they may simply not have been "
            "exercised during this particular HAR capture.",
            "",
            "| node_id | Sampler Name | Method | Enabled |",
            "|---|---|---|---|",
        ]
        for ep in removed_eps:
            enabled = "Yes" if ep.get("enabled", True) else "No"
            lines.append(
                f"| {ep.get('node_id', '')} "
                f"| {ep.get('testname', '')} "
                f"| {ep.get('method', '')} "
                f"| {enabled} |"
            )

    lines.append("")
    return "\n".join(lines)


def _md_high_severity(
    matches: List[Dict[str, Any]],
    new_endpoints: List[Dict[str, Any]],
) -> List[str]:
    """Render high-severity action items."""
    lines: List[str] = []

    # New endpoints
    if new_endpoints:
        lines += [
            "",
            "### New Endpoints — Add to JMX",
            "",
            "| # | Method | URL | Suggested Location |",
            "|---|---|---|---|",
        ]
        for i, ep in enumerate(new_endpoints, 1):
            loc = ep.get("suggested_location", {})
            loc_str = ""
            if loc:
                after = loc.get("after_node_id", "")
                ctrl = loc.get("parent_controller", "")
                loc_str = f"After node {after}"
                if ctrl:
                    loc_str += f" in {ctrl}"
            lines.append(
                f"| {i} | {ep.get('method', '')} "
                f"| {ep.get('url_path', '')} "
                f"| {loc_str} |"
            )

    # Correlation drift
    drift_rows = []
    for m in matches:
        sampler = m["jmx_sampler"]
        for d in m.get("differences", []):
            if d.get("category") == "correlation_drift":
                drift_rows.append((sampler, d))

    if drift_rows:
        lines += [
            "",
            "### Correlation Drift — Extractor Update Required",
            "",
            "| Sampler (node_id) | Extractor | Current Path/Regex | Suggested |",
            "|---|---|---|---|",
        ]
        for sampler, d in drift_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            ext_label = d.get("extractor_name", d.get("refname", ""))
            ext_nid = d.get("extractor_node_id", "")
            if ext_nid:
                ext_label += f" ({ext_nid})"
            current = d.get("current_json_path", d.get("current_regex", ""))
            suggested = d.get(
                "suggested_json_path",
                d.get("suggested_source_field", "—"),
            )
            lines.append(f"| {sampler_label} | {ext_label} | {current} | {suggested} |")

    # URL / Method changes
    url_method_rows = []
    for m in matches:
        sampler = m["jmx_sampler"]
        har = m["har_entry"]
        for d in m.get("differences", []):
            if d.get("category") in ("url_change", "method_change"):
                url_method_rows.append((sampler, har, d))

    if url_method_rows:
        lines += [
            "",
            "### URL / Method Changes",
            "",
            "| JMX Sampler (node_id) | Current | HAR Observed | Change Type |",
            "|---|---|---|---|",
        ]
        for sampler, har, d in url_method_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            if d["category"] == "url_change":
                current = f"{sampler.get('method', '')} {sampler.get('url_pattern', '')}"
                observed = f"{har['method']} {har['url_path']}"
                change = "URL path change"
            else:
                current = f"{d.get('jmx_method', '')} {sampler.get('url_pattern', '')}"
                observed = f"{d.get('har_method', '')} {har['url_path']}"
                change = "HTTP method change"
            lines.append(f"| {sampler_label} | {current} | {observed} | {change} |")

    return lines


def _md_medium_severity(matches: List[Dict[str, Any]]) -> List[str]:
    """Render medium-severity action items (payload + response schema)."""
    lines: List[str] = []

    payload_rows = []
    for m in matches:
        sampler = m["jmx_sampler"]
        for d in m.get("differences", []):
            if d.get("category", "").startswith("payload_field"):
                payload_rows.append((sampler, d))

    if payload_rows:
        lines += [
            "",
            "### Payload Changes",
            "",
            "| Sampler (node_id) | Field | Change |",
            "|---|---|---|",
        ]
        for sampler, d in payload_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            field = d.get("field", "—")
            lines.append(f"| {sampler_label} | {field} | {d.get('description', '')} |")

    schema_rows = []
    for m in matches:
        sampler = m["jmx_sampler"]
        for d in m.get("differences", []):
            if d.get("category") == "response_schema_change":
                schema_rows.append((sampler, d))

    if schema_rows:
        lines += [
            "",
            "### Response Schema Changes",
            "",
            "| Sampler (node_id) | Extractor | Path | Details |",
            "|---|---|---|---|",
        ]
        for sampler, d in schema_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            ext_name = d.get("extractor_name", "")
            jp = d.get("json_path", "")
            lines.append(
                f"| {sampler_label} | {ext_name} | {jp} | {d.get('description', '')} |"
            )

    return lines


def _md_low_severity(matches: List[Dict[str, Any]]) -> List[str]:
    """Render low-severity action items."""
    lines: List[str] = []

    status_rows = []
    query_rows = []
    header_rows = []

    for m in matches:
        sampler = m["jmx_sampler"]
        for d in m.get("differences", []):
            cat = d.get("category", "")
            if cat == "status_code_change":
                status_rows.append((sampler, d))
            elif cat == "query_param_change":
                query_rows.append((sampler, d))
            elif cat == "header_change":
                header_rows.append((sampler, d))

    if status_rows:
        lines += [
            "",
            "### Status Code Changes",
            "",
            "| Sampler (node_id) | Expected | Observed |",
            "|---|---|---|",
        ]
        for sampler, d in status_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            expected = d.get("expected_statuses", [])
            observed = d.get("har_status", "")
            lines.append(f"| {sampler_label} | {expected} | {observed} |")

    if query_rows:
        lines += [
            "",
            "### Query Parameter Changes",
            "",
            "| Sampler (node_id) | Change | Parameters |",
            "|---|---|---|",
        ]
        for sampler, d in query_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            change = d.get("change_type", "")
            params = ", ".join(d.get("params", []))
            lines.append(f"| {sampler_label} | {change} | {params} |")

    if header_rows:
        lines += [
            "",
            "### Header Changes",
            "",
            "| Sampler (node_id) | Header | Details |",
            "|---|---|---|",
        ]
        for sampler, d in header_rows:
            sampler_label = f"{sampler['testname']} ({sampler['node_id']})"
            header = d.get("header", "")
            lines.append(
                f"| {sampler_label} | {header} | {d.get('description', '')} |"
            )

    return lines


# ============================================================
# Phase D — File Persistence
# ============================================================


def save_comparison_report(
    test_run_id: str,
    report: Dict[str, Any],
    output_format: str = "both",
) -> Dict[str, str]:
    """
    Save the comparison report to versioned files with rotation.

    Args:
        test_run_id: Test run identifier (determines artifact directory).
        report: Full JSON report from build_json_report().
        output_format: "json", "markdown", or "both".

    Returns:
        Dict with keys "json" and/or "markdown" mapping to absolute file paths.
    """
    analysis_dir = get_jmeter_analysis_dir(test_run_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    max_files = _get_max_analysis_files()

    exported: Dict[str, str] = {}

    if output_format in ("json", "both"):
        json_path = os.path.join(
            analysis_dir, f"{_COMPARISON_FILE_PREFIX}{timestamp}.json"
        )
        save_json_file(json_path, report)
        rotate_analysis_files(analysis_dir, _COMPARISON_FILE_PREFIX, max_files)
        exported["json"] = json_path

    if output_format in ("markdown", "both"):
        md_content = build_markdown_report(report)
        md_path = os.path.join(
            analysis_dir, f"{_COMPARISON_FILE_PREFIX}{timestamp}.md"
        )
        save_markdown_file(md_path, md_content)
        rotate_analysis_files(analysis_dir, _COMPARISON_FILE_PREFIX, max_files)
        exported["markdown"] = md_path

    logger.info(
        "Comparison report saved: %s",
        ", ".join(f"{k}={v}" for k, v in exported.items()),
    )

    return exported
