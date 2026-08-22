"""
Target resolver for MS Teams notification destinations.

Resolves named targets (from config.yaml) to conversation IDs,
or passes raw IDs through unchanged.  Supports both ``channels``
and ``chats`` blocks under ``notification_targets``.
"""

import logging
from typing import Any

from utils.config import load_config

logger = logging.getLogger("msteams-mcp.target-resolver")

_config = load_config()
_teams_cfg = _config.get("teams", {})
_targets_cfg = _teams_cfg.get("notification_targets", {})


def _get_channels() -> dict[str, dict[str, Any]]:
    return _targets_cfg.get("channels", {})


def _get_chats() -> dict[str, dict[str, Any]]:
    return _targets_cfg.get("chats", {})


def resolve_target(target: str) -> dict[str, Any]:
    """
    Resolve a target string to a conversation ID and metadata.

    Resolution logic:
        1. If target matches a named channel in config → use it.
        2. If target matches a named chat in config → use it.
        3. Otherwise, treat target as a raw conversation ID.

    Returns:
        Dict with keys: conversation_id, target_name, target_type,
        optional template, and optional mentions list.
    """
    channels = _get_channels()

    if target in channels:
        entry = channels[target]
        conversation_id = entry.get("conversation_id", "")
        if not conversation_id:
            logger.warning("Named channel '%s' has no conversation_id in config", target)
        return {
            "conversation_id": conversation_id,
            "target_name": target,
            "target_type": "channel",
            "template": entry.get("template"),
            "mentions": entry.get("mentions", []),
        }

    chats = _get_chats()

    if target in chats:
        entry = chats[target]
        conversation_id = entry.get("conversation_id", "")
        if not conversation_id:
            logger.warning("Named chat '%s' has no conversation_id in config", target)
        return {
            "conversation_id": conversation_id,
            "target_name": target,
            "target_type": "chat",
            "template": entry.get("template"),
            "mentions": entry.get("mentions", []),
        }

    return {
        "conversation_id": target,
        "target_name": None,
        "target_type": "raw",
        "template": None,
        "mentions": [],
    }


def list_configured_targets() -> dict[str, Any]:
    """
    List all configured notification targets from config.yaml.

    Returns channels and chats entries with their metadata.
    """
    channels = _get_channels()
    chats = _get_chats()

    def _format_entries(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for name, entry in entries.items():
            result.append({
                "name": name,
                "conversation_id": entry.get("conversation_id", ""),
                "description": entry.get("description", ""),
                "template": entry.get("template"),
                "mentions": entry.get("mentions", []),
            })
        return result

    return {
        "channels": _format_entries(channels),
        "channel_count": len(channels),
        "chats": _format_entries(chats),
        "chat_count": len(chats),
    }
