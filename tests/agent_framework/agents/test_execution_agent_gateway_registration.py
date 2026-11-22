"""Tests for execution-agent native tool registration.

Verifies that ``build_execution_agent()`` attaches the four composite
tools and asks the gateway registry for the ``blazemeter`` and
``jmeter`` namespaces.

``create_agent``, the harness, and the gateway registry are stubbed.
Composite-tool wrapping uses the installed AG2 tool decorator so a
bad parameter annotation fails here. No live gateway or model call.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "agent-framework" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_AGENT_PY = _BACKEND_DIR / "agents" / "execution-agent" / "agent.py"

_COMPOSITE_TOOLS = {
    "start_performance_test",
    "wait_for_completion",
    "extract_test_run_artifacts",
    "provision_performance_test",
}


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


def test_gateway_namespaces_match_config():
    """``GATEWAY_MCP_NAMESPACES`` matches ``config.example.yaml``."""
    module = _load_execution_agent_module()
    namespaces = module.GATEWAY_MCP_NAMESPACES

    config_path = (
        _BACKEND_DIR / "agents" / "execution-agent" / "config.example.yaml"
    )
    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    config_namespaces = config["mcp_tools"]["allowed_namespaces"]
    assert sorted(namespaces) == sorted(config_namespaces)


def _harness_double() -> MagicMock:
    harness = MagicMock()
    harness.agent_kwargs.return_value = {
        "middleware": ("middleware",),
        "observers": ("observer",),
        "hitl_hook": "hook",
    }
    return harness


def test_build_registers_composite_and_gateway_tools():
    """The factory passes harness kwargs and registers both tool sets."""
    module = _load_execution_agent_module()
    agent = MagicMock()
    added: list = []
    agent.add_tool.side_effect = added.append
    harness = _harness_double()
    created: dict = {}

    def _create(definition, **kwargs):
        created["definition"] = definition
        created["kwargs"] = kwargs
        return agent

    registry = AsyncMock(return_value=18)
    with (
        patch("core.base_agent.create_agent", side_effect=_create),
        patch("core.agent_harness.build_agent_harness", return_value=harness),
        patch("services.mcp_client.resolve_gateway_url", return_value="http://localhost:8888/mcp/"),
        patch("services.mcp_tool_registry.register_mcp_tools_on_agent", registry),
    ):
        built = module.build_execution_agent()

    assert built is agent
    assert created["definition"].name == "execution-agent"
    assert created["kwargs"]["middleware"] == ("middleware",)
    assert created["kwargs"]["observers"] == ("observer",)
    assert created["kwargs"]["hitl_hook"] == "hook"
    assert {tool.name for tool in added} == _COMPOSITE_TOOLS
    registry.assert_awaited_once()
    assert registry.await_args.args[0] is agent
    assert registry.await_args.args[1] == "http://localhost:8888/mcp/"
    assert sorted(registry.await_args.args[2]) == ["blazemeter", "jmeter"]


def test_build_succeeds_when_gateway_registers_nothing():
    """A zero gateway count still leaves the composite tools attached."""
    module = _load_execution_agent_module()
    agent = MagicMock()
    added: list = []
    agent.add_tool.side_effect = added.append
    registry = AsyncMock(return_value=0)
    with (
        patch("core.base_agent.create_agent", return_value=agent),
        patch("core.agent_harness.build_agent_harness", return_value=_harness_double()),
        patch("services.mcp_client.resolve_gateway_url", return_value="http://localhost:8888/mcp/"),
        patch("services.mcp_tool_registry.register_mcp_tools_on_agent", registry),
    ):
        built = module.build_execution_agent()

    assert built is agent
    assert {tool.name for tool in added} == _COMPOSITE_TOOLS
    registry.assert_awaited_once()


async def test_provision_unknown_project_is_a_resolver_error(tmp_path: Path) -> None:
    """A missing project is returned as an error, not an unbound exception."""
    from services.resolvers.project_catalog import ProjectNotFoundError

    module = _load_execution_agent_module()
    jmx = tmp_path / "sample.jmx"
    jmx.write_text("<jmeterTestPlan/>", encoding="utf-8")

    def _missing(_name: str) -> dict:
        raise ProjectNotFoundError("missing-project", ["demo"])

    with patch(
        "services.resolvers.project_catalog.resolve_load_testing_config",
        side_effect=_missing,
    ):
        result = await module.provision_performance_test(
            environment="QA",
            test_name="demo",
            jmx_path=str(jmx),
            project="missing-project",
        )

    assert result["ok"] is False
    assert result["error"]["phase"] == "env-resolver"
    assert "missing-project" in result["error"]["message"]
