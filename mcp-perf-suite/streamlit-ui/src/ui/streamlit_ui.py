"""
MCP Performance Suite - Root Streamlit Application

Uses st.navigation() for multi-page routing with four pages:
- Home: Landing page with overview
- Config Editor: YAML configuration management
- Config Migration: Cross-repo config migration tool
- KPI Dashboard: Interactive performance metrics viewer
"""

import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import streamlit as st

from src.utils.config import load_config
from src.ui.page_utils import initialize_session_state


# --- Load Configuration ---
module_config = load_config()

# --- Initialize Session State ---
initialize_session_state()


# --- Render UI ---
def render_ui():
    """
    Renders the Streamlit UI for the MCP Performance Suite.
    """
    st.set_page_config(
        page_title=module_config.get("ui", {}).get("page_title", "MCP Performance Suite"),
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    # Define pages
    page_home = st.Page("nav_pages/page_homepage.py", title="Home", icon="🏠")
    page_config_editor = st.Page("nav_pages/page_config_editor.py", title="Config Editor", icon="⚙️")
    page_config_migrate = st.Page("nav_pages/page_config_migrate.py", title="Config Migration", icon="🔁")
    page_kpi_dashboard = st.Page("nav_pages/page_kpi_dashboard.py", title="KPI Dashboard", icon="📊")

    # Set up navigation
    pg = st.navigation(
        pages=[page_home, page_config_editor, page_config_migrate, page_kpi_dashboard],
        position="sidebar",
    )
    pg.run()


if __name__ == "__main__":
    render_ui()
