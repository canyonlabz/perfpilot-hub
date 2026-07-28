"""
Common Config Sections - Shared form fields across all MCP servers.

Renders the server, general, logging, and artifacts sections that
are present in every MCP server's config.yaml.
"""

import streamlit as st
from pathlib import Path


def render_server_section(data: dict, key_prefix: str) -> dict:
    """Render the 'server' section (read-only metadata)."""
    server = data.get("server", {})

    st.markdown("##### Server Metadata")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Server Name", value=server.get("name", ""), key=f"{key_prefix}_server_name", disabled=True)
        version = st.text_input("Version", value=server.get("version", ""), key=f"{key_prefix}_server_version", disabled=True)
    with col2:
        description = st.text_input("Description", value=server.get("description", ""), key=f"{key_prefix}_server_desc", disabled=True)
        build_date = st.text_input("Build Date", value=server.get("build", {}).get("date", ""), key=f"{key_prefix}_build_date", disabled=True)

    return {
        "name": name,
        "version": version,
        "description": description,
        "build": {"date": build_date},
    }


def render_general_section(data: dict, key_prefix: str) -> dict:
    """Render the 'general' section."""
    general = data.get("general", {})

    st.markdown("##### General Settings")
    col1, col2 = st.columns(2)
    with col1:
        enable_debug = st.toggle(
            "Enable Debug Mode",
            value=general.get("enable_debug", False),
            key=f"{key_prefix}_debug",
            help="Enable additional diagnostic output for troubleshooting",
        )
    with col2:
        enable_logging = st.toggle(
            "Enable Logging",
            value=general.get("enable_logging", True),
            key=f"{key_prefix}_logging",
            help="Enable/disable log file output",
        )

    return {"enable_debug": enable_debug, "enable_logging": enable_logging}


def render_logging_section(data: dict, key_prefix: str) -> dict:
    """Render the 'logging' section."""
    logging = data.get("logging", {})

    st.markdown("##### Logging Configuration")
    col1, col2 = st.columns(2)
    with col1:
        log_level = st.selectbox(
            "Log Level",
            options=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            index=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].index(
                logging.get("log_level", "INFO")
            ),
            key=f"{key_prefix}_log_level",
        )
        verbose_mode = st.toggle(
            "Verbose Mode",
            value=logging.get("verbose_mode", False),
            key=f"{key_prefix}_verbose",
            help="Enable verbose logging for detailed debugging output",
        )
    with col2:
        log_path = st.text_input(
            "Log Path",
            value=logging.get("log_path", ""),
            key=f"{key_prefix}_log_path",
            help="Directory where log files are written",
        )
        # Path existence check
        _show_path_status(log_path)

    return {"log_level": log_level, "verbose_mode": verbose_mode, "log_path": log_path}


def render_artifacts_section(data: dict, key_prefix: str) -> dict:
    """Render the 'artifacts' section."""
    artifacts = data.get("artifacts", {})

    st.markdown("##### Artifacts Path")
    artifacts_path = st.text_input(
        "Artifacts Path",
        value=artifacts.get("artifacts_path", ""),
        key=f"{key_prefix}_artifacts_path",
        help="Root directory for test run artifacts",
    )
    _show_path_status(artifacts_path)

    return {"artifacts_path": artifacts_path}


def _show_path_status(path_value: str):
    """Show a path existence status indicator."""
    if not path_value:
        return

    if "<" in path_value and ">" in path_value:
        st.caption(":material/warning: Contains placeholder - needs configuration")
    elif Path(path_value).exists():
        st.caption(":material/check_circle: Path exists")
    else:
        st.caption(":material/error: Path does not exist")
