"""Tests for the native ``ag2.Agent`` factory in ``core.base_agent``.

``create_agent()`` maps a loaded four-file ``AgentDefinition`` onto the AG2
1.x construction surface and (when no config is supplied) builds + retains an
``LLMProvider`` so the agent owns its ``httpx2`` connection pool.
``aclose_agent()`` releases that pool at shutdown.

These are pure unit tests: ``ag2.Agent`` is monkeypatched with a recorder and
``services.llm_provider.build_llm_provider`` is stubbed, so no real AG2 agent
is constructed and no network or LLM provider is contacted. The factory imports
both symbols lazily inside the function, so patching the source modules
(``ag2`` and ``services.llm_provider``) is sufficient.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.base_agent import AgentDefinition, aclose_agent, create_agent


def _definition(
    *,
    name: str = "demo-agent",
    enabled: bool = True,
    llm_provider: dict | None = None,
    instructions: str = "You are a terse test agent.",
) -> AgentDefinition:
    """Build an ``AgentDefinition`` in memory (no disk access)."""
    config: dict = {"enabled": enabled}
    if llm_provider is not None:
        config["llm_provider"] = llm_provider
    return AgentDefinition(
        name=name,
        folder=Path("/nonexistent") / name,
        config=config,
        instructions=instructions,
        agent_card={"name": name},
        enabled=enabled,
    )


@pytest.fixture
def recorder_agent(monkeypatch):
    """Patch ``ag2.Agent`` with a recorder capturing construction kwargs.

    Returns the list that receives one ``SimpleNamespace`` per instantiation,
    each exposing the positional ``name`` / ``prompt`` and the keyword args the
    factory passed, plus allowing attribute assignment (so the factory can set
    ``_perfpilot_llm_provider``).
    """
    import ag2

    instances: list = []

    class _RecorderAgent:
        def __init__(self, name, prompt, *, config=None, hitl_hook=None,
                     tools=(), middleware=(), observers=()):
            self.name = name
            self.prompt = prompt
            self.config = config
            self.hitl_hook = hitl_hook
            self.tools = tools
            self.middleware = middleware
            self.observers = observers
            instances.append(self)

    monkeypatch.setattr(ag2, "Agent", _RecorderAgent, raising=True)
    return instances


@pytest.fixture
def stub_provider(monkeypatch):
    """Patch ``build_llm_provider`` to return a close-tracking stub provider.

    Returns a ``SimpleNamespace`` with:
      - ``provider``: the stub with ``to_ag2_config()`` -> sentinel config and
        an awaitable ``close()`` that records invocation.
      - ``sentinel``: the object ``to_ag2_config()`` returns.
      - ``blocks``: list of the ``agent_llm_block`` args ``build_llm_provider``
        was called with (to assert the factory forwards the YAML block).
      - ``closed``: single-element list toggled True when ``close()`` is awaited.
    """
    import services.llm_provider as llm

    sentinel = object()
    closed = [False]
    blocks: list = []

    async def _close():
        closed[0] = True

    provider = SimpleNamespace(
        to_ag2_config=lambda: sentinel,
        close=_close,
    )

    def _build(agent_llm_block=None):
        blocks.append(agent_llm_block)
        return provider

    monkeypatch.setattr(llm, "build_llm_provider", _build, raising=True)
    return SimpleNamespace(provider=provider, sentinel=sentinel, blocks=blocks, closed=closed)


# =============================================================================
# create_agent — happy path (factory builds the provider)
# =============================================================================


def test_create_agent_builds_native_agent_with_provider(recorder_agent, stub_provider):
    definition = _definition(llm_provider={"provider": "anthropic"})

    agent = create_agent(definition)

    assert len(recorder_agent) == 1
    assert agent is recorder_agent[0]
    # INSTRUCTIONS.md text becomes the prompt; name is positional.
    assert agent.name == "demo-agent"
    assert agent.prompt == "You are a terse test agent."
    # The provider's typed config is passed through as `config`.
    assert agent.config is stub_provider.sentinel
    # The originating provider is retained for aclose_agent().
    assert agent._perfpilot_llm_provider is stub_provider.provider
    # The per-agent YAML llm_provider block is forwarded to build_llm_provider.
    assert stub_provider.blocks == [{"provider": "anthropic"}]


def test_create_agent_forwards_tools_middleware_observers_and_hitl(recorder_agent, stub_provider):
    tools = [object(), object()]
    middleware = [object()]
    observers = [object()]
    hook = object()

    agent = create_agent(
        _definition(),
        tools=tools,
        middleware=middleware,
        observers=observers,
        hitl_hook=hook,
    )

    # Injected collections are passed as tuples (copied, not the live lists).
    assert agent.tools == tuple(tools)
    assert agent.middleware == tuple(middleware)
    assert agent.observers == tuple(observers)
    assert agent.hitl_hook is hook


def test_create_agent_passes_none_llm_block_when_absent(recorder_agent, stub_provider):
    # No `llm_provider` key -> factory forwards None (global defaults path).
    create_agent(_definition(llm_provider=None))
    assert stub_provider.blocks == [None]


# =============================================================================
# create_agent — caller-owned config / prompt override
# =============================================================================


def test_create_agent_with_caller_config_skips_provider(recorder_agent, stub_provider):
    precooked = object()

    agent = create_agent(_definition(), config=precooked, prompt="custom prompt")

    # Caller owns the config: factory must not build/retain a provider.
    assert stub_provider.blocks == []
    assert agent.config is precooked
    assert agent.prompt == "custom prompt"
    assert agent._perfpilot_llm_provider is None


# =============================================================================
# create_agent — disabled agent
# =============================================================================


def test_create_agent_disabled_returns_marker_without_constructing(recorder_agent, stub_provider):
    marker = create_agent(_definition(enabled=False))

    assert marker == {
        "agent_name": "demo-agent",
        "status": "disabled",
        "reason": "agent config has enabled: false",
    }
    # Neither the AG2 agent nor the provider is built for a disabled agent.
    assert recorder_agent == []
    assert stub_provider.blocks == []


# =============================================================================
# aclose_agent
# =============================================================================


async def test_aclose_agent_closes_retained_provider(recorder_agent, stub_provider):
    agent = create_agent(_definition())
    await aclose_agent(agent)
    assert stub_provider.closed[0] is True


async def test_aclose_agent_noop_when_config_caller_owned(recorder_agent, stub_provider):
    agent = create_agent(_definition(), config=object())
    # No retained provider -> must not raise and must not touch the stub.
    await aclose_agent(agent)
    assert stub_provider.closed[0] is False


async def test_aclose_agent_noop_on_plain_object_and_marker():
    # Safe on an object without the attribute and on the disabled-marker dict.
    await aclose_agent(object())
    await aclose_agent({"status": "disabled"})
