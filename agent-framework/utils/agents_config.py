"""Loader for `config/agents.yaml` (or its committed `.example` fallback).

Provides small helpers used by the A2A server, the AG-UI bridge, and
the orchestrator:

    load_agents_config()             -> the full parsed dict
    is_agent_enabled()               -> per-agent on/off bool
    list_enabled_agents()            -> list of agent names that are enabled
    get_session_cookie_config()      -> AG-UI session-cookie tunables
    get_context_indicator_enabled()  -> context-usage bar on/off bool

The first call caches the parsed YAML in-process. Hot-reload during dev is
left for future Features; for Epic 3, restarting the server picks up edits.

Heavy import (`yaml`) is deferred into `load_agents_config()` so this module
can be imported in environments without PyYAML installed.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

KNOWN_AGENTS = (
    "orchestrator",
    "execution-agent",
    "script-agent",
    "monitoring-agent",
    "analysis-agent",
    "reporting-agent",
    "notifications-agent",
)

# --- Defaults for `web_ui.session_cookie` (used when the YAML omits a key) --
# Kept here so the entire block can be missing without breaking startup.
# Match the docstring of `utils.user_identity.set_user_id_cookie`.
_SESSION_COOKIE_DEFAULTS = {
    "max_age_days": 365,
    "secure": False,
    "samesite": "lax",
}

_VALID_SAMESITE = ("lax", "strict", "none")


@dataclass(frozen=True)
class SessionCookieConfig:
    """Resolved AG-UI `perfpilot_token` cookie settings.

    All fields are guaranteed to be non-None and within validated ranges
    even if the YAML omits the entire `web_ui.session_cookie` block.
    """

    max_age_days: int
    secure: bool
    samesite: str

_cache_lock = threading.Lock()
_cached: Optional[dict] = None
_cached_for: Optional[Path] = None


def load_agents_config(framework_dir: Optional[Path] = None, *, force_reload: bool = False) -> dict:
    """Return the parsed `agents.yaml` (or `agents.example.yaml`) as a dict.

    Mirrors `utils.llm_provider.load_agents_yaml` but caches the result so
    repeated lookups are O(1). The cache key is the resolved framework dir;
    set `force_reload=True` to re-read from disk (useful in tests).

    Returns:
        Parsed YAML dict. Empty dict (with warning) if neither file exists.
    """
    global _cached, _cached_for
    import yaml  # deferred so module imports without PyYAML installed

    if framework_dir is None:
        framework_dir = Path(__file__).resolve().parent.parent

    with _cache_lock:
        if not force_reload and _cached is not None and _cached_for == framework_dir:
            return _cached

        candidates = (
            framework_dir / "config" / "agents.yaml",
            framework_dir / "config" / "agents.example.yaml",
        )
        for candidate in candidates:
            if candidate.exists():
                with open(candidate, "r", encoding="utf-8") as f:
                    _cached = yaml.safe_load(f) or {}
                _cached_for = framework_dir
                log.info("Loaded agents config from %s", candidate)
                return _cached

        log.warning(
            "Neither agents.yaml nor agents.example.yaml found under %s/config/. "
            "All agents will be treated as disabled.",
            framework_dir,
        )
        _cached = {}
        _cached_for = framework_dir
        return _cached


def is_agent_enabled(agent_name: str, framework_dir: Optional[Path] = None) -> bool:
    """Return True if `agent_name` is enabled in `agents.yaml`.

    Unknown agents (not in `KNOWN_AGENTS`) are always disabled. An agent
    listed in the config without an explicit `enabled` key defaults to True.
    """
    if agent_name not in KNOWN_AGENTS:
        return False

    config = load_agents_config(framework_dir)
    agents_block = config.get("agents") or {}
    entry = agents_block.get(agent_name)
    if entry is None:
        return False
    if isinstance(entry, dict):
        return bool(entry.get("enabled", True))
    return bool(entry)


def list_enabled_agents(framework_dir: Optional[Path] = None) -> list[str]:
    """Return the list of agent names that are currently enabled."""
    return [name for name in KNOWN_AGENTS if is_agent_enabled(name, framework_dir)]


def get_session_cookie_config(framework_dir: Optional[Path] = None) -> SessionCookieConfig:
    """Resolve the AG-UI `perfpilot_token` cookie settings from `agents.yaml`.

    Reads `web_ui.session_cookie.{max_age_days,secure,samesite}`. Missing
    keys fall back to `_SESSION_COOKIE_DEFAULTS`. Invalid values
    (non-positive `max_age_days`, unknown `samesite`) trigger a warning
    and the default for that key is used.

    `samesite=none` requires `secure=true` per the browser cookie spec;
    when the YAML mixes them, this helper logs a warning but does NOT
    auto-correct -- the operator should fix the config explicitly so the
    intent is unambiguous in version control.

    Returns:
        Always a populated `SessionCookieConfig`. Never raises.
    """
    raw = (load_agents_config(framework_dir).get("web_ui") or {}).get("session_cookie") or {}

    max_age_days = raw.get("max_age_days", _SESSION_COOKIE_DEFAULTS["max_age_days"])
    try:
        max_age_days = int(max_age_days)
    except (TypeError, ValueError):
        log.warning(
            "agents.yaml web_ui.session_cookie.max_age_days=%r not an int; using default %d",
            max_age_days, _SESSION_COOKIE_DEFAULTS["max_age_days"],
        )
        max_age_days = _SESSION_COOKIE_DEFAULTS["max_age_days"]
    if max_age_days <= 0:
        log.warning(
            "agents.yaml web_ui.session_cookie.max_age_days=%d is not positive; using default %d",
            max_age_days, _SESSION_COOKIE_DEFAULTS["max_age_days"],
        )
        max_age_days = _SESSION_COOKIE_DEFAULTS["max_age_days"]

    secure = bool(raw.get("secure", _SESSION_COOKIE_DEFAULTS["secure"]))

    samesite = str(raw.get("samesite", _SESSION_COOKIE_DEFAULTS["samesite"])).strip().lower()
    if samesite not in _VALID_SAMESITE:
        log.warning(
            "agents.yaml web_ui.session_cookie.samesite=%r not in %s; using default %r",
            samesite, _VALID_SAMESITE, _SESSION_COOKIE_DEFAULTS["samesite"],
        )
        samesite = _SESSION_COOKIE_DEFAULTS["samesite"]

    if samesite == "none" and not secure:
        # Browser will silently reject; warn so the operator knows why
        # nothing is being persisted.
        log.warning(
            "agents.yaml web_ui.session_cookie: samesite=none requires secure=true; "
            "browsers will reject the perfpilot_token cookie until secure is enabled."
        )

    return SessionCookieConfig(
        max_age_days=max_age_days,
        secure=secure,
        samesite=samesite,
    )


def get_context_indicator_enabled(framework_dir: Optional[Path] = None) -> bool:
    """Return whether the task-context usage indicator is enabled.

    Reads ``web_ui.context_indicator.enabled`` from ``agents.yaml``.
    Defaults to ``True`` when the key is missing or the entire
    ``context_indicator`` block is absent — the indicator is opt-out.
    """
    raw = (
        (load_agents_config(framework_dir).get("web_ui") or {})
        .get("context_indicator") or {}
    )
    return bool(raw.get("enabled", True))
