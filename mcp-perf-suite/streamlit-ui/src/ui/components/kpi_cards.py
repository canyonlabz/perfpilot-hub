"""
KPI Card Components - Reusable metric card layouts.

Provides functions for rendering rows of KPI metric cards
using st.columns() + st.metric() with consistent styling.
"""

import streamlit as st
from typing import Optional


def render_kpi_row(metrics: list[dict], columns: int = 4):
    """
    Render a row of KPI metric cards.

    Args:
        metrics: List of dicts with keys: label, value, delta (optional), help (optional)
        columns: Number of columns in the row.
    """
    cols = st.columns(columns, border=True)
    for idx, metric in enumerate(metrics):
        col_idx = idx % columns
        with cols[col_idx]:
            st.metric(
                label=metric["label"],
                value=metric["value"],
                delta=metric.get("delta"),
                help=metric.get("help"),
            )


def render_status_card(
    label: str,
    value: str,
    status: str = "normal",
    help_text: Optional[str] = None,
):
    """
    Render a single status card with color-coded background.

    Args:
        label: Card title.
        value: Display value.
        status: One of "success", "warning", "error", "normal".
        help_text: Optional tooltip.
    """
    color_map = {
        "success": "#1a3d1a",
        "warning": "#3d3a1a",
        "error": "#3d1a1a",
        "normal": "#1a2a3a",
    }
    text_color_map = {
        "success": "#2ecc40",
        "warning": "#ffdc00",
        "error": "#ff4136",
        "normal": "#d0e0f0",
    }

    bg = color_map.get(status, color_map["normal"])
    fg = text_color_map.get(status, text_color_map["normal"])

    html = f"""
    <div style="background-color: {bg}; padding: 12px 16px; border-radius: 8px;
                margin: 4px 0;">
        <div style="color: #8ab4d0; font-size: 0.8rem; font-weight: 600;">{label}</div>
        <div style="color: {fg}; font-size: 1.3rem; font-weight: 700;">{value}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
    if help_text:
        st.caption(help_text)
