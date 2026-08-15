"""PerfPilot Notifications Agent -- AG2 ConversableAgent factory (F3.9 stub).

This module ships the four-file pattern's `agent.py` slot per V2 doc
§7.1.  In F3.9, it contains ONLY the agent factory -- no tool functions
are registered.  The notifications-agent stays a stub through all of
Epic 3; real vendor adapters (Teams, SharePoint, Slack, etc.) wire in
Epic 4.

The notifications-agent owns vendor-neutral event emission: it receives
structured events from the orchestrator (e.g., ``TestRunCompleted``,
``TestRunStarted``, ``ReportPublished``) and routes them to configured
notification channels.  The routing is the adapter concern -- the agent
itself emits a canonical event shape and the adapter translates it to
the vendor-specific API.

**MCP collaboration (Epic 4):**

- MS Teams MCP (standalone, ``msteams_*``) -- message + adaptive card
  delivery to Teams channels and chats.
- SharePoint MCP (standalone, ``sharepoint_*``) -- artifact upload to
  SharePoint document libraries.
- Future adapters (Slack, PagerDuty, email, webhooks) can plug in via
  the same vendor-agnostic event contract.

**No MCP namespaces in Epic 3:** The notifications-agent has an empty
``mcp_tools.allowed_namespaces`` list until Epic 4 wires the adapters.

Heavy imports (``autogen``, ``yaml``) live inside the functions that
need them so this module is cheap to import in smoke tests.

NOTE: This module deliberately does NOT use ``from __future__ import
annotations``.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent


def build_notifications_agent():
    """Construct and return the PerfPilot Notifications Agent.

    No tools are registered in F3.9 (stub) or for all of Epic 3.
    Epic 4 will wire vendor adapters:
    - MS Teams notification delivery
    - SharePoint artifact upload
    - Slack / PagerDuty / email adapters
    """
    try:
        from autogen import ConversableAgent
    except ImportError:
        from ag2 import ConversableAgent

    instructions_path = _AGENT_DIR / "INSTRUCTIONS.md"
    system_message = instructions_path.read_text(encoding="utf-8-sig")

    from utils.config_loader import load_agent_config
    from services.llm_provider import build_llm_config

    agent_config = load_agent_config("notifications-agent")
    llm_config = build_llm_config(agent_config.get("llm_provider"))

    agent = ConversableAgent(
        name="notifications-agent",
        system_message=system_message,
        llm_config=llm_config,
        human_input_mode="NEVER",
    )

    logger.info(
        "notifications-agent built (F3.9 stub — no tools; "
        "vendor adapters wire in Epic 4)"
    )
    return agent
