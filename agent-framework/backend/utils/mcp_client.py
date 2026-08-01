"""FastMCP StreamableHTTP client for the PerfPilot Hub gateway-mcp.

Each agent uses this module to reach MCP tools (BlazeMeter, JMeter, Datadog,
PerfAnalysis, PerfReport, Confluence, PerfMemory, MSTeams, SharePoint)
through the `gateway-mcp` aggregator at `http://localhost:8888/mcp/` (local)
or whatever URL `GATEWAY_MCP_URL` resolves to (production). The script-agent
additionally reaches the Microsoft Playwright MCP container via the
separate URL resolved by `resolve_playwright_url()`.

Two namespace-filter layers protect agents from invoking tools they should
not see:

  1. The agent's `mcp_tools.allowed_namespaces` list in its per-agent
     `config.yaml` declares the prefixes the agent may use (for example
     `["blazemeter", "jmeter"]` for the execution-agent). The orchestrator
     uses `["*"]` to see everything.
  2. `MCPClient.call_tool()` enforces the allowlist on every invocation,
     raising `PermissionError` if a tool name falls outside the agent's
     declared namespaces. `MCPClient.list_tools()` likewise hides
     out-of-namespace tools from the catalog returned to the LLM.

This is a security and clarity boundary, not a hard protocol restriction
(any client process with the gateway URL can call any mounted tool). See
V2 doc Section 13.

Status:
    F3.2 - URL resolution, namespace filtering helper, public API skeleton
        (real client raised `NotImplementedError`).
    F3.7 - status update; client still skeletonized (orchestrator did not
        yet need MCP).
    F3.8.2 (this commit) - real FastMCP `StreamableHttpTransport` wiring.
        Adds `MCPClient` async context manager with cached `list_tools()`
        and allowlist-enforced `call_tool()`. Adds the
        `smoke_test_mcp_client.py` connectivity smoke.
    F3.13 - planned: auth header propagation (gateway-mcp auth surface)
        and OpenTelemetry span hooks.

Heavy imports (`fastmcp`) are deferred into the methods that need them so
this module can be imported in environments without FastMCP installed
(structural smoke test, IDE indexing, etc.).
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)

DEFAULT_GATEWAY_URL = "http://localhost:8888/mcp/"
DEFAULT_PLAYWRIGHT_URL = "http://localhost:8931/mcp"

# FastMCP namespace separator. `gateway.mount(..., namespace="blazemeter")`
# in `gateway-mcp/gateway.py` prefixes every tool from blazemeter-mcp with
# `blazemeter_`. The same convention applies to every other mounted MCP.
# Filter and allowlist checks key off this separator.
NAMESPACE_SEPARATOR = "_"


@dataclass(frozen=True)
class McpClientConfig:
    """Connection configuration for a per-agent MCP client.

    Attributes:
        gateway_url: HTTP endpoint of the PerfPilot Hub gateway. Must end
            with a path that the gateway's FastMCP HTTP transport serves
            the MCP protocol on (default `/mcp/`).
        allowed_namespaces: Tuple of namespace prefixes (for example
            `("blazemeter", "jmeter")`) that this client may invoke.
            Bare-form ("blazemeter") and underscored-form ("blazemeter_")
            are both accepted; the comparison helper appends the
            `NAMESPACE_SEPARATOR` automatically when needed. The
            single-element tuple `("*",)` allows everything
            (orchestrator-only convention).
    """

    gateway_url: str
    allowed_namespaces: tuple[str, ...]


def resolve_gateway_url() -> str:
    """Resolve the gateway-mcp URL.

    Priority:
        1. `GATEWAY_MCP_URL` environment variable
        2. `DEFAULT_GATEWAY_URL` (loopback for local dev)
    """
    return os.environ.get("GATEWAY_MCP_URL", DEFAULT_GATEWAY_URL)


def resolve_playwright_url() -> str:
    """Resolve the Microsoft Playwright MCP URL.

    Used only by the script-agent. Other agents must not call this.
    """
    return os.environ.get("PLAYWRIGHT_MCP_URL", DEFAULT_PLAYWRIGHT_URL)


def build_client_config(allowed_namespaces: Iterable[str]) -> McpClientConfig:
    """Construct an `McpClientConfig` from an agent's `allowed_namespaces`.

    Args:
        allowed_namespaces: Iterable of namespace prefixes from the agent's
            `config.yaml -> mcp_tools.allowed_namespaces`. Duplicates are
            deduplicated and ordering is made deterministic for
            reproducibility (cache hits, log readability).

    Returns:
        Frozen `McpClientConfig` ready to pass to `MCPClient(config)` or
        `create_mcp_client(config)`.
    """
    namespaces = tuple(sorted({n.strip() for n in allowed_namespaces if n and n.strip()}))
    if not namespaces:
        log.warning(
            "build_client_config received an empty allowed_namespaces list; "
            "the resulting client will expose no tools to the LLM"
        )
    return McpClientConfig(
        gateway_url=resolve_gateway_url(),
        allowed_namespaces=namespaces,
    )


# =============================================================================
# Namespace allowlist helpers
# =============================================================================

def _normalize_prefix(namespace: str) -> str:
    """Return `namespace` with `NAMESPACE_SEPARATOR` appended if missing.

    Accepts both bare-form (`"blazemeter"`) and already-suffixed
    (`"blazemeter_"`) namespace declarations. The two are equivalent.
    """
    if namespace == "*":
        return namespace
    return namespace if namespace.endswith(NAMESPACE_SEPARATOR) else namespace + NAMESPACE_SEPARATOR


def is_tool_allowed(tool_name: str, allowed_namespaces: tuple[str, ...]) -> bool:
    """Return True if `tool_name` falls inside any of the allowed namespaces.

    Uses `<ns>_` prefix matching so `"blazemeter"` allows
    `"blazemeter_start_test"` but does NOT accidentally allow
    `"blazemetersomething"` (rare edge case, but worth closing).
    """
    if not allowed_namespaces:
        return False
    if "*" in allowed_namespaces:
        return True
    if not tool_name:
        return False
    for ns in allowed_namespaces:
        if tool_name.startswith(_normalize_prefix(ns)):
            return True
    return False


def filter_tools(
    tool_catalog: list,
    allowed_namespaces: tuple[str, ...],
) -> list:
    """Filter the gateway's full tool catalog to only the allowed namespaces.

    Tool names follow the convention `<namespace>_<tool_name>`, for example
    `blazemeter_start_test` or `jmeter_analyze_jmeter_log`, established by
    `gateway-mcp/gateway.py::gateway.mount(..., namespace="<ns>")`.

    Args:
        tool_catalog: Full list of tools returned by `Client.list_tools()`.
            Each entry may be an `mcp.types.Tool` object (with `.name`) or
            a dict containing a `"name"` key; both shapes are accepted.
        allowed_namespaces: Tuple of allowed prefixes; `("*",)` for "all".

    Returns:
        Filtered list, preserving original tool order.
    """
    if not allowed_namespaces:
        return []
    if "*" in allowed_namespaces:
        return list(tool_catalog)

    filtered: list = []
    for tool in tool_catalog:
        tool_name = _tool_name(tool)
        if tool_name and is_tool_allowed(tool_name, allowed_namespaces):
            filtered.append(tool)
    return filtered


def _tool_name(tool: Any) -> str | None:
    """Extract the tool `.name` regardless of whether `tool` is an SDK object or a dict."""
    if hasattr(tool, "name"):
        return getattr(tool, "name")
    if isinstance(tool, dict):
        return tool.get("name")
    return None


# =============================================================================
# Real FastMCP client (PBI 3.8.2)
# =============================================================================

class MCPClient:
    """Async, namespace-filtered FastMCP client for the gateway-mcp aggregator.

    Use as an async context manager:

        config = build_client_config(["blazemeter", "jmeter"])
        async with MCPClient(config) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "blazemeter_get_artifacts_path", {}
            )

    The client wraps `fastmcp.Client` + `StreamableHttpTransport` so the
    agent-framework never imports FastMCP directly anywhere else. Two
    invariants:

    - `list_tools()` returns only tools whose name prefix matches the
      configured `allowed_namespaces`. Cached on first call (override
      with `use_cache=False`).
    - `call_tool(name, args)` raises `PermissionError` when `name` is
      outside the allowlist, before any network call leaves the agent.

    Retry semantics live one layer up. Per the project's
    `mcp-error-handling` rule, agent tools that wrap MCP calls implement
    their own retry budget (3 retries with 5-10 s back-off for API-based
    MCPs; no retry for code-based MCPs like `jmeter_analyze_jmeter_log`).
    `MCPClient` itself does a single round-trip per `call_tool()` and
    surfaces whatever the gateway returns.
    """

    def __init__(self, config: McpClientConfig):
        self._config = config
        self._client: Any = None
        self._transport: Any = None
        self._tool_catalog_cache: list | None = None

    @property
    def gateway_url(self) -> str:
        return self._config.gateway_url

    @property
    def allowed_namespaces(self) -> tuple[str, ...]:
        return self._config.allowed_namespaces

    async def __aenter__(self) -> "MCPClient":
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        self._transport = StreamableHttpTransport(url=self._config.gateway_url)
        self._client = Client(self._transport)
        await self._client.__aenter__()
        log.debug(
            "MCPClient connected to %s (allowed_namespaces=%s)",
            self._config.gateway_url, self._config.allowed_namespaces,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(exc_type, exc, tb)
            finally:
                self._client = None
                self._transport = None
                self._tool_catalog_cache = None

    async def list_tools(self, *, use_cache: bool = True) -> list:
        """Return all tools the gateway exposes, filtered to the allowlist.

        Cached on first call. Pass `use_cache=False` to force a refetch
        (useful after a gateway restart or namespace-flip test).
        """
        self._require_connected()
        if not use_cache or self._tool_catalog_cache is None:
            self._tool_catalog_cache = await self._client.list_tools()
        return filter_tools(self._tool_catalog_cache, self._config.allowed_namespaces)

    async def list_tools_raw(self) -> list:
        """Return the unfiltered tool catalog (admin / smoke-test use only).

        Bypasses the allowlist filter so smokes can compare
        `len(allowlist_filtered)` vs `len(raw_catalog)` and assert the
        filter behaves. Do NOT use from agent tools; that defeats the
        per-agent namespace boundary documented in V2 §13.
        """
        self._require_connected()
        return await self._client.list_tools()

    async def call_tool(self, name: str, args: dict | None = None) -> Any:
        """Invoke a tool by name. Enforces the allowlist before dispatch.

        Args:
            name: Fully-qualified gateway tool name (e.g.
                `"blazemeter_get_artifacts_path"`).
            args: Keyword arguments for the tool (defaults to empty dict).

        Returns:
            The FastMCP `CallToolResult` returned by the gateway. Callers
            typically read `.data` (structured output) or
            `.content[0].text` (raw text). The agent-framework does NOT
            unwrap on behalf of the caller; the agent tool that wraps
            this call is the right place to normalize.

        Raises:
            PermissionError: when `name` falls outside the agent's
                declared `allowed_namespaces`. Raised before any network
                round-trip.
            fastmcp.exceptions.ToolError: when the gateway-side tool
                itself errors. Caller is responsible for catching and
                applying its own retry policy per the project's
                `mcp-error-handling` rule.
        """
        self._require_connected()
        if not is_tool_allowed(name, self._config.allowed_namespaces):
            raise PermissionError(
                f"Tool '{name}' is outside the allowed namespaces "
                f"{self._config.allowed_namespaces} for this MCP client. "
                "Adjust the agent's config.yaml->mcp_tools.allowed_namespaces "
                "if this is a legitimate addition."
            )
        log.debug("MCPClient.call_tool(%s, %s)", name, args)
        return await self._client.call_tool(name, args or {})

    def _require_connected(self) -> None:
        if self._client is None:
            raise RuntimeError(
                "MCPClient is not connected. Use it as an async context manager: "
                "`async with MCPClient(config) as client: ...`"
            )


async def create_mcp_client(config: McpClientConfig) -> MCPClient:
    """Construct an `MCPClient` (not yet connected).

    Equivalent to `MCPClient(config)`; kept as a thin factory so the
    F3.2 public API surface stays stable for callers that import the
    function name.

    The returned client is **not yet connected** -- the caller must
    enter it as an async context manager before invoking
    `list_tools()` / `call_tool()`.
    """
    return MCPClient(config)
