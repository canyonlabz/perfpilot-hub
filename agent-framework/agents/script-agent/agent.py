"""PerfPilot Script Agent -- AG2 ConversableAgent factory.

This module ships the four-file pattern's ``agent.py`` slot per V2 doc
§7.1. The script-agent owns the JMeter-script lifecycle: convert
HAR/Swagger/Playwright captures to JMX, edit and analyze scripts,
run smoke tests, and iteratively debug with PerfMemory integration.

**MCP collaboration (auto-discovered at build time via F3.10):**

1. JMeter MCP (gateway, ``jmeter_*``) -- JMX creation, editing,
   component manipulation, smoke testing, HAR/Swagger conversion,
   correlation. Code-based (single attempt, no retry).

**Blocked on F3.12:**

- Playwright MCP (direct, ``browser_*``) -- Browser automation for
  live network capture. Requires Playwright MCP container. The
  ``jmeter_capture_network_traffic``, ``jmeter_get_browser_steps``,
  ``jmeter_get_test_specs``, and ``jmeter_archive_playwright_traces``
  tools depend on this and are excluded from the namespace filter
  at the gateway level until F3.12.

**Workflow orchestration** is handled externally by Cursor Skills
(HAR conversion, Swagger conversion, debugging, HITL editing skills).

Heavy imports (``autogen``, ``yaml``, ``fastmcp``) live inside the
functions that need them so this module is cheap to import in smoke
tests.

NOTE: This module deliberately does NOT use ``from __future__ import
annotations``.

Status:
    F3.9 PBI 3.9.1 -- stub scaffold (no tools registered).
    F3.10 PBI 3.10.6 -- partial promotion (JMeter MCP only; Playwright
        deferred to F3.12).
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent

MCP_NAMESPACES = ["jmeter"]


def build_script_agent():
    """Construct and return the PerfPilot Script Agent.

    Returns a ``ConversableAgent`` with JMeter MCP tools
    auto-discovered from the gateway. Playwright (``browser_*``) tools
    are deferred to F3.12.
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

    agent = ConversableAgent(
        name="script-agent",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )

    tool_count = _register_mcp_tools(agent)
    logger.info(
        "script-agent built (F3.10 partial — %d JMeter MCP tools registered; "
        "Playwright deferred to F3.12)", tool_count,
    )
    return agent


def _register_mcp_tools(agent) -> int:
    """Auto-discover and register JMeter MCP tools on the agent."""
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
                register_mcp_tools_on_agent(agent, gateway_url, MCP_NAMESPACES),
            )
            return future.result(timeout=30)
    else:
        return asyncio.run(
            register_mcp_tools_on_agent(agent, gateway_url, MCP_NAMESPACES),
        )
