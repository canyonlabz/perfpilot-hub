"""
utils/data_loader_utils.py
Data loading utilities for performance report generation.

This module consolidates data loading logic used by both report_generator.py
and report_revision_generator.py to ensure consistency and reduce code duplication.
"""
import csv
from pathlib import Path
from typing import Dict, List, Optional
from collections import Counter

from utils.config import load_config
from utils.file_utils import _load_json_safe, _load_text_safe

# -----------------------------------------------
# Global Configuration
# -----------------------------------------------
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))


# -----------------------------------------------
# Data Loading Functions
# -----------------------------------------------
async def load_report_data(run_id: str) -> Dict:
    """
    Load all data required for generating or revising performance test reports.
    
    This function consolidates data loading for both generate_performance_test_report()
    and revise_performance_test_report() to ensure consistency and reduce duplication.
    
    Args:
        run_id: Test run identifier
    
    Returns:
        Dict containing all loaded data and metadata:
            - status: "success" or "error"
            - error: Error message if status is "error"
            - run_path: Path to run folder
            - analysis_path: Path to analysis folder
            - apm_path: Path to APM data folder (currently Datadog)
            - load_test_path: Path to load test data folder (currently BlazeMeter)
            - environment_type: "host" or "kubernetes"
            - perf_data: Performance analysis JSON (or None)
            - infra_data: Infrastructure analysis JSON (or None)
            - corr_data: Correlation analysis JSON (or None)
            - perf_summary_md: Performance summary markdown (or None)
            - infra_summary_md: Infrastructure summary markdown (or None)
            - corr_summary_md: Correlation summary markdown (or None)
            - log_data: Log analysis JSON (or None)
            - bottleneck_data: Bottleneck analysis JSON from PerfAnalysis identify_bottlenecks (or None)
            - jmeter_log_analysis_data: BlazeMeter/JMeter log analysis JSON from JMeter MCP analyze_jmeter_log (or None)
            - kpi_data: KPI analysis JSON from PerfAnalysis analyze_apm_metrics (or None)
            - kpi_summary_md: KPI summary markdown (or None)
            - kpi_correlations: KPI correlations dict extracted from correlation_analysis.json (or None)
            - apm_trace_summary: APM trace summary dict (or None)
            - load_test_config: Load test configuration (or None)
            - load_test_public_report: Load test public report URL (or None)
            - source_files: Dict mapping source names to file paths
            - warnings: List of non-fatal warnings
            - missing_sections: List of missing data sections
    
    Note:
        TODO: Schema-Driven Architecture Enhancement
        Currently, this function uses tool-specific folder names:
        - "datadog" folder for APM data
        - "blazemeter" folder for load test data
        
        In a future enhancement, these should be abstracted to support multiple
        APM tools (Datadog, Dynatrace, AppDynamics, etc.) and load testing tools
        (BlazeMeter, k6, Gatling, Locust, etc.) via a schema-driven configuration.
        The folder structure could become:
        - "apm/" with tool-specific subfolders or a unified schema
        - "load_test/" with tool-specific subfolders or a unified schema
        
        See README.md for the project roadmap on schema-driven architecture.
    """
    warnings = []
    missing_sections = []
    source_files = {}
    
    # Build paths
    run_path = ARTIFACTS_PATH / run_id
    analysis_path = run_path / "analysis"
    
    # TODO: These paths are currently tool-specific (Datadog, BlazeMeter).
    # Future schema-driven architecture will abstract these to be tool-agnostic.
    apm_path = run_path / "datadog"
    load_test_path = run_path / "blazemeter"
    
    # Validate run_id path exists
    if not run_path.exists():
        return {
            "status": "error",
            "error": f"Run path not found: {run_path}",
            "run_id": run_id
        }
    
    # Validate analysis path exists
    if not analysis_path.exists():
        return {
            "status": "error",
            "error": f"Analysis path not found: {analysis_path}",
            "run_id": run_id
        }
    
    # Load analysis JSON files
    perf_data = await _load_json_safe(
        analysis_path / "performance_analysis.json",
        "performance_analysis",
        source_files,
        warnings,
        missing_sections
    )
    
    infra_data = await _load_json_safe(
        analysis_path / "infrastructure_analysis.json",
        "infrastructure_analysis",
        source_files,
        warnings,
        missing_sections
    )
    
    corr_data = await _load_json_safe(
        analysis_path / "correlation_analysis.json",
        "correlation_analysis",
        source_files,
        warnings,
        missing_sections
    )
    
    # Determine environment type ('host' or 'kubernetes') from correlation_analysis.json
    environment_type = None
    if corr_data:
        env_raw = corr_data.get("environment_type")
        if isinstance(env_raw, str):
            env_norm = env_raw.strip().lower()
            if env_norm in {"host", "kubernetes"}:
                environment_type = env_norm
    
    # Return error if environment type cannot be determined
    if environment_type not in {"host", "kubernetes"}:
        return {
            "status": "error",
            "error": (
                "Environment type not detected in correlation_analysis.json. "
                "Expected 'host' or 'kubernetes' in field 'environment_type'."
            ),
            "run_id": run_id
        }
    
    # Load markdown summaries (optional)
    perf_summary_md = await _load_text_safe(
        analysis_path / "performance_summary.md",
        "performance_summary_md",
        source_files,
        warnings
    )
    
    infra_summary_md = await _load_text_safe(
        analysis_path / "infrastructure_summary.md",
        "infrastructure_summary_md",
        source_files,
        warnings
    )
    
    corr_summary_md = await _load_text_safe(
        analysis_path / "correlation_analysis.md",
        "correlation_analysis_md",
        source_files,
        warnings
    )
    
    kpi_summary_md = await _load_text_safe(
        analysis_path / "kpi_summary.md",
        "kpi_summary_md",
        source_files,
        warnings
    )
    
    # Load log analysis data (optional)
    log_data = await _load_json_safe(
        analysis_path / "log_analysis.json",
        "log_analysis",
        source_files,
        warnings,
        missing_sections
    )
    
    # Load bottleneck analysis data (optional - from PerfAnalysis identify_bottlenecks)
    bottleneck_data = await _load_json_safe(
        analysis_path / "bottleneck_analysis.json",
        "bottleneck_analysis",
        source_files,
        warnings,
        []  # Don't add to missing_sections - this is optional
    )
    
    # Load BlazeMeter/JMeter log analysis data (optional - from JMeter MCP analyze_jmeter_log)
    # This is separate from PerfAnalysis log_analysis.json; it provides deeper JMeter-specific
    # error analysis with request/response details, JTL correlation, and error categorization.
    jmeter_log_analysis_data = await _load_json_safe(
        analysis_path / "blazemeter_log_analysis.json",
        "blazemeter_log_analysis",
        source_files,
        warnings,
        []  # Don't add to missing_sections - this is optional
    )
    
    # Load KPI analysis data (optional - from PerfAnalysis analyze_apm_metrics when KPI files exist)
    # Contains per-service/host metric summaries, categories, insights, and scope information.
    kpi_data = await _load_json_safe(
        analysis_path / "kpi_analysis.json",
        "kpi_analysis",
        source_files,
        warnings,
        []  # Don't add to missing_sections - this is optional
    )
    
    # Extract KPI correlations from correlation_analysis.json (if present)
    # The kpi_correlations section is embedded in correlation_analysis.json by PerfAnalysis
    # when KPI timeseries files exist. Contains correlation pairs, available metrics, and insights.
    kpi_correlations = None
    if corr_data:
        kpi_correlations = corr_data.get("kpi_correlations")
    
    # Load APM trace data (optional)
    # TODO: Currently loads from Datadog-specific folder. Future schema-driven
    # architecture will support multiple APM tools.
    apm_trace_summary = await _load_apm_trace_summary(apm_path, source_files, warnings)
    
    # Load test configuration (optional - contains max_virtual_users, actual test dates)
    # TODO: Currently loads from BlazeMeter-specific folder. Future schema-driven
    # architecture will support multiple load testing tools.
    load_test_config = await _load_json_safe(
        load_test_path / "test_config.json",
        "load_test_config",
        source_files,
        warnings,
        []  # Don't add to missing_sections - this is optional
    )
    
    # Load test public report URL (optional)
    load_test_public_report = await _load_json_safe(
        load_test_path / "public_report.json",
        "load_test_public_report",
        source_files,
        warnings,
        []  # Don't add to missing_sections - this is optional
    )
    
    # Return all loaded data
    return {
        "status": "success",
        "run_id": run_id,
        
        # Paths
        "run_path": run_path,
        "analysis_path": analysis_path,
        "apm_path": apm_path,
        "load_test_path": load_test_path,
        
        # Environment
        "environment_type": environment_type,
        
        # Analysis JSON data
        "perf_data": perf_data,
        "infra_data": infra_data,
        "corr_data": corr_data,
        "log_data": log_data,
        "bottleneck_data": bottleneck_data,
        "jmeter_log_analysis_data": jmeter_log_analysis_data,
        "kpi_data": kpi_data,
        "kpi_correlations": kpi_correlations,
        
        # Markdown summaries
        "perf_summary_md": perf_summary_md,
        "infra_summary_md": infra_summary_md,
        "corr_summary_md": corr_summary_md,
        "kpi_summary_md": kpi_summary_md,
        
        # APM and Load Test data
        # TODO: Variable names are currently generic but load from tool-specific folders.
        # Future schema-driven architecture will unify this.
        "apm_trace_summary": apm_trace_summary,
        "load_test_config": load_test_config,
        "load_test_public_report": load_test_public_report,
        
        # Metadata
        "source_files": source_files,
        "warnings": warnings,
        "missing_sections": missing_sections
    }


async def _load_apm_trace_summary(apm_path: Path, source_files: Dict, warnings: List) -> Dict:
    """
    Load and summarize APM trace data from the APM data folder.
    
    Args:
        apm_path: Path to APM data folder (e.g., artifacts/{run_id}/datadog)
        source_files: Dict to record loaded file paths
        warnings: List to append non-fatal warnings
    
    Returns:
        Dict with APM trace summary or {"available": False} if no data
    
    Note:
        TODO: Schema-Driven Architecture Enhancement
        Currently assumes Datadog CSV format for APM traces. Future enhancement
        should support multiple APM tools with different trace formats via a
        schema-driven parser configuration.
    """
    result = {"available": False}
    
    if not apm_path.exists():
        return result
    
    # Find APM trace files (currently Datadog format)
    apm_files = list(apm_path.glob("apm_traces_*.csv"))
    if not apm_files:
        return result
    
    result["available"] = True
    result["file_count"] = len(apm_files)
    
    total_spans = 0
    http_status_counts = Counter()
    service_error_counts = Counter()
    error_type_counts = Counter()
    
    for apm_file in apm_files:
        try:
            source_files[f"apm_trace_{apm_file.name}"] = str(apm_file)
            with open(apm_file, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_spans += 1
                    
                    # Count HTTP status codes
                    http_status = row.get("http_status_code", "")
                    if http_status:
                        http_status_counts[http_status] += 1
                    
                    # Count services with errors
                    service = row.get("service", "unknown")
                    if row.get("error") == "1" or row.get("status") == "error":
                        service_error_counts[service] += 1
                    
                    # Count error types
                    error_type = row.get("error_type", "")
                    if error_type:
                        error_type_counts[error_type] += 1
                        
        except Exception as e:
            warnings.append(f"Failed to parse APM file {apm_file.name}: {str(e)}")
    
    result["total_error_spans"] = total_spans
    result["http_status_counts"] = dict(http_status_counts)
    result["top_services"] = [
        {"service": svc, "count": cnt} 
        for svc, cnt in service_error_counts.most_common(10)
    ]
    result["top_error_types"] = [
        {"error_type": err, "count": cnt}
        for err, cnt in error_type_counts.most_common(10)
    ]
    
    return result
