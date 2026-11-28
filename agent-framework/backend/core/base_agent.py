"""Factory for constructing native ``ag2.Agent`` instances from agent folders.

This module provides the framework-level convention for how an agent's
four-file pattern (`agent.py`, `agent_card.json`, `INSTRUCTIONS.md`,
plus *one of* `config.yaml` / `config.example.yaml`) is loaded from disk.
Each specialist agent's `agent.py` calls into `create_agent()` and supplies
agent-specific behavior (its MCP tools, middleware, observers) on top of the
shared factory.

Per-agent config files follow the same `<file>.yaml` / `<file>.example.yaml`
override-vs-template split that the MCP servers and the global
``backend/config/agents.yaml`` already use. The loader walks
`CANDIDATE_CONFIG_FILES` (`config.yaml` -> `config.example.yaml`) in
priority order so operator-side overrides win without ever being committed.
See `resolve_agent_config_path()` for the helper.

`create_agent()` builds a native ``ag2.Agent``: the agent's INSTRUCTIONS.md
text becomes its ``prompt``, the agent's ``llm_provider`` config block becomes
a typed ``ag2.config.ModelConfig`` via ``services.llm_provider`` (which
dispatches to the vendor-agnostic adapter layer, so OpenAI, Azure OpenAI,
Ollama, and Anthropic each resolve to their own typed config), and tools /
middleware / observers are injected by the caller. The agent owns an
``httpx2`` client through its config, so the factory retains the originating
``LLMProvider`` on the agent and ``aclose_agent()`` releases the connection
pool at shutdown.

Heavy imports (`ag2`) are deferred into the functions that need them so this
module can be imported in environments without AG2 installed (structural smoke
test, IDE indexing, etc.).
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# The four-file pattern (V2 §7.1). `config.yaml` is special: the loader
# accepts either the operator-side override (`config.yaml`, gitignored)
# or the committed default (`config.example.yaml`). At least one of the
# two must exist. The other three are required as-is.
REQUIRED_AGENT_FILES = ("agent.py", "agent_card.json", "INSTRUCTIONS.md")

# Per-agent config-file candidates, in priority order. The first one that
# exists on disk is used. Mirrors the same pattern that the MCP servers
# in this repo follow (`config.yaml` -> `config.example.yaml`) and that
# the global ``backend/config/agents.yaml`` / ``agents.example.yaml``
# pair uses in ``services/llm_provider.py::load_agents_yaml``.
CANDIDATE_CONFIG_FILES = ("config.yaml", "config.example.yaml")


def resolve_agent_config_path(agent_folder: Path) -> Path | None:
    """Return the first existing per-agent config file under `agent_folder`.

    Walks `CANDIDATE_CONFIG_FILES` in priority order so an operator-side
    `config.yaml` (gitignored) wins over the committed `config.example.yaml`
    fallback. Returns None when neither candidate exists.
    """
    for candidate in CANDIDATE_CONFIG_FILES:
        path = agent_folder / candidate
        if path.exists():
            return path
    return None


_PLACEHOLDER_URL = "https://{host}:{port}"


def card_display_name(card: dict, fallback: str = "") -> str:
    """Return ``metadata.displayName``, then ``name``, then ``fallback``."""
    meta = card.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    for value in (meta.get("displayName"), card.get("name"), fallback):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def card_status(card: dict, fallback: str = "stub") -> str:
    """Return ``metadata.status``, or ``fallback`` when unset."""
    meta = card.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    value = meta.get("status")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def card_tags(card: dict) -> list[str]:
    """Return ``metadata.tags`` as a list of strings."""
    meta = card.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
    tags = meta.get("tags")
    if not isinstance(tags, list):
        return []
    return [tag for tag in tags if isinstance(tag, str)]


def with_catalog_aliases(card: dict) -> dict:
    """Copy catalog fields the Web UI still reads from the card root.

    Official A2A cards keep ``displayName`` and ``status`` in
    ``metadata``. ``GET /agents/{name}/.well-known/agent.json`` adds
    ``display_name`` and ``status`` so the existing catalog panel
    keeps working without an AG-UI rewrite.
    """
    view = dict(card)
    view["display_name"] = card_display_name(card, fallback=str(card.get("name") or ""))
    view["status"] = card_status(card)
    return view


def synthesize_stub_card(agent_name: str, framework_version: str = "0.1.0") -> dict:
    """Return a truthful stub Agent Card for an agent with no on-disk card.

    Advertises empty ``skills[]`` and ``metadata.status: "stub"`` rather
    than promising capabilities the agent does not yet have. Used by
    discovery routes and ``list_available_specialists()``.
    """
    return {
        "name": agent_name,
        "description": (
            f"PerfPilot {agent_name} stub. Real capabilities ship later; "
            "this card is published so A2A clients can discover the agent."
        ),
        "url": f"/agents/{agent_name}",
        "version": framework_version,
        "supportedInterfaces": [
            {
                "url": _PLACEHOLDER_URL,
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            }
        ],
        "provider": {
            "organization": "PerfPilot",
            "url": _PLACEHOLDER_URL,
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [],
        "metadata": {
            "displayName": f"PerfPilot {agent_name}",
            "status": "stub",
            "tags": ["stub"],
        },
    }


def read_agent_card(
    agent_folder: Path,
    *,
    fallback_framework_version: str = "0.1.0",
) -> dict:
    """Return an agent's `agent_card.json`, synthesizing a stub when missing.

    The on-disk card (when present) is loaded with `utf-8-sig` so a
    Windows-emitted BOM is transparently stripped. When the card is
    absent, `synthesize_stub_card(agent_folder.name)` provides a truthful
    placeholder.
    """
    card_path = agent_folder / "agent_card.json"
    if card_path.exists():
        with open(card_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return synthesize_stub_card(agent_folder.name, framework_version=fallback_framework_version)


@dataclass
class AgentDefinition:
    """Materialized view of an agent's four-file pattern.

    Loaded from disk by `load_agent_definition()` and consumed by
    `create_agent()`. The fields capture the entire static description of
    an agent before its runtime LLM provider and MCP client are wired in.
    """

    name: str
    folder: Path
    config: dict
    instructions: str
    agent_card: dict
    enabled: bool = True
    extras: dict = field(default_factory=dict)


def load_agent_definition(agent_folder: Path) -> AgentDefinition:
    """Load an agent's four-file pattern from disk.

    Args:
        agent_folder: Path to `agents/<agent-name>/`.

    Returns:
        `AgentDefinition` with config, instructions, and agent card loaded.

    Raises:
        FileNotFoundError: If the folder or any required file is missing.
        ValueError: If `config.yaml` or `agent_card.json` are malformed.
    """
    if not agent_folder.is_dir():
        raise FileNotFoundError(f"Agent folder not found: {agent_folder}")

    name = agent_folder.name

    for required in REQUIRED_AGENT_FILES:
        candidate = agent_folder / required
        if not candidate.exists():
            raise FileNotFoundError(
                f"Required file missing for agent '{name}': {candidate}"
            )

    config_path = resolve_agent_config_path(agent_folder)
    if config_path is None:
        raise FileNotFoundError(
            f"Required config file missing for agent '{name}': expected one of "
            f"{CANDIDATE_CONFIG_FILES} under {agent_folder}"
        )

    instructions_path = agent_folder / "INSTRUCTIONS.md"
    agent_card_path = agent_folder / "agent_card.json"

    from utils import config_loader

    config = config_loader.load_agent_config(
        name, framework_dir=agent_folder.parent.parent,
    )

    with open(instructions_path, "r", encoding="utf-8-sig") as f:
        instructions = f.read()

    with open(agent_card_path, "r", encoding="utf-8-sig") as f:
        try:
            agent_card = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Agent '{name}' agent_card.json is not valid JSON") from exc

    enabled = bool(config.get("enabled", True))

    return AgentDefinition(
        name=name,
        folder=agent_folder,
        config=config,
        instructions=instructions,
        agent_card=agent_card,
        enabled=enabled,
    )


def discover_agents(agents_root: Path) -> list[AgentDefinition]:
    """Walk `agents/` and load every agent that has the full four-file pattern.

    Subfolders that do not yet have all four files (for example, freshly
    scaffolded folders containing only `.gitkeep`) are skipped silently and
    logged at DEBUG level. This lets the scaffolding coexist with the eventual
    real agents in F3.7+.

    Args:
        agents_root: Path to ``backend/agents/``.

    Returns:
        List of fully loaded `AgentDefinition` instances, sorted by name.
    """
    if not agents_root.is_dir():
        log.warning("agents/ folder not found at %s", agents_root)
        return []

    definitions: list[AgentDefinition] = []
    for child in sorted(agents_root.iterdir()):
        if not child.is_dir():
            continue
        all_required_present = all(
            (child / required).exists() for required in REQUIRED_AGENT_FILES
        )
        has_config = resolve_agent_config_path(child) is not None
        if not (all_required_present and has_config):
            log.debug("Skipping incomplete agent folder: %s", child)
            continue
        try:
            definitions.append(load_agent_definition(child))
        except Exception:
            log.exception("Failed to load agent definition from %s", child)
    return definitions


def create_agent(
    definition: AgentDefinition,
    *,
    tools: Iterable[Any] = (),
    middleware: Iterable[Any] = (),
    observers: Iterable[Any] = (),
    hitl_hook: Any = None,
    config: Any = None,
    prompt: Any = None,
) -> Any:
    """Construct a native ``ag2.Agent`` from a loaded ``AgentDefinition``.

    This is the canonical factory shared by every specialist agent. It maps the
    four-file definition onto the AG2 1.x construction surface:

    - ``definition.instructions`` (INSTRUCTIONS.md) becomes the agent ``prompt``.
    - the agent's ``llm_provider`` config block becomes a typed
      ``ag2.config.ModelConfig`` via ``services.llm_provider``, which dispatches
      to the per-vendor adapter layer (OpenAI / Azure OpenAI / Ollama / Anthropic
      each resolve to their own typed config).
    - ``tools`` / ``middleware`` / ``observers`` are injected by the caller
      (MCP tool wiring and HITL / auth / telemetry middleware land in later
      steps).

    The legacy ``human_input_mode="NEVER"`` has no native equivalent and is
    dropped: an agent with no ``hitl_hook`` simply never requests human input.
    The old ``max_consecutive_auto_reply`` cap is likewise expressed via
    middleware by the specialist when it needs it.

    When ``config`` is not supplied the factory builds (and retains) an
    ``LLMProvider`` so the agent owns the lifecycle of its ``httpx2`` client.
    Call :func:`aclose_agent` at shutdown to release the connection pool.

    Args:
        definition: ``AgentDefinition`` from ``load_agent_definition()``.
        tools: AG2 tools / callables to register on the agent at construction.
        middleware: AG2 ``MiddlewareFactory`` instances.
        observers: AG2 ``Observer`` instances.
        hitl_hook: Optional human-in-the-loop hook; ``None`` means no HITL.
        config: Optional pre-built ``ModelConfig``. When provided, the factory
            does not build or retain an ``LLMProvider`` (the caller owns it).
        prompt: Optional prompt override. Defaults to ``definition.instructions``.

    Returns:
        A native ``ag2.Agent`` instance, or a disabled-marker dict when the
        agent's config sets ``enabled: false``.
    """
    if not definition.enabled:
        log.info(
            "Agent '%s' is disabled in config; create_agent() returning disabled marker",
            definition.name,
        )
        return {
            "agent_name": definition.name,
            "status": "disabled",
            "reason": "agent config has enabled: false",
        }

    # Heavy import deferred so this module loads without AG2 installed.
    from ag2 import Agent

    retained_provider = None
    resolved_config = config
    if resolved_config is None:
        # Build and retain the provider so the agent owns its httpx2 client.
        from services.llm_provider import build_llm_provider

        retained_provider = build_llm_provider(definition.config.get("llm_provider"))
        resolved_config = retained_provider.to_ag2_config()

    resolved_prompt = definition.instructions if prompt is None else prompt

    agent = Agent(
        definition.name,
        resolved_prompt,
        config=resolved_config,
        hitl_hook=hitl_hook,
        tools=tuple(tools),
        middleware=tuple(middleware),
        observers=tuple(observers),
    )

    # Attach the provider (if we built it) so aclose_agent() can release the
    # connection pool at shutdown. ag2.Agent allows attribute assignment at
    # runtime; Pyright can't see this dynamic attribute on the typed class.
    agent._perfpilot_llm_provider = retained_provider  # type: ignore[attr-defined]

    log.info("create_agent('%s') constructed native ag2.Agent", definition.name)
    return agent


async def aclose_agent(agent: Any) -> None:
    """Release resources owned by an agent built by :func:`create_agent`.

    Closes the ``LLMProvider`` the factory attached to the agent (if any),
    which releases its ``httpx2`` connection pool. Safe to call on any object:
    agents created with a caller-owned ``config`` (or the disabled-marker dict)
    carry no retained provider and are left untouched.
    """
    provider = getattr(agent, "_perfpilot_llm_provider", None)
    if provider is not None:
        await provider.close()
