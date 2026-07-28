"""
Export Helper Components - Download buttons for various formats.

Provides reusable export/download functionality for DataFrames,
charts, and report content.
"""

import io
import streamlit as st
import pandas as pd
from typing import Optional


def render_csv_download(
    df: pd.DataFrame,
    filename: str = "export.csv",
    label: str = "Download CSV",
    key: Optional[str] = None,
):
    """
    Render a download button for a DataFrame as CSV.

    Args:
        df: DataFrame to export.
        filename: Download filename.
        label: Button label text.
        key: Unique widget key.
    """
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    csv_data = csv_buffer.getvalue()

    st.download_button(
        label=label,
        data=csv_data,
        file_name=filename,
        mime="text/csv",
        key=key,
    )


def render_json_download(
    data: dict,
    filename: str = "export.json",
    label: str = "Download JSON",
    key: Optional[str] = None,
):
    """
    Render a download button for a dictionary as JSON.

    Args:
        data: Dictionary to export.
        filename: Download filename.
        label: Button label text.
        key: Unique widget key.
    """
    import json
    json_str = json.dumps(data, indent=2, default=str)

    st.download_button(
        label=label,
        data=json_str,
        file_name=filename,
        mime="application/json",
        key=key,
    )
