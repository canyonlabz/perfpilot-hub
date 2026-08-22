# JMeter MCP Server Script Generator
# This module generates JMeter JMX files based on network capture JSON files.
from fastmcp import FastMCP, Context        # ✅ FastMCP 3.x import
from typing import Optional, Dict, Any

mcp = FastMCP("jmeter")

#from services.spec_parser import list_test_specs, load_browser_steps
#from services.network_capture import capture_traffic, analyze_traffic
from services.script_generator import generate_jmeter_jmx #validate_jmeter_script
from services.spec_parser import list_test_specs, load_browser_steps  # Load spec files and parse steps
from services.jmeter_runner import (
    run_jmeter_test, 
    stop_running_test,
    list_jmeter_scripts_for_run,
    get_jmeter_realtime_status, 
    generate_aggregate_report_csv,
    #summarize_test_run
)

from services.playwright_adapter import run_playwright_capture_pipeline
from services.playwright_adapter import archive_existing_traces
from services.correlations import analyze_traffic  # New modular package (v0.2.0)
from services.jmeter_log_analyzer import analyze_logs as run_jmeter_log_analysis
from services.jmx_editor import (
    analyze_jmx_file as _analyze_jmx,
    add_jmx_component as _add_jmx_component,
    edit_jmx_component as _edit_jmx_component,
)
from services.jmx.component_registry import list_supported_components as _list_components

# Lazy import: HAR adapter is optional — server must start even if it fails to load (safeguard #1)
try:
    from services.har_adapter import convert_har_to_capture as _har_convert, validate_har_file as _har_validate
    _HAR_ADAPTER_AVAILABLE = True
except Exception:
    _HAR_ADAPTER_AVAILABLE = False

# Lazy import: Swagger/OpenAPI adapter is optional — same safeguard pattern
try:
    from services.swagger_adapter import convert_swagger_to_capture as _swagger_convert, validate_spec_file as _swagger_validate
    _SWAGGER_ADAPTER_AVAILABLE = True
except Exception:
    _SWAGGER_ADAPTER_AVAILABLE = False

# Lazy import: HAR-JMX diff engine is optional — same safeguard pattern
try:
    from services.har_jmx_diffengine import (
        extract_har_entries as _extract_har,
        extract_jmx_samplers as _extract_jmx,
        run_matching as _run_matching,
        analyze_differences as _analyze_diffs,
    )
    from services.helpers.diffengine_report_helpers import (
        build_json_report as _build_json_report,
        save_comparison_report as _save_comparison_report,
    )
    _DIFFENGINE_AVAILABLE = True
except Exception:
    _DIFFENGINE_AVAILABLE = False

# ----------------------------------------------------------
# Browser Automation Helper Tools
# ----------------------------------------------------------

@mcp.tool()
async def archive_playwright_traces(ctx: Context, test_run_id: Optional[str] = None) -> dict:
    """
    Archives existing Playwright trace files before a new browser automation run.
    Moves the entire traces directory to a timestamped backup location.
    
    Args:
        ctx (Context): FastMCP context for state/error details.
        test_run_id (Optional[str]): Optional identifier for logging/context purposes.
    
    Returns:
        dict: {
            "status": "OK" | "NO_ACTION" | "ERROR",
            "message": str,
            "archived_path": str | None,
            "test_run_id": str | None
        }
    """
    _ = ctx  # reserved for future context usage
    try:
        archived_path = archive_existing_traces()
        
        if archived_path:
            return {
                "status": "OK",
                "message": f"Traces archived to: {archived_path}",
                "archived_path": archived_path,
                "test_run_id": test_run_id
            }
        else:
            return {
                "status": "NO_ACTION",
                "message": "No traces to archive (directory empty or doesn't exist)",
                "archived_path": None,
                "test_run_id": test_run_id
            }
    
    except PermissionError as e:
        return {
            "status": "ERROR",
            "message": f"Permission denied when archiving traces: {e}",
            "archived_path": None,
            "test_run_id": test_run_id
        }
    except OSError as e:
        return {
            "status": "ERROR",
            "message": f"OS error when archiving traces: {e}",
            "archived_path": None,
            "test_run_id": test_run_id
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Unexpected error archiving traces: {e}",
            "archived_path": None,
            "test_run_id": test_run_id
        }

@mcp.tool()
async def get_test_specs(ctx: Context, test_run_id: Optional[str] = None) -> dict:
    """
    Discovers available Markdown browser automation specs in the 'test-specs/' directory.
    Args:
        test_run_id (Optional[str]): Unique identifier for the test run. If provided, also searches artifacts/<test_run_id>/test-specs/.
        ctx (Context, optional): FastMCP context for state/error details.

    Returns: dict of spec file names and metadata.
    """
    _ = ctx  # reserved for future context usage
    try:
        result = list_test_specs(test_run_id)
        return result
    except Exception as exc:
        return {
            "status": "ERROR",
            "message": str(exc),
            "count": 0,
            "specs": [],
            "roots_scanned": [],
        }

@mcp.tool()
async def get_browser_steps(test_run_id: str, filename: str, ctx: Context) -> list:
    """
    Loads a given Markdown file containing browser automation test steps (supports 'Steps' and 'Test Cases / Test Steps').
    Args:
        test_run_id (str): Unique identifier for the test run.
        filename (str): Relative path to a test-specs Markdown file.
        ctx (Context, optional): FastMCP context for state/error details.
    
    Returns: List of browser automation steps parsed from the spec file.
    """
    return load_browser_steps(test_run_id, filename, ctx)

@mcp.tool()
async def capture_network_traffic(test_run_id: str, spec_file: str, ctx: Context) -> dict:
    """
    Parses Playwright network traces and maps them to test steps from a spec file.
    
    This tool reads the latest Playwright trace from .playwright-mcp/traces/,
    correlates network requests with steps defined in the spec file, and outputs
    a structured JSON file for JMeter script generation.
    
    Args:
        test_run_id (str): Unique identifier for the test run (used for output path).
        spec_file (str): Full path to the spec file.
        ctx (Context, optional): FastMCP context for state/error details.
    
    Returns: dict with artifact path(s), status, and any errors.
    """
    try:
        output_path = run_playwright_capture_pipeline(spec_file, test_run_id)
        return {
            "status": "OK",
            "message": f"Network capture saved to: {output_path}",
            "test_run_id": test_run_id,
            "spec_file": spec_file,
            "output_path": output_path,
            "error": None
        }
    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "spec_file": spec_file,
            "output_path": None,
            "error": str(e)
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Error during network capture: {e}",
            "test_run_id": test_run_id,
            "spec_file": spec_file,
            "output_path": None,
            "error": str(e)
        }

@mcp.tool()
async def convert_har_to_capture(
    test_run_id: str,
    har_path: str,
    ctx: Context,
    step_strategy: str = "auto",
    time_gap_threshold_ms: int = 3000,
) -> dict:
    """
    Convert a HAR (HTTP Archive) file to network capture JSON format.

    Use this instead of capture_network_traffic when you have a HAR file
    from Chrome DevTools, a proxy tool (Charles, Fiddler, mitmproxy),
    or Postman. The output JSON feeds directly into analyze_network_traffic
    and generate_jmeter_script.

    Args:
        test_run_id (str): Unique identifier for the test run.
        har_path (str): Full path to the HAR file.
        ctx (Context): FastMCP context for logging.
        step_strategy (str): How to group entries into steps
            (auto/page/time_gap/single_step). Default: auto.
        time_gap_threshold_ms (int): Gap threshold for time_gap strategy.
            Default: 3000ms.

    Returns:
        dict: {
            "status": "OK" | "ERROR",
            "message": str,
            "test_run_id": str,
            "network_capture_path": str | None,
            "error": str | None
        }
    """
    _ = ctx  # reserved for future context usage
    if not _HAR_ADAPTER_AVAILABLE:
        return {
            "status": "ERROR",
            "message": "HAR adapter is not available. Check server logs for import errors.",
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": "HAR adapter failed to load",
        }
    try:
        output_path = _har_convert(
            har_path=har_path,
            test_run_id=test_run_id,
            step_strategy=step_strategy,
            time_gap_threshold_ms=time_gap_threshold_ms,
        )
        return {
            "status": "OK",
            "message": f"HAR file converted. Network capture saved to: {output_path}",
            "test_run_id": test_run_id,
            "network_capture_path": output_path,
            "error": None,
        }
    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": str(e),
        }
    except ValueError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": str(e),
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Unexpected error converting HAR file: {e}",
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": str(e),
        }

@mcp.tool()
async def convert_swagger_to_capture(
    test_run_id: str,
    spec_path: str,
    ctx: Context,
    base_url: str = "",
    step_strategy: str = "tag",
    include_deprecated: bool = False,
) -> dict:
    """
    Convert a Swagger 2.x / OpenAPI 3.x spec file to network capture JSON format.

    Use this when you have an API specification file (JSON or YAML) and want
    to generate a synthetic network capture for JMeter script generation.
    The output JSON feeds directly into analyze_network_traffic and
    generate_jmeter_script.

    Args:
        test_run_id (str): Unique identifier for the test run.
        spec_path (str): Full path to the Swagger/OpenAPI spec file.
        ctx (Context): FastMCP context for logging.
        base_url (str): Base URL for the API. Required when the spec has
            a relative server URL (e.g., 'https://api.example.com/file-svc').
        step_strategy (str): How to group endpoints into steps
            (tag/path/single_step). Default: tag.
        include_deprecated (bool): Whether to include deprecated endpoints.
            Default: False.

    Returns:
        dict: {
            "status": "OK" | "ERROR",
            "message": str,
            "test_run_id": str,
            "network_capture_path": str | None,
            "error": str | None
        }
    """
    _ = ctx  # reserved for future context usage
    if not _SWAGGER_ADAPTER_AVAILABLE:
        return {
            "status": "ERROR",
            "message": "Swagger adapter is not available. Check server logs for import errors.",
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": "Swagger adapter failed to load",
        }
    try:
        output_path = _swagger_convert(
            spec_path=spec_path,
            test_run_id=test_run_id,
            base_url=base_url,
            step_strategy=step_strategy,
            include_deprecated=include_deprecated,
        )
        return {
            "status": "OK",
            "message": f"OpenAPI spec converted. Network capture saved to: {output_path}",
            "test_run_id": test_run_id,
            "network_capture_path": output_path,
            "error": None,
        }
    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": str(e),
        }
    except ValueError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": str(e),
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Unexpected error converting spec file: {e}",
            "test_run_id": test_run_id,
            "network_capture_path": None,
            "error": str(e),
        }

@mcp.tool()
async def analyze_network_traffic(test_run_id: str, ctx: Context) -> dict:
    """
    Analyzes network traffic data, extracting test request metadata/stats
    and potential correlations, and writes correlation_spec.json.

    Args:
        test_run_id (str): Unique identifier for the test run.
        ctx (Context, optional): FastMCP context for state/error details.
        
    Returns: dict with extracted stats, request/response mappings, discovered correlations.
    """
    return await analyze_traffic(test_run_id, ctx)

# ----------------------------------------------------------
# JMeter JMX Generation
# ----------------------------------------------------------

@mcp.tool()
async def generate_jmeter_script(test_run_id: str, json_path: str, ctx: Context) -> dict:
    """
    Generate a JMeter JMX script from the structured JSON or HAR output.
    Args:
        test_run_id (str): Unique identifier for the test run.
        json_path (str): Path to the JSON (network capture, steps, etc.)
        ctx (Context, optional): Optional FastMCP workflow context for state/error details.
    
    Returns:
        dict: Includes output JMX path, mapping info, warnings, and errors (if any).
    """
    return await generate_jmeter_jmx(test_run_id, json_path, ctx)

# ----------------------------------------------------------
# JMeter HITL (Human-in-the-Loop) Script Editing Tools
# ----------------------------------------------------------

@mcp.tool()
async def analyze_jmeter_script(
    test_run_id: str,
    ctx: Context,
    jmx_filename: str = "",
    detail_level: str = "summary",
    export_structure: bool = True,
    output_format: str = "both",
) -> dict:
    """
    Analyze an existing JMeter JMX script to understand its structure,
    components, and configuration.

    Returns a summary-only response with element counts, a text outline,
    and paths to exported structure files. The full hierarchy, node_index,
    and variables are NOT included in the response — they are persisted to
    versioned JSON/Markdown files under artifacts/<test_run_id>/jmeter/analysis/.

    To obtain node_ids for add/edit operations, read the exported JSON file
    at the path returned in exported_files.json. This avoids consuming the
    AI agent's context window with large script structures.

    Args:
        test_run_id (str): Unique identifier for the test run.
        ctx (Context): FastMCP context for state/error details.
        jmx_filename (str): Optional JMX filename inside the artifacts folder.
            If empty, auto-discovers the most recent ai-generated script.
        detail_level (str): Controls what is included in the exported files:
            - "summary" (default): Hierarchy outline + element counts. When
              export_structure is True, internally upgrades to "detailed" for
              the exported file so node_index is always available on disk.
            - "detailed": Full node index with node_ids + variable scan.
            - "full": Adds element properties to the node index.
        export_structure (bool): If True (default), persist analysis to
            versioned JSON and/or Markdown files in the artifacts directory.
        output_format (str): File format for exported structure:
            - "json": JSON file only.
            - "markdown": Markdown file only.
            - "both" (default): Both JSON and Markdown.

    Returns:
        dict: {
            "status": "OK" | "ERROR",
            "message": str,
            "test_run_id": str,
            "jmx_path": str,
            "jmx_filename": str,
            "detail_level": str,
            "summary": {"total_elements": int, "by_type": dict},
            "outline": str,
            "exported_files": {"json": str, "markdown": str} (when export_structure=True)
        }

    Note:
        The exported JSON file contains the full hierarchy, node_index,
        and variables. Read it from disk for node lookups:
            exported_files.json -> jmx_structure_<timestamp>.json
    """
    return await _analyze_jmx(
        test_run_id, jmx_filename, detail_level, ctx,
        export_structure=export_structure,
        output_format=output_format,
    )


@mcp.tool()
async def add_jmeter_component(
    test_run_id: str,
    component_type: str,
    parent_node_id: str,
    component_config: dict,
    ctx: Context,
    jmx_filename: str = "",
    position: str = "last",
    dry_run: bool = False,
) -> dict:
    """
    Add a new JMeter component to an existing JMX script.

    Supports adding any registered component type including controllers,
    samplers, config elements, extractors, assertions, pre/post processors,
    and timers. Run analyze_jmeter_script first to obtain the parent_node_id.

    See JMeter Component Reference:
    https://jmeter.apache.org/usermanual/component_reference.html

    Args:
        test_run_id (str): Unique identifier for the test run.
        component_type (str): Type of component to add. Examples:
            "loop_controller", "json_extractor", "jsr223_preprocessor",
            "response_assertion", "constant_timer", "csv_dataset", etc.
        parent_node_id (str): node_id of the parent element where the new
            component will be inserted (from analyze_jmeter_script output).
        component_config (dict): Component configuration including name and
            type-specific properties. Required/optional fields depend on
            the component_type.
        ctx (Context): FastMCP context for state/error details.
        jmx_filename (str): Optional JMX filename override. If empty,
            auto-discovers the most recent ai-generated script.
        position (str): Where to add within the parent's children:
            "first" or "last" (default: "last").
        dry_run (bool): If True, validate and preview the change without
            saving to disk. Default: False.

    Returns:
        dict: {
            "status": "OK" | "ERROR",
            "message": str,
            "test_run_id": str,
            "jmx_path": str,
            "backup_path": str | None,
            "dry_run": bool,
            "change_summary": dict
        }
    """
    return await _add_jmx_component(
        test_run_id, component_type, parent_node_id,
        component_config, jmx_filename, position, dry_run, ctx
    )


@mcp.tool()
async def edit_jmeter_component(
    test_run_id: str,
    target_node_id: str,
    operations: list,
    ctx: Context,
    jmx_filename: str = "",
    dry_run: bool = False,
) -> dict:
    """
    Edit an existing JMeter component in a JMX script.

    Applies one or more patch operations to the targeted component.
    Run analyze_jmeter_script first to obtain the target_node_id.

    Args:
        test_run_id (str): Unique identifier for the test run.
        target_node_id (str): node_id of the component to edit
            (from analyze_jmeter_script output).
        operations (list): List of edit operation dicts. Supported ops:
            - {"op": "rename", "value": "New Name"}
            - {"op": "set_prop", "name": "HTTPSampler.method",
               "value": "POST", "prop_type": "stringProp"}
            - {"op": "replace_in_body", "find": "old_text",
               "replace": "new_text"}
            - {"op": "toggle_enabled", "value": true/false}
        ctx (Context): FastMCP context for state/error details.
        jmx_filename (str): Optional JMX filename override. If empty,
            auto-discovers the most recent ai-generated script.
        dry_run (bool): If True, validate and preview changes without
            saving to disk. Default: False.

    Returns:
        dict: {
            "status": "OK" | "ERROR",
            "message": str,
            "test_run_id": str,
            "jmx_path": str,
            "backup_path": str | None,
            "dry_run": bool,
            "change_summary": dict
        }
    """
    return await _edit_jmx_component(
        test_run_id, target_node_id, operations,
        jmx_filename, dry_run, ctx
    )


@mcp.tool()
async def list_jmeter_component_types(
    ctx: Context,
    category: str = "",
) -> dict:
    """
    List all supported JMeter component types that can be added via
    add_jmeter_component.

    Useful for discovering what components are available and what
    configuration fields each type requires.

    Args:
        ctx (Context): FastMCP context.
        category (str): Optional filter by category. Options:
            "controller", "sampler", "config_element", "pre_processor",
            "post_processor", "assertion", "timer", "listener".
            Empty string returns all categories.

    Returns:
        dict: {
            "status": "OK",
            "components": list of component type descriptors,
            "count": int
        }
    """
    _ = ctx
    cat = category if category else None
    components = _list_components(cat)
    return {
        "status": "OK",
        "components": components,
        "count": len(components),
    }


# ----------------------------------------------------------
# JMeter HAR-JMX Comparison Tools
# ----------------------------------------------------------

@mcp.tool()
async def compare_har_to_jmx(
    test_run_id: str,
    har_file_path: str,
    ctx: Context,
    jmx_file_path: str = "",
    jmx_structure_file: str = "",
    correlation_spec_file: str = "",
    strict_matching: bool = False,
    output_format: str = "both",
) -> dict:
    """
    Cross-compare a HAR file against an existing JMeter JMX script to
    identify API changes that require script updates.

    This tool is diagnostic only — it produces a report of differences but
    does NOT modify the JMX. Use edit_jmeter_component / add_jmeter_component
    to apply fixes based on the report findings.

    The tool runs a four-phase pipeline:
      1. Extract comparison-relevant fields from both HAR and JMX
      2. Multi-pass matching algorithm (exact, parameterized, fuzzy)
      3. Per-match difference analysis across 10 categories
      4. Report generation (JSON for AI consumption, Markdown for humans)

    Args:
        test_run_id (str): Unique identifier for the test run.
        har_file_path (str): Absolute path to the HAR file.
        ctx (Context): FastMCP context for state/error details.
        jmx_file_path (str): Path to the JMX file. If empty, auto-discovers
            via discover_jmx_file.
        jmx_structure_file (str): Path to a jmx_structure_*.json file from
            analyze_jmeter_script. If provided and fresh, speeds up JMX
            parsing. If empty, parses the JMX from scratch.
        correlation_spec_file (str): Path to correlation_spec.json for richer
            correlation drift detection. Optional.
        strict_matching (bool): When True, disables Pass 3 (fuzzy path-segment
            matching) to reduce false positives. Default: False.
        output_format (str): File format for the comparison report:
            - "json": JSON file only.
            - "markdown": Markdown file only.
            - "both" (default): Both JSON and Markdown.

    Returns:
        dict: {
            "status": "OK" | "ERROR",
            "message": str,
            "test_run_id": str,
            "har_file": str,
            "jmx_file": str,
            "summary": dict (category counts),
            "match_stats": dict (per-pass counts and confidence breakdown),
            "exported_files": {"json": str, "markdown": str},
            "error": str | None
        }
    """
    _ = ctx
    if not _DIFFENGINE_AVAILABLE:
        return {
            "status": "ERROR",
            "message": (
                "HAR-JMX diff engine is not available. "
                "Check server logs for import errors."
            ),
            "test_run_id": test_run_id,
            "error": "Diff engine failed to load",
        }

    try:
        import json as _json
        from services.jmx_editor import discover_jmx_file as _discover_jmx_file

        # --- Phase A: Extraction ---
        har_entries, har_metadata = _extract_har(har_file_path)

        jmx_path = jmx_file_path
        if not jmx_path:
            jmx_path = _discover_jmx_file(test_run_id)

        jmx_struct = jmx_structure_file if jmx_structure_file else None
        jmx_samplers, jmx_metadata = _extract_jmx(jmx_path, jmx_struct)

        # --- Phase B: Matching ---
        matching_result = _run_matching(
            har_entries, jmx_samplers, strict_matching=strict_matching,
        )

        # --- Phase C: Difference Analysis ---
        correlation_spec = None
        if correlation_spec_file:
            try:
                with open(correlation_spec_file, "r", encoding="utf-8") as f:
                    raw_spec = _json.load(f)
                if isinstance(raw_spec, list):
                    correlation_spec = {
                        entry.get("refname", entry.get("variable", "")): entry
                        for entry in raw_spec if isinstance(entry, dict)
                    }
                elif isinstance(raw_spec, dict):
                    correlation_spec = raw_spec
            except (OSError, _json.JSONDecodeError) as exc:
                return {
                    "status": "ERROR",
                    "message": f"Failed to load correlation_spec.json: {exc}",
                    "test_run_id": test_run_id,
                    "error": str(exc),
                }

        analysis_result = _analyze_diffs(matching_result, correlation_spec)

        # --- Phase D: Report Generation ---
        report = _build_json_report(
            har_metadata, jmx_metadata, analysis_result,
            jmx_structure_file=jmx_struct,
            strict_matching=strict_matching,
        )

        exported = _save_comparison_report(
            test_run_id, report, output_format=output_format,
        )

        return {
            "status": "OK",
            "message": (
                f"Comparison complete: {analysis_result['match_stats']['total_matched']} "
                f"matched, {analysis_result['match_stats']['new_endpoints']} new, "
                f"{analysis_result['match_stats']['removed_endpoints']} possibly removed"
            ),
            "test_run_id": test_run_id,
            "har_file": har_metadata.get("har_file", ""),
            "jmx_file": jmx_metadata.get("jmx_file", ""),
            "summary": report.get("summary", {}),
            "match_stats": report.get("match_stats", {}),
            "exported_files": exported,
            "error": None,
        }

    except FileNotFoundError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "error": str(e),
        }
    except ValueError as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "test_run_id": test_run_id,
            "error": str(e),
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": f"Unexpected error during HAR-JMX comparison: {e}",
            "test_run_id": test_run_id,
            "error": str(e),
        }


# ----------------------------------------------------------
# JMeter Test Execution Tools
# ----------------------------------------------------------

@mcp.tool()
async def list_jmeter_scripts(test_run_id: str, ctx: Context) -> dict:
    """
    List existing JMeter .jmx scripts for the given test_run_id.

    Looks under:
        <artifacts_root>/<test_run_id>/jmeter

    Args:
        test_run_id (str): Unique identifier for the test run.
        ctx (Context, optional): FastMCP context (currently unused, but reserved).

    Returns:
        dict: {
            "test_run_id": str,
            "artifact_dir": str,
            "scripts": [...],
            "count": int,
            "status": "OK" | "NOT_FOUND" | "EMPTY",
            "message": str
        }
    """
    # Currently no need to use ctx, but we accept it for consistency
    return list_jmeter_scripts_for_run(test_run_id)

@mcp.tool()
async def start_jmeter_test(test_run_id: str, jmx_path: str, ctx: Context) -> dict:
    """
    Execute the JMeter test plan using the given JMX and config, returning summary info and artifacts.
    Args:
        test_run_id (str): Unique identifier for the test run.
        jmx_path (str): Path to the JMX script that should be executed.
        ctx (Context, optional): FastMCP context for tracking state, status, or error reporting.
    
    Returns:
        dict: Test results, artifact paths, timings, and status.
    """
    return await run_jmeter_test(test_run_id, jmx_path, ctx)

@mcp.tool()
async def stop_jmeter_test(test_run_id: str, ctx: Context) -> dict:
    """
    Gracefully stops a running JMeter test session identified by run_id.
    Args:
        test_run_id (str): JMeter/runner session identifier.
        ctx (Context, optional): FastMCP context object.
    
    Returns:
        dict: Stop status, error (if any), and timestamps.
    """
    return await stop_running_test(test_run_id, ctx)

@mcp.tool()
async def get_jmeter_run_status(test_run_id: str, pid: int, ctx: Context) -> dict:
    """
    Return current smoke-test metrics for the given test_run_id by reading its JTL file.

    Intended usage:
      1. Call start_jmeter_test(...)
      2. Poll this tool every few seconds while the smoke test runs
      3. Inspect total samples, error rate, avg, p90, etc.

    Args:
        test_run_id (str): Unique identifier for the test run.
        pid (int): Process ID of the running JMeter test.
        ctx (Context, optional): FastMCP context for tracking state, status, or error reporting.
    Returns:
        dict: Real-time test run metrics and status.
    """
    return get_jmeter_realtime_status(test_run_id, pid)

@mcp.tool(tags={"deprecated"})
async def get_jmeter_run_summary(test_run_id: str, ctx: Context) -> dict:
    """
    Analyzes the test run results and provides high-level summary.
    Args:
        test_run_id (str): Unique identifier for the test run.
        ctx (Context, optional): FastMCP context for tracking state, status, or error reporting.
    Returns:
        dict: Summary of test run results, KPIs, and any errors/warnings.
    """
    ##return await summarize_test_run(test_run_id, ctx)

@mcp.tool()
async def generate_aggregate_report(test_run_id: str, ctx: Context) -> dict:
    """
    Generate a BlazeMeter-style Aggregate Performance Report CSV
    for the given test_run_id.

    This reads the JTL located under:
        <artifacts_root>/<test_run_id>/jmeter/<test_run_id>.jtl
    and writes:
        <artifacts_root>/<test_run_id>/jmeter/<test_run_id>_aggregate_report.csv

    Returns:
        dict with:
          - test_run_id
          - status: "OK" | "NO_JTL"
          - aggregate_report_path
          - label_count
    """
    _ = ctx  # currently unused, reserved for future context/state
    return generate_aggregate_report_csv(test_run_id)

# ----------------------------------------------------------
# JMeter Log Analysis Tools
# ----------------------------------------------------------

@mcp.tool()
async def analyze_jmeter_log(test_run_id: str, ctx: Context, log_source: str = "blazemeter") -> dict:
    """
    Analyze JMeter or BlazeMeter log files for a given test run.

    Performs deep analysis of all .log files under the specified source folder,
    identifying errors, grouping them by type/API/root cause, capturing first-
    occurrence request/response details, and optionally correlating with JTL data.

    Outputs three files to artifacts/<test_run_id>/analysis/:
      - <log_source>_log_analysis.csv  (all issues in tabular form)
      - <log_source>_log_analysis.json (metadata + summary + full issue list)
      - <log_source>_log_analysis.md   (human-readable report)

    Args:
        test_run_id (str): Unique identifier for the test run.
        ctx (Context): FastMCP context for state/error details.
        log_source (str): Which log folder to analyze — "jmeter" or "blazemeter".
            Defaults to "blazemeter".

    Returns:
        dict: {
            "test_run_id": str,
            "log_source": str,
            "status": "OK" | "NO_LOGS" | "ERROR",
            "log_files_analyzed": list[str],
            "jtl_file_analyzed": str | None,
            "total_issues": int,
            "total_occurrences": int,
            "issues_by_severity": dict,
            "output_files": {"csv": str, "json": str, "markdown": str},
            "message": str
        }
    """
    _ = ctx  # reserved for future context usage
    try:
        return run_jmeter_log_analysis(test_run_id, log_source)
    except Exception as e:
        return {
            "test_run_id": test_run_id,
            "log_source": log_source,
            "status": "ERROR",
            "message": f"Unexpected error during log analysis: {e}",
        }

# -----------------------------
# Visibility: hide deprecated tools
# -----------------------------
mcp.disable(tags={"deprecated"})

# -----------------------------
# JMeter MCP entry point
# -----------------------------
if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down JMeter MCP…")
