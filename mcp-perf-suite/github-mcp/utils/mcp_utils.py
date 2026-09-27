"""Shared HTTP utility helpers for the MCP entrypoint."""

import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


def register_health_route(mcp: FastMCP, default_prefix: str, server_name: str) -> None:
    """Register a prefixed ``/health`` route on the given FastMCP instance.

    The route path is ``<MCP_HTTP_PREFIX>/health`` where ``MCP_HTTP_PREFIX``
    is read from the environment (falling back to ``default_prefix``).
    Prefixing the health path prevents collisions when multiple MCP servers
    share the same hostname behind a gateway or ingress in cloud deployments.

    The endpoint responds to ``GET`` with ``200 OK`` and a small JSON body:

        {"status": "healthy", "server": "<server_name>"}

    Args:
        mcp: The FastMCP instance to register the route on.
        default_prefix: Fallback path prefix if ``MCP_HTTP_PREFIX`` is unset.
        server_name: Value returned in the ``server`` field of the JSON body.
    """
    prefix = os.environ.get("MCP_HTTP_PREFIX", default_prefix)

    @mcp.custom_route(f"{prefix}/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "server": server_name})
