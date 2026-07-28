"""
Data Table Components - Styled DataFrame and HTML table renderers.

Provides functions for rendering performance data in tabular format
with conditional formatting and styling.
"""

import streamlit as st
import pandas as pd
from typing import Optional


def render_styled_dataframe(
    df: pd.DataFrame,
    height: Optional[int] = None,
    use_container_width: bool = True,
):
    """
    Render a DataFrame with Streamlit's native dataframe widget.

    Args:
        df: DataFrame to display.
        height: Optional fixed height. Auto-calculated if None.
        use_container_width: Whether to stretch to container width.
    """
    if height is None:
        height = min(40 * len(df) + 40, 600)

    # Map old flag to new width API
    width = "stretch" if use_container_width else "content"

    st.dataframe(df, width=width, height=height)


def render_html_table(df: pd.DataFrame, max_rows: Optional[int] = None):
    """
    Render a DataFrame as a styled HTML table with the .custom-table class.

    Args:
        df: DataFrame to display.
        max_rows: Optional limit on rows displayed.
    """
    display_df = df.head(max_rows) if max_rows else df
    html = display_df.to_html(index=False, escape=False, classes="custom-table")
    st.markdown(html, unsafe_allow_html=True)


def render_sla_table(df: pd.DataFrame):
    """
    Render an SLA compliance table with pass/fail badges.

    Expected columns: api_name, sla_compliant, p90_response_time (or similar),
                      sla_threshold_ms.
    """
    if df.empty:
        st.info("No SLA data available.")
        return

    # Add badge column
    display_df = df.copy()
    if "sla_compliant" in display_df.columns:
        display_df["Status"] = display_df["sla_compliant"].apply(
            lambda x: '<span class="sla-pass">PASS</span>'
            if x
            else '<span class="sla-fail">FAIL</span>'
        )

    render_html_table(display_df)


def render_severity_table(df: pd.DataFrame):
    """
    Render a table with severity-colored badges.

    Expected column: severity (critical, high, medium, low, info).
    """
    if df.empty:
        st.info("No findings data available.")
        return

    display_df = df.copy()
    if "severity" in display_df.columns:
        display_df["Severity"] = display_df["severity"].apply(
            lambda x: f'<span class="severity-{x.lower()}">{x.upper()}</span>'
            if isinstance(x, str)
            else x
        )

    render_html_table(display_df)
