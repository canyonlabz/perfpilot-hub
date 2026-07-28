import asyncio
import logging
import sys
import os
import re
from urllib.parse import urlparse

from utils.config import load_config

# === Global configuration ===
CONFIG = load_config()
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]

# === Set up logging ===
def setup_logging(log_level_str="INFO"):
    # Convert string to logging level (default to INFO if invalid)
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    
    # Create a logger
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    
    # Disable propagation to the root logger
    logger.propagate = False

    # Avoid adding duplicate handlers
    if not logger.handlers:
        # Create a stream-based handler
        handler = logging.StreamHandler(sys.stdout)
        
        # Create a formatter and add it to the handler
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        
        # Add the handler to the logger
        logger.addHandler(handler)
    
    return logger

LOGGER = setup_logging(CONFIG.get("logging", {}).get("level", "INFO"))

# === Apex domain extraction ===
def extract_apex_domain_from_task(task_text: str) -> str | None:
    """
    Parse the provided task/spec text and return the *apex* domain of the
    first HTTP/HTTPS URL that appears.

    Example:
      "Open https://demoblaze.com/ and click Laptops"
        -> "demoblaze.com"
    """
    url_pattern = re.compile(r"https?://[^\s)']+")
    match = url_pattern.search(task_text)
    if not match:
        return None

    url = match.group(0)
    parsed = urlparse(url)

    # remove port if present
    host = parsed.netloc.split(":", 1)[0]
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


# === Run an async coroutine with a timeout ===
async def run_with_timeout(coro, timeout_seconds: float):
    """
    Run the given coroutine with an overall timeout in seconds.
    Returns the result of the coroutine if it completes in time,
    otherwise returns None and logs a timeout message.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        LOGGER.warning("⏰ Operation timed out after %s seconds.", timeout_seconds)
        return None
