"""
Config Editor Page - YAML configuration management for all MCP servers.

Provides form-based and raw YAML editing modes with validation,
save (with backup), and reset-to-example functionality.
"""

import streamlit as st

from src.ui.page_header import render_page_header
from src.ui.page_utils import render_page_title
from src.ui.page_styles import inject_config_editor_styles
from src.utils.state import (
    MCP_SERVERS,
    CONFIG_SELECTED_SERVER,
    CONFIG_EDIT_MODE,
    EditMode,
)
from src.services.config_manager import (
    load_config as load_yaml_config,
    load_config_raw,
    save_config_raw,
    validate_config,
    reset_to_example,
)
from src.utils.path_utils import get_mcp_suite_root

# Section form renderers
from src.ui.sections.config_blazemeter import render_blazemeter_config_form
from src.ui.sections.config_datadog import render_datadog_config_form
from src.ui.sections.config_jmeter import render_jmeter_config_form
from src.ui.sections.config_perfanalysis import render_perfanalysis_config_form
from src.ui.sections.config_perfreport import render_perfreport_config_form
from src.ui.sections.config_confluence import render_confluence_config_form
from src.ui.sections.config_msgraph import render_msgraph_config_form

# Map server names to their form renderer functions
FORM_RENDERERS = {
    "BlazeMeter": render_blazemeter_config_form,
    "Datadog": render_datadog_config_form,
    "JMeter": render_jmeter_config_form,
    "PerfAnalysis": render_perfanalysis_config_form,
    "PerfReport": render_perfreport_config_form,
    "Confluence": render_confluence_config_form,
    "MS Graph": render_msgraph_config_form,
}


def render_ui():
    render_page_header()
    render_page_title(
        "Configuration Editor",
        "Manage YAML configuration files for all MCP servers"
    )
    inject_config_editor_styles()

    mcp_root = get_mcp_suite_root()

    # ── Sidebar: Server Selector ──
    with st.sidebar:
        st.markdown("### Select MCP Server")
        selected_server = st.radio(
            "Server",
            options=list(MCP_SERVERS.keys()),
            index=list(MCP_SERVERS.keys()).index(
                st.session_state.get(CONFIG_SELECTED_SERVER, "BlazeMeter")
            ),
            key="server_radio",
            label_visibility="collapsed",
        )
        st.session_state[CONFIG_SELECTED_SERVER] = selected_server

        # Server info
        server_info = MCP_SERVERS[selected_server]
        st.markdown(f"*{server_info['description']}*")
        st.markdown(f"**Directory:** `{server_info['directory']}`")
        st.markdown(f"**Config files:** {len(server_info['config_files'])}")

    # ── Main Area ──
    server_info = MCP_SERVERS[selected_server]

    # Edit mode toggle
    col_mode, col_spacer = st.columns([0.3, 0.7])
    with col_mode:
        is_raw = st.toggle(
            "Raw YAML Mode",
            value=st.session_state.get(CONFIG_EDIT_MODE) == EditMode.RAW.value,
            key="edit_mode_toggle",
        )
        st.session_state[CONFIG_EDIT_MODE] = EditMode.RAW.value if is_raw else EditMode.FORM.value

    # Config file tabs
    config_files = server_info["config_files"]
    if len(config_files) > 1:
        tabs = st.tabs(config_files)
    else:
        tabs = [st.container()]

    for idx, config_file in enumerate(config_files):
        with tabs[idx]:
            _render_config_file(selected_server, server_info, config_file, mcp_root)

    # ── Action Buttons ──
    st.markdown("---")
    btn_col1, btn_col2, btn_col3, btn_spacer = st.columns([0.12, 0.14, 0.18, 0.56])

    with btn_col1:
        if st.button("Save", key="btn_save", use_container_width=True):
            _handle_save(selected_server, server_info, config_files, mcp_root)

    with btn_col2:
        if st.button("Validate", key="btn_validate", use_container_width=True):
            _handle_validate(selected_server, server_info, config_files, mcp_root)

    with btn_col3:
        if st.button("Reset to Example", key="btn_reset", use_container_width=True):
            _handle_reset(selected_server)


def _render_config_file(server_name: str, server_info: dict, config_file: str, mcp_root):
    """Render the editor for a single config file."""
    config_path = mcp_root / server_info["directory"] / config_file

    # Check for example file
    example_path = None
    if config_file == "config.yaml":
        example_path = mcp_root / server_info["directory"] / "config.example.yaml"

    # Determine which file to load
    file_to_load = None
    if config_path.exists():
        file_to_load = config_path
        st.success(f"`{config_file}` loaded from `{config_path}`")
    elif example_path and example_path.exists():
        file_to_load = example_path
        st.warning(
            f"`{config_file}` not found. Showing example from `{example_path}`. "
            "Edit and save to create your config."
        )
    else:
        st.error(f"Neither `{config_file}` nor example found at `{mcp_root / server_info['directory']}`")
        return

    edit_mode = st.session_state.get(CONFIG_EDIT_MODE, EditMode.FORM.value)

    if edit_mode == EditMode.RAW.value:
        # Raw YAML/JSON editor
        try:
            content = load_config_raw(file_to_load)
            st.text_area(
                f"Edit {config_file}",
                value=content,
                height=500,
                key=f"raw_{server_name}_{config_file}",
                label_visibility="collapsed",
            )
        except Exception as e:
            st.error(f"Error reading file: {e}")
    else:
        # Form mode
        if config_file == "config.yaml" or config_file.endswith(".yaml") and config_file in ["config.yaml"]:
            # Use structured form renderer for config.yaml
            try:
                data = load_yaml_config(file_to_load)
                renderer = FORM_RENDERERS.get(server_name)
                if renderer:
                    renderer(data, key_prefix=f"form_{server_name}")
                else:
                    st.info(f"No form renderer for {server_name}. Use Raw YAML mode.")
                    _show_readonly_preview(file_to_load)
            except Exception as e:
                st.error(f"Error loading config: {e}")
        else:
            # For non-config.yaml files (workflow.yaml, slas.yaml, etc.)
            # Show read-only preview with option to edit in raw mode
            st.info(f"Form editor for `{config_file}` - use Raw YAML mode for editing specialized configs.")
            _show_readonly_preview(file_to_load)


def _show_readonly_preview(file_path):
    """Show a read-only preview of a config file."""
    try:
        content = load_config_raw(file_path)
        lang = "yaml" if file_path.suffix in (".yaml", ".yml") else "json"
        with st.expander("File Preview (read-only)", expanded=True):
            st.code(content, language=lang)
    except Exception as e:
        st.error(f"Error reading file: {e}")


def _handle_save(server_name: str, server_info: dict, config_files: list, mcp_root):
    """Handle the Save button click."""
    edit_mode = st.session_state.get(CONFIG_EDIT_MODE, EditMode.FORM.value)

    if edit_mode == EditMode.RAW.value:
        # Save from raw text areas
        saved_count = 0
        for config_file in config_files:
            raw_key = f"raw_{server_name}_{config_file}"
            if raw_key in st.session_state:
                config_path = mcp_root / server_info["directory"] / config_file
                try:
                    save_config_raw(config_path, st.session_state[raw_key])
                    saved_count += 1
                except Exception as e:
                    st.error(f"Error saving `{config_file}`: {e}")

        if saved_count > 0:
            st.success(f"Saved {saved_count} config file(s) with backup (.bak)")
    else:
        st.info(
            "Form-mode save reconstructs the YAML from form values. "
            "For now, use Raw YAML mode for saving. Full form save coming soon."
        )


def _handle_validate(server_name: str, server_info: dict, config_files: list, mcp_root):
    """Handle the Validate button click."""
    for config_file in config_files:
        config_path = mcp_root / server_info["directory"] / config_file
        if not config_path.exists():
            st.warning(f"`{config_file}` does not exist - skipping validation")
            continue

        try:
            data = load_yaml_config(config_path)
            errors = validate_config(server_name, data)

            if errors:
                st.error(f"**{config_file}** - {len(errors)} issue(s) found:")
                for err in errors:
                    st.markdown(f"- {err}")
            else:
                st.success(f"**{config_file}** - Validation passed")

        except Exception as e:
            st.error(f"Error validating `{config_file}`: {e}")


def _handle_reset(server_name: str):
    """Handle the Reset to Example button click."""
    if reset_to_example(server_name):
        st.success(f"Config reset to example for {server_name}. Existing config backed up.")
        st.rerun()
    else:
        st.warning(f"No example config found for {server_name}.")


render_ui()
