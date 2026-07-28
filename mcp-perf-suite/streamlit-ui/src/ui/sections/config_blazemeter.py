"""
BlazeMeter Config Section - Form fields for blazemeter-mcp configuration.
"""

import streamlit as st

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
)


def render_blazemeter_config_form(data: dict, key_prefix: str = "bz") -> dict:
    """Render the full BlazeMeter config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)
    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # BlazeMeter-specific section
    st.markdown("##### BlazeMeter Settings")
    bz = data.get("blazemeter", {})

    col1, col2 = st.columns(2)
    with col1:
        ssl_verification = st.selectbox(
            "SSL Verification",
            options=["disabled", "ca_bundle"],
            index=0 if bz.get("ssl_verification", "disabled") == "disabled" else 1,
            key=f"{key_prefix}_ssl",
            help="'ca_bundle' uses certs from env vars; 'disabled' skips verification",
        )
        polling_interval = st.number_input(
            "Polling Interval (seconds)",
            value=bz.get("polling_interval_seconds", 30),
            min_value=5,
            max_value=300,
            step=5,
            key=f"{key_prefix}_poll_interval",
        )
        polling_max_retries = st.number_input(
            "Polling Max Retries",
            value=bz.get("polling_max_retries", 3),
            min_value=1,
            max_value=20,
            step=1,
            key=f"{key_prefix}_poll_retries",
        )

    with col2:
        polling_timeout = st.number_input(
            "Polling Timeout (seconds)",
            value=bz.get("polling_timeout_seconds", 600),
            min_value=60,
            max_value=3600,
            step=30,
            key=f"{key_prefix}_poll_timeout",
        )
        pagination_limit = st.number_input(
            "Pagination Limit",
            value=bz.get("pagination_limit", 150),
            min_value=10,
            max_value=500,
            step=10,
            key=f"{key_prefix}_pagination",
        )
        download_retries = st.number_input(
            "Artifact Download Max Retries",
            value=bz.get("artifact_download_max_retries", 3),
            min_value=1,
            max_value=10,
            step=1,
            key=f"{key_prefix}_dl_retries",
        )

    retry_delay = st.number_input(
        "Artifact Download Retry Delay (seconds)",
        value=bz.get("artifact_download_retry_delay", 2),
        min_value=1,
        max_value=30,
        step=1,
        key=f"{key_prefix}_dl_delay",
    )
    cleanup_sessions = st.toggle(
        "Cleanup Session Folders",
        value=bz.get("cleanup_session_folders", False),
        key=f"{key_prefix}_cleanup",
        help="Remove sessions/ subfolder after combining artifacts",
    )

    result["blazemeter"] = {
        "ssl_verification": ssl_verification,
        "polling_interval_seconds": polling_interval,
        "polling_max_retries": polling_max_retries,
        "polling_timeout_seconds": polling_timeout,
        "pagination_limit": pagination_limit,
        "artifact_download_max_retries": download_retries,
        "artifact_download_retry_delay": retry_delay,
        "cleanup_session_folders": cleanup_sessions,
    }

    return result
