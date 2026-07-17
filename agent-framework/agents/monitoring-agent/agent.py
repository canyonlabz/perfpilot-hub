"""PerfPilot Monitoring Agent -- AG2 ConversableAgent factory.

This module ships the four-file pattern's ``agent.py`` slot per V2 doc
§7.1. The monitoring-agent owns per-test-run observability data
extraction: pulling host metrics, Kubernetes metrics, APM traces, and
application logs from Datadog during and after a performance test run.
The extracted data feeds the analysis-agent's SLA validation and
bottleneck attribution pipeline.

**MCP collaboration (auto-discovered at build time via F3.10):**

- Datadog MCP (gateway, ``datadog_*``) -- host metrics (CPU, memory,
  disk, network), Kubernetes pod/node metrics, APM service traces, and
  application log queries scoped to a test run's time window.

**Timing contract:**

The monitoring-agent needs the test run's start_time and end_time
(provided by the execution-agent's artifact extraction) to scope its
Datadog queries. It also uses environment-specific host/service
definitions from ``datadog-mcp/environments.json`` and optional custom
queries from ``datadog-mcp/custom_queries.json``.

**Workflow orchestration** is handled externally by Cursor Skills
(``performance-testing-workflow`` Step 3) and future ``workflows/``
pipelines (F3.11). This agent provides tool access; the Skill decides
what to call and when.

Heavy imports (``autogen``, ``yaml``, ``fastmcp``) live inside the
functions that need them so this module is cheap to import in smoke
tests.

NOTE: This module deliberately does NOT use ``from __future__ import
annotations``. AG2 0.13.3 introspects tool function signatures via
pydantic's ``TypeAdapter``, which cannot evaluate stringified
``Annotated`` annotations.

Status:
    F3.9 PBI 3.9.2 -- stub scaffold (no tools registered).
    F3.10 PBI 3.10.3 -- promoted to working agent with MCP auto-discovery.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent

MCP_NAMESPACES = ["datadog"]


def build_monitoring_agent():
    """Construct and return the PerfPilot Monitoring Agent.

    Returns a ``ConversableAgent`` with the system prompt loaded from
    ``INSTRUCTIONS.md``, LLM configuration resolved from the per-agent
    config cascade, and Datadog MCP tools auto-discovered from the
    gateway and registered with full JSON schemas.

    MCP auto-discovery (F3.10): at build time, connects to the FastMCP
    gateway, fetches all ``datadog_*`` tool schemas, and registers them
    on the agent via ``autogen.tools.Tool(parameters_json_schema=...)``.
    If the gateway is unreachable, the agent is still built (graceful
    degradation) but will lack tool schemas in the LLM context.
    """
    import asyncio

    try:
        from autogen import ConversableAgent
    except ImportError:
        from ag2 import ConversableAgent

    instructions_path = _AGENT_DIR / "INSTRUCTIONS.md"
    system_message = instructions_path.read_text(encoding="utf-8-sig")

    from utils.config_loader import load_agent_config
    from utils.llm_provider import build_llm_config

    agent_config = load_agent_config("monitoring-agent")
    llm_config = build_llm_config(agent_config.get("llm_provider"))

    agent = ConversableAgent(
        name="monitoring-agent",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )

    tool_count = _register_mcp_tools(agent)
    logger.info(
        "monitoring-agent built (F3.10 — %d MCP tools registered)", tool_count,
    )
    return agent


def _register_mcp_tools(agent) -> int:
    """Auto-discover and register Datadog MCP tools on the agent.

    Runs the async ``register_mcp_tools_on_agent()`` call synchronously
    since the factory function is called from synchronous contexts
    (importlib module load, orchestrator's ``asyncio.to_thread``).
    """
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
