"""PerfPilot Notifications Agent factory.

Builds a native ``ag2.Agent`` from this folder's four-file definition.
No tools are registered yet. Vendor adapters (Teams, SharePoint, and
others) are not wired in this module.

This module does not use ``from __future__ import annotations``. Native
tool registration introspects live annotations.
"""

import logging
import pathlib

logger = logging.getLogger(__name__)

_AGENT_DIR = pathlib.Path(__file__).resolve().parent


def build_notifications_agent():
    """Construct and return the PerfPilot Notifications Agent.

    The agent is returned with its instructions and harness and with
    no tools attached.
    """
    from core.agent_harness import build_agent_harness
    from core.base_agent import create_agent, load_agent_definition

    definition = load_agent_definition(_AGENT_DIR)
    harness = build_agent_harness(definition.name)
    agent = create_agent(definition, **harness.agent_kwargs())
    if isinstance(agent, dict):
        logger.info("notifications-agent is disabled in its config")
        return agent

    logger.info("notifications-agent built — no tools registered")
    return agent
