"""
file_utils.py

This module contains utility functions for file operations.
End-users and developers can extend or modify these functions independently
of the core JMeter component utilities.
"""
import csv
import fnmatch
import re
import xml.etree.ElementTree as ET
import datetime
import json
import os
import urllib.parse
from typing import Any, Dict, List
from xml.dom import minidom

_INVALID_XML_CHARS = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]'
)

from utils.config import load_config

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]

def get_jmeter_artifacts_dir(run_id: str) -> str:
    """
    Returns the absolute directory path where JMeter artifacts
    (JMX, JTL, logs, etc.) should be stored for a given run_id.

    Final layout:
      artifacts/<run_id>/jmeter/
    """
    output_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def get_jmeter_analysis_dir(run_id: str) -> str:
    """
    Returns the absolute directory path for JMeter analysis output files.
    Creates the directory if it does not exist.

    Final layout:
      artifacts/<run_id>/jmeter/analysis/

    Args:
        run_id: Test run identifier.

    Returns:
        Absolute path to the JMeter analysis output directory.
    """
    output_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter", "analysis")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def rotate_analysis_files(
    directory: str,
    prefix: str,
    max_count: int,
) -> List[str]:
    """
    Prune oldest versioned analysis files when count exceeds max_count.

    Matches files whose name starts with `prefix` (e.g. "jmx_structure_")
    inside `directory`. Files are sorted by modification time (oldest first)
    and the oldest are deleted until at most `max_count` remain.

    Args:
        directory: Absolute path to the analysis directory.
        prefix: Filename prefix to match (e.g. "jmx_structure_").
        max_count: Maximum number of files to retain.

    Returns:
        List of absolute paths that were deleted (empty if none pruned).
    """
    if not os.path.isdir(directory) or max_count < 1:
        return []

    matched = []
    for filename in os.listdir(directory):
        if filename.startswith(prefix):
            full_path = os.path.join(directory, filename)
            if os.path.isfile(full_path):
                matched.append(full_path)

    matched.sort(key=lambda p: os.path.getmtime(p))

    deleted: List[str] = []
    while len(matched) > max_count:
        oldest = matched.pop(0)
        os.remove(oldest)
        deleted.append(oldest)

    return deleted


def save_jmx_file(root_element: ET.Element, run_id: str) -> str:
    """
    Saves the given XML tree (root_element) as a pretty-printed JMX file
    for the given run_id.

    The file will be stored under:
      artifacts/<run_id>/jmeter/

    The filename will include a timestamp for uniqueness:
      ai-generated_script_<timestamp>.jmx

    Returns the full output file path.
    """
    output_dir = get_jmeter_artifacts_dir(run_id)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(output_dir, f"ai-generated_script_{timestamp}.jmx")

    xml_bytes = ET.tostring(root_element, encoding="utf-8")
    xml_cleaned = _INVALID_XML_CHARS.sub('', xml_bytes.decode("utf-8"))
    pretty_xml = minidom.parseString(xml_cleaned.encode("utf-8")).toprettyxml(indent="  ")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(pretty_xml)

    return output_file

def save_correlation_spec(run_id: str, correlation_spec: dict) -> str:
    """
    Saves the correlation specification JSON for a given run_id.

    The file will be stored under:
        artifacts/<run_id>/jmeter/correlation_spec.json

    Returns the full output file path.
    """
    output_dir = get_jmeter_artifacts_dir(run_id)
    output_file = os.path.join(output_dir, "correlation_spec.json")

    # Ensure directory exists (get_jmeter_artifacts_dir already does this)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(correlation_spec, f, indent=2)

    return output_file


# ============================================================
# Analysis Output Helpers
# ============================================================

def get_analysis_output_dir(run_id: str) -> str:
    """
    Returns the absolute directory path for analysis output files.
    Creates the directory if it does not exist.

    Final layout:
      artifacts/<run_id>/analysis/

    Args:
        run_id: Test run identifier.

    Returns:
        Absolute path to the analysis output directory.
    """
    output_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "analysis")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def get_source_artifacts_dir(run_id: str, source: str) -> str:
    """
    Returns the absolute directory path for a given source's artifacts.
    Does NOT create the directory (it should already exist from the test run).

    Final layout:
      artifacts/<run_id>/<source>/

    Args:
        run_id: Test run identifier.
        source: Source folder name (e.g., "jmeter" or "blazemeter").

    Returns:
        Absolute path to the source artifacts directory.
    """
    return os.path.join(ARTIFACTS_PATH, str(run_id), source)


def discover_files_by_extension(directory: str, extension: str) -> List[str]:
    """
    Find all files with the given extension in a directory.
    Does NOT recurse into subdirectories.

    Args:
        directory: Absolute path to search.
        extension: File extension including dot (e.g., ".log", ".jtl", ".csv").

    Returns:
        List of absolute file paths, sorted by modification time (oldest first).
        Empty list if directory doesn't exist or no files match.
    """
    if not os.path.isdir(directory):
        return []

    ext_lower = extension.lower()
    matched = []
    for filename in os.listdir(directory):
        if filename.lower().endswith(ext_lower):
            full_path = os.path.join(directory, filename)
            if os.path.isfile(full_path):
                matched.append(full_path)

    # Sort by modification time, oldest first
    matched.sort(key=lambda p: os.path.getmtime(p))
    return matched


def save_csv_file(
    file_path: str,
    fieldnames: List[str],
    rows: List[Dict[str, Any]]
) -> str:
    """
    Write a list of dicts as CSV using DictWriter.
    Uses standard encoding (utf-8) and newline="" per Python CSV best practices.

    Args:
        file_path: Absolute output path.
        fieldnames: List of column header names.
        rows: List of dicts, one per row.

    Returns:
        The file_path written to.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return file_path


def save_json_file(file_path: str, data: dict) -> str:
    """
    Write a dict as pretty-printed JSON (indent=2, utf-8).

    Args:
        file_path: Absolute output path.
        data: Dict to serialize.

    Returns:
        The file_path written to.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return file_path


def save_markdown_file(file_path: str, content: str) -> str:
    """
    Write a Markdown string to file (utf-8).

    Args:
        file_path: Absolute output path.
        content: Markdown content string.

    Returns:
        The file_path written to.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return file_path
