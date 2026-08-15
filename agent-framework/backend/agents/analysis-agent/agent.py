"""PerfPilot Analysis Agent -- AG2 ConversableAgent factory.

This module ships the four-file pattern's ``agent.py`` slot per V2 doc
§7.1. The analysis-agent owns post-test data correlation and verdict
generation: SLA validation (P90 response times against ``slas.yaml``
thresholds), bottleneck attribution (correlating BlazeMeter results
with Datadog infrastructure metrics), and log-error analysis (mapping
failed transactions to root-cause buckets).

**MCP collaboration (auto-discovered at build time via F3.10):**

- PerfAnalysis MCP (gateway, ``perfanalysis_*``) -- automated SLA
  validation, bottleneck detection, comparative analysis, and
  structured analysis output generation. All tools are code-based
  (single attempt, no retry).

**Workflow orchestration** is handled externally by Cursor Skills
(``performance-testing-workflow`` Step 4) and future ``workflows/``
pipelines (F3.11).

Heavy imports (``autogen``, ``yaml``, ``fastmcp``) live inside the
functions that need them so this module is cheap to import in smoke
tests.

NOTE: This module deliberately does NOT use ``from __future__ import
annotations``.

Status:
    F3.9 PBI 3.9.3 -- stub scaffold (no tools registered).
    F3.10 PBI 3.10.4 -- promoted to working agent with MCP auto-discovery.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent

MCP_NAMESPACES = ["perfanalysis"]


def build_analysis_agent():
    """Construct and return the PerfPilot Analysis Agent.

    Returns a ``ConversableAgent`` with PerfAnalysis MCP tools
    auto-discovered from the gateway and registered with full JSON
    schemas.
    """
    try:
        from autogen import ConversableAgent
    except ImportError:
        from ag2 import ConversableAgent

    instructions_path = _AGENT_DIR / "INSTRUCTIONS.md"
    system_message = instructions_path.read_text(encoding="utf-8-sig")

    from utils.config_loader import load_agent_config
    from services.llm_provider import build_llm_config

    agent_config = load_agent_config("analysis-agent")
    llm_config = build_llm_config(agent_config.get("llm_provider"))

    agent = ConversableAgent(
        name="analysis-agent",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )

    tool_count = _register_mcp_tools(agent)
    logger.info(
        "analysis-agent built (F3.10 — %d MCP tools registered)", tool_count,
    )
    return agent


def _register_mcp_tools(agent) -> int:
    """Auto-discover and register PerfAnalysis MCP tools on the agent."""
    import asyncio

    from services.mcp_client import resolve_gateway_url
    from services.mcp_tool_registry import register_mcp_tools_on_agent

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
