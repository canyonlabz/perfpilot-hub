from fastmcp import FastMCP, Context
from typing import Optional
from services.report_generator import (
    generate_performance_test_report,
)
from services.chart_generator import (
    generate_chart,
    generate_comparison_chart,
)
from services.template_manager import (
    list_templates as get_template_list, 
    get_template_details as get_template_info
)
from services.comparison_report_generator import generate_comparison_report
from services.revision_data_discovery import discover_revision_data as get_revision_data
from services.revision_context_manager import prepare_revision_context as save_revision_context
from services.report_revision_generator import revise_performance_test_report as generate_revised_report


mcp = FastMCP("perfreport")


@mcp.tool
async def create_performance_test_report(run_id: str, ctx: Context, format: str = "md", template: str = None) -> dict:
    """
    Generate a formatted performance test report for a specific run.
    Args:
        run_id: Unique test run identifier.
        ctx: Workflow context for chaining.
        format: Output type; one of 'md', 'pdf', or 'docx'.
        template: Optional name of Markdown report template to use.
        
    Returns:
        dict with run_id and path to created report file, or error info.
    """
    return await generate_performance_test_report(run_id, ctx, format, template)

@mcp.tool
async def create_comparison_report(run_id_list: list, template: str = None, format: str = "md", ctx: Context = None) -> dict:
    """
    Generate a report comparing multiple test runs.
    
    Args:
        run_id_list: List of test run identifiers to compare (2-5 recommended).
        template: Optional name of Markdown comparison template to use.
        format: Output type; 'md', 'pdf', or 'docx'.
        ctx: Workflow chaining context.
    
    Returns:
        dict with run_id_list, report path, and metadata, or error info.
    """
    return await generate_comparison_report(run_id_list, ctx, format, template)
    
@mcp.tool
async def discover_revision_data(
    run_id: str,
    report_type: str = "single_run",
    additional_context: Optional[str] = None,
    ctx: Context = None
) -> dict:
    """
    Discover available data files for AI-assisted report revision.
    
    Scans artifacts folder structure to identify all output files from BlazeMeter,
    Datadog, and PerfAnalysis MCPs that can be used for generating revised content.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" (default) or "comparison".
        additional_context: Optional user-provided context to incorporate into revisions
                           (e.g., project name, purpose, feature/PBI details from ADO/JIRA).
        ctx: Workflow chaining context.
    
    Returns:
        dict containing:
            - data_sources: Organized file paths by MCP source
            - revisable_sections: Enabled sections from config
            - revision_output_path: Path for saving revision files
            - additional_context: User context passed through for AI
            - existing_revisions: Current revision versions per section
            - revision_guidelines: Instructions for AI on expected output
    """
    return await get_revision_data(run_id, report_type, additional_context)


@mcp.tool
async def prepare_revision_context(
    run_id: str,
    section_id: str,
    revised_content: str,
    report_type: str = "single_run",
    additional_context: Optional[str] = None,
    ctx: Context = None
) -> dict:
    """
    Save AI-generated revised content for a report section.
    
    Supports Human-In-The-Loop (HITL) workflows by automatically incrementing
    version numbers. Call this for each section after generating revised content.
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        section_id: Section identifier (e.g., "executive_summary", "key_observations").
        revised_content: AI-generated markdown content for the section.
        report_type: "single_run" (default) or "comparison".
        additional_context: Optional context that was used during revision (for traceability).
        ctx: Workflow chaining context.
    
    Returns:
        dict containing:
            - section_full_id: Composite identifier (e.g., "single_run.executive_summary")
            - revision_number: Version number assigned (1, 2, 3...)
            - revision_path: Full path to the saved revision file
            - previous_versions: List of existing versions before this save
            - status: "success" or "error"
    """
    return await save_revision_context(run_id, section_id, revised_content, report_type, additional_context)


@mcp.tool
async def revise_performance_test_report(
    run_id: str,
    report_type: str = "single_run",
    revision_version: Optional[int] = None,
    ctx: Context = None
) -> dict:
    """
    Assemble a revised performance test report using AI-generated content.
    
    This tool assembles the final revised report by:
    1. Loading AI revision content for each enabled section
    2. Backing up the original report and metadata
    3. Replacing placeholders with AI-revised content
    4. Saving the new revised report
    
    Prerequisites:
    - Run create_performance_test_report() first to generate initial report
    - Enable desired sections in report_config.yaml
    - Run discover_revision_data() to get data context
    - Run prepare_revision_context() for each section with AI content
    
    Args:
        run_id: Test run ID (for single_run) or comparison_id (for comparison).
        report_type: "single_run" (default) or "comparison".
        revision_version: Optional specific version of revisions to use (1, 2, 3...).
                         If None, uses the latest version for each section.
        ctx: Workflow chaining context.
    
    Returns:
        dict containing:
            - revised_report_path: Path to the new revised report
            - backup_report_path: Path where original was backed up
            - sections_revised: List of sections that were revised
            - revision_versions_used: Dict mapping section_id to version used
            - warnings: Any non-fatal warnings
            - status: "success" or "error"
    """
    return await generate_revised_report(run_id, report_type, revision_version)

@mcp.tool
async def create_chart(run_id: str, chart_id: str, env_name: Optional[str] = None, ctx: Context = None) -> dict:
    """
    Unified chart generation tool for MCP.
    Args:
        run_id: Test run ID
        chart_id: Chart type specifier (must match YAML/schema)
        env_name: Optional, for infrastructure charts
        ctx: Workflow context
    Returns:
        dict containing chart metadata, path, and any errors
    """
    return await generate_chart(run_id, env_name, chart_id)


@mcp.tool
async def create_comparison_chart(
    comparison_id: str,
    run_id_list: list, 
    chart_id: str, 
    env_name: Optional[str] = None, 
    ctx: Context = None
) -> dict:
    """
    Generate comparison bar charts for multiple test runs.
    
    Args:
        comparison_id: Unique identifier for this comparison (from create_comparison_report)
        run_id_list: List of test run IDs to compare (2-5 recommended)
        chart_id: Chart type specifier (CPU_PEAK_CORE_COMPARISON_BAR, MEMORY_PEAK_USAGE_COMPARISON_BAR, CPU_AVG_CORE_COMPARISON_BAR, MEMORY_AVG_USAGE_COMPARISON_BAR)
        env_name: Optional environment name for resource filtering
        ctx: Workflow context
        
    Returns:
        dict containing comparison_id, run_id_list, chart_id, charts list, and any errors
    """
    return await generate_comparison_chart(comparison_id, run_id_list, chart_id, env_name)


@mcp.tool
async def list_templates(ctx: Context = None) -> dict:
    """
    List available Markdown templates for report generation.
    Args:
        ctx: Workflow chaining context.
    Returns:
        dict listing template names and description metadata.
    """
    return await get_template_list()
    
@mcp.tool
async def get_template_details(template_name: str, ctx: Context = None) -> dict:
    """
    Get detailed info about a report markdown template.
    Args:
        template_name: The markdown template to look up.
        ctx: Workflow chaining context.
    Returns:
        dict with template metadata and content preview, or error info.
    """
    return await get_template_info(template_name)

@mcp.tool
async def list_chart_types(ctx: Context = None) -> dict:
    """
    List available chart types from chart_schema.yaml.
    
    Args:
        ctx: Workflow chaining context.
    
    Returns:
        dict with available chart types and their descriptions.
    """
    from services.chart_generator import CHART_SCHEMA
    
    chart_types = []
    for chart in CHART_SCHEMA.get('charts', []):
        chart_types.append({
            'id': chart['id'],
            'title': chart['title'],
            'description': chart['description'],
            'chart_type': chart['chart_type'],
            'placeholder': chart.get('placeholder', '')
        })
    
    return {
        'chart_types': chart_types,
        'total_count': len(chart_types)
    }

if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down Performance Reporting MCP…")

