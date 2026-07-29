# services/datadog_api.py
import os
import re
import json
import httpx
import csv
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional, Union
from dotenv import load_dotenv
from fastmcp import FastMCP, Context    # ✅ FastMCP 2.x import
from utils.config import load_config
from utils.datadog_config_loader import load_environment_json

# -----------------------------------------------
# Bootstrap
# -----------------------------------------------
load_dotenv()   # Load environment variables from .env file such as API keys and secrets
config = load_config()
dd_config = config.get('datadog', {})
artifacts_base = config["artifacts"]["artifacts_path"]
environments_json_path = config["datadog"]["environments_json_path"]
configured_tz = config.get("datadog", {}).get("time_zone", "UTC")

DD_API_KEY = os.getenv("DD_API_KEY")
DD_APP_KEY = os.getenv("DD_APP_KEY")
DD_API_BASE_URL = os.getenv("DD_API_BASE_URL", "https://api.datadoghq.com")
V1_QUERY_URL = f"{DD_API_BASE_URL}/api/v1/query"
V2_TIMESERIES_URL = f"{DD_API_BASE_URL}/api/v2/query/timeseries"

# CA bundle path for SSL verification
CA_BUNDLE = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")

# -----------------------------------------------
# Helpers
# -----------------------------------------------

def _calculate_memory_percentage(usage_bytes: float, limit_bytes: float) -> float:
    """Calculate memory utilization percentage."""
    if limit_bytes <= 0:
        return 0.0
    return (usage_bytes / limit_bytes) * 100.0

def _sanitize_filename(text: str) -> str:
    """Sanitize text to be safe for filenames."""
    text = text.strip().replace(" ", "_")
    return re.sub(r"[^a-zA-Z0-9._-]", "_", text)

def _normalize_k8s_filter(raw_filter: str) -> str:
    """Produce a deterministic, filesystem-safe resource name from a K8s filter.

    Strips wildcard characters before sanitizing so that 'my-pod*' and
    'my-pod' both resolve to the same filename segment ('my-pod').
    """
    stripped = raw_filter.replace("*", "")
    return _sanitize_filename(stripped)

def _ensure_ready():
    """Ensure required environment variables are set."""
    missing = []
    if not DD_API_KEY:
        missing.append("DD_API_KEY")
    if not DD_APP_KEY:
        missing.append("DD_APP_KEY")
    if missing:
        msg = f"Missing environment variable(s): {', '.join(missing)}"
        raise RuntimeError(msg)

def _ensure_artifacts_dir(run_id: str) -> str:
    """Ensure artifacts/run_id/datadog/ directory exists and return its path."""
    base = os.path.join(artifacts_base, str(run_id), "datadog")
    os.makedirs(base, exist_ok=True)
    return base

def _parse_to_utc(start_time: str, end_time: str) -> Tuple[int, int, int, int, str]:
    """Parse input times (epoch or ISO) and convert to epoch timestamps.
    
    Accepts multiple input formats (all assumed to be in UTC):
    - Epoch timestamp (e.g., "1761933994")
    - ISO 8601 format (e.g., "2025-10-31T14:06:34Z" or "2025-10-31 14:06:34")
    - Datetime string format (e.g., "2025-10-31 14:06:34")
    
    Note: All input timestamps are treated as UTC. No timezone conversion is performed.
    The function only formats/parses the timestamp, it does not convert between timezones.

    Returns: (v1_from_s, v1_to_s, v2_from_ms, v2_to_ms, tz_label)
    """
    # Accept epoch int/float or ISO8601 (with/without T)
    def parse_one(val: str) -> datetime:
        # Try epoch first
        try:
            if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
                return datetime.fromtimestamp(int(val), tz=timezone.utc)
        except Exception:
            pass
        # ISO-like formats; treat as UTC (no conversion)
        # We only rely on naive parsing here to stay dependency-free
        # Handle ISO 8601 format with Z suffix (e.g., "2025-10-31T14:06:34Z")
        # Note: All inputs are guaranteed to be UTC, so no timezone offset handling needed
        val_norm = val.replace("T", " ").strip()
        # Strip Z suffix if present (all inputs are UTC)
        val_norm = val_norm.rstrip("Z")
        # Try common formats
        fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
        for fmt in fmts:
            try:
                dt_naive = datetime.strptime(val_norm, fmt)
                # Mark as UTC without conversion - input is assumed to already be UTC
                return dt_naive.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        raise ValueError(f"Unrecognized time format: {val}")

    # Parse both times (already UTC, no conversion needed)
    start_dt_utc = parse_one(start_time)
    end_dt_utc = parse_one(end_time)
    if start_dt_utc >= end_dt_utc:
        raise ValueError("start_time must be before end_time")

    v1_from_s = int(start_dt_utc.timestamp())
    v1_to_s = int(end_dt_utc.timestamp())
    v2_from_ms = v1_from_s * 1000
    v2_to_ms = v1_to_s * 1000
    tz_label = "UTC"
    return v1_from_s, v1_to_s, v2_from_ms, v2_to_ms, tz_label

def _write_csv_header(writer: csv.writer):
    """Write standard CSV header for output files."""
    writer.writerow([
        "env_name", "env_tag", "scope", "hostname", "filter", "container_or_pod",
        "timestamp_utc", "metric", "value", "unit"
    ])


def _build_combined_metrics_request(
    from_ms: int,
    to_ms: int,
    usage_query: str,
    limits_query: str,
    interval: int = 20000
) -> Dict[str, Any]:
    """
    Build Datadog v2 timeseries request body with both usage and limits queries.
    
    This allows querying both metrics in a single API call, with usage as query_index=0
    and limits as query_index=1 in the response.
    
    Args:
        from_ms: Start timestamp in milliseconds
        to_ms: End timestamp in milliseconds
        usage_query: Datadog query string for usage metric
        limits_query: Datadog query string for limits metric
        interval: Rollup interval in milliseconds (default 20000 = 20 seconds)
    
    Returns:
        Dict: Request body for Datadog v2 timeseries API
    """
    return {
        "data": {
            "type": "timeseries_request",
            "interval": interval,
            "attributes": {
                "from": from_ms,
                "to": to_ms,
                "queries": [
                    {"data_source": "metrics", "name": "usage", "query": usage_query},
                    {"data_source": "metrics", "name": "limits", "query": limits_query}
                ],
                "formulas": [
                    {"formula": "usage"},
                    {"formula": "limits"}
                ]
            }
        }
    }

# -----------------------------------------------
# Hosts (v1) — per-host CSV + aggregates
# -----------------------------------------------
async def collect_host_metrics(env_name: str, start_time: str, end_time: str, run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Collect CPU & Memory metrics for each host and write one CSV per host.

    Args:
        env_name: Environment name to load (e.g., 'QA', 'UAT')
        start_time: Start timestamp (epoch or ISO8601)
        end_time: End timestamp (epoch or ISO8601)  
        run_id: Test run identifier for artifacts
        ctx: FastMCP context for logging

    Returns:
        dict: {
          "files": ["<path-to-host_metrics_[hostname].csv>", ...],
          "summary": {
            "env_name": str, "env_tag": str,
            "entities": int, "metrics": ["cpu","mem"],
            "date_range": {"start": str, "end": str, "tz": str},
            "aggregates": [{"hostname": str, "avg_cpu_util": float, "avg_mem_pct": float}],
            "warnings": [str, ...]
          }
        }
    """
    _ensure_ready()

    # Load environment config internally.
    env_config = await load_environment_json(env_name, ctx)
    if not env_config:
        msg = "No infrastructure configuration available. Load environment JSON file first."
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    env_name = env_config.get("environment_name", "unknown")
    env_tag = env_config.get("env_tag", "unknown")
    hosts = env_config.get("hosts", [])
    if not hosts:
        msg = "No hosts configured in environment."
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    run = str(run_id) if run_id else "mock_run_id"
    outdir = _ensure_artifacts_dir(run)

    v1_from_s, v1_to_s, v2_from_ms, v2_to_ms, tz_label = _parse_to_utc(start_time, end_time)

    files: List[str] = []
    aggregates: List[Dict[str, Any]] = []
    warnings: List[str] = []

    # Build queries (include cpu.system so we can compute total busy ≈ user+system)
    # Keeping scope to CPU+Memory only.
    q_tpl = (
        "avg:system.cpu.user{host:%(h)s}.rollup(avg,60),"
        "avg:system.cpu.system{host:%(h)s}.rollup(avg,60),"
        "avg:system.mem.used{host:%(h)s}.rollup(avg,60),"
        "avg:system.mem.total{host:%(h)s}.rollup(avg,60)"
    )

    headers = {"DD-API-KEY": DD_API_KEY, "DD-APPLICATION-KEY": DD_APP_KEY}

    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        for h in hosts:
            hostname = h.get("hostname")
            if not hostname:
                continue

            query = q_tpl % {"h": hostname}
            params = {"from": v1_from_s, "to": v1_to_s, "query": query}

            # Collect series values keyed by metric name
            series_map: Dict[str, List[Tuple[int, float]]] = {}
            cpu_unit_family: Optional[str] = None
            try:
                resp = await client.get(V1_QUERY_URL, params=params, headers=headers, timeout=60.0)
                resp.raise_for_status()
                data = resp.json()
                for series in data.get("series", []):
                    metric = series.get("metric") or series.get("display_name")
                    pts = series.get("pointlist", [])
                    # each point is [ms, value]
                    series_map[metric] = [(int(ts), val if val is not None else float("nan")) for ts, val in pts]
                    # Extract unit family from system.cpu.user to determine if values are already %
                    if metric == "system.cpu.user" and cpu_unit_family is None:
                        unit_info = series.get("unit")
                        if unit_info and isinstance(unit_info, list) and len(unit_info) > 0 and unit_info[0]:
                            cpu_unit_family = unit_info[0].get("family", "").lower()
            except Exception as e:
                warnings.append(f"Host '{hostname}': API error — {e}; Request params: {params}; Response: {json.dumps(data, indent=2) if 'data' in locals() else 'N/A'}")
                await ctx.error(f"Host '{hostname}': API error — {e}")
                continue

            # If no datapoints at all, skip file
            if not any(series_map.values()):
                warnings.append(f"Host '{hostname}': no datapoints in the date range; skipping file")
                continue

            # Prepare per-host CSV
            outcsv = os.path.join(outdir, f"host_metrics_[{_sanitize_filename(hostname)}].csv")
            files.append(outcsv)
            with open(outcsv, "w", newline="", encoding="utf-8") as fcsv:
                w = csv.writer(fcsv)
                _write_csv_header(w)

                # Write rows for each metric series
                def write_series(metric_name: str, unit: str = ""):
                    for ts_ms, val in series_map.get(metric_name, []):
                        dt_iso = datetime.utcfromtimestamp(ts_ms / 1000).isoformat()
                        w.writerow([env_name, env_tag, "host", hostname, "", "", dt_iso, metric_name, val, unit])

                write_series("system.cpu.user", "%")
                write_series("system.cpu.system", "%")
                write_series("system.mem.used", "bytes")
                write_series("system.mem.total", "bytes")

                # Derived memory percent per timestamp (if both available)
                mem_used = dict(series_map.get("system.mem.used", []))
                mem_tot = dict(series_map.get("system.mem.total", []))
                mem_pct_vals: List[float] = []
                for ts_ms, used in mem_used.items():
                    tot = mem_tot.get(ts_ms)
                    if tot and tot > 0:
                        pct = (used / tot) * 100.0
                        mem_pct_vals.append(pct)
                        dt_iso = datetime.utcfromtimestamp(ts_ms / 1000).isoformat()
                        w.writerow([env_name, env_tag, "host", hostname, "", "", dt_iso, "mem_util_pct", pct, "%"])

                # Derived CPU percent per timestamp
                cpu_user = dict(series_map.get("system.cpu.user", []))
                cpu_sys = dict(series_map.get("system.cpu.system", []))
                common_ts = sorted(set(cpu_user.keys()) & set(cpu_sys.keys()))

                if cpu_unit_family == "percentage" and common_ts:
                    for ts_ms in common_ts:
                        cpu_total = (cpu_user.get(ts_ms, 0) or 0) + (cpu_sys.get(ts_ms, 0) or 0)
                        dt_iso = datetime.utcfromtimestamp(ts_ms / 1000).isoformat()
                        w.writerow([env_name, env_tag, "host", hostname, "", "", dt_iso, "cpu_util_pct", cpu_total, "%"])
                elif common_ts:
                    warnings.append(
                        f"Host '{hostname}': CPU metrics returned in non-percent units "
                        f"({cpu_unit_family or 'unknown'}) — cpu_util_pct cannot be derived"
                    )
                    await ctx.warning(warnings[-1])

            # Aggregates
            # CPU utilization ≈ avg(user + system) over overlapping timestamps
            cpu_user = dict(series_map.get("system.cpu.user", []))
            cpu_sys = dict(series_map.get("system.cpu.system", []))
            common_ts = sorted(set(cpu_user.keys()) & set(cpu_sys.keys()))
            cpu_util_vals = [(cpu_user[t] or 0) + (cpu_sys[t] or 0) for t in common_ts]
            avg_cpu_util = sum(cpu_util_vals) / len(cpu_util_vals) if cpu_util_vals else 0.0

            avg_mem_pct = 0.0
            # reuse mem_pct_vals collected above
            avg_mem_pct = sum(mem_pct_vals) / len(mem_pct_vals) if 'mem_pct_vals' in locals() and mem_pct_vals else 0.0

            aggregates.append({
                "hostname": hostname,
                "avg_cpu_util": round(avg_cpu_util, 4),
                "avg_mem_pct": round(avg_mem_pct, 4),
            })

    summary = {
        "env_name": env_name,
        "env_tag": env_tag,
        "entities": len(hosts),
        "metrics": ["cpu", "mem"],
        "date_range": {"start": str(start_time), "end": str(end_time), "tz": tz_label},
        "aggregates": aggregates,
        "warnings": warnings,
    }

    return {"files": files, "summary": summary}

# -----------------------------------------------
# Kubernetes (v2) — per-service CSV (merged CPU+Mem) + aggregates
# -----------------------------------------------
async def collect_kubernetes_metrics(env_name: str, start_time: str, end_time: str, run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Collect CPU & Memory metrics for each configured k8s service and/or pod and write one CSV per service/pod.
    - Services are read from environment["kubernetes"]["services"].
    - Pods are read from environment["kubernetes"]["pods"].
    - At least one of them must be defined (services or pods).
    - One CSV is produced per service / pod, all using the same schema.

    Args:
        env_name: Environment name to load (e.g., 'QA', 'UAT')
        start_time: Start timestamp (epoch or ISO8601)
        end_time: End timestamp (epoch or ISO8601)  
        run_id: Test run identifier for artifacts
        ctx: FastMCP context for logging

    Returns:
        dict: {
          "files": ["<path-to-k8s_metrics_[service].csv>", ...],
          "summary": {
            "env_name": str, "env_tag": str,
            "entities": int, "metrics": ["cpu","mem"],
            "date_range": {"start": str, "end": str, "tz": str},
            "aggregates": [{"filter": str, "avg_cpu": float, "avg_mem": float}],
            "warnings": [str, ...]
          }
        }
    """
    _ensure_ready()

    # Load environment config internally.
    env_config = await load_environment_json(env_name, ctx)
    if not env_config:
        msg = "No infrastructure configuration available. Load environment JSON file first."
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    env_tag = env_config.get("env_tag", "unknown")

    kube_namespace = env_config.get("kube_namespace") or env_config.get("namespace")

    k8s_cfg = env_config.get("kubernetes", {}) or {}
    services: List[Dict[str, Any]] = k8s_cfg.get("services", []) or []
    pods: List[Dict[str, Any]] = k8s_cfg.get("pods", []) or []

    if not services and not pods:
        msg = "No Kubernetes pods or services configured in environment. At least one must be defined."
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    run = str(run_id) if run_id else "mock_run_id"
    outdir = _ensure_artifacts_dir(run)

    v1_from_s, v1_to_s, v2_from_ms, v2_to_ms, tz_label = _parse_to_utc(start_time, end_time)

    files: List[str] = []
    aggregates: List[Dict[str, Any]] = []
    warnings: List[str] = []

    headers = {
        "DD-API-KEY": DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
        "Content-Type": "application/json",
    }

    verify_ssl = get_ssl_verify_setting()

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        # -------------------------------------------------------------
        # 1) Services - Using DYNAMIC limits from Datadog
        # -------------------------------------------------------------
        for svc in services:
            s_filter = svc.get("service_filter") or svc.get("kube_service")
            if not s_filter:
                warn_msg = f"Service entry skipped — no 'service_filter' or 'kube_service' key found. Keys present: {list(svc.keys())}"
                warnings.append(warn_msg)
                await ctx.warning(warn_msg)
                continue

            # NOTE: We no longer use static limits from environments.json for K8s
            # Instead, we query limits dynamically from Datadog alongside usage metrics

            # 1a) CPU request (usage + limits combined)
            cpu_usage_query, cpu_limits_query = svc_cpu_with_limits_query(env_tag, s_filter)
            body_cpu = _build_combined_metrics_request(v2_from_ms, v2_to_ms, cpu_usage_query, cpu_limits_query)

            cpu_usage_series: Dict[str, List[Tuple[int, float]]] = {}
            cpu_limits_series: Dict[str, List[Tuple[int, float]]] = {}
            try:
                r_cpu = await client.post(V2_TIMESERIES_URL, headers=headers, json=body_cpu, timeout=60.0)
                r_cpu.raise_for_status()
                attrs = r_cpu.json().get("data", {}).get("attributes", {})
                cpu_usage_series, cpu_limits_series = _extract_series_with_limits(attrs, ["kube_container_name"])
            except Exception as e:
                warnings.append(f"Service '{s_filter}': CPU query error — {e}")
                await ctx.error(warnings[-1])
                continue

            # 1b) Memory request (usage + limits combined)
            mem_usage_query, mem_limits_query = svc_mem_with_limits_query(env_tag, s_filter)
            body_mem = _build_combined_metrics_request(v2_from_ms, v2_to_ms, mem_usage_query, mem_limits_query)

            mem_usage_series: Dict[str, List[Tuple[int, float]]] = {}
            mem_limits_series: Dict[str, List[Tuple[int, float]]] = {}
            try:
                r_mem = await client.post(V2_TIMESERIES_URL, headers=headers, json=body_mem, timeout=60.0)
                r_mem.raise_for_status()
                attrs = r_mem.json().get("data", {}).get("attributes", {})
                mem_usage_series, mem_limits_series = _extract_series_with_limits(attrs, ["kube_container_name"])
            except Exception as e:
                warnings.append(f"Service '{s_filter}': Memory query error — {e}")
                await ctx.error(warnings[-1])
                continue

            # If both CPU and Memory usage are empty, skip file
            if not any(cpu_usage_series.values()) and not any(mem_usage_series.values()):
                warnings.append(f"Service '{s_filter}': no datapoints in the date range; skipping file")
                continue

            # Calculate % utilization using dynamic limits
            cpu_util_series, has_cpu_limits = _calculate_utilization_with_dynamic_limits(
                cpu_usage_series, cpu_limits_series, is_cpu=True
            )
            mem_util_series, has_mem_limits = _calculate_utilization_with_dynamic_limits(
                mem_usage_series, mem_limits_series, is_cpu=False
            )

            # Log warnings for missing limits (informational, not errors)
            if not has_cpu_limits:
                warn_msg = f"Service '{s_filter}': CPU limits not defined in Kubernetes. % utilization marked as -1."
                warnings.append(warn_msg)
            if not has_mem_limits:
                warn_msg = f"Service '{s_filter}': Memory limits not defined in Kubernetes. % utilization marked as -1."
                warnings.append(warn_msg)

            # Prepare per-service CSV
            fname = f"k8s_metrics_[{_normalize_k8s_filter(s_filter)}].csv"
            outcsv = os.path.join(outdir, fname)
            files.append(outcsv)

            # Write CSV with all metrics
            with open(outcsv, "w", newline="", encoding="utf-8") as fcsv:
                w = csv.writer(fcsv)
                _write_csv_header(w)

                def write_series(
                    metric_name: str,
                    unit: str,
                    per_container: Dict[str, List[Tuple[int, float]]],
                ):
                    for cname, pts in per_container.items():
                        for ts_ms, val in pts:
                            dt_iso = datetime.utcfromtimestamp(ts_ms / 1000).isoformat()
                            w.writerow([env_name, env_tag, "k8s", "", s_filter, cname, dt_iso, metric_name, val, unit])

                # Raw CPU/Memory usage metrics
                write_series("kubernetes.cpu.usage.total", "nanocores", cpu_usage_series)
                write_series("kubernetes.memory.usage", "bytes", mem_usage_series)

                # Dynamic limits from Datadog (new rows)
                # Fill missing limits with 0.0 for CSV consistency when Datadog returns no data
                cpu_limits_for_csv = _fill_missing_limits_series(cpu_usage_series, cpu_limits_series)
                mem_limits_for_csv = _fill_missing_limits_series(mem_usage_series, mem_limits_series)
                write_series("kubernetes.cpu.limits", "cores", cpu_limits_for_csv)
                write_series("kubernetes.memory.limits", "bytes", mem_limits_for_csv)

                # CPU utilization percentage (calculated from dynamic limits)
                # Value of -1 indicates limits not defined in Kubernetes
                write_series("cpu_util_pct", "%", cpu_util_series)

                # Memory utilization percentage (calculated from dynamic limits)
                # Value of -1 indicates limits not defined in Kubernetes
                write_series("mem_util_pct", "%", mem_util_series)

            # Aggregates (per service) - using raw usage values
            def flat_vals(series: Dict[str, List[Tuple[int, float]]]) -> List[float]:
                return [v for pts in series.values() for (_, v) in pts]

            def flat_vals_exclude_negative(series: Dict[str, List[Tuple[int, float]]]) -> List[float]:
                """Exclude -1 values (limits not defined) from aggregation."""
                return [v for pts in series.values() for (_, v) in pts if v >= 0]

            cpu_usage_vals = flat_vals(cpu_usage_series)
            mem_usage_vals = flat_vals(mem_usage_series)
            cpu_util_vals = flat_vals_exclude_negative(cpu_util_series)
            mem_util_vals = flat_vals_exclude_negative(mem_util_series)

            avg_cpu_usage = (sum(cpu_usage_vals) / len(cpu_usage_vals)) if cpu_usage_vals else 0.0
            avg_mem_usage = (sum(mem_usage_vals) / len(mem_usage_vals)) if mem_usage_vals else 0.0

            aggregate_entry = {
                "filter": s_filter,
                "entity_type": "service",
                "avg_cpu_nanocores": round(avg_cpu_usage, 4),
                "avg_mem_bytes": round(avg_mem_usage, 4),
                "cpu_limits_available": has_cpu_limits,
                "mem_limits_available": has_mem_limits,
            }

            # Only include % utilization in aggregates if limits are available
            if has_cpu_limits and cpu_util_vals:
                aggregate_entry["avg_cpu_pct"] = round(sum(cpu_util_vals) / len(cpu_util_vals), 4)
            else:
                aggregate_entry["avg_cpu_pct"] = -1  # -1 indicates limits not defined

            if has_mem_limits and mem_util_vals:
                aggregate_entry["avg_mem_pct"] = round(sum(mem_util_vals) / len(mem_util_vals), 4)
            else:
                aggregate_entry["avg_mem_pct"] = -1  # -1 indicates limits not defined

            aggregates.append(aggregate_entry)

        # -------------------------------------------------------------
        # 2) Pods - Using DYNAMIC limits from Datadog
        # -------------------------------------------------------------
        for pod in pods:
            pod_filter = pod.get("pod_filter") or pod.get("kube_service")
            if not pod_filter:
                warn_msg = f"Pod entry skipped — no 'pod_filter' or 'kube_service' key found. Keys present: {list(pod.keys())}"
                warnings.append(warn_msg)
                await ctx.warning(warn_msg)
                continue

            # NOTE: We no longer use static limits from environments.json for K8s
            # Instead, we query limits dynamically from Datadog alongside usage metrics

            normalized_filter = pod_filter.rstrip("*")
            pod_id_tag_keys = ["kube_service", "kube_pod_name", "kube_container_name"]

            # 2a) CPU request (usage + limits combined)
            cpu_usage_query, cpu_limits_query = pod_cpu_with_limits_query(kube_namespace, pod_filter)
            body_cpu = _build_combined_metrics_request(v2_from_ms, v2_to_ms, cpu_usage_query, cpu_limits_query)

            pod_cpu_usage_series: Dict[str, List[Tuple[int, float]]] = {}
            pod_cpu_limits_series: Dict[str, List[Tuple[int, float]]] = {}
            try:
                r_cpu = await client.post(V2_TIMESERIES_URL, headers=headers, json=body_cpu, timeout=60.0)
                r_cpu.raise_for_status()
                attrs = r_cpu.json().get("data", {}).get("attributes", {})
                pod_cpu_usage_series, pod_cpu_limits_series = _extract_series_with_limits(
                    attrs, pod_id_tag_keys, default_identifier=normalized_filter
                )

                # Query-level fallback: if primary (by kube_service) returned no usage data,
                # retry with original grouping (by kube_namespace) for backwards compatibility
                if not pod_cpu_usage_series:
                    warnings.append(f"Pod '{pod_filter}': primary CPU query (by kube_service) returned no data; retrying with fallback (by kube_namespace)")
                    await ctx.warning(warnings[-1])
                    if kube_namespace:
                        fb_cpu_usage = f"avg:kubernetes.cpu.usage.total{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_namespace}}"
                        fb_cpu_limits = f"sum:kubernetes.cpu.limits{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_namespace}}"
                    else:
                        fb_cpu_usage = f"avg:kubernetes.cpu.usage.total{{kube_service:{pod_filter}}} by {{kube_namespace}}"
                        fb_cpu_limits = f"sum:kubernetes.cpu.limits{{kube_service:{pod_filter}}} by {{kube_namespace}}"
                    body_cpu_fb = _build_combined_metrics_request(v2_from_ms, v2_to_ms, fb_cpu_usage, fb_cpu_limits)
                    r_cpu_fb = await client.post(V2_TIMESERIES_URL, headers=headers, json=body_cpu_fb, timeout=60.0)
                    r_cpu_fb.raise_for_status()
                    attrs_fb = r_cpu_fb.json().get("data", {}).get("attributes", {})
                    pod_cpu_usage_series, pod_cpu_limits_series = _extract_series_with_limits(
                        attrs_fb, pod_id_tag_keys, default_identifier=normalized_filter
                    )
            except Exception as e:
                warnings.append(f"Pod '{pod_filter}': CPU query failed with error: {e}")
                await ctx.error(warnings[-1])
                continue

            # 2b) Memory request (usage + limits combined)
            mem_usage_query, mem_limits_query = pod_mem_with_limits_query(kube_namespace, pod_filter)
            body_mem = _build_combined_metrics_request(v2_from_ms, v2_to_ms, mem_usage_query, mem_limits_query)

            pod_mem_usage_series: Dict[str, List[Tuple[int, float]]] = {}
            pod_mem_limits_series: Dict[str, List[Tuple[int, float]]] = {}
            try:
                r_mem = await client.post(V2_TIMESERIES_URL, headers=headers, json=body_mem, timeout=60.0)
                r_mem.raise_for_status()
                attrs = r_mem.json().get("data", {}).get("attributes", {})
                pod_mem_usage_series, pod_mem_limits_series = _extract_series_with_limits(
                    attrs, pod_id_tag_keys, default_identifier=normalized_filter
                )

                # Query-level fallback: if primary (by kube_service) returned no usage data,
                # retry with original grouping (by kube_pod_name) for backwards compatibility
                if not pod_mem_usage_series:
                    warnings.append(f"Pod '{pod_filter}': primary Memory query (by kube_service) returned no data; retrying with fallback (by kube_pod_name)")
                    await ctx.warning(warnings[-1])
                    if kube_namespace:
                        fb_mem_usage = f"sum:kubernetes.memory.usage{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_pod_name}}"
                        fb_mem_limits = f"sum:kubernetes.memory.limits{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_pod_name}}"
                    else:
                        fb_mem_usage = f"sum:kubernetes.memory.usage{{kube_service:{pod_filter}}} by {{kube_pod_name}}"
                        fb_mem_limits = f"sum:kubernetes.memory.limits{{kube_service:{pod_filter}}} by {{kube_pod_name}}"
                    body_mem_fb = _build_combined_metrics_request(v2_from_ms, v2_to_ms, fb_mem_usage, fb_mem_limits)
                    r_mem_fb = await client.post(V2_TIMESERIES_URL, headers=headers, json=body_mem_fb, timeout=60.0)
                    r_mem_fb.raise_for_status()
                    attrs_fb = r_mem_fb.json().get("data", {}).get("attributes", {})
                    pod_mem_usage_series, pod_mem_limits_series = _extract_series_with_limits(
                        attrs_fb, pod_id_tag_keys, default_identifier=normalized_filter
                    )
            except Exception as e:
                warnings.append(f"Pod '{pod_filter}': Memory query failed with error: {e}")
                await ctx.error(warnings[-1])
                continue

            # If both CPU and Memory usage are empty, skip file
            if not any(pod_cpu_usage_series.values()) and not any(pod_mem_usage_series.values()):
                warnings.append(f"Pod '{pod_filter}': no datapoints in the date range; skipping file")
                continue

            # Calculate % utilization using dynamic limits
            pod_cpu_util_series, has_cpu_limits = _calculate_utilization_with_dynamic_limits(
                pod_cpu_usage_series, pod_cpu_limits_series, is_cpu=True
            )
            pod_mem_util_series, has_mem_limits = _calculate_utilization_with_dynamic_limits(
                pod_mem_usage_series, pod_mem_limits_series, is_cpu=False
            )

            # Log warnings for missing limits (informational, not errors)
            if not has_cpu_limits:
                warn_msg = f"Pod '{pod_filter}': CPU limits not defined in Kubernetes. % utilization marked as -1."
                warnings.append(warn_msg)
            if not has_mem_limits:
                warn_msg = f"Pod '{pod_filter}': Memory limits not defined in Kubernetes. % utilization marked as -1."
                warnings.append(warn_msg)

            # Prepare per-pod CSV
            fname = f"k8s_metrics_[{_normalize_k8s_filter(pod_filter)}].csv"
            outcsv = os.path.join(outdir, fname)
            files.append(outcsv)

            # Write CSV with all metrics
            with open(outcsv, "w", newline="", encoding="utf-8") as fcsv:
                w = csv.writer(fcsv)
                _write_csv_header(w)

                def write_pod_series(
                    metric_name: str,
                    unit: str,
                    per_pod: Dict[str, List[Tuple[int, float]]],
                ):
                    for pod_id, pts in per_pod.items():
                        for ts_ms, val in pts:
                            dt_iso = datetime.utcfromtimestamp(ts_ms / 1000).isoformat()
                            w.writerow([env_name, env_tag, "k8s", "", normalized_filter, pod_id, dt_iso, metric_name, val, unit])

                # Raw CPU/Memory usage metrics
                write_pod_series("kubernetes.cpu.usage.total", "nanocores", pod_cpu_usage_series)
                write_pod_series("kubernetes.memory.usage", "bytes", pod_mem_usage_series)

                # Dynamic limits from Datadog (new rows)
                # Fill missing limits with 0.0 for CSV consistency when Datadog returns no data
                pod_cpu_limits_for_csv = _fill_missing_limits_series(pod_cpu_usage_series, pod_cpu_limits_series)
                pod_mem_limits_for_csv = _fill_missing_limits_series(pod_mem_usage_series, pod_mem_limits_series)
                write_pod_series("kubernetes.cpu.limits", "cores", pod_cpu_limits_for_csv)
                write_pod_series("kubernetes.memory.limits", "bytes", pod_mem_limits_for_csv)

                # CPU utilization percentage (calculated from dynamic limits)
                # Value of -1 indicates limits not defined in Kubernetes
                write_pod_series("cpu_util_pct", "%", pod_cpu_util_series)

                # Memory utilization percentage (calculated from dynamic limits)
                # Value of -1 indicates limits not defined in Kubernetes
                write_pod_series("mem_util_pct", "%", pod_mem_util_series)

            # Aggregates (per pod_filter) - using raw usage values
            def flat_vals(series: Dict[str, List[Tuple[int, float]]]) -> List[float]:
                return [v for pts in series.values() for (_, v) in pts]

            def flat_vals_exclude_negative(series: Dict[str, List[Tuple[int, float]]]) -> List[float]:
                """Exclude -1 values (limits not defined) from aggregation."""
                return [v for pts in series.values() for (_, v) in pts if v >= 0]

            cpu_usage_vals = flat_vals(pod_cpu_usage_series)
            mem_usage_vals = flat_vals(pod_mem_usage_series)
            cpu_util_vals = flat_vals_exclude_negative(pod_cpu_util_series)
            mem_util_vals = flat_vals_exclude_negative(pod_mem_util_series)

            avg_cpu_usage = (sum(cpu_usage_vals) / len(cpu_usage_vals)) if cpu_usage_vals else 0.0
            avg_mem_usage = (sum(mem_usage_vals) / len(mem_usage_vals)) if mem_usage_vals else 0.0

            aggregate_entry = {
                "filter": pod_filter,
                "entity_type": "pod",
                "avg_cpu_nanocores": round(avg_cpu_usage, 4),
                "avg_mem_bytes": round(avg_mem_usage, 4),
                "cpu_limits_available": has_cpu_limits,
                "mem_limits_available": has_mem_limits,
            }

            # Only include % utilization in aggregates if limits are available
            if has_cpu_limits and cpu_util_vals:
                aggregate_entry["avg_cpu_pct"] = round(sum(cpu_util_vals) / len(cpu_util_vals), 4)
            else:
                aggregate_entry["avg_cpu_pct"] = -1  # -1 indicates limits not defined

            if has_mem_limits and mem_util_vals:
                aggregate_entry["avg_mem_pct"] = round(sum(mem_util_vals) / len(mem_util_vals), 4)
            else:
                aggregate_entry["avg_mem_pct"] = -1  # -1 indicates limits not defined

            aggregates.append(aggregate_entry)

    # Final summary
    summary = {
        "env_name": env_name,
        "env_tag": env_tag,
        "entities": len(services) + len(pods),
        "metrics": ["cpu", "mem"],
        "date_range": {"start": str(start_time), "end": str(end_time), "tz": tz_label},
        "aggregates": aggregates,
        "warnings": warnings,
    }

    return {"files": files, "summary": summary}

# -----------------------------
# Query builder functions
# -----------------------------

def svc_cpu_query(tag: str, svc_filter: str) -> str:
    return (
        f"avg:kubernetes.cpu.usage.total{{env:{tag},service:{svc_filter}}}"
        " by {kube_container_name}"
    )

def svc_mem_query(tag: str, svc_filter: str) -> str:
    return (
        f"avg:kubernetes.memory.usage{{env:{tag},service:{svc_filter}}}"
        " by {kube_container_name}"
    )

def pod_cpu_query(ns: Optional[str], pod_filter: str) -> str:
    """
    Pod queries use pod_filter as kube_service and include kube_namespace.
    We group by kube_namespace so metrics are aggregated per namespace.
    """
    if ns:
        return (
            "avg:kubernetes.cpu.usage.total"
            f"{{kube_service:{pod_filter},kube_namespace:{ns}}} by {{kube_namespace}}"
        )
    # Fallback if namespace is not configured
    return (
        "avg:kubernetes.cpu.usage.total"
        f"{{kube_service:{pod_filter}}} by {{kube_namespace}}"
    )

def pod_mem_query(ns: Optional[str], pod_filter: str) -> str:
    """
    Pod queries use pod_filter as kube_service and include kube_namespace.
    We group by kube_namespace so metrics are aggregated per namespace.
    """
    if ns:
        return (
            "avg:kubernetes.memory.usage"
            f"{{kube_service:{pod_filter},kube_namespace:{ns}}} by {{kube_namespace}}"
        )
    return (
        "avg:kubernetes.memory.usage"
        f"{{kube_service:{pod_filter}}} by {{kube_namespace}}"
    )

# -----------------------------
# Query builder functions WITH LIMITS (Dynamic limits from Datadog)
# -----------------------------
# These functions return tuples of (usage_query, limits_query) for combined API requests.
# The limits are queried dynamically from Datadog rather than using static values from environments.json.
# TODO: Future enhancement - add kubernetes.cpu.requests and kubernetes.memory.requests
#       for calculating % utilization by request vs % utilization by limit.

def svc_cpu_with_limits_query(env_tag: str, svc_filter: str) -> Tuple[str, str]:
    """
    Return tuple of (usage_query, limits_query) for service CPU metrics.
    
    Args:
        env_tag: Environment tag (e.g., 'prod-east')
        svc_filter: Service filter pattern (e.g., 'web-app-svc*')
    
    Returns:
        Tuple of (usage_query, limits_query) strings for Datadog API
    """
    usage = f"avg:kubernetes.cpu.usage.total{{env:{env_tag},service:{svc_filter}}} by {{kube_container_name}}"
    limits = f"avg:kubernetes.cpu.limits{{env:{env_tag},service:{svc_filter}}} by {{kube_container_name}}"
    return usage, limits


def svc_mem_with_limits_query(env_tag: str, svc_filter: str) -> Tuple[str, str]:
    """
    Return tuple of (usage_query, limits_query) for service Memory metrics.
    
    Args:
        env_tag: Environment tag (e.g., 'prod-east')
        svc_filter: Service filter pattern (e.g., 'web-app-svc*')
    
    Returns:
        Tuple of (usage_query, limits_query) strings for Datadog API
    """
    usage = f"sum:kubernetes.memory.usage{{env:{env_tag},service:{svc_filter}}} by {{kube_container_name}}"
    limits = f"sum:kubernetes.memory.limits{{env:{env_tag},service:{svc_filter}}} by {{kube_container_name}}"
    return usage, limits


def pod_cpu_with_limits_query(kube_namespace: Optional[str], pod_filter: str) -> Tuple[str, str]:
    """
    Return tuple of (usage_query, limits_query) for pod CPU metrics.
    
    Args:
        kube_namespace: Kubernetes namespace (e.g., 'qa-eastus')
        pod_filter: Pod filter pattern (e.g., 'web-app*')
    
    Returns:
        Tuple of (usage_query, limits_query) strings for Datadog API
    """
    if kube_namespace:
        usage = f"avg:kubernetes.cpu.usage.total{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_service}}"
        limits = f"sum:kubernetes.cpu.limits{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_service}}"
    else:
        usage = f"avg:kubernetes.cpu.usage.total{{kube_service:{pod_filter}}} by {{kube_service}}"
        limits = f"sum:kubernetes.cpu.limits{{kube_service:{pod_filter}}} by {{kube_service}}"
    return usage, limits


def pod_mem_with_limits_query(kube_namespace: Optional[str], pod_filter: str) -> Tuple[str, str]:
    """
    Return tuple of (usage_query, limits_query) for pod Memory metrics.
    
    Args:
        kube_namespace: Kubernetes namespace (e.g., 'qa-eastus')
        pod_filter: Pod filter pattern (e.g., 'web-app*')
    
    Returns:
        Tuple of (usage_query, limits_query) strings for Datadog API
    """
    if kube_namespace:
        usage = f"sum:kubernetes.memory.usage{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_service}}"
        limits = f"sum:kubernetes.memory.limits{{kube_service:{pod_filter},kube_namespace:{kube_namespace}}} by {{kube_service}}"
    else:
        usage = f"sum:kubernetes.memory.usage{{kube_service:{pod_filter}}} by {{kube_service}}"
        limits = f"sum:kubernetes.memory.limits{{kube_service:{pod_filter}}} by {{kube_service}}"
    return usage, limits


# Small helper to normalize v2 timeseries into: { identifier -> [(ts_ms, value), ...] }
def _extract_series(attrs: Dict[str, Any], id_tag_keys: List[str]) -> Dict[str, List[Tuple[int, float]]]:
    times = attrs.get("times", []) or []
    series_list = attrs.get("series", []) or []
    values = attrs.get("values", []) or []

    per_id: Dict[str, List[Tuple[int, float]]] = {}
    for s_idx, series in enumerate(series_list):
        gtags = series.get("group_tags", []) or []

        identifier: str = ""
        # Try each tag key in order (e.g. kube_pod_name, kube_container_name)
        for tag_key in id_tag_keys:
            identifier = next(
                (t.split(":", 1)[1] for t in gtags if t.startswith(f"{tag_key}:")),
                identifier,
            )
            if identifier:
                break

        if not identifier:
            identifier = f"series_{s_idx}"

        row_vals = values[s_idx] if s_idx < len(values) else []
        pts: List[Tuple[int, float]] = []
        for t_idx, ts_ms in enumerate(times):
            if t_idx >= len(row_vals):
                break
            val = row_vals[t_idx]
            if val is None:
                continue
            try:
                pts.append((int(ts_ms), float(val)))
            except (TypeError, ValueError):
                continue

        if pts:
            per_id[identifier] = pts

    return per_id


def _extract_series_with_limits(
    attrs: Dict[str, Any],
    id_tag_keys: List[str],
    default_identifier: Optional[str] = None
) -> Tuple[Dict[str, List[Tuple[int, float]]], Dict[str, List[Tuple[int, float]]]]:
    """
    Extract both usage (query_index=0) and limits (query_index=1) series from Datadog response.
    
    This function parses the response from a combined usage+limits query request,
    separating the series by their query_index to return usage and limits data separately.
    
    Args:
        attrs: The 'attributes' section from Datadog v2 timeseries response
        id_tag_keys: List of tag keys to try for identifying series (e.g., ['kube_container_name'])
        default_identifier: Fallback identifier when no tag keys match (e.g., normalized filter name).
                            If None, falls back to 'series_{index}'.
    
    Returns:
        Tuple of (usage_series, limits_series) where each is:
        { identifier -> [(ts_ms, value), ...] }
    """
    times = attrs.get("times", []) or []
    series_list = attrs.get("series", []) or []
    values = attrs.get("values", []) or []
    
    usage_series: Dict[str, List[Tuple[int, float]]] = {}
    limits_series: Dict[str, List[Tuple[int, float]]] = {}
    
    for s_idx, series in enumerate(series_list):
        query_index = series.get("query_index", 0)
        gtags = series.get("group_tags", []) or []
        
        # Extract identifier from group tags
        identifier = ""
        for tag_key in id_tag_keys:
            identifier = next(
                (t.split(":", 1)[1] for t in gtags if t.startswith(f"{tag_key}:")),
                identifier
            )
            if identifier:
                break
        if not identifier:
            identifier = default_identifier or f"series_{s_idx}"
        
        # Extract values for this series
        row_vals = values[s_idx] if s_idx < len(values) else []
        pts: List[Tuple[int, float]] = []
        for t_idx, ts_ms in enumerate(times):
            if t_idx >= len(row_vals):
                break
            val = row_vals[t_idx]
            if val is None:
                continue
            try:
                pts.append((int(ts_ms), float(val)))
            except (TypeError, ValueError):
                continue
        
        # Route to appropriate series based on query_index
        # query_index 0 = usage, query_index 1 = limits
        if pts:
            if query_index == 0:
                usage_series[identifier] = pts
            elif query_index == 1:
                limits_series[identifier] = pts
    
    return usage_series, limits_series


def _fill_missing_limits_series(
    usage_series: Dict[str, List[Tuple[int, float]]],
    limits_series: Dict[str, List[Tuple[int, float]]]
) -> Dict[str, List[Tuple[int, float]]]:
    """
    Fill in missing limits series with 0.0 values when Datadog returns no limits data.
    
    This ensures CSV consistency - limits rows are always present even when
    Kubernetes doesn't have limits configured (Datadog may return empty series).
    
    Args:
        usage_series: Usage data per identifier { id -> [(ts_ms, value), ...] }
        limits_series: Limits data per identifier (may be empty)
    
    Returns:
        limits_series with synthetic 0.0 entries added for identifiers/timestamps
        that have usage data but no limits data
    """
    if limits_series:
        # Limits data exists, return as-is
        return limits_series
    
    if not usage_series:
        # No usage data either, nothing to fill
        return limits_series
    
    # Create synthetic 0.0 limits entries for each identifier/timestamp in usage_series
    filled_limits: Dict[str, List[Tuple[int, float]]] = {}
    for identifier, usage_pts in usage_series.items():
        filled_limits[identifier] = [(ts_ms, 0.0) for ts_ms, _ in usage_pts]
    
    return filled_limits


def _calculate_utilization_with_dynamic_limits(
    usage_series: Dict[str, List[Tuple[int, float]]],
    limits_series: Dict[str, List[Tuple[int, float]]],
    is_cpu: bool = True
) -> Tuple[Dict[str, List[Tuple[int, float]]], bool]:
    """
    Calculate % utilization from usage and dynamically-queried limits.
    
    Returns -1 for timestamps where limits are 0 or not available (industry-standard
    marker for "not defined").
    
    Args:
        usage_series: Usage data per identifier { id -> [(ts_ms, value), ...] }
        limits_series: Limits data per identifier { id -> [(ts_ms, value), ...] }
        is_cpu: True for CPU (usage in nanocores, limits in cores), False for Memory (both in bytes)
    
    Returns:
        Tuple of:
        - utilization_series: { identifier -> [(ts_ms, pct_or_minus1), ...] }
        - has_valid_limits: True if at least one valid limit > 0 was found
    """
    utilization_series: Dict[str, List[Tuple[int, float]]] = {}
    has_valid_limits = False
    
    for identifier, usage_pts in usage_series.items():
        limits_pts = limits_series.get(identifier, [])
        limits_by_ts = {ts: val for ts, val in limits_pts}
        
        pct_pts: List[Tuple[int, float]] = []
        for ts_ms, usage_val in usage_pts:
            limit_val = limits_by_ts.get(ts_ms, 0.0)
            
            if limit_val > 0:
                has_valid_limits = True
                if is_cpu:
                    # CPU: usage is in nanocores, limits are in cores
                    # Convert nanocores to cores: 1 core = 1e9 nanocores
                    usage_cores = usage_val / 1e9
                    pct = (usage_cores / limit_val) * 100.0
                else:
                    # Memory: both usage and limits are in bytes
                    pct = (usage_val / limit_val) * 100.0
                pct_pts.append((ts_ms, pct))
            else:
                # Limits not defined - output -1 as industry-standard marker
                pct_pts.append((ts_ms, -1))
        
        if pct_pts:
            utilization_series[identifier] = pct_pts
    
    return utilization_series, has_valid_limits


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