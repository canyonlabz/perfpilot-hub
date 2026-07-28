"""
MS Graph Config Section - Form fields for msgraph-mcp configuration.
"""

import streamlit as st

from src.ui.sections.config_common import (
    render_server_section,
    render_general_section,
    render_logging_section,
    render_artifacts_section,
)


def render_msgraph_config_form(data: dict, key_prefix: str = "mg") -> dict:
    """Render the full MS Graph config.yaml form."""
    result = {}

    result["server"] = render_server_section(data, key_prefix)
    result["general"] = render_general_section(data, key_prefix)
    result["logging"] = render_logging_section(data, key_prefix)
    result["artifacts"] = render_artifacts_section(data, key_prefix)

    # MS Graph-specific section
    st.markdown("##### Microsoft Graph Settings")
    mg = data.get("msgraph", {})
    col1, col2 = st.columns(2)
    with col1:
        ssl_verification = st.selectbox(
            "SSL Verification",
            options=["disabled", "ca_bundle"],
            index=0 if mg.get("ssl_verification", "disabled") == "disabled" else 1,
            key=f"{key_prefix}_ssl",
            help="'ca_bundle' uses certs from env vars; 'disabled' skips verification",
        )
    with col2:
        time_zone = st.text_input(
            "Time Zone",
            value=mg.get("time_zone", "America/New_York"),
            key=f"{key_prefix}_tz",
            help="IANA time zone for notifications",
        )

    result["msgraph"] = {"ssl_verification": ssl_verification, "time_zone": time_zone}

    # SharePoint section
    st.markdown("##### SharePoint Settings")
    sp = data.get("sharepoint", {})
    col1, col2 = st.columns(2)
    with col1:
        site_id = st.text_input("Site ID", value=sp.get("site_id", ""), key=f"{key_prefix}_site_id", help="SharePoint site identifier")
        drive_id = st.text_input("Drive ID", value=sp.get("drive_id", ""), key=f"{key_prefix}_drive_id", help="Document library identifier")
    with col2:
        subfolders_text = st.text_input("Include Subfolders (comma-separated)", value=", ".join(sp.get("include_subfolders", [])), key=f"{key_prefix}_subfolders")
        extensions_text = st.text_input("Include Extensions (comma-separated)", value=", ".join(sp.get("include_extensions", [])), key=f"{key_prefix}_extensions")

    result["sharepoint"] = {
        "site_id": site_id,
        "drive_id": drive_id,
        "include_subfolders": [s.strip() for s in subfolders_text.split(",") if s.strip()],
        "include_extensions": [s.strip() for s in extensions_text.split(",") if s.strip()],
    }

    # Teams section
    st.markdown("##### Microsoft Teams Settings")
    teams = data.get("teams", {})
    col1, col2 = st.columns(2)
    with col1:
        team_id = st.text_input("Team ID", value=teams.get("team_id", ""), key=f"{key_prefix}_team_id")
        channel_id = st.text_input("Channel ID", value=teams.get("channel_id", ""), key=f"{key_prefix}_channel_id")
    with col2:
        notify_start = st.toggle("Notify on Test Start", value=teams.get("notify_on_start", True), key=f"{key_prefix}_notify_start")
        notify_complete = st.toggle("Notify on Test Complete", value=teams.get("notify_on_complete", True), key=f"{key_prefix}_notify_complete")
        notify_failure = st.toggle("Notify on Failure", value=teams.get("notify_on_failure", True), key=f"{key_prefix}_notify_failure")

    result["teams"] = {
        "team_id": team_id,
        "channel_id": channel_id,
        "notify_on_start": notify_start,
        "notify_on_complete": notify_complete,
        "notify_on_failure": notify_failure,
    }

    return result
