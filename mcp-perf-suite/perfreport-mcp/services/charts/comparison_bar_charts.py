"""
Vertical bar chart generators for comparison reports.
These charts visualize resource usage across multiple test runs.
X-axis: Test runs, Y-axis: Metric values
"""

import matplotlib.pyplot as plt
import numpy as np
from typing import Dict, List, Optional
from utils.chart_utils import (
    get_comparison_chart_output_path, 
    interpolate_placeholders,
    get_comparison_colors,
    resolve_colors
)


# -----------------------------------------------
# Comparison Bar Chart Generators
# -----------------------------------------------

async def generate_cpu_core_comparison_bar_chart(
    run_data: List[Dict],
    resource_name: str,
    chart_spec: dict,
    comparison_id: str
) -> dict:
    """
    Generate a vertical bar chart comparing CPU core usage across test runs.
    
    This chart shows one vertical bar per test run on the X-axis, with CPU
    usage values on the Y-axis. Makes it easy to visualize how CPU usage
    changed between runs for a specific resource.
    
    Supports multiple aggregation types via the 'aggregation' property in
    chart_spec (from chart_schema.yaml):
      - "peak": Uses peak_cores (maximum observed value)
      - "avg":  Uses avg_cores (average value across test run)
    
    Args:
        run_data: List of dicts with keys:
                 - run_id: Test run identifier
                 - peak_cores: Peak CPU usage in cores
                 - avg_cores: Average CPU usage in cores
        resource_name: Name of the host/service being compared
        chart_spec: Chart configuration from schema (chart_schema.yaml).
                    Must include 'aggregation' key ("peak" or "avg").
        comparison_id: Unique identifier for this comparison (e.g., timestamp)
    
    Returns:
        dict: {
            "chart_id": str (e.g., "CPU_PEAK_CORE_COMPARISON_BAR"),
            "resource": resource_name,
            "path": str (full path to generated PNG)
        }
        Or dict with "error" key if generation fails.
    
    Example:
        run_data = [
            {"run_id": "80593110", "peak_cores": 1.25, "avg_cores": 0.85},
            {"run_id": "80840304", "peak_cores": 1.42, "avg_cores": 0.92}
        ]
        result = await generate_cpu_core_comparison_bar_chart(
            run_data, "api-service", spec, "20260117"
        )
    """
    # Determine aggregation type from chart_spec (driven by chart_schema.yaml)
    aggregation = chart_spec.get("aggregation", "peak")
    
    # Resolve chart_id from the spec (supports both peak and avg variants)
    chart_id = chart_spec.get("id", f"CPU_{'PEAK' if aggregation == 'peak' else 'AVG'}_CORE_COMPARISON_BAR")
    
    if not run_data:
        return {"chart_id": chart_id, "resource": resource_name, "error": "No run data provided"}
    
    # Get unit configuration
    unit_config = chart_spec.get("unit", {})
    unit_type = unit_config.get("type", "cores")
    
    # Determine labels and conversion based on unit type
    if unit_type == "millicores":
        conversion_factor = 1000  # cores to millicores
        y_label = chart_spec.get("y_axis", {}).get("label", "CPU Usage (mCPU)")
        value_format = "{:.0f}"
    else:  # cores (default)
        conversion_factor = 1.0
        y_label = chart_spec.get("y_axis", {}).get("label", "CPU Usage (Cores)")
        value_format = "{:.3f}"
    
    # Select data key based on aggregation type
    data_key = "avg_cores" if aggregation == "avg" else "peak_cores"
    
    # Extract and convert data
    run_labels = [f"Run\n{d['run_id']}" for d in run_data]
    peak_values = [d.get(data_key, 0) * conversion_factor for d in run_data]
    
    # Chart configuration
    raw_title = chart_spec.get("title", f"CPU Core Usage Comparison - {resource_name}")
    title = interpolate_placeholders(raw_title, resource_name=resource_name)
    x_label = chart_spec.get("x_axis", {}).get("label", "Test Run")
    
    # Use comparison color palette from chart_colors.yaml (navy-blue gradient)
    # Falls back to schema colors if comparison not defined
    color_names = chart_spec.get("colors", [])
    if color_names:
        colors = resolve_colors(color_names, len(run_data))
    else:
        colors = get_comparison_colors(len(run_data))
    
    # Figure sizing
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)
    
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Create vertical bar chart
    x_pos = np.arange(len(run_labels))
    bar_width = 0.6
    bars = ax.bar(x_pos, peak_values, color=colors, width=bar_width)
    
    # Add value labels above bars if configured
    if chart_spec.get("show_value_labels", True):
        for bar, value in zip(bars, peak_values):
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height + max(peak_values) * 0.02,  # Slightly above bar
                value_format.format(value),
                ha='center',
                va='bottom',
                fontsize=9,
                fontweight='bold'
            )
    
    ax.set_xticks(x_pos)
    ax.set_xticklabels(run_labels)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    
    if chart_spec.get("show_grid", True):
        ax.grid(True, axis='y', linewidth=0.5, alpha=0.6)
    
    # Add some padding to the top for value labels
    if chart_spec.get("show_value_labels", True):
        ax.set_ylim(0, max(peak_values) * 1.15)
    
    # Ensure y-axis starts at 0
    ax.set_ylim(bottom=0)
    
    plt.tight_layout()
    
    # Save chart
    bbox = chart_spec.get("bbox_inches", "tight")
    safe_resource_name = resource_name.replace('/', '_').replace('\\', '_').replace('*', '')
    chart_path = get_comparison_chart_output_path(comparison_id, f"{chart_id}-{safe_resource_name}")
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)
    
    return {
        "chart_id": chart_id,
        "resource": resource_name,
        "path": str(chart_path),
        "unit": unit_type,
        "runs": len(run_data)
    }


async def generate_memory_usage_comparison_bar_chart(
    run_data: List[Dict],
    resource_name: str,
    chart_spec: dict,
    comparison_id: str
) -> dict:
    """
    Generate a vertical bar chart comparing memory usage across test runs.
    
    This chart shows one vertical bar per test run on the X-axis, with memory
    usage values on the Y-axis. Makes it easy to visualize how memory usage
    changed between runs for a specific resource.
    
    Supports multiple aggregation types via the 'aggregation' property in
    chart_spec (from chart_schema.yaml):
      - "peak": Uses peak_gb (maximum observed value)
      - "avg":  Uses avg_gb (average value across test run)
    
    Args:
        run_data: List of dicts with keys:
                 - run_id: Test run identifier
                 - peak_gb: Peak memory usage in GB
                 - avg_gb: Average memory usage in GB
        resource_name: Name of the host/service being compared
        chart_spec: Chart configuration from schema (chart_schema.yaml).
                    Must include 'aggregation' key ("peak" or "avg").
        comparison_id: Unique identifier for this comparison (e.g., timestamp)
    
    Returns:
        dict: {
            "chart_id": str (e.g., "MEMORY_PEAK_USAGE_COMPARISON_BAR"),
            "resource": resource_name,
            "path": str (full path to generated PNG)
        }
        Or dict with "error" key if generation fails.
    
    Example:
        run_data = [
            {"run_id": "80593110", "peak_gb": 2.5, "avg_gb": 1.8},
            {"run_id": "80840304", "peak_gb": 2.8, "avg_gb": 2.1}
        ]
        result = await generate_memory_usage_comparison_bar_chart(
            run_data, "api-service", spec, "20260117"
        )
    """
    # Determine aggregation type from chart_spec (driven by chart_schema.yaml)
    aggregation = chart_spec.get("aggregation", "peak")
    
    # Resolve chart_id from the spec (supports both peak and avg variants)
    chart_id = chart_spec.get("id", f"MEMORY_{'PEAK' if aggregation == 'peak' else 'AVG'}_USAGE_COMPARISON_BAR")
    
    if not run_data:
        return {"chart_id": chart_id, "resource": resource_name, "error": "No run data provided"}
    
    # Get unit configuration
    unit_config = chart_spec.get("unit", {})
    unit_type = unit_config.get("type", "gb")
    
    # Determine labels and conversion based on unit type
    if unit_type == "mb":
        conversion_factor = 1024  # GB to MB
        y_label = chart_spec.get("y_axis", {}).get("label", "Memory Usage (MB)")
        value_format = "{:.0f}"
    else:  # gb (default)
        conversion_factor = 1.0
        y_label = chart_spec.get("y_axis", {}).get("label", "Memory Usage (GB)")
        value_format = "{:.2f}"
    
    # Select data key based on aggregation type
    data_key = "avg_gb" if aggregation == "avg" else "peak_gb"
    
    # Extract and convert data
    run_labels = [f"Run\n{d['run_id']}" for d in run_data]
    peak_values = [d.get(data_key, 0) * conversion_factor for d in run_data]
    
    # Chart configuration
    raw_title = chart_spec.get("title", f"Memory Usage Comparison - {resource_name}")
    title = interpolate_placeholders(raw_title, resource_name=resource_name)
    x_label = chart_spec.get("x_axis", {}).get("label", "Test Run")
    
    # Use comparison color palette from chart_colors.yaml (navy-blue gradient)
    # Falls back to schema colors if comparison not defined
    color_names = chart_spec.get("colors", [])
    if color_names:
        colors = resolve_colors(color_names, len(run_data))
    else:
        colors = get_comparison_colors(len(run_data))
    
    # Figure sizing
    dpi = int(chart_spec.get("dpi", 144))
    width_px = int(chart_spec.get("width_px", 1280))
    height_px = int(chart_spec.get("height_px", 720))
    figsize = (width_px / dpi, height_px / dpi)
    
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Create vertical bar chart
    x_pos = np.arange(len(run_labels))
    bar_width = 0.6
    bars = ax.bar(x_pos, peak_values, color=colors, width=bar_width)
    
    # Add value labels above bars if configured
    if chart_spec.get("show_value_labels", True):
        for bar, value in zip(bars, peak_values):
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height + max(peak_values) * 0.02,  # Slightly above bar
                value_format.format(value),
                ha='center',
                va='bottom',
                fontsize=9,
                fontweight='bold'
            )
    
    ax.set_xticks(x_pos)
    ax.set_xticklabels(run_labels)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    
    if chart_spec.get("show_grid", True):
        ax.grid(True, axis='y', linewidth=0.5, alpha=0.6)
    
    # Add some padding to the top for value labels
    if chart_spec.get("show_value_labels", True):
        ax.set_ylim(0, max(peak_values) * 1.15)
    
    # Ensure y-axis starts at 0
    ax.set_ylim(bottom=0)
    
    plt.tight_layout()
    
    # Save chart
    bbox = chart_spec.get("bbox_inches", "tight")
    safe_resource_name = resource_name.replace('/', '_').replace('\\', '_').replace('*', '')
    chart_path = get_comparison_chart_output_path(comparison_id, f"{chart_id}-{safe_resource_name}")
    fig.savefig(chart_path, dpi=dpi, bbox_inches=bbox, facecolor="white")
    plt.close(fig)
    
    return {
        "chart_id": chart_id,
        "resource": resource_name,
        "path": str(chart_path),
        "unit": unit_type,
        "runs": len(run_data)
    }
