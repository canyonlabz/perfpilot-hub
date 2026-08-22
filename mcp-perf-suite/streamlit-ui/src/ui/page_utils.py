"""
Session state initialization and shared utility functions.

Centralizes all session state setup to ensure consistent defaults
across the application. Called once at app startup.
"""
from pathlib import Path
import streamlit as st

from src.utils.state import (
    CONFIG_SELECTED_SERVER,
    CONFIG_EDIT_MODE,
    CONFIG_UNSAVED_CHANGES,
    MIGRATION_SOURCE_PATH,
    MIGRATION_DEST_PATH,
    MIGRATION_SCAN_RESULTS,
    MIGRATION_SELECTED_FILES,
    KPI_SELECTED_RUN_ID,
    KPI_LOADED_DATA,
    KPI_ACTIVE_TAB,
    EditMode,
    MigrationStatus,
)


# === Static Asset Helpers ===

def get_logo_path() -> Path:
    """
    Resolve the path to the header logo image.

    Assumes repo layout:
    streamlit-ui/
      app.py
      src/ui/static/floating-robot.jpg
    """
    # This file lives at src/ui/page_utils.py → parent is ui, parent.parent is src
    this_dir = Path(__file__).resolve().parent
    project_root = this_dir.parent.parent  # .../streamlit-ui
    return project_root / "src" / "ui" / "static" / "floating-robot.jpg"


@st.cache_resource
def get_logo_bytes() -> bytes:
    """Return the logo image as bytes (cached)."""
    return get_logo_path().read_bytes()

# === Session State Initialization ===

def initialize_session_state():
    """Initialize all session state variables with defaults."""

    # ── General ──
    if "session_initialized" not in st.session_state:
        st.session_state.session_initialized = True

    # ── Config Editor ──
    if CONFIG_SELECTED_SERVER not in st.session_state:
        st.session_state[CONFIG_SELECTED_SERVER] = "BlazeMeter"

    if CONFIG_EDIT_MODE not in st.session_state:
        st.session_state[CONFIG_EDIT_MODE] = EditMode.FORM.value

    if CONFIG_UNSAVED_CHANGES not in st.session_state:
        st.session_state[CONFIG_UNSAVED_CHANGES] = False

    # ── Config Migration ──
    if MIGRATION_SOURCE_PATH not in st.session_state:
        st.session_state[MIGRATION_SOURCE_PATH] = ""

    if MIGRATION_DEST_PATH not in st.session_state:
        st.session_state[MIGRATION_DEST_PATH] = ""

    if MIGRATION_SCAN_RESULTS not in st.session_state:
        st.session_state[MIGRATION_SCAN_RESULTS] = None

    if MIGRATION_SELECTED_FILES not in st.session_state:
        st.session_state[MIGRATION_SELECTED_FILES] = {}

    if "migration_status" not in st.session_state:
        st.session_state.migration_status = MigrationStatus.IDLE.value

    # ── KPI Dashboard ──
    if KPI_SELECTED_RUN_ID not in st.session_state:
        st.session_state[KPI_SELECTED_RUN_ID] = None

    if KPI_LOADED_DATA not in st.session_state:
        st.session_state[KPI_LOADED_DATA] = {}

    if KPI_ACTIVE_TAB not in st.session_state:
        st.session_state[KPI_ACTIVE_TAB] = "Summary"


def render_page_title(title: str, subtitle: str = ""):
    """Render a centered page title with optional subtitle."""
    from src.ui.page_styles import inject_page_title_styles
    inject_page_title_styles()

    st.markdown(f'<div class="page-title">{title}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(
            f'<div class="page-subtitle">{subtitle}</div>',
            unsafe_allow_html=True,
        )
