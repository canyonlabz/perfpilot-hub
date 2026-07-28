"""
Home Page - Landing page with application overview and quick navigation.
"""

import streamlit as st

from src.ui.page_header import render_page_header
from src.ui.page_utils import render_page_title
from src.utils.config import load_config
from src.utils.path_utils import get_mcp_suite_root, get_artifacts_path
from src.utils.state import MCP_SERVERS


def render_ui():
    render_page_header()
    render_page_title("MCP Performance Suite", "Unified configuration, analysis, and reporting for performance testing.")

    config = load_config()
    mcp_root = get_mcp_suite_root()
    artifacts_path = get_artifacts_path(config)

    # ── Quick Status ──
    st.markdown("---")

    col1, col2, col3 = st.columns(3, border=True)

    with col1:
        st.markdown("#### Configuration")
        st.markdown(f"**MCP Suite Root:** `{mcp_root}`")
        # Count configured servers (those with config.yaml, not just example)
        configured = 0
        for server_info in MCP_SERVERS.values():
            config_path = mcp_root / server_info["directory"] / "config.yaml"
            if config_path.exists():
                configured += 1
        st.metric("Servers Configured", f"{configured} / {len(MCP_SERVERS)}")

    with col2:
        st.markdown("#### Artifacts")
        st.markdown(f"**Artifacts Path:** `{artifacts_path}`")
        # Count available test runs
        run_count = 0
        if artifacts_path.exists():
            run_count = len([
                d for d in artifacts_path.iterdir()
                if d.is_dir() and d.name not in ("comparisons", "_ARCHIVE")
            ])
        st.metric("Test Runs Available", run_count)

    with col3:
        st.markdown("#### Quick Actions")
        st.page_link(
            "nav_pages/page_config_editor.py",
            label="Open Config Editor",
            icon=":material/settings:",
        )
        st.page_link(
            "nav_pages/page_kpi_dashboard.py",
            label="Open KPI Dashboard",
            icon=":material/monitoring:",
        )
        st.page_link(
            "nav_pages/page_config_migrate.py",
            label="Open Config Migration",
            icon=":material/sync:",
        )

    # ── MCP Server Overview ──
    st.markdown("---")
    st.markdown("### MCP Server Overview")

    for server_name, server_info in MCP_SERVERS.items():
        server_dir = mcp_root / server_info["directory"]
        has_config = (server_dir / "config.yaml").exists()
        has_example = (server_dir / "config.example.yaml").exists()

        status_icon = ":material/check_circle:" if has_config else ":material/warning:"
        status_text = "Configured" if has_config else "Not configured (example available)" if has_example else "Missing"

        with st.expander(f"{status_icon} **{server_name}** - {server_info['description']}", expanded=False):
            st.markdown(f"**Directory:** `{server_dir}`")
            st.markdown(f"**Status:** {status_text}")
            st.markdown(f"**Config files:** {', '.join(f'`{f}`' for f in server_info['config_files'])}")


render_ui()
