"""Tests for execution-agent gateway MCP tool registration.

Verifies that ``build_execution_agent()`` registers both the four composite
agent tools AND the gateway MCP tools from the ``blazemeter`` and ``jmeter``
namespaces on the agent's LLM catalog.

These tests mock the heavy dependencies (AG2 ConversableAgent, LLMProvider,
mcp_tool_registry) to run in isolation without a live gateway or LLM.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirror existing agent-framework test conventions
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "agent-framework" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_AGENT_PY = _BACKEND_DIR / "agents" / "execution-agent" / "agent.py"


def _load_execution_agent_module() -> ModuleType:
    """Load the execution-agent module via importlib (hyphenated directory)."""
    spec = importlib.util.spec_from_file_location(
        "agents_execution_agent_dynamic", str(_AGENT_PY),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. GATEWAY_MCP_NAMESPACES matches config.example.yaml
# ---------------------------------------------------------------------------

def test_gateway_namespaces_match_config():
    """The module-level GATEWAY_MCP_NAMESPACES must match the
    ``mcp_tools.allowed_namespaces`` list in config.example.yaml."""
    import yaml

    module = _load_execution_agent_module()
    namespaces = module.GATEWAY_MCP_NAMESPACES

    config_path = (
        _BACKEND_DIR / "agents" / "execution-agent" / "config.example.yaml"
    )
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config_namespaces = config["mcp_tools"]["allowed_namespaces"]
    assert sorted(namespaces) == sorted(config_namespaces), (
        f"GATEWAY_MCP_NAMESPACES {namespaces} does not match "
        f"config.example.yaml {config_namespaces}"
    )


# ---------------------------------------------------------------------------
# 2. build_execution_agent calls _register_gateway_tools
# ---------------------------------------------------------------------------

def _make_mock_agent():
    """Build a mock ConversableAgent that accepts tool registration."""
    agent = MagicMock()
    agent.name = "execution-agent"
    agent.register_for_llm.return_value = lambda fn: fn
    agent.register_for_execution.return_value = lambda fn: fn
    return agent


def test_build_calls_register_gateway_tools():
    """build_execution_agent must call _register_gateway_tools after
    _register_tools, resulting in gateway MCP tools being registered."""
    module = _load_execution_agent_module()

    mock_agent = _make_mock_agent()
    mock_registry = AsyncMock(return_value=18)

    with (
        patch.object(module, "_load_system_message", return_value="test prompt"),
        patch.object(module, "_resolve_provider_config", return_value={
            "provider": "openai", "openai_model": "gpt-4o-mini", "temperature": 0.1,
        }),
        patch("autogen.ConversableAgent", return_value=mock_agent),
        patch("services.llm_provider.LLMProvider") as mock_provider_cls,
        patch("services.mcp_client.resolve_gateway_url", return_value="http://localhost:8888/mcp/"),
        patch("services.mcp_tool_registry.register_mcp_tools_on_agent", mock_registry),
    ):
        mock_provider = MagicMock()
        mock_provider.provider = "openai"
        mock_provider.get_model_name.return_value = "gpt-4o-mini"
        mock_provider.to_ag2_config.return_value = {"config_list": []}
        mock_provider_cls.return_value = mock_provider

        agent = module.build_execution_agent()

    assert agent is mock_agent
    mock_registry.assert_called_once()
    call_args = mock_registry.call_args
    assert call_args[0][0] is mock_agent
    assert call_args[0][1] == "http://localhost:8888/mcp/"
    assert sorted(call_args[0][2]) == ["blazemeter", "jmeter"]


# ---------------------------------------------------------------------------
# 3. Agent still builds when gateway is unreachable
# ---------------------------------------------------------------------------

def test_build_succeeds_when_gateway_unreachable():
    """When the gateway is unreachable at build time, the agent must still
    be constructed with its composite tools. Zero MCP tools is acceptable."""
    module = _load_execution_agent_module()

    mock_agent = _make_mock_agent()
    mock_registry = AsyncMock(return_value=0)

    with (
        patch.object(module, "_load_system_message", return_value="test prompt"),
        patch.object(module, "_resolve_provider_config", return_value={
            "provider": "openai", "openai_model": "gpt-4o-mini", "temperature": 0.1,
        }),
        patch("autogen.ConversableAgent", return_value=mock_agent),
        patch("services.llm_provider.LLMProvider") as mock_provider_cls,
        patch("services.mcp_client.resolve_gateway_url", return_value="http://localhost:8888/mcp/"),
        patch("services.mcp_tool_registry.register_mcp_tools_on_agent", mock_registry),
    ):
        mock_provider = MagicMock()
        mock_provider.provider = "openai"
        mock_provider.get_model_name.return_value = "gpt-4o-mini"
        mock_provider.to_ag2_config.return_value = {"config_list": []}
        mock_provider_cls.return_value = mock_provider

        agent = module.build_execution_agent()

    assert agent is mock_agent
    mock_registry.assert_called_once()
