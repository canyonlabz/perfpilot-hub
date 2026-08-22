"""
utils/revision_utils.py
Shared utilities for the report revision workflow.

This module provides helper functions for:
- Path construction for revision files and folders
- Version detection and management for iterative revisions (HITL)
- Validation of report types and section IDs
- File naming with version suffixes
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.config import load_config, load_revisable_sections_config


# Load artifacts path from config
CONFIG = load_config()
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', './artifacts'))


# -----------------------------------------------
# Path Helper Functions
# -----------------------------------------------

def get_artifacts_base_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the base artifacts path for a given run_id and report type.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the artifacts base directory.
        - single_run: artifacts/{run_id}/
        - comparison: artifacts/comparisons/{run_id}/
    
    Example:
        >>> path = get_artifacts_base_path("80247571", "single_run")
        >>> print(path)
        artifacts/80247571
    """
    if report_type == "comparison":
        return ARTIFACTS_PATH / "comparisons" / run_id
    else:
        return ARTIFACTS_PATH / run_id


def get_reports_folder_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the reports folder path for a given run_id and report type.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the reports folder.
        - single_run: artifacts/{run_id}/reports/
        - comparison: artifacts/comparisons/{run_id}/ (reports are in root)
    """
    base_path = get_artifacts_base_path(run_id, report_type)
    if report_type == "comparison":
        # Comparison reports are stored in the root of the comparison folder
        return base_path
    else:
        return base_path / "reports"


def get_revisions_folder_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the revisions subfolder path for storing AI revision files.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the revisions subfolder.
        - single_run: artifacts/{run_id}/reports/revisions/
        - comparison: artifacts/comparisons/{run_id}/revisions/
    
    Example:
        >>> path = get_revisions_folder_path("80247571", "single_run")
        >>> print(path)
        artifacts/80247571/reports/revisions
    """
    reports_path = get_reports_folder_path(run_id, report_type)
    return reports_path / "revisions"


def ensure_revisions_folder_exists(run_id: str, report_type: str = "single_run") -> Path:
    """
    Ensure the revisions folder exists, creating it if necessary.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the revisions folder (guaranteed to exist).
    """
    revisions_path = get_revisions_folder_path(run_id, report_type)
    revisions_path.mkdir(parents=True, exist_ok=True)
    return revisions_path


# -----------------------------------------------
# Version Detection Functions
# -----------------------------------------------

def get_existing_revision_versions(
    run_id: str, 
    section_id: str, 
    report_type: str = "single_run"
) -> List[int]:
    """
    Find all existing revision versions for a specific section.
    
    Scans the revisions folder for files matching the pattern:
    {output_file}_v{N}.md (e.g., AI_EXECUTIVE_SUMMARY_v1.md)
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier (e.g., "executive_summary").
        report_type: "single_run" or "comparison".
    
    Returns:
        List of version numbers found (e.g., [1, 2, 3]), sorted ascending.
        Empty list if no revisions exist.
    
    Example:
        >>> versions = get_existing_revision_versions("80247571", "executive_summary")
        >>> print(versions)
        [1, 2]  # Two revisions exist: _v1.md and _v2.md
    """
    revisions_path = get_revisions_folder_path(run_id, report_type)
    
    if not revisions_path.exists():
        return []
    
    # Get the output_file base name from config
    section_config = _get_section_config_safe(report_type, section_id)
    if not section_config:
        return []
    
    output_file_base = section_config.get("output_file", "")
    if not output_file_base:
        return []
    
    # Pattern: {output_file}_v{N}.md
    pattern = re.compile(rf"^{re.escape(output_file_base)}_v(\d+)\.md$")
    
    versions = []
    for file_path in revisions_path.glob("*.md"):
        match = pattern.match(file_path.name)
        if match:
            versions.append(int(match.group(1)))
    
    return sorted(versions)


def get_next_revision_version(
    run_id: str, 
    section_id: str, 
    report_type: str = "single_run"
) -> int:
    """
    Determine the next revision version number for a section.
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier (e.g., "executive_summary").
        report_type: "single_run" or "comparison".
    
    Returns:
        Next version number (1 if no revisions exist, otherwise max + 1).
    
    Example:
        >>> # If v1 and v2 exist:
        >>> next_ver = get_next_revision_version("80247571", "executive_summary")
        >>> print(next_ver)
        3
    """
    existing = get_existing_revision_versions(run_id, section_id, report_type)
    if not existing:
        return 1
    return max(existing) + 1


def get_latest_revision_version(
    run_id: str, 
    section_id: str, 
    report_type: str = "single_run"
) -> Optional[int]:
    """
    Get the latest (highest) revision version for a section.
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier (e.g., "executive_summary").
        report_type: "single_run" or "comparison".
    
    Returns:
        Latest version number, or None if no revisions exist.
    
    Example:
        >>> latest = get_latest_revision_version("80247571", "executive_summary")
        >>> print(latest)
        2  # v2 is the latest
    """
    existing = get_existing_revision_versions(run_id, section_id, report_type)
    if not existing:
        return None
    return max(existing)


# -----------------------------------------------
# File Naming Functions
# -----------------------------------------------

def construct_revision_filename(
    section_id: str, 
    version: int, 
    report_type: str = "single_run"
) -> str:
    """
    Construct a revision filename with version suffix.
    
    Args:
        section_id: Section identifier (e.g., "executive_summary").
        version: Version number (e.g., 1, 2, 3).
        report_type: "single_run" or "comparison".
    
    Returns:
        Filename string (e.g., "AI_EXECUTIVE_SUMMARY_v1.md").
    
    Example:
        >>> filename = construct_revision_filename("executive_summary", 2)
        >>> print(filename)
        AI_EXECUTIVE_SUMMARY_v2.md
    """
    section_config = _get_section_config_safe(report_type, section_id)
    if not section_config:
        # Fallback: construct from section_id
        output_file_base = f"AI_{section_id.upper()}"
    else:
        output_file_base = section_config.get("output_file", f"AI_{section_id.upper()}")
    
    return f"{output_file_base}_v{version}.md"


def get_revision_file_path(
    run_id: str, 
    section_id: str, 
    version: int, 
    report_type: str = "single_run"
) -> Path:
    """
    Get the full path to a specific revision file.
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier (e.g., "executive_summary").
        version: Version number (e.g., 1, 2, 3).
        report_type: "single_run" or "comparison".
    
    Returns:
        Full path to the revision file.
    
    Example:
        >>> path = get_revision_file_path("80247571", "executive_summary", 1)
        >>> print(path)
        artifacts/80247571/reports/revisions/AI_EXECUTIVE_SUMMARY_v1.md
    """
    revisions_path = get_revisions_folder_path(run_id, report_type)
    filename = construct_revision_filename(section_id, version, report_type)
    return revisions_path / filename


# -----------------------------------------------
# Validation Functions
# -----------------------------------------------

def validate_report_type(report_type: str) -> Tuple[bool, Optional[str]]:
    """
    Validate the report_type parameter.
    
    Args:
        report_type: Value to validate.
    
    Returns:
        Tuple of (is_valid, error_message).
        - (True, None) if valid
        - (False, "error message") if invalid
    
    Example:
        >>> is_valid, error = validate_report_type("single_run")
        >>> print(is_valid, error)
        True None
        
        >>> is_valid, error = validate_report_type("invalid")
        >>> print(is_valid)
        False
    """
    valid_types = ["single_run", "comparison"]
    if report_type not in valid_types:
        return False, f"Invalid report_type: '{report_type}'. Must be one of: {valid_types}"
    return True, None


def validate_section_id(section_id: str, report_type: str = "single_run") -> Tuple[bool, Optional[str]]:
    """
    Validate that a section_id exists in the configuration for the given report type.
    
    Args:
        section_id: Section identifier to validate.
        report_type: "single_run" or "comparison".
    
    Returns:
        Tuple of (is_valid, error_message).
        - (True, None) if valid
        - (False, "error message") if invalid
    
    Example:
        >>> is_valid, error = validate_section_id("executive_summary", "single_run")
        >>> print(is_valid)
        True
        
        >>> is_valid, error = validate_section_id("nonexistent", "single_run")
        >>> print(is_valid)
        False
    """
    # First validate report_type
    is_valid, error = validate_report_type(report_type)
    if not is_valid:
        return False, error
    
    # Get sections for this report type
    sections = load_revisable_sections_config(report_type)
    
    if section_id not in sections:
        available = list(sections.keys())
        return False, f"Invalid section_id: '{section_id}'. Available sections for {report_type}: {available}"
    
    return True, None


def validate_revision_exists(
    run_id: str, 
    section_id: str, 
    version: int, 
    report_type: str = "single_run"
) -> Tuple[bool, Optional[str]]:
    """
    Validate that a specific revision file exists.
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier.
        version: Version number to check.
        report_type: "single_run" or "comparison".
    
    Returns:
        Tuple of (exists, error_message).
        - (True, None) if file exists
        - (False, "error message") if file doesn't exist
    """
    file_path = get_revision_file_path(run_id, section_id, version, report_type)
    
    if not file_path.exists():
        return False, f"Revision file not found: {file_path}"
    
    return True, None


# -----------------------------------------------
# Report File Path Functions
# -----------------------------------------------

def get_original_report_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the path to the original performance report.
    
    Args:
        run_id: Test run ID or comparison_id.
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the original report file.
    """
    reports_path = get_reports_folder_path(run_id, report_type)
    
    if report_type == "comparison":
        # Comparison reports have different naming: comparison_report_{run_ids}.md
        # We need to find the file that matches the pattern
        pattern = "comparison_report_*.md"
        matches = list(reports_path.glob(pattern))
        if matches:
            return matches[0]
        return reports_path / f"comparison_report_{run_id}.md"
    else:
        return reports_path / f"performance_report_{run_id}.md"


def get_backup_report_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the path for the backup of the original report (*_original.md).
    
    Args:
        run_id: Test run ID or comparison_id.
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the backup report file.
    """
    original_path = get_original_report_path(run_id, report_type)
    # Insert _original before .md extension
    stem = original_path.stem
    return original_path.parent / f"{stem}_original.md"


def get_revised_report_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the path for the revised report (*_revised.md).
    
    Args:
        run_id: Test run ID or comparison_id.
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the revised report file.
    """
    original_path = get_original_report_path(run_id, report_type)
    stem = original_path.stem
    return original_path.parent / f"{stem}_revised.md"


def get_metadata_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the path to the report metadata JSON file.
    
    Args:
        run_id: Test run ID or comparison_id.
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the metadata JSON file.
    """
    reports_path = get_reports_folder_path(run_id, report_type)
    
    if report_type == "comparison":
        # Comparison: comparison_metadata_{run_ids}.json
        pattern = "comparison_metadata_*.json"
        matches = list(reports_path.glob(pattern))
        if matches:
            return matches[0]
        return reports_path / f"comparison_metadata_{run_id}.json"
    else:
        return reports_path / f"report_metadata_{run_id}.json"


def get_backup_metadata_path(run_id: str, report_type: str = "single_run") -> Path:
    """
    Get the path for the backup of the metadata file (*_original.json).
    
    Args:
        run_id: Test run ID or comparison_id.
        report_type: "single_run" or "comparison".
    
    Returns:
        Path to the backup metadata file.
    """
    metadata_path = get_metadata_path(run_id, report_type)
    stem = metadata_path.stem
    return metadata_path.parent / f"{stem}_original.json"


# -----------------------------------------------
# Internal Helper Functions
# -----------------------------------------------

def _get_section_config_safe(report_type: str, section_id: str) -> Dict:
    """
    Safely get section configuration, returning empty dict if not found.
    
    This is an internal helper that doesn't raise exceptions.
    """
    try:
        sections = load_revisable_sections_config(report_type)
        return sections.get(section_id, {})
    except (ValueError, Exception):
        return {}


# -----------------------------------------------
# Test Block
# -----------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("Testing revision_utils.py functions")
    print("=" * 60)
    
    test_run_id = "80247571"
    test_section_id = "executive_summary"
    
    # Test path functions
    print("\n1. Path Helper Functions:")
    print(f"   Artifacts base path: {get_artifacts_base_path(test_run_id)}")
    print(f"   Reports folder: {get_reports_folder_path(test_run_id)}")
    print(f"   Revisions folder: {get_revisions_folder_path(test_run_id)}")
    
    # Test for comparison type
    print("\n2. Comparison Path Functions:")
    print(f"   Artifacts base path: {get_artifacts_base_path('2026-01-30', 'comparison')}")
    print(f"   Revisions folder: {get_revisions_folder_path('2026-01-30', 'comparison')}")
    
    # Test filename construction
    print("\n3. Filename Construction:")
    print(f"   Version 1: {construct_revision_filename(test_section_id, 1)}")
    print(f"   Version 2: {construct_revision_filename(test_section_id, 2)}")
    print(f"   Full path v1: {get_revision_file_path(test_run_id, test_section_id, 1)}")
    
    # Test validation
    print("\n4. Validation Functions:")
    is_valid, error = validate_report_type("single_run")
    print(f"   validate_report_type('single_run'): {is_valid}")
    is_valid, error = validate_report_type("invalid")
    print(f"   validate_report_type('invalid'): {is_valid}, error: {error}")
    
    is_valid, error = validate_section_id("executive_summary", "single_run")
    print(f"   validate_section_id('executive_summary'): {is_valid}")
    is_valid, error = validate_section_id("nonexistent", "single_run")
    print(f"   validate_section_id('nonexistent'): {is_valid}")
    
    # Test report path functions
    print("\n5. Report Path Functions:")
    print(f"   Original report: {get_original_report_path(test_run_id)}")
    print(f"   Backup report: {get_backup_report_path(test_run_id)}")
    print(f"   Revised report: {get_revised_report_path(test_run_id)}")
    print(f"   Metadata: {get_metadata_path(test_run_id)}")
    print(f"   Backup metadata: {get_backup_metadata_path(test_run_id)}")
    
    print("\n" + "=" * 60)
