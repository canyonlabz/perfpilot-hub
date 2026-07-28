"""
Confluence Config Section - Form fields for confluence-mcp configuration.
"""

import streamlit as st

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
)


def render_confluence_config_form(data: dict, key_prefix: str = "cf") -> dict:
    """Render the full Confluence config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)
    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # Confluence-specific section
    st.markdown("##### Confluence Settings")
    cf = data.get("confluence", {})

    col1, col2 = st.columns(2)
    with col1:
        ssl_verification = st.selectbox(
            "SSL Verification",
            options=["disabled", "ca_bundle"],
            index=0 if cf.get("ssl_verification", "disabled") == "disabled" else 1,
            key=f"{key_prefix}_ssl",
            help="'ca_bundle' uses certs from env vars; 'disabled' skips verification",
        )
    with col2:
        pagination_limit = st.number_input(
            "Pagination Limit",
            value=cf.get("pagination_limit", 150),
            min_value=10,
            max_value=500,
            step=10,
            key=f"{key_prefix}_pagination",
        )

    result["confluence"] = {
        "ssl_verification": ssl_verification,
        "pagination_limit": pagination_limit,
    }

    return result
