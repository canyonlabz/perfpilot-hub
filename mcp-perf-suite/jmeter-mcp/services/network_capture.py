# Network Capture Service
import asyncio
import json
import os
import logging
from fnmatch import fnmatch
from urllib.parse import urlparse
from datetime import datetime
import uuid

# --- Add this block to load config and set logger level ---
from utils.config import load_config
config = load_config()
verbose = config.get("logging", {}).get("verbose", False)

logger = logging.getLogger(__name__)
if verbose:
    logger.setLevel(logging.WARNING)
else:
    logger.setLevel(logging.ERROR)
# ---------------------------------------------------------

# Global dictionary to store network activity
network_log = []

# Set up logger for this module
logger = logging.getLogger(__name__)

# TODO(exclude-paths-unify): This function duplicates domain/path filtering logic
# that also exists in services/correlations/utils.py:is_excluded_url(). This
# capture-time version additionally handles static assets, fonts, video, and
# third-party filters. See docs/plans/exclude-paths-filtering-gap.md Phase 2
# for unification plan.
def should_capture_url(url, config):
    """Determines whether a URL should be captured based on config filters."""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    path = parsed_url.path.lower()

    # Check exclude_domains list first (APM, analytics, advertising, etc.)
    # These are always excluded regardless of other settings
    exclude_domains = config.get("exclude_domains", [])
    for excluded in exclude_domains:
        excluded_lower = excluded.lower()
        # Match exact domain, subdomain, or partial match (e.g. "google" matches "google.com", "maps.google.com", etc.)
        if domain == excluded_lower or domain.endswith("." + excluded_lower) or excluded_lower in domain:
            return False

    # Check exclude_paths list (SignalR, health checks, diagnostics, etc.)
    exclude_paths = config.get("exclude_paths", [])
    for pattern in exclude_paths:
        if fnmatch(path, pattern.lower()):
            return False

    # Get the capture domain from the config, defaulting to an empty string if not provided
    capture_domain = config.get("capture_domain", "")

    # Exclude third-party requests
    if not config.get("capture_third_party", False):
        if domain and not domain.endswith(capture_domain):
            # Check if the domain is in the whitelist
            return False

    # Exclude static assets
    if not config.get("capture_static_assets", False):
        if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".ico")):
            return False

    # Exclude fonts
    if not config.get("capture_fonts", False):
        if path.endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
            return False

    # Exclude video/streaming files
    if not config.get("capture_video_streams", False):
        if path.endswith((".m3u8", ".m3u", ".ts", ".mp4", ".webm", ".ogg")):
            return False

    # Always capture APIs or requests with /api/ or .json
    if "/api/" in path or path.endswith(".json"):
        return True

    # Otherwise rely on capture_api_requests to determine
    return config.get("capture_api_requests", True)

# This function is called when a route is intercepted.
# It checks if the URL should be captured based on the configuration.
async def log_route_request(route, request, capture_config, current_step_tag):
    url = request.url
    if not should_capture_url(url, capture_config):
        await route.continue_()
        return

    try:
        if callable(request.post_data):
            post_data_raw = await request.post_data()
        else:
            post_data_raw = request.post_data
        post_data = post_data_raw if post_data_raw else ""
    except Exception as e:
        post_data = f"<error: {e}>"

    headers = dict(request.headers)
    if not capture_config.get("capture_cookies", True):
        headers.pop("cookie", None)
    
    # Generate a unique request ID and attach it
    request_id = str(uuid.uuid4())
    setattr(request, '_log_id', request_id)
    
    network_log.append({
        "request_id": request_id,
        "method": request.method,
        "url": url,
        "headers": headers,
        "post_data": post_data,
        "step": current_step_tag,  # Include the current step metadata
        "response": None,
        "log_timestamp": datetime.now().isoformat()
    })
    await route.continue_()

async def handle_response(response):
    """Handles Playwright response events and enriches the corresponding request log entry."""
    req = response.request
    logger.debug(f"⬅️ {response.status} {req.url}")
    
    # Retrieve the unique request ID from the request object
    req_id = getattr(req, '_log_id', None)
    if req_id is None:
        logger.debug(f"⚠️ No request ID found for request: {req.url}")
        return

    try:
        body = await response.body()
        body = body.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"⚠️ Failed to decode response body for {req.url}: {e}")
        body = "<unable to decode>"

    # Iterate through the list to find the matching entry by request_id
    for entry in network_log:
        if entry.get("request_id") == req_id:
            entry.update({
                "status": response.status,
                "response_headers": dict(response.headers),
                "response": body
            })
            break
    else:
        logger.debug(f"⚠️ No log entry found for request ID: {req_id}")

# This function registers a route handler for the page to log requests and responses.
# It uses the Playwright route API to intercept network requests and responses.
async def register_route_logger(page, capture_config, current_step_tag):
    async def route_handler(route, request):
        await log_route_request(route, request, capture_config, current_step_tag)

    await page.route("**/*", route_handler)
    page.on("response", lambda response: asyncio.create_task(handle_response(response)))    ## This line is added to ensure that responses are logged as well.

def initialize_json_output(capture_config):
    """
    Initializes the JSON output file.
    
    Parameters:
      - capture_config: A dictionary containing configuration settings, including the capture_log_path.
    
    This function creates (or overwrites) the JSON file and writes the opening bracket for a JSON array.
    
    Returns:
      - The full file path of the initialized JSON output file.
    """
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Get the log directory from the capture_config, defaulting to "tmp/logs"
    log_dir = capture_config.get("capture_log_path", "tmp/logs")
    file_path = os.path.join(log_dir, f"network_capture_{run_timestamp}.json")
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    with open(file_path, "w", encoding="utf-8") as f:
            f.write("{\n")
    
    return file_path

def append_step_network_data(file_path, step_data, current_step_tag, is_first):
    """
    Streams a step's network entries as a property in the top-level JSON object.

    - step_data: list of entries (may be empty)
    - current_step_tag: dict with step metadata (including 'instructions')
    - is_first: bool, controls comma placement
    """
    step_name = current_step_tag.get("instructions", "")
    with open(file_path, "a", encoding="utf-8") as f:
        if not is_first:
            f.write(",\n")
        # Write the property name
        f.write(json.dumps(step_name, ensure_ascii=False) + ": ")
        # Write the array of entries
        json.dump(step_data, f, indent=2, ensure_ascii=False)

def finalize_json_output(file_path):
    """
    Finalizes the JSON output file by appending a closing bracket
    so that the JSON array structure is properly terminated.
    """
    with open(file_path, "a", encoding="utf-8") as f:
        f.write("\n}\n")

def get_and_clear_current_log():
    """
    Returns a copy of the current network log entries and clears the global log.
    
    This function encapsulates the access to the global `network_log` variable.
    """
    global network_log
    current_entries = network_log.copy()
    network_log.clear()
    return current_entries
