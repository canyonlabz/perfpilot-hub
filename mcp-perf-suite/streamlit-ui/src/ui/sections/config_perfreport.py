"""
PerfReport Config Section - Form fields for perfreport-mcp configuration.
"""

import streamlit as st

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
    _show_path_status,
)


def render_perfreport_config_form(data: dict, key_prefix: str = "pr") -> dict:
    """Render the full PerfReport config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)
    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # PerfReport-specific section
    st.markdown("##### Report Settings")
    pr = data.get("perf_report", {})

    col1, col2, col3 = st.columns(3)
    with col1:
        time_zone = st.text_input(
            "Time Zone",
            value=pr.get("time_zone", "America/New_York"),
            key=f"{key_prefix}_tz",
            help="IANA time zone for report timestamps",
        )
    with col2:
        apm_tool = st.selectbox(
            "APM Tool",
            options=["datadog", "newrelic", "appdynamics", "dynatrace"],
            index=["datadog", "newrelic", "appdynamics", "dynatrace"].index(
                pr.get("apm_tool", "datadog")
            ),
            key=f"{key_prefix}_apm_tool",
        )
    with col3:
        templates_path = st.text_input(
            "Templates Path",
            value=pr.get("templates_path", ""),
            key=f"{key_prefix}_templates",
            help="Path to report Markdown templates",
        )
        _show_path_status(templates_path)

    result["perf_report"] = {
        "time_zone": time_zone,
        "apm_tool": apm_tool,
        "templates_path": templates_path,
    }

    return result
