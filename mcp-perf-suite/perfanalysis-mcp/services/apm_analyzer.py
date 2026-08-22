"""
APM Tool Agnostic Infrastructure Analysis Module

Handles analysis of infrastructure metrics from various APM tools:
- Datadog
- Dynatrace  
- AppDynamics
- New Relic
- etc.

Centralizes all infrastructure metric calculations, unit conversions, 
and resource utilization analysis.
"""
import json
import re
from typing import Dict, List, Optional, Any, Tuple
from fastmcp import Context     # ✅ FastMCP 2.x import
from pathlib import Path
import pandas as pd
import numpy as np
import math
import datetime
from utils.file_processor import (
    write_json_output,
    write_markdown_output,
    write_infrastructure_csv,
    format_infrastructure_markdown,
)

# -----------------------------------------------
# Main Function for the APM Analyzer MCP
# -----------------------------------------------
async def perform_infrastructure_analysis(
    k8s_files: List[Path], host_files: List[Path], 
    environments_config: Dict, config: Dict, test_run_id: str, ctx: Context
) -> Dict[str, Any]:
    """Perform comprehensive infrastructure metrics analysis"""
    
    analysis_results = {
        "infrastructure_summary": {},
        "resource_utilization": {},
        "detailed_metrics": {},
        "resource_insights": {},
        "assumptions_made": [],
        "environments_analyzed": [],
        "analysis_timestamp": datetime.datetime.now().isoformat()
    }
    
    # Analyze Kubernetes metrics if present
    if k8s_files:
        await ctx.info(f"K8s Analysis: Processing {len(k8s_files)} Kubernetes metrics files")
        k8s_analysis = await analyze_kubernetes_metrics(k8s_files, environments_config, config, ctx)
        analysis_results["detailed_metrics"]["kubernetes"] = k8s_analysis
        analysis_results["environments_analyzed"].extend(k8s_analysis.get("environments", []))
        analysis_results["assumptions_made"].extend(k8s_analysis.get("assumptions", []))
    
    # Analyze Host metrics if present  
    if host_files:
        await ctx.info(f"Host Analysis: Processing {len(host_files)} host metrics files")
        host_analysis = await analyze_host_metrics(host_files, environments_config, config, ctx)
        analysis_results["detailed_metrics"]["hosts"] = host_analysis
        analysis_results["environments_analyzed"].extend(host_analysis.get("environments", []))
        analysis_results["assumptions_made"].extend(host_analysis.get("assumptions", []))
    
    # Generate overall infrastructure summary
    analysis_results["infrastructure_summary"] = generate_infrastructure_summary(analysis_results)
    
    # Generate resource utilization insights
    analysis_results["resource_insights"] = generate_resource_insights(analysis_results, config)
    
    return analysis_results

# -----------------------------------------------
# Kubernetes Analysis Functions
# -----------------------------------------------
async def analyze_kubernetes_metrics(
    k8s_files: List[Path], environments_config: Dict, config: Dict, ctx: Context
) -> Dict[str, Any]:
    """Analyze Kubernetes container metrics with correct nanocore calculations"""
    
    k8s_analysis = {
        "entities": {},
        "environments": [],
        "assumptions": [],
        "total_containers": 0,
        "metrics_summary": {}
    }
    
    # Combine all K8s CSV files
    all_k8s_data = []
    for k8s_file in k8s_files:
        try:
            df = pd.read_csv(k8s_file)
            all_k8s_data.append(df)
        except Exception as e:
            await ctx.error(f"K8s File Error: Failed to read {k8s_file}: {str(e)}")
            continue
    
    if not all_k8s_data:
        return k8s_analysis
    
    # Combine all dataframes
    combined_df = pd.concat(all_k8s_data, ignore_index=True)
    
    # Get unique environments
    environments = combined_df['env_name'].unique().tolist()
    k8s_analysis["environments"] = environments
    
    # Process each environment
    for env_name in environments:
        env_data = combined_df[combined_df['env_name'] == env_name]
        env_config = environments_config.get("environments", {}).get(env_name, {})
        
        # Get K8s services and pods configuration
        k8s_config = env_config.get("kubernetes", {})
        services_config = k8s_config.get("services", [])
        pods_config = k8s_config.get("pods", [])
        
        # Analyze each unique service/pod
        unique_filters = env_data['filter'].unique()
        
        for filter_name in unique_filters:
            filter_data = env_data[env_data['filter'] == filter_name]
            
            # Find matching configuration - try services first, then pods
            # Supports both canonical keys (service_filter, pod_filter) and Datadog-native alias (kube_service)
            entity_config = next(
                (s for s in services_config if s.get('service_filter') == filter_name or s.get('kube_service') == filter_name), 
                None
            )
            entity_type = "service"
            
            if not entity_config:
                entity_config = next(
                    (p for p in pods_config
                     if p.get('pod_filter') == filter_name
                     or p.get('pod_filter', '').rstrip('*') == filter_name
                     or p.get('kube_service') == filter_name),
                    None
                )
                entity_type = "pod" if entity_config else "unknown"
            
            # Use empty dict if no config found
            if not entity_config:
                entity_config = {}
            
            # Get resource allocation (with config-driven defaults)
            resource_allocation = get_kubernetes_resource_allocation(entity_config, filter_name, config)
            
            # Analyze service/pod metrics
            entity_analysis = analyze_k8s_entity_metrics(filter_data, resource_allocation, config)
            k8s_analysis["entities"][f"{env_name}::{filter_name}"] = entity_analysis
            
            # Track assumptions based on CSV-driven limits (source of truth)
            cpu_defined = entity_analysis.get("limits_status", {}).get("cpu_limits_defined", False)
            mem_defined = entity_analysis.get("limits_status", {}).get("mem_limits_defined", False)
            
            if not cpu_defined and not mem_defined:
                k8s_analysis["assumptions"].append(
                    f"K8s {entity_type.title()} '{filter_name}' - "
                    f"no CPU or memory limits defined at the pod/container level"
                )
            elif not cpu_defined:
                k8s_analysis["assumptions"].append(
                    f"K8s {entity_type.title()} '{filter_name}' - no CPU limits defined at the pod/container level"
                )
            elif not mem_defined:
                k8s_analysis["assumptions"].append(
                    f"K8s {entity_type.title()} '{filter_name}' - no memory limits defined at the pod/container level"
                )
            
            # Count containers
            container_count = filter_data['container_or_pod'].nunique()
            k8s_analysis["total_containers"] += container_count
    
    return k8s_analysis

def get_kubernetes_resource_allocation(entity_config: Dict, entity_name: str, config: Dict) -> Dict:
    """Get K8s entity resource allocation. Returns None for values when no real limits exist.
    
    Resource values come from the entity's environment configuration.
    If not defined, returns None -- no fabricated defaults.
    """
    
    cpu_raw = entity_config.get("cpus")
    mem_raw = entity_config.get("memory")
    
    return {
        "cpus": parse_cpu_cores(cpu_raw),
        "memory_gb": parse_memory_gib(mem_raw)
    }

def analyze_k8s_entity_metrics(entity_data: pd.DataFrame, resource_allocation: Dict, config: Dict) -> Dict:
    """
    Analyze individual K8s entity (service or pod) metrics with DYNAMIC limits from Datadog.
    
    IMPORTANT: CPU/Memory limits are now read dynamically from the CSV (kubernetes.cpu.limits, 
    kubernetes.memory.limits). The static resource_allocation is used as fallback only if 
    dynamic limits are not available in the metrics data.
    
    Note: A value of -1 for cpu_util_pct or mem_util_pct indicates that Kubernetes
    limits are not defined for that service/pod.
    """
    
    # Separate metrics by type using explicit metric names
    cpu_usage_data = entity_data[entity_data['metric'] == 'kubernetes.cpu.usage.total']
    cpu_limits_data = entity_data[entity_data['metric'] == 'kubernetes.cpu.limits']
    cpu_util_data = entity_data[entity_data['metric'] == 'cpu_util_pct']
    
    mem_usage_data = entity_data[entity_data['metric'] == 'kubernetes.memory.usage']
    mem_limits_data = entity_data[entity_data['metric'] == 'kubernetes.memory.limits']
    mem_util_data = entity_data[entity_data['metric'] == 'mem_util_pct']
    
    entity_metrics = {
        "resource_allocation": resource_allocation,  # Keep for reference/fallback
        "cpu_analysis": {},
        "memory_analysis": {},
        "limits_status": {},  # NEW: Track whether limits were defined
        "containers": {},
        "time_range": {},
        "peak_utilization": {}
    }
    
    # Time range analysis
    if not entity_data.empty:
        entity_metrics["time_range"] = {
            "start_time": entity_data['timestamp_utc'].min(),
            "end_time": entity_data['timestamp_utc'].max(),
            "duration_minutes": calculate_duration_minutes(entity_data['timestamp_utc'])
        }
    
    # --- CPU Analysis ---
    if not cpu_usage_data.empty:
        # Convert nanocores to cores
        cpu_usage_converted = cpu_usage_data.copy()
        cpu_usage_converted['cpu_cores'] = cpu_usage_converted['value'] / 1e9
        
        entity_metrics["cpu_analysis"] = {
            "peak_usage_cores": float(cpu_usage_converted['cpu_cores'].max()),
            "avg_usage_cores": float(cpu_usage_converted['cpu_cores'].mean()),
            "min_usage_cores": float(cpu_usage_converted['cpu_cores'].min()),
            "samples_count": len(cpu_usage_converted)
        }
        
        # Get allocated cores from DYNAMIC limits (if available)
        if not cpu_limits_data.empty:
            # CPU limits are in cores from Datadog
            valid_limits = cpu_limits_data[cpu_limits_data['value'].astype(float) > 0]['value']
            if not valid_limits.empty:
                avg_limit = float(valid_limits.mean())
                entity_metrics["cpu_analysis"]["allocated_cores"] = avg_limit
                entity_metrics["limits_status"]["cpu_limits_defined"] = True
            else:
                # Limits exist but are all 0 (not defined in K8s)
                entity_metrics["cpu_analysis"]["allocated_cores"] = resource_allocation.get('cpus')  # None if unknown
                entity_metrics["limits_status"]["cpu_limits_defined"] = False
        else:
            # No limits data in CSV - use static config value (may be None)
            entity_metrics["cpu_analysis"]["allocated_cores"] = resource_allocation.get('cpus')  # None if unknown
            entity_metrics["limits_status"]["cpu_limits_defined"] = False
        
        # Get utilization from pre-calculated values (handles -1 = not defined)
        if not cpu_util_data.empty:
            # Filter out -1 values (limits not defined) for numeric calculations
            numeric_util = cpu_util_data[cpu_util_data['value'].astype(float) >= 0]['value'].astype(float)
            if not numeric_util.empty:
                entity_metrics["cpu_analysis"]["peak_utilization_pct"] = float(numeric_util.max())
                entity_metrics["cpu_analysis"]["avg_utilization_pct"] = float(numeric_util.mean())
                entity_metrics["cpu_analysis"]["min_utilization_pct"] = float(numeric_util.min())
            else:
                # All values are -1 (limits not defined)
                entity_metrics["cpu_analysis"]["peak_utilization_pct"] = None
                entity_metrics["cpu_analysis"]["avg_utilization_pct"] = None
                entity_metrics["cpu_analysis"]["min_utilization_pct"] = None
                entity_metrics["cpu_analysis"]["utilization_status"] = "limits_not_defined"
        else:
            # No pre-calculated utilization - calculate if we have valid limits
            if entity_metrics["limits_status"].get("cpu_limits_defined") and entity_metrics["cpu_analysis"].get("allocated_cores", 0) > 0:
                allocated = entity_metrics["cpu_analysis"]["allocated_cores"]
                entity_metrics["cpu_analysis"]["peak_utilization_pct"] = float((cpu_usage_converted['cpu_cores'].max() / allocated) * 100)
                entity_metrics["cpu_analysis"]["avg_utilization_pct"] = float((cpu_usage_converted['cpu_cores'].mean() / allocated) * 100)
                entity_metrics["cpu_analysis"]["min_utilization_pct"] = float((cpu_usage_converted['cpu_cores'].min() / allocated) * 100)
            else:
                entity_metrics["cpu_analysis"]["peak_utilization_pct"] = None
                entity_metrics["cpu_analysis"]["avg_utilization_pct"] = None
                entity_metrics["cpu_analysis"]["min_utilization_pct"] = None
                entity_metrics["cpu_analysis"]["utilization_status"] = "limits_not_defined"
    
    # --- Memory Analysis ---
    if not mem_usage_data.empty:
        # Convert bytes to GB
        mem_usage_converted = mem_usage_data.copy()
        mem_usage_converted['memory_gb'] = mem_usage_converted['value'] / 1e9
        
        entity_metrics["memory_analysis"] = {
            "peak_usage_gb": float(mem_usage_converted['memory_gb'].max()),
            "avg_usage_gb": float(mem_usage_converted['memory_gb'].mean()),
            "min_usage_gb": float(mem_usage_converted['memory_gb'].min()),
            "samples_count": len(mem_usage_converted)
        }
        
        # Get allocated memory from DYNAMIC limits (if available)
        if not mem_limits_data.empty:
            # Memory limits are in bytes from Datadog, convert to GB
            valid_limits = mem_limits_data[mem_limits_data['value'].astype(float) > 0]['value']
            if not valid_limits.empty:
                avg_limit_gb = float(valid_limits.mean()) / 1e9
                entity_metrics["memory_analysis"]["allocated_gb"] = avg_limit_gb
                entity_metrics["limits_status"]["mem_limits_defined"] = True
            else:
                # Limits exist but are all 0 (not defined in K8s)
                entity_metrics["memory_analysis"]["allocated_gb"] = resource_allocation.get('memory_gb')  # None if unknown
                entity_metrics["limits_status"]["mem_limits_defined"] = False
        else:
            # No limits data in CSV - use static config value (may be None)
            entity_metrics["memory_analysis"]["allocated_gb"] = resource_allocation.get('memory_gb')  # None if unknown
            entity_metrics["limits_status"]["mem_limits_defined"] = False
        
        # Get utilization from pre-calculated values (handles -1 = not defined)
        if not mem_util_data.empty:
            # Filter out -1 values (limits not defined) for numeric calculations
            numeric_util = mem_util_data[mem_util_data['value'].astype(float) >= 0]['value'].astype(float)
            if not numeric_util.empty:
                entity_metrics["memory_analysis"]["peak_utilization_pct"] = float(numeric_util.max())
                entity_metrics["memory_analysis"]["avg_utilization_pct"] = float(numeric_util.mean())
                entity_metrics["memory_analysis"]["min_utilization_pct"] = float(numeric_util.min())
            else:
                # All values are -1 (limits not defined)
                entity_metrics["memory_analysis"]["peak_utilization_pct"] = None
                entity_metrics["memory_analysis"]["avg_utilization_pct"] = None
                entity_metrics["memory_analysis"]["min_utilization_pct"] = None
                entity_metrics["memory_analysis"]["utilization_status"] = "limits_not_defined"
        else:
            # No pre-calculated utilization - calculate if we have valid limits
            if entity_metrics["limits_status"].get("mem_limits_defined") and entity_metrics["memory_analysis"].get("allocated_gb", 0) > 0:
                allocated = entity_metrics["memory_analysis"]["allocated_gb"]
                entity_metrics["memory_analysis"]["peak_utilization_pct"] = float((mem_usage_converted['memory_gb'].max() / allocated) * 100)
                entity_metrics["memory_analysis"]["avg_utilization_pct"] = float((mem_usage_converted['memory_gb'].mean() / allocated) * 100)
                entity_metrics["memory_analysis"]["min_utilization_pct"] = float((mem_usage_converted['memory_gb'].min() / allocated) * 100)
            else:
                entity_metrics["memory_analysis"]["peak_utilization_pct"] = None
                entity_metrics["memory_analysis"]["avg_utilization_pct"] = None
                entity_metrics["memory_analysis"]["min_utilization_pct"] = None
                entity_metrics["memory_analysis"]["utilization_status"] = "limits_not_defined"
    
    # --- Container-level breakdown ---
    containers = entity_data['container_or_pod'].unique()
    for container in containers:
        container_data = entity_data[entity_data['container_or_pod'] == container]
        
        container_cpu_usage = container_data[container_data['metric'] == 'kubernetes.cpu.usage.total']
        container_mem_usage = container_data[container_data['metric'] == 'kubernetes.memory.usage']
        
        entity_metrics["containers"][container] = {
            "cpu_samples": len(container_cpu_usage),
            "memory_samples": len(container_mem_usage),
            "peak_cpu_cores": float(container_cpu_usage['value'].max() / 1e9) if not container_cpu_usage.empty else 0,
            "peak_memory_gb": float(container_mem_usage['value'].max() / 1e9) if not container_mem_usage.empty else 0
        }
    
    return entity_metrics

# -----------------------------------------------
# Host Analysis Functions
# -----------------------------------------------
async def analyze_host_metrics(
    host_files: List[Path], environments_config: Dict, config: Dict, ctx: Context
) -> Dict[str, Any]:
    """Analyze host/VM metrics"""
    
    host_analysis = {
        "hosts": {},
        "environments": [],
        "assumptions": [],
        "total_hosts": 0,
        "metrics_summary": {}
    }
    
    # Combine all host CSV files
    all_host_data = []
    for host_file in host_files:
        try:
            df = pd.read_csv(host_file)
            all_host_data.append(df)
        except Exception as e:
            await ctx.error(f"Host File Error: Failed to read {host_file}: {str(e)}")
            continue
    
    if not all_host_data:
        return host_analysis
    
    # Combine all dataframes
    combined_df = pd.concat(all_host_data, ignore_index=True)
    
    # Get unique environments
    environments = combined_df['env_name'].unique().tolist()
    host_analysis["environments"] = environments
    
    # Process each environment
    for env_name in environments:
        env_data = combined_df[combined_df['env_name'] == env_name]
        env_config = environments_config.get("environments", {}).get(env_name, {})
        
        # Get hosts configuration
        hosts_config = env_config.get("hosts", [])
        
        # Analyze each unique host
        unique_hosts = env_data['hostname'].unique()
        
        for hostname in unique_hosts:
            host_data = env_data[env_data['hostname'] == hostname]
            
            # Find matching host configuration
            host_config = next(
                (h for h in hosts_config if h.get('hostname') == hostname),
                {}
            )
            
            # Get resource allocation (with config-driven defaults)
            resource_allocation = get_host_resource_allocation(host_config, hostname, config)
            
            # Analyze host metrics
            host_metrics_analysis = analyze_host_system_metrics(host_data, resource_allocation, config)
            host_analysis["hosts"][f"{env_name}::{hostname}"] = host_metrics_analysis
            
            # Track assumptions based on actual analysis results (source of truth)
            cpu_analysis = host_metrics_analysis.get("cpu_analysis", {})
            mem_analysis = host_metrics_analysis.get("memory_analysis", {})
            has_cpu = cpu_analysis.get("peak_utilization_pct") is not None
            has_mem = mem_analysis.get("peak_utilization_pct") is not None
            
            if not has_cpu and not has_mem:
                host_analysis["assumptions"].append(
                    f"Host '{hostname}' - no CPU or memory metrics available"
                )
            elif not has_cpu:
                host_analysis["assumptions"].append(
                    f"Host '{hostname}' - no CPU metrics available"
                )
            elif not has_mem:
                host_analysis["assumptions"].append(
                    f"Host '{hostname}' - no memory metrics available"
                )
            
            host_analysis["total_hosts"] += 1
    
    return host_analysis

def get_host_resource_allocation(host_config: Dict, hostname: str, config: Dict) -> Dict:
    """Get host resource allocation. Returns None for values when no real config exists.
    
    Resource values come from the host's environment configuration.
    If not defined, returns None -- no fabricated defaults.
    """
    
    cpu_raw = host_config.get("cpus")
    mem_raw = host_config.get("memory")
    
    return {
        "cpus": parse_cpu_cores(cpu_raw) if cpu_raw else None,
        "memory_gb": parse_memory_gb(mem_raw)
    }

def analyze_host_system_metrics(host_data: pd.DataFrame, resource_allocation: Dict, config: Dict) -> Dict:
    """Analyze individual host system metrics"""
    
    # Separate CPU and Memory metrics
    cpu_data = host_data[host_data['metric'].str.contains('cpu', case=False, na=False)]
    memory_data = host_data[host_data['metric'].str.contains('mem', case=False, na=False)]
    
    host_metrics = {
        "resource_allocation": resource_allocation,
        "cpu_analysis": {},
        "memory_analysis": {},
        "time_range": {},
        "metric_types": host_data['metric'].unique().tolist()
    }
    
    # Time range analysis
    if not host_data.empty:
        host_metrics["time_range"] = {
            "start_time": host_data['timestamp_utc'].min(),
            "end_time": host_data['timestamp_utc'].max(),
            "duration_minutes": calculate_duration_minutes(host_data['timestamp_utc'])
        }
    
    # CPU Analysis (Host CPU already in percentages); align by timestamp to avoid NaNs
    if not cpu_data.empty:
        try:
            cpu_wide = (
                cpu_data
                .pivot_table(index='timestamp_utc', columns='metric', values='value', aggfunc='mean')
                .sort_index()
            )
            user_series = cpu_wide.get('system.cpu.user')
            system_series = cpu_wide.get('system.cpu.system')
            if user_series is not None or system_series is not None:
                user_filled = user_series.fillna(0) if user_series is not None else pd.Series(0, index=cpu_wide.index)
                system_filled = system_series.fillna(0) if system_series is not None else pd.Series(0, index=cpu_wide.index)
                total_cpu_pct = (user_filled + system_filled).fillna(0)
            else:
                # Fallback to any CPU metric available
                any_cpu = cpu_wide.select_dtypes(include=[np.number]).sum(axis=1)
                total_cpu_pct = any_cpu.fillna(0)

            if not total_cpu_pct.empty:
                host_metrics["cpu_analysis"] = {
                    "allocated_cpus": resource_allocation.get('cpus'),  # None if unknown
                    "peak_utilization_pct": float(np.nanmax(total_cpu_pct.values) if len(total_cpu_pct.values) else 0.0),
                    "avg_utilization_pct": float(np.nanmean(total_cpu_pct.values) if len(total_cpu_pct.values) else 0.0),
                    "min_utilization_pct": float(np.nanmin(total_cpu_pct.values) if len(total_cpu_pct.values) else 0.0),
                    "samples_count": int(total_cpu_pct.shape[0]),
                    "metrics_included": cpu_data['metric'].unique().tolist()
                }
        except Exception:
            # As a last resort, compute over the raw series without alignment
            total_cpu_pct = cpu_data['value'].astype(float)
            total_cpu_pct = total_cpu_pct.replace([np.inf, -np.inf], np.nan).fillna(0)
            if not total_cpu_pct.empty:
                host_metrics["cpu_analysis"] = {
                    "allocated_cpus": resource_allocation.get('cpus'),  # None if unknown
                    "peak_utilization_pct": float(np.nanmax(total_cpu_pct.values)),
                    "avg_utilization_pct": float(np.nanmean(total_cpu_pct.values)),
                    "min_utilization_pct": float(np.nanmin(total_cpu_pct.values)),
                    "samples_count": int(total_cpu_pct.shape[0]),
                    "metrics_included": cpu_data['metric'].unique().tolist()
                }
    
    # Memory Analysis (align by timestamp to avoid NaNs)
    # Always extract GB values from raw bytes (system.mem.used) when available,
    # and use direct percentage (mem_util_pct) for percentage values if present.
    if not memory_data.empty:
        try:
            mem_wide = (
                memory_data
                .pivot_table(index='timestamp_utc', columns='metric', values='value', aggfunc='mean')
                .sort_index()
            )
            
            # Extract raw memory bytes for GB calculations (if available)
            used = mem_wide.get('system.mem.used')
            total = mem_wide.get('system.mem.total')
            
            # Calculate GB values from raw bytes
            peak_usage_gb = None
            avg_usage_gb = None
            detected_total_gb = None
            if used is not None:
                used_gb = used.astype(float) / 1e9
                peak_usage_gb = float(np.nanmax(used_gb.values) if len(used_gb.values) else 0.0)
                avg_usage_gb = float(np.nanmean(used_gb.values) if len(used_gb.values) else 0.0)
            if total is not None:
                total_gb = total.astype(float) / 1e9
                detected_total_gb = float(np.nanmean(total_gb.values) if len(total_gb.values) else 0.0)
            
            # Direct percentage if present (mem_util_pct or mem_used_pct)
            direct_pct = None
            if 'mem_util_pct' in mem_wide.columns:
                direct_pct = mem_wide['mem_util_pct'].astype(float).replace([np.inf, -np.inf], np.nan)
            elif 'mem_used_pct' in mem_wide.columns:
                direct_pct = mem_wide['mem_used_pct'].astype(float).replace([np.inf, -np.inf], np.nan)

            if direct_pct is not None:
                pct_series = direct_pct.fillna(0)
                host_metrics["memory_analysis"] = {
                    "allocated_gb": resource_allocation.get('memory_gb'),  # None if unknown
                    "peak_utilization_pct": float(np.nanmax(pct_series.values) if len(pct_series.values) else 0.0),
                    "avg_utilization_pct": float(np.nanmean(pct_series.values) if len(pct_series.values) else 0.0),
                    "min_utilization_pct": float(np.nanmin(pct_series.values) if len(pct_series.values) else 0.0),
                    "samples_count": int(pct_series.shape[0]),
                    "calculation_method": "direct_percentage"
                }
                # Also include GB values from raw bytes if available
                if peak_usage_gb is not None:
                    host_metrics["memory_analysis"]["peak_usage_gb"] = peak_usage_gb
                if avg_usage_gb is not None:
                    host_metrics["memory_analysis"]["avg_usage_gb"] = avg_usage_gb
                if detected_total_gb is not None:
                    host_metrics["memory_analysis"]["detected_total_gb"] = detected_total_gb
            elif used is not None and total is not None:
                # Calculate percentage from raw values
                    used_gb = used.astype(float) / 1e9
                    total_gb = total.astype(float) / 1e9
                    # Avoid division misalignment and divide-by-zero
                    total_gb_clean = total_gb.replace(0, np.nan)
                    util_pct = (used_gb / total_gb_clean) * 100
                    util_pct = util_pct.replace([np.inf, -np.inf], np.nan).fillna(0)
                    host_metrics["memory_analysis"] = {
                        "allocated_gb": resource_allocation.get('memory_gb'),  # None if unknown
                        "peak_usage_gb": float(np.nanmax(used_gb.values) if len(used_gb.values) else 0.0),
                        "avg_usage_gb": float(np.nanmean(used_gb.values) if len(used_gb.values) else 0.0),
                        "detected_total_gb": float(np.nanmean(total_gb.values) if len(total_gb.values) else 0.0),
                        "peak_utilization_pct": float(np.nanmax(util_pct.values) if len(util_pct.values) else 0.0),
                        "avg_utilization_pct": float(np.nanmean(util_pct.values) if len(util_pct.values) else 0.0),
                        "samples_count": int(util_pct.shape[0]),
                        "calculation_method": "calculated_from_raw"
                    }
        except Exception:
            # Fallback: best-effort calculation over raw values
            try:
                used_raw = memory_data[memory_data['metric'] == 'system.mem.used']['value'].astype(float) / 1e9
                total_raw = memory_data[memory_data['metric'] == 'system.mem.total']['value'].astype(float) / 1e9
                min_len = min(len(used_raw), len(total_raw))
                if min_len > 0:
                    used_arr = used_raw.values[:min_len]
                    total_arr = total_raw.values[:min_len]
                    total_arr[total_arr == 0] = np.nan
                    util_arr = (used_arr / total_arr) * 100
                    util_arr = np.nan_to_num(util_arr, nan=0.0, posinf=0.0, neginf=0.0)
                    host_metrics["memory_analysis"] = {
                        "allocated_gb": resource_allocation.get('memory_gb'),  # None if unknown
                        "peak_usage_gb": float(np.nanmax(used_arr)),
                        "avg_usage_gb": float(np.nanmean(used_arr)),
                        "detected_total_gb": float(np.nanmean(total_arr[np.isfinite(total_arr)]) if np.isfinite(total_arr).any() else 0.0),
                        "peak_utilization_pct": float(np.nanmax(util_arr)),
                        "avg_utilization_pct": float(np.nanmean(util_arr)),
                        "samples_count": int(min_len),
                        "calculation_method": "calculated_from_raw_fallback"
                    }
            except Exception:
                pass
    
    return host_metrics

# -----------------------------------------------
# Utility Functions 
# -----------------------------------------------
def parse_cpu_cores(cpu_str) -> float:
    """Parse CPU allocation from various formats. Returns None if no valid value can be parsed."""
    if cpu_str is None:
        return None
    if isinstance(cpu_str, (int, float)):
        return float(cpu_str)
    
    try:
        # Handle formats: "4.05 core", "4 cores", "4.0", "4", "50 millicores"
        import re
        cpu_str = str(cpu_str).lower()
        matches = re.findall(r'(\d+\.?\d*)', cpu_str)
        return float(matches[0]) if matches else None
    except (ValueError, AttributeError):
        return None

def parse_memory_gb(memory_str) -> float:
    """Parse memory allocation from various formats. Returns None if no valid value can be parsed."""
    if memory_str is None:
        return None
    if isinstance(memory_str, (int, float)):
        return float(memory_str)
    
    try:
        import re
        memory_str = str(memory_str).upper()
        
        # Extract numeric value
        matches = re.findall(r'(\d+\.?\d*)', memory_str)
        if not matches:
            return None
        
        value = float(matches[0])
        
        # Convert based on unit
        if 'GB' in memory_str or 'GIB' in memory_str:
            return value
        elif 'MB' in memory_str or 'MIB' in memory_str:
            return value / 1024
        elif 'TB' in memory_str or 'TIB' in memory_str:
            return value * 1024
        else:
            return value  # Assume GB if no unit
    except (ValueError, AttributeError):
        return None

def parse_memory_gib(memory_str: str) -> float:
    """Parse memory allocation from GiB format (same as GB for practical purposes)"""
    return parse_memory_gb(memory_str)

def calculate_duration_minutes(timestamp_series) -> float:
    """Calculate duration in minutes from timestamp series"""
    try:
        start_time = pd.to_datetime(timestamp_series.min())
        end_time = pd.to_datetime(timestamp_series.max())
        duration = (end_time - start_time).total_seconds() / 60
        return round(duration, 2)
    except:
        return 0.0

# -----------------------------------------------
# Summary & Insight Functions
# -----------------------------------------------
def generate_infrastructure_summary(analysis_results: Dict) -> Dict:
    """Generate overall infrastructure summary"""
    
    summary = {
        "total_environments": len(analysis_results.get("environments_analyzed", [])),
        "kubernetes_summary": {},
        "host_summary": {},
        "overall_health": "unknown"
    }
    
    # K8s summary
    k8s_data = analysis_results.get("detailed_metrics", {}).get("kubernetes", {})
    if k8s_data:
        summary["kubernetes_summary"] = {
            "total_entities": len(k8s_data.get("entities", {})),
            "total_containers": k8s_data.get("total_containers", 0),
            "environments": k8s_data.get("environments", [])
        }
    
    # Host summary
    host_data = analysis_results.get("detailed_metrics", {}).get("hosts", {})
    if host_data:
        summary["host_summary"] = {
            "total_hosts": host_data.get("total_hosts", 0),
            "environments": host_data.get("environments", [])
        }
    
    return summary

def generate_resource_insights(analysis_results: Dict, config: Dict) -> Dict:
    """Generate resource utilization insights with config-driven thresholds"""
    
    cpu_thresholds = config['perf_analysis']['resource_thresholds']['cpu']
    memory_thresholds = config['perf_analysis']['resource_thresholds']['memory']
    
    insights = {
        "high_utilization": [],
        "low_utilization": [],
        "right_sized": [],
        "recommendations": []
    }
    
    # Analyze K8s resources
    k8s_data = analysis_results.get("detailed_metrics", {}).get("kubernetes", {})
    for entity_name, entity_metrics in k8s_data.get("entities", {}).items():
        analyze_k8s_utilization(entity_name, entity_metrics, cpu_thresholds, memory_thresholds, insights)
    
    # Analyze Host resources
    host_data = analysis_results.get("detailed_metrics", {}).get("hosts", {})
    for host_name, host_metrics in host_data.get("hosts", {}).items():
        analyze_host_utilization(host_name, host_metrics, cpu_thresholds, memory_thresholds, insights)
    
    return insights

def analyze_k8s_utilization(entity_name: str, entity_metrics: Dict, cpu_thresholds: Dict, memory_thresholds: Dict, insights: Dict):
    """
    Analyze K8s entity utilization against thresholds.
    
    Handles None values for utilization when limits are not defined in Kubernetes.
    """
    
    cpu_analysis = entity_metrics.get("cpu_analysis", {})
    memory_analysis = entity_metrics.get("memory_analysis", {})
    limits_status = entity_metrics.get("limits_status", {})
    
    # CPU utilization analysis
    if cpu_analysis:
        peak_cpu_pct = cpu_analysis.get("peak_utilization_pct")
        avg_cpu_pct = cpu_analysis.get("avg_utilization_pct")
        
        # Handle None values (limits not defined)
        if peak_cpu_pct is None or avg_cpu_pct is None:
            # CPU limits not defined - report as "unknown" status
            insights["right_sized"].append({
                "resource": f"{entity_name} (CPU)",
                "utilization": None,
                "status": "limits_not_defined",
                "note": "CPU limits not defined in Kubernetes - cannot calculate % utilization"
            })
        elif peak_cpu_pct > cpu_thresholds['high']:
            insights["high_utilization"].append({
                "resource": f"{entity_name} (CPU)",
                "type": "kubernetes",
                "peak_utilization": peak_cpu_pct,
                "avg_utilization": avg_cpu_pct,
                "threshold": cpu_thresholds['high'],
                "recommendation": f"CPU usage peaked at {peak_cpu_pct:.1f}% - consider increasing CPU allocation"
            })
        elif avg_cpu_pct < cpu_thresholds['low']:
            insights["low_utilization"].append({
                "resource": f"{entity_name} (CPU)",
                "type": "kubernetes", 
                "avg_utilization": avg_cpu_pct,
                "threshold": cpu_thresholds['low'],
                "recommendation": f"CPU avg usage {avg_cpu_pct:.1f}% - consider reducing allocation to save costs"
            })
        else:
            insights["right_sized"].append({
                "resource": f"{entity_name} (CPU)",
                "utilization": avg_cpu_pct,
                "status": "well-utilized"
            })
    
    # Memory utilization analysis  
    if memory_analysis:
        peak_mem_pct = memory_analysis.get("peak_utilization_pct")
        avg_mem_pct = memory_analysis.get("avg_utilization_pct")
        
        # Handle None values (limits not defined)
        if peak_mem_pct is None or avg_mem_pct is None:
            # Memory limits not defined - report as "unknown" status
            insights["right_sized"].append({
                "resource": f"{entity_name} (Memory)",
                "utilization": None,
                "status": "limits_not_defined",
                "note": "Memory limits not defined in Kubernetes - cannot calculate % utilization"
            })
        elif peak_mem_pct > memory_thresholds['high']:
            insights["high_utilization"].append({
                "resource": f"{entity_name} (Memory)",
                "type": "kubernetes",
                "peak_utilization": peak_mem_pct,
                "avg_utilization": avg_mem_pct,
                "threshold": memory_thresholds['high'],
                "recommendation": f"Memory usage peaked at {peak_mem_pct:.1f}% - consider increasing memory allocation"
            })
        elif avg_mem_pct < memory_thresholds['low']:
            insights["low_utilization"].append({
                "resource": f"{entity_name} (Memory)",
                "type": "kubernetes",
                "avg_utilization": avg_mem_pct,
                "threshold": memory_thresholds['low'],
                "recommendation": f"Memory avg usage {avg_mem_pct:.1f}% - consider reducing allocation to save costs"
            })
        else:
            insights["right_sized"].append({
                "resource": f"{entity_name} (Memory)",
                "utilization": avg_mem_pct,
                "status": "well-utilized"
            })

def analyze_host_utilization(host_name: str, host_metrics: Dict, cpu_thresholds: Dict, memory_thresholds: Dict, insights: Dict):
    """Analyze host utilization against thresholds"""
    
    cpu_analysis = host_metrics.get("cpu_analysis", {})
    memory_analysis = host_metrics.get("memory_analysis", {})
    
    # CPU utilization analysis
    if cpu_analysis:
        peak_cpu_pct = cpu_analysis.get("peak_utilization_pct", 0)
        avg_cpu_pct = cpu_analysis.get("avg_utilization_pct", 0)
        
        if peak_cpu_pct > cpu_thresholds['high']:
            insights["high_utilization"].append({
                "resource": f"{host_name} (CPU)",
                "type": "host",
                "peak_utilization": peak_cpu_pct,
                "avg_utilization": avg_cpu_pct,
                "threshold": cpu_thresholds['high'],
                "recommendation": f"CPU usage peaked at {peak_cpu_pct:.1f}% - monitor for potential scaling needs"
            })
        elif avg_cpu_pct < cpu_thresholds['low']:
            insights["low_utilization"].append({
                "resource": f"{host_name} (CPU)",
                "type": "host",
                "avg_utilization": avg_cpu_pct,
                "threshold": cpu_thresholds['low'],
                "recommendation": f"CPU avg usage {avg_cpu_pct:.1f}% - host may be over-provisioned"
            })
        else:
            insights["right_sized"].append({
                "resource": f"{host_name} (CPU)",
                "utilization": avg_cpu_pct,
                "status": "well-utilized"
            })
    
    # Memory utilization analysis
    if memory_analysis:
        peak_mem_pct = memory_analysis.get("peak_utilization_pct", 0)
        avg_mem_pct = memory_analysis.get("avg_utilization_pct", 0)
        
        if peak_mem_pct > memory_thresholds['high']:
            insights["high_utilization"].append({
                "resource": f"{host_name} (Memory)",
                "type": "host",
                "peak_utilization": peak_mem_pct,
                "avg_utilization": avg_mem_pct,
                "threshold": memory_thresholds['high'],
                "recommendation": f"Memory usage peaked at {peak_mem_pct:.1f}% - monitor for potential memory pressure"
            })
        elif avg_mem_pct < memory_thresholds['low']:
            insights["low_utilization"].append({
                "resource": f"{host_name} (Memory)",
                "type": "host",
                "avg_utilization": avg_mem_pct,
                "threshold": memory_thresholds['low'],
                "recommendation": f"Memory avg usage {avg_mem_pct:.1f}% - host may be over-provisioned"
            })
        else:
            insights["right_sized"].append({
                "resource": f"{host_name} (Memory)",
                "utilization": avg_mem_pct,
                "status": "well-utilized"
            })

async def generate_infrastructure_outputs(analysis: Dict, output_path: Path, test_run_id: str, ctx: Context) -> Dict[str, str]:
    """Generate all output files for infrastructure analysis"""
    
    output_files = {}
    
    try:
        # JSON Output - Detailed analysis
        json_file = output_path / 'infrastructure_analysis.json'
        await write_json_output(analysis, json_file)
        output_files['json'] = str(json_file)
        
        # CSV Output - Structured data for reporting
        csv_file = output_path / 'infrastructure_summary.csv'
        await write_infrastructure_csv(analysis, csv_file)
        output_files['csv'] = str(csv_file)
        
        # Markdown Output - Human-readable summary
        markdown_file = output_path / 'infrastructure_summary.md'
        markdown_content = format_infrastructure_markdown(analysis, test_run_id)
        await write_markdown_output(markdown_content, markdown_file)
        output_files['markdown'] = str(markdown_file)
        
        await ctx.info(f"Infrastructure Output Generation: Generated {len(output_files)} analysis files")
        
    except Exception as e:
        await ctx.error(f"Infrastructure Output Generation Error: Failed to generate outputs: {str(e)}")
    
    return output_files

