"""PerfPilot Script Agent — AG2 ConversableAgent factory.

The script-agent owns the JMeter-script lifecycle: convert
HAR/Swagger/Playwright captures to JMX, edit and analyze scripts,
run smoke tests, and iteratively debug with PerfMemory integration.

MCP collaboration (auto-discovered at build time):

1. JMeter MCP (via gateway, ``jmeter_*``) — JMX creation, editing,
   component manipulation, smoke testing, HAR/Swagger conversion,
   correlation, network traffic capture/analysis. Code-based (single
   attempt, no retry).

2. Playwright MCP (direct connection, ``browser_*``) — Browser
   automation for live network capture driven by test specification
   files. The Playwright MCP container (Microsoft ``playwright-mcp``)
   runs as a separate service and the script-agent connects directly
   to it (not through the gateway).

Heavy imports (``autogen``, ``yaml``, ``fastmcp``) live inside the
functions that need them so this module is cheap to import in smoke
tests.

NOTE: This module deliberately does NOT use ``from __future__ import
annotations``.
"""

import logging
import os
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent

GATEWAY_MCP_NAMESPACES = ["jmeter"]
PLAYWRIGHT_MCP_NAMESPACE = ["browser"]

# Playwright MCP endpoint resolution:
#   1. PLAYWRIGHT_MCP_URL env var (operator override)
#   2. Docker internal: http://playwright-mcp:8931 (when PERFPILOT_DOCKER=true)
#   3. Local default: http://localhost:8931
_DEFAULT_PLAYWRIGHT_URL_DOCKER = "http://playwright-mcp:8931/mcp"
_DEFAULT_PLAYWRIGHT_URL_LOCAL = "http://localhost:8931/mcp"


def _resolve_playwright_url(agent_config: dict) -> str | None:
    """Resolve the Playwright MCP endpoint URL.

    Returns None if Playwright is explicitly disabled in config.
    """
    playwright_cfg = agent_config.get("playwright_mcp", {})
    if not playwright_cfg.get("enabled", True):
        return None

    # Explicit env var takes priority
    env_url = os.environ.get("PLAYWRIGHT_MCP_URL")
    if env_url:
        return env_url

    # Config file override
    cfg_url = playwright_cfg.get("endpoint")
    if cfg_url:
        return cfg_url

    # Auto-detect Docker vs local
    if os.environ.get("PERFPILOT_DOCKER", "").lower() in ("true", "1"):
        return _DEFAULT_PLAYWRIGHT_URL_DOCKER
    return _DEFAULT_PLAYWRIGHT_URL_LOCAL


def build_script_agent(stateful_client_holder: dict | None = None):
    """Construct and return the PerfPilot Script Agent.

    Args:
        stateful_client_holder: Optional mutable dict
            ``{"client": Client | None}`` for Playwright browser tools.
            When provided, ``browser_*`` tools use the shared persistent
            client instead of per-call connections, preserving browser
            state across the multi-turn tool loop. When ``None``
            (default), all tools use per-call connections (backward-
            compatible with non-Playwright workflows).

    Returns a ``ConversableAgent`` with:
    - JMeter MCP tools auto-discovered from the gateway
    - Playwright MCP tools auto-discovered from the Playwright container
    """
    import yaml

    try:
        from autogen import ConversableAgent
    except ImportError:
        from ag2 import ConversableAgent

    instructions_path = _AGENT_DIR / "INSTRUCTIONS.md"
    system_message = instructions_path.read_text(encoding="utf-8-sig")

    from utils.base_agent import resolve_agent_config_path
    from utils.llm_provider import build_llm_config

    config_path = resolve_agent_config_path(_AGENT_DIR)
    with open(config_path, encoding="utf-8-sig") as fh:
        agent_config = yaml.safe_load(fh) or {}

    llm_config = build_llm_config(agent_config.get("llm_provider"))

    if agent_config.get("force_tool_choice", False):
        llm_config["tool_choice"] = "required"

    agent = ConversableAgent(
        name="script-agent",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )

    # Register JMeter tools (via gateway)
    jmeter_count = _register_gateway_tools(agent)

    # Register Playwright tools (direct connection)
    playwright_url = _resolve_playwright_url(agent_config)
    playwright_count = 0
    if playwright_url:
        playwright_count = _register_playwright_tools(
            agent, playwright_url, stateful_client_holder,
        )

    total = jmeter_count + playwright_count
    logger.info(
        "script-agent built — %d JMeter tools + %d Playwright tools = %d total",
        jmeter_count, playwright_count, total,
    )
    return agent


def _register_gateway_tools(agent) -> int:
    """Auto-discover and register JMeter MCP tools from the gateway."""
    import asyncio

    from utils.mcp_client import resolve_gateway_url
    from utils.mcp_tool_registry import register_mcp_tools_on_agent

    gateway_url = resolve_gateway_url()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                register_mcp_tools_on_agent(agent, gateway_url, GATEWAY_MCP_NAMESPACES),
            )
            return future.result(timeout=30)
    else:
        return asyncio.run(
            register_mcp_tools_on_agent(agent, gateway_url, GATEWAY_MCP_NAMESPACES),
        )


def _register_playwright_tools(
    agent,
    playwright_url: str,
    stateful_client_holder: dict | None = None,
) -> int:
    """Auto-discover and register Playwright browser tools (direct connection).

    When ``stateful_client_holder`` is provided, ``browser_*`` tools are
    registered with a shared persistent client wrapper instead of the
    default per-call connection wrapper.
    """
    import asyncio

    from utils.mcp_tool_registry import register_mcp_tools_on_agent

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    coro = register_mcp_tools_on_agent(
        agent, playwright_url, PLAYWRIGHT_MCP_NAMESPACE,
        stateful_client_holder=stateful_client_holder,
    )

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        return asyncio.run(coro)
