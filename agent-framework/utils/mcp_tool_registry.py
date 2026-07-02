"""MCP Auto-Discovery Tool Registry for AG2 ConversableAgents.

Fetches live MCP tool schemas from the FastMCP gateway and registers them
as fully-typed AG2 ``Tool`` objects on ``ConversableAgent`` instances.
Each registered tool wraps a single gateway MCP call with the project's
NEVER-raise contract applied.

Core pattern (validated in Perplexity AG2-and-MCP-Bridge research):

    FastMCP Client.list_tools()
        -> namespace filter (e.g. "datadog_")
        -> autogen.tools.Tool(parameters_json_schema=mcp_tool.inputSchema)
        -> Tool.register_for_llm(agent)        # LLM sees exact JSON Schema
        -> Tool.register_for_execution(agent)   # AG2 can invoke the function

Both ``register_for_llm`` and ``register_for_execution`` target the SAME
``ConversableAgent``. This matches the execution-agent's proven pattern
where the specialist plays both LLM and executor roles.

No new dependencies: uses ``fastmcp.Client`` (fastmcp>=3.4.2) and
``autogen.tools.Tool`` (ag2>=0.13.3), both already in requirements.txt.

Retry policy per the project's ``mcp-error-handling`` rule:
    - API-based namespaces (blazemeter, datadog, confluence): retry up
      to 3x with 5s back-off on transient failures.
    - Code-based namespaces (perfanalysis, perfreport, jmeter, etc.):
      single attempt, surface errors immediately.

Heavy imports (``fastmcp``, ``autogen.tools``) are deferred into the
functions that need them so this module is cheap to import in
environments that don't exercise auto-discovery.

Status:
    F3.10 PBI 3.10.1 -- initial implementation.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespace -> retry classification
# ---------------------------------------------------------------------------

API_BASED_NAMESPACES: frozenset[str] = frozenset({
    "blazemeter", "datadog", "confluence",
})
CODE_BASED_NAMESPACES: frozenset[str] = frozenset({
    "perfanalysis", "perfreport", "jmeter",
    "perfmemory", "msteams", "sharepoint",
})

STATEFUL_NAMESPACES: frozenset[str] = frozenset({
    "browser",
})

_MAX_RETRY_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 5.0

# ---------------------------------------------------------------------------
# Process-scoped tool catalog cache (keyed by gateway_url)
# ---------------------------------------------------------------------------

_tool_catalog_cache: dict[str, list] = {}


def _clear_tool_cache() -> None:
    """Clear the process-scoped tool catalog cache.

    Intended for test isolation: prevents stale schemas from a prior
    test fixture leaking into subsequent tests within the same process.
    """
    _tool_catalog_cache.clear()


# ---------------------------------------------------------------------------
# CallToolResult normalization (NEVER-raise)
# ---------------------------------------------------------------------------

def _extract_and_normalize(result: Any) -> str:
    """Extract content from a FastMCP ``CallToolResult`` and normalize to
    a string suitable for the LLM's tool-result message.

    Handles the shapes documented in FastMCP's response model:

    - ``isError=True`` -> JSON error envelope ``{ok: false, error: {...}}``
    - ``.data`` attribute (structured return) -> JSON-serialize
    - Single ``TextContent`` in ``.content`` -> extract ``.text``
    - Multiple ``TextContent`` items -> join text values into JSON array
    - Fallback -> ``str(result)``

    NEVER raises. Normalization failures are caught and returned as
    structured error envelopes.
    """
    try:
        if getattr(result, "isError", False):
            content = getattr(result, "content", []) or []
            error_texts = []
            for c in content:
                text = getattr(c, "text", None)
                if text is not None:
                    error_texts.append(text)
            return json.dumps({
                "ok": False,
                "error": {
                    "mcp_error": " ".join(error_texts) if error_texts else str(content),
                },
            })

        data = getattr(result, "data", None)
        if data is not None:
            if isinstance(data, str):
                return data
            return json.dumps(data)

        content = getattr(result, "content", None) or []
        texts = [c.text for c in content if hasattr(c, "text")]
        if len(texts) == 1:
            return texts[0]
        if len(texts) > 1:
            return json.dumps(texts)

        return str(result)
    except Exception as e:
        return json.dumps({
            "ok": False,
            "error": {"message": f"Response normalization failed: {e}"},
        })


# ---------------------------------------------------------------------------
# Namespace classification
# ---------------------------------------------------------------------------

def _is_api_based(tool_name: str) -> bool:
    """Return True if ``tool_name`` belongs to an API-based namespace.

    API-based tools are eligible for retry on transient failure.
    Code-based tools must never retry (a retry will not change a
    deterministic outcome).
    """
    for ns in API_BASED_NAMESPACES:
        if tool_name.startswith(ns + "_"):
            return True
    return False


def _is_stateful(tool_name: str) -> bool:
    """Return True if ``tool_name`` belongs to a stateful namespace
    requiring a persistent client connection across the tool loop.

    Stateful tools (e.g. Playwright ``browser_*``) maintain server-side
    session state (page, cookies, DOM) that must survive between calls.
    """
    for ns in STATEFUL_NAMESPACES:
        if tool_name.startswith(ns + "_"):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool wrapper factories (proper closure capture, no default-arg hack)
# ---------------------------------------------------------------------------

def _make_api_tool_wrapper(
    tool_name: str,
    gateway_url: str,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Build an async wrapper for an API-based MCP tool (retry-eligible).

    Parameters are function arguments (not closure captures), so each
    wrapper is fully isolated regardless of loop iteration order.
    """
    async def _call_tool(**kwargs: Any) -> str:
        from fastmcp import Client

        last_error: BaseException | None = None
        for attempt in range(1, _MAX_RETRY_ATTEMPTS + 1):
            try:
                async with Client(gateway_url) as c:
                    result = await c.call_tool(tool_name, kwargs)
                return _extract_and_normalize(result)
            except Exception as e:
                last_error = e
                if attempt < _MAX_RETRY_ATTEMPTS:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
        return json.dumps({
            "ok": False,
            "error": {
                "type": type(last_error).__name__,
                "message": str(last_error)[:500],
                "attempts": _MAX_RETRY_ATTEMPTS,
            },
        })

    return _call_tool


def _make_code_tool_wrapper(
    tool_name: str,
    gateway_url: str,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Build an async wrapper for a code-based MCP tool (single attempt).

    Per ``mcp-error-handling``: code-based MCPs do NOT retry on failure.
    """
    async def _call_tool(**kwargs: Any) -> str:
        from fastmcp import Client

        try:
            async with Client(gateway_url) as c:
                result = await c.call_tool(tool_name, kwargs)
            return _extract_and_normalize(result)
        except Exception as e:
            return json.dumps({
                "ok": False,
                "error": {
                    "type": type(e).__name__,
                    "message": str(e)[:500],
                },
            })

    return _call_tool


def _make_stateful_tool_wrapper(
    tool_name: str,
    client_holder: dict,
) -> Callable[..., Coroutine[Any, Any, str]]:
    """Build an async wrapper for a stateful MCP tool that uses a shared
    persistent client reference instead of creating a new connection per call.

    The ``client_holder`` dict contains a ``"client"`` key pointing to the
    active ``fastmcp.Client`` instance. The client lifecycle is managed
    externally — opened before the multi-turn loop starts, closed after
    the loop exits (including error/early-exit paths).

    Args:
        tool_name: Fully-qualified MCP tool name (e.g. ``browser_navigate``).
        client_holder: Mutable dict ``{"client": Client | None}`` shared
            across all stateful wrappers for the same session.
    """
    async def _call_tool(**kwargs: Any) -> str:
        client = client_holder.get("client")
        if client is None:
            return json.dumps({
                "ok": False,
                "error": {
                    "type": "SessionNotAvailable",
                    "message": (
                        f"No persistent MCP session available for stateful "
                        f"tool '{tool_name}'. The session may have been "
                        f"closed or was never opened."
                    ),
                },
            })
        try:
            result = await client.call_tool(tool_name, kwargs)
            return _extract_and_normalize(result)
        except Exception as e:
            return json.dumps({
                "ok": False,
                "error": {
                    "type": type(e).__name__,
                    "message": str(e)[:500],
                },
            })

    return _call_tool


# ---------------------------------------------------------------------------
# Gateway catalog fetch (cached)
# ---------------------------------------------------------------------------

async def _fetch_tool_catalog(gateway_url: str) -> list:
    """Fetch and cache the full tool catalog from the gateway.

    Cache is keyed by ``gateway_url`` so different test fixtures (or
    mock vs real gateway) don't collide. Cache lives for the process
    lifetime; call ``_clear_tool_cache()`` for test isolation.
    """
    if gateway_url in _tool_catalog_cache:
        log.debug(
            "[mcp_tool_registry] Using cached catalog for %s (%d tools)",
            gateway_url, len(_tool_catalog_cache[gateway_url]),
        )
        return _tool_catalog_cache[gateway_url]

    from fastmcp import Client

    async with Client(gateway_url) as client:
        tools = await client.list_tools()

    _tool_catalog_cache[gateway_url] = tools
    log.info(
        "[mcp_tool_registry] Fetched and cached %d tools from %s",
        len(tools), gateway_url,
    )
    return tools


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def register_mcp_tools_on_agent(
    agent: Any,
    gateway_url: str,
    allowed_namespaces: list[str],
    stateful_client_holder: dict | None = None,
) -> int:
    """Fetch live MCP tool schemas from the FastMCP gateway and register
    them on an existing ``ConversableAgent`` with full JSON schemas.

    Each matching tool is wrapped in an ``autogen.tools.Tool`` object
    that carries the gateway's ``inputSchema`` verbatim, so the LLM
    sees the exact parameter contract at inference time -- eliminating
    the "passes a string where a list is expected" class of errors.

    The wrapper functions honour the project's NEVER-raise contract:
    all code paths return a string (either the MCP response or a
    structured JSON error envelope). No exceptions surface to the agent.

    Graceful degradation: if the gateway is unreachable at build time,
    a warning is logged and 0 is returned. The agent still functions
    with its system prompt; it just won't have tool schemas injected.

    Args:
        agent: An AG2 ``ConversableAgent`` instance. Both
            ``register_for_llm`` and ``register_for_execution`` target
            this same agent (matching the execution-agent's proven
            pattern where the specialist plays both LLM and executor
            roles).
        gateway_url: URL of the FastMCP gateway (e.g.
            ``http://localhost:8000/mcp/``).
        allowed_namespaces: List of namespace prefixes to filter by
            (e.g. ``["datadog"]``). A tool named ``datadog_get_logs``
            matches the namespace ``"datadog"`` via ``<ns>_`` prefix.
        stateful_client_holder: Optional mutable dict
            ``{"client": Client | None}`` for stateful namespaces
            (e.g. Playwright ``browser_*``). When provided, tools
            belonging to ``STATEFUL_NAMESPACES`` use a shared
            persistent client instead of per-call connections. When
            ``None`` (default), all tools use per-call connections
            (backward-compatible behavior).

    Returns:
        Count of tools successfully registered. Zero when the gateway
        is unreachable or no tools match the namespace filter.
    """
    from autogen.tools import Tool

    try:
        mcp_tools = await _fetch_tool_catalog(gateway_url)
    except Exception as e:
        log.warning(
            "[mcp_tool_registry] Gateway unreachable at %s: %s -- "
            "agent '%s' will function without MCP tool schemas",
            gateway_url, e, agent.name,
        )
        return 0

    registered = 0
    for mcp_tool in mcp_tools:
        tool_name = getattr(mcp_tool, "name", None)
        if not tool_name:
            continue

        if not any(tool_name.startswith(ns + "_") for ns in allowed_namespaces):
            continue

        tool_description = getattr(mcp_tool, "description", "") or tool_name
        tool_schema = getattr(mcp_tool, "inputSchema", None)

        if _is_stateful(tool_name) and stateful_client_holder is not None:
            wrapper = _make_stateful_tool_wrapper(tool_name, stateful_client_holder)
        elif _is_api_based(tool_name):
            wrapper = _make_api_tool_wrapper(tool_name, gateway_url)
        else:
            wrapper = _make_code_tool_wrapper(tool_name, gateway_url)

        wrapper.__name__ = tool_name
        wrapper.__doc__ = tool_description

        ag2_tool = Tool(
            name=tool_name,
            description=tool_description,
            func_or_tool=wrapper,
            parameters_json_schema=tool_schema,
        )
        ag2_tool.register_for_llm(agent)
        ag2_tool.register_for_execution(agent)
        registered += 1

    if registered > 25:
        log.warning(
            "[mcp_tool_registry] %s registered %d tools -- "
            "consider namespace splitting if LLM accuracy degrades",
            agent.name, registered,
        )

    log.info(
        "[mcp_tool_registry] Registered %d tools on '%s' (namespaces=%s)",
        registered, agent.name, allowed_namespaces,
    )
    return registered
