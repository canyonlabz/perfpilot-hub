# blazemeter-mcp/utils/file_utils.py
"""
File I/O helper functions for BlazeMeter MCP.
Handles writing JSON artifacts to the appropriate folder structure.
"""
import os
import json
from typing import Dict, Any

from utils.config import load_config

# Load configuration
config = load_config()
artifacts_base = config['artifacts']['artifacts_path']


def write_public_report_json(run_id: str, public_report_data: Dict[str, Any]) -> str:
    """
    Write the BlazeMeter public report data to artifacts folder.
    
    Creates/updates artifacts/{run_id}/blazemeter/public_report.json with the
    public URL and token information for use by PerfReport MCP.
    
    Args:
        run_id: The BlazeMeter run/master ID.
        public_report_data: Dictionary containing:
            - run_id: The run ID
            - public_url: The shareable report URL
            - public_token: The public token
            - is_new: Whether the token was newly created
            - error: Error message if any (None if successful)
    
    Returns:
        str: Path to the written JSON file, or error message if failed.
    
    Example:
        >>> data = {
        ...     "run_id": "80593110",
        ...     "public_url": "https://a.blazemeter.com/app/?public-token=abc123#/masters/80593110/summary",
        ...     "public_token": "abc123",
        ...     "is_new": True,
        ...     "error": None
        ... }
        >>> path = write_public_report_json("80593110", data)
        >>> print(path)
        'artifacts/80593110/blazemeter/public_report.json'
    """
    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter")
    os.makedirs(dest_folder, exist_ok=True)
    report_path = os.path.join(dest_folder, "public_report.json")
    
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(public_report_data, f, indent=2)
        return report_path
    except Exception as e:
        return f"❗ Error writing public_report.json: {e}"
