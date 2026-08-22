"""
Session state constants and enums for the Streamlit UI.

Centralizes all session state keys and default values to avoid
scattered magic strings throughout the codebase.
"""

from enum import Enum


# ---------------------------------------------------------------------------
# Session state key constants
# ---------------------------------------------------------------------------

# Config Editor
CONFIG_SELECTED_SERVER = "config_selected_server"
CONFIG_EDIT_MODE = "config_edit_mode"  # "form" or "raw"
CONFIG_UNSAVED_CHANGES = "config_unsaved_changes"

# Config Migration
MIGRATION_SOURCE_PATH = "migration_source_path"
MIGRATION_DEST_PATH = "migration_dest_path"
MIGRATION_SCAN_RESULTS = "migration_scan_results"
MIGRATION_SELECTED_FILES = "migration_selected_files"

# KPI Dashboard
KPI_SELECTED_RUN_ID = "kpi_selected_run_id"
KPI_LOADED_DATA = "kpi_loaded_data"
KPI_ACTIVE_TAB = "kpi_active_tab"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EditMode(Enum):
    """Config editor display mode."""
    FORM = "form"
    RAW = "raw"


class MigrationStatus(Enum):
    """Config migration workflow status."""
    IDLE = "idle"
    SCANNED = "scanned"
    PREVIEWED = "previewed"
    APPLIED = "applied"
    ERROR = "error"


# ---------------------------------------------------------------------------
# MCP Server registry (name -> directory -> config files)
# ---------------------------------------------------------------------------

MCP_SERVERS = {
    "BlazeMeter": {
        "directory": "blazemeter-mcp",
        "config_files": ["config.yaml", "workflow.yaml"],
        "description": "BlazeMeter API integration for cloud load testing",
    },
    "Datadog": {
        "directory": "datadog-mcp",
        "config_files": ["config.yaml", "environments.json", "custom_queries.json"],
        "description": "Datadog APM metrics, logs, and trace collection",
    },
    "JMeter": {
        "directory": "jmeter-mcp",
        "config_files": ["config.yaml", "jmeter_config.yaml"],
        "description": "JMeter script generation and local test execution",
    },
    "PerfAnalysis": {
        "directory": "perfanalysis-mcp",
        "config_files": ["config.yaml", "slas.yaml"],
        "description": "Performance analysis, SLA evaluation, and bottleneck detection",
    },
    "PerfReport": {
        "directory": "perfreport-mcp",
        "config_files": ["config.yaml", "report_config.yaml", "chart_schema.yaml", "chart_colors.yaml"],
        "description": "Report generation, charting, and AI-assisted revisions",
    },
    "Confluence": {
        "directory": "confluence-mcp",
        "config_files": ["config.yaml"],
        "description": "Confluence page creation and report publishing",
    },
    "MS Graph": {
        "directory": "msgraph-mcp",
        "config_files": ["config.yaml"],
        "description": "Microsoft Teams notifications and SharePoint integration",
    },
}
