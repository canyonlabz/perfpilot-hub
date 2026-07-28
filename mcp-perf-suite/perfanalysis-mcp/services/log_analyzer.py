# services/log_analyzer.py
import os
import re
import json
import csv
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from collections import defaultdict
from fastmcp import Context
from dotenv import load_dotenv
from utils.config import load_config
from utils.file_processor import (
    write_json_output,
    write_csv_output,
    write_markdown_output
)

# Load configuration and environment
load_dotenv()
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
PA_CONFIG = CONFIG.get('perf_analysis', {})
LOAD_TOOL = PA_CONFIG.get('load_tool', 'blazemeter').lower()
APM_TOOL = PA_CONFIG.get('apm_tool', 'datadog').lower()
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', '../artifacts'))

# ============================================================================
# MAIN MCP TOOL FUNCTION
# ============================================================================

async def analyze_logs(test_run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Analyze log files from load testing tools (JMeter/BlazeMeter) and APM tools (Datadog).
    
    This function identifies errors, groups them by type and API/request, correlates with
    existing performance and infrastructure analyses, and generates comprehensive output files.
    
    Args:
        test_run_id: The unique test run identifier
        ctx: FastMCP workflow context for chaining
    
    Returns:
        Dictionary containing:
            - success: Boolean indicating if analysis completed
            - test_run_id: The test run identifier
            - total_issues: Total number of log issues identified
            - issues_by_source: Breakdown of issues by source (jmeter, datadog)
            - output_files: Paths to generated CSV, JSON, and Markdown files
            - correlations: Summary of correlations with performance/infrastructure analyses
            - message: Status message
    
    Raises:
        FileNotFoundError: If required log files are not found
        ValueError: If config.yaml is missing required fields
    
    Note:
        - Requires artifacts/{test_run_id}/blazemeter/jmeter.log (if load_tool is blazemeter)
        - Requires artifacts/{test_run_id}/datadog/logs_*.csv files (if apm_tool is datadog)
        - Outputs to artifacts/{test_run_id}/analysis/log_analysis.[csv|json|md]
    """
    try:
        # Load configuration
        config = load_config()
        
        # Determine repo root and paths
        artifacts_base = ARTIFACTS_PATH / test_run_id
        analysis_path = artifacts_base / "analysis"
        analysis_path.mkdir(parents=True, exist_ok=True)
        
        # Validate required directories exist
        if not artifacts_base.exists():
            return {
                "success": False,
                "test_run_id": test_run_id,
                "error": f"Artifacts directory not found: {artifacts_base}",
                "message": "Please ensure test artifacts exist before running log analysis"
            }
        
        # Initialize results collection
        all_log_issues = []
        issues_by_source = {}
        
        # ============================
        # Analyze Load Testing Logs
        # ============================
        if LOAD_TOOL in ["blazemeter", "jmeter"]:
            blazemeter_dir = artifacts_base / "blazemeter"
            # Glob matches: jmeter.log (single-session) AND jmeter-1.log, jmeter-2.log, etc. (multi-session)
            jmeter_logs = sorted(blazemeter_dir.glob("jmeter*.log"))
            
            if jmeter_logs:
                for jmeter_log_path in jmeter_logs:
                    jmeter_issues = await analyze_jmeter_log(
                        jmeter_log_path, 
                        test_run_id, 
                        CONFIG, 
                        ctx
                    )
                    all_log_issues.extend(jmeter_issues)
                issues_by_source["jmeter"] = len([
                    issue for issue in all_log_issues
                    if issue.get("source") == "jmeter"
                ])
                await ctx.info(f"Found {issues_by_source['jmeter']} JMeter log issues across {len(jmeter_logs)} log file(s)")
            else:
                await ctx.warning(f"No JMeter logs found in: {blazemeter_dir}")
                issues_by_source["jmeter"] = 0
        
        # ============================
        # Analyze APM Tool Logs
        # ============================
        if APM_TOOL == "datadog":
            datadog_logs_path = artifacts_base / "datadog"
            
            if datadog_logs_path.exists():
                await ctx.info(f"Analyzing Datadog logs in: {datadog_logs_path}")
                datadog_issues = await analyze_datadog_logs(
                    datadog_logs_path, 
                    test_run_id, 
                    CONFIG, 
                    ctx
                )
                all_log_issues.extend(datadog_issues)
                issues_by_source["datadog"] = len(datadog_issues)
                await ctx.info(f"Found {len(datadog_issues)} Datadog log issues")
            else:
                await ctx.warning(f"Datadog logs directory not found: {datadog_logs_path}")
                issues_by_source["datadog"] = 0
        
        # ============================
        # Correlate with Existing Analyses
        # ============================
        correlations = {}
        
        # Correlate with performance analysis
        perf_analysis_path = analysis_path / "performance_analysis.json"
        if perf_analysis_path.exists():
            perf_correlations = await correlate_with_performance_analysis(
                all_log_issues, 
                perf_analysis_path,
                ctx
            )
            correlations["performance"] = perf_correlations
        else:
            await ctx.info("Performance analysis not found, skipping correlation")
            correlations["performance"] = {"available": False}
        
        # Correlate with infrastructure analysis
        infra_analysis_path = analysis_path / "infrastructure_analysis.json"
        if infra_analysis_path.exists():
            infra_correlations = await correlate_with_infrastructure_analysis(
                all_log_issues, 
                infra_analysis_path,
                ctx
            )
            correlations["infrastructure"] = infra_correlations
        else:
            await ctx.info("Infrastructure analysis not found, skipping correlation")
            correlations["infrastructure"] = {"available": False}
        
        # ============================
        # Generate Output Files
        # ============================
        output_files = await generate_log_analysis_outputs(
            all_log_issues,
            correlations,
            analysis_path,
            test_run_id,
            config,
            ctx
        )
        
        # ============================
        # Return Summary
        # ============================
        return {
            "success": True,
            "test_run_id": test_run_id,
            "total_issues": len(all_log_issues),
            "issues_by_source": issues_by_source,
            "output_files": output_files,
            "correlations_summary": {
                "performance_correlation": correlations.get("performance", {}).get("available", False),
                "infrastructure_correlation": correlations.get("infrastructure", {}).get("available", False)
            },
            "message": f"Log analysis complete. Found {len(all_log_issues)} total issues across {len(issues_by_source)} sources."
        }
        
    except Exception as e:
        await ctx.error(f"Error during log analysis: {str(e)}")
        return {
            "success": False,
            "test_run_id": test_run_id,
            "error": str(e),
            "message": "Log analysis failed. Check error details."
        }


# ============================================================================
# JMETER LOG ANALYSIS
# ============================================================================

async def analyze_jmeter_log(
    log_path: Path, 
    test_run_id: str, 
    config: Dict, 
    ctx: Context
) -> List[Dict[str, Any]]:
    """
    Analyze JMeter log file for errors, exceptions, and performance issues.
    
    Uses chunked reading for large files (up to 100MB+) and pattern matching
    based on JMeter log analysis techniques.
    
    Args:
        log_path: Path to jmeter.log file
        test_run_id: The test run identifier
        config: Configuration dictionary from config.yaml
        ctx: FastMCP context for logging
    
    Returns:
        List of dictionaries, each representing a log issue with fields:
            - source: "jmeter"
            - timestamp: When error occurred
            - error_type: Categorized error type
            - severity: Critical/High/Medium/Low
            - api_request: API or request involved
            - error_message: The actual error message
            - context: Additional context lines
            - line_number: Line number in log file
            - count: Number of similar occurrences
    """
    issues = []
    error_patterns = compile_jmeter_error_patterns()
    
    # For large files, use chunked reading
    chunk_size = 1024 * 1024  # 1MB chunks
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            line_number = 0
            context_buffer = []  # Keep last 5 lines for context
            max_context = 5
            
            for line in f:
                line_number += 1
                context_buffer.append(line)
                
                # Keep only last max_context lines
                if len(context_buffer) > max_context:
                    context_buffer.pop(0)
                
                # Check if line matches any error pattern
                for pattern_name, pattern_config in error_patterns.items():
                    if pattern_config["regex"].search(line):
                        # Extract error details
                        issue = categorize_jmeter_error(
                            line, 
                            context_buffer.copy(), 
                            pattern_name,
                            line_number,
                            ctx
                        )
                        issues.append(issue)
        
        # Group similar errors together
        grouped_issues = group_errors_by_type_and_api(issues)
        
        await ctx.info(f"JMeter log analysis complete. Found {len(grouped_issues)} unique issue groups.")
        
        return grouped_issues
        
    except Exception as e:
        await ctx.error(f"Error reading JMeter log file: {str(e)}")
        return []


def compile_jmeter_error_patterns() -> Dict[str, Dict]:
    """
    Compile regex patterns for JMeter error detection.
    
    Based on jmeter-log-analysis-techniques.md patterns.
    
    Returns:
        Dictionary mapping pattern names to compiled regex and metadata
    """
    patterns = {
        "fatal_error": {
            "regex": re.compile(r"FATAL", re.IGNORECASE),
            "severity": "Critical",
            "error_type": "Fatal Error"
        },
        "error_general": {
            "regex": re.compile(r"\bERROR\b", re.IGNORECASE),
            "severity": "High",
            "error_type": "General Error"
        },
        "exception": {
            "regex": re.compile(r"Exception|Error:", re.IGNORECASE),
            "severity": "High",
            "error_type": "Exception"
        },
        "connection_error": {
            "regex": re.compile(r"Connection refused|Connection reset|Connection timeout|SocketException", re.IGNORECASE),
            "severity": "High",
            "error_type": "Network Connection Error"
        },
        "http_error": {
            "regex": re.compile(r"HTTP.*(?:500|502|503|504|404|403|401)", re.IGNORECASE),
            "severity": "High",
            "error_type": "HTTP Error"
        },
        "timeout": {
            "regex": re.compile(r"timeout|timed out", re.IGNORECASE),
            "severity": "High",
            "error_type": "Timeout"
        },
        "ssl_error": {
            "regex": re.compile(r"SSL|TLS|certificate|handshake", re.IGNORECASE),
            "severity": "Medium",
            "error_type": "SSL/TLS Error"
        },
        "thread_error": {
            "regex": re.compile(r"Thread.*(?:exception|error|failed)", re.IGNORECASE),
            "severity": "Medium",
            "error_type": "Thread Error"
        },
        "script_error": {
            "regex": re.compile(r"Variable.*not defined|null.*pointer|undefined", re.IGNORECASE),
            "severity": "High",
            "error_type": "Script/Variable Error"
        },
        "warning": {
            "regex": re.compile(r"\bWARN\b", re.IGNORECASE),
            "severity": "Low",
            "error_type": "Warning"
        }
    }
    
    return patterns


def categorize_jmeter_error(
    line: str, 
    context_lines: List[str], 
    pattern_name: str,
    line_number: int,
    ctx: Context
) -> Dict[str, Any]:
    """
    Categorize a JMeter error line and extract relevant details.
    
    Args:
        line: The log line containing the error
        context_lines: Surrounding lines for context
        pattern_name: Which pattern matched
        line_number: Line number in log file
        ctx: FastMCP context
    
    Returns:
        Dictionary with categorized error information
    """
    patterns = compile_jmeter_error_patterns()
    pattern_config = patterns.get(pattern_name, {})
    
    # Extract timestamp (JMeter format: YYYY-MM-DD HH:MM:SS,mmm)
    timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
    timestamp = timestamp_match.group(1) if timestamp_match else "Unknown"
    
    # Extract API/request information
    api_request = extract_api_from_context(line, context_lines)
    
    # Extract thread information
    thread_match = re.search(r'Thread Group \d+-\d+', line)
    thread = thread_match.group(0) if thread_match else "Unknown Thread"
    
    return {
        "source": "jmeter",
        "timestamp": timestamp,
        "error_type": pattern_config.get("error_type", "Unknown"),
        "severity": pattern_config.get("severity", "Medium"),
        "api_request": api_request,
        "error_message": line.strip(),
        "context": "\n".join(context_lines[-3:]),  # Last 3 lines of context
        "thread": thread,
        "line_number": line_number,
        "count": 1  # Will be aggregated later
    }


def extract_api_from_context(line: str, context_lines: List[str]) -> str:
    """
    Extract API endpoint or request name from log line and context.
    
    Args:
        line: Current log line
        context_lines: Surrounding context lines
    
    Returns:
        API endpoint or request identifier
    """
    # Common patterns for API extraction in JMeter logs
    api_patterns = [
        r'(?:GET|POST|PUT|DELETE|PATCH)\s+(https?://[^\s]+)',  # HTTP method + URL
        r'URL:\s*(https?://[^\s]+)',  # URL: prefix
        r'HTTPSampler:\s*([^\s]+)',  # HTTPSampler name
        r'Request:\s*([^\s]+)',  # Request name
    ]
    
    # Check current line first
    for pattern in api_patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return match.group(1)
    
    # Check context lines
    for context_line in reversed(context_lines):
        for pattern in api_patterns:
            match = re.search(pattern, context_line, re.IGNORECASE)
            if match:
                return match.group(1)
    
    return "Unknown API"


def group_errors_by_type_and_api(issues: List[Dict]) -> List[Dict]:
    """
    Group similar errors together by error type and API.
    
    Args:
        issues: List of individual error issues
    
    Returns:
        List of grouped errors with aggregated counts
    """
    grouped = defaultdict(lambda: {
        "source": "",
        "timestamp": "",
        "error_type": "",
        "severity": "",
        "api_request": "",
        "error_message": "",
        "context": "",
        "thread": "",
        "line_numbers": [],
        "count": 0,
        "first_occurrence": None,
        "last_occurrence": None
    })
    
    for issue in issues:
        # Create grouping key based on error_type, severity, and api_request
        key = (issue["error_type"], issue["severity"], issue["api_request"])
        
        # Initialize if first occurrence
        if grouped[key]["count"] == 0:
            grouped[key].update({
                "source": issue["source"],
                "timestamp": issue["timestamp"],
                "error_type": issue["error_type"],
                "severity": issue["severity"],
                "api_request": issue["api_request"],
                "error_message": issue["error_message"],
                "context": issue["context"],
                "thread": issue["thread"],
                "first_occurrence": issue["timestamp"]
            })
        
        # Aggregate
        grouped[key]["count"] += 1
        grouped[key]["line_numbers"].append(issue["line_number"])
        grouped[key]["last_occurrence"] = issue["timestamp"]
    
    # Convert to list and format line_numbers
    result = []
    for key, data in grouped.items():
        data["line_numbers"] = ",".join(map(str, data["line_numbers"][:10]))  # Limit to first 10
        if len(data["line_numbers"]) > 10:
            data["line_numbers"] += f",... (+{len(data['line_numbers']) - 10} more)"
        result.append(data)
    
    # Sort by count (most frequent first) then by severity
    severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    result.sort(key=lambda x: (severity_order.get(x["severity"], 999), -x["count"]))
    
    return result


# ============================================================================
# DATADOG LOG ANALYSIS
# ============================================================================

async def analyze_datadog_logs(
    logs_path: Path, 
    test_run_id: str, 
    config: Dict, 
    ctx: Context
) -> List[Dict[str, Any]]:
    """
    Analyze Datadog CSV log files for errors and issues.
    
    Handles multiple log files with different query types (all_errors, service_errors, etc.).
    
    Args:
        logs_path: Path to directory containing Datadog log CSV files
        test_run_id: The test run identifier
        config: Configuration dictionary from config.yaml
        ctx: FastMCP context for logging
    
    Returns:
        List of dictionaries, each representing a log issue with fields:
            - source: "datadog"
            - env_name: Environment name
            - query_type: Type of query (e.g., "http-errors")
            - timestamp: When error occurred
            - error_type: Categorized error type
            - severity: Critical/High/Medium/Low
            - api_request: API or request involved (from custom attributes)
            - service: Service name
            - host: Host name
            - http_status_code: HTTP status code if applicable
            - error_kind: Error kind from Datadog
            - error_message: The actual error message
            - count: Number of similar occurrences
    """
    issues = []
    
    # Find all Datadog log files (logs_*_*.csv pattern)
    log_files = list(logs_path.glob("logs_*.csv"))
    
    if not log_files:
        await ctx.warning(f"No Datadog log files found in {logs_path}")
        return []
    
    for log_file in log_files:
        
        try:
            # Parse filename to extract query_type and environment
            # Format: logs_<query-type>_<env-name>.csv
            filename_parts = log_file.stem.split("_")
            if len(filename_parts) >= 3:
                query_type = "_".join(filename_parts[1:-1])  # Middle parts
                env_name = filename_parts[-1]  # Last part
            else:
                query_type = "unknown"
                env_name = "unknown"
            
            # Read CSV file
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.DictReader(f)
                
                for row_num, row in enumerate(reader, start=1):
                    # Process each log entry
                    issue = categorize_datadog_error(row, query_type, env_name, ctx)
                    if issue:
                        issues.append(issue)
            
        except Exception as e:
            await ctx.error(f"Error processing {log_file.name}: {str(e)}")
            continue
    
    # Group similar errors
    grouped_issues = group_datadog_errors(issues)
    
    await ctx.info(f"Datadog log analysis complete. Found {len(grouped_issues)} unique issue groups.")
    
    return grouped_issues


def categorize_datadog_error(
    row: Dict[str, str], 
    query_type: str, 
    env_name: str, 
    ctx: Context
) -> Optional[Dict[str, Any]]:
    """
    Categorize a Datadog log entry.
    
    Args:
        row: CSV row as dictionary with Datadog schema fields
        query_type: Type of query (e.g., "http-errors")
        env_name: Environment name
        ctx: FastMCP context
    
    Returns:
        Dictionary with categorized error information, or None if not an error
    """
    # Extract fields from Datadog schema
    status = row.get("status", "").lower()
    level = row.get("level", "").lower()
    error_kind = row.get("error_kind", "Unknown")
    http_status = row.get("http_status_code", "")
    message = row.get("message", "")
    
    # Determine if this is an error (skip non-errors)
    if status not in ["error", "critical", "alert"] and level not in ["error", "critical", "fatal"]:
        return None
    
    # Determine severity based on status/level and HTTP code
    severity = determine_datadog_severity(status, level, http_status)
    
    # Extract error type
    error_type = determine_datadog_error_type(error_kind, http_status, message, query_type)
    
    # Extract API/request from message or custom attributes
    api_request = extract_api_from_datadog_log(row, message)
    
    return {
        "source": "datadog",
        "env_name": row.get("env_name", env_name),
        "env_tag": row.get("env_tag", ""),
        "query_type": query_type,
        "timestamp": row.get("timestamp_utc", "Unknown"),
        "error_type": error_type,
        "severity": severity,
        "api_request": api_request,
        "service": row.get("service", "Unknown"),
        "host": row.get("host", "Unknown"),
        "http_status_code": http_status,
        "http_method": row.get("http_method", ""),
        "error_kind": error_kind,
        "error_message": message,
        "log_level": level,
        "count": 1  # Will be aggregated
    }


def determine_datadog_severity(status: str, level: str, http_status: str) -> str:
    """
    Determine severity level for Datadog log entry.
    
    Args:
        status: Datadog status field
        level: Datadog level field
        http_status: HTTP status code
    
    Returns:
        Severity string: Critical/High/Medium/Low
    """
    # Critical conditions
    if status in ["critical", "alert"] or level in ["critical", "fatal"]:
        return "Critical"
    
    # HTTP 5xx errors are high severity
    if http_status and http_status.startswith("5"):
        return "High"
    
    # General errors
    if status == "error" or level == "error":
        return "High"
    
    # HTTP 4xx errors are medium
    if http_status and http_status.startswith("4"):
        return "Medium"
    
    return "Medium"


def determine_datadog_error_type(
    error_kind: str, 
    http_status: str, 
    message: str, 
    query_type: str
) -> str:
    """
    Determine error type from Datadog log fields.
    
    Args:
        error_kind: Datadog error_kind field
        http_status: HTTP status code
        message: Log message
        query_type: Query type from filename
    
    Returns:
        Categorized error type string
    """
    # Use error_kind if available and meaningful
    if error_kind and error_kind != "Unknown" and error_kind.strip():
        return error_kind
    
    # Categorize by HTTP status
    if http_status:
        if http_status.startswith("5"):
            return f"HTTP {http_status} Server Error"
        elif http_status.startswith("4"):
            return f"HTTP {http_status} Client Error"
    
    # Categorize by query type
    query_type_mapping = {
        "http-errors": "HTTP Error",
        "api-errors": "API Error",
        "service-errors": "Service Error",
        "host-errors": "Host Error",
        "kubernetes-errors": "Kubernetes Error",
        "all-errors": "General Error"
    }
    
    return query_type_mapping.get(query_type, "Unknown Error")


def extract_api_from_datadog_log(row: Dict[str, str], message: str) -> str:
    """
    Extract API endpoint from Datadog log entry.
    
    Args:
        row: CSV row dictionary
        message: Log message
    
    Returns:
        API endpoint or identifier
    """
    # Try custom_attributes first (might contain URL)
    custom_attrs = row.get("custom_attributes", "")
    
    # Look for URL patterns in custom attributes or message
    url_pattern = r'(?:GET|POST|PUT|DELETE|PATCH)?\s*((?:https?://)?[^\s]+(?:/[^\s]*)?)'
    
    # Check custom attributes
    if custom_attrs:
        match = re.search(url_pattern, custom_attrs, re.IGNORECASE)
        if match:
            return match.group(1)
    
    # Check message
    match = re.search(url_pattern, message, re.IGNORECASE)
    if match:
        return match.group(1)
    
    # Fallback to service name
    return row.get("service", "Unknown API")


def group_datadog_errors(issues: List[Dict]) -> List[Dict]:
    """
    Group similar Datadog errors by error type, service, and API.
    
    Args:
        issues: List of individual Datadog error issues
    
    Returns:
        List of grouped errors with aggregated counts
    """
    grouped = defaultdict(lambda: {
        "source": "datadog",
        "env_name": "",
        "query_type": "",
        "timestamp": "",
        "error_type": "",
        "severity": "",
        "api_request": "",
        "service": "",
        "host": "",
        "http_status_code": "",
        "error_kind": "",
        "error_message": "",
        "count": 0,
        "first_occurrence": None,
        "last_occurrence": None,
        "affected_hosts": set(),
        "http_methods": set()
    })
    
    for issue in issues:
        # Group by error_type, k8s entity, api_request, and http_status_code
        key = (
            issue["error_type"], 
            issue["service"], 
            issue["api_request"],
            issue["http_status_code"]
        )
        
        # Initialize if first occurrence
        if grouped[key]["count"] == 0:
            grouped[key].update({
                "source": issue["source"],
                "env_name": issue["env_name"],
                "query_type": issue["query_type"],
                "timestamp": issue["timestamp"],
                "error_type": issue["error_type"],
                "severity": issue["severity"],
                "api_request": issue["api_request"],
                "service": issue["service"],
                "host": issue["host"],
                "http_status_code": issue["http_status_code"],
                "error_kind": issue["error_kind"],
                "error_message": issue["error_message"],
                "first_occurrence": issue["timestamp"]
            })
        
        # Aggregate
        grouped[key]["count"] += 1
        grouped[key]["last_occurrence"] = issue["timestamp"]
        grouped[key]["affected_hosts"].add(issue["host"])
        if issue.get("http_method"):
            grouped[key]["http_methods"].add(issue["http_method"])
    
    # Convert to list and format sets
    result = []
    for key, data in grouped.items():
        data["affected_hosts"] = ", ".join(list(data["affected_hosts"])[:5])  # Limit to 5
        data["http_methods"] = ", ".join(list(data["http_methods"]))
        result.append(data)
    
    # Sort by severity then count
    severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    result.sort(key=lambda x: (severity_order.get(x["severity"], 999), -x["count"]))
    
    return result


# ============================================================================
# CORRELATION WITH EXISTING ANALYSES
# ============================================================================

async def correlate_with_performance_analysis(
    log_issues: List[Dict], 
    analysis_path: Path,
    ctx: Context
) -> Dict[str, Any]:
    """
    Correlate log issues with performance analysis results.
    
    Args:
        log_issues: List of log issues identified
        analysis_path: Path to performance_analysis.json
        ctx: FastMCP context
    
    Returns:
        Dictionary containing correlation findings:
            - available: Boolean if correlation was performed
            - high_response_time_apis: APIs with both high response times and errors
            - error_impact_on_performance: Analysis of how errors affected performance
            - temporal_correlation: Time-based correlation insights
    """
    try:
        with open(analysis_path, 'r') as f:
            perf_data = json.load(f)
        
        # Extract APIs with SLA violations from performance analysis
        sla_violations = perf_data.get("sla_violations", [])
        
        # Find log issues that match APIs with SLA violations
        correlated_issues = []
        
        for issue in log_issues:
            api = issue.get("api_request", "")
            
            for violation in sla_violations:
                violation_api = violation.get("label", "")
                
                # Simple string matching (can be enhanced)
                if api in violation_api or violation_api in api:
                    correlated_issues.append({
                        "api": api,
                        "error_type": issue["error_type"],
                        "error_count": issue["count"],
                        "avg_response_time": violation.get("avg_response_time_ms"),
                        "max_response_time": violation.get("max_response_time_ms"),
                        "severity": issue["severity"]
                    })
        
        return {
            "available": True,
            "total_correlated_issues": len(correlated_issues),
            "high_response_time_apis": correlated_issues,
            "error_impact_on_performance": _analyze_error_impact(correlated_issues),
            "temporal_correlation": "Future enhancement: Not available yet"
        }
        
    except Exception as e:
        await ctx.error(f"Error correlating with performance analysis: {str(e)}")
        return {"available": False, "error": str(e)}


async def correlate_with_infrastructure_analysis(
    log_issues: List[Dict], 
    analysis_path: Path,
    ctx: Context
) -> Dict[str, Any]:
    """
    Correlate log issues with infrastructure/APM metrics analysis.
    
    Args:
        log_issues: List of log issues identified
        analysis_path: Path to infrastructure_analysis.json
        ctx: FastMCP context
    
    Returns:
        Dictionary containing correlation findings:
            - available: Boolean if correlation was performed
            - resource_constrained_hosts: Hosts with both errors and resource issues
            - service_health_correlation: Services with errors and metric anomalies
            - temporal_correlation: Time-based correlation insights
    """
    try:
        with open(analysis_path, 'r') as f:
            infra_data = json.load(f)
        
        # Extract hosts/services with resource issues
        kpi_violations = infra_data.get("kpi_violations", [])
        
        # Group log issues by host/k8s entity (using Datadog service field as entity identifier)
        issues_by_host = defaultdict(list)
        issues_by_k8s_entity = defaultdict(list)
        
        for issue in log_issues:
            if issue.get("host"):
                issues_by_host[issue["host"]].append(issue)
            if issue.get("service"):
                issues_by_k8s_entity[issue["service"]].append(issue)
        
        # Find correlated infrastructure issues
        resource_constrained = []
        
        for violation in kpi_violations:
            host = violation.get("host", "")
            metric = violation.get("metric", "")
            
            if host in issues_by_host:
                resource_constrained.append({
                    "host": host,
                    "metric": metric,
                    "threshold": violation.get("threshold"),
                    "max_value": violation.get("max_value"),
                    "error_count": sum(i["count"] for i in issues_by_host[host])
                })
        
        return {
            "available": True,
            "total_hosts_analyzed": len(issues_by_host),
            "resource_constrained_hosts": resource_constrained,
            "k8s_entity_health_correlation": _analyze_k8s_entity_health(issues_by_k8s_entity, infra_data),
            "temporal_correlation": "Future enhancement: Not available yet"
        }
        
    except Exception as e:
        await ctx.error(f"Error correlating with infrastructure analysis: {str(e)}")
        return {"available": False, "error": str(e)}


def _analyze_error_impact(correlated_issues: List[Dict]) -> Dict[str, Any]:
    """
    Analyze how errors impact performance metrics.
    
    Args:
        correlated_issues: List of correlated issues
    
    Returns:
        Dictionary with impact analysis
    """
    if not correlated_issues:
        return {"impact_detected": False}
    
    total_errors = sum(issue["error_count"] for issue in correlated_issues)
    avg_response_time = sum(issue.get("avg_response_time", 0) for issue in correlated_issues) / len(correlated_issues)
    
    return {
        "impact_detected": True,
        "total_errors_affecting_performance": total_errors,
        "avg_response_time_of_affected_apis": round(avg_response_time, 2),
        "critical_apis_affected": len([i for i in correlated_issues if i["severity"] == "Critical"])
    }


def _analyze_k8s_entity_health(
    issues_by_k8s_entity: Dict[str, List[Dict]], 
    infra_data: Dict
) -> Dict[str, Any]:
    """
    Analyze K8s entity health based on errors and infrastructure metrics.
    
    Args:
        issues_by_k8s_entity: Dictionary mapping K8s entities to their issues
        infra_data: Infrastructure analysis data
    
    Returns:
        Dictionary with K8s entity health analysis
    """
    if not issues_by_k8s_entity:
        return {"k8s_entities_analyzed": 0}
    
    unhealthy_k8s_entities = []
    
    for entity, issues in issues_by_k8s_entity.items():
        total_errors = sum(issue["count"] for issue in issues)
        critical_errors = sum(issue["count"] for issue in issues if issue["severity"] == "Critical")
        
        if critical_errors > 0:
            unhealthy_k8s_entities.append({
                "k8s_entity": entity,
                "total_errors": total_errors,
                "critical_errors": critical_errors
            })
    
    return {
        "k8s_entities_analyzed": len(issues_by_k8s_entity),
        "unhealthy_k8s_entities": unhealthy_k8s_entities,
        "k8s_entities_requiring_attention": len(unhealthy_k8s_entities)
    }


# ============================================================================
# OUTPUT GENERATION
# ============================================================================

async def generate_log_analysis_outputs(
    log_issues: List[Dict],
    correlations: Dict,
    output_path: Path,
    test_run_id: str,
    config: Dict,
    ctx: Context
) -> Dict[str, str]:
    """
    Generate CSV, JSON, and Markdown output files for log analysis.
    
    Args:
        log_issues: List of all log issues identified
        correlations: Correlation data with performance/infrastructure
        output_path: Directory path for output files
        test_run_id: Test run identifier
        config: Configuration dictionary
        ctx: FastMCP context
    
    Returns:
        Dictionary with paths to generated files:
            - csv: Path to log_analysis.csv
            - json: Path to log_analysis.json
            - markdown: Path to log_analysis.md
    """
    output_files = {}
    
    # ============================
    # Generate CSV Output
    # ============================
    csv_path = output_path / "log_analysis.csv"
    
    csv_headers = [
        "source", "timestamp", "error_type", "severity", "api_request",
        "error_message", "count", "service", "host", "http_status_code",
        "http_method", "first_occurrence", "last_occurrence", "line_numbers"
    ]
    
    try:
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=csv_headers, extrasaction='ignore')
            writer.writeheader()
            
            for issue in log_issues:
                # Ensure all required fields exist
                row = {header: issue.get(header, "") for header in csv_headers}
                writer.writerow(row)
        
        output_files["csv"] = str(csv_path)
        
    except Exception as e:
        await ctx.error(f"Error writing CSV output: {str(e)}")
    
    # ============================
    # Generate JSON Summary
    # ============================
    json_path = output_path / "log_analysis.json"
    
    # Calculate summary statistics
    summary = calculate_log_summary_statistics(log_issues, correlations, test_run_id)
    
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
        
        output_files["json"] = str(json_path)
        
    except Exception as e:
        await ctx.error(f"Error writing JSON output: {str(e)}")
    
    # ============================
    # Generate Markdown Report
    # ============================
    md_path = output_path / "log_analysis.md"
    
    try:
        markdown_content = format_log_analysis_markdown(
            log_issues, 
            correlations, 
            summary, 
            test_run_id,
            config
        )
        
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        output_files["markdown"] = str(md_path)
        
    except Exception as e:
        await ctx.error(f"Error writing Markdown output: {str(e)}")
    
    return output_files


def calculate_log_summary_statistics(
    log_issues: List[Dict],
    correlations: Dict,
    test_run_id: str
) -> Dict[str, Any]:
    """
    Calculate summary statistics for log analysis.
    
    Args:
        log_issues: List of log issues
        correlations: Correlation data
        test_run_id: Test run identifier
    
    Returns:
        Dictionary with summary statistics
    """
    # Group by source
    by_source = defaultdict(int)
    by_severity = defaultdict(int)
    by_error_type = defaultdict(int)
    
    total_error_count = 0
    
    for issue in log_issues:
        by_source[issue["source"]] += issue["count"]
        by_severity[issue["severity"]] += issue["count"]
        by_error_type[issue["error_type"]] += issue["count"]
        total_error_count += issue["count"]
    
    # Get top error types
    top_error_types = sorted(
        by_error_type.items(), 
        key=lambda x: x[1], 
        reverse=True
    )[:10]
    
    # Get critical issues
    critical_issues = [i for i in log_issues if i["severity"] == "Critical"]
    
    return {
        "test_run_id": test_run_id,
        "analysis_timestamp": datetime.now().isoformat(),
        "summary": {
            "total_unique_issues": len(log_issues),
            "total_error_occurrences": total_error_count,
            "issues_by_source": dict(by_source),
            "issues_by_severity": dict(by_severity),
            "critical_issues_count": len(critical_issues),
            "high_severity_count": by_severity.get("High", 0),
            "medium_severity_count": by_severity.get("Medium", 0),
            "low_severity_count": by_severity.get("Low", 0)
        },
        "top_error_types": [
            {"error_type": error_type, "count": count}
            for error_type, count in top_error_types
        ],
        "critical_issues": [
            {
                "error_type": issue["error_type"],
                "api_request": issue["api_request"],
                "count": issue["count"],
                "source": issue["source"]
            }
            for issue in critical_issues[:20]  # Limit to top 20
        ],
        "correlations": correlations
    }


def format_log_analysis_markdown(
    log_issues: List[Dict],
    correlations: Dict,
    summary: Dict,
    test_run_id: str,
    config: Dict
) -> str:
    """
    Format log analysis results as Markdown report.
    
    Args:
        log_issues: List of log issues
        correlations: Correlation data
        summary: Summary statistics
        test_run_id: Test run identifier
        config: Configuration dictionary
    
    Returns:
        Formatted Markdown string
    """
    md = []
    
    # Header
    md.append(f"# Log Analysis Report - {test_run_id}")
    md.append(f"\n**Analysis Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md.append(f"\n**Load Tool:** {config.get('perf_analysis', {}).get('load_tool', 'Unknown')}")
    md.append(f"\n**APM Tool:** {config.get('perf_analysis', {}).get('apm_tool', 'Unknown')}")
    md.append("\n---\n")
    
    # Executive Summary
    md.append("## Executive Summary\n")
    summary_data = summary.get("summary", {})
    md.append(f"- **Total Unique Issues:** {summary_data.get('total_unique_issues', 0)}")
    md.append(f"- **Total Error Occurrences:** {summary_data.get('total_error_occurrences', 0)}")
    md.append(f"- **Critical Issues:** {summary_data.get('critical_issues_count', 0)}")
    md.append(f"- **High Severity:** {summary_data.get('high_severity_count', 0)}")
    md.append(f"- **Medium Severity:** {summary_data.get('medium_severity_count', 0)}")
    md.append(f"- **Low Severity:** {summary_data.get('low_severity_count', 0)}\n")
    
    # Issues by Source
    md.append("## Issues by Source\n")
    for source, count in summary_data.get("issues_by_source", {}).items():
        md.append(f"- **{source.upper()}:** {count} errors")
    md.append("\n")
    
    # Top Error Types
    md.append("## Top Error Types\n")
    md.append("| Error Type | Count |")
    md.append("|------------|-------|")
    for error_type_data in summary.get("top_error_types", [])[:10]:
        md.append(f"| {error_type_data['error_type']} | {error_type_data['count']} |")
    md.append("\n")
    
    # Critical Issues
    critical_issues = summary.get("critical_issues", [])
    if critical_issues:
        md.append("## Critical Issues Requiring Immediate Attention\n")
        md.append("| Error Type | API/Request | Count | Source |")
        md.append("|------------|-------------|-------|--------|")
        for issue in critical_issues[:20]:
            md.append(f"| {issue['error_type']} | {issue['api_request']} | {issue['count']} | {issue['source']} |")
        md.append("\n")
    
    # Performance Correlation
    perf_corr = correlations.get("performance", {})
    if perf_corr.get("available"):
        md.append("## Performance Correlation Analysis\n")
        md.append(f"**Total Correlated Issues:** {perf_corr.get('total_correlated_issues', 0)}\n")
        
        high_rt_apis = perf_corr.get("high_response_time_apis", [])
        if high_rt_apis:
            md.append("### APIs with Both High Response Times and Errors\n")
            md.append("| API | Error Type | Error Count | Avg Response Time (ms) | Severity |")
            md.append("|-----|------------|-------------|------------------------|----------|")
            for item in high_rt_apis[:15]:
                md.append(f"| {item.get('api', 'N/A')} | {item.get('error_type', 'N/A')} | {item.get('error_count', 0)} | {item.get('avg_response_time', 'N/A')} | {item.get('severity', 'N/A')} |")
            md.append("\n")
        
        error_impact = perf_corr.get("error_impact_on_performance", {})
        if error_impact.get("impact_detected"):
            md.append("### Error Impact on Performance\n")
            md.append(f"- **Total Errors Affecting Performance:** {error_impact.get('total_errors_affecting_performance', 0)}")
            md.append(f"- **Average Response Time of Affected APIs:** {error_impact.get('avg_response_time_of_affected_apis', 0)} ms")
            md.append(f"- **Critical APIs Affected:** {error_impact.get('critical_apis_affected', 0)}\n")
    
    # Infrastructure Correlation
    infra_corr = correlations.get("infrastructure", {})
    if infra_corr.get("available"):
        md.append("## Infrastructure Correlation Analysis\n")
        md.append(f"**Total Hosts Analyzed:** {infra_corr.get('total_hosts_analyzed', 0)}\n")
        
        resource_constrained = infra_corr.get("resource_constrained_hosts", [])
        if resource_constrained:
            md.append("### Hosts with Resource Constraints and Errors\n")
            md.append("| Host | Metric | Threshold | Max Value | Error Count |")
            md.append("|------|--------|-----------|-----------|-------------|")
            for item in resource_constrained[:15]:
                md.append(f"| {item.get('host', 'N/A')} | {item.get('metric', 'N/A')} | {item.get('threshold', 'N/A')} | {item.get('max_value', 'N/A')} | {item.get('error_count', 0)} |")
            md.append("\n")
        
        k8s_entity_health = infra_corr.get("k8s_entity_health_correlation", {})
        unhealthy_k8s_entities = k8s_entity_health.get("unhealthy_k8s_entities", [])
        if unhealthy_k8s_entities:
            md.append("### K8s Entities Requiring Attention\n")
            md.append("| K8s Entity | Total Errors | Critical Errors |")
            md.append("|------------|--------------|-----------------|")
            for item in unhealthy_k8s_entities[:15]:
                md.append(f"| {item.get('k8s_entity', 'N/A')} | {item.get('total_errors', 0)} | {item.get('critical_errors', 0)} |")
            md.append("\n")
    
    # Recommendations
    md.append("## Recommendations\n")
    md.append(_generate_recommendations(summary, correlations))
    
    # Footer
    md.append("\n---\n")
    md.append("*This report was automatically generated by the PerfAnalysis MCP Log Analyzer.*")
    
    return "\n".join(md)


def _generate_recommendations(summary: Dict, correlations: Dict) -> str:
    """
    Generate recommendations based on log analysis findings.
    
    Args:
        summary: Summary statistics
        correlations: Correlation data
    
    Returns:
        Markdown-formatted recommendations
    """
    recommendations = []
    
    summary_data = summary.get("summary", {})
    critical_count = summary_data.get("critical_issues_count", 0)
    
    if critical_count > 0:
        recommendations.append(f"1. **Immediate Action Required:** {critical_count} critical issues detected. Review and resolve these issues before proceeding with production deployment.")
    
    perf_corr = correlations.get("performance", {})
    if perf_corr.get("available") and perf_corr.get("total_correlated_issues", 0) > 0:
        recommendations.append("2. **Performance Impact:** Errors are correlated with high response times. Investigate error-prone APIs for optimization opportunities.")
    
    infra_corr = correlations.get("infrastructure", {})
    resource_constrained = infra_corr.get("resource_constrained_hosts", [])
    if resource_constrained:
        recommendations.append("3. **Resource Constraints:** Some hosts are experiencing both errors and resource constraints. Consider scaling infrastructure or optimizing resource usage.")
    
    top_error_types = summary.get("top_error_types", [])
    if top_error_types:
        top_error = top_error_types[0]
        recommendations.append(f"4. **Most Common Error:** '{top_error['error_type']}' occurred {top_error['count']} times. Focus debugging efforts on resolving this error type first.")
    
    if not recommendations:
        recommendations.append("1. **Overall Health:** No critical issues detected. Continue monitoring for trends.")
    
    return "\n".join(recommendations)


# ============================================================================
# FUTURE ENHANCEMENTS (SCAFFOLDED)
# ============================================================================

async def analyze_temporal_error_patterns(
    log_issues: List[Dict], 
    test_duration_seconds: int,
    ctx: Context
) -> Dict[str, Any]:
    """
    Analyze when errors occur during test execution to identify temporal patterns.
    
    Future enhancement: Track error distribution over time, identify error spikes,
    correlate with test load ramp-up/down phases.
    
    Args:
        log_issues: List of log issues with timestamps
        test_duration_seconds: Total test duration
        ctx: FastMCP context
    
    Returns:
        Dictionary with temporal analysis (placeholder for now)
    """
    await ctx.info("Temporal error pattern analysis called")
    return {
        "status": "Future enhancement: Not available yet",
        "description": "Will analyze error distribution over time, identify spikes, and correlate with load phases"
    }


async def detect_error_trends_across_runs(
    test_run_ids: List[str],
    ctx: Context
) -> Dict[str, Any]:
    """
    Compare error patterns across multiple test runs to identify trends.
    
    Future enhancement: Track if errors are increasing/decreasing, identify
    new error types, compare error rates.
    
    Args:
        test_run_ids: List of test run identifiers to compare
        ctx: FastMCP context
    
    Returns:
        Dictionary with trend analysis (placeholder for now)
    """
    await ctx.info("Error trend detection called")
    return {
        "status": "Future enhancement: Not available yet",
        "description": "Will compare error patterns across runs and identify trends"
    }


async def generate_error_recommendations(
    log_issues: List[Dict],
    correlations: Dict,
    ctx: Context
) -> List[Dict[str, str]]:
    """
    Generate actionable recommendations based on error patterns.
    
    Future enhancement: Use rule-based logic or ML to suggest specific actions
    like "Increase connection pool size", "Review timeout configuration", etc.
    
    Args:
        log_issues: List of log issues
        correlations: Correlation data
        ctx: FastMCP context
    
    Returns:
        List of recommendation dictionaries (placeholder for now)
    """
    await ctx.info("Error recommendation generation called")
    return [
        {
            "status": "Future enhancement: Not available yet",
            "description": "Will generate specific, actionable recommendations based on error patterns"
        }
    ]


async def correlate_errors_with_test_phases(
    log_issues: List[Dict],
    test_config: Dict,
    ctx: Context
) -> Dict[str, Any]:
    """
    Correlate errors with test execution phases (ramp-up, steady-state, ramp-down).
    
    Future enhancement: Identify if errors occur more during specific test phases.
    
    Args:
        log_issues: List of log issues with timestamps
        test_config: Test configuration with phase information
        ctx: FastMCP context
    
    Returns:
        Dictionary with phase correlation analysis (placeholder for now)
    """
    await ctx.info("Error-phase correlation called")
    return {
        "status": "Future enhancement: Not available yet",
        "description": "Will correlate errors with test execution phases"
    }
