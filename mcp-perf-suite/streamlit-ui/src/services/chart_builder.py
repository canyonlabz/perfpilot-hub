"""
Chart Builder Service - Prepares data and creates Altair charts.

Transforms raw artifact data (JTL CSVs, analysis JSON, Datadog CSVs)
into the format expected by the chart component factories.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

from src.ui.components.charts import (
    create_area_time_series,
    create_dual_axis_time_series,
    create_single_axis_time_series,
    create_multi_line_time_series,
    create_horizontal_bar,
    create_donut_chart,
    create_severity_bar,
)


# ---------------------------------------------------------------------------
# JTL helpers
# ---------------------------------------------------------------------------

def _calculate_bucket_seconds(jtl_df: pd.DataFrame) -> int:
    """Dynamically choose bucket size based on test duration."""
    if jtl_df is None or jtl_df.empty or "timeStamp" not in jtl_df.columns:
        return 30

    duration_ms = jtl_df["timeStamp"].max() - jtl_df["timeStamp"].min()
    duration_min = duration_ms / 60000

    if duration_min <= 5:
        return 5
    elif duration_min <= 15:
        return 10
    elif duration_min <= 30:
        return 30
    elif duration_min <= 60:
        return 60
    else:
        return 120


# ---------------------------------------------------------------------------
# Performance Tab Charts
# ---------------------------------------------------------------------------

def build_response_time_chart(jtl_df: pd.DataFrame):
    """
    Dual-axis chart: P90 Response Time vs Virtual Users over time.
    """
    if jtl_df is None or jtl_df.empty:
        return None
    required_cols = {"timeStamp", "elapsed", "allThreads"}
    if not required_cols.issubset(jtl_df.columns):
        return None

    bucket_sec = _calculate_bucket_seconds(jtl_df)
    df = jtl_df.copy()
    df["timestamp"] = pd.to_datetime(df["timeStamp"], unit="ms")
    df["bucket"] = df["timestamp"].dt.floor(f"{bucket_sec}s")

    agg = df.groupby("bucket").agg(
        p90_response_time=("elapsed", lambda x: np.percentile(x, 90)),
        max_vusers=("allThreads", "max"),
    ).reset_index()

    return create_dual_axis_time_series(
        df=agg, x_col="bucket",
        y1_col="p90_response_time", y2_col="max_vusers",
        y1_title="P90 Response Time (ms)", y2_title="Virtual Users",
        title="Response Time (P90) vs Virtual Users",
    )


def build_throughput_chart(jtl_df: pd.DataFrame):
    """Single-axis chart: Throughput (req/s) over time."""
    if jtl_df is None or jtl_df.empty or "timeStamp" not in jtl_df.columns:
        return None

    bucket_sec = _calculate_bucket_seconds(jtl_df)
    df = jtl_df.copy()
    df["timestamp"] = pd.to_datetime(df["timeStamp"], unit="ms")
    df["bucket"] = df["timestamp"].dt.floor(f"{bucket_sec}s")

    agg = df.groupby("bucket").agg(request_count=("timeStamp", "count")).reset_index()
    agg["throughput_rps"] = agg["request_count"] / bucket_sec

    return create_single_axis_time_series(
        df=agg, x_col="bucket", y_col="throughput_rps",
        y_title="Requests/sec", color="#2ecc40",
        title="Throughput Over Time",
    )


def build_error_rate_chart(jtl_df: pd.DataFrame):
    """Single-axis chart: Error rate (%) over time."""
    if jtl_df is None or jtl_df.empty:
        return None
    required_cols = {"timeStamp", "success"}
    if not required_cols.issubset(jtl_df.columns):
        return None

    bucket_sec = _calculate_bucket_seconds(jtl_df)
    df = jtl_df.copy()
    df["timestamp"] = pd.to_datetime(df["timeStamp"], unit="ms")
    df["bucket"] = df["timestamp"].dt.floor(f"{bucket_sec}s")

    # Handle 'success' column (could be boolean or string)
    if df["success"].dtype == object:
        df["is_error"] = df["success"].str.lower() != "true"
    else:
        df["is_error"] = ~df["success"].astype(bool)

    agg = df.groupby("bucket").agg(
        total=("timeStamp", "count"),
        errors=("is_error", "sum"),
    ).reset_index()
    agg["error_rate_pct"] = (agg["errors"] / agg["total"]) * 100

    return create_single_axis_time_series(
        df=agg, x_col="bucket", y_col="error_rate_pct",
        y_title="Error Rate (%)", color="#ff4136",
        title="Error Rate Over Time",
    )


def build_top_slowest_apis_chart(perf_analysis: dict, top_n: int = 15):
    """Horizontal bar chart of the slowest APIs by P90."""
    api_analysis = perf_analysis.get("api_analysis", {})
    if not api_analysis:
        return None

    rows = [
        {"api": name, "p90_response_time": stats.get("p90_response_time", 0)}
        for name, stats in api_analysis.items()
    ]
    df = pd.DataFrame(rows).nlargest(top_n, "p90_response_time")

    return create_horizontal_bar(
        df=df, x_col="p90_response_time", y_col="api",
        x_title="P90 Response Time (ms)", color="#ff851b",
        title=f"Top {min(top_n, len(df))} Slowest APIs (P90)",
    )


def build_pass_fail_donut(perf_analysis: dict):
    """Donut chart from SLA compliance data."""
    sla = perf_analysis.get("sla_analysis", {})
    if not sla:
        # Derive from api_analysis if sla_analysis not present
        api_analysis = perf_analysis.get("api_analysis", {})
        if api_analysis:
            compliant = sum(1 for s in api_analysis.values() if s.get("sla_compliant", True))
            violating = len(api_analysis) - compliant
        else:
            return None
    else:
        compliant = sla.get("compliant_apis", 0)
        violating = sla.get("violating_apis", 0)

    if compliant + violating == 0:
        return None

    df = pd.DataFrame({"Result": ["Pass", "Fail"], "Count": [compliant, violating]})
    return create_donut_chart(
        df=df, theta_col="Count", color_col="Result",
        color_scale=["#2ecc40", "#ff4136"], title="SLA Compliance",
    )


# ---------------------------------------------------------------------------
# Infrastructure Tab Charts
# ---------------------------------------------------------------------------

def _detect_environment_type(df: pd.DataFrame) -> str:
    """Detect whether the CSV data is K8s-based or Host-based."""
    if "metric" not in df.columns:
        return "unknown"
    metrics = df["metric"].unique()
    if any("kubernetes." in m for m in metrics):
        return "k8s"
    if any(m in ("cpu_util_pct", "mem_util_pct") for m in metrics):
        return "host"
    return "unknown"


def _get_service_label(df: pd.DataFrame) -> str:
    """Extract a service/host label from the DataFrame."""
    if "container_or_pod" in df.columns:
        vals = df["container_or_pod"].dropna().unique()
        if len(vals) > 0 and str(vals[0]) != "N/A":
            return str(vals[0])
    if "filter" in df.columns:
        vals = df["filter"].dropna().unique()
        if len(vals) > 0:
            return str(vals[0]).rstrip("*")
    if "hostname" in df.columns:
        vals = df["hostname"].dropna().unique()
        if len(vals) > 0 and str(vals[0]) != "N/A":
            return str(vals[0])
    return "service"


def build_infra_cpu_chart(datadog_dir: Path, cpu_unit: str = "millicores") -> Optional[dict]:
    """
    Area chart: CPU usage over time with optional limit line.

    Args:
        datadog_dir: Path to the datadog/ artifact directory.
        cpu_unit: "millicores" or "cores" (only applies to K8s data).

    Returns:
        dict with keys: chart, is_k8s, service_label
        or None if no data.
    """
    csv_files = _find_metric_csvs(datadog_dir)
    if not csv_files:
        return None

    for f in csv_files:
        df = pd.read_csv(f)
        if "metric" not in df.columns:
            continue

        env_type = _detect_environment_type(df)
        service_label = _get_service_label(df)

        if env_type == "k8s":
            usage_df = df[df["metric"] == "kubernetes.cpu.usage.total"].copy()
            if usage_df.empty:
                continue
            usage_df["timestamp"] = pd.to_datetime(usage_df["timestamp_utc"])

            if cpu_unit == "cores":
                usage_df["display_value"] = usage_df["value"] / 1e9
                y_title = "CPU Usage (Cores)"
            else:
                usage_df["display_value"] = usage_df["value"] / 1e6
                y_title = "CPU Usage (Millicores)"

            # Check for limits
            limit_value = None
            limits_df = df[df["metric"] == "kubernetes.cpu.limits"]
            if not limits_df.empty:
                raw_limit = limits_df["value"].mean()
                if raw_limit > 0:
                    if cpu_unit == "cores":
                        limit_value = raw_limit
                    else:
                        limit_value = raw_limit * 1000

            chart = create_area_time_series(
                df=usage_df, x_col="timestamp", y_col="display_value",
                y_title=y_title, color="#5276A7",
                title=f"CPU Usage - {service_label}",
                limit_value=limit_value,
                limit_label=f"CPU Limit ({cpu_unit.title()})",
            )
            return {"chart": chart, "is_k8s": True, "service_label": service_label}

        elif env_type == "host":
            usage_df = df[df["metric"] == "cpu_util_pct"].copy()
            if usage_df.empty:
                continue
            usage_df["timestamp"] = pd.to_datetime(usage_df["timestamp_utc"])
            usage_df["display_value"] = usage_df["value"]

            chart = create_area_time_series(
                df=usage_df, x_col="timestamp", y_col="display_value",
                y_title="CPU Utilization (%)", color="#5276A7",
                title=f"CPU Utilization - {service_label}",
            )
            return {"chart": chart, "is_k8s": False, "service_label": service_label}

    return None


def build_infra_memory_chart(datadog_dir: Path, mem_unit: str = "mb") -> Optional[dict]:
    """
    Area chart: Memory usage over time with optional limit line.

    Args:
        datadog_dir: Path to the datadog/ artifact directory.
        mem_unit: "mb" or "gb" (only applies to K8s data).

    Returns:
        dict with keys: chart, is_k8s, service_label
        or None if no data.
    """
    csv_files = _find_metric_csvs(datadog_dir)
    if not csv_files:
        return None

    for f in csv_files:
        df = pd.read_csv(f)
        if "metric" not in df.columns:
            continue

        env_type = _detect_environment_type(df)
        service_label = _get_service_label(df)

        if env_type == "k8s":
            usage_df = df[df["metric"] == "kubernetes.memory.usage"].copy()
            if usage_df.empty:
                continue
            usage_df["timestamp"] = pd.to_datetime(usage_df["timestamp_utc"])

            if mem_unit == "gb":
                usage_df["display_value"] = usage_df["value"] / 1e9
                y_title = "Memory Usage (GB)"
            else:
                usage_df["display_value"] = usage_df["value"] / 1e6
                y_title = "Memory Usage (MB)"

            # Check for limits
            limit_value = None
            limits_df = df[df["metric"] == "kubernetes.memory.limits"]
            if not limits_df.empty:
                raw_limit = limits_df["value"].mean()
                if raw_limit > 0:
                    if mem_unit == "gb":
                        limit_value = raw_limit / 1e9
                    else:
                        limit_value = raw_limit / 1e6

            chart = create_area_time_series(
                df=usage_df, x_col="timestamp", y_col="display_value",
                y_title=y_title, color="#F18727",
                title=f"Memory Usage - {service_label}",
                limit_value=limit_value,
                limit_label=f"Memory Limit ({mem_unit.upper()})",
            )
            return {"chart": chart, "is_k8s": True, "service_label": service_label}

        elif env_type == "host":
            usage_df = df[df["metric"] == "mem_util_pct"].copy()
            if usage_df.empty:
                continue
            usage_df["timestamp"] = pd.to_datetime(usage_df["timestamp_utc"])
            usage_df["display_value"] = usage_df["value"]

            chart = create_area_time_series(
                df=usage_df, x_col="timestamp", y_col="display_value",
                y_title="Memory Utilization (%)", color="#F18727",
                title=f"Memory Utilization - {service_label}",
            )
            return {"chart": chart, "is_k8s": False, "service_label": service_label}

    return None


def _find_metric_csvs(datadog_dir: Path) -> list[Path]:
    """Find all metric CSV files in the Datadog directory."""
    if not datadog_dir.exists():
        return []
    return (
        list(datadog_dir.glob("host_metrics_*.csv"))
        + list(datadog_dir.glob("k8s_metrics_*.csv"))
    )


# ---------------------------------------------------------------------------
# Bottleneck Tab Charts
# ---------------------------------------------------------------------------

def build_bottleneck_severity_chart(bottleneck_data: dict):
    """Vertical bar chart: Bottleneck findings by severity."""
    summary = bottleneck_data.get("summary", {})
    by_severity = summary.get("bottlenecks_by_severity", {})
    if not by_severity:
        return None

    rows = [{"severity": sev, "count": cnt} for sev, cnt in by_severity.items() if cnt > 0]
    if not rows:
        return None

    return create_severity_bar(pd.DataFrame(rows), title="Bottleneck Findings by Severity")


def build_bottleneck_type_chart(bottleneck_data: dict):
    """Horizontal bar chart: Bottleneck findings by type."""
    summary = bottleneck_data.get("summary", {})
    by_type = summary.get("bottlenecks_by_type", {})
    if not by_type:
        return None

    rows = [{"type": t.replace("_", " ").title(), "count": c} for t, c in by_type.items() if c > 0]
    if not rows:
        return None

    df = pd.DataFrame(rows)
    return create_horizontal_bar(
        df=df, x_col="count", y_col="type",
        x_title="Count", color="#5276A7",
        title="Bottleneck Findings by Type",
    )


# ---------------------------------------------------------------------------
# Log Analysis Tab Charts
# ---------------------------------------------------------------------------

def build_log_severity_chart(log_data: dict):
    """Vertical bar chart: Log issues by severity."""
    summary = log_data.get("summary", {})
    by_severity = summary.get("issues_by_severity", {})
    if not by_severity:
        return None

    rows = [{"severity": sev, "count": cnt} for sev, cnt in by_severity.items() if cnt > 0]
    if not rows:
        return None

    return create_severity_bar(pd.DataFrame(rows), title="Log Issues by Severity")


def build_log_category_chart(log_data: dict):
    """Horizontal bar chart: Log issues by error category."""
    summary = log_data.get("summary", {})
    by_category = summary.get("issues_by_category", {})
    if not by_category:
        return None

    rows = [{"category": cat, "count": cnt} for cat, cnt in by_category.items() if cnt > 0]
    if not rows:
        return None

    df = pd.DataFrame(rows).nlargest(15, "count")
    return create_horizontal_bar(
        df=df, x_col="count", y_col="category",
        x_title="Occurrences", color="#ff4136",
        title="Error Categories",
    )
