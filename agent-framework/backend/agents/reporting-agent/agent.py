"""PerfPilot Reporting Agent -- AG2 ConversableAgent factory.

This module ships the four-file pattern's ``agent.py`` slot per V2 doc
§7.1. The reporting-agent owns the final-mile delivery of performance
test results: chart generation from analysis data, Markdown report
assembly, multi-round Human-in-the-Loop revision (the only specialist
that drives HITL revision loops), and Confluence publishing.

**MCP collaboration (auto-discovered at build time via F3.10):**

- PerfReport MCP (gateway, ``perfreport_*``) -- chart generation (PNG),
  Markdown report creation, AI-driven report revision, template
  management. Code-based (single attempt, no retry).
- Confluence MCP (gateway, ``confluence_*``) -- page creation, content
  update, image attachment, space/page navigation. API-based (retry up
  to 3x with 5s back-off).

**Workflow orchestration** is handled externally by three Cursor Skills:
- ``performance-testing-workflow`` Step 5 -- report + chart generation
- ``report-revision-workflow`` -- HITL iterative revision loop
- ``comparison-report-workflow`` -- multi-run comparison reports

Heavy imports (``autogen``, ``yaml``, ``fastmcp``) live inside the
functions that need them so this module is cheap to import in smoke
tests.

NOTE: This module deliberately does NOT use ``from __future__ import
annotations``.

Status:
    F3.9 PBI 3.9.4 -- stub scaffold (no tools registered).
    F3.10 PBI 3.10.5 -- promoted to working agent with MCP auto-discovery.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent

MCP_NAMESPACES = ["perfreport", "confluence"]


def build_reporting_agent():
    """Construct and return the PerfPilot Reporting Agent.

    Returns a ``ConversableAgent`` with PerfReport + Confluence MCP
    tools auto-discovered from the gateway and registered with full
    JSON schemas.
    """
    try:
        from autogen import ConversableAgent
    except ImportError:
        from ag2 import ConversableAgent

    instructions_path = _AGENT_DIR / "INSTRUCTIONS.md"
    system_message = instructions_path.read_text(encoding="utf-8-sig")

    from utils.config_loader import load_agent_config
    from utils.llm_provider import build_llm_config

    agent_config = load_agent_config("reporting-agent")
    llm_config = build_llm_config(agent_config.get("llm_provider"))

    agent = ConversableAgent(
        name="reporting-agent",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )

    tool_count = _register_mcp_tools(agent)
    logger.info(
        "reporting-agent built (F3.10 — %d MCP tools registered)", tool_count,
    )
    return agent


def _register_mcp_tools(agent) -> int:
    """Auto-discover and register PerfReport + Confluence MCP tools."""
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
