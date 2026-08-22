"""
Centralized CSS injection functions for the Streamlit UI.

Each function injects scoped CSS via st.markdown() with unsafe_allow_html=True.
Follows the pattern from llm-perf-studio for consistent, maintainable styling.
"""

import streamlit as st


def inject_page_header_styles():
    """Inject CSS for the shared page header (logo, nav tabs, session controls)."""
    st.markdown("""<style>
    /* Navigation tab links in header */
    a[data-testid="stPageLink-NavLink"] {
        background-color: transparent;
        border: 1.5px solid #3d6b8e;
        border-radius: 20px;
        padding: 6px 18px;
        color: #a0c4e0;
        font-weight: 500;
        font-size: 0.85rem;
        text-decoration: none;
        transition: all 0.2s ease;
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }

    a[data-testid="stPageLink-NavLink"]:hover {
        background-color: #1a3a52;
        border-color: #5a9bc7;
        color: #d0e8f5;
    }

    a[data-testid="stPageLink-NavLink"][aria-current="page"] {
        background-color: #1a3a52;
        border-color: #5a9bc7;
        color: #ffffff;
        font-weight: 600;
    }

    /* Clear Session and Exit App buttons */
    .st-key-clear_session button, .st-key-exit_app button {
        background-color: transparent;
        border: 1.5px solid #c95000;
        border-radius: 20px;
        color: #e8a06a;
        font-weight: 500;
        font-size: 0.78rem;
        padding: 4px 14px;
        white-space: nowrap;  /* Prevent text wrapping */
    }
    .st-key-clear_session button:hover, .st-key-exit_app button:hover {
        /* background-color: #3d1f00; */
        /* color: #ffb880; */
        background: #c95000;
        color: #fff;
    }
    </style>""", unsafe_allow_html=True)


def inject_page_title_styles():
    """Inject CSS for page title and subtitle."""
    st.markdown("""<style>
    .page-title {
        text-align: center;
        font-size: 2rem;
        font-weight: 800;
        font-family: 'Georgia', serif;
        margin-top: -30px !important;
        margin-bottom: 0.25rem;
        color: #26355E; /* Dark blue-gray */
    }
    .page-subtitle {
        text-align: center;
        font-size: 1.25rem;
        font-weight: 400;
        color: #3d2b1f; /* Dark brown */
        margin-bottom: 2rem;
    }
    </style>""", unsafe_allow_html=True)


def inject_config_editor_styles():
    """Inject CSS for the config editor page."""
    st.markdown("""<style>
    /* Server selector in sidebar */
    .config-server-label {
        font-size: 0.85rem;
        font-weight: 600;
        color: #a0c4e0;
        margin-bottom: 4px;
    }

    /* Save/Validate/Reset buttons */
    .st-key-btn_save button {
        background-color: #1a5c3a;
        border: 1px solid #2d8c5a;
        color: #c0f0d0;
        border-radius: 6px;
        font-weight: 600;
    }
    .st-key-btn_save button:hover {
        background-color: #237a4a;
    }

    .st-key-btn_validate button {
        background-color: #1a3a5c;
        border: 1px solid #2d6b8e;
        color: #a0d0f0;
        border-radius: 6px;
        font-weight: 600;
    }
    .st-key-btn_validate button:hover {
        background-color: #234a6a;
    }

    .st-key-btn_reset button {
        background-color: #5c3a1a;
        border: 1px solid #8e5c2d;
        color: #f0d0a0;
        border-radius: 6px;
        font-weight: 600;
    }
    .st-key-btn_reset button:hover {
        background-color: #6a4a23;
    }

    /* Path status indicators */
    .path-exists {
        color: #2ecc40;
        font-size: 0.8rem;
    }
    .path-missing {
        color: #ff4136;
        font-size: 0.8rem;
    }
    .path-placeholder {
        color: #ffdc00;
        font-size: 0.8rem;
    }
    </style>""", unsafe_allow_html=True)


def inject_kpi_dashboard_styles():
    """Inject CSS for the KPI dashboard page."""
    st.markdown("""<style>
    /* KPI metric cards */
    [data-testid="stMetricLabel"] {
        font-size: 0.85rem;
        font-weight: 600;
        color: #8ab4d0;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.4rem;
        font-weight: 700;
    }

    /* Custom data tables */
    .custom-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
    }
    .custom-table th {
        background-color: #1a2a3a;
        color: #a0c4e0;
        padding: 8px 12px;
        text-align: left;
        border-bottom: 2px solid #2d4a5e;
        font-weight: 600;
    }
    .custom-table td {
        padding: 6px 12px;
        border-bottom: 1px solid #1a2a3a;
        color: #d0e0f0;
    }
    .custom-table tr:hover {
        background-color: #0f1a25;
    }

    /* SLA pass/fail badges */
    .sla-pass {
        background-color: #1a3d1a;
        color: #2ecc40;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .sla-fail {
        background-color: #3d1a1a;
        color: #ff4136;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    /* Severity badges for bottleneck/log analysis */
    .severity-critical {
        background-color: #3d1a1a;
        color: #ff4136;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .severity-high {
        background-color: #3d2a1a;
        color: #ff851b;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .severity-medium {
        background-color: #3d3a1a;
        color: #ffdc00;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .severity-low {
        background-color: #1a2a3d;
        color: #7fdbff;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }

    /* Run selector dropdown */
    .run-selector-label {
        font-size: 0.9rem;
        font-weight: 600;
        color: #a0c4e0;
    }
    </style>""", unsafe_allow_html=True)


def inject_migration_styles():
    """Inject CSS for the config migration page."""
    st.markdown("""<style>
    /* Diff display */
    .diff-added {
        background-color: #1a3d1a;
        color: #2ecc40;
        padding: 2px 6px;
        font-family: monospace;
        font-size: 0.8rem;
    }
    .diff-removed {
        background-color: #3d1a1a;
        color: #ff4136;
        padding: 2px 6px;
        font-family: monospace;
        font-size: 0.8rem;
    }
    .diff-unchanged {
        color: #8090a0;
        padding: 2px 6px;
        font-family: monospace;
        font-size: 0.8rem;
    }

    /* Flagged field warning */
    .field-flagged {
        border-left: 3px solid #ffdc00;
        padding-left: 8px;
        margin: 4px 0;
    }

    /* Scan/Preview/Apply buttons */
    .st-key-btn_scan button {
        background-color: #1a3a5c;
        border: 1px solid #2d6b8e;
        color: #a0d0f0;
        border-radius: 6px;
        font-weight: 600;
    }
    .st-key-btn_preview button {
        background-color: #3a3a1a;
        border: 1px solid #6b6b2d;
        color: #e0e0a0;
        border-radius: 6px;
        font-weight: 600;
    }
    .st-key-btn_apply button {
        background-color: #1a5c3a;
        border: 1px solid #2d8c5a;
        color: #c0f0d0;
        border-radius: 6px;
        font-weight: 600;
    }
    </style>""", unsafe_allow_html=True)
