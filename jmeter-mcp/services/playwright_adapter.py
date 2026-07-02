"""
playwright_adapter.py

Adapter module that reads Playwright MCP trace artifacts and converts them
into the canonical, step-aware network capture JSON used for JMeter script
generation.

High-level responsibilities:

- Discover the latest Playwright `.network` trace and its `resources/` folder.
- Parse NDJSON `resource-snapshot` entries.
- Resolve request and response bodies from `resources/*.json` (and related files).
- Apply config-driven filters (static assets, fonts, third-party, domain, etc.).
- Map captured requests to test steps defined in a Markdown spec file.
- Emit `network_capture_<timestamp>.json` under:
    artifacts/<run_id>/jmeter/network-capture/
"""

import glob
import os
import json
import shutil
import uuid
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
from urllib.parse import urlparse

from utils.config import load_config
from utils.browser_utils import extract_apex_domain_from_task
from services.spec_parser import (
    load_browser_steps,
    generate_task,
    _resolve_spec_path,
)
from services import network_capture  # for should_capture_url

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
NETWORK_CAPTURE_CONFIG = CONFIG.get("network_capture", {})

# Default Playwright MCP traces directory (relative to jmeter-mcp/)
DEFAULT_TRACES_DIR = os.path.join("..", ".playwright-mcp", "traces")
PLAYWRIGHT_TRACES_DIR = CONFIG.get("playwright", {}).get("traces_path", DEFAULT_TRACES_DIR)

# ============================================================
# Playwright MCP Capture Pipeline
# ============================================================

def run_playwright_capture_pipeline(spec_path: str, run_id: str) -> str:
    """
    High-level orchestration function that:

      1. Loads the test steps from the given spec/Task file.
      2. Discovers the latest Playwright `.network` trace + `resources/` dir.
      3. Parses and filters the trace into a step-aware mapping.
      4. Writes the canonical network capture JSON for JMeter under
         artifacts/<run_id>/jmeter/network-capture/.

    Returns:
        The full path to the written network capture JSON file.
    """
    # Load and parse steps from the spec
    step_texts = load_browser_steps(run_id, spec_path)

    # Discover latest trace
    network_file, resources_dir = _find_latest_network_trace()

    # Optionally derive/override capture_domain from the Task content
    task_text = generate_task(_resolve_spec_path(spec_path, run_id))
    apex_domain = extract_apex_domain_from_task(task_text)
    capture_config = dict(NETWORK_CAPTURE_CONFIG)  # shallow copy
    if apex_domain and not capture_config.get("capture_domain"):
        capture_config["capture_domain"] = apex_domain

    # Parse trace -> per-step mapping
    per_step = parse_network_trace_to_step_map(
        network_file=network_file,
        resources_dir=resources_dir,
        step_texts=step_texts,
        capture_config=capture_config,
    )

    # Write final JSON
    output_path = write_step_network_capture(per_step, run_id=run_id)
    return output_path

# ============================================================
# Helper Functions
# ============================================================

def archive_existing_traces() -> Optional[str]:
    """
    If the Playwright MCP output directory contains previous run artifacts,
    move the traces directory and any verbose output files (console logs,
    page YAML snapshots) to a timestamped backup folder, then recreate an
    empty traces directory.

    Archived files:
      - traces/          — network trace files and resources (moved as-is)
      - console-*.log    — Playwright MCP console log
      - page-*.yml       — Playwright MCP page snapshots (one per browser step)

    All files are placed side-by-side in the backup folder:
      .playwright-mcp/traces_<YYYYMMDD_HHMMSS>/

    Returns:
        The path to the archived backup directory, or None if nothing was archived.

    NOTE: This is intended to be called *before* a new Playwright MCP run,
    e.g., from the MCP tool that prepares the environment. The main parser
    does NOT call this automatically.
    """
    traces_dir = PLAYWRIGHT_TRACES_DIR
    parent_dir = os.path.dirname(traces_dir)

    # Collect verbose output files from the parent .playwright-mcp/ directory
    verbose_files = (
        glob.glob(os.path.join(parent_dir, "console-*.log"))
        + glob.glob(os.path.join(parent_dir, "page-*.yml"))
    )

    traces_exist = os.path.isdir(traces_dir) and any(os.scandir(traces_dir))

    if not traces_exist and not verbose_files:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(traces_dir.rstrip(os.sep))
    archived_dir = os.path.join(parent_dir, f"{base_name}_{timestamp}")

    if traces_exist:
        os.rename(traces_dir, archived_dir)
    else:
        os.makedirs(archived_dir, exist_ok=True)

    # Move verbose output files into the backup folder
    for file_path in verbose_files:
        dest = os.path.join(archived_dir, os.path.basename(file_path))
        shutil.move(file_path, dest)

    # Recreate empty traces directory for the next run
    os.makedirs(traces_dir, exist_ok=True)
    return archived_dir

def parse_network_trace_to_step_map(
    network_file: str,
    resources_dir: str,
    step_texts: List[str],
    capture_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Core parser that reads the NDJSON `.network` file and produces a
    step-aware mapping:

        {
          "Step 1: ...": [ {request_entry}, ... ],
          "Step 2: ...": [ ... ],
          ...
        }

    Each request_entry matches the canonical schema used by the legacy
    jmeter-ai-studio network capture JSON:

        {
          "request_id": str,
          "method": str,
          "url": str,
          "headers": { ... },
          "post_data": str,
          "step": { "step_number": int, "instructions": str, "timestamp": str },
          "response": str,
          "log_timestamp": str,
          "status": int,
          "response_headers": { ... }
        }
    """
    if capture_config is None:
        capture_config = NETWORK_CAPTURE_CONFIG

    all_requests: List[Dict[str, Any]] = []

    with open(network_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "resource-snapshot":
                continue

            snapshot = obj.get("snapshot") or {}
            req = snapshot.get("request") or {}
            resp = snapshot.get("response") or {}
            url = req.get("url") or ""
            method = (req.get("method") or "GET").upper()

            # Skip non-HTTP(s) URLs
            parsed_url = urlparse(url)
            if parsed_url.scheme not in ("http", "https"):
                continue

            content = resp.get("content") or {}
            mime_type = (content.get("mimeType") or "").lower()

            if not _should_capture_snapshot(url, mime_type, capture_config):
                continue

            request_headers = _headers_list_to_dict(req.get("headers") or [])
            response_headers = _headers_list_to_dict(resp.get("headers") or [])

            # Request body (for POST/PUT/etc.)
            post_data_text = ""
            post_data = req.get("postData") or {}
            sha1_req = post_data.get("_sha1") or ""
            if sha1_req:
                post_data_text = _load_body_from_sha1(resources_dir, sha1_req)

            # Response body (HTML, JSON, text, etc.)
            response_text = ""
            sha1_resp = content.get("_sha1") or ""
            if sha1_resp:
                response_text = _load_body_from_sha1(resources_dir, sha1_resp)

            started_dt = snapshot.get("startedDateTime") or ""

            entry: Dict[str, Any] = {
                "request_id": str(uuid.uuid4()),
                "method": method,
                "url": url,
                "headers": request_headers,
                "post_data": post_data_text or "",
                # 'step' will be filled in later once we assign to steps
                "step": {},
                "response": response_text or "",
                "log_timestamp": datetime.utcnow().isoformat(),
                "status": resp.get("status"),
                "response_headers": response_headers,
                "started_at": started_dt,
            }
            all_requests.append(entry)

    # Map requests -> steps (by order)
    per_step = _assign_requests_to_steps(all_requests, step_texts)

    # Enrich each entry with concrete 'step' metadata
    for idx, step_text in enumerate(step_texts, start=1):
        key = step_text.splitlines()[0].strip() if step_text else f"Step {idx}"
        step_meta = _create_step_metadata(idx, key)

        for entry in per_step.get(key, []):
            entry["step"] = step_meta

        # Remove 'started_at' from the final JSON entries
        for entry in per_step.get(key, []):
            entry.pop("started_at", None)

    return per_step

def write_step_network_capture(
    per_step_requests: Dict[str, List[Dict[str, Any]]],
    run_id: str,
    timestamp: Optional[str] = None,
) -> str:
    """
    Write the step-aware mapping to a network capture JSON file, using the
    same top-level structure as the original jmeter-ai-studio output.

    Returns:
        The full path to the written JSON file.
    """
    output_path = _get_network_capture_output_path(run_id, timestamp)
    # We write the full JSON object at once; streaming is not required here.
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(per_step_requests, f, indent=2, ensure_ascii=False)
    return output_path

# ============================================================
# Utility Functions
# ============================================================

def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _find_latest_network_trace() -> Tuple[str, str]:
    """
    Locate the most recent `.network` file in the Playwright traces directory
    and return (network_file_path, resources_dir).

    Raises:
        FileNotFoundError if no `.network` file is found.
    """
    traces_dir = PLAYWRIGHT_TRACES_DIR
    if not os.path.isdir(traces_dir):
        raise FileNotFoundError(f"Playwright traces directory not found: {traces_dir}")

    network_files: List[str] = []
    for entry in os.scandir(traces_dir):
        if entry.is_file() and entry.name.startswith("trace-") and entry.name.endswith(".network"):
            network_files.append(entry.path)

    if not network_files:
        raise FileNotFoundError(f"No *.network trace files found under: {traces_dir}")

    # Choose the most recently modified trace
    network_files.sort(key=os.path.getmtime, reverse=True)
    network_file = network_files[0]

    resources_dir = os.path.join(traces_dir, "resources")
    if not os.path.isdir(resources_dir):
        raise FileNotFoundError(f"Resources directory not found next to trace: {resources_dir}")

    return network_file, resources_dir


def _headers_list_to_dict(headers_list: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Convert a Playwright-style list of {name, value} headers into a simple dict
    (last occurrence wins if duplicates exist).
    """
    result: Dict[str, str] = {}
    for h in headers_list or []:
        name = h.get("name")
        value = h.get("value")
        if name is not None and value is not None:
            result[name.lower()] = value
    return result


def _should_capture_snapshot(url: str, mime_type: str, capture_config: Dict[str, Any]) -> bool:
    """
    Decide whether a given resource snapshot should be captured for JMeter
    based on:
      - URL-based config filters (static assets, fonts, video, domains, etc.)
      - MIME type (HTML and relevant data types)

    URL-based exclusions always take priority over MIME type heuristics.
    For example, a .js file returning text/html (e.g. a 404 error page)
    is still excluded when capture_static_assets is False.
    """
    # Apply URL-based config filters first (static assets, fonts, domains, etc.)
    try:
        if not network_capture.should_capture_url(url, capture_config):
            return False
    except Exception:
        # Be conservative: fall through to MIME-based logic if config is malformed
        pass

    # Allow HTML pages through for JMeter HTTP Samplers
    if mime_type.lower().startswith("text/html"):
        return True

    # URL filter already approved this request
    return True


def _assign_requests_to_steps(
    requests: List[Dict[str, Any]],
    step_texts: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Naive, order-based mapping of captured requests to steps.

    Assumptions:
      - Steps were executed in the order they appear in `step_texts`.
      - Network requests occurred in roughly the same order as steps.
      - We don't yet use `.trace` events for precise boundaries.

    Strategy:
      - Sort requests by their `started_at` timestamp.
      - Evenly partition the ordered requests across the number of steps.
      - Every step key is present in the result, even if it has zero requests.

    This can later be replaced or enhanced with `.trace`-aware segmentation
    without changing the output JSON schema.
    """
    num_steps = len(step_texts)
    per_step: Dict[str, List[Dict[str, Any]]] = {}

    # Initialize all steps with empty lists
    for idx, step_text in enumerate(step_texts, start=1):
        key = step_text.splitlines()[0].strip() if step_text else f"Step {idx}"
        per_step[key] = []

    if not requests or num_steps == 0:
        return per_step

    # Sort by started_at (string ISO timestamps are fine for ordering here)
    sorted_reqs = sorted(requests, key=lambda r: r.get("started_at") or "")

    total = len(sorted_reqs)
    if total == 0:
        return per_step

    # Evenly assign by index
    for i, req in enumerate(sorted_reqs):
        step_index = int(i * num_steps / total)  # 0-based
        if step_index >= num_steps:
            step_index = num_steps - 1
        step_text = step_texts[step_index]
        key = step_text.splitlines()[0].strip() if step_text else f"Step {step_index + 1}"
        per_step.setdefault(key, []).append(req)

    return per_step


def _create_step_metadata(step_number: int, instructions: str) -> Dict[str, Any]:
    """
    Build the 'step' metadata payload used for each network entry.
    """
    return {
        "step_number": step_number,
        "instructions": instructions,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _get_network_capture_output_path(run_id: str, timestamp: Optional[str] = None) -> str:
    """
    Compute the path for the network capture JSON file for a given run.

    Layout:
      artifacts/<run_id>/jmeter/network-capture/network_capture_<timestamp>.json
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    base_dir = os.path.join(ARTIFACTS_PATH, str(run_id), "jmeter", "network-capture")
    _safe_mkdir(base_dir)
    return os.path.join(base_dir, f"network_capture_{timestamp}.json")


def _load_body_from_sha1(resources_dir: str, sha1_name: str) -> str:
    """
    Given a SHA1-based file name from Playwright's `postData._sha1` or
    `content._sha1`, load its contents as text if possible.

    We deliberately do NOT parse JSON here; we keep the raw body as a string.
    If the file is missing or unreadable, return an empty string.
    """
    if not sha1_name:
        return ""

    body_path = os.path.join(resources_dir, sha1_name)
    if not os.path.isfile(body_path):
        return ""

    # Many bodies are *.json or *.html; others (like .dat) can be ignored
    ext = os.path.splitext(body_path)[1].lower()
    if ext in {".json", ".html", ".txt"}:
        try:
            with open(body_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    else:
        # Non-text / binary resources (e.g., .dat, images, etc.) are ignored
        return ""
