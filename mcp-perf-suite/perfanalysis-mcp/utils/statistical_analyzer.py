# utils/statistical_analyzer.py
import pandas as pd
import numpy as np
import json
import datetime
import math
from fastmcp import Context     # ✅ FastMCP 2.x import
from pathlib import Path
from typing import Dict, List, Any, Optional
from scipy.stats import pearsonr, spearmanr

# Import config at module level (global)
from utils.config import load_config
from utils.sla_config import get_sla_for_api, validate_sla_patterns
from utils.kpi_utils import discover_kpi_files, load_kpi_pivoted
from services.kpi_analyzer import build_kpi_correlation_pairs, compute_kpi_correlations

# Load configuration globally
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))
PFA_CONFIG = CONFIG.get('perf_analysis', {})

# -----------------------------------------------
# BlazeMeter/JMeter statistical analysis functions
# -----------------------------------------------
async def perform_aggregate_analysis(df: pd.DataFrame, test_run_id: str, config: Dict, ctx: Context, sla_id: Optional[str] = None) -> Dict[str, Any]:
    """Perform comprehensive analysis on aggregate report data"""
    
    # Convert numpy types to Python native types for JSON serialization
    def to_native_type(value):
        if pd.isna(value) or (isinstance(value, float) and math.isnan(value)):
            return None
        elif isinstance(value, np.integer):
            return int(value)
        elif isinstance(value, np.floating):
            return float(value)
        else:
            return value
    
    # Get overall summary from "ALL" row
    all_summary = df[df['labelName'] == 'ALL']
    if all_summary.empty:
        await ctx.warning("No 'ALL' summary row found in aggregate data")
        overall_stats = {"error": "No 'ALL' summary found in aggregate data"}
    else:
        all_row = all_summary.iloc[0]
        overall_stats = {
            "total_samples": to_native_type(all_row['samples']),
            "avg_response_time": to_native_type(all_row['avgResponseTime']),
            "min_response_time": to_native_type(all_row['minResponseTime']),
            "max_response_time": to_native_type(all_row['maxResponseTime']),
            "median_response_time": to_native_type(all_row['medianResponseTime']),
            "p90_response_time": to_native_type(all_row['90line']),
            "p95_response_time": to_native_type(all_row['95line']),
            "p99_response_time": to_native_type(all_row['99line']),
            "std_deviation": to_native_type(all_row['stDev']),
            "avg_latency": to_native_type(all_row['avgLatency']),
            "error_count": to_native_type(all_row['errorsCount']),
            "error_rate": to_native_type(all_row['errorsRate']),
            "avg_throughput": to_native_type(all_row['avgThroughput']),
            "test_duration": to_native_type(all_row['duration']),
            "success_rate": to_native_type(100.0 - all_row['errorsRate']) if pd.notna(all_row['errorsRate']) else 100.0
        }
    
    # Analyze individual APIs (exclude "ALL" row)
    individual_apis = df[df['labelName'] != 'ALL']
    api_analysis = {}

    # Map sla_unit to the corresponding aggregate report column name
    sla_unit_column_map = {"P90": "90line", "P95": "95line", "P99": "99line"}

    for _, row in individual_apis.iterrows():
        api_name = row['labelName']
        avg_rt = to_native_type(row['avgResponseTime'])

        # Resolve per-API SLA from slas.yaml
        api_sla = get_sla_for_api(sla_id, api_name)
        sla_threshold = api_sla["response_time_sla_ms"]
        sla_unit = api_sla["sla_unit"]
        sla_column = sla_unit_column_map.get(sla_unit, "90line")

        # Compare the configured percentile against the SLA threshold
        percentile_value = to_native_type(row[sla_column])
        sla_compliant = bool(percentile_value <= sla_threshold) if percentile_value is not None else None

        api_analysis[api_name] = {
            "samples": to_native_type(row['samples']),
            "avg_response_time": avg_rt,
            "min_response_time": to_native_type(row['minResponseTime']),
            "max_response_time": to_native_type(row['maxResponseTime']),
            "median_response_time": to_native_type(row['medianResponseTime']),
            "p90_response_time": to_native_type(row['90line']),
            "p95_response_time": to_native_type(row['95line']),
            "p99_response_time": to_native_type(row['99line']),
            "std_deviation": to_native_type(row['stDev']),
            "error_count": to_native_type(row['errorsCount']),
            "error_rate": to_native_type(row['errorsRate']),
            "success_rate": to_native_type(100.0 - row['errorsRate']) if pd.notna(row['errorsRate']) else 100.0,
            "throughput": to_native_type(row['avgThroughput']),
            "sla_compliant": sla_compliant,
            "sla_threshold_ms": sla_threshold,
            "sla_unit": sla_unit,
            "sla_source": api_sla["source"],
        }

    # SLA Analysis
    sla_analysis = analyze_sla_compliance(individual_apis, sla_id)

    # Validate SLA patterns -- report any api_override patterns that don't
    # match actual test result labels. Only runs when an sla_id is provided
    # (the file-level default_sla has no api_overrides to validate).
    if sla_id:
        actual_labels = individual_apis['labelName'].tolist()
        await validate_sla_patterns(sla_id, actual_labels, ctx)

    # Statistical Analysis
    statistical_summary = {
        "total_apis_analyzed": len(individual_apis),
        "apis_with_errors": int(len(individual_apis[individual_apis['errorsCount'] > 0])),
        "slowest_api": get_slowest_api(individual_apis),
        "fastest_api": get_fastest_api(individual_apis),
        "high_variability_apis": get_high_variability_apis(individual_apis)
    }
    
    return {
        "test_run_id": test_run_id,
        "overall_stats": overall_stats,
        "api_analysis": api_analysis,
        "sla_analysis": sla_analysis,
        "statistical_summary": statistical_summary,
        "analysis_timestamp": datetime.datetime.now().isoformat(),
        "data_quality": {
            "total_labels": len(df),
            "individual_apis": len(individual_apis),
            "has_overall_summary": not all_summary.empty
        }
    }

def analyze_sla_compliance(df: pd.DataFrame, sla_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyze SLA compliance across APIs using per-API SLA resolution.

    Each API is evaluated against its own resolved SLA threshold using the
    percentile metric defined by sla_unit (P90 by default).

    Args:
        df:      DataFrame of individual APIs (excludes the 'ALL' row).
        sla_id:  SLA profile ID for per-API resolution. If None, the
                 file-level default_sla from slas.yaml is used.

    Returns:
        Dict with compliance summary, per-API results, and violations.
    """
    if df.empty:
        return {"error": "No API data to analyze"}

    # Map sla_unit to the corresponding aggregate report column name
    sla_unit_column_map = {"P90": "90line", "P95": "95line", "P99": "99line"}

    # Resolve the profile-level default for summary metadata
    profile_default_sla = get_sla_for_api(sla_id, "")

    compliant_count = 0
    violating_count = 0
    violations = []

    for _, row in df.iterrows():
        api_name = row['labelName']
        api_sla = get_sla_for_api(sla_id, api_name)
        sla_threshold = api_sla["response_time_sla_ms"]
        sla_unit = api_sla["sla_unit"]
        sla_column = sla_unit_column_map.get(sla_unit, "90line")

        percentile_value = float(row[sla_column]) if pd.notna(row[sla_column]) else None

        if percentile_value is not None and percentile_value <= sla_threshold:
            compliant_count += 1
        else:
            violating_count += 1
            if percentile_value is not None:
                violations.append({
                    "api_name": api_name,
                    "percentile_value": percentile_value,
                    "sla_unit": sla_unit,
                    "sla_threshold": sla_threshold,
                    "sla_source": api_sla["source"],
                    "violation_amount": float(percentile_value - sla_threshold),
                    "violation_percentage": float(
                        (percentile_value - sla_threshold) / sla_threshold * 100
                    ),
                })

    total = len(df)
    return {
        "sla_threshold_ms": profile_default_sla["response_time_sla_ms"],
        "sla_unit": profile_default_sla["sla_unit"],
        "total_apis": total,
        "compliant_apis": compliant_count,
        "non_compliant_apis": violating_count,
        "compliance_rate": float(compliant_count / total * 100) if total > 0 else 0.0,
        "violations": violations,
    }

def get_slowest_api(df: pd.DataFrame) -> Optional[Dict]:
    """Get the slowest API by average response time"""
    if df.empty:
        return None
    
    slowest = df.loc[df['avgResponseTime'].idxmax()]
    return {
        "api_name": slowest['labelName'],
        "avg_response_time": float(slowest['avgResponseTime']),
        "samples": int(slowest['samples'])
    }

def get_fastest_api(df: pd.DataFrame) -> Optional[Dict]:
    """Get the fastest API by average response time"""
    if df.empty:
        return None
    
    fastest = df.loc[df['avgResponseTime'].idxmin()]
    return {
        "api_name": fastest['labelName'],
        "avg_response_time": float(fastest['avgResponseTime']),
        "samples": int(fastest['samples'])
    }

def get_high_variability_apis(df: pd.DataFrame, threshold: float = 50.0) -> List[Dict]:
    """Get APIs with high response time variability (high std deviation)"""
    if df.empty:
        return []
    
    high_var_apis = df[df['stDev'] > threshold]
    
    return [
        {
            "api_name": row['labelName'],
            "std_deviation": float(row['stDev']),
            "avg_response_time": float(row['avgResponseTime']),
            "coefficient_of_variation": float(row['stDev'] / row['avgResponseTime']) if row['avgResponseTime'] > 0 else 0
        }
        for _, row in high_var_apis.iterrows()
    ]

# -----------------------------------------------
# Correlation analysis functions
# -----------------------------------------------
def calculate_correlation_matrix(performance_data: Dict, infrastructure_data: Dict, test_run_id: str, config: Dict = None, sla_id: Optional[str] = None) -> Dict:
    """Calculate temporal correlation between performance and infrastructure metrics"""
    
    correlations = {
        "test_run_id": test_run_id,
        "analysis_timestamp": datetime.datetime.now().isoformat(),
        "environment_type": None,
        "correlation_matrix": {},
        "significant_correlations": [],
        "correlation_threshold": 0.3,
        "summary": {},
        "insights": [],
        "temporal_analysis": {}
    }
    
    try:
        # Get configuration
        if config is None:
            config = {}
        
        granularity_window = config.get('perf_analysis', {}).get('correlation_granularity_window', 60)
        resource_thresholds = config.get('perf_analysis', {}).get('resource_thresholds', {})

        # Resolve the SLA threshold from slas.yaml (profile default used for temporal analysis)
        resolved_sla = get_sla_for_api(sla_id, "")
        sla_threshold = resolved_sla["response_time_sla_ms"]
        
        # === ENVIRONMENT TYPE DETECTION ===
        infra_summary = infrastructure_data.get('infrastructure_summary', {})
        k8s_summary = infra_summary.get('kubernetes_summary', {})
        host_summary = infra_summary.get('host_summary', {})
        
        # Check for K8s: support both 'total_entities' and 'total_services' field names
        has_k8s = k8s_summary.get('total_entities', 0) > 0 or k8s_summary.get('total_services', 0) > 0
        has_hosts = host_summary.get('total_hosts', 0) > 0

        if has_k8s and not has_hosts:
            correlations["environment_type"] = "kubernetes"
            return analyze_temporal_kubernetes_correlations(correlations, performance_data, infrastructure_data, 
                                                         granularity_window, sla_threshold, resource_thresholds)
        elif has_hosts and not has_k8s:
            correlations["environment_type"] = "host"
            return analyze_temporal_host_correlations(correlations, performance_data, infrastructure_data,
                                                   granularity_window, sla_threshold, resource_thresholds)
        elif has_k8s and has_hosts:
            correlations["environment_type"] = "hybrid"
            correlations["insights"].append("Warning: Mixed K8s+Host environment detected - correlation may be complex")
            return analyze_hybrid_correlations(correlations, performance_data, infrastructure_data)
        else:
            correlations["environment_type"] = "none"
            correlations["insights"].append("No infrastructure metrics available for correlation")
            return correlations
            
    except Exception as e:
        correlations["error"] = f"Correlation analysis failed: {str(e)}"
        return correlations

def analyze_kubernetes_correlations(correlations: Dict, performance_data: Dict, infrastructure_data: Dict) -> Dict:
    """Analyze correlations for Kubernetes-based environments"""
    
    # Extract performance metrics
    perf_overall = performance_data.get('overall_stats', {})
    perf_apis = performance_data.get('api_analysis', {})
    
    # Extract K8s infrastructure metrics
    infra_detailed = infrastructure_data.get('detailed_metrics', {})
    k8s_data = infra_detailed.get('kubernetes', {})
    k8s_services = k8s_data.get('services', {})
    
    correlations["infrastructure_scope"] = "kubernetes"
    correlations["services_analyzed"] = len(k8s_services)
    
    # Correlate performance with K8s resource utilization
    service_correlations = []
    
    for service_name, service_metrics in k8s_services.items():
        cpu_analysis = service_metrics.get('cpu_analysis', {})
        memory_analysis = service_metrics.get('memory_analysis', {})
        
        # Get resource utilization metrics
        avg_cpu_util = cpu_analysis.get('avg_utilization_pct', 0)
        peak_cpu_util = cpu_analysis.get('peak_utilization_pct', 0)
        avg_memory_util = memory_analysis.get('avg_utilization_pct', 0)
        peak_memory_util = memory_analysis.get('peak_utilization_pct', 0)
        
        # Correlate with overall performance
        overall_response_time = perf_overall.get('avg_response_time', 0)
        overall_p95 = perf_overall.get('p95_response_time', 0)
        
        # Calculate correlations (simplified approach)
        cpu_rt_correlation = calculate_resource_correlation(avg_cpu_util, overall_response_time, "cpu")
        memory_rt_correlation = calculate_resource_correlation(avg_memory_util, overall_response_time, "memory")
        
        service_correlation = {
            "service": service_name,
            "cpu_correlation": cpu_rt_correlation,
            "memory_correlation": memory_rt_correlation,
            "avg_cpu_utilization": avg_cpu_util,
            "peak_cpu_utilization": peak_cpu_util,
            "avg_memory_utilization": avg_memory_util,
            "peak_memory_utilization": peak_memory_util,
            "containers": len(service_metrics.get('containers', {}))
        }
        
        service_correlations.append(service_correlation)
        
        # Check for significant correlations
        check_significant_correlation(correlations, service_correlation, "kubernetes")
    
    correlations["correlation_matrix"]["kubernetes_correlations"] = service_correlations
    correlations["correlation_matrix"]["performance_overview"] = perf_overall
    
    # Generate K8s-specific insights
    correlations["insights"] = generate_kubernetes_insights(service_correlations, perf_overall)
    
    return correlations

def analyze_host_correlations(correlations: Dict, performance_data: Dict, infrastructure_data: Dict) -> Dict:
    """Analyze correlations for Host/VM-based environments"""
    
    # Extract performance metrics
    perf_overall = performance_data.get('overall_stats', {})
    perf_apis = performance_data.get('api_analysis', {})
    
    # Extract Host infrastructure metrics  
    infra_detailed = infrastructure_data.get('detailed_metrics', {})
    host_data = infra_detailed.get('hosts', {})
    host_systems = host_data.get('hosts', {})
    
    correlations["infrastructure_scope"] = "host"
    correlations["hosts_analyzed"] = len(host_systems)
    
    # Correlate performance with Host resource utilization
    host_correlations = []
    
    for host_name, host_metrics in host_systems.items():
        cpu_analysis = host_metrics.get('cpu_analysis', {})
        memory_analysis = host_metrics.get('memory_analysis', {})
        
        # Get resource utilization metrics
        avg_cpu_util = cpu_analysis.get('avg_utilization_pct', 0)
        peak_cpu_util = cpu_analysis.get('peak_utilization_pct', 0)
        avg_memory_util = memory_analysis.get('avg_utilization_pct', 0)
        peak_memory_util = memory_analysis.get('peak_utilization_pct', 0)
        
        # Correlate with overall performance
        overall_response_time = perf_overall.get('avg_response_time', 0)
        overall_p95 = perf_overall.get('p95_response_time', 0)
        
        # Calculate correlations
        cpu_rt_correlation = calculate_resource_correlation(avg_cpu_util, overall_response_time, "cpu")
        memory_rt_correlation = calculate_resource_correlation(avg_memory_util, overall_response_time, "memory")
        
        host_correlation = {
            "host": host_name,
            "cpu_correlation": cpu_rt_correlation,
            "memory_correlation": memory_rt_correlation,
            "avg_cpu_utilization": avg_cpu_util,
            "peak_cpu_utilization": peak_cpu_util,
            "avg_memory_utilization": avg_memory_util,
            "peak_memory_utilization": peak_memory_util,
            "allocated_cpus": cpu_analysis.get('allocated_cpus'),  # None if not defined
            "allocated_memory_gb": memory_analysis.get('allocated_gb')  # None if not defined
        }
        
        host_correlations.append(host_correlation)
        
        # Check for significant correlations
        check_significant_correlation(correlations, host_correlation, "host")
    
    correlations["correlation_matrix"]["host_correlations"] = host_correlations
    correlations["correlation_matrix"]["performance_overview"] = perf_overall
    
    # Generate Host-specific insights
    correlations["insights"] = generate_host_insights(host_correlations, perf_overall)
    
    return correlations

def calculate_resource_correlation(resource_util: float, response_time: float, resource_type: str) -> float:
    """Calculate correlation between resource utilization and performance"""
    # Simplified correlation logic - in reality, you'd use time-series data
    
    if resource_util == 0 or response_time == 0:
        return 0.0
    
    # Basic correlation based on resource utilization levels
    if resource_type == "cpu":
        # High CPU usage typically correlates with slower response times
        if resource_util > 80:
            return 0.7 + (resource_util - 80) / 100  # Strong positive correlation
        elif resource_util > 50:
            return 0.3 + (resource_util - 50) / 100  # Moderate correlation
        else:
            return 0.1  # Weak correlation
    
    elif resource_type == "memory":
        # High memory usage may correlate with slower response times (due to GC, swapping)
        if resource_util > 85:
            return 0.6 + (resource_util - 85) / 75   # Strong positive correlation
        elif resource_util > 60:
            return 0.2 + (resource_util - 60) / 100  # Moderate correlation
        else:
            return 0.05  # Very weak correlation
    
    return 0.0

def check_significant_correlation(correlations: Dict, resource_correlation: Dict, env_type: str):
    """Check if correlations are significant and add to results"""
    threshold = correlations["correlation_threshold"]
    
    cpu_corr = resource_correlation.get("cpu_correlation", 0)
    memory_corr = resource_correlation.get("memory_correlation", 0)
    resource_name = resource_correlation.get("service" if env_type == "kubernetes" else "host", "unknown")
    
    if abs(cpu_corr) >= threshold:
        correlations["significant_correlations"].append({
            "type": "cpu_performance",
            "environment": env_type,
            "resource": resource_name,
            "correlation_coefficient": cpu_corr,
            "strength": "strong" if abs(cpu_corr) > 0.7 else "moderate",
            "direction": "positive" if cpu_corr > 0 else "negative",
            "interpretation": f"CPU utilization {'increases' if cpu_corr > 0 else 'decreases'} with response time"
        })
    
    if abs(memory_corr) >= threshold:
        correlations["significant_correlations"].append({
            "type": "memory_performance",
            "environment": env_type,
            "resource": resource_name,
            "correlation_coefficient": memory_corr,
            "strength": "strong" if abs(memory_corr) > 0.7 else "moderate",
            "direction": "positive" if memory_corr > 0 else "negative",
            "interpretation": f"Memory utilization {'increases' if memory_corr > 0 else 'decreases'} with response time"
        })

def generate_kubernetes_insights(service_correlations: List[Dict], performance_overview: Dict) -> List[str]:
    """Generate Kubernetes-specific insights"""
    insights = []
    
    total_services = len(service_correlations)
    insights.append(f"Analyzed {total_services} Kubernetes service(s) for performance correlation")
    
    # CPU insights
    high_cpu_services = [s for s in service_correlations if s["avg_cpu_utilization"] > 50]
    if high_cpu_services:
        insights.append(f"{len(high_cpu_services)} service(s) showing elevated CPU usage (>50%)")
    
    # Memory insights
    high_memory_services = [s for s in service_correlations if s["avg_memory_utilization"] > 60]
    if high_memory_services:
        insights.append(f"{len(high_memory_services)} service(s) showing elevated memory usage (>60%)")
    
    # Container density insights
    total_containers = sum(s.get("containers", 0) for s in service_correlations)
    insights.append(f"Total containers analyzed: {total_containers}")
    
    return insights

def generate_host_insights(host_correlations: List[Dict], performance_overview: Dict) -> List[str]:
    """Generate Host/VM-specific insights"""
    insights = []
    
    total_hosts = len(host_correlations)
    insights.append(f"Analyzed {total_hosts} host system(s) for performance correlation")
    
    # CPU insights
    high_cpu_hosts = [h for h in host_correlations if h["avg_cpu_utilization"] > 60]
    if high_cpu_hosts:
        insights.append(f"{len(high_cpu_hosts)} host(s) showing elevated CPU usage (>60%)")
    
    # Memory insights
    high_memory_hosts = [h for h in host_correlations if h["avg_memory_utilization"] > 70]
    if high_memory_hosts:
        insights.append(f"{len(high_memory_hosts)} host(s) showing elevated memory usage (>70%)")
    
    return insights

def analyze_hybrid_correlations(correlations: Dict, performance_data: Dict, infrastructure_data: Dict) -> Dict:
    """Analyze correlations for hybrid environments (both K8s and Host)"""
    # For simplicity, just note that hybrid analysis is complex
    correlations["insights"].append("Hybrid environment detected - detailed correlation analysis not implemented")
    return correlations

# -----------------------------------------------
# Temporal correlation analysis functions
# -----------------------------------------------
def analyze_temporal_kubernetes_correlations(correlations: Dict, performance_data: Dict, infrastructure_data: Dict,
                                           granularity_window: int, sla_threshold: float, resource_thresholds: Dict) -> Dict:
    """Analyze temporal correlations for Kubernetes environments"""
    
    try:
        # Load raw data files for temporal analysis
        test_run_id = correlations["test_run_id"]
        artifacts_path = ARTIFACTS_PATH / test_run_id

        # Load performance data (check blazemeter/ first, fall back to jmeter/)
        blazemeter_file = artifacts_path / "blazemeter" / "test-results.csv"
        if not blazemeter_file.exists():
            blazemeter_file = artifacts_path / "jmeter" / "test-results.csv"
        if not blazemeter_file.exists():
            if not artifacts_path.exists():
                correlations["error"] = f"Artifacts directory not found: {artifacts_path} (cwd: {Path.cwd()})"
            else:
                correlations["error"] = f"Test results CSV not found (checked blazemeter/ and jmeter/)"
            return correlations
        
        # Load Datadog infrastructure data: only host/k8s metrics CSVs
        datadog_dir = artifacts_path / "datadog"
        datadog_files = []
        for prefix in ("host", "k8s"):
            datadog_files.extend(datadog_dir.glob(f"{prefix}_metrics_*.csv"))
        datadog_files = list(sorted(datadog_files))
        if not datadog_files:
            correlations["error"] = f"Datadog data files not found in {artifacts_path / 'datadog'}"
            return correlations

        # Discover KPI timeseries files (optional)
        kpi_files = discover_kpi_files(datadog_dir)

        # Process the data
        perf_df = load_and_process_performance_data(blazemeter_file, sla_threshold=sla_threshold)
        infra_df = load_and_process_infrastructure_data(datadog_files, granularity_window)
        
        if perf_df is None or infra_df is None:
            correlations["error"] = "Failed to load or process performance/infrastructure data"
            return correlations

        # Get service identifiers
        services = infra_df['identifier'].unique()
        num_services = len(services)
        correlations["insights"].append(f"Analyzed {num_services} service(s) for performance correlation")
        # Add kubernetes-specific context
        correlations["infrastructure_scope"] = "kubernetes"
        correlations["services_analyzed"] = num_services

        # Perform temporal correlation analysis
        temporal_results = perform_temporal_correlation_analysis(
            perf_df,
            infra_df,
            granularity_window,
            sla_threshold,
            resource_thresholds,
            environment_type="k8s",
            kpi_files=kpi_files,
        )
        
        # Update correlations with temporal results
        correlations["temporal_analysis"] = temporal_results
        correlations["correlation_matrix"] = temporal_results.get("correlation_matrix", {})
        correlations["significant_correlations"] = temporal_results.get("significant_correlations", [])
        correlations["insights"].extend(temporal_results.get("insights", []))
        
        # KPI correlations (additive section)
        if "kpi_correlations" in temporal_results:
            correlations["kpi_correlations"] = temporal_results["kpi_correlations"]
        
        # Generate summary
        correlations["summary"] = generate_temporal_correlation_summary(temporal_results)
        
        return correlations
        
    except Exception as e:
        correlations["error"] = f"Temporal Kubernetes correlation analysis failed: {str(e)}"
        return correlations

def analyze_temporal_host_correlations(correlations: Dict, performance_data: Dict, infrastructure_data: Dict,
                                     granularity_window: int, sla_threshold: float, resource_thresholds: Dict) -> Dict:
    """
    Analyze temporal correlations for Host-based environments
    """

    try:
        # Load raw data files for temporal analysis
        test_run_id = correlations["test_run_id"]
        artifacts_path = ARTIFACTS_PATH / test_run_id
        
        # Load performance data (check blazemeter/ first, fall back to jmeter/)
        blazemeter_file = artifacts_path / "blazemeter" / "test-results.csv"
        if not blazemeter_file.exists():
            blazemeter_file = artifacts_path / "jmeter" / "test-results.csv"
        if not blazemeter_file.exists():
            if not artifacts_path.exists():
                correlations["error"] = f"Artifacts directory not found: {artifacts_path} (cwd: {Path.cwd()})"
            else:
                correlations["error"] = f"Test results CSV not found (checked blazemeter/ and jmeter/)"
            return correlations
        
        # Load Datadog infrastructure data: only host/k8s metrics CSVs
        datadog_dir = artifacts_path / "datadog"
        datadog_files = []
        for prefix in ("host", "k8s"):
            datadog_files.extend(datadog_dir.glob(f"{prefix}_metrics_*.csv"))
        datadog_files = list(sorted(datadog_files))
        if not datadog_files:
            correlations["error"] = f"Datadog data files not found in {artifacts_path / 'datadog'}"
            return correlations    

        # Discover KPI timeseries files (optional)
        kpi_files = discover_kpi_files(datadog_dir)

        # Process the data
        perf_df = load_and_process_performance_data(blazemeter_file, sla_threshold=sla_threshold)
        infra_df = load_and_process_infrastructure_data(datadog_files, granularity_window)

        if perf_df is None or infra_df is None:
            correlations["error"] = "Failed to load or process performance/infrastructure data"
            return correlations

        # Get host identifiers
        hosts = infra_df['identifier'].unique()
        num_hosts = len(hosts)
        correlations["insights"].append(f"Analyzed {num_hosts} host(s) for performance correlation")
        # Add host-specific context
        correlations["infrastructure_scope"] = "host"
        correlations["hosts_analyzed"] = num_hosts

        # Perform temporal correlation analysis
        temporal_results = perform_temporal_correlation_analysis(
            perf_df,
            infra_df,
            granularity_window,
            sla_threshold,
            resource_thresholds,
            environment_type="host",
            kpi_files=kpi_files,
        )

        # Update correlations with temporal results
        correlations["temporal_analysis"] = temporal_results
        correlations["correlation_matrix"] = temporal_results.get("correlation_matrix", {})
        correlations["significant_correlations"] = temporal_results.get("significant_correlations", [])
        correlations["insights"].extend(temporal_results.get("insights", []))
        
        # KPI correlations (additive section)
        if "kpi_correlations" in temporal_results:
            correlations["kpi_correlations"] = temporal_results["kpi_correlations"]
        
        # Generate summary
        correlations["summary"] = generate_temporal_correlation_summary(temporal_results)
        
        return correlations
        
    except Exception as e:
        correlations["error"] = f"Temporal Host correlation analysis failed: {str(e)}"
        return correlations

def load_and_process_performance_data(file_path: Path, sla_threshold: float = None) -> Optional[pd.DataFrame]:
    """
    Load and process BlazeMeter performance data for temporal analysis.

    Args:
        file_path:      Path to the test-results.csv (JTL) file.
        sla_threshold:  SLA threshold in milliseconds from slas.yaml.
                        Used to flag individual requests as SLA violations.

    Returns:
        DataFrame with columns: timestamp, elapsed, label, success, sla_violation.
        Returns None on error.

    Raises:
        ValueError: If sla_threshold is not provided.
    """
    if sla_threshold is None:
        raise ValueError(
            "sla_threshold is required for load_and_process_performance_data(). "
            "This value must come from slas.yaml — no hardcoded defaults are permitted."
        )

    try:
        use_cols = ["timeStamp", "elapsed", "label", "success"]
        dtype_map = {
            "timeStamp": "int64",
            "elapsed": "int64",
            "label": "category",
            "success": "str",
        }

        df = pd.read_csv(file_path, usecols=use_cols, dtype=dtype_map, low_memory=True)

        df['timestamp'] = pd.to_datetime(df['timeStamp'], unit='ms', utc=True)
        df = df[['timestamp', 'elapsed', 'label', 'success']].copy()
        df['sla_violation'] = df['elapsed'] > sla_threshold

        return df

    except Exception as e:
        print(f"Error loading performance data: {e}")
        return None

def load_and_process_infrastructure_data(infra_csv_files, granularity_window):
    """
    Unified loader for both K8s and Host infrastructure metrics
    Works with identical CSV schema from Datadog MCP
    
    Returns DataFrame with columns: timestamp, cpu_util_pct, mem_util_pct, identifier
    Where identifier is either filter (k8s) or hostname (host)
    """
    
    all_infra_data = []

    for csv_file in infra_csv_files:
        try:
            df = pd.read_csv(csv_file)
            
            # Filter for only the utilization percentage metrics
            cpu_df = df[df['metric'] == 'cpu_util_pct'].copy()
            mem_df = df[df['metric'] == 'mem_util_pct'].copy()
            
            if cpu_df.empty and mem_df.empty:
                continue
            
            # Detect environment type from 'scope' column
            if not df.empty:
                scope = df['scope'].iloc[0]
                env_type = 'k8s' if scope == 'k8s' else 'host'
            else:
                continue
            
            # Process CPU data
            if not cpu_df.empty:
                cpu_processed = cpu_df[['timestamp_utc', 'value']].copy()
                cpu_processed.columns = ['timestamp', 'cpu_util_pct']
                cpu_processed['timestamp'] = pd.to_datetime(cpu_processed['timestamp'], utc=True)
                
                # Add identifier based on environment type
                if env_type == 'k8s':
                    cpu_processed['identifier'] = cpu_df['filter'].values
                else:  # host
                    cpu_processed['identifier'] = cpu_df['hostname'].values
                
                all_infra_data.append(cpu_processed)
            
            # Process Memory data
            if not mem_df.empty:
                mem_processed = mem_df[['timestamp_utc', 'value']].copy()
                mem_processed.columns = ['timestamp', 'mem_util_pct']
                mem_processed['timestamp'] = pd.to_datetime(mem_processed['timestamp'], utc=True)
                
                # Add identifier based on environment type
                if env_type == 'k8s':
                    mem_processed['identifier'] = mem_df['filter'].values
                else:  # host
                    mem_processed['identifier'] = mem_df['hostname'].values
                
                all_infra_data.append(mem_processed)
                
        except Exception as e:
            print(f"Error processing infrastructure CSV {csv_file}: {e}")
            continue
    
    if not all_infra_data:
        return pd.DataFrame()
    
    # Combine all data
    combined_df = pd.concat(all_infra_data, ignore_index=True)
    
    # Merge CPU and Memory data on timestamp and identifier
    has_cpu = 'cpu_util_pct' in combined_df.columns
    has_mem = 'mem_util_pct' in combined_df.columns

    cpu_data = (
        combined_df[combined_df['cpu_util_pct'].notna()][['timestamp', 'identifier', 'cpu_util_pct']]
        if has_cpu else pd.DataFrame(columns=['timestamp', 'identifier', 'cpu_util_pct'])
    )
    mem_data = (
        combined_df[combined_df['mem_util_pct'].notna()][['timestamp', 'identifier', 'mem_util_pct']]
        if has_mem else pd.DataFrame(columns=['timestamp', 'identifier', 'mem_util_pct'])
    )
    
    # Merge on timestamp and identifier
    merged_df = pd.merge(
        cpu_data, 
        mem_data, 
        on=['timestamp', 'identifier'], 
        how='outer'
    )
    
    # Sort by timestamp
    merged_df = merged_df.sort_values('timestamp').reset_index(drop=True)
    
    # Create time windows
    if not merged_df.empty:
        merged_df['time_window'] = merged_df['timestamp'].dt.floor(f'{granularity_window}min')
    
    return merged_df

def perform_temporal_correlation_analysis(perf_df: pd.DataFrame, infra_df: pd.DataFrame, 
                                        granularity_window: int, sla_threshold: float, 
                                        resource_thresholds: Dict,
                                        environment_type: str = "k8s",
                                        kpi_files: Optional[List[Path]] = None) -> Dict:
    """Perform temporal correlation analysis - OPTIMIZED with pandas vectorization"""
    
    results = {
        "analysis_periods": [],
        "correlation_matrix": {},
        "significant_correlations": [],
        "insights": []
    }
    
    try:
        # Get resource thresholds
        cpu_high_threshold = resource_thresholds.get('cpu', {}).get('high', 80)
        memory_high_threshold = resource_thresholds.get('memory', {}).get('high', 85)
        
        # Align time boundaries
        start_time = max(perf_df['timestamp'].min(), infra_df['timestamp'].min())
        end_time = min(perf_df['timestamp'].max(), infra_df['timestamp'].max())
        
        # Filter to common time range
        perf_df = perf_df[(perf_df['timestamp'] >= start_time) & (perf_df['timestamp'] <= end_time)].copy()
        infra_df = infra_df[(infra_df['timestamp'] >= start_time) & (infra_df['timestamp'] <= end_time)].copy()
        
        if perf_df.empty or infra_df.empty:
            results["error"] = "No overlapping time range between performance and infrastructure data"
            return results
        
        # === FAST PANDAS APPROACH: Resample both to same granularity ===
        # Set timestamp as index for resampling
        perf_indexed = perf_df.set_index('timestamp')
        infra_indexed = infra_df.set_index('timestamp')
        
        # Resample performance data to time windows (vectorized aggregation)
        perf_resampled = perf_indexed.resample(f'{granularity_window}s').agg({
            'elapsed': ['mean', 'max', 'count', lambda x: x.quantile(0.90) if len(x) else np.nan],
            'sla_violation': 'sum'
        })
        perf_resampled.columns = ['avg_response_time', 'max_response_time', 'request_count', 'p90_response_time', 'sla_violations']
        
        # Resample infrastructure data to same windows (vectorized aggregation)
        infra_resampled = infra_indexed.resample(f'{granularity_window}s').mean(numeric_only=True)
        
        # Merge on timestamp index (single operation)
        merged = pd.merge(
            perf_resampled, 
            infra_resampled, 
            left_index=True, 
            right_index=True, 
            how='inner'
        )
        
        # Drop windows with no data
        merged = merged[merged['request_count'] > 0].copy()
        
        if merged.empty:
            results["error"] = "No data after resampling and merging"
            return results
        
        # Add flags for constraints (vectorized boolean operations)
        merged['cpu_constraint'] = merged['cpu_util_pct'] > cpu_high_threshold if 'cpu_util_pct' in merged.columns else False
        merged['memory_constraint'] = merged['mem_util_pct'] > memory_high_threshold if 'mem_util_pct' in merged.columns else False
        merged['performance_degradation'] = merged['avg_response_time'] > sla_threshold
        
        # Filter to interesting periods (performance issues OR high resource usage)
        interesting = merged[
            (merged['sla_violations'] > 0) | 
            (merged['cpu_constraint']) | 
            (merged['memory_constraint']) |
            (merged['performance_degradation'])
        ].copy()
        
        # Build analysis periods from interesting windows with API breakdown
        for timestamp, row in interesting.iterrows():
            # Get API breakdown for this time window
            window_start = timestamp
            window_end = timestamp + pd.Timedelta(seconds=granularity_window)
            
            window_apis = perf_indexed[
                (perf_indexed.index >= window_start) & 
                (perf_indexed.index < window_end)
            ].copy()
            
            # Calculate per-API stats for this window
            api_breakdown = []
            if not window_apis.empty and 'label' in window_apis.columns:
                api_stats = window_apis.groupby('label').agg({
                    'elapsed': ['mean', 'max', 'count'],
                    'sla_violation': 'sum'
                }).reset_index()
                
                api_stats.columns = ['api_name', 'avg_response_time', 'max_response_time', 'request_count', 'sla_violations']
                
                # Sort by average response time descending
                api_stats = api_stats.sort_values('avg_response_time', ascending=False)
                
                # Convert to list of dicts
                for _, api_row in api_stats.iterrows():
                    api_breakdown.append({
                        "api_name": str(api_row['api_name']),
                        "avg_response_time": float(api_row['avg_response_time']),
                        "max_response_time": float(api_row['max_response_time']),
                        "request_count": int(api_row['request_count']),
                        "sla_violations": int(api_row['sla_violations'])
                    })
            
            results["analysis_periods"].append({
                "time_window": timestamp.isoformat(),
                "performance_issues": {
                    "avg_response_time": float(row['avg_response_time']),
                    "max_response_time": float(row['max_response_time']),
                    "sla_violations": int(row['sla_violations']),
                    "total_requests": int(row['request_count']),
                    "sla_violation_rate": float((row['sla_violations'] / row['request_count']) * 100) if row['request_count'] > 0 else 0
                },
                "infrastructure_metrics": {
                    "avg_cpu": float(row['cpu_util_pct']) if 'cpu_util_pct' in row.index else None,
                    "avg_memory": float(row['mem_util_pct']) if 'mem_util_pct' in row.index else None
                },
                "correlation_analysis": {
                    "cpu_constraint": bool(row['cpu_constraint']),
                    "memory_constraint": bool(row['memory_constraint']),
                    "performance_degradation": bool(row['performance_degradation'])
                },
                "api_breakdown": api_breakdown
            })
        
        # === CALCULATE CORRELATIONS (vectorized) ===
        # Use all windows (not just interesting ones) for correlation
        has_cpu_col = 'cpu_util_pct' in merged.columns
        has_mem_col = 'mem_util_pct' in merged.columns
        cpu_rt_corr = merged['cpu_util_pct'].corr(merged['avg_response_time']) if has_cpu_col else float('nan')
        memory_rt_corr = merged['mem_util_pct'].corr(merged['avg_response_time']) if has_mem_col else float('nan')
        cpu_sla_corr = merged['cpu_util_pct'].corr(merged['sla_violations']) if has_cpu_col else float('nan')
        memory_sla_corr = merged['mem_util_pct'].corr(merged['sla_violations']) if has_mem_col else float('nan')
        
        # Handle NaN correlations (happens when all values are constant)
        cpu_rt_corr = 0.0 if pd.isna(cpu_rt_corr) else float(cpu_rt_corr)
        memory_rt_corr = 0.0 if pd.isna(memory_rt_corr) else float(memory_rt_corr)
        cpu_sla_corr = 0.0 if pd.isna(cpu_sla_corr) else float(cpu_sla_corr)
        memory_sla_corr = 0.0 if pd.isna(memory_sla_corr) else float(memory_sla_corr)
        
        results["correlation_matrix"] = {
            "cpu_response_time_correlation": cpu_rt_corr,
            "memory_response_time_correlation": memory_rt_corr,
            "cpu_sla_violations_correlation": cpu_sla_corr,
            "memory_sla_violations_correlation": memory_sla_corr,
            "analysis_windows": len(merged),
            "interesting_periods": len(interesting),
            "total_correlation_samples": len(merged)
        }
        
        # Identify significant correlations
        threshold = 0.3
        if abs(cpu_rt_corr) >= threshold:
            results["significant_correlations"].append({
                "type": "cpu_response_time",
                "correlation_coefficient": cpu_rt_corr,
                "strength": "strong" if abs(cpu_rt_corr) > 0.7 else "moderate",
                "direction": "positive" if cpu_rt_corr > 0 else "negative",
                "interpretation": f"CPU utilization {'increases' if cpu_rt_corr > 0 else 'decreases'} with response time"
            })
        
        if abs(memory_rt_corr) >= threshold:
            results["significant_correlations"].append({
                "type": "memory_response_time", 
                "correlation_coefficient": memory_rt_corr,
                "strength": "strong" if abs(memory_rt_corr) > 0.7 else "moderate",
                "direction": "positive" if memory_rt_corr > 0 else "negative",
                "interpretation": f"Memory utilization {'increases' if memory_rt_corr > 0 else 'decreases'} with response time"
            })
        
        # CPU-SLA Violations correlation
        if abs(cpu_sla_corr) >= threshold:
            results["significant_correlations"].append({
                "type": "cpu_sla_violations",
                "correlation_coefficient": cpu_sla_corr,
                "strength": "strong" if abs(cpu_sla_corr) > 0.7 else "moderate",
                "direction": "positive" if cpu_sla_corr > 0 else "negative",
                "interpretation": f"Host CPU utilization correlates with SLA violations"
            })
        
        # Memory-SLA Violations correlation
        if abs(memory_sla_corr) >= threshold:
            results["significant_correlations"].append({
                "type": "memory_sla_violations",
                "correlation_coefficient": memory_sla_corr,
                "strength": "strong" if abs(memory_sla_corr) > 0.7 else "moderate",
                "direction": "positive" if memory_sla_corr > 0 else "negative",
                "interpretation": f"Host memory utilization correlates with SLA violations"
            })

        # Generate insights
        results["insights"].extend(
            generate_temporal_insights(
                results,
                results.get("significant_correlations", []),
                environment_type,
            )
        )
        
        # === KPI CORRELATION ANALYSIS (optional, additive) ===
        if kpi_files:
            kpi_pivoted = load_kpi_pivoted(kpi_files, convert_units=True)
            if kpi_pivoted is not None and not kpi_pivoted.empty:
                kpi_indexed = kpi_pivoted.set_index(
                    pd.to_datetime(kpi_pivoted["timestamp"], utc=True)
                )
                kpi_numeric = kpi_indexed.select_dtypes(include="number")
                kpi_resampled = kpi_numeric.resample(f"{granularity_window}s").mean(numeric_only=True)

                kpi_merged = pd.merge(
                    merged, kpi_resampled,
                    left_index=True, right_index=True, how="left",
                )

                kpi_columns = [c for c in kpi_resampled.columns if c in kpi_merged.columns]
                if kpi_columns:
                    kpi_pairs = build_kpi_correlation_pairs(
                        kpi_columns, list(kpi_merged.columns)
                    )
                    kpi_corr_results = compute_kpi_correlations(kpi_merged, kpi_pairs)
                    results["kpi_correlations"] = kpi_corr_results
                    results["insights"].extend(kpi_corr_results.get("kpi_insights", []))
        
        return results
        
    except Exception as e:
        results["error"] = f"Temporal correlation analysis failed: {str(e)}"
        import traceback
        results["error_details"] = traceback.format_exc()
        return results

def analyze_time_window(window_perf: pd.DataFrame, window_infra: pd.DataFrame, 
                       window_start: pd.Timestamp, window_end: pd.Timestamp,
                       sla_threshold: float, cpu_high_threshold: float, 
                       memory_high_threshold: float) -> Optional[Dict]:
    """Analyze a single time window for performance and infrastructure correlation"""
    
    try:
        # Performance analysis
        perf_issues = {
            "avg_response_time": window_perf['elapsed'].mean(),
            "max_response_time": window_perf['elapsed'].max(),
            "sla_violations": window_perf['sla_violation'].sum(),
            "total_requests": len(window_perf),
            "sla_violation_rate": window_perf['sla_violation'].mean() * 100
        }
        
        # Infrastructure analysis
        infra_metrics = {
            "avg_cpu": window_infra['cpu_util_pct'].mean() if 'cpu_util_pct' in window_infra.columns else 0,
            "peak_cpu": window_infra['cpu_util_pct'].max() if 'cpu_util_pct' in window_infra.columns else 0,
            "avg_memory": window_infra['mem_util_pct'].mean() if 'mem_util_pct' in window_infra.columns else 0,
            "peak_memory": window_infra['mem_util_pct'].max() if 'mem_util_pct' in window_infra.columns else 0
        }
        
        # Only include windows with performance issues or high resource usage
        has_performance_issues = (perf_issues["sla_violation_rate"] > 10 or 
                                 perf_issues["avg_response_time"] > sla_threshold)
        has_high_resource_usage = (infra_metrics["avg_cpu"] > cpu_high_threshold or 
                                  infra_metrics["avg_memory"] > memory_high_threshold)
        
        if has_performance_issues or has_high_resource_usage:
            return {
                "time_window": f"{window_start.isoformat()} to {window_end.isoformat()}",
                "performance_issues": perf_issues,
                "infrastructure_metrics": infra_metrics,
                "correlation_analysis": {
                    "cpu_constraint": infra_metrics["avg_cpu"] > cpu_high_threshold,
                    "memory_constraint": infra_metrics["avg_memory"] > memory_high_threshold,
                    "performance_degradation": has_performance_issues
                }
            }
        
        return None
        
    except Exception as e:
        print(f"Error analyzing time window: {e}")
        return None

def generate_temporal_insights(correlation_results, significant_correlations, environment_type: str) -> List[str]:
    """
    Generate actionable insights from temporal correlation analysis
    Tailored for Performance Test Engineers
    """
    
    insights = []
    
    # Resource type terminology
    resource_label = "service" if environment_type == 'k8s' else "host"
    resources_label = "services" if environment_type == 'k8s' else "hosts"
    
    # Overall correlation strength insights
    strong_corrs = [c for c in significant_correlations if c.get('strength') == 'strong']
    moderate_corrs = [c for c in significant_correlations if c.get('strength') == 'moderate']
    
    if strong_corrs:
        insights.append(
            f"Found {len(strong_corrs)} strong correlation(s) between {resource_label} resources and performance - "
            f"these are primary optimization targets"
        )
    
    if moderate_corrs:
        insights.append(
            f"Found {len(moderate_corrs)} moderate correlation(s) - "
            f"consider investigating these as secondary optimization opportunities"
        )
    
    # CPU-specific insights
    cpu_corrs = [c for c in significant_correlations if 'cpu' in c.get('type', '')]
    if cpu_corrs:
        cpu_rt = next((c for c in cpu_corrs if 'response_time' in c.get('type', '')), None)
        if cpu_rt and cpu_rt.get('direction') == 'positive':
            strength = cpu_rt.get('strength')
            if strength == 'strong':
                insights.append(
                    f"Strong CPU-performance correlation detected: {resource_label.capitalize()} CPU utilization "
                    f"directly impacts response times - recommend CPU scaling or optimization"
                )
            else:
                insights.append(
                    f"Moderate CPU-performance correlation: Monitor {resource_label} CPU usage during peak loads"
                )
    
    # Memory-specific insights
    mem_corrs = [c for c in significant_correlations if 'memory' in c.get('type', '')]
    if mem_corrs:
        mem_rt = next((c for c in mem_corrs if 'response_time' in c.get('type', '')), None)
        if mem_rt and mem_rt.get('direction') == 'positive':
            strength = mem_rt.get('strength')
            if strength == 'strong':
                insights.append(
                    f"Strong memory-performance correlation detected: {resource_label.capitalize()} memory pressure "
                    f"affecting response times - recommend memory allocation review"
                )
            else:
                insights.append(
                    f"Moderate memory-performance correlation: {resource_label.capitalize()} memory usage "
                    f"may contribute to performance variability"
                )
    
    # SLA violation insights
    sla_corrs = [c for c in significant_correlations if 'sla_violations' in c.get('type', '')]
    if sla_corrs:
        cpu_sla = next((c for c in sla_corrs if 'cpu' in c.get('type', '')), None)
        mem_sla = next((c for c in sla_corrs if 'memory' in c.get('type', '')), None)
        
        if cpu_sla and cpu_sla.get('strength') == 'strong':
            insights.append(
                f"Critical: High CPU utilization on {resources_label} strongly correlates with SLA violations - "
                f"immediate action recommended"
            )
        
        if mem_sla and mem_sla.get('strength') == 'strong':
            insights.append(
                f"Critical: High memory utilization on {resources_label} strongly correlates with SLA violations - "
                f"investigate memory leaks or sizing issues"
            )
    
    # Temporal pattern insights
    interesting_periods = correlation_results.get('interesting_periods', 0)
    total_windows = correlation_results.get('total_windows', 0)
    
    if total_windows > 0:
        interesting_pct = (interesting_periods / total_windows) * 100
        if interesting_pct > 30:
            insights.append(
                f"High variability detected: {interesting_pct:.1f}% of time windows show concerning patterns - "
                f"system stability may be at risk"
            )
        elif interesting_pct > 10:
            insights.append(
                f"Moderate variability: {interesting_pct:.1f}% of time windows show elevated resource usage or response times"
            )
        else:
            insights.append(
                f"Stable performance: Only {interesting_pct:.1f}% of time windows show elevated metrics - "
                f"system performing within expected parameters"
            )
    
    # No significant correlations case
    if not significant_correlations:
        insights.append(
            f"No significant correlations found between {resource_label} resources and performance metrics - "
            f"{resource_label} infrastructure appears adequately sized for current load"
        )
        insights.append(
            "Performance bottlenecks may be in application code, database, or external dependencies rather than infrastructure"
        )
    
    return insights

def generate_temporal_correlation_summary(results: Dict) -> Dict:
    """Generate summary statistics for temporal correlation analysis"""
    
    analysis_periods = results.get("analysis_periods", [])
    correlation_matrix = results.get("correlation_matrix", {})
    significant_correlations = results.get("significant_correlations", [])
    
    return {
        "total_analysis_periods": len(analysis_periods),
        "total_correlations_found": len(significant_correlations),
        "strong_correlations": len([c for c in significant_correlations if c.get("strength") == "strong"]),
        "moderate_correlations": len([c for c in significant_correlations if c.get("strength") == "moderate"]),
        "cpu_related_correlations": len([c for c in significant_correlations if "cpu" in c.get("type", "")]),
        "memory_related_correlations": len([c for c in significant_correlations if "memory" in c.get("type", "")]),
        "positive_correlations": len([c for c in significant_correlations if c.get("direction") == "positive"]),
        "negative_correlations": len([c for c in significant_correlations if c.get("direction") == "negative"]),
        "correlation_samples": correlation_matrix.get("total_correlation_samples", 0)
    }

def generate_temporal_analysis_breakdown(merged_df, correlation_results, sla_threshold, 
                                        resource_thresholds, environment_type='k8s'):
    """
    Generate detailed temporal analysis breakdown with per-window API stats
    Works for both K8s and Host environments
    """
    
    temporal_analysis = {
        "analysis_periods": [],
        "summary": {
            "total_periods": 0,
            "high_utilization_periods": 0,
            "high_response_time_periods": 0,
            "sla_violation_periods": 0
        }
    }
    
    if merged_df.empty:
        return temporal_analysis
    
    # Group by time window
    grouped = merged_df.groupby('time_window')
    
    cpu_threshold = resource_thresholds.get('cpu', {}).get('high', 80)
    mem_threshold = resource_thresholds.get('memory', {}).get('high', 85)
    
    for time_window, window_data in grouped:
        
        # Calculate window statistics
        avg_cpu = window_data['cpu_util_pct'].mean() if 'cpu_util_pct' in window_data else 0
        avg_memory = window_data['mem_util_pct'].mean() if 'mem_util_pct' in window_data else 0
        avg_response_time = window_data['elapsed'].mean() if 'elapsed' in window_data else 0
        
        # Count SLA violations
        sla_violations = (window_data['elapsed'] > sla_threshold).sum() if 'elapsed' in window_data else 0
        total_requests = len(window_data)
        
        # Identify interesting period
        is_high_cpu = avg_cpu > cpu_threshold
        is_high_memory = avg_memory > mem_threshold
        is_high_response = avg_response_time > sla_threshold
        has_sla_violations = sla_violations > 0
        
        is_interesting = is_high_cpu or is_high_memory or is_high_response or has_sla_violations
        
        # Get API breakdown for this window
        api_breakdown = []
        if 'label' in window_data.columns:
            api_groups = window_data.groupby('label')
            for api_name, api_data in api_groups:
                api_breakdown.append({
                    "api_name": api_name,
                    "request_count": len(api_data),
                    "avg_response_time": float(api_data['elapsed'].mean()),
                    "max_response_time": float(api_data['elapsed'].max()),
                    "sla_violations": int((api_data['elapsed'] > sla_threshold).sum())
                })
        
        period_data = {
            "timestamp": time_window.isoformat(),
            "avg_cpu_utilization": round(float(avg_cpu), 2),
            "avg_memory_utilization": round(float(avg_memory), 2),
            "avg_response_time": round(float(avg_response_time), 2),
            "total_requests": int(total_requests),
            "sla_violations": int(sla_violations),
            "sla_violation_rate": round(float(sla_violations / total_requests * 100), 2) if total_requests > 0 else 0,
            "is_interesting_period": is_interesting,
            "flags": {
                "high_cpu": is_high_cpu,
                "high_memory": is_high_memory,
                "high_response_time": is_high_response,
                "has_sla_violations": has_sla_violations
            },
            "api_breakdown": api_breakdown
        }
        
        temporal_analysis["analysis_periods"].append(period_data)
        
        # Update summary counts
        if is_interesting:
            temporal_analysis["summary"]["high_utilization_periods"] += 1
        if is_high_response:
            temporal_analysis["summary"]["high_response_time_periods"] += 1
        if has_sla_violations:
            temporal_analysis["summary"]["sla_violation_periods"] += 1
    
    temporal_analysis["summary"]["total_periods"] = len(temporal_analysis["analysis_periods"])
    
    return temporal_analysis

# -----------------------------------------------
# Statistical analysis functions
# -----------------------------------------------
def calculate_response_time_stats(df: pd.DataFrame) -> Dict[str, Any]:
    """Calculate comprehensive response time statistics"""
    
    # Overall statistics
    overall_stats = {
        "total_samples": len(df),
        "success_rate": (df['success'].sum() / len(df)) * 100 if 'success' in df.columns else 0,
        "avg_response_time": df['elapsed'].mean(),
        "min_response_time": df['elapsed'].min(),
        "max_response_time": df['elapsed'].max(),
        "p90_response_time": df['elapsed'].quantile(0.9),
        "p95_response_time": df['elapsed'].quantile(0.95),
        "p99_response_time": df['elapsed'].quantile(0.99),
        "std_dev": df['elapsed'].std(),
        "error_count": len(df[df['success'] == False]) if 'success' in df.columns else 0
    }
    
    # Per-API statistics
    api_analysis = {}
    if 'label' in df.columns:
        for label in df['label'].unique():
            label_df = df[df['label'] == label]
            api_analysis[label] = {
                "samples": len(label_df),
                "avg_response_time": label_df['elapsed'].mean(),
                "min_response_time": label_df['elapsed'].min(),
                "max_response_time": label_df['elapsed'].max(),
                "p90_response_time": label_df['elapsed'].quantile(0.9),
                "success_rate": (label_df['success'].sum() / len(label_df)) * 100 if 'success' in label_df.columns else 0,
                "error_count": len(label_df[label_df['success'] == False]) if 'success' in label_df.columns else 0
            }
    
    return {
        "overall_stats": overall_stats,
        "api_analysis": api_analysis,
        "analysis_timestamp": pd.Timestamp.now().isoformat()
    }

def detect_statistical_anomalies(blazemeter_file: Path, datadog_files: List[Path], sensitivity: str) -> Dict:
    """Detect anomalies using statistical methods"""
    
    # Map sensitivity to standard deviations
    sensitivity_map = {"low": 3.0, "medium": 2.5, "high": 2.0}
    threshold = sensitivity_map.get(sensitivity, 2.5)
    
    anomalies = {
        "sensitivity": sensitivity,
        "threshold_std_dev": threshold,
        "anomalies": [],
        "summary": {
            "total_anomalies": 0,
            "response_time_anomalies": 0,
            "resource_anomalies": 0
        }
    }
    
    # Load and analyze BlazeMeter data for response time anomalies
    if blazemeter_file.exists():
        df = pd.read_csv(blazemeter_file)
        mean_rt = df['elapsed'].mean()
        std_rt = df['elapsed'].std()
        
        # Detect response time anomalies
        rt_anomalies = df[(np.abs(df['elapsed'] - mean_rt) > threshold * std_rt)]
        
        for _, row in rt_anomalies.iterrows():
            anomalies["anomalies"].append({
                "type": "response_time",
                "timestamp": row['timeStamp'],
                "api": row.get('label', 'unknown'),
                "value": row['elapsed'],
                "z_score": (row['elapsed'] - mean_rt) / std_rt,
                "severity": "high" if abs((row['elapsed'] - mean_rt) / std_rt) > threshold + 1 else "medium"
            })
    
    anomalies["summary"]["total_anomalies"] = len(anomalies["anomalies"])
    return anomalies

def identify_resource_bottlenecks(correlation_file: Path, anomaly_file: Path, test_run_id: str) -> Dict:
    """Identify system bottlenecks based on correlation and anomaly analysis"""
    
    bottlenecks = {
        "test_run_id": test_run_id,
        "bottlenecks": [],
        "recommendations": [],
        "priority_ranking": []
    }
    
    # Load correlation and anomaly data
    if correlation_file.exists():
        with open(correlation_file, 'r') as f:
            correlation_data = json.load(f)
    
    if anomaly_file and anomaly_file.exists():
        with open(anomaly_file, 'r') as f:
            anomaly_data = json.load(f)
    
    # Analyze patterns and identify bottlenecks
    # This would involve more complex logic to identify resource constraints
    
    return bottlenecks
