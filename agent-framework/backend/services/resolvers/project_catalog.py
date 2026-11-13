"""Project catalog and capability resolver.

Loads ``config/environments.yaml`` (falling back to
``environments.example.yaml``) and resolves a project key or alias to
application URLs, a certificate profile, and the capability blocks
``load_testing``, ``project_management``, and ``documentation``.

A2A and AG-UI both call ``resolve_project_context``. The returned dict
omits blocks that are not configured. An unknown project raises
``ProjectNotFoundError``. An unknown environment is omitted from the
result so the other blocks can still be stamped.

Credentials are not read from this file. Blocks may name an environment
variable (for example ``jks_password_env``) but never a secret value.

This module does not read the Datadog MCP ``environments.json``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from utils.logging_config import log_event

log = logging.getLogger(__name__)

_LOGGER_NAME = "services.resolvers.project_catalog"

CONTEXT_KEYS: tuple[str, ...] = (
    "_resolved_environment",
    "_cert_profile",
    "_resolved_load_testing",
    "_resolved_project_management",
    "_resolved_documentation",
)

_DEFAULT_SOURCE = "project_catalog"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_projects_cache: Optional[dict] = None
_config_framework_dir: Optional[Path] = None
_alias_index: Optional[dict[str, str]] = None


# ---------------------------------------------------------------------------
# Exceptions and catalog row
# ---------------------------------------------------------------------------


class ProjectNotFoundError(KeyError):
    """Raised when a project key or alias cannot be resolved."""

    def __init__(self, identifier: str, available_projects: list[str]) -> None:
        self.identifier = identifier
        self.available_projects = available_projects
        super().__init__(
            f"Unknown project '{identifier}'. "
            f"Available projects: {available_projects}"
        )


class EnvironmentNotFoundError(KeyError):
    """Raised when a project is known but the environment is not."""

    def __init__(
        self,
        project: str,
        environment: str,
        available_environments: list[str],
    ) -> None:
        self.project = project
        self.environment = environment
        self.available_environments = available_environments
        super().__init__(
            f"Environment '{environment}' not found for project '{project}'. "
            f"Available environments: {available_environments}"
        )


@dataclass(frozen=True)
class ProjectCatalogEntry:
    """One project row for Settings bindings.

    ``project_key`` is the YAML key stored on ``user_project_bindings``.
    Display name and aliases are read-only identity from the file.
    """

    project_key: str
    name: str
    aliases: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_projects_config(
    *,
    framework_dir: Optional[Path] = None,
) -> dict:
    """Load and return the ``projects`` mapping.

    Thread-safe and cached for the process lifetime. Uses ``utf-8-sig``.
    Returns ``{}`` when both candidate files are missing or invalid.

    Args:
        framework_dir: Framework root (``agent-framework/backend``).
            Auto-detected when omitted.

    Returns:
        The ``projects`` dict, or ``{}``.
    """
    global _projects_cache, _config_framework_dir, _alias_index

    if framework_dir is None:
        from utils.paths import get_framework_dir
        framework_dir = get_framework_dir()

    with _cache_lock:
        if (
            _projects_cache is not None
            and _config_framework_dir == framework_dir
        ):
            return _projects_cache

    import yaml

    candidates = (
        framework_dir / "config" / "environments.yaml",
        framework_dir / "config" / "environments.example.yaml",
    )
    parsed: dict = {}
    loaded_from: Optional[Path] = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            with open(candidate, "r", encoding="utf-8-sig") as handle:
                raw = yaml.safe_load(handle) or {}
        except Exception:
            log.warning(
                "load_projects_config: failed to parse %s",
                candidate,
                exc_info=True,
            )
            continue
        if not isinstance(raw, dict):
            log.warning(
                "load_projects_config: %s is not a YAML mapping (got %s)",
                candidate,
                type(raw).__name__,
            )
            continue
        parsed = raw
        loaded_from = candidate
        break

    projects = parsed.get("projects") if isinstance(parsed, dict) else None
    if not isinstance(projects, dict):
        if loaded_from is not None:
            log.warning(
                "load_projects_config: 'projects' key missing or not a "
                "mapping in %s",
                loaded_from,
            )
        else:
            log.warning(
                "load_projects_config: neither environments.yaml nor "
                "environments.example.yaml found under %s/config/",
                framework_dir,
            )
        projects = {}

    alias_idx: dict[str, str] = {}
    for proj_key, proj_data in projects.items():
        if not isinstance(proj_data, dict):
            continue
        aliases = proj_data.get("aliases")
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            if not isinstance(alias, str) or not alias.strip():
                continue
            lower = alias.strip().lower()
            if lower in alias_idx:
                log.warning(
                    "load_projects_config: duplicate alias '%s' "
                    "(already mapped to '%s', ignoring for '%s')",
                    alias,
                    alias_idx[lower],
                    proj_key,
                )
                continue
            alias_idx[lower] = str(proj_key)

    if loaded_from is not None:
        log.info(
            "load_projects_config: loaded %d project(s) with %d alias(es) "
            "from %s",
            len(projects),
            len(alias_idx),
            loaded_from,
        )

    with _cache_lock:
        _projects_cache = projects
        _config_framework_dir = framework_dir
        _alias_index = alias_idx
    return projects


def clear_cache() -> None:
    """Clear cached projects and the alias index."""
    global _projects_cache, _config_framework_dir, _alias_index
    with _cache_lock:
        _projects_cache = None
        _config_framework_dir = None
        _alias_index = None


# ---------------------------------------------------------------------------
# Project and environment lookup
# ---------------------------------------------------------------------------


def resolve_project(
    identifier: str,
    *,
    framework_dir: Optional[Path] = None,
) -> tuple[str, dict]:
    """Resolve a project by key or alias (case-insensitive).

    Returns:
        ``(project_key, project_dict)``.

    Raises:
        ProjectNotFoundError: When neither the key nor any alias matches.
    """
    if not identifier or not isinstance(identifier, str) or not identifier.strip():
        raise ProjectNotFoundError(identifier or "", [])

    projects = load_projects_config(framework_dir=framework_dir)
    lower = identifier.strip().lower()

    for proj_key, proj_data in projects.items():
        if str(proj_key).lower() == lower and isinstance(proj_data, dict):
            return str(proj_key), proj_data

    with _cache_lock:
        alias_idx = dict(_alias_index or {})

    matched_key = alias_idx.get(lower)
    if matched_key is not None and matched_key in projects:
        proj_data = projects[matched_key]
        if isinstance(proj_data, dict):
            return matched_key, proj_data

    raise ProjectNotFoundError(
        identifier=identifier,
        available_projects=[str(key) for key in projects.keys()],
    )


def resolve_environment_url(
    project: str,
    environment: str,
    *,
    framework_dir: Optional[Path] = None,
) -> str:
    """Resolve a project plus environment name to an application URL.

    Raises:
        ProjectNotFoundError: When the project cannot be resolved.
        EnvironmentNotFoundError: When the environment is not configured
            or has no ``url`` string.
    """
    proj_key, proj_data = resolve_project(project, framework_dir=framework_dir)
    environments = proj_data.get("environments")
    if not isinstance(environments, dict) or not environments:
        raise EnvironmentNotFoundError(proj_key, environment, [])

    env_lower = environment.strip().lower()
    for env_name, env_data in environments.items():
        if str(env_name).lower() != env_lower:
            continue
        if isinstance(env_data, dict) and isinstance(env_data.get("url"), str):
            return env_data["url"]
        raise EnvironmentNotFoundError(
            proj_key, environment, list(environments.keys()),
        )

    raise EnvironmentNotFoundError(
        proj_key, environment, list(environments.keys()),
    )


def list_projects(
    *,
    framework_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Return summary dicts (``key``, ``name``, ``aliases``, ``environment_count``)."""
    projects = load_projects_config(framework_dir=framework_dir)
    result: list[dict[str, Any]] = []
    for proj_key in sorted(projects.keys(), key=str):
        proj_data = projects[proj_key]
        if not isinstance(proj_data, dict):
            continue
        envs = proj_data.get("environments")
        env_count = len(envs) if isinstance(envs, dict) else 0
        aliases = proj_data.get("aliases")
        result.append({
            "key": str(proj_key),
            "name": proj_data.get("name", proj_key),
            "aliases": aliases if isinstance(aliases, list) else [],
            "environment_count": env_count,
        })
    return result


def list_project_catalog(
    *,
    framework_dir: Optional[Path] = None,
) -> list[ProjectCatalogEntry]:
    """List project keys the Settings Projects tab can bind to."""
    entries: list[ProjectCatalogEntry] = []
    for item in list_projects(framework_dir=framework_dir):
        aliases_raw = item.get("aliases") or []
        aliases = tuple(
            str(alias).strip()
            for alias in aliases_raw
            if alias is not None and str(alias).strip()
        )
        name = item.get("name") or item["key"]
        entries.append(
            ProjectCatalogEntry(
                project_key=str(item["key"]),
                name=str(name).strip() or str(item["key"]),
                aliases=aliases,
            )
        )
    return entries


def resolve_project_key(
    project_key: str,
    *,
    framework_dir: Optional[Path] = None,
) -> Optional[ProjectCatalogEntry]:
    """Return the catalog entry for a key or alias, or ``None``."""
    try:
        key, _data = resolve_project(project_key, framework_dir=framework_dir)
    except ProjectNotFoundError:
        return None
    for entry in list_project_catalog(framework_dir=framework_dir):
        if entry.project_key == key:
            return entry
    return None


def list_environments(
    project: str,
    *,
    framework_dir: Optional[Path] = None,
) -> list[str]:
    """Return sorted environment names for a project.

    Raises:
        ProjectNotFoundError: When the project cannot be resolved.
    """
    _key, proj_data = resolve_project(project, framework_dir=framework_dir)
    envs = proj_data.get("environments")
    if not isinstance(envs, dict):
        return []
    return sorted(str(name) for name in envs.keys())


# ---------------------------------------------------------------------------
# Capability blocks
# ---------------------------------------------------------------------------


def _block(project: str, field: str, *, framework_dir: Optional[Path]) -> Optional[dict]:
    _key, proj_data = resolve_project(project, framework_dir=framework_dir)
    block = proj_data.get(field)
    if not isinstance(block, dict):
        return None
    return block


def resolve_certificate_profile(
    project: str,
    *,
    framework_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return the ``certificate_profile`` block when ``type`` is ``mtls``."""
    profile = _block(project, "certificate_profile", framework_dir=framework_dir)
    if profile is None or profile.get("type") != "mtls":
        return None
    return profile


def resolve_load_testing_config(
    project: str,
    *,
    framework_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return the ``load_testing`` block, or ``None`` when absent."""
    return _block(project, "load_testing", framework_dir=framework_dir)


def resolve_project_management_config(
    project: str,
    *,
    framework_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return the ``project_management`` block, or ``None`` when absent."""
    return _block(project, "project_management", framework_dir=framework_dir)


def resolve_documentation_config(
    project: str,
    *,
    framework_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return the ``documentation`` block, or ``None`` when absent."""
    return _block(project, "documentation", framework_dir=framework_dir)


# ---------------------------------------------------------------------------
# Composite context
# ---------------------------------------------------------------------------


def resolve_project_context(
    project: str,
    *,
    environment: Optional[str] = None,
    source: Optional[str] = None,
    framework_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Assemble the composite per-project context dict.

    Possible keys (each omitted when the underlying block is absent):

    - ``_resolved_environment`` when ``environment`` is supplied and found
    - ``_cert_profile``
    - ``_resolved_load_testing``
    - ``_resolved_project_management``
    - ``_resolved_documentation``

    An unknown environment logs a warning and is omitted. An unknown
    project raises ``ProjectNotFoundError``.
    """
    proj_key, _proj_data = resolve_project(project, framework_dir=framework_dir)
    result: dict[str, Any] = {}

    if environment is not None and str(environment).strip():
        try:
            url = resolve_environment_url(
                proj_key,
                str(environment).strip(),
                framework_dir=framework_dir,
            )
            result["_resolved_environment"] = {
                "project": proj_key,
                "environment": str(environment).strip(),
                "url": url,
                "source": source or _DEFAULT_SOURCE,
            }
        except EnvironmentNotFoundError:
            log_event(
                _LOGGER_NAME,
                logging.WARNING,
                "project_context_environment_not_found",
                {
                    "project_context.project": proj_key,
                    "project_context.environment": environment,
                    "message": (
                        "environment not configured for project; "
                        "omitting _resolved_environment from result"
                    ),
                },
            )

    cert_profile = resolve_certificate_profile(
        proj_key, framework_dir=framework_dir,
    )
    if cert_profile is not None:
        result["_cert_profile"] = cert_profile

    load_testing = resolve_load_testing_config(
        proj_key, framework_dir=framework_dir,
    )
    if load_testing is not None:
        result["_resolved_load_testing"] = load_testing

    project_management = resolve_project_management_config(
        proj_key, framework_dir=framework_dir,
    )
    if project_management is not None:
        result["_resolved_project_management"] = project_management

    documentation = resolve_documentation_config(
        proj_key, framework_dir=framework_dir,
    )
    if documentation is not None:
        result["_resolved_documentation"] = documentation

    log_event(
        _LOGGER_NAME,
        logging.DEBUG,
        "project_context_resolved",
        {
            "project_context.project": proj_key,
            "project_context.environment": environment,
            "project_context.keys_resolved": ",".join(sorted(result.keys())),
        },
    )
    return result


def stamp_project_context(body: dict, context: MappingLike) -> None:
    """Copy resolved context keys onto ``body`` without overwriting."""
    if not isinstance(context, dict):
        return
    for key in CONTEXT_KEYS:
        if key in context and key not in body:
            body[key] = context[key]


def format_project_context_briefing(context: dict) -> str:
    """Render a short briefing the orchestrator can show to the model."""
    if not context:
        return ""
    lines = ["Project context (from environments.yaml):"]
    env = context.get("_resolved_environment")
    if isinstance(env, dict):
        lines.append(
            "- environment: "
            f"{env.get('project')} / {env.get('environment')} "
            f"-> {env.get('url')}"
        )
    for key, label in (
        ("_resolved_load_testing", "load_testing"),
        ("_resolved_project_management", "project_management"),
        ("_resolved_documentation", "documentation"),
        ("_cert_profile", "certificate_profile"),
    ):
        block = context.get(key)
        if isinstance(block, dict):
            provider = block.get("provider") or block.get("type") or ""
            lines.append(f"- {label}: configured ({provider})".rstrip())
    return "\n".join(lines)


def project_identifier_from_payload(payload: Any) -> Optional[str]:
    """Read a project key or alias from a task payload or its metadata."""
    if not isinstance(payload, dict):
        return None
    for key in ("project", "project_key"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in ("project", "project_key"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    resolved = payload.get("_resolved_environment")
    if isinstance(resolved, dict):
        value = resolved.get("project")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def environment_name_from_payload(payload: Any) -> Optional[str]:
    """Read an environment name from a task payload or its metadata."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("environment")
    if isinstance(value, str) and value.strip():
        return value.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("environment")
        if isinstance(value, str) and value.strip():
            return value.strip()
    resolved = payload.get("_resolved_environment")
    if isinstance(resolved, dict):
        value = resolved.get("environment")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# Local alias so the stamp helper's annotation stays import-light.
MappingLike = dict
