"""Factory for constructing AG2 ConversableAgent instances from agent folders.

This module provides the framework-level convention for how an agent's
four-file pattern (`agent.py`, `agent_card.json`, `INSTRUCTIONS.md`,
plus *one of* `config.yaml` / `config.example.yaml`) is loaded from disk.
Each specialist agent's `agent.py` calls into `create_agent()` and supplies
agent-specific behavior on top of the shared factory.

Per-agent config files follow the same `<file>.yaml` / `<file>.example.yaml`
override-vs-template split that the MCP servers and the global
``backend/config/agents.yaml`` already use. The loader walks
`CANDIDATE_CONFIG_FILES` (`config.yaml` -> `config.example.yaml`) in
priority order so operator-side overrides win without ever being committed.
See `resolve_agent_config_path()` for the helper.

Status:
    F3.2 (this commit) - public API skeleton, four-file loading, name
        validation. The actual `ConversableAgent` construction is a placeholder.
    F3.4 - LLM provider wiring once `utils/llm_provider.py` lands.
    F3.7 - real `ConversableAgent` creation, starting with the orchestrator.
    F3.13 - auth middleware and OpenTelemetry span hooks lit up.

Heavy imports (`ag2` / `autogen`) are deferred into the function that needs
them so this module can be imported in environments without AG2 installed
(structural smoke test, IDE indexing, etc.).
"""

import json
import logging
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
# pair uses in ``utils/llm_provider.py::load_agents_yaml``.
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


def synthesize_stub_card(agent_name: str, framework_version: str = "0.1.0") -> dict:
    """Return a truthful stub `agent_card.json` for an agent with no on-disk card.

    V2 doc §7.4 requires stub cards to advertise empty `skills[]` and a
    clear `status: "stub"` rather than promising capabilities the agent
    does not yet have. Used by:

    - `a2a_server._register_routes` when serving `/.well-known/agent.json`
      for an enabled agent whose folder has not yet been scaffolded with
      a real `agent_card.json` (most specialists today).
    - `agents/orchestrator/agent.py::list_available_specialists()` when
      enumerating the catalog for the orchestrator's discovery tool.
    """
    return {
        "name": agent_name,
        "description": (
            f"PerfPilot {agent_name} (Epic 3 stub). Real capabilities ship in a later "
            "Feature; this card is published only so external A2A clients can discover "
            "the agent today."
        ),
        "url": f"/agents/{agent_name}",
        "version": framework_version,
        "status": "stub",
        "skills": [],
        "tags": ["stub", "epic-3"],
        "capabilities": {
            "streaming": True,
            "long_running_tasks": True,
            "webhook_subscribers": True,
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

    from . import config_loader

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


def create_agent(definition: AgentDefinition, **overrides: Any) -> Any:
    """Construct an AG2 `ConversableAgent` from a loaded `AgentDefinition`.

    F3.2 status: this is a structural skeleton. Until F3.7 lands the real
    ConversableAgent factory (with LLM provider wiring from F3.4 and MCP
    namespace filtering via `mcp_client.py`), this returns a placeholder
    dictionary describing what *would* be created. Callers in F3.2 are not
    expected to interact with the return value.

    Args:
        definition: `AgentDefinition` from `load_agent_definition()`.
        **overrides: Optional keyword overrides applied on top of
            `definition.config`. Useful for tests and orchestrator-driven
            instantiation.

    Returns:
        AG2 `ConversableAgent` instance (in F3.7+); a placeholder dict in F3.2.
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

    log.warning(
        "create_agent('%s') is a F3.2 skeleton; real ConversableAgent wiring lights up in F3.7",
        definition.name,
    )
    return {
        "agent_name": definition.name,
        "status": "skeleton",
        "config": dict(definition.config),
        "overrides": dict(overrides),
        "instructions_preview": definition.instructions[:200],
    }
