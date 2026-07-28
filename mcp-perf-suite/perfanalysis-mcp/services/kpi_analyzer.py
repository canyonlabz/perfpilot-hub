"""
KPI Timeseries Analysis Module

Handles analysis of application-level KPI metrics from APM-generated
kpi_metrics_*.csv files.  Produces per-service summaries with category
tagging, trend detection, and insight generation.

This module is called FROM existing PerfAnalysis tools — it does not
register any MCP tools itself.

Technology-neutral: works with any runtime/APM tool.  Metric categorization
is pattern-based via the shared CATEGORY_REGISTRY in kpi_utils.
"""
import datetime
from typing import Dict, List, Optional, Any, Tuple
from fastmcp import Context
from pathlib import Path
import pandas as pd
import numpy as np

from utils.kpi_utils import (
    load_kpi_dataframe,
    load_kpi_pivoted,
    discover_kpi_files,
    detect_kpi_scope,
    categorize_metric,
    get_display_unit,
    get_conversion_factor,
    compute_metric_summary,
    CATEGORY_REGISTRY,
)
from utils.file_processor import (
    write_json_output,
    write_markdown_output,
)


# -----------------------------------------------
# Tool 1: analyze_environment_metrics
# -----------------------------------------------

async def analyze_kpi_metrics(
    kpi_files: List[Path],
    environments_config: Dict,
    config: Dict,
    ctx: Context,
) -> Dict[str, Any]:
    """Analyze KPI timeseries data and produce per-entity, per-metric summaries.

    Reads all kpi_metrics_*.csv files, groups by identifier (``hostname`` for
    host scope, ``filter`` / service name for k8s scope), computes descriptive
    statistics and trend per metric, and tags each metric with its category.

    Args:
        kpi_files:            Sorted list of kpi_metrics_*.csv paths.
        environments_config:  Environment config dict (from environments.json).
        config:               PerfAnalysis config dict.
        ctx:                  FastMCP context for progress logging.

    Returns:
        Dict with keys: ``services``, ``scope``, ``identifier_type``,
        ``metric_categories_found``, ``total_services``, ``kpi_insights``,
        ``analysis_timestamp``.
    """
    kpi_analysis: Dict[str, Any] = {
        "services": {},
        "scope": "k8s",
        "identifier_type": "service",
        "metric_categories_found": [],
        "total_services": 0,
        "kpi_insights": [],
        "analysis_timestamp": datetime.datetime.now().isoformat(),
    }

    await ctx.info(f"KPI Analysis: Processing {len(kpi_files)} KPI metrics file(s)")

    raw_df = load_kpi_dataframe(kpi_files)
    if raw_df is None or raw_df.empty:
        await ctx.warning("KPI Analysis: KPI CSV files found but contained no data")
        return kpi_analysis

    if "identifier" not in raw_df.columns:
        await ctx.warning("KPI Analysis: KPI CSV missing identifier column — skipping")
        return kpi_analysis

    scope_type = detect_kpi_scope(raw_df)
    identifier_type = "host" if scope_type == "host" else "service"
    entity_label = "host" if scope_type == "host" else "service"

    kpi_analysis["scope"] = scope_type
    kpi_analysis["identifier_type"] = identifier_type

    identifier_col = "identifier"
    entities = raw_df[identifier_col].unique()
    categories_seen: set = set()

    for entity in entities:
        entity_data = raw_df[raw_df[identifier_col] == entity]
        entity_metrics = entity_data["metric"].unique()
        entity_summary: Dict[str, Any] = {}

        for metric_name in entity_metrics:
            metric_rows = entity_data[entity_data["metric"] == metric_name]
            category = categorize_metric(metric_name)
            categories_seen.add(category)

            original_unit = str(metric_rows["unit"].iloc[0]) if "unit" in metric_rows.columns else "unknown"
            display_unit = get_display_unit(metric_name, original_unit)

            factor = get_conversion_factor(metric_name)
            converted_values = metric_rows["value"] * factor if factor != 1.0 else metric_rows["value"]
            stats = compute_metric_summary(converted_values)

            entity_summary[metric_name] = {
                "category": category,
                "unit": original_unit,
                "display_unit": display_unit,
                **stats,
            }

        kpi_analysis["services"][entity] = entity_summary

    kpi_analysis["total_services"] = len(entities)
    kpi_analysis["metric_categories_found"] = sorted(categories_seen)

    kpi_analysis["kpi_insights"] = generate_kpi_insights(kpi_analysis)

    await ctx.info(
        f"KPI Analysis Complete: Analyzed {len(entities)} {entity_label}(s), "
        f"{len(categories_seen)} category/categories: {', '.join(sorted(categories_seen))}"
    )

    return kpi_analysis


# -----------------------------------------------
# Insight Generation
# -----------------------------------------------

def generate_kpi_insights(kpi_analysis: Dict[str, Any]) -> List[str]:
    """Generate human-readable insights from the KPI analysis results.

    Scans per-service metric summaries and produces actionable observations
    about latency health, GC behaviour, throughput consistency, error trends,
    and other KPI categories.

    Args:
        kpi_analysis: The dict produced by :func:`analyze_kpi_metrics`.

    Returns:
        List of insight strings.
    """
    insights: List[str] = []

    for service, metrics in kpi_analysis.get("services", {}).items():
        short_name = _short_service_name(service)

        for metric_name, stats in metrics.items():
            category = stats.get("category", "custom")
            trend = stats.get("trend", "stable")
            samples = stats.get("samples", 0)
            if samples == 0:
                continue

            if category == "latency":
                _add_latency_insights(insights, short_name, metric_name, stats)

            elif category in ("gc_pressure", "gc_pause"):
                _add_gc_pressure_insights(insights, short_name, metric_name, stats)

            elif category == "gc_heap":
                _add_gc_heap_insights(insights, short_name, metric_name, stats)

            elif category == "gc_activity":
                if trend == "increasing":
                    insights.append(
                        f"{short_name}: {metric_name} shows increasing GC frequency "
                        f"(trend: {trend}) — may indicate growing allocation pressure"
                    )

            elif category == "throughput":
                _add_throughput_insights(insights, short_name, metric_name, stats)

            elif category == "errors":
                _add_error_insights(insights, short_name, metric_name, stats)

            elif category == "contention":
                if trend == "increasing":
                    insights.append(
                        f"{short_name}: {metric_name} trending upward — "
                        f"investigate potential lock contention hotspots"
                    )

            elif category == "threads":
                if trend == "increasing":
                    insights.append(
                        f"{short_name}: {metric_name} growing over time — "
                        f"thread pool may be under pressure"
                    )

            elif category == "connections":
                if trend == "increasing":
                    insights.append(
                        f"{short_name}: {metric_name} trending upward — "
                        f"check for connection pool saturation"
                    )

            elif category == "process_cpu":
                p90 = stats.get("p90")
                if p90 is not None and p90 > 80:
                    insights.append(
                        f"{short_name}: {metric_name} P90 at {p90:.1f}% — "
                        f"process-level CPU near saturation"
                    )

            elif category == "process_memory":
                if trend == "increasing":
                    insights.append(
                        f"{short_name}: {metric_name} trending upward — "
                        f"possible process-level memory growth"
                    )

            elif category == "host_cpu":
                _add_host_cpu_insights(insights, short_name, metric_name, stats)

            elif category == "host_memory":
                _add_host_memory_insights(insights, short_name, metric_name, stats)

            elif category == "disk_io":
                _add_disk_io_insights(insights, short_name, metric_name, stats)

            elif category == "disk_usage":
                _add_disk_usage_insights(insights, short_name, metric_name, stats)

            elif category == "network_io":
                _add_network_io_insights(insights, short_name, metric_name, stats)

            elif category == "iis":
                _add_iis_insights(insights, short_name, metric_name, stats)

            elif category == "sql_server":
                _add_sql_server_insights(insights, short_name, metric_name, stats)

            elif category == "process_queue":
                _add_process_queue_insights(insights, short_name, metric_name, stats)

    return insights


def _add_latency_insights(
    insights: List[str], svc: str, metric: str, stats: Dict
) -> None:
    """Append latency-specific insights."""
    p95 = stats.get("p95")
    p90 = stats.get("p90")
    trend = stats.get("trend", "stable")
    display_unit = stats.get("display_unit", stats.get("unit", ""))

    if p95 is not None and p90 is not None:
        insights.append(
            f"{svc}: {metric} P90={p90:.4f}{display_unit}, "
            f"P95={p95:.4f}{display_unit} — "
            + ("stable" if trend == "stable" else f"trend: {trend}")
        )
    if trend == "increasing":
        insights.append(
            f"{svc}: {metric} shows increasing latency — "
            f"investigate potential degradation"
        )


def _add_gc_pressure_insights(
    insights: List[str], svc: str, metric: str, stats: Dict
) -> None:
    """Append GC pressure / pause insights."""
    p90 = stats.get("p90")
    avg = stats.get("avg")
    trend = stats.get("trend", "stable")
    display_unit = stats.get("display_unit", stats.get("unit", ""))

    if avg is not None and p90 is not None:
        if stats.get("category") == "gc_pressure" and p90 > 85:
            insights.append(
                f"{svc}: {metric} P90 at {p90:.1f}{display_unit} — "
                f"GC pressure is high, may impact responsiveness"
            )
        elif stats.get("category") == "gc_pressure":
            insights.append(
                f"{svc}: {metric} avg {avg:.1f}{display_unit}, "
                f"P90 {p90:.1f}{display_unit} — "
                f"{'no GC pressure detected' if trend == 'stable' else f'trend: {trend}'}"
            )


def _add_gc_heap_insights(
    insights: List[str], svc: str, metric: str, stats: Dict
) -> None:
    """Append GC heap size insights (focus on growth trends)."""
    trend = stats.get("trend", "stable")
    if trend == "increasing":
        min_val = stats.get("min", 0)
        max_val = stats.get("max", 0)
        display_unit = stats.get("display_unit", stats.get("unit", ""))
        insights.append(
            f"{svc}: {metric} shows gradual increase "
            f"({min_val:.1f}→{max_val:.1f}{display_unit}, trend: {trend}) — "
            f"potential slow memory growth"
        )


def _add_throughput_insights(
    insights: List[str], svc: str, metric: str, stats: Dict
) -> None:
    """Append throughput-related insights."""
    avg = stats.get("avg")
    std_dev = stats.get("std_dev", 0)
    trend = stats.get("trend", "stable")

    if avg is not None and avg > 0:
        cv = (std_dev / avg) if avg != 0 else 0
        if cv > 0.5:
            insights.append(
                f"{svc}: {metric} has high variability "
                f"(CV={cv:.2f}) — throughput is inconsistent"
            )
        if trend == "decreasing":
            insights.append(
                f"{svc}: {metric} trending downward — "
                f"server-side throughput may be declining"
            )


def _add_error_insights(
    insights: List[str], svc: str, metric: str, stats: Dict
) -> None:
    """Append error/exception insights."""
    max_val = stats.get("max", 0)
    avg = stats.get("avg", 0)
    trend = stats.get("trend", "stable")

    if max_val is not None and max_val > 0:
        insights.append(
            f"{svc}: {metric} detected — peak {max_val:.0f}, "
            f"avg {avg:.1f} ({'stable' if trend == 'stable' else f'trend: {trend}'})"
        )
        if trend == "increasing":
            insights.append(
                f"{svc}: {metric} trending upward — investigate error source"
            )


def _add_host_cpu_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append host-level CPU insights."""
    p90 = stats.get("p90")
    max_val = stats.get("max")
    trend = stats.get("trend", "stable")

    if metric.endswith("_idle") and p90 is not None:
        if p90 < 20:
            insights.append(
                f"{host}: {metric} P90={p90:.1f}% (min idle) — "
                f"host CPU is heavily utilized"
            )
        elif p90 < 50:
            insights.append(
                f"{host}: {metric} P90={p90:.1f}% idle — "
                f"moderate CPU utilization on host"
            )
    elif metric.endswith("_user") and max_val is not None and max_val > 70:
        insights.append(
            f"{host}: {metric} peaked at {max_val:.1f}% — "
            f"high user-mode CPU on host"
        )
    elif metric.endswith("_iowait") and p90 is not None and p90 > 5:
        insights.append(
            f"{host}: {metric} P90={p90:.1f}% — "
            f"elevated I/O wait detected on host"
        )
    elif metric.endswith("_system") and max_val is not None and max_val > 20:
        insights.append(
            f"{host}: {metric} peaked at {max_val:.1f}% — "
            f"high kernel-mode CPU on host"
        )


def _add_host_memory_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append host memory insights."""
    trend = stats.get("trend", "stable")
    display_unit = stats.get("display_unit", stats.get("unit", ""))

    if "usable" in metric:
        min_val = stats.get("min")
        avg = stats.get("avg")
        if min_val is not None and avg is not None:
            insights.append(
                f"{host}: {metric} avg={avg:.1f}{display_unit}, "
                f"min={min_val:.1f}{display_unit}"
                + (f" — memory availability declining" if trend == "decreasing"
                   else " — memory availability stable")
            )
    elif "total" in metric:
        avg = stats.get("avg")
        if avg is not None:
            insights.append(
                f"{host}: {metric} = {avg:.1f}{display_unit} total host memory"
            )


def _add_disk_io_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append disk I/O queue insights."""
    p90 = stats.get("p90")
    max_val = stats.get("max")

    if p90 is not None and max_val is not None:
        if p90 > 2:
            insights.append(
                f"{host}: {metric} P90={p90:.2f} — "
                f"sustained disk queuing detected (peak {max_val:.2f})"
            )
        elif max_val > 2:
            insights.append(
                f"{host}: {metric} peak={max_val:.2f} — "
                f"intermittent disk queue spikes"
            )


def _add_disk_usage_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append disk usage insights."""
    trend = stats.get("trend", "stable")
    display_unit = stats.get("display_unit", stats.get("unit", ""))
    avg = stats.get("avg")

    if avg is not None:
        insight = f"{host}: {metric} avg={avg:.1f}{display_unit}"
        if trend == "increasing":
            insight += " — disk usage growing over test duration"
        insights.append(insight)


def _add_network_io_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append network I/O insights."""
    max_val = stats.get("max")
    avg = stats.get("avg")
    display_unit = stats.get("display_unit", stats.get("unit", ""))

    if max_val is not None and avg is not None:
        cv = (stats.get("std_dev", 0) / avg) if avg > 0 else 0
        insight = (
            f"{host}: {metric} avg={avg:.1f}{display_unit}/s, "
            f"peak={max_val:.1f}{display_unit}/s"
        )
        if cv > 0.8:
            insight += " — highly variable network traffic"
        insights.append(insight)


def _add_iis_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append IIS web server insights."""
    max_val = stats.get("max")
    avg = stats.get("avg")
    trend = stats.get("trend", "stable")
    display_unit = stats.get("display_unit", stats.get("unit", ""))

    if "connections" in metric and max_val is not None:
        insights.append(
            f"{host}: {metric} peak={max_val:.0f} concurrent IIS connections"
            + (f" — trending {trend}" if trend != "stable" else "")
        )
    elif "bytes" in metric and max_val is not None and avg is not None:
        insights.append(
            f"{host}: {metric} avg={avg:.0f}{display_unit}/s, "
            f"peak={max_val:.0f}{display_unit}/s"
        )
    elif "method_" in metric and max_val is not None and max_val > 0:
        method = metric.split("method_")[-1].upper() if "method_" in metric else metric
        insights.append(
            f"{host}: IIS {method} requests avg={avg:.2f}/s, peak={max_val:.2f}/s"
        )


def _add_sql_server_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append SQL Server insights."""
    avg = stats.get("avg")
    max_val = stats.get("max")
    p90 = stats.get("p90")
    trend = stats.get("trend", "stable")
    display_unit = stats.get("display_unit", stats.get("unit", ""))

    if "buffer_cache_hit_ratio" in metric and avg is not None:
        if avg < 0.95:
            insights.append(
                f"{host}: {metric} avg={avg:.4f} — "
                f"buffer cache hit ratio below 95%, possible memory pressure"
            )
        else:
            insights.append(
                f"{host}: {metric} avg={avg:.4f} — healthy buffer cache performance"
            )
    elif "lock_waits" in metric:
        if max_val is not None and max_val > 0:
            insights.append(
                f"{host}: {metric} detected — "
                f"peak={max_val:.2f}{display_unit}/s, investigate blocking"
            )
    elif "compilations" in metric and p90 is not None:
        insights.append(
            f"{host}: {metric} P90={p90:.1f}{display_unit}/s"
            + (f" — compilations increasing" if trend == "increasing" else "")
        )
    elif "user_connections" in metric and max_val is not None:
        insights.append(
            f"{host}: {metric} peak={max_val:.0f}, avg={avg:.0f} active connections"
            + (f" — trending {trend}" if trend != "stable" else "")
        )


def _add_process_queue_insights(
    insights: List[str], host: str, metric: str, stats: Dict
) -> None:
    """Append processor queue length insights."""
    p90 = stats.get("p90")
    max_val = stats.get("max")

    if p90 is not None and max_val is not None:
        if p90 > 2:
            insights.append(
                f"{host}: {metric} P90={p90:.1f} threads — "
                f"sustained processor queuing (peak {max_val:.1f})"
            )
        elif max_val > 5:
            insights.append(
                f"{host}: {metric} peak={max_val:.1f} threads — "
                f"intermittent processor queue spikes"
            )


def _short_service_name(full_service: str) -> str:
    """Shorten a dotted service name to its last two segments for readability."""
    parts = full_service.rsplit(".", 2)
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return full_service


# -----------------------------------------------
# Output Generation
# -----------------------------------------------

async def generate_kpi_outputs(
    kpi_analysis: Dict[str, Any],
    output_path: Path,
    test_run_id: str,
    ctx: Context,
) -> Dict[str, str]:
    """Write KPI analysis results to standalone output files.

    Produces:
      - ``kpi_analysis.json``  — full structured analysis
      - ``kpi_summary.md``     — human-readable markdown summary

    Args:
        kpi_analysis:  Dict returned by :func:`analyze_kpi_metrics`.
        output_path:   Analysis output directory (artifacts/{run}/analysis/).
        test_run_id:   Current test run ID.
        ctx:           FastMCP context.

    Returns:
        Dict mapping output type to file path string.
    """
    output_files: Dict[str, str] = {}

    try:
        json_file = output_path / "kpi_analysis.json"
        await write_json_output(kpi_analysis, json_file)
        output_files["json"] = str(json_file)

        md_file = output_path / "kpi_summary.md"
        md_content = _format_kpi_markdown(kpi_analysis, test_run_id)
        await write_markdown_output(md_content, md_file)
        output_files["markdown"] = str(md_file)

        await ctx.info(
            f"KPI Output Generation: Generated {len(output_files)} KPI analysis file(s)"
        )

    except Exception as e:
        await ctx.error(
            f"KPI Output Generation Error: Failed to generate KPI outputs: {str(e)}"
        )

    return output_files


def _format_kpi_markdown(kpi_analysis: Dict[str, Any], test_run_id: str) -> str:
    """Format KPI analysis as a Markdown summary."""
    lines: List[str] = []
    lines.append(f"# KPI Analysis Summary — {test_run_id}")
    lines.append("")
    lines.append(f"**Services analyzed:** {kpi_analysis.get('total_services', 0)}")
    categories = kpi_analysis.get("metric_categories_found", [])
    lines.append(f"**Metric categories:** {', '.join(categories) if categories else 'none'}")
    lines.append(f"**Generated:** {kpi_analysis.get('analysis_timestamp', 'N/A')}")
    lines.append("")

    for service, metrics in kpi_analysis.get("services", {}).items():
        lines.append(f"## {service}")
        lines.append("")

        if not metrics:
            lines.append("_No KPI metrics found for this service._")
            lines.append("")
            continue

        lines.append("| Metric | Category | Avg | P90 | P95 | Min | Max | Trend | Samples |")
        lines.append("|--------|----------|-----|-----|-----|-----|-----|-------|---------|")

        for metric_name, stats in metrics.items():
            cat = stats.get("category", "custom")
            du = stats.get("display_unit", "")
            suffix = f" {du}" if du else ""

            def _fmt(val: Any) -> str:
                if val is None:
                    return "N/A"
                if isinstance(val, float):
                    return f"{val:.4f}{suffix}" if abs(val) < 10 else f"{val:.1f}{suffix}"
                return str(val)

            lines.append(
                f"| {metric_name} | {cat} "
                f"| {_fmt(stats.get('avg'))} "
                f"| {_fmt(stats.get('p90'))} "
                f"| {_fmt(stats.get('p95'))} "
                f"| {_fmt(stats.get('min'))} "
                f"| {_fmt(stats.get('max'))} "
                f"| {stats.get('trend', 'N/A')} "
                f"| {stats.get('samples', 0)} |"
            )
        lines.append("")

    kpi_insights = kpi_analysis.get("kpi_insights", [])
    if kpi_insights:
        lines.append("## Insights")
        lines.append("")
        for insight in kpi_insights:
            lines.append(f"- {insight}")
        lines.append("")

    return "\n".join(lines)


# ===================================================================
# Tool 2: correlate_test_results — KPI correlation dimensions
# ===================================================================

def build_kpi_correlation_pairs(
    kpi_columns: List[str],
    available_columns: List[str],
) -> List[Dict[str, Any]]:
    """Dynamically build KPI correlation pairs using the CATEGORY_REGISTRY.

    For each KPI metric column present, looks up its category's
    ``correlate_with`` targets and includes any pair where both
    the KPI column and the target column exist in the merged DataFrame.

    Args:
        kpi_columns:       KPI metric column names present in the merged data.
        available_columns: All column names in the merged DataFrame (perf + infra + KPI).

    Returns:
        List of dicts, each with ``kpi_metric``, ``target``, ``category``,
        and ``interpretation``.
    """
    pairs: List[Dict[str, Any]] = []
    available_set = set(available_columns)

    for kpi_col in kpi_columns:
        category = categorize_metric(kpi_col)
        if category == "custom":
            continue
        reg = CATEGORY_REGISTRY.get(category, {})
        targets = reg.get("correlate_with", [])
        interpretation = reg.get("interpretation", "")

        for target in targets:
            if target in available_set:
                pairs.append({
                    "kpi_metric": kpi_col,
                    "target": target,
                    "category": category,
                    "interpretation": interpretation,
                })

    return pairs


def compute_kpi_correlations(
    merged_df: pd.DataFrame,
    kpi_pairs: List[Dict[str, Any]],
    significance_threshold: float = 0.3,
) -> Dict[str, Any]:
    """Compute Pearson correlations for KPI metric pairs.

    Args:
        merged_df:               Merged DataFrame with all columns available.
        kpi_pairs:               List from :func:`build_kpi_correlation_pairs`.
        significance_threshold:  Minimum |coefficient| to flag as significant.

    Returns:
        Dict with ``pairs`` (list of correlation results), ``kpi_metrics_available``,
        and ``kpi_insights``.
    """
    results: List[Dict[str, Any]] = []
    metrics_seen: set = set()
    insights: List[str] = []

    for pair in kpi_pairs:
        kpi_col = pair["kpi_metric"]
        target = pair["target"]
        category = pair["category"]
        interpretation = pair["interpretation"]
        metrics_seen.add(kpi_col)

        if kpi_col not in merged_df.columns or target not in merged_df.columns:
            continue

        valid = merged_df[[kpi_col, target]].dropna()
        if len(valid) < 5:
            continue

        coeff = float(valid[kpi_col].corr(valid[target]))
        if pd.isna(coeff):
            coeff = 0.0

        abs_coeff = abs(coeff)
        if abs_coeff >= 0.7:
            strength = "strong"
        elif abs_coeff >= significance_threshold:
            strength = "moderate"
        else:
            strength = "weak"

        direction = "positive" if coeff > 0 else "negative"

        result_entry = {
            "kpi_metric": kpi_col,
            "compared_with": target,
            "category": category,
            "coefficient": round(coeff, 4),
            "strength": strength,
            "direction": direction,
            "samples": len(valid),
            "interpretation": (
                f"{kpi_col} shows {strength} {direction} correlation "
                f"({coeff:.3f}) with {target} — {interpretation}"
            ),
        }
        results.append(result_entry)

        if abs_coeff >= significance_threshold:
            insights.append(
                f"KPI: {kpi_col} {strength}ly correlates with {target} "
                f"(r={coeff:.3f}, {direction})"
            )

    return {
        "pairs": results,
        "kpi_metrics_available": sorted(metrics_seen),
        "kpi_insights": insights,
    }


# ===================================================================
# Tool 3: identify_bottlenecks — KPI-driven bottleneck detection
# ===================================================================

# Minimum data-point thresholds to avoid false positives on thin data
_MIN_SAMPLES_TREND = 6
_MIN_SAMPLES_SPIKE = 3


def detect_kpi_bottlenecks(
    kpi_df: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict[str, Any],
    cfg: Dict[str, Any],
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
) -> List[Dict[str, Any]]:
    """Orchestrate all KPI-driven bottleneck detectors.

    Called from ``bottleneck_analyzer.analyze_bottlenecks`` after
    Phase 2b (capacity risks).  The KPI DataFrame is in pivoted form
    (one column per metric, with units already converted).

    Args:
        kpi_df:              Pivoted KPI DataFrame from ``load_kpi_pivoted()``.
        buckets_df:          JTL time-bucketed DataFrame (has ``bucket_start``,
                             ``p90``, ``concurrency``, ``throughput_rps``, etc.).
        baseline:            Baseline dict from ``_compute_baseline()``.
        cfg:                 Bottleneck analysis config dict.
        test_run_id:         Current test run ID.
        test_start_time:     First bucket timestamp (for elapsed calc).
        make_finding_fn:     Reference to ``_make_finding`` from bottleneck_analyzer.
        onset_fields_fn:     Reference to ``_onset_fields`` from bottleneck_analyzer.
        classify_severity_fn: Reference to ``_classify_severity_v2`` from bottleneck_analyzer.

    Returns:
        List of finding dicts, ready to extend the main findings list.
    """
    findings: List[Dict[str, Any]] = []

    kpi_aligned = _align_kpi_to_buckets(kpi_df, buckets_df, cfg)
    if kpi_aligned is None or kpi_aligned.empty:
        return findings

    # Application-level / k8s detectors
    findings.extend(_detect_gc_pressure(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
    ))
    findings.extend(_detect_gc_heap_growth(
        kpi_df, cfg, test_run_id, test_start_time,
        make_finding_fn, onset_fields_fn, classify_severity_fn,
    ))
    findings.extend(_detect_server_latency_spikes(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
    ))
    findings.extend(_detect_throughput_divergence(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
    ))

    # Host-level detectors
    scope_name = _resolve_scope_name(kpi_df)
    findings.extend(_detect_host_cpu_saturation(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
        scope_name,
    ))
    findings.extend(_detect_host_memory_pressure(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
        scope_name,
    ))
    findings.extend(_detect_disk_queue_saturation(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
        scope_name,
    ))
    findings.extend(_detect_sql_contention(
        kpi_aligned, buckets_df, baseline, cfg, test_run_id,
        test_start_time, make_finding_fn, onset_fields_fn, classify_severity_fn,
        scope_name,
    ))

    return findings


# -------------------------------------------------------------------
# KPI ↔ JTL time alignment
# -------------------------------------------------------------------

def _align_kpi_to_buckets(
    kpi_df: pd.DataFrame,
    buckets_df: pd.DataFrame,
    cfg: Dict[str, Any],
) -> Optional[pd.DataFrame]:
    """Resample KPI data into the same time buckets used by JTL analysis.

    Returns a DataFrame indexed by ``bucket_start`` with one column per
    KPI metric (aggregated by mean within each bucket).
    """
    if kpi_df is None or kpi_df.empty:
        return None

    bucket_seconds = cfg.get("bucket_seconds", 60)

    numeric_cols = [
        c for c in kpi_df.columns if c not in ("timestamp", "identifier")
    ]
    if not numeric_cols:
        return None

    kpi_work = kpi_df.copy()
    kpi_work["timestamp"] = pd.to_datetime(kpi_work["timestamp"], utc=True)
    kpi_indexed = kpi_work.set_index("timestamp")[numeric_cols]

    resampled = kpi_indexed.resample(f"{bucket_seconds}s").mean(numeric_only=True)
    resampled = resampled.dropna(how="all")

    return resampled


# -------------------------------------------------------------------
# Detector: GC pressure
# -------------------------------------------------------------------

def _detect_gc_pressure(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
) -> List[Dict]:
    """Detect GC memory load exceeding threshold concurrent with latency degradation."""
    gc_col = _find_column(kpi_aligned, "gc_memory")
    if gc_col is None:
        return []

    gc_threshold = cfg.get("gc_pressure_threshold", 85.0)
    baseline_p90 = baseline.get("p90", 0)
    if baseline_p90 <= 0:
        return []

    latency_factor = 1.5
    warmup = cfg.get("warmup_buckets", 2)
    findings: List[Dict] = []

    for ts, row in kpi_aligned.iterrows():
        gc_val = row.get(gc_col)
        if pd.isna(gc_val) or gc_val <= gc_threshold:
            continue

        concurrent_bucket = _get_concurrent_bucket(buckets_df, ts)
        if concurrent_bucket is None:
            continue

        bucket_p90 = concurrent_bucket.get("p90", 0)
        if pd.isna(bucket_p90) or bucket_p90 <= baseline_p90 * latency_factor:
            continue

        bucket_idx = concurrent_bucket.get("_bucket_idx", 0)
        delta_pct = (gc_val - gc_threshold) / gc_threshold * 100

        findings.append(make_finding_fn(
            bottleneck_type="gc_pressure",
            scope="service",
            scope_name="kpi_service",
            concurrency=float(concurrent_bucket.get("concurrency", 0)),
            metric_name=gc_col,
            metric_value=float(gc_val),
            baseline_value=gc_threshold,
            severity=classify_severity_fn(
                delta_pct=delta_pct,
                persistence_ratio=None,
                classification="bottleneck",
                scope="service",
                bottleneck_type="gc_pressure",
            ),
            confidence="high" if gc_val > 95 else "medium",
            classification="bottleneck",
            evidence=(
                f"GC memory load reached {gc_val:.1f}% (threshold {gc_threshold:.0f}%) "
                f"while P90 latency was {bucket_p90:.0f}ms "
                f"(baseline {baseline_p90:.0f}ms, {bucket_p90/baseline_p90:.1f}x). "
                f"GC pressure is likely contributing to latency degradation."
            ),
            test_run_id=test_run_id,
            **onset_fields_fn(
                concurrent_bucket.get("bucket_start", ts),
                warmup + bucket_idx,
                test_start_time,
            ),
        ))
        break  # report first occurrence

    return findings


# -------------------------------------------------------------------
# Detector: GC heap growth (potential memory leak)
# -------------------------------------------------------------------

def _detect_gc_heap_growth(
    kpi_df: pd.DataFrame,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
) -> List[Dict]:
    """Detect sustained Gen2 heap growth via linear regression."""
    gen2_col = _find_column(kpi_df, "gc_size_gen2")
    if gen2_col is None:
        return []

    kpi_work = kpi_df.copy()
    kpi_work["timestamp"] = pd.to_datetime(kpi_work["timestamp"], utc=True)

    gen2 = kpi_work[["timestamp", gen2_col]].dropna()
    if len(gen2) < _MIN_SAMPLES_TREND:
        return []

    x = (gen2["timestamp"] - gen2["timestamp"].min()).dt.total_seconds().values.astype(float)
    y = gen2[gen2_col].values.astype(float)

    try:
        slope, intercept = np.polyfit(x, y, 1)
    except (np.linalg.LinAlgError, ValueError):
        return []

    predicted_start = float(intercept)
    predicted_end = float(slope * x[-1] + intercept)

    if predicted_start <= 0:
        return []

    growth_pct = (predicted_end - predicted_start) / predicted_start * 100
    growth_threshold = cfg.get("gc_heap_growth_threshold_pct", 10.0)

    if growth_pct < growth_threshold:
        return []

    duration_minutes = x[-1] / 60 if x[-1] > 0 else 1
    growth_rate_per_min = slope * 60

    severity = classify_severity_fn(
        delta_pct=growth_pct,
        persistence_ratio=1.0,
        classification="bottleneck",
        scope="service",
        bottleneck_type="gc_heap_growth",
    )

    return [make_finding_fn(
        bottleneck_type="gc_heap_growth",
        scope="service",
        scope_name="kpi_service",
        concurrency=0,
        metric_name=gen2_col,
        metric_value=predicted_end,
        baseline_value=predicted_start,
        severity=severity,
        confidence="high" if growth_pct > 50 else "medium",
        classification="bottleneck",
        evidence=(
            f"GC Gen2 heap grew from {predicted_start:.1f}MB to {predicted_end:.1f}MB "
            f"({growth_pct:.1f}% increase over {duration_minutes:.0f} minutes, "
            f"rate: {growth_rate_per_min:.2f}MB/min). "
            f"Sustained monotonic growth may indicate a memory leak."
        ),
        test_run_id=test_run_id,
        onset_timestamp=str(gen2["timestamp"].iloc[0]),
        onset_bucket_index=0,
        test_elapsed_seconds=0.0,
    )]


# -------------------------------------------------------------------
# Detector: Server-side latency spikes
# -------------------------------------------------------------------

def _detect_server_latency_spikes(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
) -> List[Dict]:
    """Detect server-side P99/max latency spikes that correlate with client degradation."""
    p99_col = _find_column(kpi_aligned, "p99_latency")
    max_col = _find_column(kpi_aligned, "max_latency")

    check_col = p99_col or max_col
    if check_col is None:
        return []

    baseline_p90 = baseline.get("p90", 0)
    if baseline_p90 <= 0:
        return []

    spike_factor = cfg.get("kpi_latency_spike_factor", 3.0)
    warmup = cfg.get("warmup_buckets", 2)
    findings: List[Dict] = []

    series = kpi_aligned[check_col].dropna()
    if len(series) < _MIN_SAMPLES_SPIKE:
        return []

    kpi_baseline = float(series.iloc[:max(2, len(series) // 5)].median())
    if kpi_baseline <= 0:
        return []

    spike_threshold = kpi_baseline * spike_factor

    for ts, val in series.items():
        if val <= spike_threshold:
            continue

        concurrent_bucket = _get_concurrent_bucket(buckets_df, ts)
        if concurrent_bucket is None:
            continue

        bucket_p90 = concurrent_bucket.get("p90", 0)
        if pd.isna(bucket_p90) or bucket_p90 <= baseline_p90:
            continue

        bucket_idx = concurrent_bucket.get("_bucket_idx", 0)
        delta_pct = (val - kpi_baseline) / kpi_baseline * 100

        findings.append(make_finding_fn(
            bottleneck_type="server_latency_spike",
            scope="service",
            scope_name="kpi_service",
            concurrency=float(concurrent_bucket.get("concurrency", 0)),
            metric_name=check_col,
            metric_value=float(val),
            baseline_value=float(kpi_baseline),
            severity=classify_severity_fn(
                delta_pct=delta_pct,
                persistence_ratio=None,
                classification="bottleneck",
                scope="service",
                bottleneck_type="server_latency_spike",
            ),
            confidence="high",
            classification="bottleneck",
            evidence=(
                f"Server-side {check_col} spiked to {val:.1f}ms "
                f"(baseline {kpi_baseline:.1f}ms, {val/kpi_baseline:.1f}x) "
                f"while client P90 was {bucket_p90:.0f}ms at "
                f"{concurrent_bucket.get('concurrency', 0):.0f} concurrent users. "
                f"Server latency spike preceded/coincided with client-side degradation."
            ),
            test_run_id=test_run_id,
            **onset_fields_fn(
                concurrent_bucket.get("bucket_start", ts),
                warmup + bucket_idx,
                test_start_time,
            ),
        ))
        break  # report first spike

    return findings


# -------------------------------------------------------------------
# Detector: Throughput divergence
# -------------------------------------------------------------------

def _detect_throughput_divergence(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
) -> List[Dict]:
    """Detect divergence between server-side hits and client-side request count."""
    hits_col = _find_column(kpi_aligned, "request_hits") or _find_column(kpi_aligned, "hits")
    if hits_col is None:
        return []

    warmup = cfg.get("warmup_buckets", 2)
    divergence_threshold = cfg.get("kpi_throughput_divergence_pct", 30.0)
    findings: List[Dict] = []

    matched_count = 0
    divergent_count = 0
    worst_divergence_pct = 0.0
    worst_ts = None
    worst_bucket = None

    for ts, row in kpi_aligned.iterrows():
        server_hits = row.get(hits_col)
        if pd.isna(server_hits) or server_hits <= 0:
            continue

        concurrent_bucket = _get_concurrent_bucket(buckets_df, ts)
        if concurrent_bucket is None:
            continue

        client_rps = concurrent_bucket.get("throughput_rps", 0)
        if client_rps <= 0:
            continue

        matched_count += 1

        ratio_diff = abs(server_hits - client_rps) / client_rps * 100
        if ratio_diff > divergence_threshold:
            divergent_count += 1
            if ratio_diff > worst_divergence_pct:
                worst_divergence_pct = ratio_diff
                worst_ts = ts
                worst_bucket = concurrent_bucket

    if divergent_count == 0 or matched_count < _MIN_SAMPLES_SPIKE:
        return []

    divergence_ratio = divergent_count / matched_count
    if divergence_ratio < 0.3:
        return []

    bucket_idx = worst_bucket.get("_bucket_idx", 0) if worst_bucket else 0
    server_val = float(kpi_aligned.loc[worst_ts, hits_col]) if worst_ts is not None else 0
    client_val = float(worst_bucket.get("throughput_rps", 0)) if worst_bucket else 0

    findings.append(make_finding_fn(
        bottleneck_type="throughput_divergence",
        scope="service",
        scope_name="kpi_service",
        concurrency=float(worst_bucket.get("concurrency", 0)) if worst_bucket else 0,
        metric_name=f"{hits_col}_vs_client_rps",
        metric_value=server_val,
        baseline_value=client_val,
        severity="medium" if worst_divergence_pct < 50 else "high",
        confidence="medium" if divergence_ratio < 0.5 else "high",
        classification="bottleneck",
        evidence=(
            f"Server-side {hits_col} diverged from client throughput in "
            f"{divergent_count}/{matched_count} windows ({divergence_ratio:.0%}). "
            f"Worst divergence: server {server_val:.1f} vs client {client_val:.1f} RPS "
            f"({worst_divergence_pct:.1f}% difference). "
            f"Requests may be dropped or load-balanced unevenly."
        ),
        test_run_id=test_run_id,
        **onset_fields_fn(
            worst_bucket.get("bucket_start", worst_ts) if worst_bucket else worst_ts,
            warmup + bucket_idx,
            test_start_time,
        ),
    ))

    return findings


# -------------------------------------------------------------------
# Detector: Host CPU saturation
# -------------------------------------------------------------------

def _detect_host_cpu_saturation(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
    scope_name: str,
) -> List[Dict]:
    """Detect host CPU saturation (low idle %) concurrent with latency degradation."""
    idle_col = _find_column(kpi_aligned, "cpu_idle")
    if idle_col is None:
        return []

    idle_threshold = cfg.get("host_cpu_idle_threshold", 15.0)
    baseline_p90 = baseline.get("p90", 0)
    if baseline_p90 <= 0:
        return []

    warmup = cfg.get("warmup_buckets", 2)
    findings: List[Dict] = []

    for ts, row in kpi_aligned.iterrows():
        idle_val = row.get(idle_col)
        if pd.isna(idle_val) or idle_val > idle_threshold:
            continue

        concurrent_bucket = _get_concurrent_bucket(buckets_df, ts)
        if concurrent_bucket is None:
            continue

        bucket_p90 = concurrent_bucket.get("p90", 0)
        if pd.isna(bucket_p90) or bucket_p90 <= baseline_p90 * 1.3:
            continue

        bucket_idx = concurrent_bucket.get("_bucket_idx", 0)
        cpu_used = 100 - idle_val

        user_col = _find_column(kpi_aligned, "cpu_user")
        system_col = _find_column(kpi_aligned, "cpu_system")
        user_val = row.get(user_col, 0) if user_col else 0
        system_val = row.get(system_col, 0) if system_col else 0

        delta_pct = (cpu_used - (100 - idle_threshold)) / (100 - idle_threshold) * 100

        findings.append(make_finding_fn(
            bottleneck_type="host_cpu_saturation",
            scope="host",
            scope_name=scope_name,
            concurrency=float(concurrent_bucket.get("concurrency", 0)),
            metric_name=idle_col,
            metric_value=float(cpu_used),
            baseline_value=100 - idle_threshold,
            severity=classify_severity_fn(
                delta_pct=delta_pct,
                persistence_ratio=None,
                classification="bottleneck",
                scope="host",
                bottleneck_type="host_cpu_saturation",
            ),
            confidence="high" if idle_val < 10 else "medium",
            classification="bottleneck",
            evidence=(
                f"Host CPU idle dropped to {idle_val:.1f}% "
                f"(user: {user_val:.1f}%, system: {system_val:.1f}%) "
                f"while client P90 was {bucket_p90:.0f}ms at "
                f"{concurrent_bucket.get('concurrency', 0):.0f} concurrent users. "
                f"Host CPU saturation likely contributing to latency degradation."
            ),
            test_run_id=test_run_id,
            **onset_fields_fn(
                concurrent_bucket.get("bucket_start", ts),
                warmup + bucket_idx,
                test_start_time,
            ),
        ))
        break

    return findings


# -------------------------------------------------------------------
# Detector: Host memory pressure
# -------------------------------------------------------------------

def _detect_host_memory_pressure(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
    scope_name: str,
) -> List[Dict]:
    """Detect high host memory utilization concurrent with latency degradation."""
    usable_col = _find_column(kpi_aligned, "mem_usable")
    total_col = _find_column(kpi_aligned, "mem_total")
    if usable_col is None or total_col is None:
        return []

    mem_pressure_threshold = cfg.get("host_mem_pressure_threshold_pct", 85.0)
    baseline_p90 = baseline.get("p90", 0)
    if baseline_p90 <= 0:
        return []

    warmup = cfg.get("warmup_buckets", 2)
    findings: List[Dict] = []

    for ts, row in kpi_aligned.iterrows():
        usable = row.get(usable_col)
        total = row.get(total_col)
        if pd.isna(usable) or pd.isna(total) or total <= 0:
            continue

        used_pct = (1.0 - usable / total) * 100
        if used_pct < mem_pressure_threshold:
            continue

        concurrent_bucket = _get_concurrent_bucket(buckets_df, ts)
        if concurrent_bucket is None:
            continue

        bucket_p90 = concurrent_bucket.get("p90", 0)
        if pd.isna(bucket_p90) or bucket_p90 <= baseline_p90 * 1.3:
            continue

        bucket_idx = concurrent_bucket.get("_bucket_idx", 0)
        delta_pct = (used_pct - mem_pressure_threshold) / mem_pressure_threshold * 100

        findings.append(make_finding_fn(
            bottleneck_type="host_memory_pressure",
            scope="host",
            scope_name=scope_name,
            concurrency=float(concurrent_bucket.get("concurrency", 0)),
            metric_name=f"{usable_col}_vs_{total_col}",
            metric_value=float(used_pct),
            baseline_value=mem_pressure_threshold,
            severity=classify_severity_fn(
                delta_pct=delta_pct,
                persistence_ratio=None,
                classification="bottleneck",
                scope="host",
                bottleneck_type="host_memory_pressure",
            ),
            confidence="high" if used_pct > 95 else "medium",
            classification="bottleneck",
            evidence=(
                f"Host memory utilization at {used_pct:.1f}% "
                f"(usable: {usable:.1f}, total: {total:.1f}) "
                f"while client P90 was {bucket_p90:.0f}ms at "
                f"{concurrent_bucket.get('concurrency', 0):.0f} concurrent users. "
                f"High memory pressure may force paging and degrade performance."
            ),
            test_run_id=test_run_id,
            **onset_fields_fn(
                concurrent_bucket.get("bucket_start", ts),
                warmup + bucket_idx,
                test_start_time,
            ),
        ))
        break

    return findings


# -------------------------------------------------------------------
# Detector: Disk queue saturation
# -------------------------------------------------------------------

def _detect_disk_queue_saturation(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
    scope_name: str,
) -> List[Dict]:
    """Detect elevated disk queue length concurrent with latency degradation."""
    queue_col = _find_column(kpi_aligned, "disk_queue")
    if queue_col is None:
        return []

    queue_threshold = cfg.get("host_disk_queue_threshold", 2.0)
    baseline_p90 = baseline.get("p90", 0)
    if baseline_p90 <= 0:
        return []

    warmup = cfg.get("warmup_buckets", 2)
    findings: List[Dict] = []

    sustained_count = 0
    total_count = 0
    worst_val = 0.0
    worst_ts = None
    worst_bucket = None

    for ts, row in kpi_aligned.iterrows():
        queue_val = row.get(queue_col)
        if pd.isna(queue_val):
            continue

        total_count += 1
        if queue_val > queue_threshold:
            concurrent_bucket = _get_concurrent_bucket(buckets_df, ts)
            if concurrent_bucket is not None:
                sustained_count += 1
                if queue_val > worst_val:
                    worst_val = queue_val
                    worst_ts = ts
                    worst_bucket = concurrent_bucket

    if sustained_count < _MIN_SAMPLES_SPIKE or worst_bucket is None:
        return []

    persistence = sustained_count / total_count if total_count > 0 else 0
    if persistence < 0.1:
        return []

    bucket_idx = worst_bucket.get("_bucket_idx", 0)
    delta_pct = (worst_val - queue_threshold) / queue_threshold * 100

    findings.append(make_finding_fn(
        bottleneck_type="disk_queue_saturation",
        scope="host",
        scope_name=scope_name,
        concurrency=float(worst_bucket.get("concurrency", 0)),
        metric_name=queue_col,
        metric_value=float(worst_val),
        baseline_value=queue_threshold,
        severity=classify_severity_fn(
            delta_pct=delta_pct,
            persistence_ratio=persistence,
            classification="bottleneck",
            scope="host",
            bottleneck_type="disk_queue_saturation",
        ),
        confidence="high" if persistence > 0.3 else "medium",
        classification="bottleneck",
        evidence=(
            f"Disk queue length exceeded {queue_threshold} in "
            f"{sustained_count}/{total_count} windows ({persistence:.0%}). "
            f"Peak queue depth: {worst_val:.2f}. "
            f"Sustained disk I/O queuing can cause latency spikes."
        ),
        test_run_id=test_run_id,
        **onset_fields_fn(
            worst_bucket.get("bucket_start", worst_ts),
            warmup + bucket_idx,
            test_start_time,
        ),
    ))

    return findings


# -------------------------------------------------------------------
# Detector: SQL Server contention
# -------------------------------------------------------------------

def _detect_sql_contention(
    kpi_aligned: pd.DataFrame,
    buckets_df: pd.DataFrame,
    baseline: Dict,
    cfg: Dict,
    test_run_id: str,
    test_start_time,
    make_finding_fn,
    onset_fields_fn,
    classify_severity_fn,
    scope_name: str,
) -> List[Dict]:
    """Detect SQL Server lock waits or compilation spikes concurrent with latency."""
    lock_col = _find_column(kpi_aligned, "sql_lock_waits")
    compile_col = _find_column(kpi_aligned, "sql_compilations")
    if lock_col is None and compile_col is None:
        return []

    baseline_p90 = baseline.get("p90", 0)
    if baseline_p90 <= 0:
        return []

    warmup = cfg.get("warmup_buckets", 2)
    compile_spike_factor = cfg.get("sql_compilation_spike_factor", 3.0)
    findings: List[Dict] = []

    if lock_col is not None:
        lock_series = kpi_aligned[lock_col].dropna()
        if len(lock_series) >= _MIN_SAMPLES_SPIKE:
            lock_events = lock_series[lock_series > 0]
            if len(lock_events) >= _MIN_SAMPLES_SPIKE:
                worst_idx = lock_events.idxmax()
                worst_val = float(lock_events.max())
                concurrent_bucket = _get_concurrent_bucket(buckets_df, worst_idx)
                if concurrent_bucket is not None:
                    bucket_idx = concurrent_bucket.get("_bucket_idx", 0)
                    persistence = len(lock_events) / len(lock_series)
                    findings.append(make_finding_fn(
                        bottleneck_type="sql_contention",
                        scope="host",
                        scope_name=scope_name,
                        concurrency=float(concurrent_bucket.get("concurrency", 0)),
                        metric_name=lock_col,
                        metric_value=worst_val,
                        baseline_value=0.0,
                        severity="high" if persistence > 0.3 else "medium",
                        confidence="high" if persistence > 0.5 else "medium",
                        classification="bottleneck",
                        evidence=(
                            f"SQL Server lock waits detected in "
                            f"{len(lock_events)}/{len(lock_series)} windows "
                            f"({persistence:.0%}). Peak: {worst_val:.2f}/s. "
                            f"Lock contention can serialize database access "
                            f"and cause application-level latency spikes."
                        ),
                        test_run_id=test_run_id,
                        **onset_fields_fn(
                            concurrent_bucket.get("bucket_start", worst_idx),
                            warmup + bucket_idx,
                            test_start_time,
                        ),
                    ))

    if compile_col is not None and len(findings) == 0:
        compile_series = kpi_aligned[compile_col].dropna()
        if len(compile_series) >= _MIN_SAMPLES_TREND:
            early_baseline = compile_series.iloc[:max(3, warmup)].mean()
            if early_baseline > 0:
                spikes = compile_series[compile_series > early_baseline * compile_spike_factor]
                if len(spikes) >= _MIN_SAMPLES_SPIKE:
                    worst_idx = spikes.idxmax()
                    worst_val = float(spikes.max())
                    concurrent_bucket = _get_concurrent_bucket(buckets_df, worst_idx)
                    if concurrent_bucket is not None:
                        bucket_idx = concurrent_bucket.get("_bucket_idx", 0)
                        delta_pct = (worst_val - early_baseline) / early_baseline * 100
                        findings.append(make_finding_fn(
                            bottleneck_type="sql_contention",
                            scope="host",
                            scope_name=scope_name,
                            concurrency=float(concurrent_bucket.get("concurrency", 0)),
                            metric_name=compile_col,
                            metric_value=worst_val,
                            baseline_value=float(early_baseline),
                            severity=classify_severity_fn(
                                delta_pct=delta_pct,
                                persistence_ratio=len(spikes) / len(compile_series),
                                classification="bottleneck",
                                scope="host",
                                bottleneck_type="sql_contention",
                            ),
                            confidence="medium",
                            classification="bottleneck",
                            evidence=(
                                f"SQL compilations spiked to {worst_val:.1f}/s "
                                f"(early baseline: {early_baseline:.1f}/s, "
                                f"{worst_val/early_baseline:.1f}x). "
                                f"Excessive recompilation can indicate "
                                f"plan cache pressure or ad-hoc query storms."
                            ),
                            test_run_id=test_run_id,
                            **onset_fields_fn(
                                concurrent_bucket.get("bucket_start", worst_idx),
                                warmup + bucket_idx,
                                test_start_time,
                            ),
                        ))

    return findings


# -------------------------------------------------------------------
# Scope resolution for bottleneck findings
# -------------------------------------------------------------------

def _resolve_scope_name(kpi_df: pd.DataFrame) -> str:
    """Extract a meaningful scope name from the KPI DataFrame.

    For host-scoped data, returns the hostname.  For k8s-scoped data,
    returns the service/filter name.  Falls back to ``"kpi_entity"``.
    """
    if "identifier" in kpi_df.columns:
        identifiers = kpi_df["identifier"].dropna().unique()
        if len(identifiers) == 1:
            return str(identifiers[0])
        if len(identifiers) > 1:
            return str(identifiers[0])
    return "kpi_entity"


# -------------------------------------------------------------------
# Shared helpers for bottleneck detectors
# -------------------------------------------------------------------

def _find_column(df: pd.DataFrame, pattern: str) -> Optional[str]:
    """Find the first DataFrame column whose name contains ``pattern``.

    Returns the column name, or ``None`` if no match.
    """
    for col in df.columns:
        if pattern in col:
            return col
    return None


def _get_concurrent_bucket(
    buckets_df: pd.DataFrame, kpi_timestamp
) -> Optional[Dict[str, Any]]:
    """Find the JTL time bucket that overlaps with a KPI timestamp.

    Returns a dict of the bucket row's values (with ``_bucket_idx`` added),
    or ``None`` if no match within tolerance.
    """
    kpi_ts = pd.Timestamp(kpi_timestamp)
    if kpi_ts.tzinfo is None:
        kpi_ts = kpi_ts.tz_localize("UTC")

    bucket_starts = pd.to_datetime(buckets_df["bucket_start"], utc=True)
    diffs = (bucket_starts - kpi_ts).abs()
    min_idx = diffs.idxmin()

    if diffs.loc[min_idx].total_seconds() > 120:
        return None

    row = buckets_df.loc[min_idx].to_dict()
    row["_bucket_idx"] = int(min_idx) if isinstance(min_idx, (int, np.integer)) else 0
    return row
