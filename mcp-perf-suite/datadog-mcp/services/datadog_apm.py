import httpx
import json
import csv
import os
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Union
from dotenv import load_dotenv
from pathlib import Path
from fastmcp import FastMCP, Context
from utils.config import load_config
from utils.datadog_config_loader import load_environment_json, load_custom_queries_json
from services.datadog_api import get_ssl_verify_setting

# -----------------------------------------------
# Bootstrap
# -----------------------------------------------
load_dotenv()
config = load_config()
dd_config = config.get('datadog', {})
artifacts_base = config["artifacts"]["artifacts_path"]
environments_json_path = config["datadog"]["environments_json_path"]
configured_tz = config.get("datadog", {}).get("time_zone", "UTC")
apm_page_limit = config.get("datadog", {}).get("apm_page_limit", 1000)

DD_API_KEY = os.getenv("DD_API_KEY")
DD_APP_KEY = os.getenv("DD_APP_KEY")
DD_API_BASE_URL = os.getenv("DD_API_BASE_URL", "https://api.datadoghq.com")
V2_APM_SPANS_URL = f"{DD_API_BASE_URL}/api/v2/spans/events/search"

# CA bundle path for SSL verification
CA_BUNDLE = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")

# -----------------------------------------------
# Helpers
# -----------------------------------------------

async def _build_apm_query(env_tag: str, query_type: str, ctx: Context, env_config: dict = None, custom_query: str = None) -> str:
    """
    Build Datadog APM query based on type and configuration.
    Order of resolution:
      1. If query_type == 'custom' -> use custom_query (required).
      2. Built-in templates (all_errors, http_500_errors, etc.).
      3. custom_queries.json -> apm_queries[query_type].
      4. 'service_errors' special handling.

    Args:
        env_tag (str): Environment tag to filter traces (e.g., 'qa', 'uat').
        query_type (str): Type of query to build (e.g., 'all_errors', 'http_500_errors', etc.).
        ctx (Context): Workflow context for logging and error handling.
        env_config (dict, optional): Environment configuration dictionary.
        custom_query (str, optional): Custom query string if query_type is 'custom'.
    
    Returns:
        str: Constructed Datadog APM query string.
    """
    # 1) Explicit custom query
    if query_type == 'custom':
        if not custom_query:
            await ctx.error("Custom query_type requires a custom_query string")
            raise ValueError("Custom query_type requires a custom_query string")
        return custom_query
    
    # 2) Predefined APM query templates
    templates = {
        'all_errors': f"env:{env_tag} status:error",
        'http_500_errors': f"env:{env_tag} @http.status_code:500",
        'http_errors': f"env:{env_tag} @http.status_code:[400 TO 599]",
        'slow_requests': f"env:{env_tag} @duration:>1000000000"  # > 1 second in nanoseconds
    }
    
    # Direct template match
    if query_type in templates:
        return templates[query_type]
    
    # 3) Custom APM queries from custom_queries.json
    custom_queries_config = await load_custom_queries_json()
    apm_queries = (custom_queries_config or {}).get("apm_queries", {}) or {}

    if query_type in apm_queries:
        query_def = apm_queries[query_type]
        query_string = query_def.get("query", "")
        return query_string
    
    # 4) Service errors (uses env_config.services)
    if query_type == 'service_errors':
        if not env_config or 'services' not in env_config:
            await ctx.info("No services found in environment config; defaulting to all_errors template")
            return templates['all_errors']
        
        services = [s['service_name'] for s in env_config.get('services', [])]
        if not services:
            return templates['all_errors']
        
        service_query_part = ' OR '.join([f"service:{s}" for s in services])
        return f"env:{env_tag} ({service_query_part}) status:error"
    
    raise ValueError(f"Unrecognized query_type: {query_type}")

def _parse_apm_response(response_json: dict) -> Tuple[List[dict], Optional[str]]:
    """
    Parse Datadog APM API response and extract span entries.
    Args:
        response_json (dict): JSON response from Datadog APM API.
    Returns:
        Tuple[List[dict], Optional[str]]: List of span entries and next page cursor if available.
    """
    if response_json is None:
        return [], None
        
    spans = []
    
    for entry in response_json.get('data', []):
        attrs = entry.get('attributes', {})
        custom_attrs = attrs.get('custom', {})
        tags = attrs.get('tags', [])
        
        # Extract HTTP details
        http_info = custom_attrs.get('http', {})
        error_info = custom_attrs.get('error', {})
        
        span_entry = {
            'span_id': attrs.get('span_id', entry.get('id', '')),
            'trace_id': attrs.get('trace_id', ''),
            'timestamp': attrs.get('start_timestamp', ''),
            'end_timestamp': attrs.get('end_timestamp', ''),
            'service': attrs.get('service', ''),
            'resource_name': attrs.get('resource_name', ''),
            'operation_name': attrs.get('operation_name', ''),
            'duration': custom_attrs.get('duration', ''),
            'status': attrs.get('status', ''),
            'http_status_code': http_info.get('status_code', ''),
            'http_method': http_info.get('method', ''),
            'http_url': http_info.get('url', ''),
            'http_route': http_info.get('route', ''),
            'error': '1' if attrs.get('error') else '0',
            'error_message': error_info.get('message', '')[:500],  # Truncate long messages
            'error_type': error_info.get('type', ''),
            'error_stack': error_info.get('stack', '')[:1000],  # Truncate long stacks
            'env': attrs.get('env', ''),
            'host': attrs.get('host', ''),
            'tags': ','.join(tags[:10]) if isinstance(tags, list) else str(tags)  # Limit tags
        }
        spans.append(span_entry)
    
    # Check for next page cursor
    meta = response_json.get('meta') or {}
    page = meta.get('page') or {}
    next_cursor = page.get('after')
    return spans, next_cursor

async def _write_apm_csv(spans: List[dict], csv_path: Path, ctx: Context):
    """
    Write APM spans to CSV file.
    Args:
        spans: List of span dictionaries
        csv_path: Path to output CSV file
        ctx: Context for logging
    """
    if not spans:
        await ctx.info(f"No APM spans to write to {csv_path}")
        return
    
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = [
            'span_id', 'trace_id', 'timestamp', 'end_timestamp', 'service', 'resource_name',
            'operation_name', 'duration', 'status', 'http_status_code',
            'http_method', 'http_url', 'http_route', 'error', 'error_message', 'error_type',
            'error_stack', 'env', 'host', 'tags'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(spans)

def _normalize_timestamp(timestamp: str) -> str:
    """
    Normalize timestamp to ISO 8601 format for Datadog API.
    Accepts: epoch, ISO 8601, or datetime string format.
    Returns: ISO 8601 string in UTC.
    """
    # If already ISO format with Z or timezone
    if 'T' in timestamp and ('Z' in timestamp or '+' in timestamp or timestamp.endswith('00:00')):
        if not timestamp.endswith('Z') and '+' not in timestamp:
            timestamp = timestamp.replace(' ', 'T') + 'Z'
        return timestamp
    
    # Try to parse as epoch (seconds or milliseconds)
    if timestamp.isdigit():
        epoch_val = int(timestamp)
        # If looks like milliseconds (> year 2100 in seconds)
        if epoch_val > 4102444800:
            dt = datetime.fromtimestamp(epoch_val / 1000.0, tz=timezone.utc)
        else:
            dt = datetime.fromtimestamp(epoch_val, tz=timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    # Try to parse as datetime string
    try:
        # Try with timezone first
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except:
        try:
            # Try without timezone, assume UTC
            dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            dt = dt.replace(tzinfo=timezone.utc)
        except:
            # Last resort: try just date
            dt = datetime.strptime(timestamp.split()[0], '%Y-%m-%d')
            dt = dt.replace(tzinfo=timezone.utc)
    
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

# -----------------------------------------------
# APM Spans (v2) API - Search Spans
# -----------------------------------------------
async def collect_apm_traces(env_name: str, start_time: str, end_time: str, query_type: str, run_id: str, ctx: Context, custom_query: Optional[str] = None) -> dict:
    """
    Retrieve APM traces/spans from Datadog APM API.
    
    Args:
        env_name: Environment name from environments.json
        start_time: Start timestamp in UTC. Accepts epoch, ISO 8601, or datetime string formats.
        end_time: End timestamp in UTC. Accepts the same formats as start_time.
        query_type: Query template type. Valid values:
            - "all_errors": All spans with error status
            - "service_errors": Errors filtered by configured services
            - "http_500_errors": HTTP 500 status code errors
            - "http_errors": HTTP 4xx and 5xx status codes
            - "slow_requests": Requests with duration > 1 second
            - "custom": Use custom_query parameter
        run_id: Test run identifier for artifacts organization
        ctx: Workflow context for logging and error handling
        custom_query: Custom Datadog query string (required if query_type="custom")
        
    Returns:
        dict: Dictionary containing:
            - 'csv_file': Path to output CSV file
            - 'summary': Summary statistics (total_spans, status_counts, http_status_counts, top_services, top_resources, error_count)
            - 'span_count': Total number of spans collected
            - 'pages_fetched': Number of API pages retrieved
            - 'query': The actual query string used
            - 'time_range': Start/end timestamps used
            - 'env_name': Environment name
            - 'env_tag': Environment tag from config
            - 'run_id': Test run identifier
    """
    try:
        # Normalize timestamps
        start_iso = _normalize_timestamp(start_time)
        end_iso = _normalize_timestamp(end_time)

        # Load environment config
        env_config = await load_environment_json(env_name, ctx)
        if not env_config:
            msg = "No infrastructure configuration available. Load environment JSON file first."
            await ctx.error(msg)
            return {"files": [], "summary": {"warnings": [msg]}}

        # Extract environment configuration
        env_tag = env_config.get('env_tag')
        
        # Validate query_type
        predefined_query_types = [
            "all_errors",
            "service_errors",
            "http_500_errors",
            "http_errors",
            "slow_requests",
            "custom",
        ]

        # Load custom APM queries from custom_queries.json
        custom_queries_config = await load_custom_queries_json()
        apm_queries_config = (custom_queries_config or {}).get("apm_queries", {}) or {}
        custom_apm_keys = list(apm_queries_config.keys())

        valid_query_types = predefined_query_types + custom_apm_keys

        if query_type not in valid_query_types:
            await ctx.error(f"Invalid query_type: {query_type}. Valid types: {', '.join(valid_query_types)}")
            raise ValueError(f"Invalid query_type: {query_type}")
        
        # Build query
        query = await _build_apm_query(env_tag, query_type, ctx, env_config, custom_query)
        
        await ctx.info(f"Fetching APM traces for {env_name} from {start_iso} to {end_iso}")

        # API request setup
        url = V2_APM_SPANS_URL
        headers = {
            'DD-API-KEY': DD_API_KEY,
            'DD-APPLICATION-KEY': DD_APP_KEY,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # Convert timestamps to milliseconds epoch for APM API
        start_dt = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        
        body = {
            'data': {
                'type': 'search_request',
                'attributes': {
                    'filter': {
                        'from': str(start_ms),
                        'to': str(end_ms),
                        'query': query
                    },
                    'page': {
                        'limit': min(apm_page_limit, 1000)
                    },
                    'sort': 'timestamp'
                }
            }
        }
        
        # Collect all spans with pagination
        all_spans = []
        next_cursor = None
        page_count = 0
        
        verify_ssl = get_ssl_verify_setting()
        timeout_config = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(verify=verify_ssl, timeout=timeout_config) as client:
            while True:
                page_count += 1
                
                # Add cursor for pagination
                if next_cursor:
                    body['data']['attributes']['page']['cursor'] = next_cursor
                
                try:
                    response = await client.post(url, headers=headers, json=body)
                    response.raise_for_status()
                    response_json = response.json()
                    
                    # Debug logging
                    if response_json is None:
                        await ctx.error("response.json() returned None!")
                        break
                    
                    page_spans, next_cursor = _parse_apm_response(response_json)
                except httpx.HTTPStatusError as e:
                    await ctx.error(f"HTTP error fetching APM traces: {e.response.status_code} - {e.response.text}")
                    break
                except json.JSONDecodeError as e:
                    await ctx.error(f"Invalid JSON response from APM API: {str(e)}")
                    break
                except Exception as e:
                    await ctx.error(f"Error fetching APM traces: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    break
                all_spans.extend(page_spans)

                # Stop if no more pages or hit limit
                if not next_cursor or len(all_spans) >= apm_page_limit:
                    break
        
        await ctx.info(f"Total APM spans collected: {len(all_spans)} across {page_count} page(s)")
        
        # Write to CSV
        env_slug = env_name.lower().replace(' ', '-').replace('_', '-')
        csv_filename = f"apm_traces_{query_type}_{env_slug}.csv"
        csv_path = Path(artifacts_base) / run_id / "datadog" / csv_filename
        
        await _write_apm_csv(all_spans, csv_path, ctx)
        
        # Generate summary
        summary = {
            'total_spans': len(all_spans),
            'status_counts': {},
            'http_status_counts': {},
            'top_services': {},
            'top_resources': {},
            'error_count': 0
        }
        
        for span in all_spans:
            # Status counts
            status = span.get('status', 'unknown')
            summary['status_counts'][status] = summary['status_counts'].get(status, 0) + 1
            
            # HTTP status counts
            http_status = span.get('http_status_code', '')
            if http_status:
                summary['http_status_counts'][http_status] = summary['http_status_counts'].get(http_status, 0) + 1
            
            # Service counts
            service = span.get('service', 'unknown')
            summary['top_services'][service] = summary['top_services'].get(service, 0) + 1
            
            # Resource counts
            resource = span.get('resource_name', 'unknown')
            summary['top_resources'][resource] = summary['top_resources'].get(resource, 0) + 1
            
            # Error count
            if span.get('error') or status == 'error':
                summary['error_count'] += 1
        
        # Sort and limit top items
        summary['top_services'] = dict(sorted(summary['top_services'].items(), key=lambda x: x[1], reverse=True)[:10])
        summary['top_resources'] = dict(sorted(summary['top_resources'].items(), key=lambda x: x[1], reverse=True)[:10])
        
        return {
            'env_name': env_name,
            'env_tag': env_tag,
            'query_type': query_type,
            'query': query,
            'time_range': {
                'start': start_iso,
                'end': end_iso
            },
            'span_count': len(all_spans),
            'pages_fetched': page_count,
            'csv_file': str(csv_path),
            'run_id': run_id,
            'summary': summary
        }
    
    except Exception as e:
        error_msg = f"Error collecting APM traces: {str(e)}"
        try:
            await ctx.error(error_msg)
        except:
            print(f"ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            'error': error_msg,
            'env_name': env_name,
            'query_type': query_type,
            'span_count': 0,
            'pages_fetched': 0,
            'csv_file': 'N/A',
            'summary': {}
        }

