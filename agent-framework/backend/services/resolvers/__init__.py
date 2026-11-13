"""Project-configuration resolvers.

``project_catalog`` loads ``config/environments.yaml`` and returns the
composite context dict shared by A2A and AG-UI. SCM URL parsing stays in
``services.scm_resolver``.
"""

from .project_catalog import (
    CONTEXT_KEYS,
    EnvironmentNotFoundError,
    ProjectCatalogEntry,
    ProjectNotFoundError,
    clear_cache,
    environment_name_from_payload,
    format_project_context_briefing,
    list_environments,
    list_project_catalog,
    list_projects,
    load_projects_config,
    project_identifier_from_payload,
    resolve_certificate_profile,
    resolve_documentation_config,
    resolve_environment_url,
    resolve_load_testing_config,
    resolve_project,
    resolve_project_context,
    resolve_project_key,
    resolve_project_management_config,
    stamp_project_context,
)

__all__ = [
    "CONTEXT_KEYS",
    "EnvironmentNotFoundError",
    "ProjectCatalogEntry",
    "ProjectNotFoundError",
    "clear_cache",
    "environment_name_from_payload",
    "format_project_context_briefing",
    "list_environments",
    "list_project_catalog",
    "list_projects",
    "load_projects_config",
    "project_identifier_from_payload",
    "resolve_certificate_profile",
    "resolve_documentation_config",
    "resolve_environment_url",
    "resolve_load_testing_config",
    "resolve_project",
    "resolve_project_context",
    "resolve_project_key",
    "resolve_project_management_config",
    "stamp_project_context",
]
