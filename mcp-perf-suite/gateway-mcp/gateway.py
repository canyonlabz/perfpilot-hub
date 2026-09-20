"""
PerfPilot Hub — MCP Gateway for Performance Testing

Aggregates the 8 gateway-mounted MCP servers behind a single FastMCP
endpoint. Each remote MCP is mounted over streamable-HTTP transport
using ``fastmcp.server.create_proxy(url)``; the parent gateway then
serves the aggregated tool catalog to any MCP client.

Endpoints are configured via ``MCP_URL_<NAME>`` environment variables
(uppercase, one per MCP) or via ``mcp_urls`` entries in ``config.yaml``.
Environment variables take precedence. Empty or unset URLs skip that
MCP without failing the gateway startup.

Local-only servers (``msteams``, ``sharepoint``) are intentionally not
mounted here — they continue to run as stdio processes registered
directly in the MCP client.

Transports:
    * ``MCP_TRANSPORT=http`` (default in Docker): serve at
      ``MCP_HTTP_PREFIX + "/mcp"`` on ``HTTP_PORT``.
    * ``MCP_TRANSPORT=stdio`` (default locally): serve on stdio so
      the gateway can be registered directly in a local MCP client.
"""
import logging
import os

from fastmcp import FastMCP
from fastmcp.server import create_proxy

from utils.config import load_config

log = logging.getLogger(__name__)

# The 8 gateway-mounted MCPs, in canonical order. Namespace is the tool
# prefix exposed by the aggregator; env_var is the override; default_key
# looks up the fallback URL in ``config["mcp_urls"]``.
_MCP_MOUNTS = (
    ("jmeter",       "MCP_URL_JMETER"),
    ("blazemeter",   "MCP_URL_BLAZEMETER"),
    ("datadog",      "MCP_URL_DATADOG"),
    ("perfanalysis", "MCP_URL_PERFANALYSIS"),
    ("perfreport",   "MCP_URL_PERFREPORT"),
    ("confluence",   "MCP_URL_CONFLUENCE"),
    ("perfmemory",   "MCP_URL_PERFMEMORY"),
    ("github",       "MCP_URL_GITHUB"),
)

config = load_config()
server_cfg = config.get("server", {})
mcp_urls_cfg = config.get("mcp_urls", {}) or {}

gateway = FastMCP(server_cfg.get("name", "perfpilot-mcp-gateway"))


def _resolve_url(namespace: str, env_var: str) -> str:
    """Return the URL to mount for ``namespace``.

    Precedence: environment variable > config file > empty string.
    """
    url = os.environ.get(env_var, "").strip()
    if url:
        return url
    return str(mcp_urls_cfg.get(namespace, "")).strip()


def _mount_remotes() -> None:
    """Mount every configured remote MCP on the parent gateway."""
    mounted = 0
    for namespace, env_var in _MCP_MOUNTS:
        url = _resolve_url(namespace, env_var)
        if not url:
            log.info("gateway: skipping %s (no URL configured)", namespace)
            continue
        try:
            gateway.mount(create_proxy(url), namespace=namespace)
            log.info("gateway: mounted %s -> %s", namespace, url)
            mounted += 1
        except Exception as exc:  # pragma: no cover - defensive
            log.error(
                "gateway: failed to mount %s (%s): %s",
                namespace,
                url,
                exc,
            )
    log.info("gateway: %d/%d remote MCPs mounted", mounted, len(_MCP_MOUNTS))


_mount_remotes()


if __name__ == "__main__":
    from utils.logging_config import configure_logging
    from utils.mcp_utils import register_health_route

    configure_logging()
    try:
        if os.environ.get("MCP_TRANSPORT", server_cfg.get("transport", "stdio")) == "http":
            prefix = os.environ.get("MCP_HTTP_PREFIX", "/perfpilot-mcp-gateway")
            port = int(os.environ.get("HTTP_PORT", server_cfg.get("port", 8125)))

            # Register the prefixed /health route (HTTP transport only).
            register_health_route(gateway, prefix, "perfpilot-mcp-gateway")

            gateway.run(
                transport="http",
                host=os.environ.get("HTTP_HOST", server_cfg.get("host", "0.0.0.0")),
                port=port,
                path=prefix + "/mcp",
            )
        else:
            gateway.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down PerfPilot Gateway MCP…")
