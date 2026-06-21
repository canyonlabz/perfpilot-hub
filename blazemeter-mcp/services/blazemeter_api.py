# services/blazemeter_api.py
import os
import httpx
import base64
import time
import zipfile
import shutil
import json
import csv
from datetime import datetime
from typing import Dict, Any, List, Union
from dotenv import load_dotenv
from fastmcp import FastMCP, Context        # ✅ FastMCP 3.x import
from utils.config import load_config, get_cleanup_session_folders, get_shared_folder_allowed_extensions
from utils.file_utils import write_public_report_json
from services.artifact_manager import (
    get_manifest_path, load_manifest, save_manifest, create_manifest,
    append_jtl_to_csv, download_with_retry,
)

# Load environment variables from .env file such as API keys and secrets
load_dotenv()

# Load the config.yaml which contains path folder settings. NOTE: OS specific yaml files will override default config.yaml
config = load_config()
bz_config = config.get('blazemeter', {})
artifacts_base = config['artifacts']['artifacts_path']

# Default HTTP timeout for BlazeMeter API calls (seconds). Configurable via
# config.yaml -> blazemeter.http_timeout_seconds. Applies to all httpx calls
# that don't have their own explicit timeout (e.g. artifact downloads use 600s).
HTTP_TIMEOUT_SECONDS: float = float(bz_config.get('http_timeout_seconds', 60))

BLAZEMETER_API_KEY = os.getenv("BLAZEMETER_API_KEY")
BLAZEMETER_API_SECRET = os.getenv("BLAZEMETER_API_SECRET")
BLAZEMETER_ACCOUNT_ID = os.getenv("BLAZEMETER_ACCOUNT_ID")
BLAZEMETER_WORKSPACE_ID = os.getenv("BLAZEMETER_WORKSPACE_ID")
BLAZEMETER_API_BASE = "https://a.blazemeter.com/api/v4"

# CA bundle path for SSL verification
CA_BUNDLE = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")

# ===============================================
# Helper Functions
# ===============================================

def get_headers(extra: dict = None):
    # Basic Auth header BlazeMeter expects
    auth = base64.b64encode(f"{BLAZEMETER_API_KEY}:{BLAZEMETER_API_SECRET}".encode()).decode()
    h = {
        "Authorization": f"Basic {auth}",
    }
    if extra:
        h.update(extra)
    return h

def format_timestamp(ts: str) -> str:
    """Convert BlazeMeter ISO timestamp to readable format."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

def to_epoch(dt_str: str) -> int:
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            dt = datetime.strptime(dt_str, fmt)
            return int(time.mktime(dt.timetuple()))
        except Exception:
            continue
    raise ValueError(f"Invalid date format: {dt_str}. Expected 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD'.")

def epoch_to_timestamp(epoch: int) -> str:
    if epoch is None:
        return None
    return datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S UTC")

def format_duration(seconds: int) -> str:
    if seconds is None:
        return "N/A"
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec}s" if minutes else f"{sec}s"

def write_test_config_json(run_id: str, summary_fields: dict) -> str:
    """
    Writes test_config.json to the proper artifacts path for the given run.
    Returns the path to the JSON file or an error message.
    """
    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter")
    os.makedirs(dest_folder, exist_ok=True)
    config_path = os.path.join(dest_folder, "test_config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(summary_fields, f, indent=2)
        return config_path
    except Exception as e:
        return f"❗ Error writing test_config.json: {e}"

# ===============================================
# Main API Functions for the BlazeMeter MCP
# ===============================================

async def list_workspaces() -> str:
    """List BlazeMeter workspaces for the configured account."""
    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = await client.get(f"{BLAZEMETER_API_BASE}/workspaces?accountId={BLAZEMETER_ACCOUNT_ID}", headers=get_headers())
        resp.raise_for_status()
        workspaces = resp.json()["result"]
        return "\n".join(f"{ws['id']}: {ws['name']}" for ws in workspaces)

async def list_projects(workspace_id: str | None = None, project_name: str | None = None) -> str:
    """List BlazeMeter projects for a workspace.

    Args:
        workspace_id: Optional explicit workspace ID. If omitted/None/empty, falls back to env `BLAZEMETER_WORKSPACE_ID`.
        project_name: Optional filter to match a specific project name.

    Returns:
        Newline separated string of `id: name` entries, or an informative message if none found / error.
    """
    # Fallback to globally configured workspace id if not provided explicitly
    workspace_id = workspace_id or BLAZEMETER_WORKSPACE_ID
    if not workspace_id:
        return "❗ workspace_id not provided and BLAZEMETER_WORKSPACE_ID is not set in the environment."
    
    # Get pagination limit from config or default to 100
    pagination_limit = bz_config.get('pagination_limit', 100)

    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        url = f"{BLAZEMETER_API_BASE}/projects?workspaceId={workspace_id}&limit={pagination_limit}"
        if project_name:
            url += f"&name={project_name}"
        resp = await client.get(url, headers=get_headers())
        resp.raise_for_status()
        projects = resp.json()["result"]
        
        if not projects and project_name:
            return f"No projects found matching '{project_name}'"
        
        return "\n".join(f"{p['id']}: {p['name']}" for p in projects)

async def list_tests(project_id: str) -> str:
    """List BlazeMeter tests for a given project ID."""
    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        url = f"{BLAZEMETER_API_BASE}/tests?projectId={project_id}"
        resp = await client.get(url, headers=get_headers({"Content-Type": "application/json"}))
        resp.raise_for_status()
        tests = resp.json()["result"]
        return "\n".join(f"{t['id']}: {t['name']}" for t in tests)

async def run_test(test_id: str, ctx: Context) -> str:
    """
    Starts a BlazeMeter test run and stores the run_id in context for workflow chaining.
    Args:
        test_id: The BlazeMeter test ID.
        ctx (Context, optional): FastMCP context object for state management.
    Returns:
        String with created run ID and status.
    """
    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        url = f"{BLAZEMETER_API_BASE}/tests/{test_id}/start?delayedStart=false"
        resp = await client.post(url, headers=get_headers({"Content-Type": "application/json"}))
        resp.raise_for_status()
        result = resp.json()["result"]
        run_id = result['id']
        # Optionally store run_id in context for downstream tools
        if ctx is not None:
            await ctx.set_state("run_id", run_id)
        return f"Run started. Run ID: {run_id}"

async def get_test_status(run_id: str, ctx: Context) -> dict:
    """
    Retrieves the current status and status breakdown for the given BlazeMeter run.

    Args:
        run_id: The BlazeMeter master/run ID.
        ctx (Context, optional): FastMCP workflow context for state passing.

    Returns:
        Dictionary with keys:
            - run_id: Run/master ID
            - status: Main status string (e.g. 'ENDED', 'RUNNING', etc.)
            - statuses: Breakdown of session states (pending, booting, ready, ended)
            - error: Error object/string/null (if present in API response)
            - has_error: True if error or failed/aborted state detected, else False
            - context: Updated workflow context (if used)
    """
    url = f"{BLAZEMETER_API_BASE}/masters/{run_id}/status"
    try:
        verify_ssl = get_ssl_verify_setting()
        async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=get_headers())
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            status = result.get("status", "UNKNOWN")
            statuses = result.get("statuses", {})
            error = data.get("error")
            has_error = bool(error) or (status.upper() in {"FAILED", "ERROR", "ABORTED"})
            # Save to context for workflow chaining
            if ctx is not None:
                await ctx.set_state("last_status", status)
                await ctx.set_state("statuses", statuses)
                await ctx.set_state("has_error", has_error)
            return {
                "run_id": run_id,
                "status": status,
                "statuses": statuses,
                "error": error,
                "has_error": has_error
            }
    except Exception as e:
        if ctx is not None:
            await ctx.set_state("last_status", "ERROR")
            await ctx.set_state("error", str(e))
            await ctx.set_state("has_error", True)
            await ctx.error(f"Error retrieving test status: {e}")
        return {
            "run_id": run_id,
            "status": "ERROR",
            "statuses": {},
            "error": str(e),
            "has_error": True
        }

async def get_results_summary(run_id: str, ctx: Context) -> str:
    """
    Fetch and format a summary report for the BlazeMeter test run, merging
    fields from both 'master' details and 'summary statistics' endpoints.
    Also writes test_config.json with key run and test config metadata.

    Args:
        run_id: The BlazeMeter master/run ID.
        ctx (Context, optional): FastMCP workflow context for caching or chaining summary.

    Returns:
        A pretty-printed, human-friendly test summary, or error details if retrieval fails.
        Writes test_config.json for downstream analysis. Updates context with summary if present.
    """

    # Prepare results for later combination
    master = {}
    summary = {}
    summary_fields = {}
    config_fields = {}

    try:
        verify_ssl = get_ssl_verify_setting()
        async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
            # 1. Fetch main test run (master) info
            master_url = f"{BLAZEMETER_API_BASE}/masters/{run_id}"
            master_resp = await client.get(master_url, headers=get_headers(), timeout=30.0)
            master_resp.raise_for_status()
            master = master_resp.json().get("result", {})

            # 2. Fetch summary statistics (aggregated metrics per run)
            summary_url = f"{BLAZEMETER_API_BASE}/masters/{run_id}/reports/default/summary"
            summary_resp = await client.get(summary_url, headers=get_headers(), timeout=30.0)
            summary_resp.raise_for_status()
            summary_data = summary_resp.json().get("result", {})

            # There may be a "summary" array (per doc); pick the overall summary.
            summary_list = summary_data.get("summary", [])
            summary = summary_list[0] if summary_list else {}

    except httpx.HTTPStatusError as he:
        return f"❗ Error: BlazeMeter API request failed ({he.response.status_code})\nDetails: {he}"
    except Exception as e:
        return f"❗ Error: Could not fetch summary for run {run_id}.\nDetails: {e}"

    if not master or not summary:
        return f"⚠️ No results available for run ID {run_id} (master or summary empty)."

    # Safely extract key fields
    test_id = master.get("testId", "Unknown")
    test_name = master.get("name", "Unknown")
    workspace_id = BLAZEMETER_WORKSPACE_ID if BLAZEMETER_WORKSPACE_ID else "Workspace ID not found"
    project_id = master.get("projectId", None)
    sessions_id = master.get("sessionsId", [])
    max_virtual_users = summary.get("maxUsers", master.get("maxUsers", "N/A"))
    start_time = epoch_to_timestamp(master.get("created")) if master.get("created") else "N/A"
    end_time = epoch_to_timestamp(master.get("ended")) if master.get("ended") else "N/A"

    # Calculate duration in seconds if possible
    duration_seconds = None
    duration_str = "N/A"
    if start_time and end_time:
        try:
            duration_seconds = int(master.get("ended")) - int(master.get("created"))
            duration_str = format_duration(duration_seconds)
        except Exception:
            pass

    samples_total = summary.get("hits", "N/A")
    error_count = summary.get("failed", "N/A")
    try:
        # Only compute if both fields are int-able
        pass_count = int(samples_total) - int(error_count)
        fail_count = int(error_count)
    except Exception:
        pass_count = "N/A"
        fail_count = error_count

    rt_min = summary.get("min", "N/A")
    rt_max = summary.get("max", "N/A")
    rt_avg = summary.get("avg", "N/A")
    rt_p90 = summary.get("tp90", "N/A")

    # Extract relevant config from first executions[] entry, if present
    executions = master.get("executions", [])
    if executions:
        exec0 = executions[0]
        config_fields = {
            "concurrency": exec0.get("concurrency"),
            "rampUp": exec0.get("rampUp"),
            "steps": exec0.get("steps"),
            "iterations": exec0.get("iterations"),
        }

    # Fill out the test_config.json schema
    summary_fields = {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "test_id": test_id,
        "test_name": test_name,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": duration_seconds,
        "max_virtual_users": max_virtual_users,
        "samples_total": samples_total,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "error_count": error_count,
        "response_time_min_ms": rt_min,
        "response_time_max_ms": rt_max,
        "response_time_avg_ms": rt_avg,
        "response_time_p90_ms": rt_p90,
        "config": config_fields,
        "labels": [lbl.get("name") for lbl in master.get("jetpackLabels", [])] if master.get("jetpackLabels") else None,
        "notes": ""
    }

    test_config_json = write_test_config_json(run_id, summary_fields)

    # Update context with summary for downstream tools
    if ctx is not None:
        await ctx.set_state("summary", summary_fields)
        await ctx.set_state("test_config_json_path", test_config_json)

    report = (
        f"BlazeMeter Test Run Summary\n"
        f"===========================\n"
        f"Test Name: {test_name}\n"
        f"Test ID: {test_id}\n"
        f"Run ID: {run_id}\n\n"
        f"Start Time: {start_time}\n"
        f"End Time: {end_time}\n"
        f"Duration: {duration_str}s\n"
        f"Max Virtual Users: {max_virtual_users}\n\n"
        f"Samples Total: {samples_total}\n"
        f"Pass Count: {pass_count}\n"
        f"Fail Count: {fail_count}\n"
        f"Error Count: {error_count}\n\n"
        f"Response Time (ms):\n"
        f"Session ID: {sessions_id}\n"
        f"  Min: {rt_min}\n"
        f"  Max: {rt_max}\n"
        f"  Avg: {rt_avg}\n"
        f"  90th Percentile: {rt_p90}\n"
        f"Test Config: \n"
        f"  concurrency={config_fields.get('concurrency')}\n"
        f"  rampUp={config_fields.get('rampUp')}\n"
        f"  steps={config_fields.get('steps')}\n"
        f"  iterations={config_fields.get('iterations')}\n"
        f"Test Configuration JSON: {test_config_json}\n"
    )
    return report

async def list_test_runs(test_id: str, start_time: str, end_time: str, ctx: Context) -> list:
    """
    Lists BlazeMeter test runs (masters) for the specified test and time range.
    Accepts dates as 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'.

    Returns:
        List of dicts with run/master info and session IDs.
        Fields: run_id, test_name, start_time, end_time, status, session_ids, duration_seconds (optional)
    """
    start_epoch = to_epoch(start_time)
    end_epoch = to_epoch(end_time)
    url = f"{BLAZEMETER_API_BASE}/masters?testId={test_id}&from={start_epoch}&to={end_epoch}"

    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.get(url, headers=get_headers())
            resp.raise_for_status()
            results = resp.json().get("result", [])
        except Exception as e:
            return [{"error": f"Failed to retrieve test runs: {e}"}]

        runs = []
        for m in results:
            created = m.get("created")
            ended = m.get("ended")
            # Calculate duration in seconds if possible
            duration_seconds = None
            duration_str = "N/A"
            if created and ended:
                try:
                    duration_seconds = int(ended) - int(created)
                    duration_str = format_duration(duration_seconds)
                except Exception:
                    pass
            runs.append({
                "run_id": m.get("id"),
                "test_name": m.get("name"),
                "start_time": epoch_to_timestamp(created),
                "end_time": epoch_to_timestamp(ended),
                "sessions_id": m.get("sessionsId", []),
                "project_id": m.get("projectId"),
                "max_users": m.get("maxUsers"),
                "duration": duration_str,                   # Human-friendly e.g. "2m 8s"
                "duration_seconds": duration_seconds,       # Raw seconds for downstream use
                "locations": m.get("locations", []),
            })
        return runs if runs else [{"message": "No matching runs found."}]

async def get_session_artifacts(session_id: str, ctx: Context) -> dict:
    """
    Calls BlazeMeter API to get artifact and log file URLs for a given session.

    Args:
        session_id: BlazeMeter session ID
        ctx (Context, optional): FastMCP workflow context for passing file URLs downstream.

    Returns:
        Dict mapping each filename to its downloadable URL (dataUrl). Updates context with file list if present.
    """
    url = f"{BLAZEMETER_API_BASE}/sessions/{session_id}/reports/logs"
    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, headers=get_headers())
        resp.raise_for_status()
        result = resp.json().get("result", {})
        files = {}
        for item in result.get("data", []):
            filename = item.get("filename")
            data_url = item.get("dataUrl")
            if filename and data_url:
                files[filename] = data_url
            if filename and filename.lower() == "artifacts.zip":
                await ctx.set_state("artifact_zip_url", data_url)
                await ctx.set_state("artifact_zip_filename", filename)
        if ctx is not None:
            await ctx.set_state("artifact_file_list", files)
            await ctx.set_state("artifact_file_session_id", session_id)
        return files if files else {"message": "No files found in this session's logs report."}

async def download_artifact_zip_file(artifact_zip_url: str, run_id: str, ctx: Context) -> str:
    """
    Downloads the artifact ZIP file for a test run to the correct artifacts folder.

    Args:
        artifact_zip_url: Signed S3 URL for artifacts.zip.
        run_id: The BlazeMeter run ID (master ID).
        ctx (Context, optional): FastMCP workflow context to save downloaded file path.

    Returns:
        Full local path to the downloaded ZIP file, or error message.
        Updates context with file path.
    """
    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter")
    os.makedirs(dest_folder, exist_ok=True)
    local_zip_path = os.path.join(dest_folder, "artifacts.zip")
    try:
        verify_ssl = get_ssl_verify_setting()
        async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
            # Try with minimal headers first (like Postman might send)
            minimal_headers = {"Accept": "*/*", "User-Agent": "Mozilla/5.0 (compatible; BlazeMeter-MCP/1.0)"}
            try:
                response = await client.get(artifact_zip_url, headers=minimal_headers)
                response.raise_for_status()
            except Exception as e1:
                # If minimal headers fail, try with BlazeMeter auth headers
                try:
                    response = await client.get(artifact_zip_url, headers=get_headers({"Accept": "*/*"}))
                    response.raise_for_status()
                except Exception as e2:
                    if ctx is not None:
                        await ctx.set_state("download_error", str(e2))
                    return f"❗ Error downloading artifacts.zip: Minimal headers failed: {e1}, Auth headers failed: {e2}"
            
            with open(local_zip_path, "wb") as f:
                f.write(response.content)
        if ctx is not None:
            await ctx.set_state("local_zip_path", local_zip_path)
        return local_zip_path
    except Exception as e:
        if ctx is not None:
            await ctx.set_state("download_error", str(e))
            await ctx.error(f"Error downloading artifacts.zip: {e}")
        return f"❗ Error downloading artifacts.zip: {e}"

async def extract_artifact_zip_file(local_zip_path: str, run_id: str, ctx: Context) -> list:
    """
    Extracts the specified artifacts.zip file to the appropriate folder for a run.

    Args:
        local_zip_path: Full path to the downloaded artifacts.zip file.
        run_id: BlazeMeter run ID.
        ctx (Context, optional): Workflow context to store extracted file list.

    Returns:
        List of full paths to the extracted files (within the run's 'artifacts' directory). Updates context for downstream use.
    """
    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter", "artifacts")
    os.makedirs(dest_folder, exist_ok=True)
    try:
        with zipfile.ZipFile(local_zip_path, "r") as zip_ref:
            zip_ref.extractall(dest_folder)
            extracted_files = [os.path.join(dest_folder, name) for name in zip_ref.namelist()]
        if ctx is not None:
            await ctx.set_state("extracted_files", extracted_files)
        return extracted_files
    except Exception as e:
        if ctx is not None:
            await ctx.set_state("extraction_error", str(e))
            await ctx.error(f"Error extracting artifacts.zip: {e}")
        return [f"❗ Error extracting ZIP: {e}"]

async def process_extracted_artifact_files(run_id: str, extracted_files: list, ctx: Context) -> dict:
    """
    Processes BlazeMeter artifact files for a run:
      - Moves/renames kpi.jtl to test-results.csv
      - Moves jmeter.log
      - Ignores error.jtl and other .jtl files

    Args:
        run_id: BlazeMeter run ID.
        extracted_files: List of full paths to extracted files.
        ctx (Context, optional): Workflow context to store result file paths and errors.

    Returns:
        Dict with processed file paths, errors. Updates context for downstream steps.
    """
    result = {"errors": []}
    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter")
    os.makedirs(dest_folder, exist_ok=True)

    # Only use kpi.jtl for CSV conversion
    kpi_file = next((f for f in extracted_files if os.path.basename(f).lower() == 'kpi.jtl'), None)
    log_file = next((f for f in extracted_files if os.path.basename(f).lower() == 'jmeter.log'), None)

    # Rename and move kpi.jtl
    if kpi_file and os.path.exists(kpi_file):
        csv_path = os.path.join(dest_folder, "test-results.csv")
        shutil.move(kpi_file, csv_path)
        result["csv_path"] = csv_path
    else:
        result["errors"].append("kpi.jtl (metrics) not found.")

    # Move jmeter.log
    if log_file and os.path.exists(log_file):
        log_dest = os.path.join(dest_folder, "jmeter.log")
        shutil.move(log_file, log_dest)
        result["log_path"] = log_dest
    else:
        result["errors"].append("jmeter.log not found.")

    if ctx is not None:
        await ctx.set_state("processed_csv_path", result.get("csv_path"))
        await ctx.set_state("processed_log_path", result.get("log_path"))
        await ctx.set_state("process_errors", result.get("errors"))

    return result


# ===============================================
# Session Artifact Processor (Composite)
# ===============================================

async def session_artifact_processor(
    run_id: str,
    sessions_id: list,
    ctx: Context,
) -> dict:
    """
    Downloads, extracts, and processes artifact ZIPs for all sessions of a run.

    Handles single-session and multi-session runs uniformly via session subfolders.
    Supports idempotent re-runs using a session manifest -- if called again after a
    partial failure, it skips already-completed sessions and retries only the failed ones.

    Args:
        run_id: BlazeMeter run/master ID.
        sessions_id: List of session IDs from get_run_results (sessionsId field).
            For single-session runs, this is a list with one element.
        ctx: FastMCP context for logging and state.

    Returns:
        dict with per-session status, combined CSV path, log file paths, and manifest path.
            - status: "success" (all done), "partial" (some failed), "error" (all failed)
    """
    # Load config values (with defaults)
    max_retries = bz_config.get("artifact_download_max_retries", 3)
    retry_delay = bz_config.get("artifact_download_retry_delay", 2)
    cleanup = get_cleanup_session_folders(config)

    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter")
    sessions_folder = os.path.join(dest_folder, "sessions")
    os.makedirs(sessions_folder, exist_ok=True)

    total_sessions = len(sessions_id)
    is_multi = total_sessions > 1

    # --- Load or create manifest ---
    manifest = load_manifest(run_id)
    if manifest is None:
        manifest = create_manifest(run_id, sessions_id)
        save_manifest(run_id, manifest)
        await ctx.info(f"Created session manifest for {total_sessions} session(s).")
    else:
        await ctx.info(f"Resuming from existing manifest. {total_sessions} session(s).")

    # --- Process each session ---
    for i, session_id in enumerate(sessions_id, start=1):
        session_key = f"session-{i}"
        session_data = manifest["sessions"].get(session_key, {})

        # Skip completed sessions
        if session_data.get("status") == "completed":
            continue

        session_dir = os.path.join(sessions_folder, session_key)
        os.makedirs(session_dir, exist_ok=True)

        # ---- STAGE 1: Download ----
        dl_stage = session_data.get("stages", {}).get("download", {})
        zip_path = os.path.join(session_dir, "artifacts.zip")

        if dl_stage.get("status") != "completed":
            await ctx.info(f"Downloading artifacts for {session_key} ({session_id})...")
            manifest["sessions"][session_key]["status"] = "downloading"
            manifest["sessions"][session_key]["stages"]["download"]["status"] = "in_progress"
            save_manifest(run_id, manifest)

            # Get artifact file list for this session
            await get_session_artifacts(session_id, ctx)
            artifact_zip_url = (await ctx.get_state("artifact_zip_url")) if ctx else None

            if not artifact_zip_url:
                manifest["sessions"][session_key]["status"] = "failed"
                manifest["sessions"][session_key]["stages"]["download"] = {
                    "status": "failed", "attempts": 0,
                    "error": f"No artifacts.zip URL found for session {session_id}",
                }
                save_manifest(run_id, manifest)
                await ctx.error(f"No artifacts.zip URL for {session_key}")
                continue

            dl_result = await download_with_retry(
                artifact_zip_url=artifact_zip_url,
                dest_path=zip_path,
                ssl_verify_setting=get_ssl_verify_setting(),
                auth_headers_func=get_headers,
                max_retries=max_retries,
                retry_delay=retry_delay,
                ctx=ctx,
            )
            manifest["sessions"][session_key]["stages"]["download"] = {
                "status": dl_result["status"],
                "file": zip_path if dl_result["status"] == "completed" else None,
                "attempts": dl_result["attempts"],
                "error": dl_result.get("error"),
            }
            if dl_result["status"] == "failed":
                manifest["sessions"][session_key]["status"] = "failed"
                save_manifest(run_id, manifest)
                await ctx.error(f"Download failed for {session_key}: {dl_result['error']}")
                continue
        # ---- STAGE 2: Extract ----
        ext_stage = session_data.get("stages", {}).get("extract", {})
        extract_dir = os.path.join(session_dir, "artifacts")

        if ext_stage.get("status") != "completed":
            manifest["sessions"][session_key]["status"] = "extracting"
            manifest["sessions"][session_key]["stages"]["extract"]["status"] = "in_progress"
            save_manifest(run_id, manifest)

            try:
                os.makedirs(extract_dir, exist_ok=True)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_dir)
                    file_count = len(zf.namelist())
                manifest["sessions"][session_key]["stages"]["extract"] = {
                    "status": "completed", "file_count": file_count,
                }
            except Exception as e:
                manifest["sessions"][session_key]["status"] = "failed"
                manifest["sessions"][session_key]["stages"]["extract"] = {
                    "status": "failed", "error": str(e),
                }
                save_manifest(run_id, manifest)
                await ctx.error(f"Extraction failed for {session_key}: {e}")
                continue
        # ---- STAGE 3: Process (move logs + append JTL) ----
        proc_stage = session_data.get("stages", {}).get("process", {})

        if proc_stage.get("status") != "completed":
            manifest["sessions"][session_key]["status"] = "processing"
            manifest["sessions"][session_key]["stages"]["process"]["status"] = "in_progress"
            save_manifest(run_id, manifest)

            try:
                extracted_files = [
                    os.path.join(extract_dir, f) for f in os.listdir(extract_dir)
                ]

                # --- Move JMeter log ---
                log_file = next(
                    (f for f in extracted_files if os.path.basename(f).lower() == "jmeter.log"),
                    None,
                )
                log_dest_name = f"jmeter-{i}.log" if is_multi else "jmeter.log"
                log_dest = os.path.join(dest_folder, log_dest_name)

                if log_file and os.path.exists(log_file):
                    shutil.copy2(log_file, log_dest)
                else:
                    await ctx.warning(f"jmeter.log not found in {session_key}")

                # --- Append JTL to combined CSV ---
                kpi_file = next(
                    (f for f in extracted_files if os.path.basename(f).lower() == "kpi.jtl"),
                    None,
                )
                csv_path = os.path.join(dest_folder, "test-results.csv")
                jtl_rows = 0

                if kpi_file and os.path.exists(kpi_file):
                    # Check if this session's data is already in the combined CSV
                    sessions_included = manifest["combined_csv"].get("sessions_included", [])
                    if session_key not in sessions_included:
                        is_first = len(sessions_included) == 0
                        jtl_rows = append_jtl_to_csv(kpi_file, csv_path, is_first=is_first)
                        manifest["combined_csv"]["sessions_included"].append(session_key)
                        manifest["combined_csv"]["total_rows"] += jtl_rows
                        await ctx.info(f"Appended {jtl_rows} rows from {session_key} to test-results.csv")
                else:
                    await ctx.warning(f"kpi.jtl not found in {session_key}")

                # Mark session completed
                manifest["sessions"][session_key]["status"] = "completed"
                manifest["sessions"][session_key]["stages"]["process"] = {
                    "status": "completed",
                    "jtl_rows": jtl_rows,
                    "log_file": log_dest_name if log_file else None,
                }
                save_manifest(run_id, manifest)

            except Exception as e:
                manifest["sessions"][session_key]["status"] = "failed"
                manifest["sessions"][session_key]["stages"]["process"] = {
                    "status": "failed", "error": str(e),
                }
                save_manifest(run_id, manifest)
                await ctx.error(f"Processing failed for {session_key}: {e}")
                continue

    # --- Cleanup session folders if configured ---
    if cleanup:
        all_completed = all(
            s.get("status") == "completed" for s in manifest["sessions"].values()
        )
        if all_completed:
            shutil.rmtree(sessions_folder, ignore_errors=True)

    save_manifest(run_id, manifest)

    # --- Build return summary ---
    completed = [k for k, v in manifest["sessions"].items() if v["status"] == "completed"]
    failed = [k for k, v in manifest["sessions"].items() if v["status"] == "failed"]
    log_files = []
    for k, v in manifest["sessions"].items():
        lf = v.get("stages", {}).get("process", {}).get("log_file")
        if lf:
            log_files.append(lf)

    overall_status = "success" if len(failed) == 0 else ("partial" if completed else "error")

    result = {
        "status": overall_status,
        "run_id": str(run_id),
        "total_sessions": total_sessions,
        "completed_sessions": len(completed),
        "failed_sessions": len(failed),
        "sessions": {
            k: {
                "status": v["status"],
                "error": (
                    v.get("stages", {}).get("download", {}).get("error")
                    or v.get("stages", {}).get("extract", {}).get("error")
                    or v.get("stages", {}).get("process", {}).get("error")
                ),
            }
            for k, v in manifest["sessions"].items()
        },
        "combined_csv": os.path.join(dest_folder, "test-results.csv") if completed else None,
        "combined_csv_rows": manifest["combined_csv"]["total_rows"],
        "log_files": log_files,
        "manifest_path": get_manifest_path(run_id),
        "message": (
            f"{len(completed)} of {total_sessions} sessions processed successfully."
            + (f" Re-run to retry {len(failed)} failed session(s)." if failed else "")
        ),
    }

    # Update context for downstream tools
    if ctx:
        await ctx.set_state("processed_csv_path", result["combined_csv"])
        await ctx.set_state("processed_log_files", log_files)
        await ctx.set_state("session_manifest", manifest)

    return result


async def get_public_report_url(run_id: str, ctx: Context) -> dict:
    """
    Requests a public token for the provided run_id and returns a shareable BlazeMeter report URL.
    Also saves the result to artifacts/{run_id}/blazemeter/public_report.json for use by PerfReport.

    Args:
        run_id: The BlazeMeter master/run ID.
        ctx (Context, optional): FastMCP context to pass/share report URL and token.

    Returns:
        Dictionary with:
            - run_id: The provided run ID.
            - public_url: The public report URL for sharing.
            - public_token: The raw public token.
            - is_new: True if the token was newly created, False if already existed.
            - error: Error message or None.
            - json_path: Path to the saved public_report.json file.
        Updates context with public_url and public_token for workflow chaining.
    """
    url = f"{BLAZEMETER_API_BASE}/masters/{run_id}/public-token"
    try:
        verify_ssl = get_ssl_verify_setting()
        async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=get_headers({"Content-Type": "application/json"}))
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            token = result.get("publicToken")
            is_new = result.get("new", False)
            if token:
                public_url = f"https://a.blazemeter.com/app/?public-token={token}#/masters/{run_id}/summary"
                
                # Build response data
                response_data = {
                    "run_id": run_id,
                    "public_url": public_url,
                    "public_token": token,
                    "is_new": is_new,
                    "error": None
                }
                
                # Write to public_report.json for PerfReport consumption
                json_path = write_public_report_json(run_id, response_data)
                response_data["json_path"] = json_path
                
                if ctx is not None:
                    await ctx.set_state("public_url", public_url)
                    await ctx.set_state("public_token", token)
                    await ctx.set_state("is_new_token", is_new)
                    await ctx.set_state("public_report_json_path", json_path)
                
                return response_data
            else:
                error_data = {
                    "run_id": run_id,
                    "public_url": None,
                    "public_token": None,
                    "is_new": False,
                    "error": "Public token not returned by API.",
                    "json_path": None
                }
                if ctx is not None:
                    await ctx.set_state("public_url", None)
                    await ctx.set_state("public_token", None)
                    await ctx.set_state("is_new_token", False)
                    await ctx.set_state("public_report_error", "Public token not returned by API.")
                return error_data
    except Exception as e:
        error_data = {
            "run_id": run_id,
            "public_url": None,
            "public_token": None,
            "is_new": False,
            "error": str(e),
            "json_path": None
        }
        if ctx is not None:
            await ctx.set_state("public_url", None)
            await ctx.set_state("public_report_error", str(e))
        return error_data

async def fetch_aggregate_report(run_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Fetch aggregate performance report from BlazeMeter API and save to CSV.
    Returns only the 'ALL' aggregate summary to keep response lightweight.
    """
    try:
        url = f"{BLAZEMETER_API_BASE}/masters/{run_id}/reports/aggregatereport/data"
        
        verify_ssl = get_ssl_verify_setting()
        async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=get_headers(), timeout=30.0)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("error"):
                return {"error": f"BlazeMeter API error: {data['error']}", "status": "failed"}
            
            results = data.get("result", [])
            if not results:
                return {"error": "No aggregate data available", "status": "failed"}
            
            # Save to CSV for PerfAnalysis consumption
            csv_file = write_aggregate_report_csv(run_id, results)
            
            # Extract only the 'ALL' aggregate for response
            all_aggregate = None
            for item in results:
                if item.get("labelName") == "ALL":
                    all_aggregate = clean_aggregate_data(item)
                    break
            
            if not all_aggregate:
                return {"error": "No 'ALL' aggregate found in BlazeMeter response", "status": "failed"}

            # Update context with aggregate data and CSV path
            await ctx.set_state("aggregate_report_data", json.dumps(all_aggregate))
            await ctx.set_state("aggregate_report_csv", csv_file)
            
            await ctx.info(f"Aggregate report: {all_aggregate['samples']} samples, {all_aggregate['avgResponseTime']:.1f}ms avg")
            
            return {
                "status": "success",
                "run_id": run_id,
                "total_labels": len(results),
                "aggregate_summary": all_aggregate,
                "csv_file": csv_file
            }
            
    except Exception as e:
        error_msg = f"Failed to fetch aggregate report: {str(e)}"
        await ctx.error(f"Aggregate report error: {error_msg}")
        return {"error": error_msg, "status": "failed"}

def write_aggregate_report_csv(run_id: str, results: List[Dict]) -> str:
    """Write aggregate report data to CSV file"""
    
    dest_folder = os.path.join(artifacts_base, str(run_id), "blazemeter")
    os.makedirs(dest_folder, exist_ok=True)
    
    csv_file = os.path.join(dest_folder, "aggregate_performance_report.csv")
    
    # Define CSV headers matching the JSON structure
    headers = [
        "labelName", "samples", "avgResponseTime", "minResponseTime", "maxResponseTime",
        "medianResponseTime", "90line", "95line", "99line", "stDev",
        "avgLatency", "errorsCount", "errorsRate", "avgThroughput",
        "avgBytes", "duration", "concurrency", "hasLabelPassedThresholds"
    ]
    
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for item in results:
            # Clean the data and write row
            row = {header: item.get(header, '') for header in headers}
            writer.writerow(row)
    
    return csv_file

def clean_aggregate_data(item: Dict) -> Dict:
    """Clean aggregate data for JSON serialization"""
    import math
    
    cleaned = {}
    for key, value in item.items():
        # Skip labelId as it's not needed
        if key == "labelId":
            continue
            
        # Handle NaN values
        if isinstance(value, float) and math.isnan(value):
            cleaned[key] = None
        # Ensure proper types for JSON serialization
        elif isinstance(value, (int, float)):
            cleaned[key] = float(value) if isinstance(value, float) else int(value)
        else:
            cleaned[key] = value
    
    return cleaned

# -----------------------------
# Helper functions
# -----------------------------

def get_ssl_verify_setting() -> Union[str, bool]:
    """
    Determines SSL verification setting based on config.yaml.
    
    Returns:
        Union[str, bool]: 
            - Path to CA bundle (str) if ssl_verification is "ca_bundle" and certs are available
            - False if ssl_verification is "disabled"
            - True as fallback (use system certs)
    """
    ssl_verification = bz_config.get('ssl_verification', 'ca_bundle').lower()
    
    if ssl_verification == 'disabled':
        return False
    elif ssl_verification == 'ca_bundle':
        # Use CA bundle if available, otherwise default to True
        return CA_BUNDLE or True
    else:
        # Default to system cert verification
        return True


# ===============================================
# Shared Folder API Functions
# ===============================================


async def list_shared_folders(workspace_id: str = None) -> list:
    """
    Retrieve all shared folders for a BlazeMeter workspace.

    Calls the BlazeMeter ``GET /api/v4/folders`` endpoint with automatic
    pagination (page size of 50) until all folders are returned.

    Shared folders are workspace-level containers used to store test data
    files (CSVs, Excel sheets, keystores, etc.) that can be attached to one
    or more BlazeMeter tests. Files placed in a shared folder are deployed
    alongside the JMX script on every load-generator engine at runtime.

    Args:
        workspace_id: BlazeMeter workspace ID. If omitted or empty, falls
            back to the ``BLAZEMETER_WORKSPACE_ID`` environment variable.

    Returns:
        list[dict]: One dict per folder with keys ``id``, ``name``, and
        ``workspace_id``.  Returns a single-element list containing an
        ``error`` key when the workspace ID cannot be resolved.
    """
    workspace_id = workspace_id or BLAZEMETER_WORKSPACE_ID
    if not workspace_id:
        return [{"error": "workspace_id not provided and BLAZEMETER_WORKSPACE_ID is not set."}]

    verify_ssl = get_ssl_verify_setting()
    all_folders = []
    skip = 0
    page_size = 50

    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        while True:
            url = (
                f"{BLAZEMETER_API_BASE}/folders"
                f"?workspaceId={workspace_id}&skip={skip}&limit={page_size}"
            )
            resp = await client.get(url, headers=get_headers())
            resp.raise_for_status()
            folders = resp.json().get("result", [])
            if not folders:
                break
            all_folders.extend(folders)
            if len(folders) < page_size:
                break
            skip += page_size

    return [
        {"id": f["id"], "name": f["name"], "workspace_id": f.get("workspaceId")}
        for f in all_folders
    ]


async def get_shared_folder_files(folder_id: str) -> dict:
    """
    List every file stored inside a BlazeMeter shared folder.

    Calls ``GET /api/v4/folders/{folder_id}/files`` and returns a
    normalised summary including each file's name, size (bytes and MB),
    and last-modified timestamp.

    Use this to verify folder contents before or after an upload, or to
    confirm that test data files are present before triggering a test run.

    Args:
        folder_id: The unique identifier of the shared folder, as returned
            by :func:`list_shared_folders`.

    Returns:
        dict with keys:
            - ``folder_id``: Echo of the requested folder ID.
            - ``folder_name``: Human-readable folder name.
            - ``file_count``: Number of files in the folder.
            - ``files``: List of dicts, each with ``name``, ``size_bytes``,
              ``size_mb``, and ``last_modified``.
    """
    verify_ssl = get_ssl_verify_setting()
    async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
        url = f"{BLAZEMETER_API_BASE}/folders/{folder_id}/files"
        resp = await client.get(url, headers=get_headers())
        resp.raise_for_status()
        result = resp.json().get("result", {})

    files = []
    for f in result.get("files", []):
        files.append({
            "name": f.get("name"),
            "size_bytes": f.get("size"),
            "size_mb": round(f.get("size", 0) / (1024 * 1024), 1),
            "last_modified": epoch_to_timestamp(f.get("lastModified")),
        })

    return {
        "folder_id": result.get("id", folder_id),
        "folder_name": result.get("name"),
        "file_count": len(files),
        "files": files,
    }


async def upload_to_shared_folder(
    folder_id: str,
    path: str,
    ctx: Context = None,
) -> dict:
    """
    Upload one file **or** every allowed file in a directory to a BlazeMeter
    shared folder.

    Behaviour is determined by the ``path`` argument:

    * **File path** -- uploads that single file.
    * **Directory path** -- discovers all top-level files whose extension is
      on the ``allowed_extensions`` allowlist (see ``config.yaml`` →
      ``blazemeter.shared_folders.allowed_extensions``), uploads each one,
      and reports per-file results. Files whose extension is *not* in the
      allowlist are skipped and listed in ``skipped_files`` so the caller
      can review what was excluded.

    Each file is uploaded via BlazeMeter's two-step signed-URL process:

    1. ``GET /api/v4/folders/{folder_id}/s3/sign?fileName=<name>`` to
       obtain a cloud-provider signed upload URL.
    2. ``PUT`` the raw file bytes to that signed URL with
       ``Content-Type: application/octet-stream``.

    This bypasses the BlazeMeter UI's file-size restriction and works for
    files of any practical size.

    Args:
        folder_id: Target shared folder ID (from :func:`list_shared_folders`).
        path: Absolute local path to a single file **or** a directory
            containing files to upload.
        ctx: Optional FastMCP context for per-file progress logging.

    Returns:
        dict with keys:
            - ``status``: ``"success"`` | ``"partial"`` | ``"failed"``
            - ``folder_id``: Echo of the target folder ID.
            - ``source``: The path that was provided.
            - ``mode``: ``"single_file"`` or ``"directory"``.
            - ``total_files``: Number of files that were candidates for upload.
            - ``uploaded``: Count of successfully uploaded files.
            - ``failed``: Count of files that failed to upload.
            - ``skipped_files``: (directory mode only) List of dicts for files
              excluded by the extension allowlist, each with ``file`` and
              ``reason``.
            - ``total_size_mb``: Combined size of successfully uploaded files.
            - ``results``: List of per-file result dicts (``status``, ``file``,
              ``size_mb``, and ``error`` if applicable).

    Raises:
        No exceptions are raised; all errors are captured in the return dict
        and per-file ``results`` entries.
    """
    import urllib.parse

    allowed_extensions = get_shared_folder_allowed_extensions(config)

    # --- Resolve file list ------------------------------------------------
    if os.path.isfile(path):
        mode = "single_file"
        file_paths = [path]
        skipped = []
    elif os.path.isdir(path):
        mode = "directory"
        all_entries = sorted(
            os.path.join(path, f)
            for f in os.listdir(path)
            if os.path.isfile(os.path.join(path, f))
        )
        file_paths = []
        skipped = []
        for fp in all_entries:
            ext = os.path.splitext(fp)[1].lower()
            if ext in allowed_extensions:
                file_paths.append(fp)
            else:
                skipped.append({
                    "file": os.path.basename(fp),
                    "reason": f"Extension '{ext}' not in allowed_extensions",
                })
        if not file_paths and not skipped:
            return {
                "status": "failed",
                "folder_id": folder_id,
                "source": path,
                "mode": mode,
                "error": f"No files found in directory: {path}",
            }
    else:
        return {
            "status": "failed",
            "folder_id": folder_id,
            "source": path,
            "mode": "unknown",
            "error": f"Path not found (not a file or directory): {path}",
        }

    if ctx and skipped:
        await ctx.info(
            f"Skipped {len(skipped)} file(s) not matching allowed extensions: "
            + ", ".join(s["file"] for s in skipped)
        )
    if ctx and file_paths:
        total_label = "file" if len(file_paths) == 1 else "files"
        await ctx.info(f"Uploading {len(file_paths)} {total_label} to shared folder {folder_id}")

    if not file_paths:
        return {
            "status": "failed",
            "folder_id": folder_id,
            "source": path,
            "mode": mode,
            "total_files": 0,
            "uploaded": 0,
            "failed": 0,
            "skipped_files": skipped,
            "total_size_mb": 0,
            "results": [],
            "error": "No files with allowed extensions found in directory.",
        }

    # --- Upload each file -------------------------------------------------
    verify_ssl = get_ssl_verify_setting()
    results = []

    for file_path in file_paths:
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        encoded_name = urllib.parse.quote(file_name, safe="")

        try:
            # Step 1: Obtain signed upload URL from BlazeMeter
            async with httpx.AsyncClient(verify=verify_ssl, timeout=HTTP_TIMEOUT_SECONDS) as client:
                sign_url = (
                    f"{BLAZEMETER_API_BASE}/folders/{folder_id}"
                    f"/s3/sign?fileName={encoded_name}"
                )
                resp = await client.get(sign_url, headers=get_headers())
                resp.raise_for_status()
                signed_url = resp.json().get("result")

            if not signed_url:
                results.append({
                    "status": "failed",
                    "file": file_name,
                    "error": "No signed URL returned by BlazeMeter API.",
                })
                if ctx:
                    await ctx.error(f"No signed URL returned for {file_name}")
                continue

            # Step 2: PUT file bytes to the signed URL
            async with httpx.AsyncClient(verify=verify_ssl, timeout=httpx.Timeout(600.0)) as client:
                with open(file_path, "rb") as f:
                    file_data = f.read()
                resp = await client.put(
                    signed_url,
                    content=file_data,
                    headers={"Content-Type": "application/octet-stream"},
                )
                resp.raise_for_status()

            results.append({
                "status": "success",
                "file": file_name,
                "size_bytes": file_size,
                "size_mb": round(file_size / (1024 * 1024), 1),
            })

        except Exception as e:
            error_msg = f"Upload failed for {file_name}: {e}"
            if ctx:
                await ctx.error(error_msg)
            results.append({"status": "failed", "file": file_name, "error": str(e)})

    # --- Build summary ----------------------------------------------------
    succeeded = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]
    total_size_mb = sum(r.get("size_mb", 0) for r in succeeded)

    if not failed:
        overall = "success"
    elif succeeded:
        overall = "partial"
    else:
        overall = "failed"

    summary = {
        "status": overall,
        "folder_id": folder_id,
        "source": path,
        "mode": mode,
        "total_files": len(file_paths),
        "uploaded": len(succeeded),
        "failed": len(failed),
        "skipped_files": skipped,
        "total_size_mb": round(total_size_mb, 1),
        "results": results,
    }

    if ctx and failed:
        await ctx.error(f"{len(failed)} file(s) failed to upload.")

    return summary