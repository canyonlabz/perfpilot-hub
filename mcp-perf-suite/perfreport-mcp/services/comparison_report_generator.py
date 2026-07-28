"""
services/comparison_report_generator.py
Multi-run performance test comparison report generation
"""

import json
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
from fastmcp import Context

# Import config and utilities
from utils.config import load_config, load_report_config
from utils.report_utils import format_duration, strip_service_name_decorations
from utils.file_utils import (
    _load_json_safe,
    _load_text_file,
    _save_text_file,
    _save_json_file,
    _convert_to_pdf,
    _convert_to_docx
)

# Load configuration globally
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
REPORT_CONFIG = CONFIG.get('perf_report', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))
TEMPLATES_PATH = Path(REPORT_CONFIG.get('templates_path', './templates'))

# Server version info (standardized across all MCPs)
SERVER_CONFIG = CONFIG.get('server', {})
MCP_VERSION = SERVER_CONFIG.get('version', 'unknown')
MCP_BUILD_DATE = SERVER_CONFIG.get('build', {}).get('date', 'unknown')

# Maximum recommended runs for comparison
MAX_RECOMMENDED_RUNS = 5

# -----------------------------------------------
# Main Comparison Report functions
# ----------------------------------------------- 
async def generate_comparison_report(run_id_list: list, ctx: Context, format: str = "md", template: Optional[str] = None) -> Dict:
    """
    Generate multi-run comparison report from metadata JSONs.
    
    Args:
        run_id_list: List of 2-5 test run IDs to compare
        ctx: Workflow context for chaining
        format: Output format ('md', 'pdf', 'docx')
        template: Optional comparison template name
    
    Returns:
        Dict with comparison report metadata and paths
    """
    try:
        generated_timestamp = datetime.now().isoformat()
        warnings = []
        
        # Validate run count
        run_count = len(run_id_list)
        if run_count < 2:
            return {
                "error": "Comparison requires at least 2 test runs",
                "run_id_list": run_id_list,
                "generated_timestamp": generated_timestamp
            }
        
        if run_count > MAX_RECOMMENDED_RUNS:
            await ctx.warning("Comparing more than 5 test runs may lead to unreadable reports.")
            warning_msg = (
                f"WARNING: Comparing {run_count} test runs exceeds the recommended "
                f"maximum of {MAX_RECOMMENDED_RUNS}. The report may be difficult to read. "
                "Consider using a trend analysis tool for large-scale comparisons."
            )
            warnings.append(warning_msg)
        
        # Load metadata for each run
        run_metadata_list = []
        for run_id in run_id_list:
            metadata_path = ARTIFACTS_PATH / run_id / "reports" / f"report_metadata_{run_id}.json"
            
            if not metadata_path.exists():
                error_msg = f"Metadata not found for run {run_id}: {metadata_path}"
                return {
                    "error": error_msg,
                    "run_id_list": run_id_list,
                    "generated_timestamp": generated_timestamp
                }
            
            with open(metadata_path, 'r') as f:
                run_metadata = json.load(f)
                run_metadata_list.append(run_metadata)
        
        # Select template
        template_name = template or "default_comparison_report_template.md"
        template_path = TEMPLATES_PATH / template_name
        
        if not template_path.exists():
            return {
                "error": f"Comparison template not found: {template_name}",
                "run_id_list": run_id_list,
                "generated_timestamp": generated_timestamp
            }
        
        template_content = await _load_text_file(template_path)
        
        # Build comparison context
        context = _build_comparison_context(
            run_id_list,
            run_metadata_list,
            generated_timestamp
        )
        
        # Render template
        comparison_markdown = _render_comparison_template(template_content, context)
        
        # Generate timestamp-based comparison_id (format: YYYY-MM-DD-HH-MM-SS)
        comparison_id = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        joined_run_ids = "_".join(run_id_list)
        
        # Create comparison subfolder: artifacts/comparisons/{comparison_id}/
        reports_dir = ARTIFACTS_PATH / "comparisons" / comparison_id
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Report filename includes run_ids for traceability
        report_name = f"comparison_report_{joined_run_ids}.md"
        md_path = reports_dir / report_name
        await _save_text_file(md_path, comparison_markdown)
        
        # Convert to requested format
        final_path = md_path
        if format == "pdf":
            final_path = await _convert_to_pdf(md_path, reports_dir, joined_run_ids)
        elif format == "docx":
            final_path = await _convert_to_docx(md_path, reports_dir, joined_run_ids)
        
        # Define metadata path (in same subfolder)
        metadata_path = reports_dir / f"comparison_metadata_{joined_run_ids}.json"
        
        # Build response
        response = {
            "comparison_id": comparison_id,  # NEW: timestamp-based ID
            "run_id_list": run_id_list,
            "run_count": run_count,
            "report_name": report_name if format == "md" else final_path.name,
            "report_path": str(final_path),
            "metadata_path": str(metadata_path),
            "generated_timestamp": generated_timestamp,
            "mcp_version": MCP_VERSION,
            "format": format,
            "template_used": template_name,
            "warnings": warnings
        }
        
        # Build comprehensive comparison metadata
        comparison_metadata = {
            **response,
            "runs_analyzed": [
                {
                    "run_id": meta["run_id"],
                    "test_date": meta["test_config"]["test_date"],
                    "environment": meta["test_config"]["environment"]
                }
                for meta in run_metadata_list
            ]
        }
        
        # Save comparison metadata
        await _save_json_file(metadata_path, comparison_metadata)
        
        return response
        
    except Exception as e:
        return {
            "error": f"Comparison report generation failed: {str(e)}",
            "run_id_list": run_id_list,
            "generated_timestamp": datetime.now().isoformat()
        }


# ===== COMPARISON CONTEXT BUILDER =====
def _build_comparison_context(run_id_list: List[str], run_metadata_list: List[Dict], timestamp: str) -> Dict:
    """Build context dictionary for comparison template rendering."""
    run_count = len(run_id_list)
    
    # Base context
    context = {
        "GENERATED_TIMESTAMP": timestamp,
        "RUN_COUNT": str(run_count),
        "ENVIRONMENT": _determine_common_environment(run_metadata_list),
        "MCP_VERSION": MCP_VERSION,
        "MCP_BUILD_DATE": MCP_BUILD_DATE,
        "RUN_IDS_LIST": ", ".join(run_id_list)
    }
    
    # Populate run-specific columns (1-5)
    for i in range(5):
        if i < run_count:
            _populate_run_column(context, i + 1, run_metadata_list[i], i)
        else:
            _populate_empty_run_column(context, i + 1)
    
    # Determine infrastructure entity type (Host vs Service)
    infra_entity_type = _determine_environment_type(run_metadata_list)
    
    # Build comparison summaries and tables
    context.update({
        "EXECUTIVE_SUMMARY": _build_executive_summary(run_metadata_list),
        "KEY_FINDINGS_BULLETS": _build_key_findings(run_metadata_list),
        "OVERALL_TREND_SUMMARY": _build_overall_trend(run_metadata_list),
        
        # Issues & Errors
        "ISSUES_SUMMARY": _build_issues_summary(run_metadata_list),
        "CRITICAL_ISSUES_TABLE": _build_critical_issues_table(run_metadata_list),
        "PERFORMANCE_DEGRADATIONS_ROWS": _build_performance_degradations(run_metadata_list),
        "INFRASTRUCTURE_CONCERNS_ROWS": _build_infrastructure_concerns(run_metadata_list),
        "ERROR_RATE_SUMMARY": _build_error_rate_summary(run_metadata_list),
        
        # Bugs section
        "TOTAL_BUG_COUNT": "0",
        "OPEN_BUG_COUNT": "0",
        "RESOLVED_BUG_COUNT": "0",
        
        # API Performance
        "API_COMPARISON_ROWS": _build_api_comparison_table(run_metadata_list),
        "TOP_OFFENDERS_ROWS": _build_top_offenders_table(run_metadata_list),
        "P90_COMPARISON_ROWS": _build_p90_comparison_table(run_id_list, run_metadata_list),
        
        # Throughput
        "THROUGHPUT_TREND": _format_trend_symbol(_calculate_metric_trend(run_metadata_list, ["performance_metrics", "avg_throughput"], lower_is_better=False)),
        "PEAK_THROUGHPUT_TREND": _format_trend_symbol(_calculate_metric_trend(run_metadata_list, ["performance_metrics", "peak_throughput"], lower_is_better=False)),
        "THROUGHPUT_SUMMARY": _build_throughput_summary(run_metadata_list),
        
        # Infrastructure - Entity type for dynamic labels
        "INFRA_ENTITY_TYPE": infra_entity_type,
        "INFRA_ENTITY_TYPE_LOWER": infra_entity_type.lower(),
        
        # Infrastructure - Utilization (%) tables
        "CPU_COMPARISON_ROWS": _build_cpu_comparison_table(run_metadata_list),
        "MEMORY_COMPARISON_ROWS": _build_memory_comparison_table(run_metadata_list),
        "CPU_IMPROVED_COUNT": "0",
        "CPU_DEGRADED_COUNT": str(_count_degraded_services(run_metadata_list, "cpu")),
        "CPU_STABLE_COUNT": "0",
        "MEMORY_IMPROVED_COUNT": "0",
        "MEMORY_DEGRADED_COUNT": str(_count_degraded_services(run_metadata_list, "memory")),
        "MEMORY_STABLE_COUNT": "0",
        
        # Infrastructure - Raw usage tables (Cores/GB)
        "CPU_CORE_COMPARISON_ROWS": _build_cpu_core_comparison_table(run_metadata_list),
        "MEMORY_USAGE_COMPARISON_ROWS": _build_memory_usage_comparison_table(run_metadata_list),
        
        "RESOURCE_EFFICIENCY_SUMMARY": _build_resource_efficiency(run_metadata_list),
        
        # Correlation (optional)
        "CORRELATION_INSIGHTS_SECTION": _build_correlation_section(run_metadata_list),
        "CORRELATION_KEY_OBSERVATIONS": _build_correlation_observations(run_metadata_list),
        
        # Conclusion
        "CONCLUSION_SYNOPSIS": _build_conclusion_synopsis(run_metadata_list),
        "RECOMMENDATIONS_LIST": _build_recommendations(run_metadata_list),
        "NEXT_STEPS_LIST": _build_next_steps(run_metadata_list)
    })
    
    # Populate source files per run
    for i, metadata in enumerate(run_metadata_list):
        context[f"RUN_{i+1}_SOURCE_FILES"] = _format_source_files(metadata.get("source_files", {}))
    
    # Fill remaining empty runs with N/A for source files
    for i in range(run_count, 5):
        context[f"RUN_{i+1}_SOURCE_FILES"] = "N/A"
    
    return context

# -----------------------------------------------
# Comparison report utility functions
# ----------------------------------------------- 
def _populate_run_column(context: Dict, run_num: int, metadata: Dict, index: int):
    """Populate context with data for a specific run column."""
    test_config = metadata.get("test_config", {})
    perf_metrics = metadata.get("performance_metrics", {})
    infra_summary = metadata.get("infrastructure_metrics", {}).get("summary", {})
    
    prefix = f"RUN_{run_num}_"
    
    # Calculate delta vs previous run
    delta_str = ""
    if index > 0:
        # Will be calculated in table builders
        delta_str = ""
    
    # Extract max_virtual_users (may be int or string)
    max_vu = test_config.get("max_virtual_users", "N/A")
    max_vu_str = str(max_vu) if max_vu != "N/A" else "N/A"
    
    # Extract start/end times
    start_time = test_config.get("start_time", "N/A")
    end_time = test_config.get("end_time", "N/A")
    
    # Format duration as human-readable
    duration_seconds = test_config.get('test_duration', 0)
    duration_formatted = _format_duration(duration_seconds)
    
    # Use 'or 0' pattern to handle both missing keys AND keys with None values
    success_rate = test_config.get('success_rate') or 0
    avg_throughput = perf_metrics.get('avg_throughput') or 0
    peak_throughput = perf_metrics.get('peak_throughput') or 0
    error_rate = perf_metrics.get('error_rate') or 0
    error_count = perf_metrics.get('error_count') or 0
    
    context.update({
        f"{prefix}ID": metadata.get("run_id", "N/A"),
        f"{prefix}LABEL": f"Run {run_num}",
        f"{prefix}DATE": test_config.get("test_date", "N/A")[:10] if test_config.get("test_date") else "N/A",
        f"{prefix}START_TIME": start_time,
        f"{prefix}END_TIME": end_time,
        f"{prefix}MAX_VU": max_vu_str,
        f"{prefix}DURATION": duration_formatted,
        f"{prefix}SAMPLES": str(test_config.get("total_samples") or 0),
        f"{prefix}SUCCESS_RATE": f"{success_rate:.2f}",
        f"{prefix}ENV": test_config.get("environment", "Unknown"),
        f"{prefix}TYPE": test_config.get("test_type", "Load Test"),
        
        # Performance
        f"{prefix}AVG_THROUGHPUT": f"{avg_throughput:.2f}",
        f"{prefix}PEAK_THROUGHPUT": f"{peak_throughput:.2f}",
        f"{prefix}ERROR_COUNT": str(error_count),
        f"{prefix}ERROR_RATE": f"{error_rate:.2f}",
        f"{prefix}ERROR_DELTA": delta_str,
        f"{prefix}TOP_ERROR": perf_metrics.get("top_error_type", "N/A")
    })


def _populate_empty_run_column(context: Dict, run_num: int):
    """Populate context with N/A for missing run columns."""
    prefix = f"RUN_{run_num}_"
    
    for key in ["ID", "LABEL", "DATE", "START_TIME", "END_TIME", "MAX_VU", "DURATION", 
                "SAMPLES", "SUCCESS_RATE", "ENV", "TYPE", "AVG_THROUGHPUT", "PEAK_THROUGHPUT", 
                "ERROR_COUNT", "ERROR_RATE", "ERROR_DELTA", "TOP_ERROR"]:
        context[f"{prefix}{key}"] = "N/A"


def _render_comparison_template(template: str, context: Dict) -> str:
    """Render comparison template with context using {{}} placeholders."""
    rendered = template
    for key, value in context.items():
        placeholder = "{{" + key + "}}"
        rendered = rendered.replace(placeholder, str(value))
    return rendered


# ===== CALCULATION HELPERS =====

def _format_duration(seconds: int) -> str:
    """
    Format duration in seconds to human-readable format.
    
    .. deprecated::
        This function is deprecated. Use `format_duration` from 
        `utils.report_utils` instead. This wrapper is kept for 
        backwards compatibility.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Formatted string like "90m 28s" or "1h 30m 28s"
    """
    # Delegate to shared utility function
    return format_duration(seconds)


def _calculate_delta(current: float, previous: float, lower_is_better: bool = True) -> Tuple[float, str]:
    """
    Calculate percentage delta and trend indicator.
    
    Returns:
        (delta_pct, trend_symbol)
    """
    # Handle None values - treat as 0 for calculation purposes
    current = current if current is not None else 0
    previous = previous if previous is not None else 0
    
    if previous == 0:
        return 0.0, "➡️"
    
    delta_pct = ((current - previous) / previous) * 100
    
    if abs(delta_pct) <= 5:
        return delta_pct, "➡️"  # Stable
    
    # For response time, CPU, memory, errors: lower is better
    # For throughput: higher is better
    if lower_is_better:
        if delta_pct > 0:
            return delta_pct, "⬇️"  # Degraded (increased)
        else:
            return delta_pct, "⬆️"  # Improved (decreased)
    else:
        if delta_pct > 0:
            return delta_pct, "⬆️"  # Improved (increased)
        else:
            return delta_pct, "⬇️"  # Degraded (decreased)


def _get_nested_value(data: Dict, keys: List[str], default=0):
    """Safely get nested dictionary value."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, default)
        else:
            return default
    return data if data is not None else default


def _calculate_metric_trend(run_metadata_list: List[Dict], metric_path: List[str], lower_is_better: bool = True) -> str:
    """Calculate overall trend for a metric across runs."""
    values = [_get_nested_value(meta, metric_path) for meta in run_metadata_list]
    
    if len(values) < 2:
        return "➡️"
    
    first_val = values[0]
    last_val = values[-1]
    
    delta, trend = _calculate_delta(last_val, first_val, lower_is_better)
    return trend


def _format_trend_symbol(trend: str) -> str:
    """Format trend with description."""
    if trend == "⬆️":
        return "⬆️ Improved"
    elif trend == "⬇️":
        return "⬇️ Degraded"
    else:
        return "➡️ Stable"


# ===== SUMMARY BUILDERS =====

def _determine_common_environment(run_metadata_list: List[Dict]) -> str:
    """Determine if all runs share same environment."""
    envs = [meta.get("test_config", {}).get("environment", "Unknown") 
            for meta in run_metadata_list]
    unique_envs = list(set(envs))
    
    if len(unique_envs) == 1:
        return unique_envs[0]
    else:
        return f"Multiple ({', '.join(unique_envs)})"


def _build_executive_summary(run_metadata_list: List[Dict]) -> str:
    """Generate executive summary for comparison."""
    first_run = run_metadata_list[0]["performance_metrics"]
    last_run = run_metadata_list[-1]["performance_metrics"]
    
    # Use 'or 0' to handle None values
    first_rt = first_run.get("avg_response_time") or 0
    last_rt = last_run.get("avg_response_time") or 0
    
    delta, trend = _calculate_delta(last_rt, first_rt, lower_is_better=True)
    
    summary = f"Comparison of **{len(run_metadata_list)} test runs** shows "
    
    if trend == "⬆️":
        summary += f"**improvement** in average response times ({abs(delta):.1f}% faster). "
    elif trend == "⬇️":
        summary += f"**performance degradation** with average response times increasing by {abs(delta):.1f}%. "
    else:
        summary += "**stable** performance with minimal variation across runs. "
    
    # Add error rate summary - use 'or 0' to handle None values
    first_errors = first_run.get("error_rate") or 0
    last_errors = last_run.get("error_rate") or 0
    
    if last_errors > first_errors:
        summary += f"Error rates increased from {first_errors:.2f}% to {last_errors:.2f}%. "
    
    return summary


def _build_key_findings(run_metadata_list: List[Dict]) -> str:
    """Build key findings bullets."""
    findings = []
    
    # Response time trend
    rt_trend = _calculate_metric_trend(run_metadata_list, ["performance_metrics", "avg_response_time"])
    if rt_trend == "⬇️":
        findings.append("- Response times show degradation trend across test runs")
    elif rt_trend == "⬆️":
        findings.append("- Response times improved across test runs")
    
    # Infrastructure observations - use 'or 0' to handle None values
    first_cpu = run_metadata_list[0]["infrastructure_metrics"]["summary"].get("cpu_peak_pct") or 0
    last_cpu = run_metadata_list[-1]["infrastructure_metrics"]["summary"].get("cpu_peak_pct") or 0
    
    if first_cpu > 0 and last_cpu > first_cpu * 1.5:
        findings.append("- CPU utilization increased significantly")
    
    # Resource allocation - use 'or 0' to handle None values
    first_mem = run_metadata_list[0]["infrastructure_metrics"]["summary"].get("memory_allocated_gb") or 0
    last_mem = run_metadata_list[-1]["infrastructure_metrics"]["summary"].get("memory_allocated_gb") or 0
    
    if first_mem > 0 and last_mem < first_mem:
        findings.append("- Resource allocation reduced in later runs")
    
    if not findings:
        findings.append("- Performance metrics remain consistent across runs")
    
    return "\n".join(findings)


def _build_overall_trend(run_metadata_list: List[Dict]) -> str:
    """Build overall trend summary."""
    perf_trend = _calculate_metric_trend(run_metadata_list, ["performance_metrics", "avg_response_time"])
    
    if perf_trend == "⬇️":
        return "Overall performance trend shows **degradation** across test runs, likely due to resource constraints or increased load."
    elif perf_trend == "⬆️":
        return "Overall performance trend shows **improvement** across test runs."
    else:
        return "Overall performance remains **stable** with minimal variation across test runs."


def _build_issues_summary(run_metadata_list: List[Dict]) -> str:
    """Build issues summary."""
    total_sla_violations = sum(
        len(meta.get("sla_analysis", {}).get("violations", []))
        for meta in run_metadata_list
    )
    
    if total_sla_violations > 0:
        return f"A total of **{total_sla_violations} SLA violations** were detected across all test runs."
    else:
        return "No critical SLA violations detected across test runs."


def _build_critical_issues_table(run_metadata_list: List[Dict]) -> str:
    """Build critical issues table."""
    # For now, return placeholder
    return "No critical issues detected requiring immediate attention."


def _build_performance_degradations(run_metadata_list: List[Dict]) -> str:
    """Build performance degradation rows."""
    rows = []
    
    # Check response time degradation
    first_rt = run_metadata_list[0]["performance_metrics"].get("avg_response_time", 0)
    last_rt = run_metadata_list[-1]["performance_metrics"].get("avg_response_time", 0)
    delta, trend = _calculate_delta(last_rt, first_rt)
    
    if trend == "⬇️":
        affected_runs = ", ".join([f"Run {i+1}" for i in range(1, len(run_metadata_list))])
        rows.append(f"| Response Time Increase | High | {affected_runs} | Avg response time increased by {abs(delta):.1f}% |")
    
    if not rows:
        rows.append("| No significant degradations | - | - | All metrics within acceptable ranges |")
    
    return "\n".join(rows)


def _build_infrastructure_concerns(run_metadata_list: List[Dict]) -> str:
    """Build infrastructure concerns rows."""
    rows = []
    
    # Check CPU concerns - use 'or 0' to handle None values
    last_cpu = run_metadata_list[-1]["infrastructure_metrics"]["summary"].get("cpu_peak_pct") or 0
    if last_cpu > 50:
        rows.append(f"| High CPU Usage | CPU | Run {len(run_metadata_list)} | CPU utilization peaked at {last_cpu:.2f}% |")
    
    if not rows:
        rows.append("| No infrastructure concerns | - | - | All resources within acceptable ranges |")
    
    return "\n".join(rows)


def _build_error_rate_summary(run_metadata_list: List[Dict]) -> str:
    """Build error rate summary."""
    # Use 'or 0' to handle None values in the list
    error_rates = [(meta["performance_metrics"].get("error_rate") or 0) for meta in run_metadata_list]
    
    max_error_rate = max(error_rates) if error_rates else 0
    if max_error_rate < 1.0:
        return "Error rates remain low (< 1%) across all test runs."
    else:
        return f"Error rates peaked at {max_error_rate:.2f}% in Run {error_rates.index(max_error_rate) + 1}."


def _build_api_comparison_table(run_metadata_list: List[Dict]) -> str:
    """Build API comparison table rows."""
    # Get all unique APIs across runs
    all_apis = set()
    for meta in run_metadata_list:
        violations = meta.get("sla_analysis", {}).get("violations", [])
        for v in violations:
            all_apis.add(v.get("api_name", "Unknown"))
    
    if not all_apis:
        return "| No SLA violations detected | - | - | - | - | - | - | ✅ |"
    
    rows = []
    for api in sorted(all_apis):
        # Resolve the per-API SLA threshold from the first run that has a
        # violation for this API. SLA thresholds come from slas.yaml via
        # PerfAnalysis -- no hardcoded values.
        api_sla_threshold = "N/A"
        for meta in run_metadata_list:
            violations = meta.get("sla_analysis", {}).get("violations", [])
            match = next((v for v in violations if v.get("api_name") == api), None)
            if match and match.get("sla_threshold"):
                api_sla_threshold = str(int(match["sla_threshold"]))
                break

        row_data = [api, api_sla_threshold]

        # Get response times for this API across runs
        for meta in run_metadata_list:
            violations = meta.get("sla_analysis", {}).get("violations", [])
            api_violation = next((v for v in violations if v.get("api_name") == api), None)
            
            if api_violation:
                rt = api_violation.get("avg_response_time", 0)
                row_data.append(f"{rt:.0f} ❌")
            else:
                row_data.append("✅")
        
        # Pad with N/A for missing runs
        while len(row_data) < 7:
            row_data.append("N/A")
        
        # Add trend (simple: comparing first and last)
        row_data.append("⬇️")
        
        rows.append("| " + " | ".join(row_data[:8]) + " |")
    
    return "\n".join(rows[:10])  # Limit to 10 rows


def _build_top_offenders_table(run_metadata_list: List[Dict]) -> str:
    """Build top offenders table."""
    # Similar to API comparison but sorted by severity
    return _build_api_comparison_table(run_metadata_list)


def _build_throughput_summary(run_metadata_list: List[Dict]) -> str:
    """Build throughput summary."""
    # Use 'or 0' to handle None values in the list
    throughputs = [(meta["performance_metrics"].get("avg_throughput") or 0) for meta in run_metadata_list]
    
    max_throughput = max(throughputs) if throughputs else 0
    min_throughput = min(throughputs) if throughputs else 0
    
    if max_throughput == 0:
        return "No throughput data available for comparison."
    elif max_throughput - min_throughput < max_throughput * 0.1:
        return "Throughput remains consistent across all test runs with less than 10% variation."
    else:
        return f"Throughput varies from {min_throughput:.2f} to {max_throughput:.2f} req/sec across runs."


def _determine_environment_type(run_metadata_list: List[Dict]) -> str:
    """
    Determine if infrastructure is host-based or kubernetes-based.
    
    Returns:
        'Host' for host-based environments, 'Service' for Kubernetes.
    """
    # Check the first run's entities for naming patterns
    entities = run_metadata_list[0]["infrastructure_metrics"].get("entities", [])
    
    if not entities:
        return "Service"  # Default to Service
    
    # Check entity names for host patterns (typically contain :: separator with hostname)
    # Kubernetes services typically have patterns like "Perf::service-name*"
    first_entity_name = entities[0].get("entity_name", "")
    
    # Host-based environments typically have hostnames with patterns like:
    # "Environment::hostname" where hostname looks like a server name
    # Kubernetes services have patterns like "Environment::service-name*"
    
    # Simple heuristic: if cpu_peak_cores is 0 for all entities, likely host-based
    # (since Datadog doesn't provide raw core data for hosts)
    all_zero_cores = all(
        entity.get("cpu_peak_cores", 0) == 0 
        for entity in entities
    )
    
    if all_zero_cores:
        return "Host"
    else:
        return "Service"


def _get_entities_from_metadata(run_metadata_list: List[Dict]) -> List[str]:
    """Get unique entity names across all runs."""
    all_entities = set()
    for meta in run_metadata_list:
        entities = meta["infrastructure_metrics"].get("entities", [])
        for entity in entities:
            all_entities.add(entity.get("entity_name", "Unknown"))
    return sorted(list(all_entities))


def _load_performance_analysis(run_id: str) -> Dict:
    """
    Load performance_analysis.json for a given run.
    
    Args:
        run_id: Test run identifier
        
    Returns:
        Dict containing performance analysis data, or empty dict if not found
    """
    perf_analysis_path = ARTIFACTS_PATH / run_id / "analysis" / "performance_analysis.json"
    
    if not perf_analysis_path.exists():
        return {}
    
    try:
        with open(perf_analysis_path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _get_api_names_from_perf_analysis(run_id_list: List[str]) -> List[str]:
    """
    Get unique API names across all runs from performance analysis files.
    Filters out non-API entries (like 'Duration Check', 'Assign Users', etc.)
    
    Args:
        run_id_list: List of test run IDs
        
    Returns:
        Sorted list of unique API names
    """
    all_apis = set()
    
    # Patterns to exclude (test framework artifacts, not actual APIs)
    exclude_patterns = [
        "Duration Check",
        "Assign Users",
        "Loop Start",
        "Loop End",
        "Initialize",
        "Launch",
        "Microsoft_Login",
        "Login Successful"
    ]
    
    for run_id in run_id_list:
        perf_data = _load_performance_analysis(run_id)
        api_analysis = perf_data.get("api_analysis", {})
        
        for api_name in api_analysis.keys():
            # Filter out non-API entries
            if not any(pattern in api_name for pattern in exclude_patterns):
                all_apis.add(api_name)
    
    return sorted(list(all_apis))


def _build_p90_comparison_table(run_id_list: List[str], run_metadata_list: List[Dict]) -> str:
    """
    Build P90 response time comparison table by API.
    Reads directly from performance_analysis.json files.
    
    Args:
        run_id_list: List of test run IDs
        run_metadata_list: List of metadata dicts (used for run labels)
        
    Returns:
        Markdown table string
    """
    run_count = len(run_id_list)
    api_names = _get_api_names_from_perf_analysis(run_id_list)
    
    if not api_names:
        return "| No API performance data available | N/A | N/A | N/A | N/A | N/A | - | - |"
    
    # Build dynamic header based on number of runs
    header_cols = ["API Name"]
    for i in range(run_count):
        header_cols.append(f"Run {i+1} P90 (ms)")
    
    # Pad for up to 5 runs
    while len(header_cols) < 6:
        header_cols.append("N/A")
    
    header_cols.extend(["Trend", "Δ vs Run 1"])
    
    header = "| " + " | ".join(header_cols[:8]) + " |"
    separator = "|" + "|".join(["---"] * 8) + "|"
    
    rows = [header, separator]
    
    # Load performance data for each run once
    perf_data_list = [_load_performance_analysis(run_id) for run_id in run_id_list]
    
    for api_name in api_names[:15]:  # Limit to 15 APIs for readability
        row_data = [api_name]
        first_p90 = None
        last_p90 = None
        
        for i, perf_data in enumerate(perf_data_list):
            api_analysis = perf_data.get("api_analysis", {})
            api_data = api_analysis.get(api_name, {})
            
            p90 = api_data.get("p90_response_time", 0)
            
            # Track first and last for trend calculation
            if i == 0 and p90 > 0:
                first_p90 = p90
            if i == len(perf_data_list) - 1 and p90 > 0:
                last_p90 = p90
            
            if p90 > 0:
                row_data.append(f"{p90:.0f}")
            else:
                row_data.append("N/A")
        
        # Pad with N/A for missing runs
        while len(row_data) < 6:
            row_data.append("N/A")
        
        # Add trend and delta (for P90, lower is better)
        if first_p90 is not None and last_p90 is not None and first_p90 > 0:
            delta, trend = _calculate_delta(last_p90, first_p90, lower_is_better=True)
            row_data.append(trend)
            row_data.append(f"{delta:+.1f}%")
        else:
            row_data.append("N/A")
            row_data.append("N/A")
        
        rows.append("| " + " | ".join(str(x) for x in row_data[:8]) + " |")
    
    return "\n".join(rows)


def _build_cpu_core_comparison_table(run_metadata_list: List[Dict]) -> str:
    """
    Build CPU core comparison table with one row per entity,
    columns for each run showing Peak/Avg values.
    
    Unit type is determined by report_config.yaml:
    - "cores" (default): Shows values in cores (e.g., 0.5, 1.25)
    - "millicores": Shows values in mCPU (e.g., 500, 1250)
    """
    env_type = _determine_environment_type(run_metadata_list)
    entity_names = _get_entities_from_metadata(run_metadata_list)
    run_count = len(run_metadata_list)
    
    if not entity_names:
        return "No infrastructure data available for CPU core comparison."
    
    # Load report config for unit settings
    report_config = load_report_config()
    cpu_config = report_config.get("infrastructure_tables", {}).get("cpu_core_usage", {})
    unit_type = cpu_config.get("unit", {}).get("type", "cores").lower()
    
    # Determine unit label and conversion factor
    if unit_type == "millicores":
        unit_label = "mCPU"
        conversion_factor = 1000  # cores to millicores
        value_format = "{:.0f}"  # No decimals for millicores
    else:  # cores (default)
        unit_label = "Cores"
        conversion_factor = 1.0
        value_format = "{:.4f}"
    
    # Build dynamic header based on number of runs
    header_cols = [f"{env_type} Name"]
    for i in range(run_count):
        header_cols.append(f"Run {i+1} Peak ({unit_label})")
        header_cols.append(f"Run {i+1} Avg ({unit_label})")
    header_cols.extend(["Trend", "Δ vs Run 1"])
    
    # Pad header for up to 5 runs
    while len(header_cols) < 13:  # Name + (5 runs * 2 cols) + Trend + Delta
        header_cols.insert(-2, "N/A")
    
    header = "| " + " | ".join(header_cols[:13]) + " |"
    separator = "|" + "|".join(["---"] * 13) + "|"
    
    rows = [header, separator]
    
    for entity_name in entity_names[:10]:  # Limit to 10 entities
        # Strip environment prefix and trailing wildcard for cleaner display
        display_name = strip_service_name_decorations(entity_name)
        row_data = [display_name]
        first_peak = None
        last_peak = None
        
        for i, meta in enumerate(run_metadata_list):
            entities = meta["infrastructure_metrics"].get("entities", [])
            entity = next((e for e in entities if e.get("entity_name") == entity_name), None)
            
            if entity:
                # Use 'or 0' to handle None values
                peak_cores = entity.get("cpu_peak_cores") or 0
                avg_cores = entity.get("cpu_avg_cores") or 0
                
                # Track first and last for trend calculation (use original values)
                if i == 0:
                    first_peak = peak_cores
                if i == len(run_metadata_list) - 1:
                    last_peak = peak_cores
                
                # Format values - show N/A if zero or None (host environments or undefined limits)
                if peak_cores == 0 and avg_cores == 0:
                    row_data.append("N/A")
                    row_data.append("N/A")
                else:
                    # Apply conversion factor for display
                    row_data.append(value_format.format(peak_cores * conversion_factor))
                    row_data.append(value_format.format(avg_cores * conversion_factor))
            else:
                row_data.append("N/A")
                row_data.append("N/A")
        
        # Pad with N/A for missing runs
        while len(row_data) < 11:  # Name + (5 runs * 2 cols)
            row_data.append("N/A")
        
        # Add trend and delta
        if first_peak is not None and last_peak is not None and first_peak > 0:
            delta, trend = _calculate_delta(last_peak, first_peak, lower_is_better=True)
            row_data.append(trend)
            row_data.append(f"{delta:+.2f}%")
        else:
            row_data.append("N/A")
            row_data.append("N/A")
        
        rows.append("| " + " | ".join(str(x) for x in row_data[:13]) + " |")
    
    # Add note for host environments
    if env_type == "Host":
        rows.append("")
        rows.append("*Note: CPU core usage is not available for host-based environments. Datadog provides CPU metrics as percentages only. See CPU Utilization section for percentage values.*")
    
    return "\n".join(rows)


def _build_memory_usage_comparison_table(run_metadata_list: List[Dict]) -> str:
    """
    Build memory usage comparison table with one row per entity,
    columns for each run showing Peak/Avg values.
    
    Unit type is determined by report_config.yaml:
    - "gb" (default): Shows values in GB (e.g., 1.5, 2.25)
    - "mb": Shows values in MB (e.g., 1536, 2304)
    """
    env_type = _determine_environment_type(run_metadata_list)
    entity_names = _get_entities_from_metadata(run_metadata_list)
    run_count = len(run_metadata_list)
    
    if not entity_names:
        return "No infrastructure data available for memory usage comparison."
    
    # Load report config for unit settings
    report_config = load_report_config()
    memory_config = report_config.get("infrastructure_tables", {}).get("memory_usage", {})
    unit_type = memory_config.get("unit", {}).get("type", "gb").lower()
    
    # Determine unit label and conversion factor
    if unit_type == "mb":
        unit_label = "MB"
        conversion_factor = 1024  # GB to MB
        value_format = "{:.0f}"  # No decimals for MB
    else:  # gb (default)
        unit_label = "GB"
        conversion_factor = 1.0
        value_format = "{:.2f}"
    
    # Build dynamic header based on number of runs
    header_cols = [f"{env_type} Name"]
    for i in range(run_count):
        header_cols.append(f"Run {i+1} Peak ({unit_label})")
        header_cols.append(f"Run {i+1} Avg ({unit_label})")
    header_cols.extend(["Trend", "Δ vs Run 1"])
    
    # Pad header for up to 5 runs
    while len(header_cols) < 13:  # Name + (5 runs * 2 cols) + Trend + Delta
        header_cols.insert(-2, "N/A")
    
    header = "| " + " | ".join(header_cols[:13]) + " |"
    separator = "|" + "|".join(["---"] * 13) + "|"
    
    rows = [header, separator]
    
    for entity_name in entity_names[:10]:  # Limit to 10 entities
        # Strip environment prefix and trailing wildcard for cleaner display
        display_name = strip_service_name_decorations(entity_name)
        row_data = [display_name]
        first_peak = None
        last_peak = None
        
        for i, meta in enumerate(run_metadata_list):
            entities = meta["infrastructure_metrics"].get("entities", [])
            entity = next((e for e in entities if e.get("entity_name") == entity_name), None)
            
            if entity:
                # Use 'or 0' to handle None values
                peak_gb = entity.get("memory_peak_gb") or 0
                avg_gb = entity.get("memory_avg_gb") or 0
                
                # Track first and last for trend calculation (use original values)
                if i == 0:
                    first_peak = peak_gb
                if i == len(run_metadata_list) - 1:
                    last_peak = peak_gb
                
                # Format values - show N/A if zero or None (undefined limits)
                if peak_gb == 0 and avg_gb == 0:
                    row_data.append("N/A")
                    row_data.append("N/A")
                else:
                    # Apply conversion factor for display
                    row_data.append(value_format.format(peak_gb * conversion_factor))
                    row_data.append(value_format.format(avg_gb * conversion_factor))
            else:
                row_data.append("N/A")
                row_data.append("N/A")
        
        # Pad with N/A for missing runs
        while len(row_data) < 11:  # Name + (5 runs * 2 cols)
            row_data.append("N/A")
        
        # Add trend and delta
        if first_peak is not None and last_peak is not None and first_peak > 0:
            delta, trend = _calculate_delta(last_peak, first_peak, lower_is_better=True)
            row_data.append(trend)
            row_data.append(f"{delta:+.2f}%")
        else:
            row_data.append("N/A")
            row_data.append("N/A")
        
        rows.append("| " + " | ".join(str(x) for x in row_data[:13]) + " |")
    
    return "\n".join(rows)


def _build_cpu_comparison_table(run_metadata_list: List[Dict]) -> str:
    """Build CPU utilization (%) comparison table."""
    # Get all entities from first run (use 'entities' key from metadata)
    entities = run_metadata_list[0]["infrastructure_metrics"].get("entities", [])
    
    if not entities:
        return "| No infrastructure data available | N/A | N/A | N/A | N/A | N/A | - | - |"
    
    rows = []
    for entity in entities[:5]:  # Limit to 5 entities
        entity_name = entity.get("entity_name", "Unknown")
        # Strip environment prefix and trailing wildcard for cleaner display
        display_name = strip_service_name_decorations(entity_name)
        row_data = [display_name]
        
        cpu_values = []
        for meta in run_metadata_list:
            meta_entities = meta["infrastructure_metrics"].get("entities", [])
            found_entity = next((e for e in meta_entities 
                                if e.get("entity_name") == entity_name), None)
            if found_entity:
                # Use 'or 0' to handle None values
                cpu_peak = found_entity.get("cpu_peak_pct") or 0
                cpu_values.append(cpu_peak)
                if cpu_peak == 0:
                    row_data.append("N/A")
                else:
                    row_data.append(f"{cpu_peak:.2f}%")
            else:
                row_data.append("N/A")
        
        # Pad with N/A
        while len(row_data) < 6:
            row_data.append("N/A")
        
        # Add trend and delta
        if len(cpu_values) >= 2:
            delta, trend = _calculate_delta(cpu_values[-1], cpu_values[0], lower_is_better=True)
            row_data.append(trend)
            row_data.append(f"{delta:+.2f}%")
        else:
            row_data.append("➡️")
            row_data.append("N/A")
        
        rows.append("| " + " | ".join(row_data[:8]) + " |")
    
    return "\n".join(rows)


def _build_memory_comparison_table(run_metadata_list: List[Dict]) -> str:
    """Build memory utilization (%) comparison table."""
    # Get all entities from first run (use 'entities' key from metadata)
    entities = run_metadata_list[0]["infrastructure_metrics"].get("entities", [])
    
    if not entities:
        return "| No infrastructure data available | N/A | N/A | N/A | N/A | N/A | - | - |"
    
    rows = []
    for entity in entities[:5]:  # Limit to 5 entities
        entity_name = entity.get("entity_name", "Unknown")
        # Strip environment prefix and trailing wildcard for cleaner display
        display_name = strip_service_name_decorations(entity_name)
        row_data = [display_name]
        
        mem_values = []
        for meta in run_metadata_list:
            meta_entities = meta["infrastructure_metrics"].get("entities", [])
            found_entity = next((e for e in meta_entities 
                                if e.get("entity_name") == entity_name), None)
            if found_entity:
                # Use 'or 0' to handle None values
                mem_peak = found_entity.get("memory_peak_pct") or 0
                mem_values.append(mem_peak)
                if mem_peak == 0:
                    row_data.append("N/A")
                else:
                    row_data.append(f"{mem_peak:.2f}%")
            else:
                row_data.append("N/A")
        
        while len(row_data) < 6:
            row_data.append("N/A")
        
        if len(mem_values) >= 2:
            delta, trend = _calculate_delta(mem_values[-1], mem_values[0], lower_is_better=True)
            row_data.append(trend)
            row_data.append(f"{delta:+.2f}%")
        else:
            row_data.append("➡️")
            row_data.append("N/A")
        
        rows.append("| " + " | ".join(row_data[:8]) + " |")
    
    return "\n".join(rows)


def _count_degraded_services(run_metadata_list: List[Dict], resource_type: str) -> int:
    """Count entities with degraded metrics."""
    if len(run_metadata_list) < 2:
        return 0
    
    entities = run_metadata_list[0]["infrastructure_metrics"].get("entities", [])
    degraded_count = 0
    
    metric_key = f"{resource_type}_peak_pct"
    
    for entity in entities:
        entity_name = entity.get("entity_name")
        first_val = entity.get(metric_key, 0)
        
        last_entity = next((e for e in run_metadata_list[-1]["infrastructure_metrics"].get("entities", [])
                           if e.get("entity_name") == entity_name), None)
        if last_entity:
            last_val = last_entity.get(metric_key, 0)
            delta, trend = _calculate_delta(last_val, first_val)
            if trend == "⬇️":
                degraded_count += 1
    
    return degraded_count


def _build_resource_efficiency(run_metadata_list: List[Dict]) -> str:
    """Build resource efficiency summary."""
    first_cpu_alloc = run_metadata_list[0]["infrastructure_metrics"]["summary"].get("cpu_cores_allocated", 0)
    last_cpu_alloc = run_metadata_list[-1]["infrastructure_metrics"]["summary"].get("cpu_cores_allocated", 0)
    
    if last_cpu_alloc < first_cpu_alloc:
        return f"Resource allocation decreased from {first_cpu_alloc:.1f} to {last_cpu_alloc:.1f} CPU cores, correlating with performance degradation."
    else:
        return "Resource allocation remains consistent across test runs."


def _build_correlation_section(run_metadata_list: List[Dict]) -> str:
    """Build correlation insights section (optional)."""
    # Check if any run has significant correlations
    has_insights = any(
        meta.get("correlation_insights", {}).get("significant_correlations")
        for meta in run_metadata_list
    )
    
    if not has_insights:
        return ""
    
    return "Correlation analysis reveals relationships between infrastructure metrics and performance outcomes."


def _build_correlation_observations(run_metadata_list: List[Dict]) -> str:
    """Build correlation observations."""
    observations = []
    
    for i, meta in enumerate(run_metadata_list):
        corr_insights = meta.get("correlation_insights", {}).get("significant_correlations", [])
        if corr_insights:
            observations.append(f"- **Run {i+1}:** {corr_insights[0].get('interpretation', 'N/A')}")
    
    return "\n".join(observations) if observations else "- No significant correlations detected"


def _build_conclusion_synopsis(run_metadata_list: List[Dict]) -> str:
    """Build conclusion synopsis."""
    perf_trend = _calculate_metric_trend(run_metadata_list, ["performance_metrics", "avg_response_time"])
    
    if perf_trend == "⬇️":
        return "Performance degraded across test runs, primarily due to resource constraints and increased load. Immediate action recommended to restore optimal performance."
    elif perf_trend == "⬆️":
        return "Performance improved across test runs, indicating successful optimizations or reduced load."
    else:
        return "Performance remains stable across test runs with no significant regressions detected."


def _build_recommendations(run_metadata_list: List[Dict]) -> str:
    """Build recommendations list."""
    recommendations = []
    
    # Check resource allocation
    first_cpu = run_metadata_list[0]["infrastructure_metrics"]["summary"].get("cpu_cores_allocated", 0)
    last_cpu = run_metadata_list[-1]["infrastructure_metrics"]["summary"].get("cpu_cores_allocated", 0)
    
    if last_cpu < first_cpu:
        recommendations.append(f"- Restore CPU allocation to baseline levels ({first_cpu:.1f} cores)")
    
    # Check CPU utilization
    last_cpu_peak = run_metadata_list[-1]["infrastructure_metrics"]["summary"].get("cpu_peak_pct", 0)
    if last_cpu_peak > 50:
        recommendations.append("- Investigate and optimize high CPU utilization")
    
    # SLA violations
    total_violations = sum(len(m.get("sla_analysis", {}).get("violations", [])) for m in run_metadata_list)
    if total_violations > 0:
        recommendations.append("- Address API endpoints consistently violating SLA thresholds")
    
    if not recommendations:
        recommendations.append("- Continue monitoring performance metrics")
        recommendations.append("- Maintain current resource allocation")
    
    return "\n".join(recommendations)


def _build_next_steps(run_metadata_list: List[Dict]) -> str:
    """Build next steps list."""
    return "\n".join([
        "- Schedule follow-up tests with adjusted resource allocation",
        "- Monitor infrastructure metrics during peak load periods",
        "- Review and optimize APIs with consistent SLA violations"
    ])


def _format_source_files(source_files: Dict) -> str:
    """Format source files for display."""
    return "\n".join([f"- {k}: {v}" for k, v in source_files.items()])
