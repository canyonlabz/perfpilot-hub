# Confluence API client
import re
from fastmcp import FastMCP, Context
from services.confluence_api_v1 import (
    list_spaces_v1, 
    get_space_details_v1,
    list_pages_v1, 
    get_page_by_id_v1,
    get_page_content_v1,
    create_page_v1, 
    search_content_v1,
    attach_file_v1,
    update_page_v1,
)
from services.confluence_api_v2 import (
    list_spaces_v2, 
    get_space_details_v2,
    list_pages_v2,
    get_page_by_id_v2,
    get_page_content_v2,
    create_page_v2,
    search_content_v2,
    attach_file_v2,
    update_page_v2,
)
from services.artifact_manager import list_available_reports, list_available_charts
from services.content_parser import markdown_to_confluence_xhtml, replace_chart_placeholders, _flatten_xhtml

mcp = FastMCP("confluence")

# -----------------------------
# Validation Helper Functions
# -----------------------------

# Maximum title length for Confluence pages
MAX_TITLE_LENGTH = 255

# Allowed characters pattern for page titles:
# - Alphanumeric (a-z, A-Z, 0-9)
# - Whitespace
# - Underscores, dashes
# - Parentheses, square brackets
# - Commas, periods, colons, semi-colons
# - Hash (#), forward slash (/), percent (%), ampersand (&), apostrophe (')
ALLOWED_TITLE_PATTERN = re.compile(r"^[a-zA-Z0-9\s_\-\(\)\[\],.:;#/%&']+$")

def validate_page_title(title: str) -> dict:
    """
    Validates a Confluence page title for acceptable characters and length.
    
    Args:
        title (str): The title to validate.
    
    Returns:
        dict: Validation result with:
            - 'valid': True if title passes validation, False otherwise
            - 'error': Error message if validation failed, None otherwise
    """
    if not title or not title.strip():
        return {"valid": False, "error": "Title cannot be empty or whitespace only."}
    
    title = title.strip()
    
    # Check length
    if len(title) > MAX_TITLE_LENGTH:
        return {
            "valid": False,
            "error": f"Title exceeds maximum length of {MAX_TITLE_LENGTH} characters. "
                     f"Provided title is {len(title)} characters."
        }
    
    # Check for allowed characters
    if not ALLOWED_TITLE_PATTERN.match(title):
        # Find the invalid characters to provide helpful feedback
        invalid_chars = set()
        for char in title:
            if not re.match(r"[a-zA-Z0-9\s_\-\(\)\[\],.:;#/%&']", char):
                invalid_chars.add(repr(char))
        
        return {
            "valid": False,
            "error": f"Title contains invalid characters: {', '.join(sorted(invalid_chars))}. "
                     f"Allowed characters are: alphanumeric, whitespace, underscores, dashes, "
                     f"parentheses (), square brackets [], commas, periods, colons, semi-colons, "
                     f"hash (#), forward slash (/), percent (%), ampersand (&), and apostrophe (')."
        }
    
    return {"valid": True, "error": None}

# -----------------------------
# MCP Tool Definitions
# -----------------------------

@mcp.tool
async def list_spaces(mode: str, ctx: Context) -> list:
    """
    Lists available Confluence spaces accessible in the given mode (cloud/onprem).

    Args:
        mode (str): Which Confluence API to use. Options:
            - "cloud": Use Confluence Cloud v2 API (uses 'space_id').
            - "onprem": Use on-premises Confluence v1 API (uses 'space_key').
        ctx (Context): FastMCP invocation context. Stores retrieved spaces for reuse.

    Returns:
        List[dict]: Spaces with keys:
            - 'space_ref': Use as space identifier in other tools (maps to either space_id or key based on mode).
            - 'name': Human-friendly display name.
            - 'type': Space type, if available.
    """
    if mode == "cloud":
        return await list_spaces_v2(ctx)
    return await list_spaces_v1(ctx)

@mcp.tool()
async def get_space_details(space_ref: str, mode: str, ctx: Context) -> dict:
    """
    Retrieves metadata and configuration details for a specific Confluence space.

    Args:
        space_ref (str): Identifier for the Confluence space (space_id for cloud or space_key for on-prem).
        mode (str): "cloud" (Confluence v2 API) or "onprem" (Confluence v1 API).
        ctx (Context): FastMCP context for workflow chaining and error reporting.

    Returns:
        dict: Space metadata, including:
            - 'space_ref' (ID or key)
            - 'name'
            - 'type'
            - 'description'
            - 'status'
            - Additional API-provided metadata (e.g., permissions, categories, homepage)
    """
    if mode == "cloud":
        return await get_space_details_v2(space_ref, ctx)
    return await get_space_details_v1(space_ref, ctx)

@mcp.tool
async def list_pages(space_ref: str, mode: str, ctx: Context) -> list:
    """
    Lists pages in a Confluence space. Use 'space_ref' output from list_spaces.

    Args:
        space_ref (str): Unique reference for the Confluence space (either space_id or space_key).
        mode (str): "cloud" for v2 API; "onprem" for v1 API.
        ctx (Context): FastMCP invocation context. Page list will be stored for further workflow steps.

    Returns:
        List of page metadata dicts with:
            - page_ref: Unique page identifier
            - title: Page title
            - status: Page status (current, archived, etc.)
            - url: Direct link to page
            - Additional fields depending on API version
    """
    if mode == "cloud":
        return await list_pages_v2(space_ref, ctx)
    else:
        return await list_pages_v1(space_ref, ctx)

@mcp.tool()
async def get_page_by_id(page_ref: str, mode: str, ctx: Context) -> dict:
    """
    Retrieves detailed metadata for a specific Confluence page by ID.
    
    Args:
        page_ref (str): Unique page identifier (page ID).
        mode (str): "cloud" (Confluence v2 API) or "onprem" (Confluence v1 API).
        ctx (Context): FastMCP invocation context for workflow chaining and logging.
    
    Returns:
        dict: Page metadata including:
            - page_ref: Page ID
            - title: Page title
            - status: Page status (current, archived, etc.)
            - version: Current version number
            - space information (key/name for v1, id for v2)
            - timestamps (created, last modified)
            - author/owner information
            - url: Direct link to page
    """
    if mode == "cloud":
        return await get_page_by_id_v2(page_ref, ctx)
    else:
        return await get_page_by_id_v1(page_ref, ctx)

@mcp.tool()
async def get_page_content(page_ref: str, mode: str, ctx: Context) -> dict:
    """
    Retrieves the full content body of a Confluence page in storage format (XHTML).
    
    This is useful for:
    - Reading existing page content before updates
    - Extracting specific sections or data
    - Backing up page content
    - Analyzing page structure
    
    Args:
        page_ref (str): Unique page identifier (page ID).
        mode (str): "cloud" (Confluence v2 API) or "onprem" (Confluence v1 API).
        ctx (Context): FastMCP invocation context for workflow chaining and logging.
    
    Returns:
        dict: Page content including:
            - page_ref: Page ID
            - title: Page title
            - status: Page status
            - storage_xhtml: Full page body content in Confluence storage format (XHTML)
            - representation: Content representation type (typically "storage")
            - url: Direct link to page
    """
    if mode == "cloud":
        return await get_page_content_v2(page_ref, ctx)
    else:
        return await get_page_content_v1(page_ref, ctx)

@mcp.tool
async def create_page(space_ref: str, test_run_id: str, filename: str, mode: str, ctx: Context, parent_id: str, title: str = None, report_type: str = "single_run") -> dict:
    """
    Creates a new Confluence page by importing a Markdown performance report.
    
    Args:
        space_ref (str): Space identifier (space_id for cloud, space_key for on-prem) from list_spaces.
        test_run_id (str): ID of the test run or comparison_id (used for artifact path).
        filename (str): Markdown report filename, as returned by get_available_reports.
        mode (str): "cloud" or "onprem".
        ctx (Context): FastMCP context for error/status reporting.
        parent_id (str): Parent page ID to nest the new page under.
        title (str, optional): Custom page title. If not provided, title is extracted from 
            the markdown H1 heading, or falls back to the filename. Allowed characters:
            alphanumeric, whitespace, underscores, dashes, parentheses, square brackets,
            commas, periods, colons, semi-colons, hash, forward slash, percent, ampersand,
            and apostrophe. Maximum length: 255 characters.
        report_type (str): Type of report - "single_run" (default) or "comparison".
            - "single_run": Path is artifacts/{test_run_id}/reports/
            - "comparison": Path is artifacts/comparisons/{test_run_id}/ (test_run_id is comparison_id)
    
    Returns:
        dict with:
            - 'page_ref': Created page reference/ID.
            - 'url': New page URL.
            - 'title': The title used for the page (user-provided or auto-extracted).
            - 'title_source': Indicates where the title came from ("user_provided", "markdown_h1", or "filename").
            - 'status': Result status ("created" or "error").
    
    Examples:
        # Single-run report
        create_page(space_ref="MYQA", test_run_id="80247571", 
                    filename="performance_report_80247571.md", mode="onprem", 
                    report_type="single_run", ...)
        
        # Comparison report (test_run_id is comparison_id)
        create_page(space_ref="MYQA", test_run_id="2026-01-21-09-03-30", 
                    filename="comparison_report_run1_run2.md", mode="onprem",
                    report_type="comparison", ...)
    """
    from pathlib import Path
    from utils.config import load_config
    
    # Load artifacts path from config
    config = load_config()
    artifacts_path = config['artifacts']['artifacts_path']
    
    # Construct full path to markdown file based on report_type
    if report_type == "comparison":
        # Comparison reports: artifacts/comparisons/{comparison_id}/
        markdown_file_path = Path(artifacts_path) / "comparisons" / test_run_id / filename
    else:
        # Single-run reports: artifacts/{test_run_id}/reports/
        markdown_file_path = Path(artifacts_path) / test_run_id / "reports" / filename
    
    title_source = None
    
    # Determine title: user-provided or fallback to extraction
    if title and title.strip():
        # User provided a custom title - validate it
        validation = validate_page_title(title)
        if not validation["valid"]:
            return {
                "error": validation["error"],
                "status": "error",
                "title_provided": title
            }
        title = title.strip()
        title_source = "user_provided"
        await ctx.info(f"Using user-provided title: '{title}'")
    else:
        # Fall back to extracting title from markdown or filename
        try:
            with open(markdown_file_path, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                if first_line.startswith('#'):
                    title = first_line.lstrip('#').strip()
                    title_source = "markdown_h1"
                else:
                    title = Path(filename).stem
                    title_source = "filename"
        except Exception:
            title = Path(filename).stem
            title_source = "filename"
        
        await ctx.info(f"Using auto-extracted title from {title_source}: '{title}'")
    
    # Convert markdown to Confluence XHTML
    xhtml_result = await markdown_to_confluence_xhtml(test_run_id, filename, ctx, report_type)
    
    # Check if conversion failed
    if isinstance(xhtml_result, dict) and "error" in xhtml_result:
        return xhtml_result
    
    storage_xhtml = xhtml_result
    
    # Create page using appropriate API
    if mode == "cloud":
        result = await create_page_v2(space_ref, title, storage_xhtml, ctx, parent_id)
    else:
        result = await create_page_v1(space_ref, title, storage_xhtml, ctx, parent_id)
    
    # Add title_source to the result for transparency
    if result.get("status") == "created":
        result["title_source"] = title_source
    
    return result

@mcp.tool
async def attach_images(page_ref: str, test_run_id: str, mode: str, ctx: Context, report_type: str = "single_run") -> dict:
    """
    Attaches all PNG chart images from a test run to an existing Confluence page.
    
    Uploads all PNG files from the appropriate charts folder to the specified page.
    Continues on partial failures and reports which images succeeded/failed.
    
    Args:
        page_ref (str): Page ID to attach images to (from create_page or list_pages).
        test_run_id (str): Test run ID or comparison_id whose charts to upload.
        mode (str): "cloud" or "onprem".
        ctx (Context): FastMCP context for chaining/error handling.
        report_type (str): Type of report - "single_run" (default) or "comparison".
            - "single_run": Path is artifacts/{test_run_id}/charts/
            - "comparison": Path is artifacts/comparisons/{test_run_id}/charts/
    
    Returns:
        dict containing:
            - 'page_ref': Target page ID
            - 'test_run_id': Test run ID or comparison_id
            - 'attached': List of successfully attached images with details
            - 'failed': List of failed attachments with error details
            - 'total_attempted': Total number of files attempted
            - 'total_attached': Number of files successfully attached
            - 'status': "success" (all attached), "partial" (some failed), or "error" (all failed)
    
    Example:
        # Single-run charts
        result = await attach_images(
            page_ref="123456789",
            test_run_id="80593110",
            mode="onprem",
            report_type="single_run",
            ctx=ctx
        )
        
        # Comparison charts (test_run_id is comparison_id)
        result = await attach_images(
            page_ref="123456789",
            test_run_id="2026-01-21-09-03-30",
            mode="onprem",
            report_type="comparison",
            ctx=ctx
        )
    """
    from pathlib import Path
    from utils.config import load_config
    
    # Load artifacts path from config
    config = load_config()
    artifacts_path = Path(config['artifacts']['artifacts_path'])
    
    # Construct path to charts folder based on report_type
    if report_type == "comparison":
        # Comparison charts: artifacts/comparisons/{comparison_id}/charts/
        charts_folder = artifacts_path / "comparisons" / test_run_id / "charts"
    else:
        # Single-run charts: artifacts/{test_run_id}/charts/
        charts_folder = artifacts_path / test_run_id / "charts"
    
    if not charts_folder.exists():
        error_msg = f"Charts folder not found: {charts_folder}"
        return {
            "error": error_msg,
            "page_ref": page_ref,
            "test_run_id": test_run_id,
            "status": "error"
        }
    
    # Find all PNG files in the charts folder
    png_files = list(charts_folder.glob("*.png"))
    
    if not png_files:
        error_msg = f"No PNG files found in: {charts_folder}"
        return {
            "page_ref": page_ref,
            "test_run_id": test_run_id,
            "attached": [],
            "failed": [],
            "total_attempted": 0,
            "total_attached": 0,
            "status": "error",
            "message": error_msg
        }
    
    await ctx.info(f"Found {len(png_files)} PNG files to attach in {charts_folder}")
    
    # Select the appropriate attach function based on mode
    attach_func = attach_file_v2 if mode == "cloud" else attach_file_v1
    
    attached = []
    failed = []
    
    # Attach each file, continuing on errors
    for png_file in png_files:
        try:
            result = await attach_func(page_ref, str(png_file), ctx)
            
            if result.get("status") == "attached":
                attached.append(result)
            else:
                failed.append(result)
                
        except Exception as e:
            error_msg = f"Unexpected error attaching {png_file.name}: {str(e)}"
            await ctx.error(error_msg)
            failed.append({
                "filename": png_file.name,
                "error": error_msg,
                "status": "error"
            })
    
    # Determine overall status
    total_attempted = len(png_files)
    total_attached = len(attached)
    
    if total_attached == total_attempted:
        status = "success"
    elif total_attached > 0:
        status = "partial"
    else:
        status = "error"
    
    await ctx.info(f"Attachment complete: {total_attached}/{total_attempted} images attached to page {page_ref}")
    
    return {
        "page_ref": page_ref,
        "test_run_id": test_run_id,
        "attached": attached,
        "failed": failed,
        "total_attempted": total_attempted,
        "total_attached": total_attached,
        "status": status
    }


@mcp.tool
async def update_page(page_ref: str, test_run_id: str, mode: str, ctx: Context, use_revised: bool = False, report_type: str = "single_run") -> dict:
    """
    Updates a Confluence page by replacing chart placeholders with embedded images.
    
    This tool:
    1. Reads the XHTML file from the appropriate reports folder
    2. Scans the appropriate charts folder for PNG files
    3. Builds a chart mapping (chart_id -> filename + height from chart_schema.yaml)
    4. Replaces {{CHART_PLACEHOLDER: ID}} markers with Confluence ac:image XHTML
    5. Saves a *_with_images.xhtml file for reference
    6. Updates the Confluence page with the new content
    
    IMPORTANT: Call attach_images BEFORE update_page to ensure images are uploaded.
    
    Note: This tool fetches the current page version before updating, so manual edits
    between create_page and update_page won't cause version conflicts.
    
    Args:
        page_ref (str): Page ID to update (from create_page).
        test_run_id (str): Test run ID or comparison_id whose XHTML and charts to use.
        mode (str): "cloud" or "onprem".
        ctx (Context): FastMCP context.
        use_revised (bool): If True, use the AI-revised XHTML file (*_revised.xhtml).
            If False (default), use the main report XHTML.
            This allows publishing revised content to an existing page.
        report_type (str): Type of report - "single_run" (default) or "comparison".
            - "single_run": Paths are artifacts/{test_run_id}/reports/ and charts/
            - "comparison": Paths are artifacts/comparisons/{test_run_id}/ (root and charts/)
    
    Returns:
        dict containing:
            - page_ref: Updated page ID
            - test_run_id: Test run ID or comparison_id
            - xhtml_source: Which XHTML file was used (filename only)
            - used_revised: Whether the revised XHTML was used
            - xhtml_path: Path to the *_with_images.xhtml file
            - placeholders_replaced: List of chart IDs that were replaced
            - placeholders_remaining: List of chart IDs that had no matching image
            - version: New page version number
            - url: Page URL
            - status: "updated", "partial" (some placeholders not replaced), or "error"
    
    Example workflow:
        # Single-run report - Initial publish
        1. create_page(..., report_type="single_run") -> page_ref
        2. attach_images(page_ref, test_run_id, mode, ctx, report_type="single_run")
        3. update_page(page_ref, test_run_id, mode, ctx, report_type="single_run")
        
        # Single-run report - Publish AI-revised content
        4. convert_markdown_to_xhtml(test_run_id, "performance_report_{id}_revised.md")
        5. update_page(page_ref, test_run_id, mode, ctx, use_revised=True, report_type="single_run")
        
        # Comparison report (test_run_id is comparison_id)
        1. create_page(..., report_type="comparison") -> page_ref
        2. attach_images(page_ref, comparison_id, mode, ctx, report_type="comparison")
        3. update_page(page_ref, comparison_id, mode, ctx, report_type="comparison")
        
        # Comparison report - Publish AI-revised content
        4. convert_markdown_to_xhtml(comparison_id, "comparison_report_{ids}_revised.md", report_type="comparison")
        5. update_page(page_ref, comparison_id, mode, ctx, use_revised=True, report_type="comparison")
    """
    from pathlib import Path
    import re
    import yaml
    from utils.config import load_config
    
    # Load config
    config = load_config()
    artifacts_path = Path(config['artifacts']['artifacts_path'])
    
    # Paths based on report_type
    if report_type == "comparison":
        # Comparison: artifacts/comparisons/{comparison_id}/ (XHTML in root, charts in charts/)
        reports_folder = artifacts_path / "comparisons" / test_run_id
        charts_folder = artifacts_path / "comparisons" / test_run_id / "charts"
    else:
        # Single-run: artifacts/{test_run_id}/reports/ and charts/
        reports_folder = artifacts_path / test_run_id / "reports"
        charts_folder = artifacts_path / test_run_id / "charts"
    
    # Find the appropriate XHTML file based on use_revised flag
    if use_revised:
        # Look for revised XHTML (*_revised.xhtml, excluding *_with_images.xhtml)
        revised_files = [f for f in reports_folder.glob("*_revised.xhtml") 
                         if not f.name.endswith("_with_images.xhtml")]
        
        if not revised_files:
            error_msg = (
                f"No revised XHTML found in {reports_folder}. "
                "Run convert_markdown_to_xhtml on the revised report first."
            )
            await ctx.error(error_msg)
            return {"error": error_msg, "status": "error", "page_ref": page_ref, "test_run_id": test_run_id}
        
        xhtml_file = revised_files[0]
        await ctx.info(f"Using revised XHTML: {xhtml_file.name}")
    else:
        # Look for main (non-revised, non-backup) XHTML
        exclude_patterns = ['_original.xhtml', '_revised.xhtml', '_with_images.xhtml']
        main_files = [
            f for f in reports_folder.glob("*.xhtml")
            if not any(f.name.endswith(p) for p in exclude_patterns)
        ]
        
        if not main_files:
            # Fallback: check if revised exists
            revised_files = [f for f in reports_folder.glob("*_revised.xhtml")
                            if not f.name.endswith("_with_images.xhtml")]
            
            if revised_files:
                xhtml_file = revised_files[0]
                await ctx.warning(f"No main XHTML found, using revised: {xhtml_file.name}")
            else:
                error_msg = f"No XHTML file found in: {reports_folder}"
                return {"error": error_msg, "status": "error", "page_ref": page_ref, "test_run_id": test_run_id}
        elif len(main_files) > 1:
            # Multiple main files - this shouldn't happen but handle gracefully
            file_list = ", ".join(f.name for f in main_files)
            await ctx.warning(f"Multiple XHTML files found, using first: {file_list}")
            xhtml_file = main_files[0]
        else:
            xhtml_file = main_files[0]
        
        await ctx.info(f"Using XHTML: {xhtml_file.name}")
    
    # Read the XHTML content
    try:
        with open(xhtml_file, 'r', encoding='utf-8') as f:
            xhtml_content = f.read()
    except Exception as e:
        error_msg = f"Failed to read XHTML file: {e}"
        return {"error": error_msg, "status": "error", "page_ref": page_ref, "test_run_id": test_run_id}
    
    # Load chart schema for heights
    chart_schema_path = Path(__file__).parent.parent / "perfreport-mcp" / "chart_schema.yaml"
    chart_heights = {}
    default_height = 250
    
    try:
        if chart_schema_path.exists():
            with open(chart_schema_path, 'r', encoding='utf-8') as f:
                schema = yaml.safe_load(f)
            
            # Get default height
            default_height = schema.get('defaults', {}).get('confluence', {}).get('image_height', 250)
            
            # Build chart_id -> height mapping
            for chart in schema.get('charts', []):
                chart_id = chart.get('id')
                if chart_id:
                    # Check for chart-specific confluence height
                    height = chart.get('confluence', {}).get('image_height', default_height)
                    chart_heights[chart_id] = height
        else:
            await ctx.warning(f"Chart schema not found at {chart_schema_path}, using default height {default_height}")
    except Exception as e:
        await ctx.warning(f"Failed to load chart schema: {e}, using default height {default_height}")
    
    # Scan charts folder for PNG files and build mapping
    # Values are lists to support multiple per-resource images per chart_id
    # (e.g., CPU_CORES_LINE -> [web.png, worker.png])
    chart_mapping = {}
    if charts_folder.exists():
        for png_file in sorted(charts_folder.glob("*.png")):
            # Extract chart_id from filename
            # Format: CHART_ID.png or CHART_ID-<resource>.png
            stem = png_file.stem
            
            # Try to match against known chart IDs
            # First check for exact match (multi-line charts, performance charts)
            if stem in chart_heights:
                chart_mapping[stem] = [{
                    "filename": png_file.name,
                    "height": chart_heights.get(stem, default_height)
                }]
            else:
                # Check if stem starts with a known chart ID (per-resource charts)
                for chart_id in chart_heights:
                    if stem.startswith(f"{chart_id}-"):
                        if chart_id not in chart_mapping:
                            chart_mapping[chart_id] = []
                        chart_mapping[chart_id].append({
                            "filename": png_file.name,
                            "height": chart_heights.get(chart_id, default_height)
                        })
                        break
    
    await ctx.info(f"Found {len(chart_mapping)} chart mappings: {list(chart_mapping.keys())}")
    
    # Find all placeholders in the XHTML
    placeholder_pattern = r'\{\{CHART_PLACEHOLDER:\s*([A-Z0-9_]+)\s*\}\}'
    all_placeholders = set(re.findall(placeholder_pattern, xhtml_content))
    
    # Replace placeholders with image XHTML
    xhtml_with_images = replace_chart_placeholders(xhtml_content, chart_mapping, default_height)
    
    # Determine which placeholders were replaced
    remaining_placeholders = set(re.findall(placeholder_pattern, xhtml_with_images))
    replaced_placeholders = all_placeholders - remaining_placeholders
    
    await ctx.info(f"Placeholders replaced: {list(replaced_placeholders)}")
    if remaining_placeholders:
        await ctx.warning(f"Placeholders without images: {list(remaining_placeholders)}")
    
    # Save the _with_images.xhtml file
    output_path = xhtml_file.with_name(xhtml_file.stem + "_with_images.xhtml")
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xhtml_with_images)
        await ctx.info(f"Saved XHTML with images to: {output_path}")
    except Exception as e:
        await ctx.warning(f"Failed to save _with_images.xhtml: {e}")
    
    # Flatten XHTML for API submission
    flattened_xhtml = _flatten_xhtml(xhtml_with_images)
    
    # Update the page
    if mode == "cloud":
        result = await update_page_v2(page_ref, flattened_xhtml, ctx)
    else:
        result = await update_page_v1(page_ref, flattened_xhtml, ctx)
    
    # Determine overall status
    if result.get("status") == "error":
        status = "error"
    elif remaining_placeholders:
        status = "partial"
    else:
        status = "updated"
    
    return {
        "page_ref": page_ref,
        "test_run_id": test_run_id,
        "xhtml_source": xhtml_file.name,
        "used_revised": use_revised,
        "xhtml_path": str(output_path),
        "placeholders_replaced": list(replaced_placeholders),
        "placeholders_remaining": list(remaining_placeholders),
        "version": result.get("version"),
        "url": result.get("url"),
        "status": status,
        "update_result": result
    }


@mcp.tool()
async def get_available_reports(test_run_id: str = None, ctx: Context = None) -> list:
    """
    Lists available Markdown performance reports that can be published to Confluence.
    
    Args:
        test_run_id: Test run ID for single-run reports.
                     Use None or "comparisons" to list comparison reports.
        ctx: FastMCP context.
    
    Returns:
        List of report metadata dicts with filename, type, and test run IDs.
    
    Examples:
        # List single-run reports for a specific test
        get_available_reports(test_run_id="80247571")
        
        # List comparison reports (either approach works)
        get_available_reports()
        get_available_reports(test_run_id="comparisons")
    """
    return await list_available_reports(test_run_id, ctx)

@mcp.tool()
async def get_available_charts(test_run_id: str = None, ctx: Context = None) -> list:
    """
    Lists available PNG chart images that can be attached to Confluence pages.
    
    Args:
        test_run_id: Optional test run ID for single-run charts. If omitted, lists comparison charts.
        ctx: FastMCP context.
    
    Returns:
        List of chart metadata dicts with filename, type, and description.
    """
    return await list_available_charts(test_run_id, ctx)

@mcp.tool
async def convert_markdown_to_xhtml(test_run_id: str, filename: str, ctx: Context = None, report_type: str = "single_run") -> str:
    """
    Converts a Markdown performance report to Confluence storage-format XHTML.
    
    Args:
        test_run_id: ID of the test run or comparison_id (used for artifact path).
        filename: Filename of the markdown report. NOTE: full path is constructed internally. Get list from get_available_reports.
        ctx: FastMCP context for error/status reporting.
        report_type: Type of report - "single_run" (default) or "comparison".
            - "single_run": Path is artifacts/{test_run_id}/reports/
            - "comparison": Path is artifacts/comparisons/{test_run_id}/
    
    Returns:
        str: Confluence-compatible XHTML markup, ready for page creation or update.
             Returns error dict if conversion fails.
    """
    return await markdown_to_confluence_xhtml(test_run_id, filename, ctx, report_type)

@mcp.tool()
async def search_pages(query: str, mode: str, ctx: Context, space_ref: str = None) -> list:
    """
    Searches for Confluence pages using CQL (Confluence Query Language).
    
    Both on-prem and cloud support powerful CQL queries for searching titles and content.
    
    Args:
        query (str): Search term or phrase (e.g., "performance test", "QA Testing Process").
        mode (str): "cloud" or "onprem".
        ctx (Context): FastMCP context.
        space_ref (str, optional): Limit search to specific space (space key for both v1 and cloud).
    
    Returns:
        List of matching pages with:
            - page_ref: Page ID
            - title: Page title
            - url: Page URL
            - space_key: Space key
            - space_name: Space name
            - excerpt: Search result preview
            - last_modified: Last modification date
    
    Examples:
        - search_pages("performance test", "cloud")
        - search_pages("QA Testing Process", "onprem", space_ref="NPQA")
    """
    if mode == "cloud":
        return await search_content_v2(query, space_ref, ctx)
    else:
        return await search_content_v1(query, space_ref, ctx)

# -----------------------------
# Confluence MCP entry point
# -----------------------------
if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down Confluence MCP...")

