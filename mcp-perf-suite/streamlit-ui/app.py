"""
MCP Performance Suite - Streamlit UI Launcher

Entry point that launches the Streamlit application via subprocess.
Handles environment setup, path configuration, and Windows asyncio policy.
"""

import os
import subprocess
import sys
from pathlib import Path
import asyncio
import logging
import warnings

import dotenv

dotenv.load_dotenv()

# On Windows, set the appropriate event loop policy for asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Suppress Streamlit thread warnings
logging.getLogger("streamlit.runtime.scriptrunner.script_runner").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*missing ScriptRunContext.*")

# Use poll-based file watcher for stability
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "poll"


def main():
    """Launch the Streamlit UI application."""

    # Locate the Streamlit UI script
    here = Path(__file__).parent
    ui_app = here / "src" / "ui" / "streamlit_ui.py"

    if not ui_app.exists():
        print(f"ERROR: Could not find Streamlit UI at {ui_app}", file=sys.stderr)
        sys.exit(1)

    # Add the project root to PYTHONPATH so src.* imports resolve
    project_root = str(here)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{project_root}{os.pathsep}{env.get('PYTHONPATH', '')}"

    # Also set the MCP suite root (parent of streamlit-ui/) for config discovery
    mcp_suite_root = str(here.parent)
    env["MCP_SUITE_ROOT"] = mcp_suite_root

    print(f"MCP Suite Root: {mcp_suite_root}")
    print(f"Launching Streamlit UI: {ui_app}")

    try:
        subprocess.run(
            ["streamlit", "run", str(ui_app)],
            check=True,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to launch Streamlit: {e}", file=sys.stderr)
        sys.exit(e.returncode)
    except FileNotFoundError:
        print(
            "ERROR: 'streamlit' command not found. "
            "Install it with: pip install streamlit",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
