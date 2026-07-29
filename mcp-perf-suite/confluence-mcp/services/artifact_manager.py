import os
from pathlib import Path
from typing import Optional, List, Dict
from fastmcp import Context
from utils.config import load_config

# Load config
config = load_config()
ARTIFACTS_PATH = config['artifacts']['artifacts_path']

async def list_available_reports(test_run_id: Optional[str] = None, ctx: Context = None) -> List[Dict]:
    """
    Lists available Markdown performance reports from artifacts directory.
    
    Args:
        test_run_id: If provided, lists single-run reports for that run_id.
                     If None or "comparisons", lists all comparison reports from subfolders.
                     If a specific comparison_id (e.g., "2026-01-21-09-03-30"), lists that comparison only.
        ctx: FastMCP context for logging.
    
    Returns:
        List of report metadata dicts with:
        - filename
        - filepath
        - report_type ("single_run" or "comparison")
        - test_run_ids (list)
        - comparison_id (for comparison reports)
        - display_name (parsed title or filename)
    
    Examples:
        # List single-run reports
        list_available_reports("80247571")
        
        # List all comparison reports
        list_available_reports()
        list_available_reports("comparisons")
        
        # List specific comparison report
        list_available_reports("2026-01-21-09-03-30")
    """
    reports = []
    
    if test_run_id and test_run_id != "comparisons":
        # Check if this is a comparison_id (timestamp format) or a regular test_run_id
        comparisons_dir = Path(ARTIFACTS_PATH) / "comparisons" / test_run_id
        if comparisons_dir.exists():
            # This is a comparison_id - list reports from that specific comparison folder
            report_type = "comparison"
            for file in comparisons_dir.glob("*.md"):
                filename = file.name
                
                # Parse test run IDs from filename: comparison_report_<id1>_<id2>_..._<idN>.md
                test_run_ids = []
                parts = filename.replace(".md", "").split("_")
                if len(parts) >= 4 and parts[0] == "comparison" and parts[1] == "report":
                    test_run_ids = parts[2:]
                
                # Extract display name from first line of file
                display_name = filename
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        first_line = f.readline().strip()
                        if first_line.startswith('#'):
                            display_name = first_line.lstrip('#').strip()
                except Exception as e:
                    if ctx:
                        await ctx.warning(f"Could not parse title from {filename}: {e}")
                
                reports.append({
                    "filename": filename,
                    "filepath": str(file.absolute()),
                    "report_type": report_type,
                    "test_run_ids": test_run_ids,
                    "comparison_id": test_run_id,
                    "display_name": display_name
                })
        else:
            # This is a regular test_run_id - single test run reports
            report_dir = Path(ARTIFACTS_PATH) / test_run_id / "reports"
            report_type = "single_run"
            
            if not report_dir.exists():
                warning_msg = f"Report directory not found: {report_dir}"
                return {"warning": warning_msg}
            
            for file in report_dir.glob("*.md"):
                filename = file.name
                
                # Parse test run IDs from filename: performance_report_<run_id>.md
                test_run_ids = []
                parts = filename.replace(".md", "").split("_")
                if len(parts) >= 3:
                    test_run_ids = [parts[-1]]
                
                # Extract display name from first line of file
                display_name = filename
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        first_line = f.readline().strip()
                        if first_line.startswith('#'):
                            display_name = first_line.lstrip('#').strip()
                except Exception as e:
                    if ctx:
                        await ctx.warning(f"Could not parse title from {filename}: {e}")
                
                reports.append({
                    "filename": filename,
                    "filepath": str(file.absolute()),
                    "report_type": report_type,
                    "test_run_ids": test_run_ids,
                    "display_name": display_name
                })
    else:
        # List all comparison reports from all comparison subfolders
        comparisons_base = Path(ARTIFACTS_PATH) / "comparisons"
        report_type = "comparison"
        
        if not comparisons_base.exists():
            warning_msg = f"Comparisons directory not found: {comparisons_base}"
            return {"warning": warning_msg}
        
        # Iterate through all comparison_id subfolders
        for comparison_folder in comparisons_base.iterdir():
            if comparison_folder.is_dir():
                comparison_id = comparison_folder.name
                
                for file in comparison_folder.glob("*.md"):
                    filename = file.name
                    
                    # Parse test run IDs from filename
                    test_run_ids = []
                    parts = filename.replace(".md", "").split("_")
                    if len(parts) >= 4 and parts[0] == "comparison" and parts[1] == "report":
                        test_run_ids = parts[2:]
                    
                    # Extract display name from first line of file
                    display_name = filename
                    try:
                        with open(file, 'r', encoding='utf-8') as f:
                            first_line = f.readline().strip()
                            if first_line.startswith('#'):
                                display_name = first_line.lstrip('#').strip()
                    except Exception as e:
                        if ctx:
                            await ctx.warning(f"Could not parse title from {filename}: {e}")
                    
                    reports.append({
                        "filename": filename,
                        "filepath": str(file.absolute()),
                        "report_type": report_type,
                        "test_run_ids": test_run_ids,
                        "comparison_id": comparison_id,
                        "display_name": display_name
                    })

    if not reports:
        error_msg = f"No markdown report files found"
        return {"error": error_msg}

    return reports


async def list_available_charts(test_run_id: Optional[str] = None, ctx: Context = None) -> List[Dict]:
    """
    Lists available PNG chart images from artifacts directory.
    
    Args:
        test_run_id: If provided as a regular run_id, lists charts for that single run.
                     If provided as a comparison_id (e.g., "2026-01-21-09-03-30"), lists charts for that comparison.
                     If None, lists all comparison charts from all comparison subfolders.
        ctx: FastMCP context for logging.
    
    Returns:
        List of chart metadata dicts with:
        - filename
        - filepath
        - chart_type (inferred from filename if possible)
        - description
        - comparison_id (for comparison charts)
    """
    charts = []
    
    if test_run_id:
        # Check if this is a comparison_id or a regular test_run_id
        comparison_chart_dir = Path(ARTIFACTS_PATH) / "comparisons" / test_run_id / "charts"
        single_chart_dir = Path(ARTIFACTS_PATH) / test_run_id / "charts"
        
        if comparison_chart_dir.exists():
            # This is a comparison_id
            chart_dir = comparison_chart_dir
            is_comparison = True
            comparison_id = test_run_id
        elif single_chart_dir.exists():
            # This is a regular test_run_id
            chart_dir = single_chart_dir
            is_comparison = False
            comparison_id = None
        else:
            warning_msg = f"Chart directory not found for: {test_run_id}"
            return {"warning": warning_msg}
        
        for file in chart_dir.glob("*.png"):
            chart_data = _parse_chart_file(file, is_comparison, comparison_id)
            charts.append(chart_data)
    else:
        # List all comparison charts from all comparison subfolders
        comparisons_base = Path(ARTIFACTS_PATH) / "comparisons"
        
        if not comparisons_base.exists():
            warning_msg = f"Comparisons directory not found: {comparisons_base}"
            return {"warning": warning_msg}
        
        for comparison_folder in comparisons_base.iterdir():
            if comparison_folder.is_dir():
                comparison_id = comparison_folder.name
                charts_folder = comparison_folder / "charts"
                
                if charts_folder.exists():
                    for file in charts_folder.glob("*.png"):
                        chart_data = _parse_chart_file(file, True, comparison_id)
                        charts.append(chart_data)
    
    if not charts:
        error_msg = f"No PNG chart files found"
        return {"error": error_msg}
    
    return charts


def _parse_chart_file(file: Path, is_comparison: bool, comparison_id: Optional[str]) -> Dict:
    """
    Parse chart file metadata from filename.
    
    Args:
        file: Path to the PNG file.
        is_comparison: Whether this is a comparison chart.
        comparison_id: Comparison ID if this is a comparison chart.
    
    Returns:
        Dict with chart metadata.
    """
    filename = file.name
    name_without_ext = filename.replace(".png", "")
    
    # Parse chart filename patterns:
    # Single-run: <chart_id>.png or <chart_id>-<resource>.png
    # Comparison: <chart_id>-<resource>.png (e.g., CPU_PEAK_CORE_COMPARISON_BAR-authentication2-svc.png)
    
    chart_id = "unknown"
    resource_name = None
    
    # Try to extract chart_id and resource
    if "-" in name_without_ext:
        # Has resource suffix
        parts = name_without_ext.split("-", 1)
        chart_id = parts[0]
        resource_name = parts[1] if len(parts) > 1 else None
    else:
        chart_id = name_without_ext
    
    # Create human-readable description
    if resource_name:
        description = f"{chart_id.replace('_', ' ').title()} for {resource_name}"
    else:
        description = f"{chart_id.replace('_', ' ').title()}"
    
    result = {
        "filename": filename,
        "filepath": str(file.absolute()),
        "chart_id": chart_id,
        "resource_name": resource_name,
        "description": description,
        "is_comparison": is_comparison
    }
    
    if comparison_id:
        result["comparison_id"] = comparison_id
    
    return result
