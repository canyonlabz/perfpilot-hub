"""Tests for the native-AG2 port of ``services.mcp_tool_registry``.

These cover the C-3a increment: ``register_mcp_tools_on_agent`` builds native
``ag2.tools.FunctionTool`` objects (via the ``ag2.tools.tool`` decorator,
carrying the MCP ``inputSchema`` verbatim) and attaches them with
``agent.add_tool``.

The real ``ag2.tools.tool`` decorator runs (so we assert against the actual
``FunctionTool`` it produces), but the gateway catalog fetch and the per-call
wrapper factories are stubbed, so no network or live gateway is touched.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services import mcp_tool_registry as reg


class _StubAgent:
    """Minimal stand-in for a native ``ag2.Agent``: name + add_tool capture."""

    def __init__(self, name: str = "stub-agent") -> None:
        self.name = name
        self.tools: list = []

    def add_tool(self, function_tool) -> None:
        self.tools.append(function_tool)


def _catalog_tool(name: str, schema: dict | None):
    """Build a fake MCP catalog entry (name / description / inputSchema)."""
    return SimpleNamespace(name=name, description=f"{name} description", inputSchema=schema)


@pytest.fixture
def patch_catalog(monkeypatch):
    """Return a setter that stubs ``_fetch_tool_catalog`` with a fixed list."""

    def _set(catalog: list):
        async def _fake_fetch(_gateway_url: str):
            return catalog

        monkeypatch.setattr(reg, "_fetch_tool_catalog", _fake_fetch)

    return _set


def _params_of(function_tool):
    """Pull the advertised JSON-Schema parameters out of a native FunctionTool."""
    return function_tool.schema.function.parameters


# =============================================================================
# Namespace filtering + verbatim schema
# =============================================================================


async def test_registers_only_allowed_namespaces_with_verbatim_schema(patch_catalog):
    datadog_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    }
    patch_catalog([
        _catalog_tool("datadog_get_logs", datadog_schema),
        _catalog_tool("blazemeter_start_test", {"type": "object", "properties": {}}),
        _catalog_tool("jmeter_run", {"type": "object", "properties": {}}),
    ])
    agent = _StubAgent()

    count = await reg.register_mcp_tools_on_agent(
        agent, "http://gw/mcp/", ["datadog"],
    )

    assert count == 1
    assert len(agent.tools) == 1
    (ft,) = agent.tools
    assert ft.name == "datadog_get_logs"
    # The MCP inputSchema is carried into the native tool verbatim.
    assert _params_of(ft) == datadog_schema


async def test_missing_input_schema_uses_empty_object_guard(patch_catalog):
    # A tool that reports no inputSchema must advertise a clean no-arg schema,
    # never the spurious {"kwargs": ...} that schema=None would introspect.
    patch_catalog([_catalog_tool("jmeter_validate", None)])
    agent = _StubAgent()

    count = await reg.register_mcp_tools_on_agent(agent, "http://gw/mcp/", ["jmeter"])

    assert count == 1
    (ft,) = agent.tools
    assert _params_of(ft) == {"type": "object", "properties": {}}


# =============================================================================
# Per-tool allow-list (allowed_tools) — additive with namespace filter
# =============================================================================


async def test_allowed_tools_admits_tool_outside_allowed_namespaces(patch_catalog):
    # Agent authorized for perfreport_* can also be granted the single
    # datadog_load_environment tool without inheriting all of datadog_*.
    patch_catalog([
        _catalog_tool("perfreport_build", {"type": "object", "properties": {}}),
        _catalog_tool("datadog_load_environment", {"type": "object", "properties": {}}),
        _catalog_tool("datadog_get_logs", {"type": "object", "properties": {}}),
    ])
    agent = _StubAgent()

    count = await reg.register_mcp_tools_on_agent(
        agent,
        "http://gw/mcp/",
        ["perfreport"],
        allowed_tools=["datadog_load_environment"],
    )

    assert count == 2
    names = sorted(ft.name for ft in agent.tools)
    # datadog_get_logs stays excluded — only the explicitly-listed datadog tool
    # joins the perfreport namespace.
    assert names == ["datadog_load_environment", "perfreport_build"]


async def test_allowed_tools_none_preserves_namespace_only_behavior(patch_catalog):
    patch_catalog([
        _catalog_tool("perfreport_build", {"type": "object", "properties": {}}),
        _catalog_tool("datadog_load_environment", {"type": "object", "properties": {}}),
    ])
    agent = _StubAgent()

    count = await reg.register_mcp_tools_on_agent(
        agent, "http://gw/mcp/", ["perfreport"],
    )

    assert count == 1
    assert [ft.name for ft in agent.tools] == ["perfreport_build"]


# =============================================================================
# Wrapper-factory selection (API retry / code single-shot / stateful)
# =============================================================================


@pytest.fixture
def spy_wrappers(monkeypatch):
    """Record which wrapper factory each tool is routed through."""
    calls: dict[str, list] = {"api": [], "code": [], "stateful": []}

    def _make(kind: str):
        def _factory(*args, **kwargs):
            calls[kind].append((args, kwargs))

            async def _wrapper(**_kw):
                return "ok"

            return _wrapper

        return _factory

    monkeypatch.setattr(reg, "_make_api_tool_wrapper", _make("api"))
    monkeypatch.setattr(reg, "_make_code_tool_wrapper", _make("code"))
    monkeypatch.setattr(reg, "_make_stateful_tool_wrapper", _make("stateful"))
    return calls


async def test_wrapper_selection_api_code_and_stateful(patch_catalog, spy_wrappers):
    patch_catalog([
        _catalog_tool("datadog_get_logs", {"type": "object", "properties": {}}),
        _catalog_tool("jmeter_run", {"type": "object", "properties": {}}),
        _catalog_tool("browser_navigate", {"type": "object", "properties": {}}),
    ])
    agent = _StubAgent()
    holder = {"client": object()}

    count = await reg.register_mcp_tools_on_agent(
        agent,
        "http://gw/mcp/",
        ["datadog", "jmeter", "browser"],
        stateful_client_holder=holder,
    )

    assert count == 3
    # datadog -> API (retry-eligible); jmeter -> code (single attempt);
    # browser -> stateful (shared client holder).
    assert [c[0][0] for c in spy_wrappers["api"]] == ["datadog_get_logs"]
    assert [c[0][0] for c in spy_wrappers["code"]] == ["jmeter_run"]
    assert [c[0][0] for c in spy_wrappers["stateful"]] == ["browser_navigate"]
    # The stateful factory receives the shared holder, not the gateway URL.
    assert spy_wrappers["stateful"][0][0][1] is holder


async def test_stateful_without_holder_falls_back_to_code(patch_catalog, spy_wrappers):
    # Without a client holder, a browser_* tool must use the per-call (code)
    # wrapper rather than the stateful one.
    patch_catalog([_catalog_tool("browser_navigate", {"type": "object", "properties": {}})])
    agent = _StubAgent()

    await reg.register_mcp_tools_on_agent(agent, "http://gw/mcp/", ["browser"])

    assert spy_wrappers["stateful"] == []
    assert [c[0][0] for c in spy_wrappers["code"]] == ["browser_navigate"]


# =============================================================================
# Graceful degradation
# =============================================================================


async def test_gateway_unreachable_returns_zero(monkeypatch):
    async def _boom(_url: str):
        raise ConnectionError("gateway down")

    monkeypatch.setattr(reg, "_fetch_tool_catalog", _boom)
    agent = _StubAgent()

    count = await reg.register_mcp_tools_on_agent(agent, "http://gw/mcp/", ["datadog"])

    assert count == 0
    assert agent.tools == []


# =============================================================================
# NEVER-raise wrapper contract (unchanged by the port, re-pinned here)
# =============================================================================


async def test_code_wrapper_never_raises_returns_error_envelope(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(reg, "_call_gateway_tool", _boom)
    wrapper = reg._make_code_tool_wrapper("jmeter_run", "http://gw/mcp/")

    out = await wrapper(foo="bar")

    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "RuntimeError"
