"""
services/report_generator.py
Performance report generation from PerfAnalysis MCP outputs
"""
import json
import asyncio
import pypandoc
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
from fastmcp import Context
import re

# Import config at module level (global)
from utils.config import load_config, load_report_config
from utils.file_utils import (
    _load_json_safe,
    _load_text_safe,
    _load_text_file,
    _save_text_file,
    _save_json_file,
    _convert_to_pdf,
    _convert_to_docx
)
from utils.report_utils import (
    format_duration,
    strip_service_name_decorations,
    strip_report_headers_footers,
    strip_service_names_in_markdown
)
from utils.data_loader_utils import load_report_data
from services.kpi_report_generator import build_kpi_analysis_section, build_kpi_correlation_section

# Load configuration globally
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
REPORT_CONFIG = CONFIG.get('perf_report', {})
REPORT_DISPLAY_CONFIG = load_report_config()

ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))
TEMPLATES_PATH = Path(REPORT_CONFIG.get('templates_path', './templates'))

# Server version info (standardized across all MCPs)
SERVER_CONFIG = CONFIG.get('server', {})
MCP_VERSION = SERVER_CONFIG.get('version', 'unknown')
MCP_BUILD_DATE = SERVER_CONFIG.get('build', {}).get('date', 'unknown')


# -----------------------------------------------
# Main Performance Report functions
# ----------------------------------------------- 
async def generate_performance_test_report(run_id: str, ctx: Context, format: str = "md", template: Optional[str] = None) -> Dict:
    """
    Generate performance test report from PerfAnalysis outputs.
    
    Args:
        run_id: Test run identifier
        ctx: Workflow context for chaining
        format: Output format ('md', 'pdf', 'docx')
        template: Optional template name. If not provided, defaults to
                  'default_report_template.md'.
        
    Returns:
        Dict with metadata and paths
        
    Note:
        When regenerating an existing report, check the 'template_used' field
        in the report metadata JSON file (report_metadata_{run_id}.json) to
        ensure template consistency. Pass that template name explicitly to
        maintain the same report format.
        
        Example:
            # Check existing metadata first
            metadata = load_json(f"artifacts/{run_id}/reports/report_metadata_{run_id}.json")
            template_name = metadata.get("template_used", "default_report_template.md")
            
        TODO: Auto-detect template from existing metadata when not specified.
              See: docs/todo/TODO-report-template-auto-detection.md
    """
    try:
        generated_timestamp = datetime.now().isoformat()
        
        # Load all report data using shared helper
        data = await load_report_data(run_id)
        
        if data["status"] == "error":
            return {
                "run_id": run_id,
                "error": data["error"],
                "generated_timestamp": generated_timestamp
            }
        
        # Extract data from loader response
        run_path = data["run_path"]
        environment_type = data["environment_type"]
        perf_data = data["perf_data"]
        infra_data = data["infra_data"]
        corr_data = data["corr_data"]
        perf_summary_md = data["perf_summary_md"]
        infra_summary_md = data["infra_summary_md"]
        corr_summary_md = data["corr_summary_md"]
        log_data = data["log_data"]
        bottleneck_data = data["bottleneck_data"]
        jmeter_log_analysis_data = data["jmeter_log_analysis_data"]
        apm_trace_summary = data["apm_trace_summary"]
        load_test_config = data["load_test_config"]
        load_test_public_report = data["load_test_public_report"]
        kpi_data = data.get("kpi_data")
        kpi_summary_md = data.get("kpi_summary_md")
        kpi_correlations = data.get("kpi_correlations")
        source_files = data["source_files"]
        warnings = data["warnings"]
        missing_sections = data["missing_sections"]
        
        # Select template
        template_name = template or "default_report_template.md"
        template_path = TEMPLATES_PATH / template_name
        
        if not template_path.exists():
            return {
                "run_id": run_id,
                "error": f"Template not found: {template_name}",
                "generated_timestamp": generated_timestamp
            }
        
        template_content = await _load_text_file(template_path)
        
        # Build context for template
        context = _build_report_context(
            run_id,
            environment_type,
            generated_timestamp,
            perf_data,
            infra_data,
            corr_data,
            perf_summary_md,
            infra_summary_md,
            corr_summary_md,
            log_data,
            apm_trace_summary,
            load_test_config,
            bottleneck_data,
            jmeter_log_analysis_data,
            kpi_data=kpi_data,
            kpi_summary_md=kpi_summary_md,
            kpi_correlations=kpi_correlations
        )
        
        # Add load test public report link to context
        # TODO: Currently uses BlazeMeter-specific key. Future schema-driven architecture
        # will abstract this to support multiple load testing tools.
        if load_test_public_report and load_test_public_report.get("public_url"):
            public_url = load_test_public_report.get("public_url")
            context["BLAZEMETER_REPORT_LINK"] = f"[View Report]({public_url})"
        else:
            # Fallback: check if URL is in test_config.json (future enhancement)
            if load_test_config and load_test_config.get("public_url"):
                public_url = load_test_config.get("public_url")
                context["BLAZEMETER_REPORT_LINK"] = f"[View Report]({public_url})"
            else:
                context["BLAZEMETER_REPORT_LINK"] = "Not available"

        # Extract key metrics for metadata (needed for comparison reports)
        overall_stats = {}
        sla_analysis = {}
        api_violations = []

        if perf_data:
            overall_stats = perf_data.get("overall_stats", {})
            sla_analysis = perf_data.get("sla_analysis", {})
            
            # Extract SLA violators (thresholds come from slas.yaml via PerfAnalysis)
            api_analysis = perf_data.get("api_analysis", {})
            for api_name, stats in api_analysis.items():
                if not stats.get("sla_compliant", True):
                    api_violations.append({
                        "api_name": api_name,
                        "avg_response_time": stats.get("avg_response_time", 0),
                        "sla_threshold": stats.get("sla_threshold_ms"),
                        "sla_unit": stats.get("sla_unit", "P90"),
                        "sla_source": stats.get("sla_source", "slas.yaml"),
                        "error_rate": stats.get("error_rate", 0)
                    })

        # Extract infrastructure details
        infra_entities = []
        if infra_data:
            detailed = infra_data.get("detailed_metrics", {})
            # Choose platform branch based on environment type
            if environment_type == "kubernetes":
                platform = detailed.get("kubernetes", {})
                entities = platform.get("entities", {})  # Key is "entities" in infrastructure_analysis.json
            else:  # host
                platform = detailed.get("hosts", {})
                entities = platform.get("entities", {})  # Key is "entities" in infrastructure_analysis.json
            
            for entity_name, entity_data in entities.items():
                cpu_analysis = entity_data.get("cpu_analysis", {})
                mem_analysis = entity_data.get("memory_analysis", {})
                res_alloc = entity_data.get("resource_allocation", {})
                
                infra_entities.append({
                    "entity_name": entity_name,
                    "cpu_peak_pct": cpu_analysis.get("peak_utilization_pct", 0),
                    "cpu_avg_pct": cpu_analysis.get("avg_utilization_pct", 0),
                    # CPU core usage values (actual cores consumed)
                    "cpu_peak_cores": cpu_analysis.get("peak_usage_cores", 0),
                    "cpu_avg_cores": cpu_analysis.get("avg_usage_cores", 0),
                    "cpu_allocated_cores": cpu_analysis.get("allocated_cores", 0),
                    "memory_peak_pct": mem_analysis.get("peak_utilization_pct", 0),
                    "memory_avg_pct": mem_analysis.get("avg_utilization_pct", 0),
                    # Memory usage values (actual GB consumed)
                    "memory_peak_gb": mem_analysis.get("peak_usage_gb", 0),
                    "memory_avg_gb": mem_analysis.get("avg_usage_gb", 0),
                    "memory_allocated_gb": mem_analysis.get("allocated_gb", 0),
                    # Keep for backwards compatibility
                    "cpu_cores": res_alloc.get("cpus", 0),
                    "memory_gb": res_alloc.get("memory_gb", 0)
                })

        # Extract correlation insights
        correlation_insights = []
        if corr_data:
            significant_corr = corr_data.get("significant_correlations", [])
            for corr in significant_corr:
                correlation_insights.append({
                    "type": corr.get("type", "Unknown"),
                    "interpretation": corr.get("interpretation", "N/A"),
                    "strength": corr.get("strength", "N/A")
                })

        # Extract KPI metrics summary for metadata
        kpi_metrics_summary = _extract_kpi_metadata(kpi_data, kpi_correlations)

        # Render template
        report_markdown = _render_template(template_content, context)
        
        # Count chart placeholders
        chart_placeholder_count = report_markdown.count("[CHART_PLACEHOLDER")
        
        # Save markdown report
        reports_dir = run_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        report_name = f"performance_report_{run_id}.md"
        md_path = reports_dir / report_name
        await _save_text_file(md_path, report_markdown)
        
        # Convert to requested format
        final_path = md_path
        if format == "pdf":
            final_path = await _convert_to_pdf(md_path, reports_dir, run_id)
        elif format == "docx":
            final_path = await _convert_to_docx(md_path, reports_dir, run_id)

        # Define metadata path
        metadata_path = reports_dir / f"report_metadata_{run_id}.json"

        # Build response
        response = {
            "run_id": run_id,
            "report_name": report_name if format == "md" else final_path.name,
            "report_path": str(final_path),
            "metadata_path": str(metadata_path),
            "generated_timestamp": generated_timestamp,
            "mcp_version": MCP_VERSION,
            "format": format,
            "template_used": template_name,
            "source_files": source_files,
            "missing_sections": missing_sections,
            "warnings": warnings,
            "chart_placeholders": chart_placeholder_count,
        }
        
        # Build comprehensive metadata for programmatic use
        metadata = {
            # Include basic response info
            **response,
            
            # Add detailed metrics for comparison report generator
            "test_config": {
                "test_date": context.get("TEST_DATE", "N/A"),  # Actual test date from BlazeMeter
                "start_time": context.get("START_TIME", "N/A"),  # Full start timestamp
                "end_time": context.get("END_TIME", "N/A"),  # Full end timestamp
                "test_duration": overall_stats.get("test_duration", 0),
                "total_samples": overall_stats.get("total_samples", 0),
                "success_rate": overall_stats.get("success_rate", 0),
                "environment": context.get("ENVIRONMENT", "Unknown"),
                "test_type": context.get("TEST_TYPE", "Load Test"),
                "max_virtual_users": context.get("MAX_VIRTUAL_USERS", "N/A")  # From BlazeMeter config
            },
            
            "performance_metrics": {
                "avg_response_time": overall_stats.get("avg_response_time", 0),
                "min_response_time": overall_stats.get("min_response_time", 0),
                "max_response_time": overall_stats.get("max_response_time", 0),
                "median_response_time": overall_stats.get("median_response_time", 0),
                "p90_response_time": overall_stats.get("p90_response_time", 0),
                "p95_response_time": overall_stats.get("p95_response_time", 0),
                "p99_response_time": overall_stats.get("p99_response_time", 0),
                "avg_throughput": overall_stats.get("avg_throughput", 0),
                "peak_throughput": overall_stats.get("peak_throughput", overall_stats.get("avg_throughput", 0)),
                "error_count": overall_stats.get("error_count", 0),
                "error_rate": overall_stats.get("error_rate", 0),
                "top_error_type": overall_stats.get("top_error_type", "N/A")
            },
            
            "infrastructure_metrics": {
                "entities": infra_entities,
                "summary": {
                    "cpu_peak_pct": _safe_float(context.get("PEAK_CPU_USAGE")),
                    "cpu_avg_pct": _safe_float(context.get("AVG_CPU_USAGE")),
                    # CPU core usage summary (max across all services)
                    "cpu_peak_cores": _safe_float(context.get("PEAK_CPU_CORES")),
                    "cpu_avg_cores": _safe_float(context.get("AVG_CPU_CORES")),
                    "memory_peak_pct": _safe_float(context.get("PEAK_MEMORY_USAGE")),
                    "memory_avg_pct": _safe_float(context.get("AVG_MEMORY_USAGE")),
                    "cpu_cores_allocated": _safe_float(context.get("CPU_CORES_ALLOCATED")),
                    "memory_allocated_gb": _safe_float(context.get("MEMORY_ALLOCATED"))
                }
            },
            
            "sla_analysis": {
                # SLA thresholds are now per-API (resolved from slas.yaml).
                # The sla_threshold_ms here is informational -- it represents
                # the profile/file-level default. Per-API thresholds are in
                # each violation entry and in api_analysis.
                "sla_threshold_ms": sla_analysis.get("sla_threshold_ms"),
                "sla_unit": sla_analysis.get("sla_unit", "P90"),
                "compliant_apis": sla_analysis.get("compliant_apis", 0),
                "non_compliant_apis": sla_analysis.get("non_compliant_apis", 0),
                "compliance_rate": sla_analysis.get("compliance_rate", 100.0),
                "violations": api_violations
            },
            
            "correlation_insights": {
                "significant_correlations": correlation_insights,
                "correlation_summary": corr_data.get("insights", "") if corr_data else ""
            },
            
            "kpi_metrics": kpi_metrics_summary,
            
            "bugs_created": []  # Placeholder for future enhancement
        }

        # Save metadata JSON
        await _save_json_file(metadata_path, metadata)

        return response
        
    except Exception as e:
        return {
            "run_id": run_id,
            "error": f"Report generation failed: {str(e)}",
            "generated_timestamp": datetime.now().isoformat()
        }

# ===== REPORT CONTEXT BUILDER =====
def _build_report_context(
    run_id: str,
    environment_type: str,
    timestamp: str,
    perf_data: Optional[Dict],
    infra_data: Optional[Dict],
    corr_data: Optional[Dict],
    perf_md: Optional[str],
    infra_md: Optional[str],
    corr_md: Optional[str],
    log_data: Optional[Dict] = None,
    apm_trace_summary: Optional[Dict] = None,
    load_test_config: Optional[Dict] = None,
    bottleneck_data: Optional[Dict] = None,
    jmeter_log_analysis_data: Optional[Dict] = None,
    kpi_data: Optional[Dict] = None,
    kpi_summary_md: Optional[str] = None,
    kpi_correlations: Optional[Dict] = None
) -> Dict:
    """Build context dictionary for template rendering"""
    
    # Extract load test configuration (max_virtual_users, actual test dates/times)
    # TODO: Currently assumes BlazeMeter JSON structure. Future schema-driven architecture
    # will support multiple load testing tools with a unified config schema.
    max_virtual_users = "N/A"
    test_date = "N/A"
    start_time_full = "N/A"
    end_time_full = "N/A"
    if load_test_config:
        max_virtual_users = load_test_config.get("max_virtual_users", "N/A")
        # Get full start/end times (format: "2025-12-16 07:03:53 UTC")
        start_time_full = load_test_config.get("start_time", "N/A")
        end_time_full = load_test_config.get("end_time", "N/A")
        # Extract just the date portion (YYYY-MM-DD) for test_date
        if start_time_full and start_time_full != "N/A":
            test_date = start_time_full.split(" ")[0] if " " in start_time_full else start_time_full[:10]
    
    context = {
        "RUN_ID": run_id,
        "GENERATED_TIMESTAMP": timestamp,
        "MCP_VERSION": MCP_VERSION,
        "MCP_BUILD_DATE": MCP_BUILD_DATE,
        "ENVIRONMENT": "Unknown",
        "TEST_TYPE": "Load Test",
        "MAX_VIRTUAL_USERS": max_virtual_users,
        "TEST_DATE": test_date,
        "START_TIME": start_time_full,
        "END_TIME": end_time_full
    }
    
    # Extract performance data
    if perf_data:
        overall = perf_data.get("overall_stats", {})
        context.update({
            "TOTAL_SAMPLES": overall.get("total_samples", "N/A"),
            "SUCCESS_RATE": f"{overall.get('success_rate', 0):.2f}",
            "AVG_RESPONSE_TIME": f"{overall.get('avg_response_time', 0):.2f}",
            "MIN_RESPONSE_TIME": f"{overall.get('min_response_time', 0):.2f}",
            "MAX_RESPONSE_TIME": f"{overall.get('max_response_time', 0):.2f}",
            "MEDIAN_RESPONSE_TIME": f"{overall.get('median_response_time', 0):.2f}",
            "P90_RESPONSE_TIME": f"{overall.get('p90_response_time', 0):.2f}",
            "P95_RESPONSE_TIME": f"{overall.get('p95_response_time', 0):.2f}",
            "P99_RESPONSE_TIME": f"{overall.get('p99_response_time', 0):.2f}",
            "AVG_THROUGHPUT": f"{overall.get('avg_throughput', 0):.2f}",
            "TEST_DURATION": format_duration(overall.get('test_duration', 0)),
            "PEAK_THROUGHPUT": f"{overall.get('avg_throughput', 0):.2f}"
        })
        
        # Build API performance table
        context["API_PERFORMANCE_TABLE"] = _build_api_table(perf_data.get("api_analysis", {}))
        
        # SLA summary
        context["SLA_SUMMARY"] = _build_sla_summary(perf_data.get("sla_analysis", {}))
    else:
        _set_na_values(context, [
            "TOTAL_SAMPLES", "SUCCESS_RATE", "AVG_RESPONSE_TIME",
            "MIN_RESPONSE_TIME", "MAX_RESPONSE_TIME", "MEDIAN_RESPONSE_TIME",
            "P90_RESPONSE_TIME", "P95_RESPONSE_TIME", "P99_RESPONSE_TIME",
            "AVG_THROUGHPUT", "TEST_DURATION", "PEAK_THROUGHPUT"
        ])
        context["API_PERFORMANCE_TABLE"] = "No performance data available"
        context["SLA_SUMMARY"] = "No SLA data available"

    # Extract infrastructure data
    if infra_data:
        summary = infra_data.get("infrastructure_summary", {})
        environments_analyzed = infra_data.get("environments_analyzed", [])
        # Flatten and deduplicate environments, then stringify
        flat_envs: List[str] = []
        for item in environments_analyzed:
            if isinstance(item, list):
                flat_envs.extend([str(x) for x in item])
            else:
                flat_envs.append(str(item))
        seen = set()
        unique_envs: List[str] = []
        for e in flat_envs:
            if e and e not in seen:
                unique_envs.append(e)
                seen.add(e)
        env_str = ", ".join(unique_envs) if unique_envs else "Unknown"
        
        # Find peak CPU/Memory from detailed metrics
        cpu_peak, cpu_avg, mem_peak, mem_avg, cpu_cores, mem_gb, cpu_peak_cores, cpu_avg_cores = _extract_infra_peaks(environment_type, infra_data)

        # Determine the entity type label for section titles (Service vs Host)
        infra_entity_type = "Service" if environment_type == "kubernetes" else "Host"
        
        context.update({
            "ENVIRONMENT": env_str,
            "INFRA_ENTITY_TYPE": infra_entity_type,
            "PEAK_CPU_USAGE": f"{cpu_peak:.2f}",
            "AVG_CPU_USAGE": f"{cpu_avg:.2f}",
            "CPU_CORES_ALLOCATED": f"{cpu_cores:.2f}",
            "PEAK_MEMORY_USAGE": f"{mem_peak:.2f}",
            "AVG_MEMORY_USAGE": f"{mem_avg:.2f}",
            "MEMORY_ALLOCATED": f"{mem_gb:.2f}",
            "INFRASTRUCTURE_SUMMARY": strip_service_names_in_markdown(strip_report_headers_footers(infra_md)) or "No infrastructure summary available",
            # CPU core usage summary (max across all services)
            "PEAK_CPU_CORES": f"{cpu_peak_cores:.6f}",
            "AVG_CPU_CORES": f"{cpu_avg_cores:.6f}",
            # Per-service/host tables for CPU and memory % utilization
            "CPU_UTILIZATION_TABLE": _build_cpu_utilization_table(infra_data, environment_type),
            "MEMORY_UTILIZATION_TABLE": _build_memory_utilization_table(infra_data, environment_type),
            # Per-service/host tables for CPU core and memory GB usage
            "CPU_CORE_TABLE": _build_cpu_core_table(infra_data, environment_type),
            "MEMORY_USAGE_TABLE": _build_memory_usage_table(infra_data, environment_type)
        })

    else:
        _set_na_values(context, [
            "PEAK_CPU_USAGE", "AVG_CPU_USAGE", "CPU_CORES_ALLOCATED",
            "PEAK_MEMORY_USAGE", "AVG_MEMORY_USAGE", "MEMORY_ALLOCATED",
            "PEAK_CPU_CORES", "AVG_CPU_CORES"
        ])
        context["INFRA_ENTITY_TYPE"] = "Service"  # Default to Service when no infra data
        context["INFRASTRUCTURE_SUMMARY"] = "No infrastructure data available"
        context["CPU_UTILIZATION_TABLE"] = "No CPU utilization data available."
        context["MEMORY_UTILIZATION_TABLE"] = "No memory utilization data available."
        context["CPU_CORE_TABLE"] = "No CPU core data available."
        context["MEMORY_USAGE_TABLE"] = "No memory usage data available."
    
    # Extract correlation data
    if corr_data:
        # Strip headers/footers from loaded markdown file if present
        correlation_summary = strip_report_headers_footers(corr_md) if corr_md else _build_correlation_summary(corr_data)
        correlation_details = _build_correlation_details(corr_data)

        # Append KPI correlation highlights to the summary when available
        kpi_highlight = _build_kpi_correlation_highlight(kpi_correlations)
        if kpi_highlight:
            correlation_summary = (correlation_summary or "") + "\n\n" + kpi_highlight

        context.update({
            "CORRELATION_SUMMARY": correlation_summary or "No correlation summary available",
            "CORRELATION_DETAILS": correlation_details
        })
    else:
        context["CORRELATION_SUMMARY"] = "No correlation analysis available"
        context["CORRELATION_DETAILS"] = ""

    # Executive summary and other sections
    context["EXECUTIVE_SUMMARY"] = _build_executive_summary(perf_data, infra_data, corr_data)
    context["KEY_OBSERVATIONS"] = _build_key_observations(perf_data, infra_data)
    context["ISSUES_TABLE"] = _build_issues_table(perf_data)
    context["BOTTLENECK_ANALYSIS"] = _build_bottleneck_analysis(corr_data, infra_data, bottleneck_data)
    context["RECOMMENDATIONS"] = _build_recommendations(perf_data, infra_data, corr_data)
    context["SOURCE_FILES_LIST"] = "See metadata JSON for complete source file list"
    
    # Log analysis sections
    context["LOG_ANALYSIS_SUMMARY"] = _build_log_analysis_summary(log_data)
    context["JMETER_LOG_ANALYSIS"] = _build_jmeter_log_analysis(log_data, jmeter_log_analysis_data)
    context["DATADOG_LOG_ANALYSIS"] = _build_datadog_log_analysis(log_data)
    context["APM_TRACE_ANALYSIS"] = _build_apm_trace_analysis(apm_trace_summary)
    
    # KPI analysis sections (from kpi_report_generator.py)
    context["KPI_ANALYSIS_SECTION"] = build_kpi_analysis_section(kpi_data, kpi_summary_md)
    context["KPI_CORRELATION_SECTION"] = build_kpi_correlation_section(kpi_correlations)
    
    return context

# -----------------------------------------------
# Report generation utility functions
# ----------------------------------------------- 
def _render_template(template: str, context: Dict) -> str:
    """Render template with context using {{}} placeholders"""
    rendered = template
    for key, value in context.items():
        placeholder = "{{" + key + "}}"
        rendered = rendered.replace(placeholder, str(value))
    return rendered

def _build_api_table(api_analysis: Dict) -> str:
    """Build Markdown table for API performance, sorted by API name."""
    if not api_analysis:
        return "No API data available"
    
    # Sort API names alphabetically to respect TCxx_TSxx natural ordering
    sorted_items = sorted(api_analysis.items(), key=lambda x: x[0])

    lines = [
        "| API Name | Samples | Avg (ms) | Min (ms) | Max (ms) | 95th (ms) | Error Rate | SLA Met |",
        "|----------|---------|----------|----------|----------|-----------|------------|---------|"
    ]
    
    for api_name, stats in sorted_items:
        line = (
            f"| {api_name} | "
            f"{stats.get('samples', 0)} | "
            f"{stats.get('avg_response_time', 0):.2f} | "
            f"{stats.get('min_response_time', 0):.2f} | "
            f"{stats.get('max_response_time', 0):.2f} | "
            f"{stats.get('p95_response_time', 0):.2f} | "
            f"{stats.get('error_rate', 0):.2f}% | "
            f"{'✅' if stats.get('sla_compliant', False) else '❌'} |"
        )
        lines.append(line)
    
    return "\n".join(lines)

def _build_sla_summary(sla_analysis: Dict) -> str:
    """Build SLA compliance summary"""
    if not sla_analysis:
        return "No SLA analysis available"
    
    total = sla_analysis.get("total_apis", 0)
    compliant = sla_analysis.get("compliant_apis", 0)
    compliance_rate = sla_analysis.get("compliance_rate", 0)
    
    return f"**SLA Compliance:** {compliant}/{total} APIs met SLA ({compliance_rate:.1f}%)"


def _build_cpu_utilization_table(infra_data: Optional[Dict], environment_type: str) -> str:
    """
    Build Markdown table for per-service/host CPU % utilization.
    
    Handles cases where Kubernetes limits are not defined (utilization shows as N/A*).
    
    Args:
        infra_data: Infrastructure analysis data
        environment_type: 'host' or 'kubernetes'
    
    Returns:
        Markdown table string with CPU utilization percentages per service/host
    """
    if not infra_data:
        return "No CPU utilization data available."
    
    detailed = infra_data.get("detailed_metrics", {})
    
    # Check config for whether to show allocated column
    show_allocated = REPORT_DISPLAY_CONFIG.get("infrastructure_tables", {}).get(
        "cpu_utilization", {}
    ).get("show_allocated_column", True)
    
    # Determine entity type and get entities
    if environment_type == "kubernetes":
        platform_data = detailed.get("kubernetes", {})
        # Support both "entities" (new) and "services" (old) for backwards compatibility
        entities = platform_data.get("entities", {}) or platform_data.get("services", {})
        entity_label = "Service Name"
    else:  # host
        platform_data = detailed.get("hosts", {})
        entities = platform_data.get("hosts", {})
        entity_label = "Host Name"
    
    if not entities:
        return f"No {environment_type} CPU data found."
    
    # Build table header (conditionally include Allocated column)
    if show_allocated:
        lines = [
            f"| {entity_label} | Peak (%) | Avg (%) | Min (%) | Allocated |",
            "|" + "-" * (len(entity_label) + 2) + "|----------|---------|---------|-----------|"
        ]
    else:
        lines = [
            f"| {entity_label} | Peak (%) | Avg (%) | Min (%) |",
            "|" + "-" * (len(entity_label) + 2) + "|----------|---------|---------|"
        ]
    
    # Sort entities alphabetically
    sorted_entities = sorted(entities.items(), key=lambda x: x[0])
    
    # Track if any entities have undefined limits (for footnote)
    any_limits_undefined = False
    
    for entity_name, entity_data in sorted_entities:
        cpu_analysis = entity_data.get("cpu_analysis", {})
        res_alloc = entity_data.get("resource_allocation", {})
        limits_status = entity_data.get("limits_status", {})
        
        # Strip environment prefix and trailing wildcard for cleaner display
        display_name = strip_service_name_decorations(entity_name)
        
        if not cpu_analysis:
            if show_allocated:
                lines.append(f"| {display_name} | N/A | N/A | N/A | N/A |")
            else:
                lines.append(f"| {display_name} | N/A | N/A | N/A |")
            continue
        
        peak_pct = cpu_analysis.get("peak_utilization_pct")
        avg_pct = cpu_analysis.get("avg_utilization_pct")
        min_pct = cpu_analysis.get("min_utilization_pct")
        allocated = cpu_analysis.get("allocated_cores", res_alloc.get("cpus", "N/A"))
        
        # Check if limits are not defined (utilization values are None)
        utilization_status = cpu_analysis.get("utilization_status")
        limits_defined = limits_status.get("cpu_limits_defined", True)
        
        if utilization_status == "limits_not_defined" or not limits_defined or peak_pct is None:
            any_limits_undefined = True
            # Show N/A* to indicate limits not defined
            peak_str = "N/A*"
            avg_str = "N/A*"
            min_str = "N/A*"
        else:
            # Format values normally
            peak_str = f"{peak_pct:.2f}" if peak_pct is not None else "N/A"
            avg_str = f"{avg_pct:.2f}" if avg_pct is not None else "N/A"
            min_str = f"{min_pct:.2f}" if min_pct is not None else "N/A"
        
        allocated_str = f"{allocated:.2f}" if isinstance(allocated, (int, float)) else str(allocated) if allocated else "N/A"
        
        if show_allocated:
            line = f"| {display_name} | {peak_str} | {avg_str} | {min_str} | {allocated_str} |"
        else:
            line = f"| {display_name} | {peak_str} | {avg_str} | {min_str} |"
        lines.append(line)
    
    # Add footnote if any entities have undefined limits
    if any_limits_undefined and environment_type == "kubernetes":
        lines.append("")
        lines.append("*\\*N/A indicates CPU limits are not defined in Kubernetes for this service. % utilization cannot be calculated.*")
    
    return "\n".join(lines)


def _build_memory_utilization_table(infra_data: Optional[Dict], environment_type: str) -> str:
    """
    Build Markdown table for per-service/host Memory % utilization.
    
    Handles cases where Kubernetes limits are not defined (utilization shows as N/A*).
    
    Args:
        infra_data: Infrastructure analysis data
        environment_type: 'host' or 'kubernetes'
    
    Returns:
        Markdown table string with memory utilization percentages per service/host
    """
    if not infra_data:
        return "No memory utilization data available."
    
    detailed = infra_data.get("detailed_metrics", {})
    
    # Check config for whether to show allocated column
    show_allocated = REPORT_DISPLAY_CONFIG.get("infrastructure_tables", {}).get(
        "memory_utilization", {}
    ).get("show_allocated_column", True)
    
    # Determine entity type and get entities
    if environment_type == "kubernetes":
        platform_data = detailed.get("kubernetes", {})
        # Support both "entities" (new) and "services" (old) for backwards compatibility
        entities = platform_data.get("entities", {}) or platform_data.get("services", {})
        entity_label = "Service Name"
    else:  # host
        platform_data = detailed.get("hosts", {})
        entities = platform_data.get("hosts", {})
        entity_label = "Host Name"
    
    if not entities:
        return f"No {environment_type} memory data found."
    
    # Build table header (conditionally include Allocated column)
    if show_allocated:
        lines = [
            f"| {entity_label} | Peak (%) | Avg (%) | Min (%) | Allocated |",
            "|" + "-" * (len(entity_label) + 2) + "|----------|---------|---------|-----------|"
        ]
    else:
        lines = [
            f"| {entity_label} | Peak (%) | Avg (%) | Min (%) |",
            "|" + "-" * (len(entity_label) + 2) + "|----------|---------|---------|"
        ]
    
    # Sort entities alphabetically
    sorted_entities = sorted(entities.items(), key=lambda x: x[0])
    
    # Track if any entities have undefined limits (for footnote)
    any_limits_undefined = False
    
    for entity_name, entity_data in sorted_entities:
        mem_analysis = entity_data.get("memory_analysis", {})
        res_alloc = entity_data.get("resource_allocation", {})
        limits_status = entity_data.get("limits_status", {})
        
        # Strip environment prefix and trailing wildcard for cleaner display
        display_name = strip_service_name_decorations(entity_name)
        
        if not mem_analysis:
            if show_allocated:
                lines.append(f"| {display_name} | N/A | N/A | N/A | N/A |")
            else:
                lines.append(f"| {display_name} | N/A | N/A | N/A |")
            continue
        
        peak_pct = mem_analysis.get("peak_utilization_pct")
        avg_pct = mem_analysis.get("avg_utilization_pct")
        min_pct = mem_analysis.get("min_utilization_pct")
        allocated_gb = mem_analysis.get("allocated_gb", res_alloc.get("memory_gb"))
        
        # Check if limits are not defined (utilization values are None)
        utilization_status = mem_analysis.get("utilization_status")
        limits_defined = limits_status.get("mem_limits_defined", True)
        
        if utilization_status == "limits_not_defined" or not limits_defined or peak_pct is None:
            any_limits_undefined = True
            # Show N/A* to indicate limits not defined
            peak_str = "N/A*"
            avg_str = "N/A*"
            min_str = "N/A*"
        else:
            # Format values normally
            peak_str = f"{peak_pct:.2f}" if peak_pct is not None else "N/A"
            avg_str = f"{avg_pct:.2f}" if avg_pct is not None else "N/A"
            min_str = f"{min_pct:.2f}" if min_pct is not None else "N/A"
        
        allocated_str = f"{allocated_gb:.2f} GB" if allocated_gb is not None else "N/A"
        
        if show_allocated:
            line = f"| {display_name} | {peak_str} | {avg_str} | {min_str} | {allocated_str} |"
        else:
            line = f"| {display_name} | {peak_str} | {avg_str} | {min_str} |"
        lines.append(line)
    
    # Add footnote if any entities have undefined limits
    if any_limits_undefined and environment_type == "kubernetes":
        lines.append("")
        lines.append("*\\*N/A indicates Memory limits are not defined in Kubernetes for this service. % utilization cannot be calculated.*")
    
    return "\n".join(lines)


def _build_cpu_core_table(infra_data: Optional[Dict], environment_type: str) -> str:
    """
    Build Markdown table for per-service/host CPU core usage.
    
    Args:
        infra_data: Infrastructure analysis data
        environment_type: 'host' or 'kubernetes'
    
    Returns:
        Markdown table string with CPU core metrics per service/host
    """
    if not infra_data:
        return "No CPU core data available."
    
    detailed = infra_data.get("detailed_metrics", {})
    
    # Check config for whether to show allocated column
    show_allocated = REPORT_DISPLAY_CONFIG.get("infrastructure_tables", {}).get(
        "cpu_core_usage", {}
    ).get("show_allocated_column", True)
    
    # Handle Kubernetes environments
    if environment_type == "kubernetes":
        k8s_data = detailed.get("kubernetes", {})
        # Support both "entities" (new) and "services" (old) for backwards compatibility
        entities = k8s_data.get("entities", {}) or k8s_data.get("services", {})
    
        if not entities:
            return "No Kubernetes services found."
        
        # Build table header (conditionally include Allocated column)
        if show_allocated:
            lines = [
                "| Service Name | Peak (Cores) | Peak (mCPU) | Avg (Cores) | Avg (mCPU) | Allocated (Cores) |",
                "|--------------|--------------|-------------|-------------|------------|-------------------|"
            ]
        else:
            lines = [
                "| Service Name | Peak (Cores) | Peak (mCPU) | Avg (Cores) | Avg (mCPU) |",
                "|--------------|--------------|-------------|-------------|------------|"
            ]
        
        # Sort entities alphabetically
        sorted_entities = sorted(entities.items(), key=lambda x: x[0])
        
        for service_name, service_data in sorted_entities:
            cpu_analysis = service_data.get("cpu_analysis", {})
            
            # Strip environment prefix and trailing wildcard for cleaner display
            display_name = strip_service_name_decorations(service_name)
            
            if not cpu_analysis:
                # Handle missing cpu_analysis gracefully
                if show_allocated:
                    lines.append(f"| {display_name} | N/A | N/A | N/A | N/A | N/A |")
                else:
                    lines.append(f"| {display_name} | N/A | N/A | N/A | N/A |")
                continue
            
            peak_cores = cpu_analysis.get("peak_usage_cores")
            avg_cores = cpu_analysis.get("avg_usage_cores")
            allocated_cores = cpu_analysis.get("allocated_cores")
            
            # Format values, using N/A for missing data
            if peak_cores is not None:
                peak_cores_str = f"{peak_cores:.4f}"
                peak_mcpu_str = f"{peak_cores * 1000:.2f}"
            else:
                peak_cores_str = "N/A"
                peak_mcpu_str = "N/A"
            
            if avg_cores is not None:
                avg_cores_str = f"{avg_cores:.4f}"
                avg_mcpu_str = f"{avg_cores * 1000:.2f}"
            else:
                avg_cores_str = "N/A"
                avg_mcpu_str = "N/A"
            
            allocated_str = f"{allocated_cores:.2f}" if allocated_cores is not None else "N/A"
            
            if show_allocated:
                line = (
                    f"| {display_name} | "
                    f"{peak_cores_str} | "
                    f"{peak_mcpu_str} | "
                    f"{avg_cores_str} | "
                    f"{avg_mcpu_str} | "
                    f"{allocated_str} |"
                )
            else:
                line = (
                    f"| {display_name} | "
                    f"{peak_cores_str} | "
                    f"{peak_mcpu_str} | "
                    f"{avg_cores_str} | "
                    f"{avg_mcpu_str} |"
                )
            lines.append(line)
        
        return "\n".join(lines)
    
    # Handle Host environments
    # TODO: Update Datadog-MCP query to include raw CPU core metrics (e.g., system.cpu.num_cores 
    #       or similar) to enable actual core usage calculations. Currently, Datadog only provides 
    #       CPU as percentages (system.cpu.user, system.cpu.system), which cannot be reliably 
    #       converted to core usage without assuming the allocated CPUs value in environments.json 
    #       is correct.
    if environment_type == "host":
        host_data = detailed.get("hosts", {})
        hosts = host_data.get("hosts", {})
        
        if not hosts:
            return "No host data found."
        
        # Build table with N/A for core values since only percentages are available
        if show_allocated:
            lines = [
                "| Host Name | Peak (Cores) | Peak (mCPU) | Avg (Cores) | Avg (mCPU) | Allocated (Cores) |",
                "|-----------|--------------|-------------|-------------|------------|-------------------|"
            ]
        else:
            lines = [
                "| Host Name | Peak (Cores) | Peak (mCPU) | Avg (Cores) | Avg (mCPU) |",
                "|-----------|--------------|-------------|-------------|------------|"
            ]
        
        # Sort hosts alphabetically
        sorted_hosts = sorted(hosts.items(), key=lambda x: x[0])
        
        for host_name, host_data_item in sorted_hosts:
            cpu_analysis = host_data_item.get("cpu_analysis", {})
            
            # Host CPU is in percentages only - core values are not available
            # Strip environment prefix and trailing wildcard for cleaner display
            display_name = strip_service_name_decorations(host_name)
            
            if show_allocated:
                line = (
                    f"| {display_name} | "
                    f"N/A | "
                    f"N/A | "
                    f"N/A | "
                    f"N/A | "
                    f"N/A |"
                )
            else:
                line = (
                    f"| {display_name} | "
                    f"N/A | "
                    f"N/A | "
                    f"N/A | "
                    f"N/A |"
                )
            lines.append(line)
        
        # Add explanatory note
        lines.append("")
        lines.append("*Note: CPU core usage is not available for host-based environments. "
                    "Datadog provides CPU metrics as percentages only. See CPU Utilization section for percentage values.*")
        
        return "\n".join(lines)
    
    return "Unknown environment type for CPU core table."


def _build_memory_usage_table(infra_data: Optional[Dict], environment_type: str) -> str:
    """
    Build Markdown table for per-service/host memory usage.
    
    Args:
        infra_data: Infrastructure analysis data
        environment_type: 'host' or 'kubernetes'
    
    Returns:
        Markdown table string with memory usage metrics per service/host
    """
    if not infra_data:
        return "No memory usage data available."
    
    detailed = infra_data.get("detailed_metrics", {})
    
    # Check config for whether to show allocated column
    show_allocated = REPORT_DISPLAY_CONFIG.get("infrastructure_tables", {}).get(
        "memory_usage", {}
    ).get("show_allocated_column", True)
    
    # Handle Kubernetes environments
    if environment_type == "kubernetes":
        k8s_data = detailed.get("kubernetes", {})
        # Support both "entities" (new) and "services" (old) for backwards compatibility
        entities = k8s_data.get("entities", {}) or k8s_data.get("services", {})
    
        if not entities:
            return "No Kubernetes services found."
        
        # Build table header (conditionally include Allocated column)
        if show_allocated:
            lines = [
                "| Service Name | Peak (GB) | Peak (MB) | Avg (GB) | Avg (MB) | Allocated (GB) |",
                "|--------------|-----------|-----------|----------|----------|----------------|"
            ]
        else:
            lines = [
                "| Service Name | Peak (GB) | Peak (MB) | Avg (GB) | Avg (MB) |",
                "|--------------|-----------|-----------|----------|----------|"
            ]
        
        # Sort entities alphabetically
        sorted_entities = sorted(entities.items(), key=lambda x: x[0])
        
        for service_name, service_data in sorted_entities:
            mem_analysis = service_data.get("memory_analysis", {})
            
            # Strip environment prefix and trailing wildcard for cleaner display
            display_name = strip_service_name_decorations(service_name)
            
            if not mem_analysis:
                # Handle missing memory_analysis gracefully
                if show_allocated:
                    lines.append(f"| {display_name} | N/A | N/A | N/A | N/A | N/A |")
                else:
                    lines.append(f"| {display_name} | N/A | N/A | N/A | N/A |")
                continue
            
            peak_gb = mem_analysis.get("peak_usage_gb")
            avg_gb = mem_analysis.get("avg_usage_gb")
            allocated_gb = mem_analysis.get("allocated_gb")
            
            # Format values, using N/A for missing data
            if peak_gb is not None:
                peak_gb_str = f"{peak_gb:.4f}"
                peak_mb_str = f"{peak_gb * 1024:.2f}"
            else:
                peak_gb_str = "N/A"
                peak_mb_str = "N/A"
            
            if avg_gb is not None:
                avg_gb_str = f"{avg_gb:.4f}"
                avg_mb_str = f"{avg_gb * 1024:.2f}"
            else:
                avg_gb_str = "N/A"
                avg_mb_str = "N/A"
            
            allocated_str = f"{allocated_gb:.2f}" if allocated_gb is not None else "N/A"
            
            if show_allocated:
                line = (
                    f"| {display_name} | "
                    f"{peak_gb_str} | "
                    f"{peak_mb_str} | "
                    f"{avg_gb_str} | "
                    f"{avg_mb_str} | "
                    f"{allocated_str} |"
                )
            else:
                line = (
                    f"| {display_name} | "
                    f"{peak_gb_str} | "
                    f"{peak_mb_str} | "
                    f"{avg_gb_str} | "
                    f"{avg_mb_str} |"
                )
            lines.append(line)
        
        return "\n".join(lines)
    
    # Handle Host environments
    # Host memory data is available from Datadog (system.mem.used, system.mem.total)
    # and PerfAnalysis-MCP calculates peak_usage_gb and avg_usage_gb from raw bytes
    if environment_type == "host":
        host_data = detailed.get("hosts", {})
        hosts = host_data.get("hosts", {})
        
        if not hosts:
            return "No host data found."
        
        # Build table header (conditionally include Allocated column)
        if show_allocated:
            lines = [
                "| Host Name | Peak (GB) | Peak (MB) | Avg (GB) | Avg (MB) | Allocated (GB) |",
                "|-----------|-----------|-----------|----------|----------|----------------|"
            ]
        else:
            lines = [
                "| Host Name | Peak (GB) | Peak (MB) | Avg (GB) | Avg (MB) |",
                "|-----------|-----------|-----------|----------|----------|"
            ]
        
        # Sort hosts alphabetically
        sorted_hosts = sorted(hosts.items(), key=lambda x: x[0])
        
        for host_name, host_data_item in sorted_hosts:
            mem_analysis = host_data_item.get("memory_analysis", {})
            res_alloc = host_data_item.get("resource_allocation", {})
            
            # Strip environment prefix and trailing wildcard for cleaner display
            display_name = strip_service_name_decorations(host_name)
            
            if not mem_analysis:
                # Handle missing memory_analysis gracefully
                if show_allocated:
                    lines.append(f"| {display_name} | N/A | N/A | N/A | N/A | N/A |")
                else:
                    lines.append(f"| {display_name} | N/A | N/A | N/A | N/A |")
                continue
            
            # Get memory values from PerfAnalysis-MCP output
            peak_gb = mem_analysis.get("peak_usage_gb")
            avg_gb = mem_analysis.get("avg_usage_gb")
            allocated_gb = res_alloc.get("memory_gb")
            
            # Format values, using N/A for missing data
            if peak_gb is not None:
                peak_gb_str = f"{peak_gb:.4f}"
                peak_mb_str = f"{peak_gb * 1024:.2f}"
            else:
                peak_gb_str = "N/A"
                peak_mb_str = "N/A"
            
            if avg_gb is not None:
                avg_gb_str = f"{avg_gb:.4f}"
                avg_mb_str = f"{avg_gb * 1024:.2f}"
            else:
                avg_gb_str = "N/A"
                avg_mb_str = "N/A"
            
            allocated_str = f"{allocated_gb:.2f}" if allocated_gb is not None else "N/A"
            
            if show_allocated:
                line = (
                    f"| {display_name} | "
                    f"{peak_gb_str} | "
                    f"{peak_mb_str} | "
                    f"{avg_gb_str} | "
                    f"{avg_mb_str} | "
                    f"{allocated_str} |"
                )
            else:
                line = (
                    f"| {display_name} | "
                    f"{peak_gb_str} | "
                    f"{peak_mb_str} | "
                    f"{avg_gb_str} | "
                    f"{avg_mb_str} |"
                )
            lines.append(line)
        
        return "\n".join(lines)
    
    return "Unknown environment type for memory usage table."

def _build_executive_summary(perf_data: Optional[Dict], infra_data: Optional[Dict], corr_data: Optional[Dict]) -> str:
    """Generate executive summary"""
    if not perf_data:
        return "Performance test completed. Detailed analysis data not available."
    
    overall = perf_data.get("overall_stats", {})
    success_rate = overall.get("success_rate", 0)
    avg_rt = overall.get("avg_response_time", 0)
    
    summary = f"Performance test completed with {success_rate:.1f}% success rate and average response time of {avg_rt:.2f}ms. "
    
    if success_rate >= 99.5:
        summary += "Test performed exceptionally well with minimal errors. "
    elif success_rate >= 95:
        summary += "Test performed well with acceptable error rates. "
    else:
        summary += "Test experienced elevated error rates requiring investigation. "
    
    return summary

def _build_key_observations(perf_data: Optional[Dict], infra_data: Optional[Dict]) -> str:
    """Build key observations bullets"""
    observations = []
    
    if perf_data:
        overall = perf_data.get("overall_stats", {})
        if overall.get("success_rate", 0) >= 99:
            observations.append("- Excellent success rate observed")
        if overall.get("error_count", 0) > 0:
            observations.append(f"- {overall.get('error_count', 0)} errors detected during test execution")
    
    if infra_data:
        observations.append("- Infrastructure metrics captured and analyzed")
    
    return "\n".join(observations) if observations else "- Test completed successfully"

def _build_issues_table(perf_data: Optional[Dict]) -> str:
    """Build issues table from error data"""
    if not perf_data:
        return "No issues data available"
    
    overall = perf_data.get("overall_stats", {})
    error_count = overall.get("error_count", 0)
    
    if error_count == 0:
        return "**No issues detected during test execution**"
    
    return f"| Issue Type | Count |\n|------------|-------|\n| Errors | {error_count} |"

def _build_correlation_summary(corr_data: Dict) -> str:
    """Build correlation summary text"""
    correlations = corr_data.get("significant_correlations", [])
    if not correlations:
        return "No significant correlations found between performance and infrastructure metrics."
    
    summary = f"Found {len(correlations)} significant correlation(s):\n"

    for corr in correlations:
        summary += f"- {corr.get('type', 'Unknown')}: {corr.get('interpretation', 'N/A')}\n"

    return summary

def _build_correlation_details(corr_data: Dict) -> str:
    """Build detailed correlation information"""
    matrix = corr_data.get("correlation_matrix", {})
    if not matrix:
        return "No correlation matrix data available"
    
    lines = ["| Metric Pair | Correlation Coefficient |", "|-------------|------------------------|"]
    
    for key, value in matrix.items():
        lines.append(f"| {key} | {value:.4f} |")
    
    return "\n".join(lines)


def _build_kpi_correlation_highlight(kpi_correlations: Optional[Dict]) -> str:
    """
    Build a brief KPI correlation highlight for the main correlation summary.

    Extracts strong and moderate KPI-to-performance correlation pairs and
    returns a short paragraph suitable for appending to Section 5.0.
    Returns an empty string when no notable correlations exist.
    """
    if not kpi_correlations:
        return ""

    pairs = kpi_correlations.get("pairs", [])
    if not pairs:
        return ""

    notable = [
        p for p in pairs
        if p.get("strength") in ("strong", "moderate")
    ]
    if not notable:
        return ""

    lines = [f"**Application KPI Correlations:** {len(notable)} notable KPI-to-performance correlation(s) detected:"]
    lines.append("")
    for p in notable:
        coeff = p.get("coefficient", 0)
        lines.append(
            f"- **{p.get('kpi_metric', '')}** vs {p.get('compared_with', '')}: "
            f"{p.get('strength', '')} {p.get('direction', '')} "
            f"(r={coeff:.4f})"
        )
    lines.append("")
    lines.append("See **Section 5.2 KPI Correlations** for the full analysis.")
    return "\n".join(lines)


def _build_bottleneck_analysis(
    corr_data: Optional[Dict],
    infra_data: Optional[Dict],
    bottleneck_data: Optional[Dict] = None
) -> str:
    """
    Build bottleneck identification section with properly formatted output.
    
    When bottleneck_data is available (from PerfAnalysis identify_bottlenecks),
    uses the rich analysis with summary headline, threshold concurrency, severity
    breakdown, and per-finding details. Falls back to correlation-based insights
    when bottleneck_data is not available.
    
    Args:
        corr_data: Correlation analysis data
        infra_data: Infrastructure analysis data
        bottleneck_data: Bottleneck analysis JSON from PerfAnalysis identify_bottlenecks (optional)
        
    Returns:
        Formatted markdown string with bottleneck analysis
    """
    # Prefer rich bottleneck analysis data when available
    if bottleneck_data:
        return _build_rich_bottleneck_analysis(bottleneck_data)
    
    # Fallback to correlation-based insights
    return _build_correlation_bottleneck_analysis(corr_data, infra_data)


def _build_rich_bottleneck_analysis(bottleneck_data: Dict) -> str:
    """
    Build bottleneck section from PerfAnalysis identify_bottlenecks output.
    
    Uses the structured bottleneck_analysis.json which includes summary headline,
    threshold concurrency, severity breakdown, baseline metrics, and detailed findings.
    
    Args:
        bottleneck_data: Full bottleneck_analysis.json content
        
    Returns:
        Formatted markdown string
    """
    lines = []
    summary = bottleneck_data.get("summary", {})
    findings = bottleneck_data.get("findings", [])
    baseline = bottleneck_data.get("baseline_metrics", {})
    
    # Headline
    headline = summary.get("headline", "")
    if headline:
        lines.append(f"**{headline}**")
        lines.append("")
    
    # Key metrics summary
    threshold = summary.get("threshold_concurrency")
    max_concurrency = summary.get("max_concurrency_tested")
    optimal_concurrency = summary.get("optimal_concurrency")
    max_throughput = summary.get("max_throughput_rps")
    total_bottlenecks = summary.get("total_bottlenecks", 0)
    
    metrics_lines = []
    if threshold is not None:
        metrics_lines.append(f"- **Degradation Threshold:** {threshold:.0f} virtual users")
    if optimal_concurrency is not None:
        metrics_lines.append(f"- **Optimal Concurrency:** {optimal_concurrency:.0f} virtual users")
    if max_concurrency is not None:
        metrics_lines.append(f"- **Max Concurrency Tested:** {max_concurrency:.0f} virtual users")
    if max_throughput is not None:
        metrics_lines.append(f"- **Peak Throughput:** {max_throughput:.2f} req/sec")
    metrics_lines.append(f"- **Total Bottlenecks Detected:** {total_bottlenecks}")
    
    if metrics_lines:
        lines.extend(metrics_lines)
        lines.append("")
    
    # Baseline metrics
    if baseline:
        baseline_p90 = baseline.get("p90")
        baseline_error = baseline.get("error_rate")
        baseline_throughput = baseline.get("throughput_rps")
        baseline_concurrency = baseline.get("concurrency")
        
        baseline_items = []
        if baseline_concurrency is not None:
            baseline_items.append(f"Concurrency: {baseline_concurrency:.0f}")
        if baseline_p90 is not None:
            baseline_items.append(f"P90: {baseline_p90:.2f} ms")
        if baseline_error is not None:
            baseline_items.append(f"Error Rate: {baseline_error:.2f}%")
        if baseline_throughput is not None:
            baseline_items.append(f"Throughput: {baseline_throughput:.2f} req/sec")
        
        if baseline_items:
            lines.append(f"**Baseline:** {' | '.join(baseline_items)}")
            lines.append("")
    
    # Severity breakdown
    by_severity = summary.get("bottlenecks_by_severity", {})
    severity_parts = []
    for level in ["critical", "high", "medium", "low", "info"]:
        count = by_severity.get(level, 0)
        if count > 0:
            severity_parts.append(f"{level.capitalize()}: {count}")
    
    if severity_parts:
        lines.append(f"**By Severity:** {' | '.join(severity_parts)}")
        lines.append("")
    
    # Bottleneck type breakdown
    by_type = summary.get("bottlenecks_by_type", {})
    type_parts = []
    for btype, count in by_type.items():
        if count > 0:
            label = btype.replace("_", " ").title()
            type_parts.append(f"{label}: {count}")
    
    if type_parts:
        lines.append(f"**By Type:** {' | '.join(type_parts)}")
        lines.append("")
    
    # Detailed findings table (top findings by severity)
    if findings:
        # Sort by severity order: critical > high > medium > low > info
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        sorted_findings = sorted(
            findings,
            key=lambda f: (severity_order.get(f.get("severity", "info"), 4), -abs(f.get("delta_pct", 0)))
        )
        
        lines.append("#### Bottleneck Details")
        lines.append("")
        lines.append("| Severity | Type | Scope | Concurrency | Metric | Value | Baseline | Delta |")
        lines.append("|----------|------|-------|-------------|--------|-------|----------|-------|")
        
        for finding in sorted_findings[:15]:  # Top 15 findings
            severity = finding.get("severity", "info").capitalize()
            btype = finding.get("bottleneck_type", "unknown").replace("_", " ").title()
            scope = finding.get("scope_name", "global")
            # Truncate long scope names for table readability
            if len(scope) > 40:
                scope = scope[:37] + "..."
            concurrency = finding.get("concurrency")
            concurrency_str = f"{concurrency:.0f}" if concurrency is not None else "N/A"
            metric_name = finding.get("metric_name", "")
            # Shorten common metric names for table readability
            metric_display = metric_name.replace("_ms", "").replace("_", " ").replace("response time", "RT")
            metric_value = finding.get("metric_value")
            metric_str = f"{metric_value:.2f}" if metric_value is not None else "N/A"
            baseline_val = finding.get("baseline_value")
            baseline_str = f"{baseline_val:.2f}" if baseline_val is not None else "N/A"
            delta_pct = finding.get("delta_pct")
            delta_str = f"{delta_pct:+.1f}%" if delta_pct is not None else "N/A"
            
            lines.append(
                f"| {severity} | {btype} | {scope} | {concurrency_str} | "
                f"{metric_display} | {metric_str} | {baseline_str} | {delta_str} |"
            )
        
        if len(findings) > 15:
            lines.append("")
            lines.append(f"*Showing top 15 of {len(findings)} findings. See bottleneck_analysis.json for full details.*")
    
    if not lines:
        return "No bottleneck analysis data available."
    
    return "\n".join(lines)


def _build_correlation_bottleneck_analysis(corr_data: Optional[Dict], infra_data: Optional[Dict]) -> str:
    """
    Build bottleneck section from correlation analysis insights (legacy fallback).
    
    Used when bottleneck_analysis.json is not available. Extracts insights
    from correlation_analysis.json.
    
    Args:
        corr_data: Correlation analysis data
        infra_data: Infrastructure analysis data
        
    Returns:
        Formatted markdown string with bullet points
    """
    if not corr_data and not infra_data:
        return "Insufficient data for bottleneck analysis"
    
    analysis = "Based on correlation and infrastructure analysis:\n\n"
    
    if corr_data:
        insights = corr_data.get("insights", "")
        if insights:
            # Handle case where insights is a list
            if isinstance(insights, list):
                for insight in insights:
                    if insight:  # Skip empty strings
                        analysis += f"- {insight}\n"
            # Handle case where insights is a string representation of a list
            elif isinstance(insights, str) and insights.strip().startswith('[') and insights.strip().endswith(']'):
                try:
                    import ast
                    insights_list = ast.literal_eval(insights)
                    if isinstance(insights_list, list):
                        for insight in insights_list:
                            if insight:  # Skip empty strings
                                analysis += f"- {insight}\n"
                except (ValueError, SyntaxError):
                    # Fallback: treat as plain text
                    analysis += insights
            # Handle plain string
            elif isinstance(insights, str) and insights.strip():
                # Check if it already has bullet points
                if insights.strip().startswith('-'):
                    analysis += insights
                else:
                    analysis += f"- {insights}\n"
    
    # If no insights were added, provide a default message
    if analysis == "Based on correlation and infrastructure analysis:\n\n":
        analysis += "- No specific bottlenecks identified from the available data.\n"
    
    return analysis.strip()

def _build_recommendations(perf_data: Optional[Dict], infra_data: Optional[Dict], corr_data: Optional[Dict]) -> str:
    """Generate recommendations based on analysis"""
    recommendations = []
    
    if perf_data:
        overall = perf_data.get("overall_stats", {})
        if overall.get("error_rate", 0) > 1:
            recommendations.append("- Investigate and resolve error causes to improve reliability")

        # Use SLA compliance data instead of a hardcoded threshold.
        # If any APIs are non-compliant, recommend optimization.
        sla = perf_data.get("sla_analysis", {})
        non_compliant = sla.get("non_compliant_apis", 0)
        if non_compliant > 0:
            recommendations.append(
                f"- Optimize response times to meet SLA requirements "
                f"({non_compliant} API(s) exceeded their SLA threshold)"
            )
    
    if not recommendations:
        recommendations.append("- Continue monitoring performance trends")
        recommendations.append("- Maintain current system configuration")
    
    return "\n".join(recommendations)

def _extract_infra_peaks(environment_type: str, infra_data: Dict) -> tuple:
    """
    Extract peak CPU/Memory values from infrastructure data.
    
    Handles None values gracefully (when Kubernetes limits are not defined,
    utilization percentages are None and should be skipped).
    
    Args:
        environment_type: 'host' or 'kubernetes'
        infra_data: Infrastructure analysis data
    
    Returns:
        Tuple of (cpu_peak, cpu_avg, mem_peak, mem_avg, cpu_cores, mem_gb,
                  cpu_peak_cores, cpu_avg_cores)
    """
    cpu_peak = 0.0
    cpu_avg = 0.0
    mem_peak = 0.0
    mem_avg = 0.0
    cpu_cores = 0.0
    mem_gb = 0.0
    # Track CPU core usage values (actual cores, not percentages)
    cpu_peak_cores = 0.0
    cpu_avg_cores = 0.0
    
    detailed = infra_data.get("detailed_metrics", {})
    # Choose platform branch based on environment type
    if environment_type == "kubernetes":
        platform = detailed.get("kubernetes", {})
        entities = platform.get("entities", {})  # Key is "entities" in infrastructure_analysis.json
    else:
        platform = detailed.get("hosts", {})
        entities = platform.get("entities", {})  # Key is "entities" in infrastructure_analysis.json

    for entity_data in entities.values():
        cpu_analysis = entity_data.get("cpu_analysis", {})
        mem_analysis = entity_data.get("memory_analysis", {})
        
        # Handle None values for utilization percentages (limits not defined)
        # Note: PerfAnalysis sets utilization to None when K8s limits are not defined
        peak_cpu_pct = cpu_analysis.get("peak_utilization_pct")
        avg_cpu_pct = cpu_analysis.get("avg_utilization_pct")
        if peak_cpu_pct is not None:
            cpu_peak = max(cpu_peak, peak_cpu_pct)
        if avg_cpu_pct is not None:
            cpu_avg = max(cpu_avg, avg_cpu_pct)
        
        # Extract CPU core values (max across all services) - these are always available
        cpu_peak_cores = max(cpu_peak_cores, cpu_analysis.get("peak_usage_cores", 0) or 0)
        cpu_avg_cores = max(cpu_avg_cores, cpu_analysis.get("avg_usage_cores", 0) or 0)
        
        # Handle None values for memory utilization
        peak_mem_pct = mem_analysis.get("peak_utilization_pct")
        avg_mem_pct = mem_analysis.get("avg_utilization_pct")
        if peak_mem_pct is not None:
            mem_peak = max(mem_peak, peak_mem_pct)
        if avg_mem_pct is not None:
            mem_avg = max(mem_avg, avg_mem_pct)
        
        res_alloc = entity_data.get("resource_allocation", {})
        cpu_cores = max(cpu_cores, _parse_numeric(res_alloc.get("cpus", 0), default=0.0))
        mem_gb = max(mem_gb, res_alloc.get("memory_gb", 0) or 0)
    
    return cpu_peak, cpu_avg, mem_peak, mem_avg, cpu_cores, mem_gb, cpu_peak_cores, cpu_avg_cores

def _set_na_values(context: Dict, keys: List[str]):
    """Set N/A for missing keys"""
    for key in keys:
        context[key] = "N/A"

def _safe_float(value, default=0.0):
    """Safely convert value to float, handling 'N/A' strings"""
    if value == "N/A" or value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _extract_kpi_metadata(
    kpi_data: Optional[Dict],
    kpi_correlations: Optional[Dict],
) -> Dict:
    """
    Extract KPI summary data for the report metadata JSON.

    Returns a dict with scope, services/hosts, per-entity metric highlights,
    category breakdown, insights, and top KPI correlations.  Returns an
    ``available: false`` stub when no KPI data is present so downstream
    consumers always see a consistent schema.
    """
    if not kpi_data:
        return {"available": False}

    # PerfAnalysis currently uses "services" for both k8s and host scopes.
    # Fall back to "hosts" for future-proofing if the schema ever splits them.
    services = kpi_data.get("services") or kpi_data.get("hosts") or {}
    if not services:
        return {"available": False}

    scope = kpi_data.get("scope")
    identifier_type = kpi_data.get("identifier_type")
    categories_found = kpi_data.get("metric_categories_found", [])

    entity_summaries = []
    for entity_name, metrics in services.items():
        metric_list = []
        for metric_name, m in metrics.items():
            metric_list.append({
                "metric": metric_name,
                "category": m.get("category", ""),
                "avg": m.get("avg"),
                "p90": m.get("p90"),
                "p95": m.get("p95"),
                "trend": m.get("trend", ""),
                "display_unit": m.get("display_unit", m.get("unit", "")),
                "samples": m.get("samples", 0),
            })
        entity_summaries.append({
            "entity_name": entity_name,
            "metric_count": len(metric_list),
            "metrics": metric_list,
        })

    kpi_corr_summary = []
    if kpi_correlations:
        pairs = kpi_correlations.get("pairs", [])
        for p in pairs:
            strength = p.get("strength", "")
            if strength in ("strong", "moderate"):
                kpi_corr_summary.append({
                    "kpi_metric": p.get("kpi_metric", ""),
                    "compared_with": p.get("compared_with", ""),
                    "coefficient": p.get("coefficient"),
                    "strength": strength,
                    "direction": p.get("direction", ""),
                })

    return {
        "available": True,
        "scope": scope,
        "identifier_type": identifier_type,
        "categories_found": categories_found,
        "total_services": kpi_data.get("total_services", len(services)),
        "entities": entity_summaries,
        "insights": kpi_data.get("kpi_insights", []),
        "notable_correlations": kpi_corr_summary,
    }


# -----------------------------------------------
# Log Analysis Helper Functions
# -----------------------------------------------
def _build_log_analysis_summary(log_data: Optional[Dict]) -> str:
    """Build log analysis summary section"""
    if not log_data:
        return "No log analysis data available."
    
    summary = log_data.get("summary", {})
    total_issues = summary.get("total_unique_issues", 0)
    total_occurrences = summary.get("total_error_occurrences", 0)
    issues_by_source = summary.get("issues_by_source", {})
    issues_by_severity = summary.get("issues_by_severity", {})
    
    lines = [
        f"**Total Unique Issues:** {total_issues}",
        f"**Total Error Occurrences:** {total_occurrences}",
        "",
        "| Severity | Count |",
        "|----------|-------|"
    ]
    
    for severity in ["Critical", "High", "Medium", "Low"]:
        count = issues_by_severity.get(severity, 0)
        if count > 0:
            lines.append(f"| {severity} | {count} |")
    
    lines.extend([
        "",
        "| Source | Errors |",
        "|--------|--------|"
    ])
    
    for source, count in issues_by_source.items():
        lines.append(f"| {source.upper()} | {count} |")
    
    return "\n".join(lines)


def _build_jmeter_log_analysis(
    log_data: Optional[Dict],
    jmeter_log_analysis_data: Optional[Dict] = None
) -> str:
    """
    Build JMeter-specific log analysis section.
    
    When jmeter_log_analysis_data is available (from JMeter MCP analyze_jmeter_log),
    uses the richer analysis with error categorization, severity breakdown, top affected
    APIs, JTL correlation, and detailed issue table. Falls back to PerfAnalysis
    log_analysis.json when jmeter_log_analysis_data is not available.
    
    Args:
        log_data: Log analysis data from PerfAnalysis analyze_logs
        jmeter_log_analysis_data: BlazeMeter log analysis JSON from JMeter MCP (optional)
        
    Returns:
        Formatted markdown string
    """
    # Prefer rich JMeter log analysis data when available
    if jmeter_log_analysis_data:
        return _build_rich_jmeter_log_analysis(jmeter_log_analysis_data)
    
    # Fallback to PerfAnalysis log_data
    return _build_perfanalysis_jmeter_log_analysis(log_data)


def _build_rich_jmeter_log_analysis(jmeter_log_data: Dict) -> str:
    """
    Build JMeter log analysis section from JMeter MCP analyze_jmeter_log output.
    
    Uses the structured blazemeter_log_analysis.json which includes error categorization,
    severity breakdown, top affected APIs, JTL correlation, and detailed issues.
    
    Args:
        jmeter_log_data: Full blazemeter_log_analysis.json content
        
    Returns:
        Formatted markdown string
    """
    lines = []
    summary = jmeter_log_data.get("summary", {})
    issues = jmeter_log_data.get("issues", [])
    
    total_issues = summary.get("total_unique_issues", 0)
    total_occurrences = summary.get("total_occurrences", 0)
    
    if total_issues == 0:
        return "No JMeter errors detected during test execution."
    
    lines.append(f"**Total Unique Issues:** {total_issues}")
    lines.append(f"**Total Error Occurrences:** {total_occurrences}")
    lines.append("")
    
    # Severity breakdown
    by_severity = summary.get("issues_by_severity", {})
    severity_parts = []
    for level in ["Critical", "High", "Medium"]:
        count = by_severity.get(level, 0)
        if count > 0:
            severity_parts.append(f"{level}: {count}")
    
    if severity_parts:
        lines.append(f"**By Severity:** {' | '.join(severity_parts)}")
        lines.append("")
    
    # Error category breakdown
    by_category = summary.get("issues_by_category", {})
    category_items = [(cat, cnt) for cat, cnt in by_category.items() if cnt > 0]
    if category_items:
        # Sort by count descending
        category_items.sort(key=lambda x: x[1], reverse=True)
        lines.append("#### Error Categories")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|----------|-------|")
        for cat, cnt in category_items:
            lines.append(f"| {cat} | {cnt} |")
        lines.append("")
    
    # Top affected APIs
    top_apis = summary.get("top_affected_apis", [])
    if top_apis:
        lines.append("#### Top Affected APIs")
        lines.append("")
        lines.append("| API Endpoint | Errors | Error Categories |")
        lines.append("|-------------|--------|-----------------|")
        for api_info in top_apis[:10]:
            endpoint = api_info.get("api_endpoint", "Unknown")
            if len(endpoint) > 60:
                endpoint = endpoint[:57] + "..."
            error_count = api_info.get("total_errors", 0)
            categories = ", ".join(api_info.get("error_categories", []))
            lines.append(f"| {endpoint} | {error_count} | {categories} |")
        lines.append("")
    
    # Error timeline
    timeline = summary.get("error_timeline", {})
    first_error = timeline.get("first_error")
    last_error = timeline.get("last_error")
    if first_error and last_error:
        lines.append(f"**Error Timeline:** {first_error} to {last_error}")
        lines.append("")
    
    # JTL correlation summary
    jtl_corr = summary.get("jtl_correlation", {})
    matched = jtl_corr.get("log_errors_matched_to_jtl", 0)
    jtl_only = jtl_corr.get("jtl_only_failures", 0)
    unmatched = jtl_corr.get("unmatched_log_errors", 0)
    if matched or jtl_only or unmatched:
        lines.append("#### JTL Correlation")
        lines.append("")
        lines.append(f"- Errors matched to JTL: {matched}")
        lines.append(f"- JTL-only failures: {jtl_only}")
        lines.append(f"- Unmatched log errors: {unmatched}")
        lines.append("")
    
    # Detailed issues table (top issues by severity)
    if issues:
        lines.append("#### Issue Details")
        lines.append("")
        lines.append("| ID | Severity | Category | Response Code | API Endpoint | Count |")
        lines.append("|----|----------|----------|---------------|-------------|-------|")
        
        for issue in issues[:15]:  # Top 15 issues
            error_id = issue.get("error_id", "")
            severity = issue.get("severity", "Medium")
            category = issue.get("error_category", "Unknown")
            resp_code = issue.get("response_code", "N/A")
            endpoint = issue.get("api_endpoint", "N/A")
            if len(endpoint) > 45:
                endpoint = endpoint[:42] + "..."
            count = issue.get("error_count", 0)
            
            lines.append(
                f"| {error_id} | {severity} | {category} | {resp_code} | "
                f"{endpoint} | {count} |"
            )
        
        if len(issues) > 15:
            lines.append("")
            lines.append(f"*Showing top 15 of {len(issues)} issues. See blazemeter_log_analysis.json for full details.*")
    
    return "\n".join(lines)


def _build_perfanalysis_jmeter_log_analysis(log_data: Optional[Dict]) -> str:
    """
    Build JMeter log analysis section from PerfAnalysis log data (legacy fallback).
    
    Used when blazemeter_log_analysis.json is not available. Extracts JMeter-specific
    errors from PerfAnalysis log_analysis.json.
    
    Args:
        log_data: Log analysis data from PerfAnalysis analyze_logs
        
    Returns:
        Formatted markdown string
    """
    if not log_data:
        return "No JMeter log data available."
    
    summary = log_data.get("summary", {})
    issues_by_source = summary.get("issues_by_source", {})
    jmeter_errors = issues_by_source.get("jmeter", 0)
    
    if jmeter_errors == 0:
        return "No JMeter errors detected during test execution."
    
    # Get top error types
    top_errors = log_data.get("top_error_types", [])
    critical_issues = log_data.get("critical_issues", [])
    
    lines = [
        f"**Total JMeter Errors:** {jmeter_errors}",
        "",
        "#### Top Error Types",
        "",
        "| Error Type | Count |",
        "|------------|-------|"
    ]
    
    for error in top_errors[:5]:  # Top 5 errors
        lines.append(f"| {error.get('error_type', 'Unknown')} | {error.get('count', 0)} |")
    
    # Add critical issues if any from JMeter
    jmeter_critical = [c for c in critical_issues if c.get("source") == "jmeter"]
    if jmeter_critical:
        lines.extend([
            "",
            "#### Critical Issues (JMeter)",
            "",
            "| Error Type | API/Request | Count |",
            "|------------|-------------|-------|"
        ])
        for issue in jmeter_critical:
            lines.append(
                f"| {issue.get('error_type', 'Unknown')} | "
                f"{issue.get('api_request', 'Unknown')[:50]} | "
                f"{issue.get('count', 0)} |"
            )
    
    return "\n".join(lines)


def _build_datadog_log_analysis(log_data: Optional[Dict]) -> str:
    """Build Datadog-specific log analysis section"""
    if not log_data:
        return "No Datadog log data available."
    
    summary = log_data.get("summary", {})
    issues_by_source = summary.get("issues_by_source", {})
    datadog_errors = issues_by_source.get("datadog", 0)
    
    if datadog_errors == 0:
        return "No Datadog log errors captured during test execution."
    
    critical_issues = log_data.get("critical_issues", [])
    
    lines = [
        f"**Total Datadog Log Errors:** {datadog_errors}",
        ""
    ]
    
    # Add Datadog-specific critical issues
    datadog_critical = [c for c in critical_issues if c.get("source") == "datadog"]
    if datadog_critical:
        lines.extend([
            "#### Critical Issues (Datadog Logs)",
            "",
            "| Error Type | Service | Host | Count |",
            "|------------|---------|------|-------|"
        ])
        for issue in datadog_critical:
            lines.append(
                f"| {issue.get('error_type', 'Unknown')} | "
                f"{issue.get('service', 'N/A')} | "
                f"{issue.get('host', 'N/A')} | "
                f"{issue.get('count', 0)} |"
            )
    else:
        lines.append("No critical issues detected in Datadog logs.")
    
    # Add correlation info if available
    correlations = log_data.get("correlations", {})
    infra_corr = correlations.get("infrastructure", {})
    if infra_corr.get("available"):
        hosts_analyzed = infra_corr.get("total_hosts_analyzed", 0)
        lines.extend([
            "",
            f"**Infrastructure Hosts Analyzed:** {hosts_analyzed}"
        ])
    
    return "\n".join(lines)


def _build_apm_trace_analysis(apm_summary: Optional[Dict]) -> str:
    """Build APM trace analysis section"""
    if not apm_summary or not apm_summary.get("available"):
        return "No APM trace data available for this test run."
    
    lines = [
        f"**Total APM Trace Files:** {apm_summary.get('file_count', 0)}",
        f"**Total Error Spans:** {apm_summary.get('total_error_spans', 0)}",
        ""
    ]
    
    # HTTP status breakdown
    http_status_counts = apm_summary.get("http_status_counts", {})
    if http_status_counts:
        lines.extend([
            "#### HTTP Status Distribution",
            "",
            "| Status Code | Count |",
            "|-------------|-------|"
        ])
        for status, count in sorted(http_status_counts.items(), key=lambda x: -x[1]):
            lines.append(f"| {status} | {count} |")
        lines.append("")
    
    # Top error services
    top_services = apm_summary.get("top_services", [])
    if top_services:
        lines.extend([
            "#### Top Services with Errors",
            "",
            "| Service | Error Count |",
            "|---------|-------------|"
        ])
        for svc in top_services[:5]:
            lines.append(f"| {svc.get('service', 'Unknown')} | {svc.get('count', 0)} |")
        lines.append("")
    
    # Top error types
    top_error_types = apm_summary.get("top_error_types", [])
    if top_error_types:
        lines.extend([
            "#### Top Error Types (APM)",
            "",
            "| Error Type | Count |",
            "|------------|-------|"
        ])
        for err in top_error_types[:5]:
            lines.append(f"| {err.get('error_type', 'Unknown')} | {err.get('count', 0)} |")
    
    return "\n".join(lines)


def _parse_numeric(value, default=0.0) -> float:
    """Parse a numeric value that may be a number or a string with units.

    No unit conversions are performed. If the value is a string like
    "4 cores" or "8 GB", the first numeric token is extracted and returned
    as a float (e.g., 4.0, 8.0). Unparseable values return the default.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower()
        # Extract the first numeric token
        m = re.search(r"[-+]?\d*\.?\d+", s)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return default
    return default
