import yaml
import os
import platform
import json
from typing import Dict
from pathlib import Path


def _get_mcp_suite_root() -> str:
    """Resolve the mcp-perf-suite repo root from this file's location."""
    return str(Path(__file__).resolve().parent.parent.parent)


def load_config():
    # Assuming this file is at 'repo/<mcp-server>/utils/config.py', we go up one level.
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # Platform-specific config mapping
    config_map = {
        'Darwin': 'config.mac.yaml',
        'Windows': 'config.windows.yaml'
    }

    system = platform.system()
    platform_config = config_map.get(system)
    
    # Use platform-specific config if it exists, otherwise fall back to config.yaml
    candidate_files = [platform_config, 'config.yaml'] if platform_config else ['config.yaml']
    
    config = None
    for filename in candidate_files:
        config_path = os.path.join(repo_root, filename)
        if os.path.exists(config_path):
            with open(config_path, 'r') as file:
                try:
                    config = yaml.safe_load(file)
                    break
                except yaml.YAMLError as e:
                    raise Exception(f"Error parsing '{filename}': {e}")
    
    if config is None:
        raise FileNotFoundError("No valid configuration file found (checked platform-specific and default).")

    # Dynamically resolve artifacts_path if not explicitly set
    if not config.get("artifacts", {}).get("artifacts_path"):
        config.setdefault("artifacts", {})
        config["artifacts"]["artifacts_path"] = str(
            Path(_get_mcp_suite_root()) / "artifacts"
        )

    # Dynamically resolve templates_path if not explicitly set
    if not config.get("perf_report", {}).get("templates_path"):
        config.setdefault("perf_report", {})
        config["perf_report"]["templates_path"] = str(
            Path(_get_mcp_suite_root()) / "perfreport-mcp" / "templates"
        )

    return config

def load_chart_colors() -> Dict:
    """Load chart color configuration"""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    colors_path = os.path.join(repo_root, "chart_colors.yaml")
    
    if not os.path.exists(colors_path):
        # Return default colors if file missing
        return {
            "primary": "#1f77b4",
            "secondary": "#ff7f0e",
            "success": "#2ca02c",
            "error": "#d62728",
            "warning": "#ff9800"
        }
    
    with open(colors_path, 'r') as file:
        try:
            return yaml.safe_load(file)
        except yaml.YAMLError as e:
            raise Exception(f"Error parsing chart_colors.yaml: {e}")


def load_report_config() -> Dict:
    """
    Load report display configuration from report_config.yaml.
    
    This configuration controls the look and feel of generated reports,
    such as which columns to display in infrastructure tables.
    
    Returns:
        Dict containing report configuration options. Returns sensible
        defaults if the config file doesn't exist.
        
    Example:
        >>> config = load_report_config()
        >>> show_allocated = config["infrastructure_tables"]["cpu_utilization"]["show_allocated_column"]
        >>> print(show_allocated)
        True
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(repo_root, "report_config.yaml")
    
    # Default configuration - used if file doesn't exist
    defaults = {
        "version": "1.1",
        "infrastructure_tables": {
            "cpu_utilization": {"show_allocated_column": True},
            "cpu_core_usage": {"show_allocated_column": True},
            "memory_utilization": {"show_allocated_column": True},
            "memory_usage": {"show_allocated_column": True}
        },
        "revisable_sections": {
            "single_run": {
                "executive_summary": {
                    "enabled": False,
                    "placeholder": "EXECUTIVE_SUMMARY",
                    "ai_placeholder": "AI_EXECUTIVE_SUMMARY",
                    "output_file": "AI_EXECUTIVE_SUMMARY",
                    "description": "High-level test outcome summary with key metrics and findings"
                },
                "key_observations": {
                    "enabled": False,
                    "placeholder": "KEY_OBSERVATIONS",
                    "ai_placeholder": "AI_KEY_OBSERVATIONS",
                    "output_file": "AI_KEY_OBSERVATIONS",
                    "description": "Bullet-point observations about test performance and issues"
                },
                "issues_table": {
                    "enabled": False,
                    "placeholder": "ISSUES_TABLE",
                    "ai_placeholder": "AI_ISSUES_TABLE",
                    "output_file": "AI_ISSUES_TABLE",
                    "description": "Table of issues and errors observed during test execution"
                }
            },
            "comparison": {
                "executive_summary": {
                    "enabled": False,
                    "placeholder": "EXECUTIVE_SUMMARY",
                    "ai_placeholder": "AI_EXECUTIVE_SUMMARY",
                    "output_file": "AI_EXECUTIVE_SUMMARY",
                    "description": "Comparison summary across multiple test runs"
                },
                "key_findings": {
                    "enabled": False,
                    "placeholder": "KEY_FINDINGS_BULLETS",
                    "ai_placeholder": "AI_KEY_FINDINGS_BULLETS",
                    "output_file": "AI_KEY_FINDINGS_BULLETS",
                    "description": "Key findings from comparing test runs"
                },
                "issues_summary": {
                    "enabled": False,
                    "placeholder": "ISSUES_SUMMARY",
                    "ai_placeholder": "AI_ISSUES_SUMMARY",
                    "output_file": "AI_ISSUES_SUMMARY",
                    "description": "Summary of issues across compared runs"
                }
            }
        }
    }
    
    if not os.path.exists(config_path):
        return defaults
    
    with open(config_path, 'r') as file:
        try:
            config = yaml.safe_load(file) or {}
            # Merge with defaults to ensure all keys exist
            merged = defaults.copy()
            
            # Merge infrastructure_tables
            if "infrastructure_tables" in config:
                for table_name, table_config in config["infrastructure_tables"].items():
                    if table_name in merged["infrastructure_tables"]:
                        merged["infrastructure_tables"][table_name].update(table_config)
                    else:
                        merged["infrastructure_tables"][table_name] = table_config
            
            # Merge revisable_sections
            if "revisable_sections" in config:
                for report_type, sections in config["revisable_sections"].items():
                    if report_type in merged["revisable_sections"]:
                        for section_id, section_config in sections.items():
                            if section_id in merged["revisable_sections"][report_type]:
                                merged["revisable_sections"][report_type][section_id].update(section_config)
                            else:
                                merged["revisable_sections"][report_type][section_id] = section_config
                    else:
                        merged["revisable_sections"][report_type] = sections
            
            return merged
        except yaml.YAMLError as e:
            raise Exception(f"Error parsing report_config.yaml: {e}")


def load_revisable_sections_config(report_type: str = "single_run", enabled_only: bool = False) -> Dict:
    """
    Load revisable sections configuration from report_config.yaml.
    
    This configuration controls which report sections are available for 
    AI-assisted revision and their associated placeholder mappings.
    
    Args:
        report_type: Type of report - "single_run" (default) or "comparison".
        enabled_only: If True, returns only sections with enabled=True.
                     If False (default), returns all sections for the report type.
    
    Returns:
        Dict of section configurations. Each section contains:
            - enabled: bool - Whether the section is enabled for revision
            - placeholder: str - Original template placeholder name
            - ai_placeholder: str - AI revision placeholder name  
            - output_file: str - Base filename for revision files (without version suffix)
            - description: str - Human-readable description
        
    Raises:
        ValueError: If report_type is not "single_run" or "comparison".
        
    Example:
        >>> # Get all single_run sections
        >>> sections = load_revisable_sections_config("single_run")
        >>> print(sections.keys())
        dict_keys(['executive_summary', 'key_observations', 'issues_table'])
        
        >>> # Get only enabled sections
        >>> enabled = load_revisable_sections_config("single_run", enabled_only=True)
        >>> print(len(enabled))  # 0 by default since all are disabled
        0
        
        >>> # Check if a specific section is enabled
        >>> sections = load_revisable_sections_config("single_run")
        >>> if sections["executive_summary"]["enabled"]:
        ...     print("Executive summary revision is enabled")
    """
    # Validate report_type
    valid_report_types = ["single_run", "comparison"]
    if report_type not in valid_report_types:
        raise ValueError(
            f"Invalid report_type: '{report_type}'. "
            f"Must be one of: {valid_report_types}"
        )
    
    # Load the full report config (includes defaults)
    config = load_report_config()
    
    # Get sections for the specified report type
    revisable_sections = config.get("revisable_sections", {})
    sections = revisable_sections.get(report_type, {})
    
    # Filter to enabled only if requested
    if enabled_only:
        sections = {
            section_id: section_config
            for section_id, section_config in sections.items()
            if section_config.get("enabled", False)
        }
    
    return sections


def get_section_config(report_type: str, section_id: str) -> Dict:
    """
    Get configuration for a specific revisable section.
    
    Args:
        report_type: Type of report - "single_run" or "comparison".
        section_id: Section identifier (e.g., "executive_summary").
    
    Returns:
        Dict containing section configuration, or empty dict if not found.
        
    Example:
        >>> config = get_section_config("single_run", "executive_summary")
        >>> print(config["ai_placeholder"])
        AI_EXECUTIVE_SUMMARY
    """
    sections = load_revisable_sections_config(report_type)
    return sections.get(section_id, {})


if __name__ == '__main__':
    # For testing purposes, print configurations.
    print("=" * 60)
    print("Testing config.py functions")
    print("=" * 60)
    
    # Test load_config()
    print("\n1. General configuration (load_config):")
    config = load_config()
    print(f"   Artifacts path: {config.get('artifacts', {}).get('artifacts_path', 'N/A')}")
    
    # Test load_report_config()
    print("\n2. Report configuration (load_report_config):")
    report_config = load_report_config()
    print(f"   Version: {report_config.get('version', 'N/A')}")
    print(f"   Infrastructure tables configured: {list(report_config.get('infrastructure_tables', {}).keys())}")
    
    # Test load_revisable_sections_config()
    print("\n3. Revisable sections - single_run (load_revisable_sections_config):")
    single_run_sections = load_revisable_sections_config("single_run")
    for section_id, section_config in single_run_sections.items():
        enabled_str = "ENABLED" if section_config.get("enabled") else "disabled"
        print(f"   - {section_id}: {enabled_str}")
    
    print("\n4. Revisable sections - comparison:")
    comparison_sections = load_revisable_sections_config("comparison")
    for section_id, section_config in comparison_sections.items():
        enabled_str = "ENABLED" if section_config.get("enabled") else "disabled"
        print(f"   - {section_id}: {enabled_str}")
    
    print("\n5. Enabled sections only (single_run):")
    enabled_sections = load_revisable_sections_config("single_run", enabled_only=True)
    if enabled_sections:
        for section_id in enabled_sections:
            print(f"   - {section_id}")
    else:
        print("   (none enabled)")
    
    print("\n" + "=" * 60)
