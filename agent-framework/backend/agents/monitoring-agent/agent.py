"""PerfPilot Monitoring Agent factory.

Builds a native ``ag2.Agent`` from this folder's four-file definition
and registers Datadog gateway tools (``datadog_*``). The agent pulls
host metrics, Kubernetes metrics, APM traces, and application logs for
a test window supplied by the caller.

This module does not use ``from __future__ import annotations``. Native
tool registration introspects live annotations.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent

MCP_NAMESPACES = ["datadog"]


def build_monitoring_agent():
    """Construct and return the PerfPilot Monitoring Agent.

    Returns a native ``ag2.Agent`` whose prompt comes from
    ``INSTRUCTIONS.md`` and whose Datadog tools are discovered from the
    gateway. If the gateway is unreachable, the agent is still returned
    with an empty tool catalog.
    """
    from core.agent_harness import build_agent_harness
    from core.base_agent import create_agent, load_agent_definition

    definition = load_agent_definition(_AGENT_DIR)
    harness = build_agent_harness(definition.name)
    agent = create_agent(definition, **harness.agent_kwargs())
    if isinstance(agent, dict):
        logger.info("monitoring-agent is disabled in its config")
        return agent

    tool_count = _register_mcp_tools(agent)
    logger.info("monitoring-agent built — %d MCP tools registered", tool_count)
    return agent


def _register_mcp_tools(agent) -> int:
    """Auto-discover and register Datadog MCP tools on the agent.

    Runs the async ``register_mcp_tools_on_agent()`` call synchronously
    since the factory function is called from synchronous contexts
    (importlib module load, orchestrator's ``asyncio.to_thread``).
    """
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
