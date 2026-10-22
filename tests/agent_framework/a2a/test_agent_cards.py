"""Phase 5: A2A Agent Card shape (capabilities, provider.url, metadata)."""

from __future__ import annotations

import json
from pathlib import Path

from a2a.shared.models import AgentProvider
from core.base_agent import (
    card_display_name,
    card_status,
    card_tags,
    synthesize_stub_card,
    with_catalog_aliases,
)

AGENTS_DIR = Path(__file__).resolve().parents[3] / "agent-framework" / "backend" / "agents"
A2A_CAPABILITY_KEYS = {"streaming", "pushNotifications", "extendedAgentCard", "extensions"}
NON_A2A_ROOT_FIELDS = {"display_name", "status", "tags"}
NON_A2A_CAPABILITY_KEYS = {
    "long_running_tasks",
    "webhook_subscribers",
    "human_in_the_loop",
}


def _on_disk_cards() -> list[tuple[Path, dict]]:
    cards: list[tuple[Path, dict]] = []
    for path in sorted(AGENTS_DIR.glob("*/agent_card.json")):
        cards.append((path, json.loads(path.read_text(encoding="utf-8-sig"))))
    return cards


def test_every_agent_has_an_on_disk_card() -> None:
    assert _on_disk_cards(), "expected agent_card.json files under agents/"


def test_on_disk_cards_use_official_capability_keys_only() -> None:
    for path, card in _on_disk_cards():
        caps = card.get("capabilities") or {}
        extra = set(caps) - A2A_CAPABILITY_KEYS
        assert not extra, f"{path}: non-A2A capabilities {sorted(extra)}"
        assert not (set(caps) & NON_A2A_CAPABILITY_KEYS)


def test_on_disk_cards_move_nonstandard_fields_to_metadata() -> None:
    for path, card in _on_disk_cards():
        extras = NON_A2A_ROOT_FIELDS & set(card)
        assert not extras, f"{path}: non-A2A root fields {sorted(extras)}"
        meta = card.get("metadata") or {}
        assert isinstance(meta.get("displayName"), str) and meta["displayName"].strip(), path
        assert isinstance(meta.get("status"), str) and meta["status"].strip(), path


def test_on_disk_cards_include_provider_url() -> None:
    for path, card in _on_disk_cards():
        provider = card.get("provider")
        assert isinstance(provider, dict), path
        AgentProvider.model_validate(provider)
        assert provider.get("url") == "https://{host}:{port}", path


def test_synthesize_stub_card_is_a2a_shaped() -> None:
    stub = synthesize_stub_card("future-agent", framework_version="0.1.0")
    assert stub["name"] == "future-agent"
    assert stub["skills"] == []
    assert set(stub["capabilities"]) <= A2A_CAPABILITY_KEYS
    assert not (NON_A2A_ROOT_FIELDS & set(stub))
    assert stub["provider"]["url"] == "https://{host}:{port}"
    assert stub["metadata"]["status"] == "stub"
    assert stub["metadata"]["displayName"] == "PerfPilot future-agent"
    assert "supportedInterfaces" in stub
    assert stub["defaultInputModes"]
    assert stub["defaultOutputModes"]


def test_card_helpers_read_metadata() -> None:
    card = {
        "name": "script-agent",
        "metadata": {
            "displayName": "PerfPilot Script Agent",
            "status": "available",
            "tags": ["script", "jmeter"],
        },
    }
    assert card_display_name(card, fallback="script-agent") == "PerfPilot Script Agent"
    assert card_status(card) == "available"
    assert card_tags(card) == ["script", "jmeter"]


def test_card_display_name_falls_back_to_name() -> None:
    assert card_display_name({"name": "script-agent"}, fallback="x") == "script-agent"
    assert card_status({}) == "stub"
    assert card_tags({}) == []


def test_catalog_aliases_keep_web_ui_fields() -> None:
    card = {
        "name": "orchestrator",
        "metadata": {"displayName": "PerfPilot Orchestrator", "status": "available"},
    }
    view = with_catalog_aliases(card)
    assert view["display_name"] == "PerfPilot Orchestrator"
    assert view["status"] == "available"
    assert "display_name" not in card


def test_list_available_specialists_uses_metadata_display_name() -> None:
    from agents.orchestrator.agent import list_available_specialists

    specialists = json.loads(list_available_specialists())
    assert specialists
    by_name = {entry["name"]: entry for entry in specialists}
    assert by_name["script-agent"]["display_name"] == "PerfPilot Script Agent"
    assert by_name["notifications-agent"]["status"] == "in_development"
    assert "script" in by_name["script-agent"]["tags"]
