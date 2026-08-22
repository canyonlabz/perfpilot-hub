"""
PerfPilot Hub — MCP Gateway for Performance Testing

The central MCP gateway for the MCP Perf Suite. Gives AI agents a single
endpoint into the performance testing lifecycle, routing them to specialized
MCP servers via FastMCP v3's create_proxy() with subprocess isolation.

Each server runs in its own process with its own venv — no shared
dependencies, no import collisions.

Supports stdio (local Cursor) and http (Docker/A2A) transports.
"""
import os
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server import create_proxy

from utils.config import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
IS_DOCKER = os.environ.get("PERFPILOT_DOCKER", "").lower() == "true"

config = load_config()
server_cfg = config.get("server", {})
servers_cfg = config.get("servers", {})

gateway = FastMCP(server_cfg.get("name", "perfpilot-hub"))


def _server_config(server_dir: str, script: str) -> dict:
    """Build an MCP server config dict for create_proxy().

    In Docker mode (PERFPILOT_DOCKER=true): uses system Python and /app/ paths.
    In local mode: uses each server's own venv Python and repo-relative paths.
    """
    if IS_DOCKER:
        server_path = Path("/app") / server_dir
        python_cmd = "/usr/local/bin/python"
    else:
        server_path = REPO_ROOT / server_dir
        venv_python = server_path / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            venv_python = server_path / ".venv" / "bin" / "python"
        python_cmd = str(venv_python)

    server_entry = {
        "command": python_cmd,
        "args": [script],
        "cwd": str(server_path),
    }

    # In Docker mode, explicitly forward all environment variables to subprocesses.
    # FastMCP's create_proxy() does not automatically inherit the parent environment.
    if IS_DOCKER:
        server_entry["env"] = dict(os.environ)

    # Pass SSL cert file to subprocesses if configured (optional)
    ssl_cert_file = server_cfg.get("ssl_cert_file")
    if ssl_cert_file:
        server_entry.setdefault("env", {})["SSL_CERT_FILE"] = ssl_cert_file

    return {"mcpServers": {"default": server_entry}}


# --- Core servers ---
if servers_cfg.get("jmeter", True):
    gateway.mount(
        create_proxy(_server_config("jmeter-mcp", "jmeter.py")),
        namespace="jmeter",
    )

if servers_cfg.get("blazemeter", True):
    gateway.mount(
        create_proxy(_server_config("blazemeter-mcp", "blazemeter.py")),
        namespace="blazemeter",
    )

if servers_cfg.get("datadog", True):
    gateway.mount(
        create_proxy(_server_config("datadog-mcp", "datadog.py")),
        namespace="datadog",
    )

if servers_cfg.get("perfanalysis", True):
    gateway.mount(
        create_proxy(_server_config("perfanalysis-mcp", "perfanalysis.py")),
        namespace="perfanalysis",
    )

if servers_cfg.get("perfreport", True):
    gateway.mount(
        create_proxy(_server_config("perfreport-mcp", "perfreport.py")),
        namespace="perfreport",
    )

if servers_cfg.get("confluence", True):
    gateway.mount(
        create_proxy(_server_config("confluence-mcp", "confluence.py")),
        namespace="confluence",
    )

if servers_cfg.get("perfmemory", True):
    gateway.mount(
        create_proxy(_server_config("perfmemory-mcp", "perfmemory.py")),
        namespace="perfmemory",
    )

# --- Local-only servers ---
if servers_cfg.get("msteams", True):
    gateway.mount(
        create_proxy(_server_config("msteams-mcp", "msteams.py")),
        namespace="msteams",
    )

if servers_cfg.get("sharepoint", True):
    gateway.mount(
        create_proxy(_server_config("sharepoint-mcp", "sharepoint.py")),
        namespace="sharepoint",
    )


# --- Health check endpoint (HTTP transport only) ---
from starlette.requests import Request
from starlette.responses import JSONResponse


@gateway.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "server": "perfpilot-hub"})


if __name__ == "__main__":
    transport = os.environ.get(
        "GATEWAY_TRANSPORT", server_cfg.get("transport", "stdio")
    )

    if transport == "http":
        host = os.environ.get("GATEWAY_HOST", server_cfg.get("host", "0.0.0.0"))
        port = int(os.environ.get("GATEWAY_PORT", server_cfg.get("port", 8000)))
        gateway.run(transport="http", host=host, port=port)
    else:
        gateway.run(transport="stdio")
