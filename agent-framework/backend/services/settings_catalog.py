"""Settings component ids and required field names for AG-UI secret routes.

Keep these ids aligned with
``agent-framework/frontend/ui/lib/settings-catalog.ts``.
"""

from __future__ import annotations

from typing import Optional

FOCUS_AREAS: dict[str, str] = {
    "ado": "tracking",
    "jira": "tracking",
    "confluence-cloud": "docs",
    "confluence-onprem": "docs",
    "blazemeter": "load",
    "datadog": "observability",
    "github": "scm",
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "ado": ("token",),
    "jira": ("token",),
    "confluence-cloud": ("token",),
    "confluence-onprem": ("token",),
    "blazemeter": ("api_key", "api_secret"),
    "datadog": ("api_key", "app_key"),
    "github": ("token",),
}

INTEGRATION_COMPONENTS: dict[str, tuple[str, ...]] = {
    "confluence": ("confluence-cloud", "confluence-onprem"),
    "blazemeter": ("blazemeter",),
    "datadog": ("datadog",),
    "github": ("github",),
}


class UnknownComponentError(ValueError):
    """``component`` is not in the Settings catalog."""


def focus_area_for(component: str) -> str:
    """Return the focus-area id for a catalog component."""
    area = FOCUS_AREAS.get(component)
    if area is None:
        raise UnknownComponentError(f"Unknown component: {component}")
    return area


def required_fields_for(component: str) -> tuple[str, ...]:
    """Return required plaintext field names for a catalog component."""
    fields = REQUIRED_FIELDS.get(component)
    if fields is None:
        raise UnknownComponentError(f"Unknown component: {component}")
    return fields


def validate_secret_fields(component: str, fields: dict[str, str]) -> dict[str, str]:
    """Return trimmed required fields or raise ``ValueError`` / catalog error."""
    required = required_fields_for(component)
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object of string values.")
    cleaned: dict[str, str] = {}
    missing: list[str] = []
    for name in required:
        raw = fields.get(name)
        if not isinstance(raw, str) or not raw.strip():
            missing.append(name)
            continue
        cleaned[name] = raw.strip()
    if missing:
        raise ValueError(
            "Missing or empty field(s): " + ", ".join(missing)
        )
    return cleaned


def integration_for_component(component: str) -> Optional[str]:
    """Return the project-binding integration key for a component, if any."""
    for integration, components in INTEGRATION_COMPONENTS.items():
        if component in components:
            return integration
    return None
