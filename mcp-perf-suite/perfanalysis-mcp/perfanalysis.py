# perfanalysis.py
from fastmcp import FastMCP, Context    # ✅ FastMCP 3.x import
from typing import Optional, List, Dict, Any
import json

from services.performance_analyzer import (
    analyze_blazemeter_results,
    analyze_apm_metrics,
    correlate_performance_data,
    detect_performance_anomalies,
    compare_multiple_runs,
    generate_executive_summary,
    get_current_analysis_status
)
from services.bottleneck_analyzer import analyze_bottlenecks
from services.log_analyzer import analyze_logs as analyze_logs_impl

mcp = FastMCP("perfanalysis")

@mcp.tool()
async def analyze_test_results(test_run_id: str, ctx: Context, sla_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyze BlazeMeter JMeter test results (JTL CSV format)
    
    Args:
        test_run_id: The unique test run identifier
        ctx: FastMCP workflow context for chaining
        sla_id: Optional SLA profile ID from slas.yaml. When provided, per-API
                SLA thresholds are resolved using the three-level pattern matching
                hierarchy. When omitted, the file-level default_sla is used.
        
    Returns:
        Dictionary containing statistical analysis of test results

    Note:
        This must be run BEFORE analyze_environment_metrics and correlate_test_results.
        Required files: artifacts/{test_run_id}/blazemeter/test-results.csv
    """
    return await analyze_blazemeter_results(test_run_id, ctx, sla_id=sla_id)

@mcp.tool()
async def analyze_environment_metrics(test_run_id: str, environment: str, ctx: Context) -> Dict[str, Any]:
    """
    Analyze Datadog infrastructure metrics and logs
    
    Args:
        test_run_id: The unique test run identifier
        environment: The target environment name (pulled from environments.json)
        ctx: FastMCP workflow context for chaining
        
    Returns:
        Dictionary containing infrastructure metrics analysis
    """
    return await analyze_apm_metrics(test_run_id, environment, ctx)

@mcp.tool()
async def correlate_test_results(test_run_id: str, ctx: Context, sla_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Cross-correlate BlazeMeter and APM data to identify relationships
    
    Args:
        test_run_id: The unique test run identifier
        ctx: FastMCP workflow context for chaining
        sla_id: Optional SLA profile ID from slas.yaml. Passed to temporal
                analysis for SLA threshold resolution.
        
    Returns:
        Dictionary containing correlation analysis results
    """
    return await correlate_performance_data(test_run_id, ctx, sla_id=sla_id)

@mcp.tool(tags={"disabled"})
async def detect_anomalies(test_run_id: str, sensitivity: str = "medium", ctx: Context = None) -> Dict[str, Any]:
    """
    Detect statistical anomalies in performance and infrastructure metrics
    
    Args:
        test_run_id: The unique test run identifier
        sensitivity: Detection sensitivity (low/medium/high)
        ctx: FastMCP workflow context for chaining
        
    Returns:
        Dictionary containing detected anomalies
    """
    return await detect_performance_anomalies(test_run_id, sensitivity, ctx)

@mcp.tool()
async def identify_bottlenecks(test_run_id: str, ctx: Context, baseline_run_id: Optional[str] = None, sla_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Identify performance bottlenecks and the concurrency threshold where degradation begins.
    
    Analyzes JTL test results (and optionally Datadog infrastructure metrics) to answer:
    "At what concurrency level does performance begin to degrade, and what is the limiting factor?"
    
    Detects: latency degradation, error rate increases, throughput plateaus,
    infrastructure saturation, resource-performance coupling, and per-endpoint bottlenecks.
    
    Args:
        test_run_id: The unique test run identifier
        ctx: FastMCP workflow context for chaining
        baseline_run_id: Optional previous run ID for comparison analysis
        sla_id: Optional SLA profile ID from slas.yaml. Enables per-endpoint
                SLA resolution for latency breach detection and multi-tier
                bottleneck analysis. When omitted, the file-level default_sla
                is used for all endpoints.
        
    Returns:
        Dictionary containing:
            - status: success or failed
            - summary: headline answer, threshold concurrency, bottleneck counts
            - findings_count: total bottlenecks detected
            - output_files: paths to JSON, CSV, and Markdown reports
    
    Note:
        Required files: artifacts/{test_run_id}/blazemeter/test-results.csv
        Optional files: artifacts/{test_run_id}/datadog/k8s_metrics_*.csv or host_metrics_*.csv
        
        Outputs:
        - artifacts/{test_run_id}/analysis/bottleneck_analysis.json
        - artifacts/{test_run_id}/analysis/bottleneck_analysis.csv
        - artifacts/{test_run_id}/analysis/bottleneck_analysis.md
    """
    return await analyze_bottlenecks(test_run_id, ctx, baseline_run_id, sla_id=sla_id)

@mcp.tool(tags={"disabled"})
async def compare_test_runs(test_run_ids: List[str], comparison_type: str = "performance", ctx: Context = None) -> Dict[str, Any]:
    """
    Compare multiple test runs for trend analysis (max 5 runs)
    
    Args:
        test_run_ids: List of test run identifiers to compare (max 5)
        comparison_type: Type of comparison (performance/infrastructure/both)
        ctx: FastMCP workflow context for chaining
        
    Returns:
        Dictionary containing comparative analysis
    """
    return await compare_multiple_runs(test_run_ids, comparison_type, ctx)

@mcp.tool(tags={"disabled"})
async def summary_analysis(test_run_id: str, include_recommendations: bool = True, ctx: Context = None) -> Dict[str, Any]:
    """
    Generate executive summary with AI-powered insights using OpenAI
    
    Args:
        test_run_id: The unique test run identifier
        include_recommendations: Include AI-generated recommendations
        ctx: FastMCP workflow context for chaining
        
    Returns:
        Dictionary containing executive summary and insights
    """
    return await generate_executive_summary(test_run_id, include_recommendations, ctx)

@mcp.tool()
async def get_analysis_status(test_run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Get the current analysis status for a test run
    
    Args:
        test_run_id: The unique test run identifier
        ctx: FastMCP workflow context for chaining
        
    Returns:
        Dictionary containing analysis completion status
    """
    return await get_current_analysis_status(test_run_id, ctx)

@mcp.tool()
async def analyze_logs(test_run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Analyze log files from load testing and APM tools for errors and performance issues.
    
    Analyzes JMeter/BlazeMeter logs and Datadog APM logs, identifies errors grouped by
    type and API, correlates with existing performance and infrastructure analyses.
    
    Args:
        test_run_id: The unique test run identifier
        ctx: FastMCP workflow context for chaining
    
    Returns:
        Dictionary containing:
            - success: Boolean indicating if analysis completed
            - total_issues: Total number of log issues identified
            - issues_by_source: Breakdown by source (jmeter, datadog)
            - output_files: Paths to CSV, JSON, and Markdown files
            - correlations_summary: Summary of correlations
    
    Note:
        Required files:
        - artifacts/{test_run_id}/blazemeter/jmeter.log (if load_tool is blazemeter)
        - artifacts/{test_run_id}/datadog/logs_*.csv (if apm_tool is datadog)
        
        Outputs:
        - artifacts/{test_run_id}/analysis/log_analysis.csv
        - artifacts/{test_run_id}/analysis/log_analysis.json
        - artifacts/{test_run_id}/analysis/log_analysis.md
    """
    return await analyze_logs_impl(test_run_id, ctx)

# -----------------------------
# Disable tools not yet ready for use (v3: tag-based visibility)
# -----------------------------
mcp.disable(tags={"disabled"})

if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down Performance Analysis MCP…")
