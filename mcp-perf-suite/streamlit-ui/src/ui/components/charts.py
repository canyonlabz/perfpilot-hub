"""
Altair Chart Factories - Reusable interactive chart builders.

Provides factory functions for common chart patterns used throughout
the KPI Dashboard. All charts include tooltips and interactive features.
"""

import altair as alt
import pandas as pd
from typing import Optional


def create_dual_axis_time_series(
    df: pd.DataFrame,
    x_col: str,
    y1_col: str,
    y2_col: str,
    y1_title: str = "Metric",
    y2_title: str = "Virtual Users",
    y1_color: str = "#5276A7",
    y2_color: str = "#F18727",
    title: str = "",
    height: int = 400,
) -> alt.LayerChart:
    """
    Create a dual-axis time series chart (e.g., Response Time vs VUsers).

    Args:
        df: DataFrame with time series data.
        x_col: Column name for x-axis (timestamp).
        y1_col: Column name for left y-axis metric.
        y2_col: Column name for right y-axis metric.
        y1_title: Left y-axis title.
        y2_title: Right y-axis title.
        y1_color: Line color for left axis.
        y2_color: Line color for right axis.
        title: Chart title.
        height: Chart height in pixels.

    Returns:
        Altair LayerChart with dual axes.
    """
    # Brush selection for time-range zoom
    brush = alt.selection_interval(encodings=["x"])

    base = alt.Chart(df).encode(
        x=alt.X(f"{x_col}:T", axis=alt.Axis(format="%H:%M:%S", labelAngle=45, title="Time")),
    )

    # Left y-axis: primary metric
    line1 = base.mark_line(color=y1_color, point=alt.OverlayMarkDef(size=30)).encode(
        y=alt.Y(f"{y1_col}:Q", axis=alt.Axis(title=y1_title)),
        tooltip=[
            alt.Tooltip(f"{x_col}:T", title="Time", format="%H:%M:%S"),
            alt.Tooltip(f"{y1_col}:Q", title=y1_title, format=",.0f"),
            alt.Tooltip(f"{y2_col}:Q", title=y2_title, format=",.0f"),
        ],
    )

    # Right y-axis: secondary metric (e.g., virtual users)
    line2 = base.mark_line(
        color=y2_color, strokeDash=[5, 3], point=alt.OverlayMarkDef(size=30)
    ).encode(
        y=alt.Y(f"{y2_col}:Q", axis=alt.Axis(title=y2_title)),
    )

    chart = alt.layer(line1, line2).resolve_scale(
        y="independent"
    ).properties(
        title=title,
        height=height,
    ).add_params(brush)

    return chart


def create_single_axis_time_series(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    y_title: str = "Value",
    color: str = "#5276A7",
    title: str = "",
    height: int = 350,
) -> alt.Chart:
    """Create a single-axis time series line chart."""
    brush = alt.selection_interval(encodings=["x"])

    chart = alt.Chart(df).mark_line(
        color=color, point=alt.OverlayMarkDef(size=30)
    ).encode(
        x=alt.X(f"{x_col}:T", axis=alt.Axis(format="%H:%M:%S", labelAngle=45, title="Time")),
        y=alt.Y(f"{y_col}:Q", axis=alt.Axis(title=y_title)),
        tooltip=[
            alt.Tooltip(f"{x_col}:T", title="Time", format="%H:%M:%S"),
            alt.Tooltip(f"{y_col}:Q", title=y_title, format=",.2f"),
        ],
    ).properties(
        title=title,
        height=height,
    ).add_params(brush)

    return chart


def create_multi_line_time_series(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    color_col: str,
    y_title: str = "Value",
    title: str = "",
    height: int = 400,
) -> alt.Chart:
    """Create a multi-line time series chart with legend toggle."""
    selection = alt.selection_point(fields=[color_col], bind="legend")

    chart = alt.Chart(df).mark_line(point=alt.OverlayMarkDef(size=20)).encode(
        x=alt.X(f"{x_col}:T", axis=alt.Axis(format="%H:%M:%S", labelAngle=45, title="Time")),
        y=alt.Y(f"{y_col}:Q", axis=alt.Axis(title=y_title)),
        color=alt.Color(f"{color_col}:N", legend=alt.Legend(title=None)),
        opacity=alt.condition(selection, alt.value(1), alt.value(0.15)),
        tooltip=[
            alt.Tooltip(f"{x_col}:T", title="Time", format="%H:%M:%S"),
            alt.Tooltip(f"{color_col}:N", title="Series"),
            alt.Tooltip(f"{y_col}:Q", title=y_title, format=",.2f"),
        ],
    ).properties(
        title=title,
        height=height,
    ).add_params(selection)

    return chart


def create_horizontal_bar(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    x_title: str = "Value",
    color: str = "#5276A7",
    title: str = "",
    height: Optional[int] = None,
) -> alt.Chart:
    """Create a horizontal bar chart (e.g., Top Slowest APIs)."""
    if height is None:
        height = max(200, len(df) * 28)

    chart = alt.Chart(df).mark_bar(color=color).encode(
        x=alt.X(f"{x_col}:Q", axis=alt.Axis(title=x_title)),
        y=alt.Y(f"{y_col}:N", sort="-x", axis=alt.Axis(title=None)),
        tooltip=[
            alt.Tooltip(f"{y_col}:N", title="API"),
            alt.Tooltip(f"{x_col}:Q", title=x_title, format=",.0f"),
        ],
    ).properties(
        title=title,
        height=height,
    )

    return chart


def create_donut_chart(
    df: pd.DataFrame,
    theta_col: str,
    color_col: str,
    color_scale: Optional[list] = None,
    title: str = "",
    size: int = 250,
) -> alt.Chart:
    """Create a donut/pie chart (e.g., Pass/Fail distribution)."""
    color_encoding = alt.Color(
        f"{color_col}:N",
        legend=alt.Legend(title=None),
    )
    if color_scale:
        domain = df[color_col].unique().tolist()
        color_encoding = alt.Color(
            f"{color_col}:N",
            scale=alt.Scale(domain=domain, range=color_scale),
            legend=alt.Legend(title=None),
        )

    chart = alt.Chart(df).mark_arc(innerRadius=50).encode(
        theta=alt.Theta(f"{theta_col}:Q"),
        color=color_encoding,
        tooltip=[
            alt.Tooltip(f"{color_col}:N"),
            alt.Tooltip(f"{theta_col}:Q", format=",.1f"),
        ],
    ).properties(
        title=title,
        width=size,
        height=size,
    )

    return chart


def create_area_time_series(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    y_title: str = "Value",
    color: str = "#5276A7",
    title: str = "",
    height: int = 400,
    limit_value: Optional[float] = None,
    limit_label: str = "Limit",
) -> alt.LayerChart:
    """
    Create a filled area chart with a line overlay for infrastructure metrics.

    Args:
        df: DataFrame with time series data (must be sorted by x_col).
        x_col: Column name for x-axis (timestamp).
        y_col: Column name for y-axis metric.
        y_title: Y-axis title.
        color: Fill and line color.
        title: Chart title.
        height: Chart height in pixels.
        limit_value: If provided, draws a horizontal dashed orange rule at this value.
        limit_label: Label for the limit line in the tooltip/legend.
    """
    base = alt.Chart(df.sort_values(x_col))

    area = base.mark_area(
        opacity=0.3,
        color=color,
        line={"color": color, "strokeWidth": 2},
    ).encode(
        x=alt.X(f"{x_col}:T", axis=alt.Axis(format="%H:%M", labelAngle=45, title="Time (hh:mm) UTC")),
        y=alt.Y(f"{y_col}:Q", axis=alt.Axis(title=y_title)),
        tooltip=[
            alt.Tooltip(f"{x_col}:T", title="Time", format="%H:%M:%S"),
            alt.Tooltip(f"{y_col}:Q", title=y_title, format=",.2f"),
        ],
    )

    layers = [area]

    if limit_value is not None and limit_value > 0:
        limit_line = alt.Chart(
            pd.DataFrame({"limit": [limit_value]})
        ).mark_rule(
            color="#F18727",
            strokeDash=[6, 4],
            strokeWidth=2,
        ).encode(
            y="limit:Q",
            tooltip=[alt.Tooltip("limit:Q", title=limit_label, format=",.2f")],
        )
        layers.append(limit_line)

    chart = alt.layer(*layers).properties(
        title=title,
        height=height,
    ).interactive()

    return chart


def create_severity_bar(
    df: pd.DataFrame,
    count_col: str = "count",
    severity_col: str = "severity",
    title: str = "Findings by Severity",
    height: int = 250,
) -> alt.Chart:
    """Create a vertical bar chart for severity distribution."""
    severity_order = ["critical", "high", "medium", "low", "info"]
    severity_colors = ["#ff4136", "#ff851b", "#ffdc00", "#7fdbff", "#aaaaaa"]

    chart = alt.Chart(df).mark_bar().encode(
        x=alt.X(
            f"{severity_col}:N",
            sort=severity_order,
            axis=alt.Axis(title=None),
        ),
        y=alt.Y(f"{count_col}:Q", axis=alt.Axis(title="Count")),
        color=alt.Color(
            f"{severity_col}:N",
            scale=alt.Scale(domain=severity_order, range=severity_colors),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip(f"{severity_col}:N", title="Severity"),
            alt.Tooltip(f"{count_col}:Q", title="Count"),
        ],
    ).properties(
        title=title,
        height=height,
    )

    return chart
