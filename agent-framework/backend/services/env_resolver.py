"""Environment name resolver for the agent framework.

Maps an environment name received in A2A / Web UI metadata
(``QA``, ``UAT``, ``PERF``, etc.) to the concrete targets a performance
run needs:

  - Application hostname / base URL under test.
  - Certificate / test-user profile (referenced by name).
  - BlazeMeter workspace + project for test provisioning.

The source of truth is ``config/environments.yaml`` (with
``environments.example.yaml`` as the checked-in fallback, mirroring the
``agents.yaml`` / ``agents.example.yaml`` fallback pattern used by
``config_loader.py``).

Public API::

    load_environments_config(...)                    -> raw dict
    resolve_environment(name, ..., overrides=...)    -> ResolvedEnvironment
    get_cert_profile(profile_name, ...)              -> CertProfile
    clear_environments_cache()

Design notes
------------
This module is additive and self-contained. It intentionally does not
modify ``utils/config_loader.py``; the loader here follows the same
conventions (utf-8-sig, ``{}`` + WARNING on missing / invalid files,
thread-safe per-process cache, deferred ``yaml`` import) so behavior
matches operator expectations.

A2A callers may pass explicit overrides for the BlazeMeter target via
metadata. Overrides always win over the file. This lets the upstream
framework target a workspace / project that is not yet mapped in
``environments.yaml`` without redeploying the backend.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BlazeMeterTarget:
    """Resolved BlazeMeter provisioning target.

    Either the numeric ids or the name-based lookups may be populated
    (or both). Callers should prefer ids when present and fall back to
    a name-based lookup via the BlazeMeter MCP otherwise.

    Attributes:
        workspace_id: BlazeMeter workspace id (string form of the
            numeric id, matching the BlazeMeter API).
        project_id: BlazeMeter project id.
        workspace_name: Human-readable workspace name for lookup when
            ``workspace_id`` is not known.
        project_name: Human-readable project name for lookup when
            ``project_id`` is not known.
    """

    workspace_id: Optional[str] = None
    project_id: Optional[str] = None
    workspace_name: Optional[str] = None
    project_name: Optional[str] = None


@dataclass(frozen=True)
class CertProfile:
    """Certificate / test-user profile referenced by an environment.

    All paths are strings so downstream tools (JMeter, browser
    automation) can consume them without translating from ``Path``.
    Values may be ``None`` when a profile does not use that credential
    kind (e.g. email-only auth without a P12).
    """

    name: str
    jks_path: Optional[str] = None
    test_user_email: Optional[str] = None
    p12_path: Optional[str] = None


@dataclass(frozen=True)
class ResolvedEnvironment:
    """Fully resolved environment entry.

    Attributes:
        name: The environment name as it appears in ``environments.yaml``.
        hostname: Application hostname / base URL under test.
        blazemeter: Resolved BlazeMeter provisioning target.
        cert_profile: Resolved certificate / test-user profile, or
            ``None`` when the environment does not reference one.
        tags: Freeform tags copied from the file.
        metadata: Freeform metadata dict copied from the file. Not
            interpreted by this module.
    """

    name: str
    hostname: Optional[str]
    blazemeter: BlazeMeterTarget
    cert_profile: Optional[CertProfile]
    tags: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal cache
# ---------------------------------------------------------------------------


_cache_lock = threading.Lock()
_config_cache: Optional[dict] = None
_config_framework_dir: Optional[Path] = None


def _default_framework_dir() -> Path:
    """Return the framework root (``agent-framework/backend/``)."""
    from utils.paths import get_framework_dir
    return get_framework_dir()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_environments_config(
    *,
    framework_dir: Optional[Path] = None,
) -> dict:
    """Load ``config/environments.yaml`` (or ``environments.example.yaml``).

    Always uses ``utf-8-sig`` encoding for Windows BOM tolerance. Returns
    ``{}`` on missing or invalid file (with a WARNING log). Cached
    per-process after first successful load.

    Args:
        framework_dir: Override for the framework root directory. When
            ``None``, auto-detected via ``utils.paths.get_framework_dir()``.

    Returns:
        Parsed YAML dict, or ``{}`` if neither file exists / is valid.
    """
    global _config_cache, _config_framework_dir

    if framework_dir is None:
        framework_dir = _default_framework_dir()

    with _cache_lock:
        if (
            _config_cache is not None
            and _config_framework_dir == framework_dir
        ):
            return _config_cache

    import yaml

    candidates = (
        framework_dir / "config" / "environments.yaml",
        framework_dir / "config" / "environments.example.yaml",
    )

    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8-sig") as f:
                    parsed = yaml.safe_load(f) or {}
            except Exception:
                log.warning(
                    "load_environments_config: failed to parse %s",
                    candidate,
                    exc_info=True,
                )
                parsed = {}

            if not isinstance(parsed, dict):
                log.warning(
                    "load_environments_config: %s is not a YAML mapping "
                    "(got %s); returning empty dict",
                    candidate,
                    type(parsed).__name__,
                )
                parsed = {}
            else:
                log.info(
                    "load_environments_config: loaded from %s",
                    candidate,
                )

            with _cache_lock:
                _config_cache = parsed
                _config_framework_dir = framework_dir
            return parsed

    log.warning(
        "load_environments_config: neither environments.yaml nor "
        "environments.example.yaml found under %s/config/",
        framework_dir,
    )
    result: dict = {}
    with _cache_lock:
        _config_cache = result
        _config_framework_dir = framework_dir
    return result


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def _coerce_str_id(value: Any) -> Optional[str]:
    """Coerce a workspace/project id to a string, or return None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_blazemeter_target(
    raw: Optional[dict],
    overrides: Optional[dict] = None,
) -> BlazeMeterTarget:
    """Merge the file-side and override-side BlazeMeter blocks.

    Overrides win. Recognized keys (case-insensitive on the override
    side to accept both snake_case from YAML and camelCase from A2A):

      - ``workspace_id`` / ``workspaceId``
      - ``project_id`` / ``projectId``
      - ``workspace_name`` / ``workspaceName``
      - ``project_name`` / ``projectName``
    """
    raw = raw or {}
    overrides = overrides or {}

    def _pick(*keys: str) -> Optional[str]:
        for key in keys:
            if key in overrides and overrides[key] is not None:
                return _coerce_str_id(overrides[key])
        for key in keys:
            if key in raw and raw[key] is not None:
                return _coerce_str_id(raw[key])
        return None

    return BlazeMeterTarget(
        workspace_id=_pick("workspace_id", "workspaceId"),
        project_id=_pick("project_id", "projectId"),
        workspace_name=_pick("workspace_name", "workspaceName"),
        project_name=_pick("project_name", "projectName"),
    )


def _build_cert_profile(
    profile_name: Optional[str],
    profiles_section: dict,
) -> Optional[CertProfile]:
    """Return the resolved CertProfile or None when unknown."""
    if not profile_name:
        return None

    entry = profiles_section.get(profile_name)
    if not isinstance(entry, dict):
        log.warning(
            "env_resolver: cert_profile '%s' referenced but not defined "
            "in cert_profiles section",
            profile_name,
        )
        return CertProfile(name=profile_name)

    return CertProfile(
        name=profile_name,
        jks_path=entry.get("jks_path"),
        test_user_email=entry.get("test_user_email"),
        p12_path=entry.get("p12_path"),
    )


def resolve_environment(
    name: str,
    *,
    framework_dir: Optional[Path] = None,
    overrides: Optional[dict] = None,
) -> Optional[ResolvedEnvironment]:
    """Resolve an environment name to a ``ResolvedEnvironment``.

    Args:
        name: Environment name as it appears in ``environments.yaml``
            (e.g. ``"QA"``, ``"UAT"``, ``"PERF"``). Case-sensitive.
        framework_dir: Override for the framework root directory.
        overrides: Optional dict of BlazeMeter overrides from A2A
            metadata (``{"workspaceId": "...", "projectId": "..."}``
            or the snake_case equivalents). Overrides always win.

    Returns:
        ``ResolvedEnvironment`` when the name is found, else ``None``.
        A ``None`` return means the caller must either fail the request
        or fall back to fully-explicit metadata; the resolver never
        fabricates defaults for an unknown environment.
    """
    if not name or not isinstance(name, str):
        return None

    config = load_environments_config(framework_dir=framework_dir)
    envs = config.get("environments")
    if not isinstance(envs, dict):
        log.warning(
            "resolve_environment: 'environments' section missing or not "
            "a mapping; cannot resolve '%s'",
            name,
        )
        return None

    entry = envs.get(name)
    if not isinstance(entry, dict):
        log.info(
            "resolve_environment: environment '%s' not found in config",
            name,
        )
        return None

    profiles_section = config.get("cert_profiles") or {}
    if not isinstance(profiles_section, dict):
        profiles_section = {}

    tags_raw = entry.get("tags") or ()
    if isinstance(tags_raw, list):
        tags = tuple(str(t) for t in tags_raw if t is not None)
    else:
        tags = ()

    metadata = entry.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    return ResolvedEnvironment(
        name=name,
        hostname=entry.get("hostname"),
        blazemeter=_build_blazemeter_target(
            entry.get("blazemeter"),
            overrides=overrides,
        ),
        cert_profile=_build_cert_profile(
            entry.get("cert_profile"),
            profiles_section,
        ),
        tags=tags,
        metadata=metadata,
    )


def get_cert_profile(
    profile_name: str,
    *,
    framework_dir: Optional[Path] = None,
) -> Optional[CertProfile]:
    """Look up a cert profile by name from the ``cert_profiles`` section.

    Useful when a caller has the profile name in hand (e.g. from an
    already-resolved environment) but wants to re-resolve after a
    config change.

    Returns:
        ``CertProfile`` when the name is found, else ``None``.
    """
    if not profile_name:
        return None

    config = load_environments_config(framework_dir=framework_dir)
    profiles_section = config.get("cert_profiles") or {}
    if not isinstance(profiles_section, dict):
        return None
    if profile_name not in profiles_section:
        return None

    return _build_cert_profile(profile_name, profiles_section)


# ---------------------------------------------------------------------------
# Cache management (for tests and reload support)
# ---------------------------------------------------------------------------


def clear_environments_cache() -> None:
    """Clear the cached environments config so the next call re-reads from disk."""
    global _config_cache, _config_framework_dir
    with _cache_lock:
        _config_cache = None
        _config_framework_dir = None
