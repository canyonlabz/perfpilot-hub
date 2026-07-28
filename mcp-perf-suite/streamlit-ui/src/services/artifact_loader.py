"""
Artifact Loader - Load analysis artifacts from the filesystem.

Reads JSON, CSV, and Markdown files from the artifacts/{run_id}/ directory.
Provides a clean interface that can later be swapped to a database backend.
"""

import json
from pathlib import Path
from typing import Optional

import pandas as pd

from src.utils.path_utils import get_artifacts_path


def get_run_path(run_id: str, config: Optional[dict] = None) -> Path:
    """Get the full path to a test run's artifact directory."""
    return get_artifacts_path(config) / run_id


def list_runs(config: Optional[dict] = None) -> list[str]:
    """
    List all available test run IDs.

    Returns:
        list[str]: Run IDs sorted descending (newest first).
    """
    artifacts_path = get_artifacts_path(config)
    if not artifacts_path.exists():
        return []

    runs = [
        d.name
        for d in artifacts_path.iterdir()
        if d.is_dir() and d.name not in ("comparisons", "_ARCHIVE")
    ]
    runs.sort(reverse=True)
    return runs


def load_json(run_id: str, relative_path: str, config: Optional[dict] = None) -> Optional[dict]:
    """
    Load a JSON artifact file.

    Args:
        run_id: Test run ID.
        relative_path: Path relative to the run directory (e.g., "analysis/performance_analysis.json").
        config: Optional UI config dict.

    Returns:
        dict or None if file doesn't exist or fails to parse.
    """
    file_path = _resolve_path_with_jmeter_fallback(get_run_path(run_id, config), relative_path)
    if file_path is None:
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_path_with_jmeter_fallback(run_path: Path, relative_path: str) -> Optional[Path]:
    """Try the requested path first, then fall back to jmeter/ if the path starts with blazemeter/."""
    candidate = run_path / relative_path
    if candidate.exists():
        return candidate
    if relative_path.startswith("blazemeter/"):
        fallback = run_path / relative_path.replace("blazemeter/", "jmeter/", 1)
        if fallback.exists():
            return fallback
    return None


def load_csv(run_id: str, relative_path: str, config: Optional[dict] = None) -> Optional[pd.DataFrame]:
    """
    Load a CSV artifact file as a DataFrame.

    Args:
        run_id: Test run ID.
        relative_path: Path relative to the run directory (e.g., "blazemeter/test-results.csv").
        config: Optional UI config dict.

    Returns:
        DataFrame or None if file doesn't exist or fails to parse.
    """
    file_path = _resolve_path_with_jmeter_fallback(get_run_path(run_id, config), relative_path)
    if file_path is None:
        return None

    try:
        return pd.read_csv(file_path)
    except (pd.errors.ParserError, OSError):
        return None


def load_markdown(run_id: str, relative_path: str, config: Optional[dict] = None) -> Optional[str]:
    """
    Load a Markdown artifact file as text.

    Args:
        run_id: Test run ID.
        relative_path: Path relative to the run directory.
        config: Optional UI config dict.

    Returns:
        str or None if file doesn't exist.
    """
    file_path = get_run_path(run_id, config) / relative_path
    if not file_path.exists():
        return None

    try:
        return file_path.read_text(encoding="utf-8")
    except OSError:
        return None


def check_data_availability(run_id: str, config: Optional[dict] = None) -> dict:
    """
    Check which data sources are available for a given test run.

    Returns:
        dict: Map of data source -> availability info.
    """
    run_path = get_run_path(run_id, config)

    bm_exists = (run_path / "blazemeter").exists()
    jm_exists = (run_path / "jmeter").exists()

    availability = {
        "blazemeter": {
            "available": bm_exists or jm_exists,
            "test_results": (
                (run_path / "blazemeter" / "test-results.csv").exists()
                or (run_path / "jmeter" / "test-results.csv").exists()
            ),
            "aggregate_report": (
                (run_path / "blazemeter" / "aggregate_performance_report.csv").exists()
                or (run_path / "jmeter" / "aggregate_performance_report.csv").exists()
            ),
        },
        "datadog": {
            "available": (run_path / "datadog").exists(),
            "host_metrics": bool(list((run_path / "datadog").glob("host_metrics_*.csv")))
            if (run_path / "datadog").exists() else False,
            "k8s_metrics": bool(list((run_path / "datadog").glob("k8s_metrics_*.csv")))
            if (run_path / "datadog").exists() else False,
            "logs": bool(list((run_path / "datadog").glob("logs_*.csv")))
            if (run_path / "datadog").exists() else False,
            "apm_traces": bool(list((run_path / "datadog").glob("apm_traces_*.csv")))
            if (run_path / "datadog").exists() else False,
        },
        "analysis": {
            "available": (run_path / "analysis").exists(),
            "performance": (run_path / "analysis" / "performance_analysis.json").exists(),
            "bottleneck": (run_path / "analysis" / "bottleneck_analysis.json").exists(),
            "correlation": (run_path / "analysis" / "correlation_analysis.json").exists(),
            "infrastructure": (run_path / "analysis" / "infrastructure_analysis.json").exists(),
            "log_analysis": (
                (run_path / "analysis" / "blazemeter_log_analysis.json").exists()
                or (run_path / "analysis" / "log_analysis.json").exists()
            ),
        },
        "reports": {
            "available": (run_path / "reports").exists(),
        },
        "charts": {
            "available": (run_path / "charts").exists(),
        },
    }

    return availability
