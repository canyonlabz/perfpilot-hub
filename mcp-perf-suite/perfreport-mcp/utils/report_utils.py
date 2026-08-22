"""
utils/report_utils.py
Shared utility functions for report generation in PerfReport MCP.

This module contains helper functions that are used across multiple report
generators (single-run reports and comparison reports).
"""

import re


def strip_report_headers_footers(content: str) -> str:
    """
    Remove auto-generated header and footer lines from analysis markdown content.
    
    This function provides backwards compatibility for analysis files that include
    verbose header/footer lines. The report template already includes metadata
    at the bottom, so these redundant sections should be stripped.
    
    Patterns removed:
    - Lines matching: "* Analysis Report - Run <run_id>" (header)
    - Lines matching: "Generated: <ISO timestamp>" (footer) - various formats
    
    Args:
        content: Raw markdown content from analysis files
        
    Returns:
        Cleaned markdown content with headers/footers removed
        
    Examples:
        >>> content = "Infrastructure Analysis Report - Run 80593110\\n\\nActual content here\\n\\nGenerated: 2026-01-13T11:03:20.594620"
        >>> strip_report_headers_footers(content)
        'Actual content here'
    
    TODO: Future enhancement - Update PerfAnalysis MCP to not output these
          header/footer lines in the first place. Once that is done, this
          function can be deprecated but kept for backwards compatibility
          with older analysis files.
    """
    if not content:
        return content
    
    lines = content.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        
        # Skip header lines (e.g., "Infrastructure Analysis Report - Run 80593110")
        if re.match(r'^.*Analysis Report - Run \d+$', stripped_line):
            continue
        
        # Skip footer lines with various "Generated:" timestamp formats:
        # - "Generated: 2026-01-13T11:03:20.594620"
        # - "*Generated: 2026-01-13T11:03:20.594620*" (italicized)
        # - "**Generated:** 2026-01-13T11:03:20.594620" (bold label)
        # - Lines that are ONLY the timestamp
        # Pattern accounts for leading/trailing markdown formatting (* or _)
        if re.match(r'^[\*_]*Generated:[\*_]*\s*\d{4}-\d{2}-\d{2}T[\d:.]+[\*_]*$', stripped_line):
            continue
        
        # Skip standalone ISO timestamps (e.g., "2026-01-13T11:03:20.594620")
        if re.match(r'^[\*_]*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.?\d*[\*_]*$', stripped_line):
            continue
        
        cleaned_lines.append(line)
    
    return '\n'.join(cleaned_lines).strip()


def strip_service_names_in_markdown(content: str) -> str:
    """
    Strip environment prefix and trailing wildcard from service/host names
    within markdown content (tables, lists, etc.).
    
    This function searches for patterns like "Perf::service-name*" in the
    content and replaces them with just "service-name".
    
    Args:
        content: Markdown content that may contain decorated service names
        
    Returns:
        Content with service names cleaned
        
    Examples:
        >>> content = "| Perf::my-service* | 45.2% |"
        >>> strip_service_names_in_markdown(content)
        '| my-service | 45.2% |'
    """
    if not content:
        return content
    
    # Pattern to match environment prefix and optional trailing wildcard
    # Matches: "EnvName::service-name*" or "EnvName::service-name"
    # Captures: (EnvPrefix)(ServiceName)(OptionalWildcard)
    pattern = r'([A-Za-z0-9_-]+)::([A-Za-z0-9_-]+)\*?'
    
    def replace_match(match):
        # Return just the service name (group 2)
        return match.group(2)
    
    return re.sub(pattern, replace_match, content)


def strip_service_name_decorations(name: str) -> str:
    """
    Strip environment prefix and trailing wildcard from service/host names.
    
    Service names in infrastructure data often include an environment prefix
    (e.g., "Perf::") added for identification and a trailing wildcard (*)
    used for Datadog API filtering. This function removes both for cleaner
    display in reports.
    
    Args:
        name: Raw service or host name with potential decorations
        
    Returns:
        Cleaned name without environment prefix or trailing wildcard
        
    Examples:
        >>> strip_service_name_decorations("Perf::my-service-name*")
        'my-service-name'
        >>> strip_service_name_decorations("UAT::api-gateway*")
        'api-gateway'
        >>> strip_service_name_decorations("my-host")
        'my-host'
        >>> strip_service_name_decorations("Prod::auth-service")
        'auth-service'
    """
    if not name:
        return name
    
    # Strip environment prefix (format: "EnvName::")
    if '::' in name:
        name = name.split('::', 1)[1]
    
    # Strip trailing wildcard
    if name.endswith('*'):
        name = name[:-1]
    
    return name


def format_duration(seconds: int) -> str:
    """
    Format duration in seconds to human-readable format.
    
    Converts a duration value in seconds to a readable string format
    showing hours, minutes, and seconds as appropriate.
    
    Args:
        seconds: Duration in seconds (integer or float, will be converted to int)
        
    Returns:
        Formatted string like "90m 28s" or "1h 30m 28s"
        
    Examples:
        >>> format_duration(5428)
        '1h 30m 28s'
        >>> format_duration(90)
        '1m 30s'
        >>> format_duration(45)
        '45s'
        >>> format_duration(0)
        '0s'
    """
    # Handle None or invalid input
    if seconds is None:
        return "0s"
    
    # Convert to integer if needed
    try:
        seconds = int(seconds)
    except (ValueError, TypeError):
        return "0s"
    
    if seconds <= 0:
        return "0s"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"
