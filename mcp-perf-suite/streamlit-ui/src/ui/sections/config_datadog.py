"""
Datadog Config Section - Form fields for datadog-mcp configuration.
"""

import streamlit as st
from pathlib import Path

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
    _show_path_status,
)


def render_datadog_config_form(data: dict, key_prefix: str = "dd") -> dict:
    """Render the full Datadog config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)
    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # Datadog-specific section
    st.markdown("##### Datadog Settings")
    dd = data.get("datadog", {})

    col1, col2 = st.columns(2)
    with col1:
        ssl_verification = st.selectbox(
            "SSL Verification",
            options=["disabled", "ca_bundle"],
            index=0 if dd.get("ssl_verification", "disabled") == "disabled" else 1,
            key=f"{key_prefix}_ssl",
            help="'ca_bundle' uses certs from env vars; 'disabled' skips verification",
        )
        time_zone = st.text_input(
            "Time Zone",
            value=dd.get("time_zone", "America/New_York"),
            key=f"{key_prefix}_tz",
            help="IANA time zone (e.g., America/New_York, UTC)",
        )
        log_page_limit = st.number_input(
            "Log Page Limit",
            value=dd.get("log_page_limit", 100),
            min_value=10,
            max_value=1000,
            step=10,
            key=f"{key_prefix}_log_limit",
            help="Number of log entries to fetch per API page",
        )

    with col2:
        environments_json_path = st.text_input(
            "Environments JSON Path",
            value=dd.get("environments_json_path", ""),
            key=f"{key_prefix}_env_json",
            help="Path to environments.json file",
        )
        _show_path_status(environments_json_path)

        custom_queries_path = st.text_input(
            "Custom Queries JSON Path",
            value=dd.get("custom_queries_json_path", ""),
            key=f"{key_prefix}_queries_json",
            help="Path to custom_queries.json file",
        )
        _show_path_status(custom_queries_path)

        apm_page_limit = st.number_input(
            "APM Page Limit",
            value=dd.get("apm_page_limit", 100),
            min_value=10,
            max_value=1000,
            step=10,
            key=f"{key_prefix}_apm_limit",
            help="Number of APM traces to fetch per API page",
        )

    result["datadog"] = {
        "ssl_verification": ssl_verification,
        "time_zone": time_zone,
        "environments_json_path": environments_json_path,
        "custom_queries_json_path": custom_queries_path,
        "log_page_limit": log_page_limit,
        "apm_page_limit": apm_page_limit,
    }

    return result
