"""
services/helpers/analysis_export_helpers.py

Helper functions for exporting JMX analysis results to versioned
JSON and Markdown files under artifacts/<run_id>/jmeter/analysis/.

Used by jmx_editor.analyze_jmx_file when export_structure=True.
"""

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

ANALYSIS_FILE_PREFIX = "jmx_structure_"


def _get_max_analysis_files() -> int:
    """Read max_analysis_files from jmx_editing config (default 10)."""
    try:
        cfg = load_config()
        return cfg.get("jmx_editing", {}).get("max_analysis_files", 10)
    except Exception:
        return 10


# ============================================================
# JSON builder
# ============================================================

def build_structure_json(
    jmx_path: str,
    detail_level: str,
    summary: dict,
    hierarchy: list,
    node_index_output: Optional[dict],
    variables: Optional[dict],
    outline: str,
) -> dict:
    """Build the JSON-serialisable structure dict for file export."""
    jmx_mtime = os.path.getmtime(jmx_path)
    data: Dict[str, Any] = {
        "script_name": os.path.basename(jmx_path),
        "jmx_path": jmx_path,
        "analyzed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jmx_last_modified": datetime.utcfromtimestamp(jmx_mtime).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "detail_level": detail_level,
        "summary": summary,
        "hierarchy": hierarchy,
    }
    if node_index_output is not None:
        data["node_index"] = node_index_output
    if variables is not None:
        data["variables"] = variables
    data["outline"] = outline
    return data


# ============================================================
# Markdown builder
# ============================================================

def build_structure_markdown(
    jmx_path: str,
    detail_level: str,
    summary: dict,
    outline: str,
    variables: Optional[dict],
) -> str:
    """Render a concise Markdown summary of the JMX structure."""
    script_name = os.path.basename(jmx_path)
    analyzed = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total = summary.get("total_elements", 0)
    by_type = summary.get("by_type", {})

    lines: List[str] = [
        f"# JMX Structure: {script_name}",
        "",
        f"**Analyzed:** {analyzed} | **Total Elements:** {total} | **Detail Level:** {detail_level}",
        "",
        "## Summary",
        "",
        "| Component Type | Count |",
        "|---|---|",
    ]
    for comp_type, count in sorted(by_type.items()):
        lines.append(f"| {comp_type} | {count} |")

    lines += [
        "",
        "## Component Tree",
        "",
        "```",
        outline,
        "```",
    ]

    if variables:
        defined_names = sorted(variables.get("defined", {}).keys()) if isinstance(
            variables.get("defined"), dict
        ) else variables.get("defined", [])
        used_names = variables.get("used", [])
        undefined_names = variables.get("undefined", [])

        lines += [
            "",
            "## Variables",
            "",
            "| Category | Variables |",
            "|---|---|",
            f"| Defined | {', '.join(defined_names) if defined_names else '—'} |",
            f"| Used | {', '.join(used_names) if used_names else '—'} |",
            f"| Undefined | {', '.join(undefined_names) if undefined_names else '—'} |",
        ]

    lines.append("")
    return "\n".join(lines)


# ============================================================
# Export orchestrator
# ============================================================

def export_structure_files(
    test_run_id: str,
    jmx_path: str,
    detail_level: str,
    summary: dict,
    hierarchy: list,
    node_index_output: Optional[dict],
    variables: Optional[dict],
    outline: str,
    output_format: str,
) -> Dict[str, str]:
    """
    Persist analysis output to versioned JSON and/or Markdown files.

    Args:
        test_run_id: Test run identifier (determines artifact directory).
        jmx_path: Absolute path to the analysed JMX file.
        detail_level: Detail level used for this export.
        summary: Summary counts dict.
        hierarchy: Nested hierarchy list.
        node_index_output: Flat node index dict (may be None).
        variables: Variables scan dict (may be None).
        outline: Human-readable outline string.
        output_format: "json", "markdown", or "both".

    Returns:
        Dict with keys "json" and/or "markdown" mapping to absolute file paths.
    """
    analysis_dir = get_jmeter_analysis_dir(test_run_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    max_files = _get_max_analysis_files()

    exported: Dict[str, str] = {}

    if output_format in ("json", "both"):
        json_data = build_structure_json(
            jmx_path, detail_level, summary, hierarchy,
            node_index_output, variables, outline,
        )
        json_path = os.path.join(
            analysis_dir, f"{ANALYSIS_FILE_PREFIX}{timestamp}.json"
        )
        save_json_file(json_path, json_data)
        rotate_analysis_files(analysis_dir, ANALYSIS_FILE_PREFIX, max_files)
        exported["json"] = json_path

    if output_format in ("markdown", "both"):
        md_content = build_structure_markdown(
            jmx_path, detail_level, summary, outline, variables,
        )
        md_path = os.path.join(
            analysis_dir, f"{ANALYSIS_FILE_PREFIX}{timestamp}.md"
        )
        save_markdown_file(md_path, md_content)
        rotate_analysis_files(analysis_dir, ANALYSIS_FILE_PREFIX, max_files)
        exported["markdown"] = md_path

    return exported
