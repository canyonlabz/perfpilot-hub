import httpx
import json
import csv
import os
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Union
from dotenv import load_dotenv
from pathlib import Path
from fastmcp import FastMCP, Context    # ✅ FastMCP 2.x import
from utils.config import load_config
from utils.datadog_config_loader import load_environment_json, load_custom_queries_json

# -----------------------------------------------
# Bootstrap
# -----------------------------------------------
load_dotenv()   # Load environment variables from .env file such as API keys and secrets
config = load_config()
dd_config = config.get('datadog', {})
artifacts_base = config["artifacts"]["artifacts_path"]
environments_json_path = config["datadog"]["environments_json_path"]
configured_tz = config.get("datadog", {}).get("time_zone", "UTC")
log_page_limit = config.get("datadog", {}).get("log_page_limit", 1000)

DD_API_KEY = os.getenv("DD_API_KEY")
DD_APP_KEY = os.getenv("DD_APP_KEY")
DD_API_BASE_URL = os.getenv("DD_API_BASE_URL", "https://api.datadoghq.com")
V2_LOGS_URL = f"{DD_API_BASE_URL}/api/v2/logs/events"
V2_LOGS_SEARCH_URL = f"{DD_API_BASE_URL}/api/v2/logs/events/search"

# CA bundle path for SSL verification
CA_BUNDLE = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")

# -----------------------------------------------
# Helpers
# -----------------------------------------------

async def _build_query(env_tag: str, query_type: str, ctx: Context, env_config: dict = None, custom_query: str = None) -> str:
    """
    Build Datadog log query based on type and environment configuration.
    Args:
        env_tag (str): Environment tag to filter logs (e.g., 'qa', 'uat').
        query_type (str): Type of query to build (e.g., 'all_errors', 'service_errors', etc.).
        ctx (Context): Workflow context for logging and error handling.
        env_config (dict, optional): Environment configuration dictionary.
        custom_query (str, optional): Custom query string if query_type is 'custom'.
    
    Returns:
        str: Constructed Datadog log query string.
    """
    # 1) Explicit custom query
    if query_type == 'custom':
        if not custom_query:
            await ctx.error("Custom query_type requires a custom_query string")
            raise ValueError("Custom query_type requires a custom_query string")
        return custom_query
    
    # 2) Predefined query templates
    templates = {
        'all_errors': f"(env:{env_tag}) AND (status:error OR level:ERROR OR level:CRITICAL OR level:FATAL)",
        'warnings': f"(env:{env_tag}) AND (status:warn OR level:WARN OR level:WARNING)",
        'http_errors': f"(env:{env_tag}) AND (@http.status_code:[400 TO 599])",
        'api_errors': f"(env:{env_tag}) AND (@http.method:* AND @http.status_code:[400 TO 599])"
    }
    
    # Direct template match, simple queries not needing env config.
    if query_type in templates:
        return templates[query_type]

    # 3) Custom log queries from custom_queries.json
    custom_queries_config = await load_custom_queries_json()
    log_queries = (custom_queries_config or {}).get("log_queries", {}) or {}

    if query_type in log_queries:
        query_def = log_queries[query_type]
        query_string = query_def.get("query", "")
        return query_string

    # -------------------------------------------------
    # 4) Build queries that depend on environment config
    # -------------------------------------------------

    # Service errors
    if query_type == 'service_errors':
        if not env_config or 'services' not in env_config:
            await ctx.error("Environment configuration with services required for service_errors query")
            raise ValueError("Environment configuration with services required for service_errors query")
        
        services = [s['service_name'] for s in env_config.get('services', [])]
        if not services:
            await ctx.info("No services found in environment config; defaulting to all_errors template")
            return templates['all_errors']  # Fallback to all errors if no services
        
        service_query_part = ' OR '.join([f"service:{s}" for s in services])
        return f"(env:{env_tag}) AND ({service_query_part}) AND (status:error OR level:ERROR)"
    
    # Host errors
    if query_type == 'host_errors':
        if not env_config or 'hosts' not in env_config:
            await ctx.error("Environment configuration with hosts required for host_errors query")
            raise ValueError("Environment configuration with hosts required for host_errors query")
        
        hosts = [h['hostname'] for h in env_config.get('hosts', [])]
        if not hosts:
            await ctx.info("No hosts found in environment config; defaulting to all_errors template")
            return templates['all_errors']  # Fallback to all errors if no hosts
        
        host_query_part = ' OR '.join([f"host:{h}" for h in hosts])
        return f"(env:{env_tag}) AND ({host_query_part}) AND (status:error OR level:ERROR)"
    
    # Kubernetes errors
    if query_type == 'kubernetes_errors':
        if not env_config or 'kubernetes' not in env_config:
            await ctx.error("Environment configuration with kubernetes required for kubernetes_errors query")
            raise ValueError("Environment configuration with kubernetes required for kubernetes_errors query")
        
        k8s_services = env_config.get('kubernetes', {}).get('services', [])
        if not k8s_services:
            await ctx.info("No Kubernetes services found in environment config; defaulting to all_errors template")
            return templates['all_errors']  # Fallback to all errors if no k8s services
        
        k8s_query_parts = []
        for svc in k8s_services:
            service_filter = svc.get('service_filter', '')
            if '*' in service_filter:
                # Wildcard pattern
                k8s_query_parts.append(f"kube_service:{service_filter}")
            else:
                # Exact match
                k8s_query_parts.append(f"kube_service:{service_filter}")
        
        k8s_query_part = ' OR '.join(k8s_query_parts)
        return f"(env:{env_tag}) AND ({k8s_query_part}) AND (status:error OR level:ERROR)"
    
    raise ValueError(f"Unrecognized query_type: {query_type}")

def _parse_logs_response(response_json: dict) -> Tuple[List[dict], Optional[str]]:
    """
    Parse Datadog logs API response and extract log entries.
    Args:
        response_json (dict): JSON response from Datadog logs API.
    Returns:
        Tuple[List[dict], Optional[str]]: List of log entries and next page cursor if available.
    """
    logs = []
    
    for entry in response_json.get('data', []):
        attrs = entry.get('attributes', {})
        custom_attrs = attrs.get('attributes', {})
        
        log_entry = {
            'id': entry.get('id'),
            'timestamp': attrs.get('timestamp'),
            'message': attrs.get('message', ''),
            'status': attrs.get('status', ''),
            'service': attrs.get('service', ''),
            'host': attrs.get('host', ''),
            'level': attrs.get('level', ''),
            'source': attrs.get('source', ''),
            'http_status_code': custom_attrs.get('http.status_code', ''),
            'http_method': custom_attrs.get('http.method', ''),
            'error_kind': custom_attrs.get('error.kind', ''),
            'error_stack': custom_attrs.get('error.stack', ''),
            'custom_attributes': json.dumps(custom_attrs) if custom_attrs else ''
        }
        logs.append(log_entry)
    
    # Extract pagination cursor
    pagination = response_json.get('meta', {}).get('page', {})
    next_cursor = pagination.get('after')
    
    return logs, next_cursor

def _logs_to_csv(logs: List[dict], env_name: str, env_tag: str, query_type: str) -> str:
    """
    Convert logs to CSV format.
    Args:
        logs (List[dict]): List of log entries.
        env_name (str): Environment name.
        env_tag (str): Environment tag.
        query_type (str): Query type used to fetch logs.
    Returns:
        str: CSV formatted string of logs.
    """
    if not logs:
        return "env_name,env_tag,query_type,id,timestamp_utc,message,status,service,host,level,source,http_status_code,http_method,error_kind,custom_attributes\n"
    
    csv_rows = []
    fieldnames = [
        'env_name', 'env_tag', 'query_type', 'id', 'timestamp_utc', 'message', 'status', 
        'service', 'host', 'level', 'source', 'http_status_code', 'http_method', 
        'error_kind', 'custom_attributes'
    ]
    
    for log in logs:
        row = {
            'env_name': env_name,
            'env_tag': env_tag,
            'query_type': query_type,
            'id': log.get('id', ''),
            'timestamp_utc': log.get('timestamp', ''),
            'message': log.get('message', '').replace('\n', ' ').replace('\r', ' ')[:500],  # Truncate long messages
            'status': log.get('status', ''),
            'service': log.get('service', ''),
            'host': log.get('host', ''),
            'level': log.get('level', ''),
            'source': log.get('source', ''),
            'http_status_code': log.get('http_status_code', ''),
            'http_method': log.get('http_method', ''),
            'error_kind': log.get('error_kind', ''),
            'custom_attributes': log.get('custom_attributes', '')
        }
        csv_rows.append(row)
    
    # Generate CSV content
    import io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(csv_rows)
    return output.getvalue()

def _normalize_timestamp(timestamp: str) -> str:
    """
    Convert timestamp to ISO 8601 format if needed.
    Accepts multiple input formats (all assumed to be in UTC):
    - Epoch timestamp (e.g., "1761933994")
    - ISO 8601 format (e.g., "2025-10-31T14:06:34Z")
    - Datetime string format (e.g., "2025-10-31 14:06:34")
    
    Note: All input timestamps are treated as UTC. No timezone conversion is performed.
    
    Args:
        timestamp (str): Input timestamp in any supported format (assumed to be UTC).
    Returns:
        str: ISO 8601 formatted timestamp in UTC (e.g., "2025-10-31T14:06:34Z").
    """
    # If it's already in ISO format, return as-is
    if 'T' in timestamp and 'Z' in timestamp:
        return timestamp
    
    # Try to parse as epoch timestamp (assumed to be UTC)
    try:
        epoch_time = int(timestamp)
        dt = datetime.fromtimestamp(epoch_time, tz=timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        pass
    
    # Try to parse as datetime string (YYYY-MM-DD HH:MM:SS format)
    # This matches the format used by get_host_metrics and get_kubernetes_metrics
    # IMPORTANT: Treat the datetime string as already being in UTC - no conversion
    val_norm = timestamp.replace("T", " ").strip()
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            dt_naive = datetime.strptime(val_norm, fmt)
            # Mark as UTC without conversion - input is assumed to already be UTC
            dt_utc = dt_naive.replace(tzinfo=timezone.utc)
            return dt_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        except ValueError:
            continue
    
    # If all parsing attempts fail, return as-is (may cause API error, but preserves original behavior)
    return timestamp

def _iso_to_epoch_ms(iso_timestamp: str) -> str:
    """Convert ISO 8601 timestamp to epoch milliseconds string for POST search API."""
    dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
    return str(int(dt.timestamp() * 1000))

async def _is_custom_query(query_type: str, custom_query: Optional[str] = None) -> bool:
    """
    Determine if a query type is a custom query (eligible for POST search fallback).

    Custom queries include:
    - Explicit custom queries (query_type == 'custom' with a custom_query string)
    - Named custom queries from custom_queries.json log_queries section
    """
    if query_type == 'custom' and custom_query:
        return True

    custom_queries_config = await load_custom_queries_json()
    log_queries = (custom_queries_config or {}).get("log_queries", {}) or {}
    return query_type in log_queries

async def _post_search_logs(
    query: str,
    start_iso: str,
    end_iso: str,
    headers: dict,
    ctx: Context,
    verify_ssl,
) -> Tuple[List[dict], int]:
    """
    Execute a POST search against /api/v2/logs/events/search.

    Used as a fallback for custom queries when the GET endpoint returns
    incomplete results. Handles pagination via cursor in the JSON body.

    Args:
        query: Datadog log query string.
        start_iso: Start timestamp in ISO 8601 format (converted to epoch ms internally).
        end_iso: End timestamp in ISO 8601 format (converted to epoch ms internally).
        headers: HTTP headers dict with DD API/APP keys.
        ctx: Workflow context for logging.
        verify_ssl: SSL verification setting (bool or path string).

    Returns:
        Tuple of (logs_list, pages_fetched). Returns empty list on failure
        so the caller can fall back to GET-only results.
    """
    start_ms = _iso_to_epoch_ms(start_iso)
    end_ms = _iso_to_epoch_ms(end_iso)

    body = {
        "filter": {
            "query": query,
            "from": start_ms,
            "to": end_ms,
        },
        "sort": "-timestamp",
        "page": {
            "limit": min(log_page_limit, 1000),
        },
    }

    all_logs: List[dict] = []
    page_count = 0

    timeout_config = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout_config) as client:
        while len(all_logs) < log_page_limit and page_count < 10:
            page_count += 1

            try:
                response = await client.post(
                    V2_LOGS_SEARCH_URL, headers=headers, json=body
                )
                response.raise_for_status()
                response_json = response.json()

                logs, next_cursor = _parse_logs_response(response_json)
                all_logs.extend(logs)

                if not next_cursor:
                    break

                body["page"]["cursor"] = next_cursor

            except httpx.HTTPStatusError as e:
                await ctx.error(
                    f"POST search HTTP error: {e.response.status_code} — {e.response.text}"
                )
                break
            except httpx.RequestError as e:
                await ctx.error(f"POST search request failed: {str(e)}")
                break

    return all_logs, page_count

def _deduplicate_logs(
    primary_logs: List[dict], secondary_logs: List[dict]
) -> Tuple[List[dict], int]:
    """
    Merge two log lists, deduplicating by log 'id'.
    Primary logs take precedence; secondary logs are appended only if their
    id is not already present in the primary set.

    Args:
        primary_logs: First log list (GET results) — all entries are kept.
        secondary_logs: Second log list (POST results) — only unique entries are appended.

    Returns:
        Tuple of (merged_logs, duplicate_count_removed).
    """
    seen_ids = {log["id"] for log in primary_logs if log.get("id")}
    unique_secondary = []
    dup_count = 0

    for log in secondary_logs:
        log_id = log.get("id")
        if log_id and log_id in seen_ids:
            dup_count += 1
        else:
            if log_id:
                seen_ids.add(log_id)
            unique_secondary.append(log)

    return primary_logs + unique_secondary, dup_count

# -----------------------------------------------
# Logs (v2) API - Search Logs
# -----------------------------------------------
async def collect_logs(env_name: str, start_time: str, end_time: str, query_type: str, run_id: str, ctx: Context, custom_query: Optional[str] = None) -> dict:
    """
    Retrieve logs from Datadog Logs API.
    
    Args:
        env_name: Environment name from environments.json
        start_time: Start timestamp in UTC. Accepts epoch, ISO 8601, or datetime string formats.
        end_time: End timestamp in UTC. Accepts the same formats as start_time.
        query_type: Query template type. Valid values:
            - "all_errors": All logs with error/critical/fatal status
            - "warnings": Warning level logs
            - "http_errors": HTTP 4xx and 5xx status codes
            - "api_errors": API-related HTTP errors
            - "service_errors": Errors filtered by configured services
            - "host_errors": Errors filtered by configured hosts
            - "kubernetes_errors": Errors filtered by configured Kubernetes services
            - "custom": Use custom_query parameter
        run_id: Test run identifier for artifacts organization
        ctx: Workflow context for logging and error handling
        custom_query: Custom Datadog query string (required if query_type="custom")
        
    Returns:
        dict: Dictionary containing:
            - 'csv_file': Path to output CSV file
            - 'summary': Summary statistics (status_counts, level_counts, top_services)
            - 'log_count': Total number of logs collected
            - 'pages_fetched': Number of API pages retrieved
            - 'query': The actual query string used
            - 'time_range': Start/end timestamps used
            - 'env_name': Environment name
            - 'env_tag': Environment tag from config
            - 'run_id': Test run identifier
    """
    # Base / predefined query types
    predefined_query_types = [
        "all_errors",
        "service_errors",
        "host_errors",
        "http_errors",
        "kubernetes_errors",
        "warnings",
        "api_errors",
        "custom",
    ]

    try:
        # Normalize timestamps
        start_iso = _normalize_timestamp(start_time)
        end_iso = _normalize_timestamp(end_time)

        # Load environment config internally.
        env_config = await load_environment_json(env_name, ctx)
        if not env_config:
            msg = "No infrastructure configuration available. Load environment JSON file first."
            await ctx.error(msg)
            return {"files": [], "summary": {"warnings": [msg]}}

        # Load custom log queries from custom_queries.json
        custom_queries_config = await load_custom_queries_json()
        log_queries_config = (custom_queries_config or {}).get("log_queries", {}) or {}
        custom_log_keys = list(log_queries_config.keys())

        # Final valid query types
        valid_query_types = predefined_query_types + custom_log_keys

        if query_type not in valid_query_types:
            await ctx.error(
                f"Invalid query_type '{query_type}'. Valid options: {', '.join(valid_query_types)}"
            )
            raise ValueError(f"Invalid query_type: {query_type}")

        # Extract environment configuration
        env_tag = env_config.get('env_tag')
        
        # Build query
        query = await _build_query(env_tag, query_type, ctx, env_config, custom_query)
        
        await ctx.info(f"Fetching logs for {env_name} from {start_iso} to {end_iso}")

        # API request setup
        url = V2_LOGS_URL
        headers = {
            'DD-API-KEY': DD_API_KEY,
            'DD-APPLICATION-KEY': DD_APP_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        params = {
            'filter[from]': start_iso,
            'filter[to]': end_iso,
            'filter[query]': query,
            'page[limit]': min(log_page_limit, 1000),  # API max is 1000
            'sort': 'timestamp'
        }
        
        # Collect all logs with pagination
        all_logs = []
        next_cursor = None
        page_count = 0
        
        verify_ssl = get_ssl_verify_setting()
        async with httpx.AsyncClient(verify=verify_ssl) as client:
            while len(all_logs) < log_page_limit and page_count < 10:  # Safety limit on pages
                if next_cursor:
                    params['page[cursor]'] = next_cursor

                try:
                    response = await client.get(url, headers=headers, params=params)
                    response.raise_for_status()
                    response_json = response.json()
                    
                    logs, next_cursor = _parse_logs_response(response_json)
                    all_logs.extend(logs)
                    page_count += 1
                    
                    if not next_cursor:
                        break
                
                except httpx.RequestError as e:
                    await ctx.error(f"HTTP request failed: {str(e)}")
                    raise Exception(f"Datadog API request failed: {str(e)}")
        
        # Limit to requested number
        all_logs = all_logs[:log_page_limit]
        get_log_count = len(all_logs)

        # --- POST search fallback for custom queries ---
        is_custom = await _is_custom_query(query_type, custom_query)
        post_log_count = 0
        dedup_count = 0
        post_search_used = False
        search_methods = ["GET"]

        if is_custom:
            post_search_used = True
            search_methods.append("POST")

            post_logs, post_pages = await _post_search_logs(
                query=query,
                start_iso=start_iso,
                end_iso=end_iso,
                headers=headers,
                ctx=ctx,
                verify_ssl=verify_ssl,
            )
            post_log_count = len(post_logs)
            page_count += post_pages

            if get_log_count == 0 and post_log_count > 0:
                await ctx.info(
                    f"GET returned 0 results; POST returned {post_log_count}. Using POST results."
                )
                all_logs = post_logs[:log_page_limit]
            elif get_log_count > 0 and post_log_count > 0:
                all_logs, dedup_count = _deduplicate_logs(all_logs, post_logs)
                all_logs = all_logs[:log_page_limit]
            elif post_log_count == 0:
                await ctx.info("POST search also returned 0 results.")

        await ctx.info(f"Retrieved {len(all_logs)} logs (final)")

        # Generate CSV content
        csv_content = _logs_to_csv(all_logs, env_name, env_tag, query_type)

        # Save to artifacts directory
        if not run_id:
            run_id = datetime.now().strftime('%Y-%m-%d_%H%M%S_UTC')
        
        artifacts_path = Path(artifacts_base)
        run_artifacts_dir = artifacts_path / run_id / 'datadog'
        run_artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        safe_query_type = query_type.replace('_', '-')
        csv_filename = f"logs_{safe_query_type}_{env_name.lower()}.csv"
        csv_filepath = run_artifacts_dir / csv_filename
        
        # Write CSV file
        with open(csv_filepath, 'w', encoding='utf-8', newline='') as f:
            f.write(csv_content)

        # Summary statistics
        status_counts = {}
        level_counts = {}
        service_counts = {}
        
        for log in all_logs:
            status = log.get('status', 'unknown')
            level = log.get('level', 'unknown')
            service = log.get('service', 'unknown')
            
            status_counts[status] = status_counts.get(status, 0) + 1
            level_counts[level] = level_counts.get(level, 0) + 1
            service_counts[service] = service_counts.get(service, 0) + 1
        
        return {
            'env_name': env_name,
            'env_tag': env_tag,
            'query_type': query_type,
            'query': query,
            'time_range': {
                'start': start_iso,
                'end': end_iso
            },
            'log_count': len(all_logs),
            'pages_fetched': page_count,
            'csv_file': str(csv_filepath),
            'run_id': run_id,
            'summary': {
                'status_counts': status_counts,
                'level_counts': level_counts,
                'top_services': dict(sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:10])
            },
            'search_methods_used': search_methods,
            'get_log_count': get_log_count,
            'post_log_count': post_log_count,
            'deduplicated_count': dedup_count,
            'post_search_used': post_search_used,
        }

    except httpx.RequestError as e:
        await ctx.error(f"API request failed: {str(e)}")
        raise Exception(f"Datadog API request failed: {str(e)}")
    
    except Exception as e:
        await ctx.error(f"Error retrieving logs: {str(e)}")
        raise

# -----------------------------
# Helper functions
# -----------------------------

def get_ssl_verify_setting() -> Union[str, bool]:
    """
    Determines SSL verification setting based on config.yaml.
    
    Returns:
        Union[str, bool]: 
            - Path to CA bundle (str) if ssl_verification is "ca_bundle" and certs are available
            - False if ssl_verification is "disabled"
            - True as fallback (use system certs)
    """
    ssl_verification = dd_config.get('ssl_verification', 'ca_bundle').lower()
    
    if ssl_verification == 'disabled':
        return False
    elif ssl_verification == 'ca_bundle':
        # Use CA bundle if available, otherwise default to True
        return CA_BUNDLE or True
    else:
        # Default to system cert verification
        return True