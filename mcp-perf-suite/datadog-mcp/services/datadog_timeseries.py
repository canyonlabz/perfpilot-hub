# services/datadog_timeseries.py

import os
import re
import csv
import json
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional
from fastmcp import Context
from utils.config import load_config
from utils.datadog_config_loader import load_environment_json, load_custom_queries_json
from utils.file_utils import backup_matching_files
from services.datadog_api import (
    _parse_to_utc,
    _ensure_artifacts_dir,
    _ensure_ready,
    _write_csv_header,
    _sanitize_filename,
    _normalize_k8s_filter,
    get_ssl_verify_setting,
    DD_API_KEY,
    DD_APP_KEY,
    V2_TIMESERIES_URL,
)

# Group tag keys that represent actual container/pod identifiers.
# Anything else found in group_tags is a non-container tag and gets
# encoded into the metric column as name[tag_key:tag_value].
_CONTAINER_POD_TAG_KEYS = frozenset([
    "kube_container_name",
    "kube_pod_name",
    "kube_service",
])

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


# -----------------------------------------------
# Public entry point
# -----------------------------------------------

async def collect_kpi_timeseries(
    env_name: str,
    query_names: List[str],
    start_time: str,
    end_time: str,
    run_id: str,
    ctx: Context,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute custom KPI timeseries queries against the Datadog V2 API and
    write standardized CSV files (one per entity).

    Args:
        env_name: Environment short name (e.g., 'QA', 'UAT').
        query_names: List of query group keys from kpi_queries in custom_queries.json.
        start_time: Start timestamp in UTC (epoch, ISO 8601, or datetime string).
        end_time: End timestamp in UTC (same formats as start_time).
        run_id: Test run identifier for artifacts (e.g., BlazeMeter run_id).
        ctx: FastMCP context for logging/errors.
        scope: Optional. "host" or "k8s". Auto-detected from environment if omitted.

    Returns:
        dict with "files" list and "summary" dict.
    """
    _ensure_ready()

    # 1. Load environment config
    env_config = await load_environment_json(env_name, ctx)
    if not env_config:
        msg = "No environment configuration available. Check environments.json."
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    env_name_resolved = env_config.get("environment_name", env_name)
    env_tag = env_config.get("env_tag", "unknown")

    # 2. Load kpi_queries
    custom_queries = await load_custom_queries_json()
    kpi_queries: Dict[str, Any] = custom_queries.get("kpi_queries", {})
    if not kpi_queries:
        msg = "No kpi_queries section found in custom_queries.json."
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    # 3. Validate requested query_names exist
    missing = [qn for qn in query_names if qn not in kpi_queries]
    if missing:
        msg = f"Query name(s) not found in kpi_queries: {missing}"
        await ctx.error(msg)
        return {"files": [], "summary": {"warnings": [msg]}}

    # 4. Validate unique query names within each requested group (BLOCKING)
    for qn in query_names:
        group = kpi_queries[qn]
        names_in_group = [q["name"] for q in group.get("queries", [])]
        seen = set()
        duplicates = set()
        for n in names_in_group:
            if n in seen:
                duplicates.add(n)
            seen.add(n)
        if duplicates:
            msg = (
                f"Duplicate query name(s) {sorted(duplicates)} found in query group "
                f"'{qn}'. Each 'name' must be unique within a query group so CSV "
                f"metric values are unambiguous. Please fix and re-run."
            )
            await ctx.warning(msg)
            return {"files": [], "summary": {"warnings": [msg]}}

    # 5. Determine scope
    detected_scope = _detect_scope(env_config, scope)
    await ctx.info(f"Scope resolved to: {detected_scope}")

    # 6. Validate static queries for env_tag mismatch (BLOCKING)
    for qn in query_names:
        group = kpi_queries[qn]
        if _is_template_query(group):
            continue
        env_mismatch = _check_static_env_mismatch(group, env_tag, qn)
        if env_mismatch:
            await ctx.error(env_mismatch)
            return {"files": [], "summary": {"warnings": [env_mismatch]}}

    # Parse timestamps
    _, _, v2_from_ms, v2_to_ms, tz_label = _parse_to_utc(start_time, end_time)

    run = str(run_id) if run_id else "mock_run_id"
    outdir = _ensure_artifacts_dir(run)

    # 7. Backup existing KPI CSV files before any API calls
    backed_up = backup_matching_files(outdir, "kpi_metrics_*.csv")
    if backed_up:
        await ctx.info(f"Backed up {len(backed_up)} existing KPI CSV file(s) to backups/")

    # 8. Process each requested query group
    # entity_rows collects CSV rows keyed by entity name.
    # Each value is a list of row tuples ready for csv.writerow().
    entity_rows: Dict[str, List[list]] = {}
    per_query_summary: List[Dict[str, Any]] = []
    warnings: List[str] = []

    headers = {
        "DD-API-KEY": DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
        "Content-Type": "application/json",
    }
    verify_ssl = get_ssl_verify_setting()

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        for qn in query_names:
            group = kpi_queries[qn]
            description = group.get("description", "")
            interval = group.get("interval", 300000)
            query_defs = group.get("queries", [])
            formulas = group.get("formulas", [])

            metric_names = [q["name"] for q in query_defs]

            if _is_template_query(group):
                entities = _resolve_entities(env_config, group, detected_scope)
                if not entities:
                    warn = f"Query group '{qn}': no matching entities in environment for placeholder iteration."
                    warnings.append(warn)
                    await ctx.warning(warn)
                    continue

                datapoints_per_entity: Dict[str, int] = {}
                for entity_name, placeholders in entities:
                    substituted_queries = _substitute_placeholders(query_defs, placeholders, env_tag)
                    body = _build_kpi_request(v2_from_ms, v2_to_ms, substituted_queries, formulas, interval)

                    try:
                        resp = await client.post(V2_TIMESERIES_URL, headers=headers, json=body, timeout=60.0)
                        resp.raise_for_status()
                        attrs = resp.json().get("data", {}).get("attributes", {})
                    except Exception as e:
                        warn = f"Query group '{qn}', entity '{entity_name}': API error — {e}"
                        warnings.append(warn)
                        await ctx.error(warn)
                        continue

                    rows = _parse_response_to_rows(
                        attrs=attrs,
                        query_defs=substituted_queries,
                        env_name=env_name_resolved,
                        env_tag=env_tag,
                        scope=detected_scope,
                        entity_name=entity_name,
                        entity_type=_entity_type_from_placeholders(placeholders),
                    )

                    if rows:
                        entity_rows.setdefault(entity_name, []).extend(rows)
                        datapoints_per_entity[entity_name] = datapoints_per_entity.get(entity_name, 0) + len(rows)

                per_query_summary.append({
                    "query_name": qn,
                    "description": description,
                    "metrics": metric_names,
                    "datapoints_per_entity": datapoints_per_entity,
                })
            else:
                # Static query
                target_entity = group.get("target_entity", qn)
                body = _build_kpi_request(v2_from_ms, v2_to_ms, query_defs, formulas, interval)

                try:
                    resp = await client.post(V2_TIMESERIES_URL, headers=headers, json=body, timeout=60.0)
                    resp.raise_for_status()
                    attrs = resp.json().get("data", {}).get("attributes", {})
                except Exception as e:
                    warn = f"Query group '{qn}' (static): API error — {e}"
                    warnings.append(warn)
                    await ctx.error(warn)
                    continue

                rows = _parse_response_to_rows(
                    attrs=attrs,
                    query_defs=query_defs,
                    env_name=env_name_resolved,
                    env_tag=env_tag,
                    scope=detected_scope,
                    entity_name=target_entity,
                    entity_type=_entity_type_from_scope(detected_scope),
                )

                if rows:
                    entity_rows.setdefault(target_entity, []).extend(rows)
                    per_query_summary.append({
                        "query_name": qn,
                        "description": description,
                        "metrics": metric_names,
                        "datapoints_per_entity": {target_entity: len(rows)},
                    })
                else:
                    per_query_summary.append({
                        "query_name": qn,
                        "description": description,
                        "metrics": metric_names,
                        "datapoints_per_entity": {},
                    })

    # 9. Write CSV files (one per entity)
    files: List[str] = []
    for entity_name, rows in entity_rows.items():
        if not rows:
            continue
        safe_name = _normalize_k8s_filter(entity_name) if detected_scope == "k8s" else _sanitize_filename(entity_name)
        fname = f"kpi_metrics_[{safe_name}].csv"
        outcsv = os.path.join(outdir, fname)

        with open(outcsv, "w", newline="", encoding="utf-8") as fcsv:
            w = csv.writer(fcsv)
            _write_csv_header(w)
            for row in rows:
                w.writerow(row)

        files.append(outcsv)

    # 10. Return summary
    all_entities = set()
    for pqs in per_query_summary:
        all_entities.update(pqs.get("datapoints_per_entity", {}).keys())

    summary = {
        "env_name": env_name_resolved,
        "env_tag": env_tag,
        "scope": detected_scope,
        "queries_executed": query_names,
        "entities": len(all_entities),
        "date_range": {"start": str(start_time), "end": str(end_time), "tz": tz_label},
        "per_query_summary": per_query_summary,
        "warnings": warnings,
    }

    return {"files": files, "summary": summary}


# -----------------------------------------------
# Placeholder helpers
# -----------------------------------------------

def _is_template_query(group: Dict[str, Any]) -> bool:
    """Return True if any query in the group contains {{placeholders}}."""
    for q in group.get("queries", []):
        if _PLACEHOLDER_RE.search(q.get("query", "")):
            return True
    return False


def _detect_scope(env_config: Dict[str, Any], user_scope: Optional[str]) -> str:
    """Determine scope from user input or environment structure."""
    if user_scope:
        return user_scope.lower()

    k8s_cfg = env_config.get("kubernetes", {}) or {}
    has_k8s = bool(k8s_cfg.get("services")) or bool(k8s_cfg.get("pods"))
    has_hosts = bool(env_config.get("hosts"))

    if has_k8s and not has_hosts:
        return "k8s"
    if has_hosts and not has_k8s:
        return "host"
    # Both present — default to k8s (user can override)
    return "k8s"


def _check_static_env_mismatch(group: Dict[str, Any], env_tag: str, query_name: str) -> str:
    """
    For static queries, extract the env: tag from the query and compare
    against the loaded environment's env_tag. Returns an error message
    string if there is a mismatch, empty string otherwise.
    """
    env_tag_pattern = re.compile(r"env:([^,}\s]+)")
    for q in group.get("queries", []):
        query_str = q.get("query", "")
        # Skip template queries
        if _PLACEHOLDER_RE.search(query_str):
            return ""
        match = env_tag_pattern.search(query_str)
        if match:
            found_tag = match.group(1)
            if found_tag != env_tag:
                return (
                    f"Query '{query_name}' contains env tag '{found_tag}' but loaded "
                    f"environment has env_tag '{env_tag}'. This is a mismatch — the "
                    f"query targets a different environment than the one provided. "
                    f"Please either:\n"
                    f"  1. Fix the hardcoded env tag in the query, or\n"
                    f"  2. Use the correct environment name, or\n"
                    f"  3. Convert the query to use {{{{env_tag}}}} placeholders."
                )
    return ""


def _resolve_entities(
    env_config: Dict[str, Any],
    group: Dict[str, Any],
    scope: str,
) -> List[Tuple[str, Dict[str, str]]]:
    """
    Based on placeholders found in the query group, return a list of
    (entity_name, placeholder_values) tuples to iterate over.
    """
    sample_query = ""
    for q in group.get("queries", []):
        sample_query = q.get("query", "")
        if sample_query:
            break

    placeholders = set(_PLACEHOLDER_RE.findall(sample_query))
    kube_namespace = env_config.get("kube_namespace") or env_config.get("namespace", "")

    if "service_filter" in placeholders or "kube_service" in placeholders:
        k8s_services = (env_config.get("kubernetes", {}) or {}).get("services", []) or []
        entities = []
        for svc in k8s_services:
            sf = svc.get("service_filter") or svc.get("kube_service")
            if sf:
                entities.append((sf, {
                    "service_filter": sf,
                    "kube_service": sf,
                    "kube_namespace": kube_namespace,
                }))
        return entities

    if "hostname" in placeholders:
        hosts = env_config.get("hosts", []) or []
        return [(h["hostname"], {"hostname": h["hostname"]}) for h in hosts if h.get("hostname")]

    if "pod_filter" in placeholders:
        k8s_pods = (env_config.get("kubernetes", {}) or {}).get("pods", []) or []
        entities = []
        for pod in k8s_pods:
            pf = pod.get("pod_filter") or pod.get("kube_service")
            if pf:
                entities.append((pf, {
                    "pod_filter": pf,
                    "kube_service": pf,
                    "kube_namespace": kube_namespace,
                }))
        return entities

    return []


def _substitute_placeholders(
    query_defs: List[Dict[str, str]],
    placeholders: Dict[str, str],
    env_tag: str,
) -> List[Dict[str, str]]:
    """
    Return a copy of query_defs with all {{placeholder}} tokens substituted.
    Always includes env_tag in the substitution map.
    """
    sub_map = {"env_tag": env_tag}
    sub_map.update(placeholders)

    result = []
    for q in query_defs:
        new_q = dict(q)
        query_str = q.get("query", "")
        for key, value in sub_map.items():
            query_str = query_str.replace(f"{{{{{key}}}}}", value)
        new_q["query"] = query_str
        result.append(new_q)
    return result


def _entity_type_from_placeholders(placeholders: Dict[str, str]) -> str:
    if "hostname" in placeholders:
        return "host"
    return "k8s"


def _entity_type_from_scope(scope: str) -> str:
    return "host" if scope == "host" else "k8s"


# -----------------------------------------------
# API request builder
# -----------------------------------------------

def _build_kpi_request(
    from_ms: int,
    to_ms: int,
    query_defs: List[Dict[str, str]],
    formulas: List[Dict[str, str]],
    interval: int = 300000,
) -> Dict[str, Any]:
    """Build a Datadog V2 timeseries request body from query definitions."""
    queries = []
    for q in query_defs:
        queries.append({
            "data_source": q.get("data_source", "metrics"),
            "name": q["name"],
            "query": q["query"],
        })

    return {
        "data": {
            "type": "timeseries_request",
            "interval": interval,
            "attributes": {
                "from": from_ms,
                "to": to_ms,
                "queries": queries,
                "formulas": list(formulas),
            }
        }
    }


# -----------------------------------------------
# Response parsing → CSV rows
# -----------------------------------------------

def _parse_response_to_rows(
    attrs: Dict[str, Any],
    query_defs: List[Dict[str, str]],
    env_name: str,
    env_tag: str,
    scope: str,
    entity_name: str,
    entity_type: str,
) -> List[list]:
    """
    Parse a Datadog V2 timeseries response into CSV rows.

    Produces rows in metric-type block order: all datapoints for one
    metric are emitted chronologically before the next metric.

    Returns:
        List of row lists matching the standard 10-column CSV schema.
    """
    times = attrs.get("times", []) or []
    series_list = attrs.get("series", []) or []
    values = attrs.get("values", []) or []

    # Build query_index → name mapping
    name_by_index = {i: q["name"] for i, q in enumerate(query_defs)}

    # Collect parsed series grouped by their effective metric name.
    # A series may have group_tags that split it into multiple sub-series.
    # metric_key → list of (container_or_pod, timestamps_values_list)
    metric_groups: Dict[str, List[Tuple[str, List[Tuple[str, float, str]]]]] = {}

    for s_idx, series in enumerate(series_list):
        query_index = series.get("query_index", 0)
        base_name = name_by_index.get(query_index, f"query_{query_index}")
        group_tags = series.get("group_tags", []) or []

        # Extract unit
        unit_info = series.get("unit", [None])
        unit_str = ""
        if unit_info and unit_info[0]:
            unit_str = unit_info[0].get("plural", "") or unit_info[0].get("name", "")

        # Determine container_or_pod and metric suffix from group_tags
        container_or_pod = ""
        metric_suffix_parts = []

        for tag_str in group_tags:
            if ":" not in tag_str:
                continue
            tag_key, tag_val = tag_str.split(":", 1)
            if tag_key in _CONTAINER_POD_TAG_KEYS:
                container_or_pod = tag_val
            else:
                metric_suffix_parts.append(f"{tag_key}:{tag_val}")

        metric_name = base_name
        if metric_suffix_parts:
            metric_name = f"{base_name}[{','.join(metric_suffix_parts)}]"

        # Extract timeseries values
        row_vals = values[s_idx] if s_idx < len(values) else []
        ts_val_list: List[Tuple[str, float, str]] = []
        for t_idx, ts_ms in enumerate(times):
            if t_idx >= len(row_vals):
                break
            val = row_vals[t_idx]
            if val is None:
                continue
            try:
                dt_iso = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                ts_val_list.append((dt_iso, float(val), unit_str))
            except (TypeError, ValueError):
                continue

        if ts_val_list:
            metric_groups.setdefault(metric_name, []).append((container_or_pod, ts_val_list))

    # Build rows in metric-type block order
    hostname_col = entity_name if entity_type == "host" else ""
    filter_col = entity_name if entity_type == "k8s" else ""

    rows: List[list] = []
    for metric_name, series_entries in metric_groups.items():
        for container_or_pod, ts_val_list in series_entries:
            for dt_iso, val, unit_str in ts_val_list:
                rows.append([
                    env_name, env_tag, scope,
                    hostname_col, filter_col, container_or_pod,
                    dt_iso, metric_name, val, unit_str,
                ])

    return rows
