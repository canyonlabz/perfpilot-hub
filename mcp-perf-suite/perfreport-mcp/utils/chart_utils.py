"""
utils/chart_utils.py
File I/O helper functions for chart generation in PerfAnalysis MCP
"""
import json
import asyncio
import pypandoc
import re
from pathlib import Path
from typing import Dict, Optional, List, Tuple

# Import config at module level
from utils.config import load_config, load_chart_colors, _get_mcp_suite_root

# Load configuration
CONFIG = load_config()
REPORT_CONFIG = CONFIG.get("perf_report", {})
ARTIFACTS_CONFIG = CONFIG.get('artifacts', {})
ARTIFACTS_PATH = Path(ARTIFACTS_CONFIG.get('artifacts_path', '../artifacts'))
APM_TOOL = REPORT_CONFIG.get("apm_tool", "datadog").lower()

# Load chart colors at module level
CHART_COLORS = load_chart_colors()


# -----------------------------------------------
# Color Helper Functions
# -----------------------------------------------

def get_multi_line_colors(count: int) -> List[str]:
    """
    Get colors for multi-line charts (multiple hosts/services on same chart).
    
    Colors cycle round-robin when count exceeds the number of defined colors.
    
    Args:
        count: Number of colors needed (typically number of data series)
    
    Returns:
        List of hex color codes
    
    Example:
        >>> colors = get_multi_line_colors(3)
        >>> print(colors)
        ['#1f77b4', '#ff7f0e', '#2ca02c']
    """
    # Get multi-line colors from config, fallback to base colors
    multi_line_config = CHART_COLORS.get("multi_line", {})
    palette = multi_line_config.get("colors", [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ])
    
    # Cycle through colors if more series than colors defined
    return [palette[i % len(palette)] for i in range(count)]


def get_comparison_colors(count: int) -> List[str]:
    """
    Get colors for comparison charts (multiple test runs).
    
    Uses a cohesive color palette designed for comparing test runs.
    Colors cycle round-robin when count exceeds the number of defined colors.
    
    Args:
        count: Number of colors needed (typically number of test runs)
    
    Returns:
        List of hex color codes
    
    Example:
        >>> colors = get_comparison_colors(5)
        >>> print(colors)
        ['#4CC9F0', '#4895EF', '#4361EE', '#2B35AF', '#12086F']
    """
    # Get comparison colors from config, fallback to navy-blue palette
    comparison_config = CHART_COLORS.get("comparison", {})
    palette = comparison_config.get("colors", [
        "#4CC9F0", "#4895EF", "#4361EE", "#2B35AF", "#12086F",
        "#560BAD", "#7209B7", "#B5179E", "#F72585", "#FF006E"
    ])
    
    # Cycle through colors if more runs than colors defined
    return [palette[i % len(palette)] for i in range(count)]


def resolve_color(color_name: str) -> str:
    """
    Resolve a color name (e.g., 'primary') to its hex value (e.g., '#1f77b4').
    
    If the color_name is already a hex code or not found in config,
    it is returned as-is.
    
    Args:
        color_name: Color name from chart_schema.yaml (e.g., 'primary', 'secondary')
                   or a hex code (e.g., '#1f77b4')
    
    Returns:
        Hex color code
    
    Example:
        >>> resolve_color('primary')
        '#1f77b4'
        >>> resolve_color('#ff0000')
        '#ff0000'
    """
    return CHART_COLORS.get(color_name, color_name)


def resolve_colors(color_names: List[str], count: int) -> List[str]:
    """
    Resolve a list of color names to hex values, cycling if needed.
    
    This is used when chart_schema.yaml specifies colors by name
    (e.g., ['primary', 'secondary']) and we need to resolve them
    to actual hex codes.
    
    Args:
        color_names: List of color names from chart spec
        count: Number of colors needed
    
    Returns:
        List of resolved hex color codes, cycling if count > len(color_names)
    
    Example:
        >>> resolve_colors(['primary', 'secondary'], 3)
        ['#1f77b4', '#ff7f0e', '#1f77b4']
    """
    colors = [resolve_color(name) for name in color_names]
    return [colors[i % len(colors)] for i in range(count)]

# -----------------------------------------------
# Filename normalization helpers (mirrors datadog-mcp logic)
# -----------------------------------------------

def _sanitize_filename(text: str) -> str:
    """Sanitize text to be safe for filenames."""
    text = text.strip().replace(" ", "_")
    return re.sub(r"[^a-zA-Z0-9._-]", "_", text)

def _normalize_k8s_filter(raw_filter: str) -> str:
    """Produce a deterministic, filesystem-safe resource name from a K8s filter.

    Strips wildcard characters before sanitizing so that 'my-pod*' and
    'my-pod' both resolve to the same filename segment ('my-pod').
    """
    stripped = raw_filter.replace("*", "")
    return _sanitize_filename(stripped)

# -----------------------------------------------
# Utility functions
# -----------------------------------------------
async def load_environment_details(run_id: str, env_name: str) -> Optional[Dict]:
    """
    Load environment information and identify its infrastructure resources.

    This unified helper loads the environments.json file from the repo
    root (datadog-mcp/environments.json) and determines whether the target
    environment is infrastructure-based (hosts) or platform-based (Kubernetes).
    It also extracts the associated resource names.

    Args:
        run_id (str):
            The performance test run identifier. Used to resolve chart paths.
        env_name (str):
            The environment key (e.g. 'QA-Central', 'QA-West') specified in
            the environments.json file.

    Returns:
        dict | None:
            Returns a dictionary containing:
            {
                "env_name": <environment name>,
                "env_type": "host" | "k8s" | "unknown",
                "resources": [<list of hostnames or filters>],
                "config": <entire environment definition>
            }
            Returns None if the JSON file is missing or the environment is undefined.

    Example:
        >>> result = await load_environment_details("run_12345", QA-Central")
        >>> print(result["env_type"])
        'k8s'
        >>> print(result["resources"])
        ['app-api', 'app-service']
    """
    env_path = Path(_get_mcp_suite_root()) / "datadog-mcp" / "environments.json"
    if not env_path.exists():
        return None

    loop = asyncio.get_event_loop()
    raw_json = await loop.run_in_executor(None, env_path.read_text)
    env_data = json.loads(raw_json)
    environments = env_data.get("environments", {})

    if env_name not in environments:
        return None

    env_entry = environments[env_name]

    # Identify environment type and collect resources
    env_type = "unknown"
    resources: List[str] = []
    if env_entry.get("hosts"):
        env_type = "host"
        resources = [h["hostname"] for h in env_entry["hosts"]]

    k8s_cfg = env_entry.get("kubernetes", {})
    if k8s_cfg.get("services") or k8s_cfg.get("pods"):
        env_type = "k8s"
        if k8s_cfg.get("services"):
            resources.extend([
                _normalize_k8s_filter(s["service_filter"])
                for s in k8s_cfg["services"]
            ])
        if k8s_cfg.get("pods"):
            resources.extend([
                _normalize_k8s_filter(p["pod_filter"])
                for p in k8s_cfg["pods"]
            ])

    return {
        "env_name": env_name,
        "env_type": env_type,
        "resources": resources,
        "config": env_entry,
    }

async def get_metric_files(
    run_id: str, env_type: str, resources: List[str]
) -> List[Tuple[str, Path]]:
    """
    Discover metric CSV files corresponding to environment resources.

    Returns a list of ``(resource_name, file_path)`` tuples — only for
    resources that have a matching CSV on disk.  Resources without a file
    are omitted, which avoids positional-alignment issues when the caller
    iterates.

    For K8s resources the canonical filename is ``k8s_metrics_[<resource>].csv``.
    A fallback check for the legacy format ``k8s_metrics_[<resource>_].csv``
    is included so that artifacts produced before this fix are still picked
    up automatically.

    Args:
        run_id:  Unique test run identifier (artifacts folder resolution).
        env_type:  Environment type — ``'host'`` or ``'k8s'``.
        resources:  Resource identifiers (hostnames or K8s filters,
            already normalised via ``_normalize_k8s_filter``).

    Returns:
        List of ``(resource_name, csv_path)`` tuples for resources that
        have a matching CSV under ``artifacts/<run_id>/<APM_TOOL>/``.

    Example:
        >>> await get_metric_files("run_12345", "k8s", ["app-api"])
        [("app-api", Path("artifacts/run_12345/datadog/k8s_metrics_[app-api].csv"))]
        >>> await get_metric_files("run_12345", "host", ["web01"])
        [("web01", Path("artifacts/run_12345/datadog/host_metrics_[web01].csv"))]
    """
    base_dir = ARTIFACTS_PATH / run_id / APM_TOOL
    print(f"DEBUG: base_dir={base_dir}, exists={base_dir.exists()}, APM_TOOL={APM_TOOL}")
    if not base_dir.exists():
        print(f"DEBUG: base_dir does not exist!")
        return []

    csv_files = list(base_dir.glob("*.csv"))
    print(f"DEBUG: Found {len(csv_files)} CSV files in {base_dir}")
    csv_names = {f.name: f for f in csv_files}

    discovered: List[Tuple[str, Path]] = []
    for resource in resources:
        canonical = f"{env_type}_metrics_[{resource}].csv"
        legacy = f"{env_type}_metrics_[{resource}_].csv" if env_type != "host" else None

        candidates = [canonical]
        if legacy:
            candidates.append(legacy)

        print(f"DEBUG: Looking for {candidates} in {base_dir}")

        matched = None
        for candidate in candidates:
            if candidate in csv_names:
                matched = csv_names[candidate]
                break

        if matched:
            print(f"DEBUG: Matched {matched.name}")
            discovered.append((resource, matched))
        else:
            print(f"DEBUG: No match found for resource '{resource}'")

    return discovered

def get_chart_output_path(run_id: str, chart_name: str) -> Path:
    """
    Return the output path for saving a generated chart,
    ensuring the output directory exists.

    Args:
        run_id (str): The test run's unique identifier.
        chart_name (str): The base filename (no extension) for the chart.

    Returns:
        Path: The absolute path to where the PNG file should be written.
    """
    charts_dir = ARTIFACTS_PATH / run_id / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    return charts_dir / f"{chart_name}.png"


def get_comparison_chart_output_path(comparison_id: str, chart_name: str) -> Path:
    """
    Return the output path for saving a comparison chart,
    ensuring the output directory exists.
    
    Comparison charts are stored in a subfolder structure:
    artifacts/comparisons/{comparison_id}/charts/{chart_name}.png

    Args:
        comparison_id (str): The comparison's unique identifier (timestamp format).
        chart_name (str): The base filename (no extension) for the chart.

    Returns:
        Path: The absolute path to where the PNG file should be written.
    
    Example:
        >>> get_comparison_chart_output_path("2026-01-21-10-30-00", "CPU_PEAK_CORE_COMPARISON_BAR-app-svc")
        Path("artifacts/comparisons/2026-01-21-10-30-00/charts/CPU_PEAK_CORE_COMPARISON_BAR-app-svc.png")
    """
    charts_dir = ARTIFACTS_PATH / "comparisons" / comparison_id / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    return charts_dir / f"{chart_name}.png"


def interpolate_placeholders(template: str, **kwargs) -> str:
    """
    Replace placeholder tokens (e.g., {resource_name}) in a template string
    with the provided keyword arguments.

    Args:
        template (str):
            The input string containing one or more placeholders enclosed
            in curly braces. Example: "CPU Utilization - {resource_name}".
        **kwargs:
            Key-value pairs corresponding to placeholders and their values.
            For example:
                interpolate_placeholders(
                    "CPU Utilization - {resource_name}",
                    resource_name="api-service"
                )

    Returns:
        str: A string with all matching placeholders substituted. If a
        placeholder in the template does not have a corresponding key
        in `kwargs`, it will remain unchanged or be replaced with a
        `{MISSING:key}` token as a fallback.

    Example:
        >>> interpolate_placeholders(
        ...     "Run {run_id} - Resource: {resource_name}",
        ...     run_id="80014829",
        ...     resource_name="api-service"
        ... )
        'Run 80014829 - Resource: api-service'
    """
    if not template or "{" not in template:
        return template
    try:
        return template.format(**kwargs)
    except KeyError as e:
        # gracefully handle missing placeholders
        missing_key = str(e).strip("'")
        return re.sub(rf"{{{missing_key}}}", f"{{MISSING:{missing_key}}}", template)


# -----------------------------------------------
# Legend Helper
# -----------------------------------------------

def apply_legend(ax, chart_spec: dict, num_series: int = 1):
    """
    Apply legend settings to a matplotlib Axes based on chart_schema.yaml config.

    Supports both standard matplotlib positions (inside the chart) and
    custom outside-chart positions for cleaner visuals.

    Args:
        ax: Matplotlib Axes object to apply the legend to.
        chart_spec (dict): Chart configuration from chart_schema.yaml.
            Reads the following keys:
            - include_legend (bool): Whether to show the legend. Default: False.
            - legend_location (str): Where to place the legend. Default: "upper left".
            - legend_fontsize (int): Font size for legend text. Default: 8.
        num_series (int): Number of data series in the chart. Used to set
            the number of columns for horizontal legend layouts.

    Legend location options:
        Inside chart (standard matplotlib loc values):
            "upper left", "upper right", "lower left", "lower right",
            "center left", "center right", "upper center", "lower center",
            "center", "best"

        Outside chart (custom positions):
            "below"  - Centered below the chart, horizontal layout
            "above"  - Centered above the chart, horizontal layout
            "right"  - To the right of the chart, vertical layout

    Example chart_schema.yaml usage:
        include_legend: true
        legend_location: "below"   # Places legend below the chart
        legend_fontsize: 9         # Optional, defaults to 8

    Note:
        The outside-chart positions ("below", "above", "right") are
        library-agnostic names that will map to any future charting
        library (e.g., Altair, Plotly).
    """
    if not chart_spec.get("include_legend", False):
        return

    location = chart_spec.get("legend_location", "upper left")
    fontsize = chart_spec.get("legend_fontsize", 8)

    if location == "below":
        ncol = min(num_series, 4)
        ax.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, -0.18),
            ncol=ncol,
            fontsize=fontsize,
            frameon=True,
        )
    elif location == "above":
        ncol = min(num_series, 4)
        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.05),
            ncol=ncol,
            fontsize=fontsize,
            frameon=True,
        )
    elif location == "right":
        ax.legend(
            loc="center left",
            bbox_to_anchor=(1.02, 0.5),
            fontsize=fontsize,
            frameon=True,
        )
    else:
        # Standard matplotlib loc value (e.g., "upper left", "best")
        ax.legend(loc=location, fontsize=fontsize)

