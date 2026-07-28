"""
services/chart_generator.py
Chart generation for performance reports using Matplotlib
"""

import json
import yaml
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime
import numpy as np
import pytz
from fastmcp import Context

# Import config at module level
from utils.config import load_config, load_chart_colors

# Import chart utilities
from utils.chart_utils import (
    load_environment_details,
    get_metric_files
)

# Import chart functions
from services.charts import (
    single_axis_charts,
    dual_axis_charts,
    multi_line_charts,
    comparison_bar_charts,
    stacked_area_charts,
    horizontal_bar_charts
)

# Import comparison chart helpers
from services.comparison_chart_generator import (
    _load_run_metadata,
    _extract_entity_metrics,
    _get_unique_entities,
    _load_all_run_metadata
)

# Load configuration globally
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', '../artifacts'))
CHART_COLORS = load_chart_colors()

# Load chart schema
REPO_ROOT = Path(__file__).parent.parent
CHART_SCHEMA_PATH = REPO_ROOT / "chart_schema.yaml"

with open(CHART_SCHEMA_PATH, 'r') as f:
    CHART_SCHEMA = yaml.safe_load(f)

# Chart defaults
CHART_DEFAULTS = CHART_SCHEMA.get('defaults', {})
CHART_WIDTH = CHART_DEFAULTS['resolution']['width'] / CHART_DEFAULTS['resolution']['dpi']
CHART_HEIGHT = CHART_DEFAULTS['resolution']['height'] / CHART_DEFAULTS['resolution']['dpi']
DPI = CHART_DEFAULTS['resolution']['dpi']

# Chart mapping to functions and data sources
chart_module_registry = {
    "single_axis_charts": single_axis_charts,
    "dual_axis_charts": dual_axis_charts,
    "multi_line_charts": multi_line_charts,
    "comparison_bar_charts": comparison_bar_charts,
    "stacked_area_charts": stacked_area_charts,
    "horizontal_bar_charts": horizontal_bar_charts,
}

chart_map = {
    "CPU_UTILIZATION_LINE": {
        "function": "generate_cpu_utilization_chart",
        "module": "single_axis_charts",
        "data_source": "infrastructure",
    },
    "MEMORY_UTILIZATION_LINE": {
        "function": "generate_memory_utilization_chart",
        "module": "single_axis_charts",
        "data_source": "infrastructure",
    },
    # CPU/Memory raw usage charts (Cores/GB instead of %)
    "CPU_CORES_LINE": {
        "function": "generate_cpu_cores_chart",
        "module": "single_axis_charts",
        "data_source": "infrastructure",
    },
    "MEMORY_USAGE_LINE": {
        "function": "generate_memory_usage_chart",
        "module": "single_axis_charts",
        "data_source": "infrastructure",
    },
    "ERROR_RATE_LINE": {
        "function": "generate_error_rate_chart",
        "module": "single_axis_charts",
        "data_source": "performance",
    },
    "THROUGHPUT_HITS_LINE": {
        "function": "generate_throughput_chart",
        "module": "single_axis_charts",
        "data_source": "performance",
    },
    "RESP_TIME_P90_VUSERS_DUALAXIS": {
        "function": "generate_p90_vusers_chart",
        "module": "dual_axis_charts",
        "data_source": "performance",
    },
    "TOP_SLOWEST_APIS_BAR": {
        "function": "generate_top_slowest_apis_chart",
        "module": "horizontal_bar_charts",
        "data_source": "performance_analysis",
    },
    # Multi-line charts (all hosts/services on single chart)
    "CPU_UTILIZATION_MULTILINE": {
        "function": "generate_cpu_utilization_multiline_chart",
        "module": "multi_line_charts",
        "data_source": "infrastructure_multi",
    },
    "MEMORY_UTILIZATION_MULTILINE": {
        "function": "generate_memory_utilization_multiline_chart",
        "module": "multi_line_charts",
        "data_source": "infrastructure_multi",
    },
    # Stacked area charts (per-service container/pod breakdown, k8s only)
    # Percentage utilization (requires k8s limits to be set)
    "CPU_UTILIZATION_STACKED": {
        "function": "generate_cpu_utilization_stacked_chart",
        "module": "stacked_area_charts",
        "data_source": "infrastructure_stacked",
        "metric_filter": "cpu_util_pct",
    },
    "MEM_UTILIZATION_STACKED": {
        "function": "generate_memory_utilization_stacked_chart",
        "module": "stacked_area_charts",
        "data_source": "infrastructure_stacked",
        "metric_filter": "mem_util_pct",
    },
    # Raw usage (always available, no limits required)
    "CPU_USAGE_STACKED": {
        "function": "generate_cpu_usage_stacked_chart",
        "module": "stacked_area_charts",
        "data_source": "infrastructure_stacked",
        "metric_filter": "kubernetes.cpu.usage.total",
    },
    "MEM_USAGE_STACKED": {
        "function": "generate_memory_usage_stacked_chart",
        "module": "stacked_area_charts",
        "data_source": "infrastructure_stacked",
        "metric_filter": "kubernetes.memory.usage",
    },
    # Infrastructure vs VUsers dual-axis charts
    "CPU_UTILIZATION_VUSERS_DUALAXIS": {
        "function": "generate_cpu_utilization_vusers_chart",
        "module": "dual_axis_charts",
        "data_source": "infrastructure_performance",
        "metric_filter": "cpu_util_pct",
    },
    "MEMORY_UTILIZATION_VUSERS_DUALAXIS": {
        "function": "generate_memory_utilization_vusers_chart",
        "module": "dual_axis_charts",
        "data_source": "infrastructure_performance",
        "metric_filter": "mem_util_pct",
    },
    # Comparison bar charts (for multi-run comparison reports)
    # Peak aggregation - shows maximum observed values (useful for capacity planning)
    "CPU_PEAK_CORE_COMPARISON_BAR": {
        "function": "generate_cpu_core_comparison_bar_chart",
        "module": "comparison_bar_charts",
        "data_source": "comparison_metadata",
    },
    "MEMORY_PEAK_USAGE_COMPARISON_BAR": {
        "function": "generate_memory_usage_comparison_bar_chart",
        "module": "comparison_bar_charts",
        "data_source": "comparison_metadata",
    },
    # Average aggregation - shows mean values (useful for steady-state trend comparison)
    "CPU_AVG_CORE_COMPARISON_BAR": {
        "function": "generate_cpu_core_comparison_bar_chart",
        "module": "comparison_bar_charts",
        "data_source": "comparison_metadata",
    },
    "MEMORY_AVG_USAGE_COMPARISON_BAR": {
        "function": "generate_memory_usage_comparison_bar_chart",
        "module": "comparison_bar_charts",
        "data_source": "comparison_metadata",
    },
    # KPI single-metric line charts (from kpi_metrics_*.csv)
    "GC_GEN2_HEAP_LINE": {
        "function": "generate_kpi_single_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": "gc_size_gen2",
    },
    "GC_MEMORY_LOAD_LINE": {
        "function": "generate_kpi_single_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": "gc_memory_load",
    },
    "SERVER_LATENCY_LINE": {
        "function": "generate_kpi_single_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": "p90_latency",
    },
    "SERVER_THROUGHPUT_LINE": {
        "function": "generate_kpi_single_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": "request_hits",
    },
    # KPI multi-metric line chart (multiple metrics on same axis)
    "HOST_MEMORY_USAGE_LINE": {
        "function": "generate_kpi_multi_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": ["host_mem_usable", "host_mem_total"],
    },
    "HOST_IIS_REQUESTS_MULTILINE": {
        "function": "generate_kpi_multi_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": ["host_iis_method_get", "host_iis_method_post", "host_iis_method_head"],
    },
    "HOST_SQL_CONNECTIONS_LINE": {
        "function": "generate_kpi_single_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": "host_sql_user_connections",
    },
    "GC_HEAP_ALL_MULTILINE": {
        "function": "generate_kpi_multi_metric_chart",
        "module": "single_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": ["gc_size_gen0", "gc_size_gen1", "gc_size_gen2", "gc_size_loh"],
    },
    "HOST_NETWORK_IO_DUALAXIS": {
        "function": "generate_kpi_dual_metric_chart",
        "module": "dual_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": ["host_net_bytes_rcvd", "host_net_bytes_sent"],
    },
    "HOST_IIS_BYTES_IO_DUALAXIS": {
        "function": "generate_kpi_dual_metric_chart",
        "module": "dual_axis_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": ["host_iis_bytes_rcvd", "host_iis_bytes_sent"],
    },
    # KPI stacked area chart (host CPU breakdown)
    "HOST_CPU_BREAKDOWN_STACKED": {
        "function": "generate_host_cpu_breakdown_stacked_chart",
        "module": "stacked_area_charts",
        "data_source": "kpi_timeseries",
        "metric_filter": ["host_cpu_user", "host_cpu_system", "host_cpu_iowait", "host_cpu_stolen", "host_cpu_idle"],
    },
    # KPI + Performance dual-axis chart (KPI latency vs VUsers)
    "KPI_LATENCY_VUSERS_DUALAXIS": {
        "function": "generate_kpi_latency_vusers_chart",
        "module": "dual_axis_charts",
        "data_source": "kpi_performance",
        "metric_filter": "p90_latency",
    },
}

# -----------------------------------------------
# Main Functions for the Chart Generation Module
# -----------------------------------------------

async def generate_chart(run_id: str, env_name: str, chart_id: str) -> dict:
    chart_spec = _get_chart_spec_by_id(chart_id)
    if not chart_spec:
        return {"error": f"Unsupported chart_id: {chart_id}"}

    mapping = chart_map.get(chart_id)
    if not mapping:
        return {"error": f"No handler mapped for chart_id: {chart_id}"}

    chart_handler = get_chart_handler(mapping)
    if not chart_handler:
        return {"error": f"Handler not found: {mapping['module']}.{mapping['function']}"}

    data_source = mapping["data_source"]
    results = []
    errors = []

    # Infrastructure (Datadog) charts
    if data_source == "infrastructure":
        env_info = await load_environment_details(run_id, env_name)
        if not env_info:
            return {"error": f"Missing environment info for: {env_name}"}
        env_type = env_info['env_type']

        resources = env_info["resources"]

        matched_files = await get_metric_files(run_id, env_type, resources)

        matched_resources = {r for r, _ in matched_files}
        for r in resources:
            if r not in matched_resources:
                errors.append({"resource": r, "error": "No metric CSV file found"})

        if not matched_files:
            errors.append({"error": f"No metric files found for environment '{env_name}' resources: {resources}"})
        else:
            for resource, metric_file in matched_files:
                try:
                    df = pd.read_csv(metric_file)
                    out = await chart_handler(df, chart_spec, env_type, resource, run_id)
                    results.append(out)
                except Exception as e:
                    errors.append({"resource": resource, "error": str(e)})

    # Performance (BlazeMeter or JMeter) charts
    elif data_source == "performance":
        perf_path = ARTIFACTS_PATH / run_id / "blazemeter" / "test-results.csv"
        if not perf_path.exists():
            return {"error": f"Missing BlazeMeter test-results.csv for run: {run_id}"}
        try:
            df = pd.read_csv(perf_path)
            out = await chart_handler(df, chart_spec, run_id)
            results.append(out)
        except Exception as e:
            errors.append({"error": str(e)})

    # Infrastructure multi-line charts (all resources on single chart)
    elif data_source == "infrastructure_multi":
        env_info = await load_environment_details(run_id, env_name)
        if not env_info:
            return {"error": f"Missing environment info for: {env_name}"}
        
        env_type = env_info['env_type']
        resources = env_info["resources"]
        matched_files = await get_metric_files(run_id, env_type, resources)
        
        matched_resources = {r for r, _ in matched_files}
        for r in resources:
            if r not in matched_resources:
                errors.append({"resource": r, "error": "No metric CSV file found"})

        # Determine metric filter based on chart_id
        if "CPU" in chart_id:
            metric_filter = "cpu_util_pct"
        elif "MEMORY" in chart_id:
            metric_filter = "mem_util_pct"
        else:
            metric_filter = None
        
        # Build dict of DataFrames for all resources
        dataframes = {}
        resource_column = "hostname" if env_type == "host" else "container_or_pod"
        
        for resource, metric_file in matched_files:
            try:
                df = pd.read_csv(metric_file)
                # Filter for the specific metric type
                if metric_filter and "metric" in df.columns:
                    df = df[df["metric"] == metric_filter]
                # Filter for this specific resource
                if resource_column in df.columns:
                    df = df[df[resource_column] == resource]
                if not df.empty:
                    dataframes[resource] = df
            except Exception as e:
                errors.append({"resource": resource, "error": str(e)})
        
        if dataframes:
            try:
                out = await chart_handler(dataframes, chart_spec, run_id)
                results.append(out)
            except Exception as e:
                errors.append({"error": str(e)})
        else:
            errors.append({"error": "No valid data found for any resource"})

    # Stacked area charts (per-service container/pod breakdown, k8s only)
    elif data_source == "infrastructure_stacked":
        env_info = await load_environment_details(run_id, env_name)
        if not env_info:
            return {"error": f"Missing environment info for: {env_name}"}
        
        env_type = env_info['env_type']
        if env_type != "k8s":
            return {"error": "Stacked area charts are only supported for Kubernetes environments. Host-based environments are not applicable."}
        
        resources = env_info["resources"]
        matched_files = await get_metric_files(run_id, env_type, resources)
        
        matched_resources = {r for r, _ in matched_files}
        for r in resources:
            if r not in matched_resources:
                errors.append({"resource": r, "error": "No metric CSV file found"})

        # Get metric filter from chart mapping (e.g., "cpu_util_pct", "kubernetes.cpu.usage.total")
        metric_filter = mapping.get("metric_filter")
        is_utilization_pct = metric_filter in ("cpu_util_pct", "mem_util_pct")
        
        for resource, metric_file in matched_files:
            try:
                df = pd.read_csv(metric_file)
                
                # Filter for the specific metric type
                if metric_filter and "metric" in df.columns:
                    df = df[df["metric"] == metric_filter]
                
                # For utilization % charts, filter out -1 sentinel values (limits not set)
                if is_utilization_pct and "value" in df.columns:
                    df = df[df["value"] != -1]
                    if df.empty:
                        errors.append({
                            "resource": resource,
                            "error": f"No valid utilization data -- k8s limits are not set for '{resource}'. "
                                     f"Use {'CPU_USAGE_STACKED' if 'cpu' in metric_filter else 'MEM_USAGE_STACKED'} instead."
                        })
                        continue
                
                if df.empty:
                    errors.append({"resource": resource, "error": f"No data found for metric '{metric_filter}'"})
                    continue
                
                # Group by container_or_pod to get all containers within this service
                # (includes main container + sidecars)
                container_dfs = {}
                if "container_or_pod" in df.columns:
                    for container_name, group_df in df.groupby("container_or_pod"):
                        container_dfs[container_name] = group_df
                else:
                    # Fallback: use the resource name as the single container
                    container_dfs[resource] = df
                
                if container_dfs:
                    out = await chart_handler(container_dfs, chart_spec, run_id, resource_name=resource)
                    results.append(out)
                    
            except Exception as e:
                errors.append({"resource": resource, "error": str(e)})
        
        if not results and not errors:
            errors.append({"error": "No valid data found for any resource"})

    # Infrastructure + Performance combined charts (dual-axis: infra metric vs vusers)
    elif data_source == "infrastructure_performance":
        # Load environment info for infrastructure data
        env_info = await load_environment_details(run_id, env_name)
        if not env_info:
            return {"error": f"Missing environment info for: {env_name}"}
        
        env_type = env_info['env_type']
        resources = env_info["resources"]
        matched_files = await get_metric_files(run_id, env_type, resources)
        
        matched_resources = {r for r, _ in matched_files}
        for r in resources:
            if r not in matched_resources:
                errors.append({"resource": r, "error": "No metric CSV file found"})

        # Get metric filter from chart mapping
        metric_filter = mapping.get("metric_filter")
        
        # Build dict of DataFrames for all resources (infrastructure data)
        infra_dataframes = {}
        resource_column = "hostname" if env_type == "host" else "container_or_pod"
        
        for resource, metric_file in matched_files:
            try:
                df = pd.read_csv(metric_file)
                # Filter for the specific metric type
                if metric_filter and "metric" in df.columns:
                    df = df[df["metric"] == metric_filter]
                # Filter for this specific resource
                if resource_column in df.columns:
                    df = df[df[resource_column] == resource]
                if not df.empty:
                    infra_dataframes[resource] = df
            except Exception as e:
                errors.append({"resource": resource, "error": str(e)})
        
        # Load performance data (BlazeMeter test-results.csv)
        perf_path = ARTIFACTS_PATH / run_id / "blazemeter" / "test-results.csv"
        perf_df = None
        if perf_path.exists():
            try:
                perf_df = pd.read_csv(perf_path)
            except Exception as e:
                errors.append({"error": f"Failed to load performance data: {str(e)}"})
        else:
            errors.append({"error": f"Missing BlazeMeter test-results.csv for run: {run_id}"})
        
        # Generate chart if we have both data sources
        if infra_dataframes and perf_df is not None:
            try:
                out = await chart_handler(infra_dataframes, perf_df, chart_spec, run_id)
                results.append(out)
            except Exception as e:
                errors.append({"error": str(e)})
        elif not infra_dataframes:
            errors.append({"error": "No valid infrastructure data found for any resource"})

    # Performance analysis JSON (for API-level charts like TOP_SLOWEST_APIS_BAR)
    elif data_source == "performance_analysis":
        analysis_path = ARTIFACTS_PATH / run_id / "analysis" / "performance_analysis.json"
        if not analysis_path.exists():
            return {"error": f"Missing performance_analysis.json for run: {run_id}"}
        try:
            with open(analysis_path, 'r') as f:
                analysis_data = json.load(f)
            api_data = analysis_data.get("api_analysis", {})
            if not api_data:
                return {"error": "No api_analysis data found in performance_analysis.json"}
            out = await chart_handler(api_data, chart_spec, run_id)
            results.append(out)
        except Exception as e:
            errors.append({"error": str(e)})

    # KPI timeseries charts (from kpi_metrics_*.csv in datadog folder)
    elif data_source == "kpi_timeseries":
        kpi_files = _discover_kpi_csv_files(run_id)
        if not kpi_files:
            return {"error": f"No kpi_metrics_*.csv files found for run: {run_id}"}

        metric_filter = mapping.get("metric_filter")
        is_multi_metric = isinstance(metric_filter, list)

        for entity_name, kpi_path in kpi_files:
            try:
                df = pd.read_csv(kpi_path)
                if "metric" not in df.columns:
                    errors.append({"resource": entity_name, "error": "KPI CSV missing 'metric' column"})
                    continue

                if is_multi_metric:
                    # Multi-metric: build dict of metric_name -> filtered DataFrame
                    metric_dfs = {}
                    for m in metric_filter:
                        mdf = df[df["metric"] == m].copy()
                        if not mdf.empty:
                            metric_dfs[m] = mdf
                    if not metric_dfs:
                        errors.append({"resource": entity_name, "error": f"No data for metrics {metric_filter}"})
                        continue

                    # Stacked area, dual-axis, or multi-metric line dispatch
                    if chart_spec.get("chart_type") == "stacked_area":
                        out = await chart_handler(metric_dfs, chart_spec, run_id, resource_name=entity_name)
                    elif chart_spec.get("chart_type") == "dual_axis_line":
                        out = await chart_handler(metric_dfs, chart_spec, chart_id, run_id, resource_name=entity_name)
                    else:
                        out = await chart_handler(metric_dfs, chart_spec, chart_id, run_id, resource_name=entity_name)
                    results.append(out)
                else:
                    # Single metric filter
                    df_filtered = df[df["metric"] == metric_filter].copy()
                    if df_filtered.empty:
                        errors.append({"resource": entity_name, "error": f"No data for metric '{metric_filter}'"})
                        continue
                    out = await chart_handler(df_filtered, chart_spec, chart_id, run_id, resource_name=entity_name)
                    results.append(out)

            except Exception as e:
                errors.append({"resource": entity_name, "error": str(e)})

        if not results and not errors:
            errors.append({"error": "No valid KPI data found in any kpi_metrics CSV"})

    # KPI + Performance combined charts (KPI metric vs VUsers dual-axis)
    elif data_source == "kpi_performance":
        kpi_files = _discover_kpi_csv_files(run_id)
        if not kpi_files:
            return {"error": f"No kpi_metrics_*.csv files found for run: {run_id}"}

        metric_filter = mapping.get("metric_filter")

        # Load performance data (BlazeMeter test-results.csv)
        perf_path = ARTIFACTS_PATH / run_id / "blazemeter" / "test-results.csv"
        perf_df = None
        if perf_path.exists():
            try:
                perf_df = pd.read_csv(perf_path)
            except Exception as e:
                errors.append({"error": f"Failed to load performance data: {str(e)}"})
        else:
            errors.append({"error": f"Missing BlazeMeter test-results.csv for run: {run_id}"})

        if perf_df is not None:
            for entity_name, kpi_path in kpi_files:
                try:
                    df = pd.read_csv(kpi_path)
                    df_filtered = df[df["metric"] == metric_filter].copy() if "metric" in df.columns else df
                    if df_filtered.empty:
                        errors.append({"resource": entity_name, "error": f"No data for metric '{metric_filter}'"})
                        continue
                    out = await chart_handler(df_filtered, perf_df, chart_spec, run_id, resource_name=entity_name)
                    results.append(out)
                except Exception as e:
                    errors.append({"resource": entity_name, "error": str(e)})

        if not results and not errors:
            errors.append({"error": "No valid KPI+performance data found"})

    else:
        return {"error": f"Unknown data source: {data_source}"}

    return {
        "run_id": run_id,
        "chart_id": chart_id,
        "charts": results,
        "errors": errors,
    }


async def generate_comparison_chart(
    comparison_id: str,
    run_id_list: List[str], 
    chart_id: str, 
    env_name: Optional[str] = None
) -> dict:
    """
    Generate comparison charts from multiple test runs.
    
    This function:
    1. Validates the chart_id is a comparison chart type
    2. Loads report_metadata_{run_id}.json for each run
    3. Extracts infrastructure metrics per entity
    4. Builds run_data list for the chart function
    5. Generates one chart per entity (resource/service)
    6. Saves charts to artifacts/comparisons/{comparison_id}/charts/
    
    Args:
        comparison_id: Unique identifier for this comparison (timestamp format)
        run_id_list: List of test run IDs to compare
        chart_id: Must be one of the comparison chart types:
                  CPU_PEAK_CORE_COMPARISON_BAR, CPU_AVG_CORE_COMPARISON_BAR,
                  MEMORY_PEAK_USAGE_COMPARISON_BAR, MEMORY_AVG_USAGE_COMPARISON_BAR
        env_name: Optional environment filter (not currently used)
        
    Returns:
        dict with comparison_id, run_id_list, chart_id, charts list, and errors
    """
    # Validate chart_id is a comparison chart type
    valid_comparison_charts = [
        "CPU_PEAK_CORE_COMPARISON_BAR",
        "CPU_AVG_CORE_COMPARISON_BAR",
        "MEMORY_PEAK_USAGE_COMPARISON_BAR",
        "MEMORY_AVG_USAGE_COMPARISON_BAR",
    ]
    if chart_id not in valid_comparison_charts:
        return {
            "error": f"Invalid comparison chart_id: {chart_id}. Must be one of: {valid_comparison_charts}",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    # Validate run_id_list
    if len(run_id_list) < 2:
        return {
            "error": "At least 2 test runs are required for comparison charts",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    # Load metadata for all runs
    run_metadata_list, load_errors = _load_all_run_metadata(run_id_list)
    
    if load_errors:
        return {
            "error": f"Failed to load metadata: {'; '.join(load_errors)}",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    # Get chart spec and handler
    chart_spec = _get_chart_spec_by_id(chart_id)
    if not chart_spec:
        return {
            "error": f"Chart specification not found for: {chart_id}",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    mapping = chart_map.get(chart_id)
    if not mapping:
        return {
            "error": f"No handler mapped for chart_id: {chart_id}",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    chart_handler = get_chart_handler(mapping)
    if not chart_handler:
        return {
            "error": f"Handler not found: {mapping['module']}.{mapping['function']}",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    # Get unique entities across all runs
    entity_names = _get_unique_entities(run_metadata_list)
    
    if not entity_names:
        return {
            "error": "No infrastructure entities found in metadata",
            "comparison_id": comparison_id,
            "run_id_list": run_id_list,
            "chart_id": chart_id
        }
    
    # Determine metric type based on chart_id
    if "CPU" in chart_id:
        metric_type = "cpu"
    elif "MEMORY" in chart_id:
        metric_type = "memory"
    else:
        metric_type = "cpu"  # Default
    
    # Generate chart for each entity
    results = []
    errors = []
    
    for entity_name in entity_names:
        try:
            # Extract metrics for this entity across all runs
            run_data = _extract_entity_metrics(run_metadata_list, entity_name, metric_type)
            
            if not run_data:
                errors.append({
                    "entity": entity_name, 
                    "error": "No metrics found for this entity"
                })
                continue
            
            # Generate the chart
            # Strip environment prefix for display name
            from utils.report_utils import strip_service_name_decorations
            display_name = strip_service_name_decorations(entity_name)
            
            result = await chart_handler(
                run_data=run_data,
                resource_name=display_name,
                chart_spec=chart_spec,
                comparison_id=comparison_id
            )
            
            results.append(result)
            
        except Exception as e:
            errors.append({
                "entity": entity_name,
                "error": str(e)
            })
    
    return {
        "comparison_id": comparison_id,
        "run_id_list": run_id_list,
        "chart_id": chart_id,
        "charts": results,
        "errors": errors
    }


# -----------------------------------------------
# Helper Functions
# -----------------------------------------------
def get_chart_handler(mapping):
    module = chart_module_registry.get(mapping["module"])
    if module is None:
        return None
    return getattr(module, mapping["function"], None)

def _parse_datetime_column(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """
    Parse datetime column handling both epoch timestamps and ISO datetime strings.
    Converts to configured timezone for human-readable display.
    
    Args:
        df: DataFrame containing the datetime column
        column: Name of the datetime column to parse
    
    Returns:
        DataFrame with parsed datetime column
    """
    if df.empty or column not in df.columns:
        return df
    
    # Get a sample value to determine format
    sample_val = df[column].iloc[0] if not df.empty else None
    
    try:
        if isinstance(sample_val, (int, float)) or (isinstance(sample_val, str) and sample_val.isdigit()):
            # Numeric timestamps - assume milliseconds
            df[column] = pd.to_datetime(df[column], unit='ms', errors='coerce')
        else:
            # ISO datetime strings
            df[column] = pd.to_datetime(df[column], errors='coerce')
        
        # Convert to configured timezone if available
        try:
            timezone_str = CONFIG.get('perf_report', {}).get('time_zone')
            if timezone_str:
                target_tz = pytz.timezone(timezone_str)
                # Ensure timezone-aware datetime
                if df[column].dt.tz is None:
                    df[column] = df[column].dt.tz_localize('UTC')
                # Convert to target timezone
                df[column] = df[column].dt.tz_convert(target_tz)
        except Exception as tz_error:
            print(f"Warning: Could not convert timezone: {str(tz_error)}")
        
        # Check for any NaT values (parsing failures)
        nat_count = df[column].isna().sum()
        if nat_count > 0:
            print(f"Warning: {nat_count} datetime values failed to parse in column '{column}'")
            
    except Exception as e:
        print(f"Error parsing datetime column '{column}': {str(e)}")
        # Try fallback parsing
        try:
            df[column] = pd.to_datetime(df[column], errors='coerce')
        except Exception as e2:
            print(f"Fallback parsing also failed for column '{column}': {str(e2)}")
    
    return df


def _validate_chart_data(df: pd.DataFrame, chart_spec: Dict) -> Tuple[bool, str]:
    """
    Validate that the data contains required columns and proper formats.
    
    Args:
        df: DataFrame to validate
        chart_spec: Chart specification from schema
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if df is None or df.empty:
        return False, "No data available for chart generation"
    
    # Check required columns
    required_columns = chart_spec.get('data_sources', {}).get('required_columns', [])
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        available_columns = list(df.columns)
        return False, f"Missing required columns: {missing_columns}. Available columns: {available_columns}"
    
    # Check for empty data after filtering
    if len(df) == 0:
        return False, "No data remaining after applying filters"
    
    # Check for numeric columns (y-axis data)
    y_columns = []
    if 'y_axis' in chart_spec:
        y_columns.append(chart_spec['y_axis']['column'])
    if 'y_axis_left' in chart_spec:
        y_columns.append(chart_spec['y_axis_left']['column'])
    if 'y_axis_right' in chart_spec:
        y_columns.append(chart_spec['y_axis_right']['column'])
    
    for y_col in y_columns:
        if y_col in df.columns:
            # Check if column contains numeric data
            if not pd.api.types.is_numeric_dtype(df[y_col]):
                try:
                    df[y_col] = pd.to_numeric(df[y_col], errors='coerce')
                    nan_count = df[y_col].isna().sum()
                    if nan_count > 0:
                        return False, f"Column '{y_col}' contains {nan_count} non-numeric values that could not be converted"
                except Exception as e:
                    return False, f"Column '{y_col}' is not numeric and could not be converted: {str(e)}"
    
    return True, ""


def _discover_kpi_csv_files(run_id: str) -> List[Tuple[str, Path]]:
    """
    Discover KPI metric CSV files for a given test run.

    Scans the APM tool folder (e.g. datadog/) for files matching the
    ``kpi_metrics_[<entity>].csv`` naming convention. The entity name
    is extracted from the filename brackets and can be a k8s service
    name or a hostname.

    Args:
        run_id: Test run identifier.

    Returns:
        List of (entity_name, csv_path) tuples for each discovered file.
    """
    import re
    kpi_dir = ARTIFACTS_PATH / run_id / "datadog"
    if not kpi_dir.exists():
        return []

    pattern = re.compile(r"^kpi_metrics_\[(.+)\]\.csv$")
    found = []
    for csv_file in sorted(kpi_dir.glob("kpi_metrics_*.csv")):
        match = pattern.match(csv_file.name)
        if match:
            entity_name = match.group(1)
            found.append((entity_name, csv_file))
    return found


def _get_chart_spec_by_id(chart_id: str) -> Optional[Dict]:
    """Retrieve chart specification from schema by ID"""
    for chart in CHART_SCHEMA.get('charts', []):
        if chart['id'] == chart_id:
            return chart
    return None


async def _load_chart_data(run_id: str, chart_spec: Dict, chart_data: dict) -> Optional[pd.DataFrame]:
    """
    Load data for chart generation from CSV or JSON.
    Supports both inline data (chart_data dict) or file references.
    """
    # If data is provided inline
    if 'data' in chart_data and isinstance(chart_data['data'], list):
        return pd.DataFrame(chart_data['data'])
    
    # Otherwise, load from file system
    run_path = ARTIFACTS_PATH / run_id
    data_source = chart_spec['data_sources']['primary']
    
    # Handle template variables in data source path
    if '{' in data_source and '}' in data_source:
        # Extract template variables from chart_data or metric_config
        template_vars = {}
        if 'scope' in chart_data:
            template_vars['scope'] = chart_data['scope']
        if 'filter' in chart_data:
            template_vars['filter'] = chart_data['filter']
        if 'pod_or_container' in chart_data:
            template_vars['pod_or_container'] = chart_data['pod_or_container']
        
        # Check for unresolved template variables
        unresolved_vars = []
        for key, value in template_vars.items():
            if value:  # Only replace if value is not None/empty
                data_source = data_source.replace(f'{{{key}}}', value)
            else:
                unresolved_vars.append(key)
        
        # Check if any template variables remain unresolved
        import re
        remaining_vars = re.findall(r'\{(\w+)\}', data_source)
        if remaining_vars:
            print(f"Warning: Unresolved template variables in data source '{data_source}': {remaining_vars}")
            print(f"Available template variables: {list(template_vars.keys())}")
            return None
    
    if data_source.endswith('.csv'):
        # Determine the correct subdirectory based on data source
        if 'blazemeter' in str(run_path) or data_source == 'test-results.csv':
            csv_path = run_path / "blazemeter" / data_source
        elif 'datadog' in str(run_path) or '_metrics_' in data_source:
            csv_path = run_path / "datadog" / data_source
        else:
            csv_path = run_path / "analysis" / data_source
            
        if not csv_path.exists():
            print(f"Warning: CSV file not found: {csv_path}")
            return None
        
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                print(f"Warning: CSV file is empty: {csv_path}")
                return None
        except Exception as e:
            print(f"Error reading CSV file {csv_path}: {str(e)}")
            return None
        
    elif data_source.endswith('.json'):
        json_path = run_path / "analysis" / data_source
        if not json_path.exists():
            return None
        
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Navigate JSON path if specified
        json_path_str = chart_spec['data_sources'].get('json_path', '')
        if json_path_str:
            for key in json_path_str.split('.'):
                if key and key in data:
                    data = data[key]
        
        # Convert to DataFrame
        if isinstance(data, dict):
            # For api_analysis: dict of dicts
            df = pd.DataFrame.from_dict(data, orient='index').reset_index()
            df.rename(columns={'index': 'api_name'}, inplace=True)
        elif isinstance(data, list):
            df = pd.DataFrame(data)
        else:
            return None
    else:
        return None
    
    # Apply metric filter if specified (e.g., only cpu_util_pct rows)
    metric_filter = chart_spec.get('data_sources', {}).get('metric_filter')
    if metric_filter and 'metric' in df.columns:
        original_count = len(df)
        df = df[df['metric'] == metric_filter]
        filtered_count = len(df)
        if df.empty:
            print(f"Warning: No data found for metric filter '{metric_filter}'")
            return None
        print(f"Applied metric filter '{metric_filter}': {original_count} -> {filtered_count} rows")
    
    # Apply filter condition if specified (e.g., only SLA violators)
    filter_cond = chart_spec.get('filter_condition')
    if filter_cond:
        try:
            df = df.query(filter_cond)
        except:
            pass
    
    # Apply limit and sort
    if 'limit' in chart_spec:
        sort_col = chart_spec['x_axis']['column']
        ascending = chart_spec.get('sort', 'descending') == 'ascending'
        df = df.nlargest(chart_spec['limit'], sort_col) if not ascending else df.nsmallest(chart_spec['limit'], sort_col)
    
    return df


async def _load_chart_data_from_spec(run_id: str, chart_spec: Dict) -> Optional[pd.DataFrame]:
    """
    Load data for chart generation based on chart specification.
    This is the template-driven approach that automatically determines data source.
    
    Args:
        run_id: Test run identifier
        chart_spec: Chart specification from schema
    
    Returns:
        DataFrame with chart data
    """
    # Create empty chart_data dict for template-driven approach
    chart_data = {}
    
    # For infrastructure charts, we need to determine scope and service from the data source path
    data_source = chart_spec['data_sources']['primary']
    
    # Handle template variables in data source path
    if '{' in data_source and '}' in data_source:
        # For infrastructure charts, we need to find the actual files and extract template variables
        run_path = ARTIFACTS_PATH / run_id
        
        # Look for matching files in datadog directory
        datadog_path = run_path / "datadog"
        if datadog_path.exists():
            # Find files matching the pattern
            import re
            pattern = data_source.replace('{scope}', r'(\w+)').replace('{filter}', r'([^]]+)')
            pattern = pattern.replace('[', r'\[').replace(']', r'\]')
            
            for file_path in datadog_path.glob("*.csv"):
                match = re.match(pattern, file_path.name)
                if match:
                    scope, filter_value = match.groups()
                    chart_data['scope'] = scope
                    chart_data['filter'] = filter_value
                    break
        
        # If no template variables found, try to load from blazemeter
        if not chart_data and data_source == 'test-results.csv':
            chart_data = {}  # Empty for blazemeter data
    
    # Use the existing _load_chart_data function
    return await _load_chart_data(run_id, chart_spec, chart_data)


async def _load_infrastructure_time_series(run_id: str, chart_spec: Dict) -> Optional[Dict]:
    """
    Load infrastructure time-series data for multiple services.
    Returns dict: {service_name: DataFrame(timestamp, metric_value)}
    """
    run_path = ARTIFACTS_PATH / run_id
    json_path = run_path / "analysis" / chart_spec['data_sources']['primary']
    
    if not json_path.exists():
        return None
    
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Navigate to detailed_metrics
    json_path_str = chart_spec['data_sources'].get('json_path', '')
    for key in json_path_str.split('.'):
        if key and key in data:
            data = data[key]
    
    service_data = {}
    metric_extraction = chart_spec['data_sources'].get('metric_extraction', 'cpu_samples')
    
    # Check both kubernetes and hosts
    for service_type in chart_spec['data_sources'].get('service_types', []):
        if service_type in data:
            for service_name, service_info in data[service_type].items():
                if metric_extraction in service_info:
                    samples = service_info[metric_extraction]
                    if samples:
                        service_data[service_name] = samples
    
    return service_data if service_data else None


def _align_service_timeseries(service_data_dict: Dict, chart_spec: Dict) -> pd.DataFrame:
    """
    Align multiple service time-series to common timeline with 1-minute granularity.
    Returns DataFrame with columns: timestamp, service1, service2, ...
    """
    all_dfs = []
    
    for service_name, samples in service_data_dict.items():
        df = pd.DataFrame(samples)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp').resample('1min').mean().ffill()
        df.rename(columns={'value': service_name}, inplace=True)
        all_dfs.append(df[[service_name]])
    
    if not all_dfs:
        return pd.DataFrame()
    
    # Merge all service DataFrames on timestamp
    merged_df = all_dfs[0]
    for df in all_dfs[1:]:
        merged_df = merged_df.join(df, how='outer')
    
    merged_df = merged_df.ffill().fillna(0).reset_index()
    return merged_df


def _check_precondition(df: pd.DataFrame, chart_spec: Dict) -> bool:
    """
    Check if chart should be generated based on preconditions.
    Example: precondition: "failure > 0"
    """
    precondition = chart_spec.get('precondition')
    if not precondition:
        return True
    
    try:
        # Check if any row meets condition
        return df.eval(precondition).any()
    except:
        return True  # If check fails, generate anyway


async def _save_chart(fig, run_id: str, chart_spec: Dict) -> Path:
    """Save matplotlib figure to PNG file"""
    charts_dir = ARTIFACTS_PATH / run_id / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    
    filename = chart_spec['output_filename'].format(run_id=run_id)
    output_path = charts_dir / filename
    
    fig.savefig(output_path, dpi=DPI, bbox_inches='tight', facecolor='white')
    
    return output_path
