# utils/file_processor.py
import json
import csv
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Optional
import aiofiles


# -----------------------------------------------
# Custom JSON encoder for NumPy / pandas types
# -----------------------------------------------
class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that transparently converts NumPy/pandas types to native Python.

    Without this, ``json.dumps()`` raises ``TypeError: Object of type int64
    is not JSON serializable`` whenever a dict contains values that leaked
    through from pandas DataFrame / Series operations.
    """

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj)
            return None if (v != v or v == float("inf") or v == float("-inf")) else v
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        return super().default(obj)


def _sanitize_for_json(obj):
    """Recursively replace float NaN / Infinity with None for valid JSON output.

    Python's ``json.dumps`` emits non-standard ``NaN`` / ``Infinity`` tokens
    for these values.  This function converts them to ``None`` (JSON ``null``)
    before serialization so the output is strictly valid JSON.
    """
    if isinstance(obj, float):
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_for_json(v) for v in obj)
    return obj


# -----------------------------------------------
# File loading functions
# -----------------------------------------------
def load_jmeter_results(file_path: Path) -> Optional[pd.DataFrame]:
    """Load JMeter results from CSV file"""
    try:
        df = pd.read_csv(file_path)
        # Basic validation of required columns
        required_cols = ['timeStamp', 'elapsed', 'label', 'responseCode', 'success']
        if not all(col in df.columns for col in required_cols):
            return None
        return df
    except Exception:
        return None

def load_datadog_metrics(file_path: Path) -> Optional[pd.DataFrame]:
    """Load Datadog metrics from CSV file"""
    try:
        return pd.read_csv(file_path)
    except Exception:
        return None

# -----------------------------------------------
# File writing functions
# -----------------------------------------------
async def write_json_output(data: Dict[str, Any], file_path: Path) -> None:
    """Write data to JSON file asynchronously"""
    try:
        clean_data = _sanitize_for_json(data)
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(clean_data, indent=2, ensure_ascii=False, cls=NumpyEncoder))
    except Exception as e:
        raise Exception(f"Failed to write JSON file {file_path}: {str(e)}")

async def write_csv_output(data: List[Dict[str, Any]], file_path: Path, 
                          headers: Optional[List[str]] = None) -> None:
    """Write data to CSV file asynchronously"""
    try:
        if not data:
            return
        
        if headers is None:
            headers = list(data[0].keys()) if data else []
        
        async with aiofiles.open(file_path, 'w', newline='', encoding='utf-8') as f:
            # Use pandas for easier async CSV writing
            df = pd.DataFrame(data)
            csv_content = df.to_csv(index=False)
            await f.write(csv_content)
            
    except Exception as e:
        raise Exception(f"Failed to write CSV file {file_path}: {str(e)}")

async def write_markdown_output(content: str, file_path: Path) -> None:
    """Write markdown content to file asynchronously"""
    try:
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(content)
    except Exception as e:
        raise Exception(f"Failed to write Markdown file {file_path}: {str(e)}")

# -----------------------------------------------
# Analysis CSV writing functions
# -----------------------------------------------
async def write_performance_csv(analysis: Dict, csv_file: Path):
    """Write performance analysis to CSV format"""
    
    csv_data = []
    
    # Overall summary row
    overall = analysis.get('overall_stats', {})
    if overall and 'error' not in overall:
        csv_data.append({
            'metric_type': 'overall',
            'api_name': 'ALL',
            'samples': overall.get('total_samples', 0),
            'avg_response_time': overall.get('avg_response_time', 0),
            'min_response_time': overall.get('min_response_time', 0),
            'max_response_time': overall.get('max_response_time', 0),
            'p95_response_time': overall.get('p95_response_time', 0),
            'p99_response_time': overall.get('p99_response_time', 0),
            'success_rate': overall.get('success_rate', 0),
            'error_count': overall.get('error_count', 0),
            'avg_throughput': overall.get('avg_throughput', 0),
            'sla_compliant': None
        })
    
    # Individual API rows
    for api_name, api_stats in analysis.get('api_analysis', {}).items():
        csv_data.append({
            'metric_type': 'per_api',
            'api_name': api_name,
            'samples': api_stats.get('samples', 0),
            'avg_response_time': api_stats.get('avg_response_time', 0),
            'min_response_time': api_stats.get('min_response_time', 0),
            'max_response_time': api_stats.get('max_response_time', 0),
            'p95_response_time': api_stats.get('p95_response_time', 0),
            'p99_response_time': api_stats.get('p99_response_time', 0),
            'success_rate': api_stats.get('success_rate', 0),
            'error_count': api_stats.get('error_count', 0),
            'avg_throughput': api_stats.get('throughput', 0),
            'sla_compliant': api_stats.get('sla_compliant', None)
        })
    
    # Write to CSV
    df = pd.DataFrame(csv_data)
    df.to_csv(csv_file, index=False)

async def write_infrastructure_csv(analysis: Dict, csv_file: Path):
    """Write infrastructure analysis to CSV format"""
    
    csv_data = []
    
    # K8s entity data
    k8s_data = analysis.get("detailed_metrics", {}).get("kubernetes", {})
    for entity_name, entity_metrics in k8s_data.get("entities", {}).items():
        env_name, k8s_name = entity_name.split("::", 1) if "::" in entity_name else ("unknown", entity_name)
        
        cpu_analysis = entity_metrics.get("cpu_analysis", {})
        memory_analysis = entity_metrics.get("memory_analysis", {})
        
        csv_data.append({
            'environment': env_name,
            'resource_type': 'kubernetes',
            'resource_name': k8s_name,
            'metric_type': 'overall',
            'allocated_cpu': cpu_analysis.get('allocated_cores'),  # None if limits not defined
            'peak_cpu_utilization_pct': cpu_analysis.get('peak_utilization_pct'),
            'avg_cpu_utilization_pct': cpu_analysis.get('avg_utilization_pct'),
            'allocated_memory_gb': memory_analysis.get('allocated_gb'),  # None if limits not defined
            'peak_memory_utilization_pct': memory_analysis.get('peak_utilization_pct'),
            'avg_memory_utilization_pct': memory_analysis.get('avg_utilization_pct'),
            'total_containers': len(entity_metrics.get('containers', {})),
            'duration_minutes': entity_metrics.get('time_range', {}).get('duration_minutes', 0)
        })
    
    # Host data
    host_data = analysis.get("detailed_metrics", {}).get("hosts", {})
    for host_name, host_metrics in host_data.get("hosts", {}).items():
        env_name, hostname = host_name.split("::", 1) if "::" in host_name else ("unknown", host_name)
        
        cpu_analysis = host_metrics.get("cpu_analysis", {})
        memory_analysis = host_metrics.get("memory_analysis", {})
        
        csv_data.append({
            'environment': env_name,
            'resource_type': 'host',
            'resource_name': hostname,
            'metric_type': 'overall',
            'allocated_cpu': cpu_analysis.get('allocated_cpus'),  # None if not defined
            'peak_cpu_utilization_pct': cpu_analysis.get('peak_utilization_pct'),
            'avg_cpu_utilization_pct': cpu_analysis.get('avg_utilization_pct'),
            'allocated_memory_gb': memory_analysis.get('allocated_gb'),  # None if not defined
            'peak_memory_utilization_pct': memory_analysis.get('peak_utilization_pct'),
            'avg_memory_utilization_pct': memory_analysis.get('avg_utilization_pct'),
            'total_containers': 0,  # N/A for hosts
            'duration_minutes': host_metrics.get('time_range', {}).get('duration_minutes', 0)
        })
    
    # Write to CSV
    if csv_data:
        df = pd.DataFrame(csv_data)
        df.to_csv(csv_file, index=False)

async def write_correlation_csv(correlation_results: Dict, csv_file: Path):
    """Write temporal correlation analysis to CSV format"""
    csv_data = []
    
    # Overall correlation summary
    summary = correlation_results.get('summary', {})
    csv_data.append({
        'analysis_type': 'summary',
        'metric': 'total_analysis_periods',
        'value': summary.get('total_analysis_periods', 0),
        'description': 'Total time periods analyzed',
        'time_window': '',
        'correlation_coefficient': '',
        'strength': '',
        'direction': '',
        'insight': ''
    })
    
    csv_data.append({
        'analysis_type': 'summary', 
        'metric': 'total_correlations_found',
        'value': summary.get('total_correlations_found', 0),
        'description': 'Total significant correlations found',
        'time_window': '',
        'correlation_coefficient': '',
        'strength': '',
        'direction': '',
        'insight': ''
    })
    
    csv_data.append({
        'analysis_type': 'summary',
        'metric': 'correlation_samples',
        'value': summary.get('correlation_samples', 0),
        'description': 'Total correlation data points',
        'time_window': '',
        'correlation_coefficient': '',
        'strength': '',
        'direction': '',
        'insight': ''
    })
    
    # Temporal analysis periods
    temporal_analysis = correlation_results.get('temporal_analysis', {})
    for period in temporal_analysis.get('analysis_periods', []):
        time_window = period.get('time_window', '')
        perf_issues = period.get('performance_issues', {})
        infra_metrics = period.get('infrastructure_metrics', {})
        correlation_analysis = period.get('correlation_analysis', {})
        
        # Performance issues row
        csv_data.append({
            'analysis_type': 'time_period',
            'metric': 'performance_issues',
            'value': perf_issues.get('sla_violations', 0),
            'description': f'SLA violations in time window',
            'time_window': time_window,
            'correlation_coefficient': '',
            'strength': '',
            'direction': '',
            'insight': f"Avg RT: {perf_issues.get('avg_response_time', 0):.0f}ms, SLA violations: {perf_issues.get('sla_violation_rate', 0):.1f}%"
        })
        
        # Infrastructure metrics row
        csv_data.append({
            'analysis_type': 'time_period',
            'metric': 'infrastructure_metrics',
            'value': infra_metrics.get('avg_cpu', 0),
            'description': f'CPU utilization in time window',
            'time_window': time_window,
            'correlation_coefficient': '',
            'strength': '',
            'direction': '',
            'insight': f"CPU: {infra_metrics.get('avg_cpu', 0):.1f}%, Memory: {infra_metrics.get('avg_memory', 0):.1f}%"
        })
        
        # Correlation analysis row
        csv_data.append({
            'analysis_type': 'time_period',
            'metric': 'correlation_analysis',
            'value': 1 if correlation_analysis.get('cpu_constraint') else 0,
            'description': f'Resource constraints in time window',
            'time_window': time_window,
            'correlation_coefficient': '',
            'strength': '',
            'direction': '',
            'insight': f"CPU constraint: {correlation_analysis.get('cpu_constraint', False)}, Memory constraint: {correlation_analysis.get('memory_constraint', False)}"
        })
    
    # Individual significant correlations
    for corr in correlation_results.get('significant_correlations', []):
        csv_data.append({
            'analysis_type': 'significant_correlation',
            'metric': corr.get('type', ''),
            'value': corr.get('correlation_coefficient', 0),
            'description': corr.get('interpretation', ''),
            'time_window': '',
            'correlation_coefficient': corr.get('correlation_coefficient', 0),
            'strength': corr.get('strength', ''),
            'direction': corr.get('direction', ''),
            'insight': f"Correlation: {corr.get('correlation_coefficient', 0):.3f}, {corr.get('strength', '')} {corr.get('direction', '')}"
        })
    
    # Correlation matrix data
    correlation_matrix = correlation_results.get('correlation_matrix', {})
    if correlation_matrix:
        csv_data.append({
            'analysis_type': 'correlation_matrix',
            'metric': 'cpu_response_time_correlation',
            'value': correlation_matrix.get('cpu_response_time_correlation', 0),
            'description': 'Overall CPU utilization vs response time correlation',
            'time_window': '',
            'correlation_coefficient': correlation_matrix.get('cpu_response_time_correlation', 0),
            'strength': get_correlation_strength(correlation_matrix.get('cpu_response_time_correlation', 0)),
            'direction': get_correlation_direction(correlation_matrix.get('cpu_response_time_correlation', 0)),
            'insight': f"Based on {correlation_matrix.get('total_correlation_samples', 0)} correlation samples"
        })
        
        csv_data.append({
            'analysis_type': 'correlation_matrix',
            'metric': 'memory_response_time_correlation',
            'value': correlation_matrix.get('memory_response_time_correlation', 0),
            'description': 'Overall memory utilization vs response time correlation',
            'time_window': '',
            'correlation_coefficient': correlation_matrix.get('memory_response_time_correlation', 0),
            'strength': get_correlation_strength(correlation_matrix.get('memory_response_time_correlation', 0)),
            'direction': get_correlation_direction(correlation_matrix.get('memory_response_time_correlation', 0)),
            'insight': f"Based on {correlation_matrix.get('total_correlation_samples', 0)} correlation samples"
        })
    
    # Write to CSV
    if csv_data:
        df = pd.DataFrame(csv_data)
        df.to_csv(csv_file, index=False)

def write_anomalies_csv(anomalies: Dict, csv_file: Path):
    """Write detected anomalies to CSV"""
    # Implementation for anomalies CSV
    pass

# -----------------------------------------------
# Formatting functions
# -----------------------------------------------
def format_performance_markdown(analysis: Dict, test_run_id: str) -> str:
    """Format performance analysis as markdown report"""
    
    overall = analysis.get('overall_stats', {})
    sla_analysis = analysis.get('sla_analysis', {})
    
    # Handle case where overall stats might have an error
    if 'error' in overall:
        total_samples = 'N/A'
        success_rate = 'N/A' 
        avg_rt = 'N/A'
    else:
        total_samples = f"{overall.get('total_samples', 'N/A'):,}"
        success_rate = f"{overall.get('success_rate', 'N/A'):.2f}%"
        avg_rt = f"{overall.get('avg_response_time', 'N/A'):.0f} ms"
    
    md_content = f"""# Performance Analysis Report - Run {test_run_id}

## Test Summary
- **Total Samples**: {total_samples}
- **Success Rate**: {success_rate}
- **Average Response Time**: {avg_rt}
- **Test Duration**: {overall.get('test_duration', 'N/A')} seconds

## Response Time Statistics
| Metric | Value (ms) |
|--------|------------|
| Minimum | {overall.get('min_response_time', 'N/A')} |
| Average | {overall.get('avg_response_time', 'N/A'):.0f} |
| Median | {overall.get('median_response_time', 'N/A'):.0f} |
| 95th Percentile | {overall.get('p95_response_time', 'N/A'):.0f} |
| 99th Percentile | {overall.get('p99_response_time', 'N/A'):.0f} |
| Maximum | {overall.get('max_response_time', 'N/A'):,} |

## SLA Analysis
- **SLA Threshold**: {sla_analysis.get('sla_threshold_ms', 'N/A')} ms
- **Compliance Rate**: {sla_analysis.get('compliance_rate', 'N/A'):.1f}%
- **APIs Meeting SLA**: {sla_analysis.get('compliant_apis', 'N/A')} / {sla_analysis.get('total_apis', 'N/A')}

"""
    
    # SLA Violations
    violations = sla_analysis.get('violations', [])
    if violations:
        md_content += "## SLA Violations\n\n"
        md_content += "| API Name | Percentile Value | SLA Threshold | Violation Amount | Violation % |\n"
        md_content += "|----------|-----------------|---------------|------------------|-------------|\n"
        
        for violation in violations:
            md_content += (
                f"| {violation['api_name']} "
                f"| {violation['percentile_value']:.0f} ms ({violation.get('sla_unit', 'P90')}) "
                f"| {violation['sla_threshold']:.0f} ms "
                f"| {violation['violation_amount']:.0f} ms "
                f"| {violation['violation_percentage']:.1f}% |\n"
            )
        md_content += "\n"
    
    # Top APIs by Response Time
    api_analysis = analysis.get('api_analysis', {})
    if api_analysis:
        sorted_apis = sorted(api_analysis.items(), 
                           key=lambda x: x[1].get('avg_response_time', 0), 
                           reverse=True)[:10]
        
        md_content += "## Top 10 Slowest APIs\n\n"
        md_content += "| API Name | Avg Response (ms) | Samples | Success Rate | SLA Status |\n"
        md_content += "|----------|-------------------|---------|--------------|------------|\n"
        
        for api_name, stats in sorted_apis:
            sla_status = "✅ Pass" if stats.get('sla_compliant') else "❌ Fail"
            md_content += f"| {api_name[:50]}{'...' if len(api_name) > 50 else ''} | {stats.get('avg_response_time', 'N/A'):.0f} "
            md_content += f"| {stats.get('samples', 'N/A')} | {stats.get('success_rate', 'N/A'):.2f}% | {sla_status} |\n"
    
    md_content += f"\n---\n*Generated: {analysis.get('analysis_timestamp', 'N/A')}*"
    
    return md_content

def format_infrastructure_markdown(analysis: Dict, test_run_id: str) -> str:
    """Format infrastructure analysis as markdown report"""
    
    infrastructure_summary = analysis.get('infrastructure_summary', {})
    resource_insights = analysis.get('resource_insights', {})
    assumptions = analysis.get('assumptions_made', [])
    
    # Basic stats
    k8s_summary = infrastructure_summary.get('kubernetes_summary', {})
    host_summary = infrastructure_summary.get('host_summary', {})
    
    total_entities = k8s_summary.get('total_entities', 0)
    total_containers = k8s_summary.get('total_containers', 0)
    total_hosts = host_summary.get('total_hosts', 0)
    
    md_content = f"""# Infrastructure Analysis Report - Run {test_run_id}

## Infrastructure Overview
- **Total Environments**: {infrastructure_summary.get('total_environments', 0)}
- **Kubernetes Entities**: {total_entities} ({total_containers} containers)
- **Host Systems**: {total_hosts}

## Resource Utilization Summary

### High Utilization (Needs Attention)
"""
    
    high_util = resource_insights.get('high_utilization', [])
    if high_util:
        md_content += "| Resource | Type | Peak Usage | Threshold | Recommendation |\n"
        md_content += "|----------|------|------------|-----------|----------------|\n"
        
        for item in high_util[:10]:  # Limit to top 10
            md_content += f"| {item.get('resource', 'N/A')} | {item.get('type', 'N/A')} | {item.get('peak_utilization', 0):.1f}% | {item.get('threshold', 0)}% | {item.get('recommendation', 'N/A')[:50]}... |\n"
    else:
        md_content += "✅ No resources showing high utilization\n"
    
    md_content += "\n### Under-Utilized Resources (Cost Optimization)\n"
    
    low_util = resource_insights.get('low_utilization', [])
    if low_util:
        md_content += "| Resource | Type | Avg Usage | Threshold | Recommendation |\n"
        md_content += "|----------|------|-----------|-----------|----------------|\n"
        
        for item in low_util[:10]:  # Limit to top 10
            md_content += f"| {item.get('resource', 'N/A')} | {item.get('type', 'N/A')} | {item.get('avg_utilization', 0):.1f}% | {item.get('threshold', 0)}% | {item.get('recommendation', 'N/A')[:50]}... |\n"
    else:
        md_content += "✅ No significantly under-utilized resources detected\n"
    
    # Well-sized resources summary
    right_sized = resource_insights.get('right_sized', [])
    md_content += f"\n### Well-Utilized Resources: {len(right_sized)}\n"
    
    # Kubernetes details
    if k8s_summary:
        md_content += f"\n## Kubernetes Analysis\n"
        md_content += f"- **K8s Entities Analyzed**: {total_entities}\n"
        md_content += f"- **Total Containers**: {total_containers}\n"
        md_content += f"- **Environments**: {', '.join(k8s_summary.get('environments', []))}\n"
    
    # Host details
    if host_summary:
        md_content += f"\n## Host Analysis\n"
        md_content += f"- **Hosts Analyzed**: {total_hosts}\n"
        md_content += f"- **Environments**: {', '.join(host_summary.get('environments', []))}\n"
    
    # Configuration notes
    if assumptions:
        md_content += "\n## Configuration Notes\n\n"
        md_content += "⚠️ The following resource configuration notes were identified during analysis:\n\n"
        for assumption in assumptions[:10]:  # Limit notes list
            md_content += f"- {assumption}\n"
        
        if len(assumptions) > 10:
            md_content += f"- ... and {len(assumptions) - 10} more notes\n"
        
        md_content += "\n💡 **Note**: Resource utilization percentages are unavailable when pod/container-level or host-level limits are not defined.\n"
    
    md_content += f"\n---\n*Generated: {analysis.get('analysis_timestamp', 'N/A')}*"
    
    return md_content

def format_correlation_markdown(correlation_results: Dict) -> str:
    """Format temporal correlation analysis as comprehensive markdown report"""
    
    test_run_id = correlation_results.get('test_run_id', 'Unknown')
    summary = correlation_results.get('summary', {})
    significant_correlations = correlation_results.get('significant_correlations', [])
    insights = correlation_results.get('insights', [])
    temporal_analysis = correlation_results.get('temporal_analysis', {})
    correlation_matrix = correlation_results.get('correlation_matrix', {})
    
    md_content = f"""# Temporal Correlation Analysis Report - Run {test_run_id}

## Analysis Summary

- **Analysis Periods**: {summary.get('total_analysis_periods', 0)}
- **Total Correlations Found**: {summary.get('total_correlations_found', 0)}
- **Strong Correlations**: {summary.get('strong_correlations', 0)}
- **Moderate Correlations**: {summary.get('moderate_correlations', 0)}
- **Correlation Samples**: {summary.get('correlation_samples', 0)}
- **Correlation Threshold**: {correlation_results.get('correlation_threshold', 0.3)}

## Key Findings

"""
    
    if insights:
        for insight in insights:
            md_content += f"- {insight}\n"
    else:
        md_content += "- No significant correlations identified in this temporal analysis\n"
    
    # Temporal Analysis Periods
    analysis_periods = temporal_analysis.get('analysis_periods', [])
    if analysis_periods:
        md_content += "\n## Time Periods with Issues\n\n"
        md_content += "| Time Window | Avg Response Time | SLA Violations | Avg CPU | Avg Memory | CPU Constraint | Memory Constraint |\n"
        md_content += "|-------------|-------------------|----------------|---------|------------|----------------|-------------------|\n"
        
        for period in analysis_periods[:10]:  # Limit to top 10 periods
            time_window = period.get('time_window', 'N/A')
            perf_issues = period.get('performance_issues', {})
            infra_metrics = period.get('infrastructure_metrics', {})
            correlation_analysis = period.get('correlation_analysis', {})
            
            # Truncate time window for display
            if len(time_window) > 35:
                time_window = time_window[:32] + "..."
            
            md_content += f"| {time_window} "
            md_content += f"| {perf_issues.get('avg_response_time', 0):.0f}ms "
            md_content += f"| {perf_issues.get('sla_violations', 0)} "
            md_content += f"| {infra_metrics.get('avg_cpu', 0):.1f}% "
            md_content += f"| {infra_metrics.get('avg_memory', 0):.1f}% "
            md_content += f"| {'✅' if correlation_analysis.get('cpu_constraint') else '❌'} "
            md_content += f"| {'✅' if correlation_analysis.get('memory_constraint') else '❌'} |\n"
        
        if len(analysis_periods) > 10:
            md_content += f"\n*... and {len(analysis_periods) - 10} more periods with issues*\n"
    
    # Significant Correlations
    md_content += "\n## Significant Correlations\n\n"
    
    if significant_correlations:
        md_content += "| Correlation Type | Coefficient | Strength | Direction | Interpretation |\n"
        md_content += "|------------------|-------------|----------|-----------|----------------|\n"
        
        for corr in significant_correlations:
            md_content += f"| {corr.get('type', 'N/A').replace('_', ' ').title()} "
            md_content += f"| {corr.get('correlation_coefficient', 0):.3f} "
            md_content += f"| {corr.get('strength', 'N/A').title()} "
            md_content += f"| {corr.get('direction', 'N/A').title()} "
            md_content += f"| {corr.get('interpretation', 'N/A')} |\n"
    else:
        md_content += "No significant correlations found above the threshold.\n"
    
    # Overall Correlation Matrix
    if correlation_matrix:
        md_content += "\n## Overall Correlation Analysis\n\n"
        md_content += "### Resource-Performance Correlations\n\n"
        md_content += "**What is Correlation?**\n"
        md_content += "Correlation measures the relationship between infrastructure resources and API performance:\n"
        md_content += "- **+1.0**: Perfect positive correlation (high CPU/Memory → slow APIs)\n"
        md_content += "- **0.0**: No relationship (resource usage doesn't affect performance)\n"
        md_content += "- **-1.0**: Perfect negative correlation (high CPU/Memory → fast APIs)\n"
        md_content += "- **Threshold**: ±0.3 or higher indicates a significant relationship worth investigating\n\n"
        
        cpu_rt_corr = correlation_matrix.get('cpu_response_time_correlation', 0)
        mem_rt_corr = correlation_matrix.get('memory_response_time_correlation', 0)
        cpu_sla_corr = correlation_matrix.get('cpu_sla_violations_correlation', 0)
        mem_sla_corr = correlation_matrix.get('memory_sla_violations_correlation', 0)
        
        # Add interpretation helpers
        def interpret_corr(value, resource, metric):
            abs_val = abs(value)
            if abs_val >= 0.7:
                strength = "**Strong**"
            elif abs_val >= 0.3:
                strength = "**Moderate**"
            else:
                strength = "Weak"
            
            if value > 0.3:
                return f"{strength} positive correlation: Higher {resource} usage → {metric}"
            elif value < -0.3:
                return f"{strength} negative correlation: Higher {resource} usage → Better {metric} (unexpected)"
            else:
                return f"{strength}: No significant relationship between {resource} and {metric}"
        
        md_content += f"**CPU vs Response Time**: {cpu_rt_corr:.3f}\n"
        md_content += f"  - {interpret_corr(cpu_rt_corr, 'CPU', 'slower response times')}\n\n"
        
        md_content += f"**Memory vs Response Time**: {mem_rt_corr:.3f}\n"
        md_content += f"  - {interpret_corr(mem_rt_corr, 'Memory', 'slower response times')}\n\n"
        
        md_content += f"**CPU vs SLA Violations**: {cpu_sla_corr:.3f}\n"
        md_content += f"  - {interpret_corr(cpu_sla_corr, 'CPU', 'more SLA violations')}\n\n"
        
        md_content += f"**Memory vs SLA Violations**: {mem_sla_corr:.3f}\n"
        md_content += f"  - {interpret_corr(mem_sla_corr, 'Memory', 'more SLA violations')}\n\n"
        
        md_content += f"**Analysis Details:**\n"
        md_content += f"- Total time windows analyzed: {correlation_matrix.get('analysis_windows', 0)}\n"
        md_content += f"- Correlation data points: {correlation_matrix.get('total_correlation_samples', 0)}\n"
    
    # Detailed Time Period Analysis
    if analysis_periods:
        md_content += "\n## Detailed Time Period Analysis\n\n"
        
        # Find periods with highest response times
        sorted_periods = sorted(analysis_periods, 
                              key=lambda x: x.get('performance_issues', {}).get('avg_response_time', 0), 
                              reverse=True)[:5]
        
        if sorted_periods:
            md_content += "### Top 5 Periods with Highest Response Times\n\n"
            for i, period in enumerate(sorted_periods, 1):
                time_window = period.get('time_window', 'N/A')
                perf_issues = period.get('performance_issues', {})
                infra_metrics = period.get('infrastructure_metrics', {})
                api_breakdown = period.get('api_breakdown', [])
                
                md_content += f"#### {i}. {time_window}\n"
                md_content += f"- **Average Response Time**: {perf_issues.get('avg_response_time', 0):.0f}ms\n"
                md_content += f"- **Max Response Time**: {perf_issues.get('max_response_time', 0):.0f}ms\n"
                md_content += f"- **SLA Violations**: {perf_issues.get('sla_violations', 0)} ({perf_issues.get('sla_violation_rate', 0):.1f}%)\n"
                md_content += f"- **Total Requests**: {perf_issues.get('total_requests', 0)}\n"
                md_content += f"- **CPU Utilization**: {infra_metrics.get('avg_cpu', 0):.1f}%\n"
                md_content += f"- **Memory Utilization**: {infra_metrics.get('avg_memory', 0):.1f}%\n"
                
                # Add slowest APIs during this period
                if api_breakdown:
                    md_content += f"\n**Slowest APIs During This Period:**\n"
                    for j, api_info in enumerate(api_breakdown[:5], 1):  # Top 5 APIs
                        api_name = api_info.get('api_name', 'Unknown')
                        avg_rt = api_info.get('avg_response_time', 0)
                        max_rt = api_info.get('max_response_time', 0)
                        count = api_info.get('request_count', 0)
                        violations = api_info.get('sla_violations', 0)
                        
                        # Truncate long API names
                        if len(api_name) > 80:
                            api_name = api_name[:77] + "..."
                        
                        md_content += f"  {j}. `{api_name}`\n"
                        md_content += f"     - Avg: {avg_rt:.0f}ms | Max: {max_rt:.0f}ms | Requests: {count} | Violations: {violations}\n"
                
                md_content += "\n"
    
    # Analysis methodology
    md_content += "\n## Analysis Methodology\n\n"
    md_content += "This temporal correlation analysis:\n"
    md_content += "- Divides the test period into 1-minute time windows\n"
    md_content += "- Analyzes each window for performance issues and resource constraints\n"
    md_content += "- Correlates infrastructure metrics with performance degradation\n"
    md_content += "- Identifies patterns between CPU/memory usage and response times\n"
    md_content += f"- Uses a correlation threshold of {correlation_results.get('correlation_threshold', 0.3)} for significance\n"
    
    # Recommendations
    if significant_correlations or analysis_periods:
        md_content += "\n## Recommendations\n\n"
        
        # CPU-related recommendations
        cpu_correlations = [c for c in significant_correlations if 'cpu' in c.get('type', '')]
        if cpu_correlations:
            md_content += "### CPU Optimization\n"
            for corr in cpu_correlations:
                strength = corr.get('strength', 'unknown')
                direction = corr.get('direction', 'unknown')
                coefficient = corr.get('correlation_coefficient', 0)
                
                if direction == 'positive' and strength in ['strong', 'moderate']:
                    md_content += f"- **CPU Constraint Detected**: Consider increasing CPU allocation or optimizing CPU-intensive operations (correlation: {coefficient:.3f})\n"
                elif direction == 'negative':
                    md_content += f"- **CPU Underutilization**: Current CPU allocation may be excessive (correlation: {coefficient:.3f})\n"
        
        # Memory-related recommendations  
        memory_correlations = [c for c in significant_correlations if 'memory' in c.get('type', '')]
        if memory_correlations:
            md_content += "\n### Memory Optimization\n"
            for corr in memory_correlations:
                strength = corr.get('strength', 'unknown')
                direction = corr.get('direction', 'unknown')
                coefficient = corr.get('correlation_coefficient', 0)
                
                if direction == 'positive' and strength in ['strong', 'moderate']:
                    md_content += f"- **Memory Constraint Detected**: Consider increasing memory allocation or optimizing memory usage (correlation: {coefficient:.3f})\n"
                elif direction == 'negative':
                    md_content += f"- **Memory Underutilization**: Current memory allocation may be excessive (correlation: {coefficient:.3f})\n"
        
        # Time period recommendations
        if analysis_periods:
            cpu_constrained = sum(1 for p in analysis_periods if p.get('correlation_analysis', {}).get('cpu_constraint'))
            memory_constrained = sum(1 for p in analysis_periods if p.get('correlation_analysis', {}).get('memory_constraint'))
            
            if cpu_constrained > 0:
                md_content += f"\n### Infrastructure Scaling\n"
                md_content += f"- **CPU Scaling**: {cpu_constrained} time periods showed CPU constraints - consider auto-scaling or resource increases\n"
            
            if memory_constrained > 0:
                md_content += f"- **Memory Scaling**: {memory_constrained} time periods showed memory constraints - consider memory optimization or scaling\n"
    
    md_content += f"\n---\n*Generated: {correlation_results.get('analysis_timestamp', 'N/A')}*"
    
    return md_content

def format_anomalies_markdown(anomalies: Dict) -> str:
    """Format anomaly detection as markdown"""
    # Implementation for markdown formatting
    return "# Anomaly Detection\n\n"

def format_bottlenecks_markdown(bottlenecks: Dict) -> str:
    """Format bottleneck analysis as markdown.
    
    Delegates to the full implementation in services.bottleneck_analyzer
    when called with the complete result dict.  Falls back to a simple
    stub for legacy callers that pass minimal data.
    """
    # If the dict looks like a full bottleneck_analysis result, delegate
    if "summary" in bottlenecks and "findings" in bottlenecks:
        from services.bottleneck_analyzer import format_bottleneck_markdown
        return format_bottleneck_markdown(bottlenecks)

    # Minimal fallback for legacy / simple dicts
    md = "# Bottleneck Analysis\n\n"
    for bn in bottlenecks.get("bottlenecks", []):
        md += f"- **{bn.get('bottleneck_type', 'unknown')}**: {bn.get('evidence', 'N/A')}\n"
    return md

def format_comparison_markdown(comparison_results: Dict) -> str:
    """Format test run comparison as markdown"""
    # Implementation for markdown formatting
    return "# Test Run Comparison\n\n"

def format_executive_markdown(summary: Dict) -> str:
    """Format executive summary as markdown"""
    # Implementation for markdown formatting
    return "# Executive Summary\n\n"

# -----------------------------------------------
# Helper functions
# -----------------------------------------------
def get_correlation_strength(correlation: float) -> str:
    """Determine correlation strength"""
    abs_corr = abs(correlation)
    if abs_corr > 0.7:
        return "strong"
    elif abs_corr > 0.3:
        return "moderate"
    else:
        return "weak"

def get_correlation_direction(correlation: float) -> str:
    """Determine correlation direction"""
    return "positive" if correlation > 0 else "negative" if correlation < 0 else "none"
