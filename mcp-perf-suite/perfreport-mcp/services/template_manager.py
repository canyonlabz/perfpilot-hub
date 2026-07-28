"""
services/template_manager.py
Template management and listing
"""

from pathlib import Path
from typing import Dict
import os

# Import config at module level
from utils.config import load_config

# Load configuration globally
CONFIG = load_config()
REPORT_CONFIG = CONFIG.get('perf_report', {})
TEMPLATES_PATH = Path(REPORT_CONFIG.get('templates_path', './templates'))


async def list_templates() -> Dict:
    """
    List available report templates.
    """
    try:
        if not TEMPLATES_PATH.exists():
            return {
                "error": f"Templates directory not found: {TEMPLATES_PATH}",
                "templates": []
            }
        
        templates = []
        for file in TEMPLATES_PATH.glob("*.md"):
            templates.append({
                "name": file.name,
                "path": str(file),
                "size": file.stat().st_size
            })
        
        return {
            "templates": templates,
            "count": len(templates)
        }
    except Exception as e:
        return {
            "error": f"Failed to list templates: {str(e)}",
            "templates": []
        }


async def get_template_details(template_name: str) -> Dict:
    """
    Get details of a specific template.
    """
    try:
        template_path = TEMPLATES_PATH / template_name
        
        if not template_path.exists():
            return {
                "error": f"Template not found: {template_name}"
            }
        
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {
            "name": template_name,
            "path": str(template_path),
            "size": template_path.stat().st_size,
            "preview": content[:500] + "..." if len(content) > 500 else content,
            "placeholders": content.count("{{")
        }
    except Exception as e:
        return {
            "error": f"Failed to get template details: {str(e)}"
        }
