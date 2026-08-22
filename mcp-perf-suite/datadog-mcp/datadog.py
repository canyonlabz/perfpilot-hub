# datadog.py
from typing import Optional, Dict, Any, List
import json
from fastmcp import FastMCP, Context    # ✅ FastMCP 3.x import
from services.datadog_api import (
    load_environment_json, 
    collect_host_metrics,
    collect_kubernetes_metrics
)
from services.datadog_logs import collect_logs
from services.datadog_apm import collect_apm_traces
from services.datadog_timeseries import collect_kpi_timeseries

mcp = FastMCP("datadog")

@mcp.tool()
async def load_environment(env_name: str, ctx: Context) -> Dict[str, Any]:
    """
    Loads the complete environment configuration from environments.json and stores it in context.

    Args:
        env_name (str): The environment short name to load (e.g., 'QA', 'UAT', etc.).
        ctx (Context, optional): Workflow context for chaining state/status/errors.

    Returns:
        dict: Complete environment configuration for the selected environment.
    """
    return await load_environment_json(env_name, ctx)

@mcp.tool()
async def get_host_metrics(env_name: str, start_time: str, end_time: str, run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Retrieves CPU and memory metrics for all Hosts in the specified environment.
    
    Args:
        env_name (str): The environment short name to load (e.g., 'QA', 'UAT', etc.).
        start_time (str): Start timestamp in UTC. Accepts multiple formats:
            - Epoch timestamp (e.g., "1761933994")
            - ISO 8601 format (e.g., "2025-10-31T14:06:34Z")
            - Datetime string format (e.g., "2025-10-31 14:06:34")
            All formats are treated as UTC - no timezone conversion is performed.
        end_time (str): End timestamp in UTC. Accepts the same formats as start_time.
        run_id (str): Test run identifier for artifacts (e.g., BlazeMeter run_id or timestamp '2023-01-01T00:00:00Z').
        ctx (Context, optional): Workflow context for chaining state/status/errors.

    Returns:
        dict: A dictionary containing list of CSV output files and high-level summary statistics.
    """
    return await collect_host_metrics(env_name, start_time, end_time, run_id, ctx)

@mcp.tool()
async def get_kubernetes_metrics(env_name: str, start_time: str, end_time: str, run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Retrieves CPU and memory metrics for all Kubernetes services in the specified environment.

    Args:
        env_name (str): The environment short name to load (e.g., 'QA', 'UAT', etc.).
        start_time (str): Start timestamp in UTC. Accepts multiple formats:
            - Epoch timestamp (e.g., "1761933994")
            - ISO 8601 format (e.g., "2025-10-31T14:06:34Z")
            - Datetime string format (e.g., "2025-10-31 14:06:34")
            All formats are treated as UTC - no timezone conversion is performed.
        end_time (str): End timestamp in UTC. Accepts the same formats as start_time.
        run_id (str): Test run identifier for artifacts (e.g., BlazeMeter run_id or timestamp '2023-01-01T00:00:00Z').
        ctx (Context, optional): Workflow context for chaining state/status/errors.

    Returns:
        dict: A dictionary containing list of CSV output files and high-level summary statistics.
    """
    return await collect_kubernetes_metrics(env_name, start_time, end_time, run_id, ctx)

@mcp.tool()
async def get_kpi_timeseries(
    env_name: str,
    query_names: List[str],
    start_time: str,
    end_time: str,
    run_id: str,
    ctx: Context,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieves KPI timeseries from Datadog using custom query definitions
    from the kpi_queries section in custom_queries.json.

    Args:
        env_name (str): Environment short name (e.g., 'QA', 'UAT').
        query_names (List[str]): List of query group keys from kpi_queries in custom_queries.json.
        start_time (str): Start timestamp in UTC. Accepts:
            - Epoch seconds ("1761933994")
            - ISO 8601 ("2025-10-31T14:06:34Z")
            - "YYYY-MM-DD HH:MM:SS"
            Treated as UTC with no timezone conversion.
        end_time (str): End timestamp in UTC, same formats as start_time.
        run_id (str): Test run identifier for artifacts (e.g., BlazeMeter run_id).
        ctx (Context, optional): Workflow context for chaining state/status/errors.
        scope (str, optional): "host" or "k8s". Auto-detected from environment if omitted.

    Returns:
        dict: Contains list of CSV output files and high-level summary per KPI.
    """
    return await collect_kpi_timeseries(env_name, query_names, start_time, end_time, run_id, ctx, scope)

@mcp.tool()
async def get_logs(env_name: str, start_time: str, end_time: str, query_type: str, run_id: str, ctx: Context, custom_query: Optional[str] = None) -> dict:
    """
    Retrieve logs from Datadog for a specific environment and time range.
    
    Args:
        env_name: Environment name from environments.json
        start_time: Start timestamp in UTC. Accepts multiple formats:
            - Epoch timestamp (e.g., "1761933994")
            - ISO 8601 format (e.g., "2025-10-31T14:06:34Z")
            - Datetime string format (e.g., "2025-10-31 14:06:34")
            All formats are treated as UTC - no timezone conversion is performed.
        end_time: End timestamp in UTC. Accepts the same formats as start_time.
        query_type: Template types ("all_errors", "warnings", "http_errors", "api_errors", "service_errors", "host_errors", "kubernetes_errors", "custom")
        run_id: Test run identifier for artifacts
        ctx: Workflow context for chaining state/status/errors
        custom_query: Custom Datadog query (required if query_type="custom")
        
    Returns:
        dict: Dictionary containing:
            - 'csv_file': Path to output CSV file
            - 'summary': Summary statistics (status_counts, level_counts, top_services)
            - 'log_count': Total number of logs collected
            - 'pages_fetched': Number of API pages retrieved
            - 'query': The actual query string used
            - 'time_range': Start/end timestamps used
    """
    return await collect_logs(env_name, start_time, end_time, query_type, run_id, ctx, custom_query)

@mcp.tool()
async def get_apm_traces(env_name: str, start_time: str, end_time: str, query_type: str, run_id: str, ctx: Context, custom_query: Optional[str] = None) -> dict:
    """
    Retrieves APM traces from Datadog for a specific environment and time range.

    Args:
        env_name: Environment name from environments.json
        start_time: Start timestamp in UTC. Accepts multiple formats:
            - Epoch timestamp (e.g., "1761933994")
            - ISO 8601 format (e.g., "2025-10-31T14:06:34Z")
            - Datetime string format (e.g., "2025-10-31 14:06:34")
            All formats are treated as UTC - no timezone conversion is performed.
        end_time: End timestamp in UTC. Accepts the same formats as start_time.
        query_type: Template types ("all_errors", "service_errors", "http_500_errors", "http_errors", "slow_requests", "custom")
        run_id: Test run identifier for artifacts
        ctx: Workflow context for chaining state/status/errors
        custom_query: Custom Datadog query (required if query_type="custom")

    Returns:
        dict: Dictionary containing:
            - 'csv_file': Path to output CSV file
            - 'summary': Summary statistics (total_spans, status_counts, http_status_counts, top_services, top_resources, error_count)
            - 'span_count': Total number of spans collected
            - 'pages_fetched': Number of API pages retrieved
            - 'query': The actual query string used
            - 'time_range': Start/end timestamps used
    """
    return await collect_apm_traces(env_name, start_time, end_time, query_type, run_id, ctx, custom_query)


if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down Datadog MCP…")
