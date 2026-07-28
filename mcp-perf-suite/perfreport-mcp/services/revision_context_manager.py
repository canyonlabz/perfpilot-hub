"""
services/revision_context_manager.py
Context management service for AI-assisted report revision.

This module implements the prepare_revision_context function that saves
AI-generated revised content to versioned markdown files, supporting
Human-In-The-Loop (HITL) iterative feedback workflows.
"""

from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

from utils.config import get_section_config
from utils.revision_utils import (
    validate_report_type,
    validate_section_id,
    ensure_revisions_folder_exists,
    get_next_revision_version,
    get_revision_file_path,
    get_existing_revision_versions,
    construct_revision_filename,
)


# -----------------------------------------------
# Main Context Management Function
# -----------------------------------------------

async def prepare_revision_context(
    run_id: str,
    section_id: str,
    revised_content: str,
    report_type: str = "single_run",
    additional_context: Optional[str] = None
) -> Dict:
    """
    Save AI-generated revised content to a versioned markdown file.
    
    Supports Human-In-The-Loop (HITL) workflows by automatically incrementing
    version numbers for each revision. Multiple revisions can be saved for the
    same section, allowing iterative refinement based on user feedback.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        section_id: Section identifier (e.g., "executive_summary", "key_observations").
        revised_content: AI-generated markdown content for the section.
        report_type: "single_run" (default) or "comparison".
        additional_context: Optional user-provided context that was used during revision
                           (stored in metadata for traceability).
    
    Returns:
        dict containing:
            - run_id: The test run or comparison ID
            - report_type: Type of report ("single_run" or "comparison")
            - section_id: The section that was revised
            - section_full_id: Composite identifier (e.g., "single_run.executive_summary")
            - revision_number: Version number assigned to this revision (1, 2, 3...)
            - revision_file: Filename of the saved revision
            - revision_path: Full path to the saved revision file
            - content_length: Character count of the saved content
            - additional_context: The context that was provided (for traceability)
            - previous_versions: List of existing version numbers before this save
            - saved_at: ISO timestamp of when the file was saved
            - status: "success" or "error"
            - error: Error message if status is "error"
    """
    try:
        # Validate report_type
        is_valid, error = validate_report_type(report_type)
        if not is_valid:
            return _error_response(run_id, report_type, section_id, error)
        
        # Validate section_id
        is_valid, error = validate_section_id(section_id, report_type)
        if not is_valid:
            return _error_response(run_id, report_type, section_id, error)
        
        # Validate revised_content is not empty
        if not revised_content or not revised_content.strip():
            return _error_response(
                run_id, report_type, section_id,
                "revised_content cannot be empty"
            )
        
        # Get section configuration for metadata
        section_config = get_section_config(report_type, section_id)
        
        # Get existing versions before saving
        existing_versions = get_existing_revision_versions(run_id, section_id, report_type)
        
        # Ensure revisions folder exists
        revisions_folder = ensure_revisions_folder_exists(run_id, report_type)
        
        # Get next version number
        version = get_next_revision_version(run_id, section_id, report_type)
        
        # Get file path for the new revision
        revision_path = get_revision_file_path(run_id, section_id, version, report_type)
        
        # Build file content with metadata header
        file_content = _build_revision_file_content(
            section_id=section_id,
            section_config=section_config,
            report_type=report_type,
            run_id=run_id,
            version=version,
            revised_content=revised_content,
            additional_context=additional_context
        )
        
        # Write the revision file
        revision_path.write_text(file_content, encoding='utf-8')
        
        # Build success response
        return {
            "run_id": run_id,
            "report_type": report_type,
            "section_id": section_id,
            "section_full_id": f"{report_type}.{section_id}",
            "revision_number": version,
            "revision_file": revision_path.name,
            "revision_path": str(revision_path),
            "content_length": len(revised_content),
            "additional_context": additional_context,
            "previous_versions": existing_versions,
            "total_versions": len(existing_versions) + 1,
            "saved_at": datetime.now().isoformat(),
            "status": "success"
        }
        
    except Exception as e:
        return _error_response(
            run_id, report_type, section_id,
            f"Failed to save revision: {str(e)}"
        )


# -----------------------------------------------
# Content Building Functions
# -----------------------------------------------

def _build_revision_file_content(
    section_id: str,
    section_config: Dict,
    report_type: str,
    run_id: str,
    version: int,
    revised_content: str,
    additional_context: Optional[str]
) -> str:
    """
    Build the complete revision file content with metadata header.
    
    The header provides traceability information including when and why
    the revision was created.
    """
    timestamp = datetime.now().isoformat()
    
    # Build metadata header
    header_lines = [
        "<!--",
        "REVISION METADATA",
        "=================",
        f"Section: {section_id}",
        f"Section Full ID: {report_type}.{section_id}",
        f"Report Type: {report_type}",
        f"Run ID: {run_id}",
        f"Version: {version}",
        f"Created: {timestamp}",
        f"Placeholder: {section_config.get('placeholder', 'N/A')}",
        f"AI Placeholder: {section_config.get('ai_placeholder', 'N/A')}",
    ]
    
    if additional_context:
        header_lines.append(f"Additional Context: {additional_context}")
    
    header_lines.extend([
        "-->",
        "",  # Empty line after header
    ])
    
    # Combine header and content
    header = "\n".join(header_lines)
    
    return header + revised_content


# -----------------------------------------------
# Helper Functions
# -----------------------------------------------

def _error_response(
    run_id: str,
    report_type: str,
    section_id: str,
    error_message: str
) -> Dict:
    """Build a standardized error response."""
    return {
        "run_id": run_id,
        "report_type": report_type,
        "section_id": section_id,
        "section_full_id": f"{report_type}.{section_id}" if section_id else None,
        "status": "error",
        "error": error_message
    }


# -----------------------------------------------
# Utility Functions for Revision Management
# -----------------------------------------------

async def get_revision_content(
    run_id: str,
    section_id: str,
    version: Optional[int] = None,
    report_type: str = "single_run"
) -> Dict:
    """
    Retrieve the content of a specific revision file.
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier.
        version: Specific version to retrieve. If None, returns latest.
        report_type: "single_run" or "comparison".
    
    Returns:
        dict containing revision content and metadata, or error.
    """
    try:
        # Validate inputs
        is_valid, error = validate_report_type(report_type)
        if not is_valid:
            return {"status": "error", "error": error}
        
        is_valid, error = validate_section_id(section_id, report_type)
        if not is_valid:
            return {"status": "error", "error": error}
        
        # Get existing versions
        existing_versions = get_existing_revision_versions(run_id, section_id, report_type)
        
        if not existing_versions:
            return {
                "status": "error",
                "error": f"No revisions found for section '{section_id}'"
            }
        
        # Determine which version to retrieve
        if version is None:
            version = max(existing_versions)
        elif version not in existing_versions:
            return {
                "status": "error",
                "error": f"Version {version} not found. Available: {existing_versions}"
            }
        
        # Get file path and read content
        revision_path = get_revision_file_path(run_id, section_id, version, report_type)
        
        if not revision_path.exists():
            return {
                "status": "error",
                "error": f"Revision file not found: {revision_path}"
            }
        
        content = revision_path.read_text(encoding='utf-8')
        
        # Strip metadata header if present
        content_without_header = _strip_metadata_header(content)
        
        return {
            "run_id": run_id,
            "report_type": report_type,
            "section_id": section_id,
            "section_full_id": f"{report_type}.{section_id}",
            "version": version,
            "revision_path": str(revision_path),
            "content": content_without_header,
            "content_with_header": content,
            "available_versions": existing_versions,
            "is_latest": version == max(existing_versions),
            "status": "success"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": f"Failed to read revision: {str(e)}"
        }


async def list_section_revisions(
    run_id: str,
    section_id: str,
    report_type: str = "single_run"
) -> Dict:
    """
    List all revisions for a specific section.
    
    Args:
        run_id: Test run ID or comparison_id.
        section_id: Section identifier.
        report_type: "single_run" or "comparison".
    
    Returns:
        dict containing list of revisions with metadata.
    """
    try:
        # Validate inputs
        is_valid, error = validate_report_type(report_type)
        if not is_valid:
            return {"status": "error", "error": error}
        
        is_valid, error = validate_section_id(section_id, report_type)
        if not is_valid:
            return {"status": "error", "error": error}
        
        # Get existing versions
        existing_versions = get_existing_revision_versions(run_id, section_id, report_type)
        
        if not existing_versions:
            return {
                "run_id": run_id,
                "report_type": report_type,
                "section_id": section_id,
                "section_full_id": f"{report_type}.{section_id}",
                "revisions": [],
                "total_count": 0,
                "status": "success"
            }
        
        # Build revision list with metadata
        revisions = []
        for version in sorted(existing_versions):
            revision_path = get_revision_file_path(run_id, section_id, version, report_type)
            
            revision_info = {
                "version": version,
                "filename": revision_path.name,
                "path": str(revision_path),
                "exists": revision_path.exists()
            }
            
            if revision_path.exists():
                stat = revision_path.stat()
                revision_info["size_bytes"] = stat.st_size
                revision_info["modified_at"] = datetime.fromtimestamp(
                    stat.st_mtime
                ).isoformat()
            
            revisions.append(revision_info)
        
        return {
            "run_id": run_id,
            "report_type": report_type,
            "section_id": section_id,
            "section_full_id": f"{report_type}.{section_id}",
            "revisions": revisions,
            "total_count": len(revisions),
            "latest_version": max(existing_versions),
            "status": "success"
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": f"Failed to list revisions: {str(e)}"
        }


def _strip_metadata_header(content: str) -> str:
    """
    Strip the metadata header from revision file content.
    
    Removes the HTML comment block at the beginning of the file that
    contains revision metadata.
    """
    if not content.startswith("<!--"):
        return content
    
    # Find the end of the metadata comment
    end_marker = "-->"
    end_pos = content.find(end_marker)
    
    if end_pos == -1:
        return content
    
    # Return content after the metadata header, stripping leading whitespace
    remaining = content[end_pos + len(end_marker):]
    return remaining.lstrip('\n')
