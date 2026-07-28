"""
Stacked area chart generators for infrastructure and KPI metrics.

Infrastructure charts (Kubernetes only) show per-service container/pod
breakdown over time. KPI charts show host-level metric breakdowns (e.g.,
CPU time decomposition).

Chart variants:
  - CPU_UTILIZATION_STACKED       (% utilization, requires k8s limits)
  - MEM_UTILIZATION_STACKED       (% utilization, requires k8s limits)
  - CPU_USAGE_STACKED             (raw millicores/cores, always available)
  - MEM_USAGE_STACKED             (raw MB/GB, always available)
  - HOST_CPU_BREAKDOWN_STACKED    (KPI: host CPU user/system/iowait/stolen/idle)
"""

import math
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from typing import Dict, Optional
from utils.chart_utils import get_chart_output_path, get_multi_line_colors, resolve_colors, apply_legend


# -----------------------------------------------
# Internal helper
# -----------------------------------------------

def _render_stacked_area(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str,
    chart_id: str,
    resource_name: str,
    y_label: str,
    title: str,
    conversion_factor: float = 1.0,
    y_max: Optional[float] = None,
) -> dict:
    """
    Core rendering logic shared by all stacked area chart functions.

    Args:
        dataframes: Dict mapping container/pod name -> DataFrame with columns
                    'timestamp_utc' and 'value'.
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier.
        chart_id: Chart ID used in the output filename.
        resource_name: Service/pod filter name for the chart title and filename.
        y_label: Y-axis label string.
        title: Chart title string (may contain {resource_name} placeholder).
        conversion_factor: Multiplier applied to 'value' before plotting
                          (e.g., 1e-6 for nanocores -> millicores).
        y_max: Optional fixed upper limit for the y-axis (e.g., 100 for %).

    Returns:
        dict with chart_id, path, resource, and containers list.
    """
    if not dataframes:
        return {"chart_id": chart_id, "error": "No data provided"}

    # Resolve title placeholder
    display_title = title.replace("{resource_name}", resource_name) if resource_name else title

    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")

    # Colors
    color_names = chart_spec.get("colors", [])
    if color_names:
        colors = resolve_colors(color_names, len(dataframes))
    else:
        colors = get_multi_line_colors(len(dataframes))

    # Figure sizing
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

    # Resample each container series to 1-minute intervals and align
    resampled = {}
    container_names = []

    for container_name, df in dataframes.items():
        if df is None or df.empty:
            continue

        df = df.copy()
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.sort_values(by="timestamp_utc")
        df["value"] = df["value"] * conversion_factor
        df = df.set_index("timestamp_utc")

        series = df["value"].resample("1min").mean()
        if not series.empty:
            resampled[container_name] = series
            container_names.append(container_name)

    if not container_names:
        plt.close(fig)
        return {"chart_id": chart_id, "error": "No valid data to plot"}

    # Align all series on a common time index
    aligned = pd.DataFrame(resampled)
    aligned = aligned.fillna(0)

    # Plot stacked area
    fill_alpha = float(chart_spec.get("fill_alpha", 0.4))

    ax.stackplot(
        aligned.index,
        [aligned[name].values for name in container_names],
        labels=container_names,
        colors=colors[:len(container_names)],
        alpha=fill_alpha,
    )

    for i, name in enumerate(container_names):
        ax.plot(aligned.index, aligned[name].values,
                color=colors[i % len(colors)], linewidth=1.2)

    ax.set_title(display_title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_ylim(bottom=0)

    if y_max is not None:
        data_max = aligned[container_names].sum(axis=1).max()
        if data_max > 0 and data_max < (y_max * 0.3):
            auto_top = data_max * 1.2
            if auto_top <= 10:
                auto_top = math.ceil(auto_top)
            elif auto_top <= 50:
                auto_top = math.ceil(auto_top / 5) * 5
            else:
                auto_top = math.ceil(auto_top / 10) * 10
            ax.set_ylim(top=auto_top)
        else:
            ax.set_ylim(top=y_max)

    if chart_spec.get("show_grid", True):
        ax.grid(True, linewidth=0.5, alpha=0.6)

    apply_legend(ax, chart_spec, num_series=len(container_names))

    # Time axis formatting + label rotation
    locator = mdates.AutoDateLocator()
    formatter = mdates.DateFormatter("%H:%M")
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)

    for label in ax.get_xticklabels():
        label.set_rotation(45)
        label.set_horizontalalignment("right")
        label.set_rotation_mode("anchor")

    plt.tight_layout()

    bbox = chart_spec.get("bbox_inches", "tight")
    chart_path = get_chart_output_path(run_id, f"{chart_id}-{resource_name}")
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)

    return {
        "chart_id": chart_id,
        "path": str(chart_path),
        "resource": resource_name,
        "containers": container_names,
    }


# -----------------------------------------------
# Percentage Utilization Charts (require k8s limits)
# -----------------------------------------------

async def generate_cpu_utilization_stacked_chart(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a stacked area chart showing CPU utilization (%) per container/pod.

    Each band represents a container within the service (main container +
    sidecars). Requires Kubernetes CPU limits to be set; if limits are not
    defined, the data source handler will filter out -1 sentinel values
    and suggest using CPU_USAGE_STACKED instead.

    Args:
        dataframes: Dict mapping container_or_pod name -> DataFrame with
                    columns 'timestamp_utc' and 'value' (% utilization).
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier.
        resource_name: Service/pod filter name for the chart title and filename.

    Returns:
        dict with chart_id, path, resource, and containers list.
    """
    chart_id = "CPU_UTILIZATION_STACKED"
    title = chart_spec.get("title", "CPU Utilization (%) - {resource_name}")
    y_label = chart_spec.get("y_axis", {}).get("label", "CPU Utilization (%)")
    y_max = chart_spec.get("y_axis", {}).get("max", 100)

    return _render_stacked_area(
        dataframes=dataframes,
        chart_spec=chart_spec,
        run_id=run_id,
        chart_id=chart_id,
        resource_name=resource_name,
        y_label=y_label,
        title=title,
        conversion_factor=1.0,  # Already in %
        y_max=y_max,
    )


async def generate_memory_utilization_stacked_chart(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a stacked area chart showing Memory utilization (%) per container/pod.

    Each band represents a container within the service (main container +
    sidecars). Requires Kubernetes Memory limits to be set; if limits are not
    defined, the data source handler will filter out -1 sentinel values
    and suggest using MEM_USAGE_STACKED instead.

    Args:
        dataframes: Dict mapping container_or_pod name -> DataFrame with
                    columns 'timestamp_utc' and 'value' (% utilization).
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier.
        resource_name: Service/pod filter name for the chart title and filename.

    Returns:
        dict with chart_id, path, resource, and containers list.
    """
    chart_id = "MEM_UTILIZATION_STACKED"
    title = chart_spec.get("title", "Memory Utilization (%) - {resource_name}")
    y_label = chart_spec.get("y_axis", {}).get("label", "Memory Utilization (%)")
    y_max = chart_spec.get("y_axis", {}).get("max", 100)

    return _render_stacked_area(
        dataframes=dataframes,
        chart_spec=chart_spec,
        run_id=run_id,
        chart_id=chart_id,
        resource_name=resource_name,
        y_label=y_label,
        title=title,
        conversion_factor=1.0,  # Already in %
        y_max=y_max,
    )


# -----------------------------------------------
# Raw Usage Charts (always available, no limits required)
# -----------------------------------------------

async def generate_cpu_usage_stacked_chart(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a stacked area chart showing raw CPU usage per container/pod.

    Converts from nanocores (Datadog kubernetes.cpu.usage.total) to the
    configured unit (millicores or cores) via chart_schema.yaml unit.type.
    Always available regardless of whether k8s CPU limits are set.

    Args:
        dataframes: Dict mapping container_or_pod name -> DataFrame with
                    columns 'timestamp_utc' and 'value' (nanocores).
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier.
        resource_name: Service/pod filter name for the chart title and filename.

    Returns:
        dict with chart_id, path, resource, containers, and unit.
    """
    chart_id = "CPU_USAGE_STACKED"

    # Resolve unit from chart_schema.yaml
    unit_config = chart_spec.get("unit", {})
    unit_type = unit_config.get("type", "millicores")

    if unit_type == "millicores":
        conversion_factor = 1.0e-6  # nanocores -> millicores
        unit_label = "Millicores"
        y_label = chart_spec.get("y_axis", {}).get("label", "CPU Usage (Millicores)")
    else:  # cores
        conversion_factor = 1.0e-9  # nanocores -> cores
        unit_label = "Cores"
        y_label = chart_spec.get("y_axis", {}).get("label", "CPU Usage (Cores)")

    title = chart_spec.get("title", "CPU Usage ({unit_label}) - {resource_name}")
    title = title.replace("{unit_label}", unit_label)

    result = _render_stacked_area(
        dataframes=dataframes,
        chart_spec=chart_spec,
        run_id=run_id,
        chart_id=chart_id,
        resource_name=resource_name,
        y_label=y_label,
        title=title,
        conversion_factor=conversion_factor,
    )
    result["unit"] = unit_type
    return result


async def generate_memory_usage_stacked_chart(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a stacked area chart showing raw Memory usage per container/pod.

    Converts from bytes (Datadog kubernetes.memory.usage) to the configured
    unit (MB or GB) via chart_schema.yaml unit.type.
    Always available regardless of whether k8s Memory limits are set.

    Args:
        dataframes: Dict mapping container_or_pod name -> DataFrame with
                    columns 'timestamp_utc' and 'value' (bytes).
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier.
        resource_name: Service/pod filter name for the chart title and filename.

    Returns:
        dict with chart_id, path, resource, containers, and unit.
    """
    chart_id = "MEM_USAGE_STACKED"

    # Resolve unit from chart_schema.yaml
    unit_config = chart_spec.get("unit", {})
    unit_type = unit_config.get("type", "mb")

    if unit_type == "mb":
        conversion_factor = 1.0 / (1024 * 1024)  # bytes -> MB
        unit_label = "MB"
        y_label = chart_spec.get("y_axis", {}).get("label", "Memory Usage (MB)")
    else:  # gb
        conversion_factor = 1.0 / (1024 * 1024 * 1024)  # bytes -> GB
        unit_label = "GB"
        y_label = chart_spec.get("y_axis", {}).get("label", "Memory Usage (GB)")

    title = chart_spec.get("title", "Memory Usage ({unit_label}) - {resource_name}")
    title = title.replace("{unit_label}", unit_label)

    result = _render_stacked_area(
        dataframes=dataframes,
        chart_spec=chart_spec,
        run_id=run_id,
        chart_id=chart_id,
        resource_name=resource_name,
        y_label=y_label,
        title=title,
        conversion_factor=conversion_factor,
    )
    result["unit"] = unit_type
    return result


# -----------------------------------------------
# KPI Stacked Area Charts
# -----------------------------------------------

async def generate_host_cpu_breakdown_stacked_chart(
    metric_dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str,
    resource_name: str = "",
) -> dict:
    """
    Generate a stacked area chart for host CPU time decomposition.

    Each band represents a CPU component (user, system, iowait, stolen, idle).
    The metric_dataframes dict is keyed by metric name (e.g. "host_cpu_user")
    with a DataFrame containing 'timestamp_utc' and 'value' columns. Values
    are already in percent and require no unit conversion.

    Args:
        metric_dataframes: Dict mapping metric_name -> DataFrame.
        chart_spec: Chart configuration from chart_schema.yaml.
        run_id: Test run identifier.
        resource_name: Hostname for the chart title and filename.

    Returns:
        dict with chart_id, path, resource, and metrics keys.
    """
    chart_id = "HOST_CPU_BREAKDOWN_STACKED"
    title = chart_spec.get("title", "Host CPU Breakdown - {resource_name}")
    y_label = chart_spec.get("y_axis", {}).get("label", "CPU (%)")
    y_max = chart_spec.get("y_axis", {}).get("max", 100)

    return _render_stacked_area(
        dataframes=metric_dataframes,
        chart_spec=chart_spec,
        run_id=run_id,
        chart_id=chart_id,
        resource_name=resource_name,
        y_label=y_label,
        title=title,
        conversion_factor=1.0,
        y_max=y_max,
    )
