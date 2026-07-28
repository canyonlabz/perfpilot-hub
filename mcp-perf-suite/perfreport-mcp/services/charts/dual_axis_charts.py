import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import pandas as pd
from typing import Dict, Optional
from fastmcp import Context
from utils.chart_utils import (
    get_chart_output_path,
    interpolate_placeholders,
    apply_legend,
)
from utils.config import load_chart_colors

# Load chart colors for color name resolution
CHART_COLORS = load_chart_colors()

def resolve_color(color_name: str) -> str:
    """Resolve color name (e.g., 'primary') to actual color value (e.g., '#1f77b4')"""
    return CHART_COLORS.get(color_name, color_name)

# -----------------------------------------------
# Dual Axis Chart Generators
# -----------------------------------------------
async def generate_p90_vusers_chart(df: pd.DataFrame, chart_spec: dict, run_id: str):
    """
    Generate and save a dual-axis line chart of P90 Response Time vs Virtual Users.

    Args:
        df (pd.DataFrame): test-results.csv (JTL) loaded as DataFrame.
            Required columns: timeStamp (ms), elapsed (ms), allThreads (int).
        chart_spec (dict): Chart configuration from YAML/schema.
            Optional keys: title, x_axis.label, y_axis_left.label, y_axis_right.label,
                           colors [left, right], dpi, width_px, height_px, bbox_inches,
                           show_grid (bool), include_legend (bool)
        run_id (str): test run identifier for output path.

    Returns:
        dict: { "chart_type": "RESP_TIME_P90_VUSERS_DUALAXIS", "path": <png path> }
    """
    # ---- 1) Prepare timestamps & group by minute ----------------------------
    # timeStamp comes from JTL in milliseconds epoch
    df = df.copy()
    df["timeStamp"] = pd.to_datetime(df["timeStamp"], unit="ms", errors="coerce")
    df = df.dropna(subset=["timeStamp"]).sort_values("timeStamp")

    # Group into minute buckets for a clean, readable trend
    df["minute"] = df["timeStamp"].dt.floor("min")
    grouped = df.groupby("minute", as_index=True)

    # ---- 2) Compute metrics -------------------------------------------------
    # p90 of response time (ms) per minute, and mean virtual users per minute
    if "elapsed" not in df.columns or "allThreads" not in df.columns:
        return {"chart_type": "RESP_TIME_P90_VUSERS_DUALAXIS",
                "error": "Missing required columns: 'elapsed' and/or 'allThreads'."}

    p90_ms = grouped["elapsed"].quantile(0.90)
    vusers = grouped["allThreads"].max()

    # ---- 3) Labels, colors, figure sizing ----------------------------------
    # Titles & axis labels (with interpolation support if you wired it in)
    raw_title = chart_spec.get("title", "P90 Response Time vs Virtual Users")
    try:
        # if you have interpolate_placeholders available
        title = interpolate_placeholders(raw_title, run_id=run_id)
    except Exception:
        title = raw_title

    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm)")
    y_left_label = chart_spec.get("y_axis_left", {}).get("label", "P90 Response Time (ms)")
    y_right_label = chart_spec.get("y_axis_right", {}).get("label", "Virtual Users")

    # Resolve colors (tokens → hex; or accept hex/named directly)
    color_tokens = chart_spec.get("colors", ["primary", "secondary"])
    left_color  = resolve_color(color_tokens[0] if len(color_tokens) > 0 else "C0")
    right_color = resolve_color(color_tokens[1] if len(color_tokens) > 1 else "C1")

    # Figure sizing: 16:9 defaults, overridable via YAML
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))   # 16:9 default
    height_px = int(chart_spec.get("height_px", 720))  # 16:9 default
    figsize = (width_px / dpi, height_px / dpi)

    fig, ax_left = plt.subplots(figsize=figsize, dpi=dpi)

    # ---- 4) Plot left axis (P90 ms) ----------------------------------------
    ax_left.plot(p90_ms.index, p90_ms.values, color=left_color, linewidth=1.8, label=y_left_label)
    ax_left.fill_between(p90_ms.index, p90_ms.values, alpha=0.1, color=left_color)
    ax_left.set_ylabel(y_left_label, color=left_color)
    ax_left.tick_params(axis="y", labelcolor=left_color)
    ax_left.set_xlabel(x_label)
    ax_left.set_title(title)

    # ---- 5) Plot right axis (virtual users) --------------------------------
    ax_right = ax_left.twinx()
    ax_right.plot(vusers.index, vusers.values, color=right_color, linewidth=1.8, label=y_right_label)
    ax_right.set_ylabel(y_right_label, color=right_color)
    ax_right.tick_params(axis="y", labelcolor=right_color)
    ax_right.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax_right.set_ylim(bottom=0, top=int(vusers.max()) + 1)

    # ---- 6) Time axis formatting & rotation --------------------------------
    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.DateFormatter("%H:%M")
    ax_left.xaxis.set_major_locator(locator)
    ax_left.xaxis.set_major_formatter(formatter)

    for lbl in ax_left.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_horizontalalignment("right")
        lbl.set_rotation_mode("anchor")

    # ---- 7) Grid / legend / save -------------------------------------------
    if chart_spec.get("show_grid", True):
        ax_left.grid(True, linewidth=0.5, alpha=0.6)

    if chart_spec.get("include_legend"):
        l1, lab1 = ax_left.get_legend_handles_labels()
        l2, lab2 = ax_right.get_legend_handles_labels()
        location = chart_spec.get("legend_location", "upper left")
        fontsize = chart_spec.get("legend_fontsize", 8)
        if location in ("below", "above", "right"):
            ncol = min(len(lab1) + len(lab2), 4)
            anchor_map = {"below": ("upper center", (0.5, -0.18)), "above": ("lower center", (0.5, 1.05)), "right": ("center left", (1.02, 0.5))}
            loc_val, bbox = anchor_map[location]
            kw = {"ncol": ncol} if location != "right" else {}
            ax_left.legend(l1 + l2, lab1 + lab2, loc=loc_val, bbox_to_anchor=bbox, fontsize=fontsize, frameon=True, **kw)
        else:
            ax_left.legend(l1 + l2, lab1 + lab2, loc=location, fontsize=fontsize)

    # Save with schema ID as filename (no hostname for performance charts)
    chart_id = "RESP_TIME_P90_VUSERS_DUALAXIS"
    bbox = chart_spec.get("bbox_inches", "tight")
    chart_path = get_chart_output_path(run_id, chart_id)
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)

    return {"chart_id": chart_id, "path": str(chart_path)}


# -----------------------------------------------
# Infrastructure vs VUsers Dual-Axis Charts
# -----------------------------------------------

async def generate_cpu_utilization_vusers_chart(
    infra_dataframes: Dict[str, pd.DataFrame],
    perf_df: pd.DataFrame,
    chart_spec: dict,
    run_id: str
) -> dict:
    """
    Generate a dual-axis chart showing CPU Utilization (%) vs Virtual Users over time.
    
    This chart correlates infrastructure CPU usage with the load applied during
    performance testing, helping identify whether CPU becomes a bottleneck
    as virtual users increase.
    
    Args:
        infra_dataframes: Dict mapping resource_name to DataFrame with columns:
                         - timestamp_utc: datetime
                         - value: CPU utilization percentage
        perf_df: DataFrame from test-results.csv with columns:
                - timeStamp: epoch milliseconds
                - allThreads: virtual user count
        chart_spec: Chart configuration from schema (chart_schema.yaml)
        run_id: Test run identifier for output path
    
    Returns:
        dict: {
            "chart_id": "CPU_UTILIZATION_VUSERS_DUALAXIS",
            "path": str (full path to generated PNG),
            "resources": list (names of resources used for averaging)
        }
        Or dict with "error" key if generation fails.
    """
    chart_id = "CPU_UTILIZATION_VUSERS_DUALAXIS"
    
    if not infra_dataframes:
        return {"chart_id": chart_id, "error": "No infrastructure data provided"}
    
    if perf_df is None or perf_df.empty:
        return {"chart_id": chart_id, "error": "No performance data provided"}
    
    # ---- 1) Process performance data (VUsers) --------------------------------
    perf_df = perf_df.copy()
    perf_df["timeStamp"] = pd.to_datetime(perf_df["timeStamp"], unit="ms", errors="coerce")
    perf_df = perf_df.dropna(subset=["timeStamp"]).sort_values("timeStamp")
    perf_df["minute"] = perf_df["timeStamp"].dt.floor("min")
    vusers = perf_df.groupby("minute")["allThreads"].max()
    
    # ---- 2) Process infrastructure data (CPU %) ------------------------------
    # Aggregate CPU utilization across all resources by computing the mean
    all_cpu_dfs = []
    resource_names = []
    
    for resource_name, df in infra_dataframes.items():
        if df is None or df.empty:
            continue
        
        df = df.copy()
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.sort_values(by="timestamp_utc")
        df["minute"] = df["timestamp_utc"].dt.floor("min")
        
        # Group by minute and get mean CPU for this resource
        resource_cpu = df.groupby("minute")["value"].mean()
        all_cpu_dfs.append(resource_cpu)
        resource_names.append(resource_name)
    
    if not all_cpu_dfs:
        return {"chart_id": chart_id, "error": "No valid CPU data to aggregate"}
    
    # Average CPU across all resources
    cpu_combined = pd.concat(all_cpu_dfs, axis=1)
    cpu_avg = cpu_combined.mean(axis=1)
    
    # ---- 3) Align time ranges ------------------------------------------------
    # Find common time range
    common_start = max(cpu_avg.index.min(), vusers.index.min())
    common_end = min(cpu_avg.index.max(), vusers.index.max())
    
    cpu_avg = cpu_avg[(cpu_avg.index >= common_start) & (cpu_avg.index <= common_end)]
    vusers = vusers[(vusers.index >= common_start) & (vusers.index <= common_end)]
    
    if cpu_avg.empty or vusers.empty:
        return {"chart_id": chart_id, "error": "No overlapping time range between infrastructure and performance data"}
    
    # ---- 4) Chart configuration ----------------------------------------------
    title = chart_spec.get("title", "CPU Utilization vs Virtual Users")
    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")
    y_left_label = chart_spec.get("y_axis_left", {}).get("label", "CPU Utilization (%)")
    y_right_label = chart_spec.get("y_axis_right", {}).get("label", "Virtual Users")
    
    color_tokens = chart_spec.get("colors", ["primary", "secondary"])
    left_color = resolve_color(color_tokens[0] if len(color_tokens) > 0 else "primary")
    right_color = resolve_color(color_tokens[1] if len(color_tokens) > 1 else "secondary")
    
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)
    
    # ---- 5) Create dual-axis plot --------------------------------------------
    fig, ax_left = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Plot CPU utilization on left axis
    ax_left.plot(cpu_avg.index, cpu_avg.values, color=left_color, linewidth=1.8, label=y_left_label)
    ax_left.fill_between(cpu_avg.index, cpu_avg.values, alpha=0.1, color=left_color)
    ax_left.set_ylabel(y_left_label, color=left_color)
    ax_left.tick_params(axis="y", labelcolor=left_color)
    ax_left.set_xlabel(x_label)
    ax_left.set_title(title)
    
    # Plot virtual users on right axis
    ax_right = ax_left.twinx()
    ax_right.plot(vusers.index, vusers.values, color=right_color, linewidth=1.8, label=y_right_label)
    ax_right.set_ylabel(y_right_label, color=right_color)
    ax_right.tick_params(axis="y", labelcolor=right_color)
    ax_right.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax_right.set_ylim(bottom=0, top=int(vusers.max()) + 1)
    
    # ---- 6) Time axis formatting ---------------------------------------------
    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.DateFormatter("%H:%M")
    ax_left.xaxis.set_major_locator(locator)
    ax_left.xaxis.set_major_formatter(formatter)
    
    for lbl in ax_left.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_horizontalalignment("right")
        lbl.set_rotation_mode("anchor")
    
    # ---- 7) Grid / legend / save ---------------------------------------------
    if chart_spec.get("show_grid", True):
        ax_left.grid(True, linewidth=0.5, alpha=0.6)
    
    if chart_spec.get("include_legend", True):
        l1, lab1 = ax_left.get_legend_handles_labels()
        l2, lab2 = ax_right.get_legend_handles_labels()
        location = chart_spec.get("legend_location", "upper left")
        fontsize = chart_spec.get("legend_fontsize", 8)
        if location in ("below", "above", "right"):
            ncol = min(len(lab1) + len(lab2), 4)
            anchor_map = {"below": ("upper center", (0.5, -0.18)), "above": ("lower center", (0.5, 1.05)), "right": ("center left", (1.02, 0.5))}
            loc_val, bbox = anchor_map[location]
            kw = {"ncol": ncol} if location != "right" else {}
            ax_left.legend(l1 + l2, lab1 + lab2, loc=loc_val, bbox_to_anchor=bbox, fontsize=fontsize, frameon=True, **kw)
        else:
            ax_left.legend(l1 + l2, lab1 + lab2, loc=location, fontsize=fontsize)
    
    bbox = chart_spec.get("bbox_inches", "tight")
    chart_path = get_chart_output_path(run_id, chart_id)
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)
    
    return {
        "chart_id": chart_id,
        "path": str(chart_path),
        "resources": resource_names
    }


async def generate_memory_utilization_vusers_chart(
    infra_dataframes: Dict[str, pd.DataFrame],
    perf_df: pd.DataFrame,
    chart_spec: dict,
    run_id: str
) -> dict:
    """
    Generate a dual-axis chart showing Memory Utilization (%) vs Virtual Users over time.
    
    This chart correlates infrastructure memory usage with the load applied during
    performance testing, helping identify whether memory becomes a bottleneck
    as virtual users increase.
    
    Args:
        infra_dataframes: Dict mapping resource_name to DataFrame with columns:
                         - timestamp_utc: datetime
                         - value: Memory utilization percentage
        perf_df: DataFrame from test-results.csv with columns:
                - timeStamp: epoch milliseconds
                - allThreads: virtual user count
        chart_spec: Chart configuration from schema (chart_schema.yaml)
        run_id: Test run identifier for output path
    
    Returns:
        dict: {
            "chart_id": "MEMORY_UTILIZATION_VUSERS_DUALAXIS",
            "path": str (full path to generated PNG),
            "resources": list (names of resources used for averaging)
        }
        Or dict with "error" key if generation fails.
    """
    chart_id = "MEMORY_UTILIZATION_VUSERS_DUALAXIS"
    
    if not infra_dataframes:
        return {"chart_id": chart_id, "error": "No infrastructure data provided"}
    
    if perf_df is None or perf_df.empty:
        return {"chart_id": chart_id, "error": "No performance data provided"}
    
    # ---- 1) Process performance data (VUsers) --------------------------------
    perf_df = perf_df.copy()
    perf_df["timeStamp"] = pd.to_datetime(perf_df["timeStamp"], unit="ms", errors="coerce")
    perf_df = perf_df.dropna(subset=["timeStamp"]).sort_values("timeStamp")
    perf_df["minute"] = perf_df["timeStamp"].dt.floor("min")
    vusers = perf_df.groupby("minute")["allThreads"].max()
    
    # ---- 2) Process infrastructure data (Memory %) ---------------------------
    # Aggregate Memory utilization across all resources by computing the mean
    all_mem_dfs = []
    resource_names = []
    
    for resource_name, df in infra_dataframes.items():
        if df is None or df.empty:
            continue
        
        df = df.copy()
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.sort_values(by="timestamp_utc")
        df["minute"] = df["timestamp_utc"].dt.floor("min")
        
        # Group by minute and get mean memory for this resource
        resource_mem = df.groupby("minute")["value"].mean()
        all_mem_dfs.append(resource_mem)
        resource_names.append(resource_name)
    
    if not all_mem_dfs:
        return {"chart_id": chart_id, "error": "No valid memory data to aggregate"}
    
    # Average memory across all resources
    mem_combined = pd.concat(all_mem_dfs, axis=1)
    mem_avg = mem_combined.mean(axis=1)
    
    # ---- 3) Align time ranges ------------------------------------------------
    # Find common time range
    common_start = max(mem_avg.index.min(), vusers.index.min())
    common_end = min(mem_avg.index.max(), vusers.index.max())
    
    mem_avg = mem_avg[(mem_avg.index >= common_start) & (mem_avg.index <= common_end)]
    vusers = vusers[(vusers.index >= common_start) & (vusers.index <= common_end)]
    
    if mem_avg.empty or vusers.empty:
        return {"chart_id": chart_id, "error": "No overlapping time range between infrastructure and performance data"}
    
    # ---- 4) Chart configuration ----------------------------------------------
    title = chart_spec.get("title", "Memory Utilization vs Virtual Users")
    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")
    y_left_label = chart_spec.get("y_axis_left", {}).get("label", "Memory Utilization (%)")
    y_right_label = chart_spec.get("y_axis_right", {}).get("label", "Virtual Users")
    
    color_tokens = chart_spec.get("colors", ["warning", "secondary"])
    left_color = resolve_color(color_tokens[0] if len(color_tokens) > 0 else "warning")
    right_color = resolve_color(color_tokens[1] if len(color_tokens) > 1 else "secondary")
    
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)
    
    # ---- 5) Create dual-axis plot --------------------------------------------
    fig, ax_left = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Plot memory utilization on left axis
    ax_left.plot(mem_avg.index, mem_avg.values, color=left_color, linewidth=1.8, label=y_left_label)
    ax_left.fill_between(mem_avg.index, mem_avg.values, alpha=0.1, color=left_color)
    ax_left.set_ylabel(y_left_label, color=left_color)
    ax_left.tick_params(axis="y", labelcolor=left_color)
    ax_left.set_xlabel(x_label)
    ax_left.set_title(title)
    
    # Plot virtual users on right axis
    ax_right = ax_left.twinx()
    ax_right.plot(vusers.index, vusers.values, color=right_color, linewidth=1.8, label=y_right_label)
    ax_right.set_ylabel(y_right_label, color=right_color)
    ax_right.tick_params(axis="y", labelcolor=right_color)
    ax_right.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax_right.set_ylim(bottom=0, top=int(vusers.max()) + 1)
    
    # ---- 6) Time axis formatting ---------------------------------------------
    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.DateFormatter("%H:%M")
    ax_left.xaxis.set_major_locator(locator)
    ax_left.xaxis.set_major_formatter(formatter)
    
    for lbl in ax_left.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_horizontalalignment("right")
        lbl.set_rotation_mode("anchor")
    
    # ---- 7) Grid / legend / save ---------------------------------------------
    if chart_spec.get("show_grid", True):
        ax_left.grid(True, linewidth=0.5, alpha=0.6)
    
    if chart_spec.get("include_legend", True):
        l1, lab1 = ax_left.get_legend_handles_labels()
        l2, lab2 = ax_right.get_legend_handles_labels()
        location = chart_spec.get("legend_location", "upper left")
        fontsize = chart_spec.get("legend_fontsize", 8)
        if location in ("below", "above", "right"):
            ncol = min(len(lab1) + len(lab2), 4)
            anchor_map = {"below": ("upper center", (0.5, -0.18)), "above": ("lower center", (0.5, 1.05)), "right": ("center left", (1.02, 0.5))}
            loc_val, bbox = anchor_map[location]
            kw = {"ncol": ncol} if location != "right" else {}
            ax_left.legend(l1 + l2, lab1 + lab2, loc=loc_val, bbox_to_anchor=bbox, fontsize=fontsize, frameon=True, **kw)
        else:
            ax_left.legend(l1 + l2, lab1 + lab2, loc=location, fontsize=fontsize)
    
    bbox = chart_spec.get("bbox_inches", "tight")
    chart_path = get_chart_output_path(run_id, chart_id)
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)
    
    return {
        "chart_id": chart_id,
        "path": str(chart_path),
        "resources": resource_names
    }


# -----------------------------------------------
# KPI vs VUsers Dual-Axis Charts
# -----------------------------------------------

_KPI_DUAL_AXIS_CONVERSIONS = {
    "ms": 1000.0,
    "mb": 1.0 / (1024 * 1024),
    "gb": 1.0 / (1024 * 1024 * 1024),
    "kb": 1.0 / 1024,
    "percent": 1.0,
}


async def generate_kpi_latency_vusers_chart(
    kpi_df: pd.DataFrame,
    perf_df: pd.DataFrame,
    chart_spec: dict,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a dual-axis chart of server-side KPI latency vs virtual users.

    Left axis: KPI P90 latency from kpi_metrics_*.csv (converted via unit.type).
    Right axis: Virtual users from BlazeMeter test-results.csv.
    Time ranges are aligned to the overlapping period.

    Args:
        kpi_df: Pre-filtered DataFrame for the target KPI metric with columns
                'timestamp_utc' and 'value'.
        perf_df: BlazeMeter test-results.csv DataFrame with columns
                 'timeStamp' (epoch ms) and 'allThreads'.
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier for output path.
        resource_name: Entity name for filename suffix (optional).

    Returns:
        dict with chart_id, path, resource, and unit keys.
    """
    chart_id = "KPI_LATENCY_VUSERS_DUALAXIS"

    if kpi_df is None or kpi_df.empty:
        return {"chart_id": chart_id, "error": "No KPI latency data available."}
    if perf_df is None or perf_df.empty:
        return {"chart_id": chart_id, "error": "No performance data (VUsers) available."}

    # ---- 1) Process KPI data (left axis) ------------------------------------
    kpi_df = kpi_df.copy()
    kpi_df["timestamp_utc"] = pd.to_datetime(kpi_df["timestamp_utc"])
    kpi_df = kpi_df.sort_values(by="timestamp_utc")
    kpi_df["minute"] = kpi_df["timestamp_utc"].dt.floor("min")

    unit_config = chart_spec.get("unit", {})
    unit_type = unit_config.get("type", "ms")
    conversion = _KPI_DUAL_AXIS_CONVERSIONS.get(unit_type, 1.0)
    kpi_df["converted"] = kpi_df["value"] * conversion

    kpi_series = kpi_df.groupby("minute")["converted"].mean()

    # ---- 2) Process performance data (right axis) ---------------------------
    perf_df = perf_df.copy()
    perf_df["timeStamp"] = pd.to_datetime(perf_df["timeStamp"], unit="ms", errors="coerce")
    perf_df = perf_df.dropna(subset=["timeStamp"]).sort_values("timeStamp")
    perf_df["minute"] = perf_df["timeStamp"].dt.floor("min")
    vusers = perf_df.groupby("minute")["allThreads"].max()

    # ---- 3) Align time ranges -----------------------------------------------
    common_start = max(kpi_series.index.min(), vusers.index.min())
    common_end = min(kpi_series.index.max(), vusers.index.max())
    kpi_series = kpi_series[(kpi_series.index >= common_start) & (kpi_series.index <= common_end)]
    vusers = vusers[(vusers.index >= common_start) & (vusers.index <= common_end)]

    if kpi_series.empty or vusers.empty:
        return {"chart_id": chart_id, "error": "No overlapping time range between KPI and performance data."}

    # ---- 4) Chart configuration ---------------------------------------------
    kpi_title = chart_spec.get("title", "Server Latency (P90) vs Virtual Users")
    kpi_title = interpolate_placeholders(kpi_title, resource_name=resource_name)
    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")
    y_left_label = chart_spec.get("y_axis_left", {}).get("label", "P90 Latency (ms)")
    y_right_label = chart_spec.get("y_axis_right", {}).get("label", "Virtual Users")

    color_tokens = chart_spec.get("colors", ["kpi_latency", "secondary"])
    left_color = resolve_color(color_tokens[0] if color_tokens else "primary")
    right_color = resolve_color(color_tokens[1] if len(color_tokens) > 1 else "secondary")

    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)

    # ---- 5) Create dual-axis plot -------------------------------------------
    fig, ax_left = plt.subplots(figsize=figsize, dpi=dpi)

    ax_left.plot(kpi_series.index, kpi_series.values, color=left_color, linewidth=1.8, label=y_left_label)
    ax_left.fill_between(kpi_series.index, kpi_series.values, alpha=0.1, color=left_color)
    ax_left.set_ylabel(y_left_label, color=left_color)
    ax_left.tick_params(axis="y", labelcolor=left_color)
    ax_left.set_xlabel(x_label)
    ax_left.set_title(kpi_title)

    ax_right = ax_left.twinx()
    ax_right.plot(vusers.index, vusers.values, color=right_color, linewidth=1.8, label=y_right_label)
    ax_right.set_ylabel(y_right_label, color=right_color)
    ax_right.tick_params(axis="y", labelcolor=right_color)
    ax_right.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax_right.set_ylim(bottom=0, top=int(vusers.max()) + 1)

    # ---- 6) Time axis formatting --------------------------------------------
    kpi_locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    kpi_formatter = mdates.DateFormatter("%H:%M")
    ax_left.xaxis.set_major_locator(kpi_locator)
    ax_left.xaxis.set_major_formatter(kpi_formatter)
    for lbl in ax_left.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_horizontalalignment("right")
        lbl.set_rotation_mode("anchor")

    # ---- 7) Grid / legend / save --------------------------------------------
    if chart_spec.get("show_grid", True):
        ax_left.grid(True, linewidth=0.5, alpha=0.6)

    if chart_spec.get("include_legend", True):
        kl1, klab1 = ax_left.get_legend_handles_labels()
        kl2, klab2 = ax_right.get_legend_handles_labels()
        loc_str = chart_spec.get("legend_location", "upper left")
        fs = chart_spec.get("legend_fontsize", 8)
        if loc_str in ("below", "above", "right"):
            ncol = min(len(klab1) + len(klab2), 4)
            anchor_map = {
                "below": ("upper center", (0.5, -0.18)),
                "above": ("lower center", (0.5, 1.05)),
                "right": ("center left", (1.02, 0.5)),
            }
            loc_val, bbox_anchor = anchor_map[loc_str]
            kw = {"ncol": ncol} if loc_str != "right" else {}
            ax_left.legend(kl1 + kl2, klab1 + klab2, loc=loc_val, bbox_to_anchor=bbox_anchor,
                           fontsize=fs, frameon=True, **kw)
        else:
            ax_left.legend(kl1 + kl2, klab1 + klab2, loc=loc_str, fontsize=fs)

    suffix = f"-{resource_name}" if resource_name else ""
    kpi_chart_path = get_chart_output_path(run_id, f"{chart_id}{suffix}")
    fig.savefig(kpi_chart_path, dpi=dpi, bbox_inches=chart_spec.get("bbox_inches", "tight"), facecolor="white")
    plt.close(fig)

    return {"chart_id": chart_id, "resource": resource_name, "path": str(kpi_chart_path), "unit": unit_type}


async def generate_kpi_dual_metric_chart(
    metric_dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    chart_id: str,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a dual-axis chart from two KPI metrics in kpi_metrics_*.csv.

    This supports charts where the two related KPI series can have different
    scales, such as bytes received vs bytes sent.
    """
    if not metric_dataframes:
        return {"chart_id": chart_id, "error": "No KPI data available for these metrics."}

    metric_filter = chart_spec.get("metric_filter", [])
    if not isinstance(metric_filter, list) or len(metric_filter) != 2:
        return {"chart_id": chart_id, "error": "KPI dual-metric charts require exactly two metric filters."}

    left_metric, right_metric = metric_filter
    left_df = metric_dataframes.get(left_metric)
    right_df = metric_dataframes.get(right_metric)

    if left_df is None or left_df.empty:
        return {"chart_id": chart_id, "error": f"No data for left metric '{left_metric}'."}
    if right_df is None or right_df.empty:
        return {"chart_id": chart_id, "error": f"No data for right metric '{right_metric}'."}

    unit_type = chart_spec.get("unit", {}).get("type", "")
    conversion = _KPI_DUAL_AXIS_CONVERSIONS.get(unit_type, 1.0)

    def _prepare_series(df: pd.DataFrame) -> pd.Series:
        prepared = df.copy()
        prepared["timestamp_utc"] = pd.to_datetime(prepared["timestamp_utc"])
        prepared = prepared.sort_values(by="timestamp_utc")
        prepared["minute"] = prepared["timestamp_utc"].dt.floor("min")
        prepared["converted"] = prepared["value"] * conversion
        return prepared.groupby("minute")["converted"].mean()

    left_series = _prepare_series(left_df)
    right_series = _prepare_series(right_df)

    common_start = max(left_series.index.min(), right_series.index.min())
    common_end = min(left_series.index.max(), right_series.index.max())
    left_series = left_series[(left_series.index >= common_start) & (left_series.index <= common_end)]
    right_series = right_series[(right_series.index >= common_start) & (right_series.index <= common_end)]

    if left_series.empty or right_series.empty:
        return {"chart_id": chart_id, "error": "No overlapping time range between KPI metrics."}

    raw_title = chart_spec.get("title", chart_id)
    title = interpolate_placeholders(raw_title, resource_name=resource_name)
    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")
    y_left_label = chart_spec.get("y_axis_left", {}).get("label", left_metric)
    y_right_label = chart_spec.get("y_axis_right", {}).get("label", right_metric)

    color_tokens = chart_spec.get("colors", ["primary", "secondary"])
    left_color = resolve_color(color_tokens[0] if color_tokens else "primary")
    right_color = resolve_color(color_tokens[1] if len(color_tokens) > 1 else "secondary")
    fill_alpha = float(chart_spec.get("fill_alpha", 0.15))

    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)

    fig, ax_left = plt.subplots(figsize=figsize, dpi=dpi)

    ax_left.plot(left_series.index, left_series.values, color=left_color, linewidth=1.8, label=y_left_label)
    ax_left.fill_between(left_series.index, 0, left_series.values, alpha=fill_alpha, color=left_color)
    ax_left.set_ylabel(y_left_label, color=left_color)
    ax_left.tick_params(axis="y", labelcolor=left_color)
    ax_left.set_xlabel(x_label)
    ax_left.set_title(title)
    ax_left.set_ylim(bottom=0)

    ax_right = ax_left.twinx()
    ax_right.plot(right_series.index, right_series.values, color=right_color, linewidth=1.8, label=y_right_label)
    ax_right.fill_between(right_series.index, 0, right_series.values, alpha=fill_alpha, color=right_color)
    ax_right.set_ylabel(y_right_label, color=right_color)
    ax_right.tick_params(axis="y", labelcolor=right_color)
    ax_right.set_ylim(bottom=0)

    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    formatter = mdates.DateFormatter("%H:%M")
    ax_left.xaxis.set_major_locator(locator)
    ax_left.xaxis.set_major_formatter(formatter)
    for lbl in ax_left.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_horizontalalignment("right")
        lbl.set_rotation_mode("anchor")

    if chart_spec.get("show_grid", True):
        ax_left.grid(True, linewidth=0.5, alpha=0.6)

    if chart_spec.get("include_legend", True):
        l1, lab1 = ax_left.get_legend_handles_labels()
        l2, lab2 = ax_right.get_legend_handles_labels()
        loc_str = chart_spec.get("legend_location", "upper left")
        fs = chart_spec.get("legend_fontsize", 8)
        ax_left.legend(l1 + l2, lab1 + lab2, loc=loc_str, fontsize=fs)

    suffix = f"-{resource_name}" if resource_name else ""
    chart_path = get_chart_output_path(run_id, f"{chart_id}{suffix}")
    fig.savefig(chart_path, dpi=dpi, bbox_inches=chart_spec.get("bbox_inches", "tight"), facecolor="white")
    plt.close(fig)

    return {
        "chart_id": chart_id,
        "resource": resource_name,
        "path": str(chart_path),
        "unit": unit_type,
        "metrics": [left_metric, right_metric],
    }
