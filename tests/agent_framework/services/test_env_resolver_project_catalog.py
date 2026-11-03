"""Unit tests for ``env_resolver.list_project_catalog``."""

from __future__ import annotations

from pathlib import Path

from services import env_resolver


def test_list_project_catalog_prefers_projects_section(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "environments.yaml").write_text(
        "projects:\n  project1:\n    name: Alpha\n    aliases: [A1]\n"
        "environments:\n  QA:\n    hostname: https://qa.example.com\n",
        encoding="utf-8",
    )
    env_resolver.clear_environments_cache()
    entries = env_resolver.list_project_catalog(framework_dir=tmp_path)
    assert [item.project_key for item in entries] == ["project1"]
    assert entries[0].name == "Alpha"
    assert entries[0].aliases == ("A1",)
    env_resolver.clear_environments_cache()


def test_list_project_catalog_falls_back_to_environments(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "environments.yaml").write_text(
        "environments:\n  QA: {}\n  UAT: {}\n",
        encoding="utf-8",
    )
    env_resolver.clear_environments_cache()
    entries = env_resolver.list_project_catalog(framework_dir=tmp_path)
    assert [item.project_key for item in entries] == ["QA", "UAT"]
    env_resolver.clear_environments_cache()


def test_resolve_project_key_is_case_insensitive(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "environments.yaml").write_text(
        "environments:\n  QA: {}\n",
        encoding="utf-8",
    )
    env_resolver.clear_environments_cache()
    found = env_resolver.resolve_project_key("qa", framework_dir=tmp_path)
    assert found is not None
    assert found.project_key == "QA"
    env_resolver.clear_environments_cache()
