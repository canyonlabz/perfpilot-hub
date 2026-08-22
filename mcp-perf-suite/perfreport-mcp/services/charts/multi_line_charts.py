"""
Multi-line chart generators for infrastructure metrics.
Supports multiple hosts or Kubernetes services on a single chart.

These charts are used in the default report template to show consolidated
infrastructure metrics across all monitored resources.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from typing import List, Dict, Optional
from utils.chart_utils import get_chart_output_path, get_multi_line_colors, resolve_colors, apply_legend


# -----------------------------------------------
# Multi-Line Chart Generators
# -----------------------------------------------

async def generate_cpu_utilization_multiline_chart(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str
) -> dict:
    """
    Generate CPU utilization chart with one line per host/service.
    
    This chart shows CPU utilization (%) over time for ALL monitored hosts
    or Kubernetes services on a single chart, making it easy to compare
    resource usage across the infrastructure.
    
    Args:
        dataframes: Dict mapping resource_name to DataFrame with columns:
                   - timestamp_utc: datetime
                   - value: CPU utilization percentage
        chart_spec: Chart configuration from schema (chart_schema.yaml)
        run_id: Test run identifier for output path
    
    Returns:
        dict: {
            "chart_id": "CPU_UTILIZATION_MULTILINE",
            "path": str (full path to generated PNG),
            "resources": list (names of resources plotted)
        }
        Or dict with "error" key if generation fails.
    
    Example:
        dataframes = {
            "application-svc": df_auth,
            "api-gateway": df_gateway
        }
        result = await generate_cpu_utilization_multiline_chart(dataframes, spec, "80593110")
    """
    chart_id = "CPU_UTILIZATION_MULTILINE"
    
    if not dataframes:
        return {"chart_id": chart_id, "error": "No data provided"}
    
    # Chart configuration from schema
    title = chart_spec.get("title", "CPU Utilization - All Services")
    y_label = chart_spec.get("y_axis", {}).get("label", "CPU Utilization (%)")
    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")
    
    # Use multi-line color palette from chart_colors.yaml
    # Falls back to schema colors if multi_line not defined
    color_names = chart_spec.get("colors", [])
    if color_names:
        colors = resolve_colors(color_names, len(dataframes))
    else:
        colors = get_multi_line_colors(len(dataframes))
    
    # Figure sizing: 16:9 with YAML overrides
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)
    
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Plot each resource as a separate line
    resource_names = []
    for idx, (resource_name, df) in enumerate(dataframes.items()):
        if df is None or df.empty:
            continue
            
        df = df.copy()
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.sort_values(by="timestamp_utc")
        
        ax.plot(
            df["timestamp_utc"], 
            df["value"], 
            color=colors[idx], 
            linewidth=1.5,
            label=resource_name
        )
        resource_names.append(resource_name)
    
    if not resource_names:
        return {"chart_id": chart_id, "error": "No valid data to plot"}
    
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    
    if chart_spec.get("show_grid", True):
        ax.grid(True, linewidth=0.5, alpha=0.6)
    
    apply_legend(ax, chart_spec, num_series=len(resource_names))
    
    # Time axis formatting + label rotation
    locator = mdates.AutoDateLocator()
    formatter = mdates.DateFormatter("%H:%M")
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    
    for label in ax.get_xticklabels():
        label.set_rotation(45)
        label.set_horizontalalignment("right")
        label.set_rotation_mode("anchor")
    
    # Save with schema ID as filename (no hostname suffix for multi-line)
    bbox = chart_spec.get("bbox_inches", "tight")
    chart_path = get_chart_output_path(run_id, chart_id)
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)
    
    return {
        "chart_id": chart_id,
        "path": str(chart_path),
        "resources": resource_names
    }


async def generate_memory_utilization_multiline_chart(
    dataframes: Dict[str, pd.DataFrame],
    chart_spec: dict,
    run_id: str
) -> dict:
    """
    Generate Memory utilization chart with one line per host/service.
    
    This chart shows Memory utilization (%) over time for ALL monitored hosts
    or Kubernetes services on a single chart, making it easy to compare
    resource usage across the infrastructure.
    
    Args:
        dataframes: Dict mapping resource_name to DataFrame with columns:
                   - timestamp_utc: datetime
                   - value: Memory utilization percentage
        chart_spec: Chart configuration from schema (chart_schema.yaml)
        run_id: Test run identifier for output path
    
    Returns:
        dict: {
            "chart_id": "MEMORY_UTILIZATION_MULTILINE",
            "path": str (full path to generated PNG),
            "resources": list (names of resources plotted)
        }
        Or dict with "error" key if generation fails.
    
    Example:
        dataframes = {
            "application-svc": df_auth,
            "api-gateway": df_gateway
        }
        result = await generate_memory_utilization_multiline_chart(dataframes, spec, "80593110")
    """
    chart_id = "MEMORY_UTILIZATION_MULTILINE"
    
    if not dataframes:
        return {"chart_id": chart_id, "error": "No data provided"}
    
    # Chart configuration from schema
    title = chart_spec.get("title", "Memory Utilization - All Services")
    y_label = chart_spec.get("y_axis", {}).get("label", "Memory Utilization (%)")
    x_label = chart_spec.get("x_axis", {}).get("label", "Time (hh:mm) UTC")
    
    # Use multi-line color palette from chart_colors.yaml
    # Falls back to schema colors if multi_line not defined
    color_names = chart_spec.get("colors", [])
    if color_names:
        colors = resolve_colors(color_names, len(dataframes))
    else:
        colors = get_multi_line_colors(len(dataframes))
    
    # Figure sizing: 16:9 with YAML overrides
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)
    
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Plot each resource as a separate line
    resource_names = []
    for idx, (resource_name, df) in enumerate(dataframes.items()):
        if df is None or df.empty:
            continue
            
        df = df.copy()
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"])
        df = df.sort_values(by="timestamp_utc")
        
        ax.plot(
            df["timestamp_utc"], 
            df["value"], 
            color=colors[idx], 
            linewidth=1.5,
            label=resource_name
        )
        resource_names.append(resource_name)
    
    if not resource_names:
        return {"chart_id": chart_id, "error": "No valid data to plot"}
    
    ax.set_title(title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    
    if chart_spec.get("show_grid", True):
        ax.grid(True, linewidth=0.5, alpha=0.6)
    
    apply_legend(ax, chart_spec, num_series=len(resource_names))
    
    # Time axis formatting + label rotation
    locator = mdates.AutoDateLocator()
    formatter = mdates.DateFormatter("%H:%M")
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    
    for label in ax.get_xticklabels():
        label.set_rotation(45)
        label.set_horizontalalignment("right")
        label.set_rotation_mode("anchor")
    
    # Save with schema ID as filename (no hostname suffix for multi-line)
    bbox = chart_spec.get("bbox_inches", "tight")
    chart_path = get_chart_output_path(run_id, chart_id)
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)
    
    return {
        "chart_id": chart_id,
        "path": str(chart_path),
        "resources": resource_names
    }
