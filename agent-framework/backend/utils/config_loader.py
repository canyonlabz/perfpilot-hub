"""Central YAML configuration loader for the agent framework.

Consolidates 13+ duplicated YAML loading sites across 9 files into three
public functions:

    load_agent_config(agent_name, ...)   -> per-agent config dict
    load_global_config(...)              -> config/agents.yaml dict
    get_agent_config_section(...)        -> convenience section accessor

All loads use ``utf-8-sig`` encoding for Windows BOM tolerance, cache
results per-process, and return ``{}`` on any error (missing file, parse
failure) with a WARNING-level log.

The ``framework_dir`` parameter on every function defaults to
``paths.get_framework_dir()`` (``agent-framework/backend/``), but can be
overridden by the caller if needed.

Heavy import (``yaml``) is deferred into the functions that need it so
this module can be imported in environments without PyYAML installed
(structural smoke tests, IDE indexing, etc.).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal caches
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_agent_config_cache: dict[str, dict] = {}
_global_config_cache: Optional[dict] = None
_global_config_framework_dir: Optional[Path] = None


def _default_framework_dir() -> Path:
    """Return the framework root (``agent-framework/backend/``)."""
    from .paths import get_framework_dir
    return get_framework_dir()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_agent_config(
    agent_name: str,
    *,
    framework_dir: Optional[Path] = None,
) -> dict:
    """Load and return the per-agent config dict.

    Resolution uses ``base_agent.resolve_agent_config_path()`` which walks
    ``config.yaml`` -> ``config.example.yaml`` in priority order. Always
    reads with ``utf-8-sig`` encoding. Returns ``{}`` on missing or
    invalid file. Cached per ``agent_name`` after first load (process
    lifetime).

    Args:
        agent_name: Agent folder name (e.g. ``"orchestrator"``,
            ``"execution-agent"``).
        framework_dir: Override for the framework root directory. When
            ``None``, auto-detected from this file's location.

    Returns:
        Parsed YAML dict, or ``{}`` if the file is missing / invalid.
    """
    with _cache_lock:
        if agent_name in _agent_config_cache:
            return _agent_config_cache[agent_name]

    import yaml

    from core.base_agent import resolve_agent_config_path

    if framework_dir is None:
        framework_dir = _default_framework_dir()

    agent_dir = framework_dir / "agents" / agent_name
    config_path = resolve_agent_config_path(agent_dir)

    if config_path is None:
        log.warning(
            "load_agent_config: no config file found for agent '%s' "
            "under %s (expected config.yaml or config.example.yaml)",
            agent_name,
            agent_dir,
        )
        result: dict = {}
        with _cache_lock:
            _agent_config_cache[agent_name] = result
        return result

    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            parsed = yaml.safe_load(f)
    except Exception:
        log.warning(
            "load_agent_config: failed to parse %s for agent '%s'",
            config_path,
            agent_name,
            exc_info=True,
        )
        result = {}
        with _cache_lock:
            _agent_config_cache[agent_name] = result
        return result

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        log.warning(
            "load_agent_config: config for agent '%s' at %s is not a "
            "YAML mapping (got %s); returning empty dict",
            agent_name,
            config_path.name,
            type(parsed).__name__,
        )
        parsed = {}
    else:
        log.info(
            "load_agent_config: loaded config for '%s' from %s",
            agent_name,
            config_path,
        )

    with _cache_lock:
        _agent_config_cache[agent_name] = parsed
    return parsed


def load_global_config(
    *,
    framework_dir: Optional[Path] = None,
) -> dict:
    """Load ``config/agents.yaml`` (or ``agents.example.yaml`` fallback).

    Always uses ``utf-8-sig`` encoding for Windows BOM tolerance.
    Returns ``{}`` on missing or invalid file. Cached after first load.

    Args:
        framework_dir: Override for the framework root directory. When
            ``None``, auto-detected from this file's location.

    Returns:
        Parsed YAML dict, or ``{}`` if neither file exists.
    """
    global _global_config_cache, _global_config_framework_dir

    if framework_dir is None:
        framework_dir = _default_framework_dir()

    with _cache_lock:
        if (
            _global_config_cache is not None
            and _global_config_framework_dir == framework_dir
        ):
            return _global_config_cache

    import yaml

    candidates = (
        framework_dir / "config" / "agents.yaml",
        framework_dir / "config" / "agents.example.yaml",
    )

    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8-sig") as f:
                    parsed = yaml.safe_load(f) or {}
            except Exception:
                log.warning(
                    "load_global_config: failed to parse %s",
                    candidate,
                    exc_info=True,
                )
                parsed = {}

            if not isinstance(parsed, dict):
                log.warning(
                    "load_global_config: %s is not a YAML mapping "
                    "(got %s); returning empty dict",
                    candidate,
                    type(parsed).__name__,
                )
                parsed = {}
            else:
                log.info("load_global_config: loaded from %s", candidate)

            with _cache_lock:
                _global_config_cache = parsed
                _global_config_framework_dir = framework_dir
            return parsed

    log.warning(
        "load_global_config: neither agents.yaml nor agents.example.yaml "
        "found under %s/config/",
        framework_dir,
    )
    result: dict = {}
    with _cache_lock:
        _global_config_cache = result
        _global_config_framework_dir = framework_dir
    return result


def get_agent_config_section(
    agent_name: str,
    section: str,
    *,
    default: Any = None,
    framework_dir: Optional[Path] = None,
) -> Any:
    """Convenience: ``load_agent_config(agent_name)[section]`` with default.

    Args:
        agent_name: Agent folder name.
        section: Top-level key to retrieve from the agent's config dict.
        default: Value returned when the section key is absent.
        framework_dir: Override for the framework root directory.

    Returns:
        The value of ``config[section]``, or ``default`` if missing.
    """
    config = load_agent_config(agent_name, framework_dir=framework_dir)
    return config.get(section, default)


# ---------------------------------------------------------------------------
# Cache management (for tests and force_reload support)
# ---------------------------------------------------------------------------


def clear_agent_cache(agent_name: Optional[str] = None) -> None:
    """Clear cached agent config(s).

    Args:
        agent_name: If provided, clear only that agent's cache entry.
            If ``None``, clear the entire agent config cache.
    """
    with _cache_lock:
        if agent_name is None:
            _agent_config_cache.clear()
        else:
            _agent_config_cache.pop(agent_name, None)


def clear_global_cache() -> None:
    """Clear the cached global config so the next call re-reads from disk."""
    global _global_config_cache, _global_config_framework_dir
    with _cache_lock:
        _global_config_cache = None
        _global_config_framework_dir = None
