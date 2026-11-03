"""Unit tests for AG-UI Settings catalog helpers."""

from __future__ import annotations

import pytest

from services import settings_catalog


def test_validate_secret_fields_requires_datadog_keys() -> None:
    with pytest.raises(ValueError, match="app_key"):
        settings_catalog.validate_secret_fields(
            "datadog", {"api_key": "k"}
        )


def test_validate_secret_fields_accepts_github_token() -> None:
    cleaned = settings_catalog.validate_secret_fields(
        "github", {"token": "  ghp_x  "}
    )
    assert cleaned == {"token": "ghp_x"}


def test_unknown_component_raises() -> None:
    with pytest.raises(settings_catalog.UnknownComponentError):
        settings_catalog.required_fields_for("not-a-vendor")


def test_focus_area_for_github() -> None:
    assert settings_catalog.focus_area_for("github") == "scm"
