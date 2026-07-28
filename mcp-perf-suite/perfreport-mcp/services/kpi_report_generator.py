"""
services/kpi_report_generator.py
KPI-specific report section builders for performance reports.

Extends report_generator.py with builders for:
- Section 4.4: Application KPI Analysis (metrics tables, insights, trend indicators)
- Section 5.2: KPI Correlations (correlation pairs table, strength indicators)

Data sources:
- kpi_analysis.json (from PerfAnalysis analyze_apm_metrics)
- kpi_correlations section within correlation_analysis.json
- kpi_summary.md (optional fallback for raw markdown)
"""
from typing import Dict, List, Optional

from utils.report_utils import strip_service_name_decorations, strip_report_headers_footers


# Category display order and labels for KPI metrics tables
_CATEGORY_ORDER = [
    "latency",
    "throughput",
    "gc_heap",
    "gc_pressure",
    "host_cpu",
    "host_memory",
    "sql_server",
    "iis",
]

_CATEGORY_LABELS = {
    "latency": "Latency",
    "throughput": "Throughput",
    "gc_heap": "GC Heap",
    "gc_pressure": "GC Pressure",
    "host_cpu": "Host CPU",
    "host_memory": "Host Memory",
    "sql_server": "SQL Server",
    "iis": "IIS",
}

# Trend indicator symbols
_TREND_ICONS = {
    "increasing": "📈",
    "decreasing": "📉",
    "stable": "➡️",
}

# Correlation strength labels and sort priority (strongest first)
_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2}

_STRENGTH_LABELS = {
    "strong": "🔴 Strong",
    "moderate": "🟡 Moderate",
    "weak": "🟢 Weak",
}


def build_kpi_analysis_section(
    kpi_data: Optional[Dict],
    kpi_summary_md: Optional[str] = None,
) -> str:
    """
    Build the Application KPI Analysis section (template placeholder {{KPI_ANALYSIS_SECTION}}).

    Generates per-service/host metric summary tables grouped by category,
    trend indicators, and pre-generated insights. Falls back to kpi_summary.md
    when structured JSON is unavailable.

    Args:
        kpi_data: Parsed kpi_analysis.json content
        kpi_summary_md: Raw kpi_summary.md content (optional fallback)

    Returns:
        Formatted markdown string for the KPI analysis section
    """
    if not kpi_data:
        if kpi_summary_md:
            return strip_report_headers_footers(kpi_summary_md)
        return "No KPI analysis data available."

    # PerfAnalysis currently uses "services" for both k8s and host scopes.
    # Fall back to "hosts" for future-proofing if the schema ever splits them.
    services = kpi_data.get("services") or kpi_data.get("hosts") or {}
    if not services:
        return "No KPI metrics found in the analysis data."

    scope = kpi_data.get("scope", "k8s")
    identifier_type = kpi_data.get("identifier_type", "service")
    entity_label = "Host" if identifier_type == "host" else "Service"
    categories_found = kpi_data.get("metric_categories_found", [])
    total_entities = kpi_data.get("total_services", len(services))
    insights = kpi_data.get("kpi_insights", [])

    lines: List[str] = []

    # Section overview
    scope_display = {"k8s": "Kubernetes", "host": "Host", "mixed": "Mixed"}.get(scope, scope)
    lines.append(
        f"**Scope:** {scope_display} · "
        f"**{entity_label}s Analyzed:** {total_entities} · "
        f"**Metric Categories:** {', '.join(categories_found)}"
    )
    lines.append("")

    # Per-entity metrics tables
    sorted_entities = sorted(services.items(), key=lambda x: x[0])
    for entity_name, metrics in sorted_entities:
        display_name = strip_service_name_decorations(entity_name)
        lines.append(f"#### {entity_label}: {display_name}")
        lines.append("")
        lines.append(_build_kpi_metrics_table(metrics, categories_found))
        lines.append("")

    # Insights
    if insights:
        lines.append("#### KPI Insights")
        lines.append("")
        for insight in insights:
            lines.append(f"- {insight}")
        lines.append("")

    return "\n".join(lines)


def build_kpi_correlation_section(
    kpi_correlations: Optional[Dict],
) -> str:
    """
    Build the KPI Correlations section (template placeholder {{KPI_CORRELATION_SECTION}}).

    Generates a correlation pairs table sorted by strength (strongest first),
    available KPI metrics list, and pre-generated correlation insights.

    Args:
        kpi_correlations: The kpi_correlations dict extracted from correlation_analysis.json

    Returns:
        Formatted markdown string for the KPI correlation section
    """
    if not kpi_correlations:
        return "No KPI correlation data available."

    pairs = kpi_correlations.get("pairs", [])
    available_metrics = kpi_correlations.get("kpi_metrics_available", [])
    insights = kpi_correlations.get("kpi_insights", [])

    if not pairs:
        return "No KPI correlation pairs found."

    lines: List[str] = []

    # Summary
    strong_count = sum(1 for p in pairs if p.get("strength") == "strong")
    moderate_count = sum(1 for p in pairs if p.get("strength") == "moderate")
    lines.append(
        f"**Correlation Pairs Analyzed:** {len(pairs)} · "
        f"**Strong:** {strong_count} · "
        f"**Moderate:** {moderate_count} · "
        f"**KPI Metrics Available:** {len(available_metrics)}"
    )
    lines.append("")

    lines.append(
        "**Understanding correlation strength:** "
        "Correlation measures how closely two metrics move together during the test. "
        "The coefficient ranges from −1.0 to +1.0."
    )
    lines.append("")
    lines.append(
        "- 🔴 **Strong** — A clear and reliable relationship. "
        "Changes in one metric consistently track changes in the other."
    )
    lines.append(
        "- 🟡 **Moderate** — A noticeable relationship exists, "
        "but other factors also influence the metrics."
    )
    lines.append(
        "- 🟢 **Weak** — No meaningful relationship detected. "
        "The two metrics appear to move independently."
    )
    lines.append("")
    lines.append(
        "A *positive* direction means both metrics increase together. "
        "A *negative* direction means one increases while the other decreases."
    )
    lines.append("")

    # Notable correlations table (moderate and strong only, sorted strongest first)
    notable = [p for p in pairs if p.get("strength") in ("strong", "moderate")]
    if notable:
        notable_sorted = sorted(
            notable,
            key=lambda p: (_STRENGTH_ORDER.get(p.get("strength", "weak"), 9), -abs(p.get("coefficient", 0))),
        )
        lines.append("**Notable Correlations:**")
        lines.append("")
        lines.append(
            "| KPI Metric | Compared With | Category | Coefficient | Strength | Direction | Samples |"
        )
        lines.append(
            "|------------|---------------|----------|-------------|----------|-----------|---------|"
        )
        for pair in notable_sorted:
            strength_label = _STRENGTH_LABELS.get(pair.get("strength", ""), pair.get("strength", ""))
            lines.append(
                f"| {pair.get('kpi_metric', '')} "
                f"| {pair.get('compared_with', '')} "
                f"| {pair.get('category', '')} "
                f"| {pair.get('coefficient', 0):.4f} "
                f"| {strength_label} "
                f"| {pair.get('direction', '')} "
                f"| {pair.get('samples', '')} |"
            )
        lines.append("")

    # Insights
    if insights:
        lines.append("**KPI Correlation Insights:**")
        lines.append("")
        for insight in insights:
            lines.append(f"- {insight}")
        lines.append("")

    # All correlations (full table)
    all_sorted = sorted(
        pairs,
        key=lambda p: (_STRENGTH_ORDER.get(p.get("strength", "weak"), 9), -abs(p.get("coefficient", 0))),
    )
    lines.append("**All KPI Correlation Pairs:**")
    lines.append("")
    lines.append(
        "| KPI Metric | Compared With | Category | Coefficient | Strength | Direction | Samples |"
    )
    lines.append(
        "|------------|---------------|----------|-------------|----------|-----------|---------|"
    )
    for pair in all_sorted:
        strength_label = _STRENGTH_LABELS.get(pair.get("strength", ""), pair.get("strength", ""))
        lines.append(
            f"| {pair.get('kpi_metric', '')} "
            f"| {pair.get('compared_with', '')} "
            f"| {pair.get('category', '')} "
            f"| {pair.get('coefficient', 0):.4f} "
            f"| {strength_label} "
            f"| {pair.get('direction', '')} "
            f"| {pair.get('samples', '')} |"
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_kpi_metrics_table(metrics: Dict, categories_found: List[str]) -> str:
    """
    Build a Markdown table of KPI metrics for a single entity, grouped by category.

    Metrics are ordered by category (following _CATEGORY_ORDER), then alphabetically
    within each category. Values use the display_unit from the analysis data.

    Args:
        metrics: Dict of metric_name -> metric stats
        categories_found: List of category strings present in the analysis

    Returns:
        Markdown table string
    """
    if not metrics:
        return "No metrics available."

    # Group metrics by category
    by_category: Dict[str, List[tuple]] = {}
    for metric_name, stats in metrics.items():
        cat = stats.get("category", "other")
        by_category.setdefault(cat, []).append((metric_name, stats))

    # Sort categories in canonical order, unknowns at end
    ordered_cats = [c for c in _CATEGORY_ORDER if c in by_category]
    remaining = sorted(set(by_category.keys()) - set(_CATEGORY_ORDER))
    ordered_cats.extend(remaining)

    lines = [
        "| Metric | Category | Avg | P90 | P95 | Min | Max | Trend | Samples |",
        "|--------|----------|-----|-----|-----|-----|-----|-------|---------|",
    ]

    for cat in ordered_cats:
        cat_metrics = sorted(by_category[cat], key=lambda x: x[0])
        for metric_name, stats in cat_metrics:
            display_unit = stats.get("display_unit", "")
            trend = stats.get("trend", "")
            trend_icon = _TREND_ICONS.get(trend, "")
            cat_label = _CATEGORY_LABELS.get(cat, cat)

            lines.append(
                f"| {metric_name} "
                f"| {cat_label} "
                f"| {_format_value(stats.get('avg'), display_unit)} "
                f"| {_format_value(stats.get('p90'), display_unit)} "
                f"| {_format_value(stats.get('p95'), display_unit)} "
                f"| {_format_value(stats.get('min'), display_unit)} "
                f"| {_format_value(stats.get('max'), display_unit)} "
                f"| {trend_icon} {trend} "
                f"| {stats.get('samples', 'N/A')} |"
            )

    return "\n".join(lines)


def _format_value(value, display_unit: str) -> str:
    """
    Format a numeric value with its display unit.

    Uses 4 decimal places for small values (< 1), 2 decimal places for medium
    values (< 1000), and 1 decimal place for large values (>= 1000).

    Args:
        value: Numeric value or None
        display_unit: Unit string (e.g. "ms", "MB", "percent")

    Returns:
        Formatted string like "0.2180 ms" or "654.57 MB"
    """
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)

    if abs(v) < 1:
        formatted = f"{v:.4f}"
    elif abs(v) < 1000:
        formatted = f"{v:.2f}"
    else:
        formatted = f"{v:.1f}"

    if display_unit:
        return f"{formatted} {display_unit}"
    return formatted
