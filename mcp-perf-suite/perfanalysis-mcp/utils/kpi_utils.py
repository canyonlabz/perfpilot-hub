"""
KPI Timeseries Utilities

Shared utilities for loading, categorizing, converting, and summarizing
KPI timeseries data from APM-generated CSV files (kpi_metrics_*.csv).

Used by all three PerfAnalysis tools:
  - analyze_environment_metrics  (performance_analyzer / kpi_analyzer)
  - correlate_test_results       (statistical_analyzer / kpi_analyzer)
  - identify_bottlenecks         (bottleneck_analyzer / kpi_analyzer)

Technology-neutral: works with any runtime (Java, .NET, Go, Python, etc.)
as long as users follow the recommended naming conventions in custom_queries.json.
"""
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import pandas as pd
import numpy as np


# -----------------------------------------------
# Category Registry — single source of truth
# -----------------------------------------------
# Checked in order; first match wins.  Any metric that does not match
# a pattern falls through to the "custom" fallback category.

CATEGORY_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ----- Host-level categories (checked first — prefix avoids collisions) -----
    "host_cpu": {
        "patterns": [lambda m: m.startswith("host_cpu")],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time"],
        "interpretation": "Host CPU utilization impact on application performance",
    },
    "host_memory": {
        "patterns": [lambda m: m.startswith("host_mem")],
        "unit_conversion": {"from": "bytes", "to": "GB", "factor": 1 / 1_073_741_824},
        "correlate_with": ["p90_response_time"],
        "interpretation": "Host memory availability impact on performance",
    },
    "disk_io": {
        "patterns": [lambda m: "disk_queue" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time"],
        "interpretation": "Disk I/O queue depth impact on latency",
    },
    "disk_usage": {
        "patterns": [lambda m: "disk_used" in m],
        "unit_conversion": {"from": "bytes", "to": "GB", "factor": 1 / 1_073_741_824},
        "correlate_with": ["p90_response_time"],
        "interpretation": "Disk space utilization trend",
    },
    "network_io": {
        "patterns": [lambda m: "net_bytes" in m],
        "unit_conversion": {"from": "bytes", "to": "KB", "factor": 1 / 1_024},
        "correlate_with": ["p90_response_time", "request_count"],
        "interpretation": "Network throughput vs application performance",
    },
    "iis": {
        "patterns": [lambda m: "iis_" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time", "request_count"],
        "interpretation": "IIS web server activity vs application performance",
    },
    "sql_server": {
        "patterns": [lambda m: "sql_" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time"],
        "interpretation": "SQL Server activity impact on application latency",
    },
    "process_queue": {
        "patterns": [lambda m: "proc_queue" in m or "queue_length" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time"],
        "interpretation": "Processor queue depth impact on responsiveness",
    },
    # ----- Application / k8s-level categories -----
    "latency": {
        "patterns": [lambda m: "latency" in m],
        "unit_conversion": {"from": "seconds", "to": "ms", "factor": 1000},
        "correlate_with": ["p90_response_time", "sla_violations"],
        "interpretation": "Server-side latency vs client-observed P90 performance",
    },
    "gc_heap": {
        "patterns": [lambda m: m.startswith("gc_size_")],
        "unit_conversion": {"from": "bytes", "to": "MB", "factor": 1 / 1_048_576},
        "correlate_with": ["mem_util_pct", "p90_response_time"],
        "interpretation": "Managed heap impact on container memory and latency",
    },
    "gc_pressure": {
        "patterns": [lambda m: "gc_memory" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time", "sla_violations"],
        "interpretation": "GC pressure impact on application responsiveness",
    },
    "gc_activity": {
        "patterns": [lambda m: m.startswith("gc_count_")],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time"],
        "interpretation": "GC collection frequency impact on latency",
    },
    "gc_pause": {
        "patterns": [lambda m: "gc_pause" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time", "sla_violations"],
        "interpretation": "GC pause duration impact on latency",
    },
    "process_cpu": {
        "patterns": [lambda m: "process_cpu" in m or "cpu_user" in m or "cpu_system" in m],
        "unit_conversion": None,
        "correlate_with": ["cpu_util_pct", "p90_response_time"],
        "interpretation": "Process-level CPU vs container/host CPU and latency",
    },
    "process_memory": {
        "patterns": [lambda m: "process_memory" in m or "mem_committed" in m],
        "unit_conversion": {"from": "bytes", "to": "MB", "factor": 1 / 1_048_576},
        "correlate_with": ["mem_util_pct"],
        "interpretation": "Process memory vs container/host memory utilization",
    },
    "threads": {
        "patterns": [lambda m: "thread" in m and "contention" not in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time"],
        "interpretation": "Thread pool health impact on latency",
    },
    "contention": {
        "patterns": [lambda m: "contention" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time", "sla_violations"],
        "interpretation": "Lock contention impact on application responsiveness",
    },
    "connections": {
        "patterns": [lambda m: "connection" in m],
        "unit_conversion": None,
        "correlate_with": ["p90_response_time", "request_count"],
        "interpretation": "Connection pool health vs throughput and latency",
    },
    "throughput": {
        "patterns": [lambda m: "hits" in m or "throughput" in m or "requests_per" in m],
        "unit_conversion": None,
        "correlate_with": ["request_count"],
        "interpretation": "Server-side vs client-side throughput agreement",
    },
    "errors": {
        "patterns": [lambda m: "error" in m or "exception" in m],
        "unit_conversion": None,
        "correlate_with": ["sla_violations"],
        "interpretation": "Server errors/exceptions driving SLA violations",
    },
}


# -----------------------------------------------
# File Discovery
# -----------------------------------------------

def discover_kpi_files(apm_path: Path) -> List[Path]:
    """Discover KPI timeseries CSV files in the APM tool artifact directory.

    Args:
        apm_path: Path to the APM tool directory (e.g. artifacts/{run}/datadog/).

    Returns:
        Sorted list of matching kpi_metrics_*.csv file paths (empty if none found).
    """
    if not apm_path.exists():
        return []
    return sorted(apm_path.glob("kpi_metrics_*.csv"))


# -----------------------------------------------
# Scope Detection
# -----------------------------------------------

def detect_kpi_scope(df: pd.DataFrame) -> str:
    """Detect the predominant scope from a loaded KPI DataFrame.

    Returns ``"host"``, ``"k8s"``, or ``"mixed"`` based on the ``scope``
    column values.  Falls back to ``"k8s"`` when the column is absent
    (backward compatibility with older CSVs).
    """
    if "scope" not in df.columns:
        return "k8s"
    scopes = set(df["scope"].dropna().unique())
    if scopes == {"host"}:
        return "host"
    if scopes == {"k8s"}:
        return "k8s"
    if len(scopes) > 1:
        return "mixed"
    return "k8s"


# -----------------------------------------------
# Metric Categorization
# -----------------------------------------------

def categorize_metric(metric_name: str) -> str:
    """Categorize a KPI metric by matching against the CATEGORY_REGISTRY patterns.

    Categories are checked in registry order; first match wins.
    Metrics that match no pattern are returned as ``"custom"``.

    Args:
        metric_name: The metric name from the CSV ``metric`` column.

    Returns:
        Category string (e.g. ``"latency"``, ``"gc_heap"``, ``"custom"``).
    """
    for category, definition in CATEGORY_REGISTRY.items():
        for pattern_fn in definition["patterns"]:
            if pattern_fn(metric_name):
                return category
    return "custom"


def get_category_registry() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the category registry for external consumption."""
    return dict(CATEGORY_REGISTRY)


def get_correlation_targets(metric_name: str) -> List[str]:
    """Return the correlation target columns for a given metric.

    Args:
        metric_name: KPI metric name.

    Returns:
        List of target column names to correlate against, or empty list for
        ``"custom"`` category metrics.
    """
    category = categorize_metric(metric_name)
    if category == "custom":
        return []
    return list(CATEGORY_REGISTRY[category].get("correlate_with", []))


def get_interpretation(metric_name: str) -> str:
    """Return the interpretation template for a given metric's category."""
    category = categorize_metric(metric_name)
    if category == "custom":
        return "Custom KPI metric"
    return CATEGORY_REGISTRY[category].get("interpretation", "")


# -----------------------------------------------
# KPI Data Loading
# -----------------------------------------------

def _resolve_identifier(df: pd.DataFrame) -> pd.DataFrame:
    """Create a unified ``identifier`` column based on the ``scope`` column.

    For ``scope=host``, the identifier is the ``hostname`` column.
    For ``scope=k8s`` (or any other scope), the identifier is ``filter``.
    If ``scope`` is absent, falls back to ``filter`` for backward compatibility.

    Args:
        df: Raw KPI DataFrame with original CSV columns.

    Returns:
        The same DataFrame with an ``identifier`` column added.
    """
    if "scope" not in df.columns:
        df["identifier"] = df.get("filter", pd.Series("unknown", index=df.index))
        return df

    def _pick(row):
        if row.get("scope") == "host":
            val = row.get("hostname")
            if pd.notna(val) and str(val).strip():
                return str(val).strip()
        val = row.get("filter")
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
        val = row.get("hostname")
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
        return "unknown"

    df["identifier"] = df.apply(_pick, axis=1)
    return df


def load_kpi_dataframe(kpi_files: List[Path]) -> Optional[pd.DataFrame]:
    """Load all KPI CSV files into a single unified DataFrame.

    The returned DataFrame retains the original long-format schema plus
    a unified ``identifier`` column resolved from ``hostname`` (host scope)
    or ``filter`` (k8s scope).

    Args:
        kpi_files: List of kpi_metrics_*.csv file paths.

    Returns:
        Combined DataFrame or ``None`` if all files are empty / unreadable.
    """
    frames: List[pd.DataFrame] = []

    for csv_file in kpi_files:
        try:
            df = pd.read_csv(csv_file)
            if df.empty:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp_utc"], utc=True)
            frames.append(df)
        except Exception as e:
            print(f"[kpi_utils] Warning: could not read {csv_file}: {e}")
            continue

    if not frames:
        return None

    combined = pd.concat(frames, ignore_index=True)
    combined = _resolve_identifier(combined)
    return combined.sort_values("timestamp").reset_index(drop=True)


def load_kpi_pivoted(
    kpi_files: List[Path],
    convert_units: bool = True,
) -> Optional[pd.DataFrame]:
    """Load KPI CSVs and pivot into one-column-per-metric format.

    Used by the correlation and bottleneck tools where each metric needs
    to be a separate column aligned on timestamp.

    Args:
        kpi_files: List of kpi_metrics_*.csv file paths.
        convert_units: If True, apply unit conversions (latency s→ms,
                       heap bytes→MB) via :func:`convert_kpi_units`.

    Returns:
        DataFrame with columns: ``timestamp``, ``identifier``, and one column
        per unique metric name.  Returns ``None`` if no data.
    """
    raw_df = load_kpi_dataframe(kpi_files)
    if raw_df is None or raw_df.empty:
        return None

    if "identifier" not in raw_df.columns:
        return None

    all_metric_dfs: List[pd.DataFrame] = []
    for metric_name in raw_df["metric"].unique():
        metric_slice = raw_df[raw_df["metric"] == metric_name][
            ["timestamp", "identifier", "value"]
        ].copy()
        metric_slice = metric_slice.rename(columns={"value": metric_name})
        all_metric_dfs.append(metric_slice)

    if not all_metric_dfs:
        return None

    result = all_metric_dfs[0]
    for additional in all_metric_dfs[1:]:
        result = pd.merge(
            result, additional, on=["timestamp", "identifier"], how="outer"
        )

    if convert_units:
        result = convert_kpi_units(result)

    return result.sort_values("timestamp").reset_index(drop=True)


# -----------------------------------------------
# Unit Conversion
# -----------------------------------------------

def convert_kpi_units(df: pd.DataFrame) -> pd.DataFrame:
    """Apply unit conversions to KPI metric columns in-place.

    Conversions are driven by the CATEGORY_REGISTRY ``unit_conversion``
    definitions, matched via :func:`categorize_metric`.

    Args:
        df: DataFrame with one column per metric name.

    Returns:
        The same DataFrame with converted values.
    """
    for col in df.columns:
        if col in ("timestamp", "identifier"):
            continue
        category = categorize_metric(col)
        if category == "custom":
            continue
        conversion = CATEGORY_REGISTRY[category].get("unit_conversion")
        if conversion is not None:
            factor = conversion["factor"]
            df[col] = df[col] * factor
    return df


def get_display_unit(metric_name: str, original_unit: str) -> str:
    """Return the display unit after conversion, or the original if no conversion applies."""
    category = categorize_metric(metric_name)
    if category == "custom":
        return original_unit
    conversion = CATEGORY_REGISTRY[category].get("unit_conversion")
    if conversion is not None:
        return conversion["to"]
    return original_unit


def get_conversion_factor(metric_name: str) -> float:
    """Return the numeric conversion factor for a metric, or 1.0 if none applies."""
    category = categorize_metric(metric_name)
    if category == "custom":
        return 1.0
    conversion = CATEGORY_REGISTRY.get(category, {}).get("unit_conversion")
    if conversion is not None:
        return conversion["factor"]
    return 1.0


# -----------------------------------------------
# Statistical Summaries
# -----------------------------------------------

def compute_metric_summary(series: pd.Series) -> Dict[str, Any]:
    """Compute descriptive statistics and trend for a single metric's timeseries.

    Args:
        series: Numeric pandas Series of metric values (NaN-safe).

    Returns:
        Dict with keys: min, max, avg, p90, p95, std_dev, samples, trend.
    """
    clean = series.dropna()
    if clean.empty:
        return {
            "min": None, "max": None, "avg": None,
            "p90": None, "p95": None, "std_dev": None,
            "samples": 0, "trend": "unknown",
        }

    return {
        "min": float(clean.min()),
        "max": float(clean.max()),
        "avg": float(clean.mean()),
        "p90": float(clean.quantile(0.90)),
        "p95": float(clean.quantile(0.95)),
        "std_dev": float(clean.std()) if len(clean) > 1 else 0.0,
        "samples": int(len(clean)),
        "trend": _compute_trend(clean),
    }


def _compute_trend(series: pd.Series) -> str:
    """Determine whether a timeseries is increasing, decreasing, or stable.

    Uses linear regression slope relative to the mean.  A slope whose
    absolute cumulative change is less than 10% of the mean is considered
    stable.
    """
    if len(series) < 3:
        return "stable"

    y = series.values.astype(float)
    x = np.arange(len(y), dtype=float)

    try:
        slope, _ = np.polyfit(x, y, 1)
    except (np.linalg.LinAlgError, ValueError):
        return "stable"

    mean_val = np.mean(y)
    if mean_val == 0:
        return "stable"

    cumulative_change = slope * len(y)
    relative_change = abs(cumulative_change / mean_val)

    if relative_change < 0.10:
        return "stable"
    return "increasing" if slope > 0 else "decreasing"
