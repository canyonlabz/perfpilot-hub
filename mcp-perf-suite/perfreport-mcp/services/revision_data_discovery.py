"""
services/revision_data_discovery.py
Data discovery service for AI-assisted report revision.

This module implements the discover_revision_data function that scans the
artifacts folder structure and returns a comprehensive manifest of all
available data files for generating revised report sections.
"""

from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from utils.config import load_config, load_revisable_sections_config
from utils.revision_utils import (
    get_artifacts_base_path,
    get_reports_folder_path,
    get_revisions_folder_path,
    get_existing_revision_versions,
    get_original_report_path,
    get_metadata_path,
    validate_report_type,
)


# Load configuration
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))


# -----------------------------------------------
# Main Discovery Function
# -----------------------------------------------

async def discover_revision_data(
    run_id: str,
    report_type: str = "single_run",
    additional_context: Optional[str] = None
) -> Dict:
    """
    Discover all available data files for AI-assisted report revision.
    
    Scans the artifacts folder structure to identify all output files from
    BlazeMeter MCP, Datadog MCP, PerfAnalysis MCP, and PerfReport MCP that
    can be used as context for generating revised report sections.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" (default) or "comparison".
        additional_context: Optional user-provided context to incorporate into revisions
                           (e.g., project name, purpose, feature/PBI details from ADO/JIRA).
    
    Returns:
        dict containing:
            - run_id: The test run or comparison ID
            - report_type: Type of report ("single_run" or "comparison")
            - artifacts_base_path: Base path to artifacts
            - data_sources: Dict organized by MCP source with file paths
            - revisable_sections: List of enabled sections from config with details
            - revision_output_path: Path where AI revision files should be saved
            - additional_context: The user-provided context (passed through for AI use)
            - existing_revisions: Dict of existing revision files per section with version numbers
            - revision_guidelines: Instructions for AI on expected output format
            - status: "success" or "error"
            - error: Error message if status is "error"
    """
    try:
        # Validate report_type
        is_valid, error = validate_report_type(report_type)
        if not is_valid:
            return {
                "run_id": run_id,
                "report_type": report_type,
                "status": "error",
                "error": error
            }
        
        # Get base paths
        artifacts_base = get_artifacts_base_path(run_id, report_type)
        
        # Check if artifacts folder exists
        if not artifacts_base.exists():
            return {
                "run_id": run_id,
                "report_type": report_type,
                "status": "error",
                "error": f"Artifacts folder not found: {artifacts_base}"
            }
        
        # Discover data sources
        data_sources = _discover_data_sources(run_id, report_type, artifacts_base)
        
        # Get revisable sections configuration
        sections_config = load_revisable_sections_config(report_type)
        enabled_sections = load_revisable_sections_config(report_type, enabled_only=True)
        
        # Format sections with full details
        revisable_sections = _format_sections_for_output(sections_config, enabled_sections)
        
        # Get existing revisions
        existing_revisions = _get_existing_revisions(run_id, report_type, sections_config)
        
        # Get revision output path
        revisions_path = get_revisions_folder_path(run_id, report_type)
        
        # Build revision guidelines for AI
        revision_guidelines = _build_revision_guidelines(report_type, enabled_sections)
        
        return {
            "run_id": run_id,
            "report_type": report_type,
            "artifacts_base_path": str(artifacts_base),
            "data_sources": data_sources,
            "revisable_sections": revisable_sections,
            "enabled_section_count": len(enabled_sections),
            "revision_output_path": str(revisions_path),
            "additional_context": additional_context,
            "existing_revisions": existing_revisions,
            "revision_guidelines": revision_guidelines,
            "discovered_at": datetime.now().isoformat(),
            "status": "success"
        }
        
    except Exception as e:
        return {
            "run_id": run_id,
            "report_type": report_type,
            "status": "error",
            "error": f"Discovery failed: {str(e)}"
        }


# -----------------------------------------------
# Data Source Discovery Functions
# -----------------------------------------------

def _discover_data_sources(run_id: str, report_type: str, artifacts_base: Path) -> Dict:
    """
    Discover all data source files organized by MCP origin.
    
    Dynamically scans folders for JSON, CSV, and MD files.
    """
    data_sources = {}
    
    if report_type == "single_run":
        # Single-run artifacts structure
        data_sources["blazemeter"] = _scan_folder(artifacts_base / "blazemeter")
        data_sources["datadog"] = _scan_folder(artifacts_base / "datadog")
        data_sources["analysis"] = _scan_folder(artifacts_base / "analysis")
        data_sources["reports"] = _scan_reports_folder(run_id, report_type)
        data_sources["charts"] = _scan_charts_folder(artifacts_base / "charts")
    else:
        # Comparison artifacts structure
        # For comparison, we need to discover data from each compared run
        data_sources["comparison_root"] = _scan_folder(artifacts_base)
        data_sources["charts"] = _scan_charts_folder(artifacts_base / "charts")
        
        # Try to find the run IDs from comparison metadata
        comparison_runs = _discover_comparison_runs(artifacts_base)
        if comparison_runs:
            data_sources["compared_runs"] = comparison_runs
    
    return data_sources


def _scan_folder(folder_path: Path) -> Dict:
    """
    Scan a folder for JSON, CSV, and MD files.
    
    Returns dict with file lists organized by type.
    """
    result = {
        "folder_path": str(folder_path),
        "exists": folder_path.exists(),
        "json_files": [],
        "csv_files": [],
        "md_files": [],
        "other_files": []
    }
    
    if not folder_path.exists():
        return result
    
    for file_path in folder_path.iterdir():
        if file_path.is_file():
            file_info = {
                "filename": file_path.name,
                "path": str(file_path),
                "size_bytes": file_path.stat().st_size
            }
            
            suffix = file_path.suffix.lower()
            if suffix == ".json":
                result["json_files"].append(file_info)
            elif suffix == ".csv":
                result["csv_files"].append(file_info)
            elif suffix == ".md":
                result["md_files"].append(file_info)
            else:
                result["other_files"].append(file_info)
    
    # Sort files by name for consistent ordering
    for key in ["json_files", "csv_files", "md_files", "other_files"]:
        result[key] = sorted(result[key], key=lambda x: x["filename"])
    
    result["total_files"] = (
        len(result["json_files"]) + 
        len(result["csv_files"]) + 
        len(result["md_files"]) +
        len(result["other_files"])
    )
    
    return result


def _scan_reports_folder(run_id: str, report_type: str) -> Dict:
    """
    Scan the reports folder with special handling for report and metadata files.
    """
    reports_path = get_reports_folder_path(run_id, report_type)
    
    result = {
        "folder_path": str(reports_path),
        "exists": reports_path.exists(),
        "report_file": None,
        "metadata_file": None,
        "other_files": []
    }
    
    if not reports_path.exists():
        return result
    
    # Get known report and metadata paths
    original_report = get_original_report_path(run_id, report_type)
    metadata_file = get_metadata_path(run_id, report_type)
    
    if original_report.exists():
        result["report_file"] = {
            "filename": original_report.name,
            "path": str(original_report),
            "size_bytes": original_report.stat().st_size
        }
    
    if metadata_file.exists():
        result["metadata_file"] = {
            "filename": metadata_file.name,
            "path": str(metadata_file),
            "size_bytes": metadata_file.stat().st_size
        }
    
    # Scan for other files (excluding revisions subfolder)
    for file_path in reports_path.iterdir():
        if file_path.is_file():
            # Skip if it's the main report or metadata
            if file_path == original_report or file_path == metadata_file:
                continue
            
            result["other_files"].append({
                "filename": file_path.name,
                "path": str(file_path),
                "size_bytes": file_path.stat().st_size
            })
    
    return result


def _scan_charts_folder(charts_path: Path) -> Dict:
    """
    Scan the charts folder for PNG images.
    """
    result = {
        "folder_path": str(charts_path),
        "exists": charts_path.exists(),
        "chart_files": []
    }
    
    if not charts_path.exists():
        return result
    
    for file_path in charts_path.glob("*.png"):
        result["chart_files"].append({
            "filename": file_path.name,
            "path": str(file_path),
            "size_bytes": file_path.stat().st_size
        })
    
    result["chart_files"] = sorted(result["chart_files"], key=lambda x: x["filename"])
    result["total_charts"] = len(result["chart_files"])
    
    return result


def _discover_comparison_runs(artifacts_base: Path) -> List[Dict]:
    """
    Try to discover which runs are being compared from metadata files.
    """
    comparison_runs = []
    
    # Look for comparison metadata file
    metadata_files = list(artifacts_base.glob("comparison_metadata_*.json"))
    
    if metadata_files:
        import json
        try:
            with open(metadata_files[0], 'r') as f:
                metadata = json.load(f)
            
            run_ids = metadata.get("run_id_list", [])
            for run_id in run_ids:
                run_path = ARTIFACTS_PATH / run_id
                comparison_runs.append({
                    "run_id": run_id,
                    "path": str(run_path),
                    "exists": run_path.exists()
                })
        except Exception:
            pass
    
    return comparison_runs


# -----------------------------------------------
# Section Formatting Functions
# -----------------------------------------------

def _format_sections_for_output(all_sections: Dict, enabled_sections: Dict) -> List[Dict]:
    """
    Format sections configuration for output with enabled status.
    """
    formatted = []
    
    for section_id, config in all_sections.items():
        is_enabled = section_id in enabled_sections
        
        formatted.append({
            "section_id": section_id,
            "enabled": is_enabled,
            "placeholder": config.get("placeholder", ""),
            "ai_placeholder": config.get("ai_placeholder", ""),
            "output_file": config.get("output_file", ""),
            "description": config.get("description", "")
        })
    
    return formatted


def _get_existing_revisions(run_id: str, report_type: str, sections_config: Dict) -> Dict:
    """
    Get information about existing revision files for each section.
    """
    existing = {}
    
    for section_id in sections_config.keys():
        versions = get_existing_revision_versions(run_id, section_id, report_type)
        
        if versions:
            existing[section_id] = {
                "versions": versions,
                "latest_version": max(versions),
                "version_count": len(versions)
            }
        else:
            existing[section_id] = {
                "versions": [],
                "latest_version": None,
                "version_count": 0
            }
    
    return existing


# -----------------------------------------------
# Revision Guidelines Builder
# -----------------------------------------------

def _build_revision_guidelines(report_type: str, enabled_sections: Dict) -> Dict:
    """
    Build guidelines for AI on how to generate revisions.
    """
    if not enabled_sections:
        return {
            "message": "No sections are enabled for revision. Enable sections in report_config.yaml first.",
            "sections_to_revise": []
        }
    
    sections_to_revise = []
    for section_id, config in enabled_sections.items():
        sections_to_revise.append({
            "section_id": section_id,
            "description": config.get("description", ""),
            "output_format": "markdown",
            "guidelines": _get_section_guidelines(section_id)
        })
    
    return {
        "message": f"Generate revised content for {len(enabled_sections)} enabled section(s).",
        "general_guidelines": [
            "Review all available data files (CSV for detailed data, MD for summaries, JSON for metadata)",
            "Incorporate the additional_context if provided (project name, purpose, etc.)",
            "Write in a professional tone suitable for leadership/stakeholders",
            "Include specific metrics and data points from the source files",
            "Keep the content concise but informative",
            "Use markdown formatting (headers, bullet points, tables as appropriate)",
            "Call prepare_revision_context() for each section after generating content"
        ],
        "sections_to_revise": sections_to_revise,
        "workflow": [
            "1. Read the relevant data files listed in data_sources",
            "2. For each enabled section, analyze the data and generate improved content",
            "3. If additional_context is provided, incorporate that information",
            "4. Call prepare_revision_context(run_id, section_id, content, report_type) for each",
            "5. After all sections are revised, call revise_performance_test_report(run_id, report_type)"
        ]
    }


def _get_section_guidelines(section_id: str) -> List[str]:
    """
    Get specific guidelines for each section type.
    """
    guidelines = {
        "executive_summary": [
            "Provide a high-level overview of test results",
            "Include key metrics: success rate, avg response time, throughput",
            "Highlight any critical issues or SLA violations",
            "Keep to 3-5 sentences or bullet points",
            "Mention the test environment and date if relevant"
        ],
        "key_observations": [
            "List 3-7 key observations from the test",
            "Use bullet points for clarity",
            "Include both positive findings and concerns",
            "Reference specific APIs or services when relevant",
            "Prioritize observations by impact"
        ],
        "issues_table": [
            "Create a markdown table of issues observed",
            "Include columns: Issue Type, Severity, Count, Description",
            "Sort by severity (Critical > High > Medium > Low)",
            "Include error rates and specific error messages if available",
            "Reference the affected APIs or endpoints"
        ],
        "bottleneck_analysis": [
            "Summarize the bottleneck analysis findings from bottleneck_analysis.json/md",
            "Include the degradation threshold concurrency and optimal concurrency",
            "Highlight critical and high severity bottlenecks with their types",
            "Reference specific endpoints or services that are bottlenecks",
            "Include baseline metrics for comparison context",
            "Mention infrastructure correlations if available (CPU/memory saturation)",
            "Provide the headline finding and key metric deltas"
        ],
        "jmeter_log_analysis": [
            "Summarize the JMeter/BlazeMeter log analysis from blazemeter_log_analysis.json/md",
            "Include total unique issues and total error occurrences",
            "Break down errors by severity (Critical, High, Medium) and category",
            "List the top affected APIs/endpoints with their error counts",
            "Include JTL correlation summary if available",
            "Reference specific error categories (HTTP 5xx, connection errors, timeouts, etc.)",
            "Note the error timeline (first and last error timestamps)"
        ],
        "recommendations": [
            "Provide actionable recommendations based on test results",
            "Reference specific bottlenecks, errors, and SLA violations",
            "Prioritize recommendations by impact and urgency",
            "Include both short-term fixes and long-term improvements",
            "Reference relevant data from bottleneck analysis and log analysis"
        ],
        "key_findings": [
            "Summarize key findings from comparing multiple test runs",
            "Highlight trends (improving, degrading, stable)",
            "Compare key metrics across runs",
            "Use bullet points for clarity"
        ],
        "issues_summary": [
            "Summarize issues across all compared runs",
            "Identify recurring issues vs one-time issues",
            "Note any issues that were resolved between runs",
            "Highlight the most critical issues requiring attention"
        ],
        "overall_trend_summary": [
            "Provide a narrative overview of performance trends across compared test runs",
            "Highlight whether key metrics (response time, throughput, error rate) are improving, degrading, or stable",
            "Reference specific percentage changes between runs",
            "Note any inflection points or significant shifts in performance",
            "Keep the narrative concise (3-5 sentences) and suitable for leadership audiences"
        ],
        "correlation_insights_section": [
            "Analyze performance-infrastructure correlations across compared test runs",
            "Identify patterns between resource utilization changes and response time changes",
            "Highlight runs where infrastructure metrics correlated with performance degradation",
            "Note any resource contention or saturation that appeared across multiple runs",
            "Reference specific services/hosts and their resource utilization trends"
        ],
        "correlation_key_observations": [
            "List 3-5 key observations from the correlation analysis across runs",
            "Use bullet points for clarity",
            "Reference specific CPU/memory metrics and their impact on response times",
            "Highlight any services with consistently high resource utilization across runs",
            "Note whether scaling or optimization between runs improved correlations"
        ],
        "kpi_analysis": [
            "Summarize application KPI metrics from kpi_analysis.json and kpi_summary.md",
            "Group metrics by category (GC/Memory, Latency, CPU, Memory, Throughput, Disk, Network)",
            "Highlight metrics with concerning trends (increasing heap, rising latency, high CPU)",
            "Include specific values (avg, p90, p95) with their units for key metrics",
            "Reference the scope (host-based or k8s) and affected services/hosts",
            "Note any GC pressure, memory leak indicators, or latency degradation patterns",
            "Provide actionable insights connecting KPI metric behavior to observed performance"
        ],
        "kpi_correlation": [
            "Analyze KPI-to-performance correlations from the kpi_correlations section in correlation_analysis.json",
            "Highlight strong and moderate correlations between KPI metrics and response times",
            "Explain the practical significance of key correlation pairs (e.g., GC heap vs latency)",
            "Note any unexpected or counter-intuitive correlations",
            "Reference specific correlation coefficients and sample sizes for credibility",
            "Connect correlation findings to bottleneck analysis when relevant",
            "Suggest which KPI metrics are most predictive of performance degradation"
        ]
    }
    
    return guidelines.get(section_id, [
        "Generate appropriate content based on the section description",
        "Use markdown formatting",
        "Be concise but informative"
    ])
