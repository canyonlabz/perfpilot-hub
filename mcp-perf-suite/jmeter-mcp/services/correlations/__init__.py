"""
Correlation analysis package for JMeter MCP.

This package analyzes Playwright-derived network captures to detect dynamic
correlations between HTTP responses and subsequent requests.

Version: 0.2.0
License: MIT
Repository: https://github.com/canyonlabz/mcp-perf-suite
"""

from .analyzer import analyze_traffic

__all__ = ["analyze_traffic"]
__version__ = "0.2.0"

