"""Unit tests for the project catalog resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.resolvers import project_catalog
from services.resolvers.project_catalog import (
    EnvironmentNotFoundError,
    ProjectNotFoundError,
)


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    project_catalog.clear_cache()
    yield
    project_catalog.clear_cache()


def _write(tmp_path: Path, body: str, *, example: bool = False) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    name = "environments.example.yaml" if example else "environments.yaml"
    (config_dir / name).write_text(body, encoding="utf-8")


_SAMPLE = """
projects:
  project1:
    name: Alpha
    aliases: [AIPTE, PerfPilotOS]
    certificate_profile:
      type: mtls
      jks_file: project1.jks
    load_testing:
      provider: blazemeter
      workspace_id: "123"
    project_management:
      provider: azure_devops
      organization: my-org
      project: Alpha
      url: https://dev.azure.com/my-org/Alpha
    documentation:
      provider: confluence
      mode: cloud
      space_ref: PTE
    environments:
      QA:
        url: https://qa.example.com
        label: QA
  project2:
    name: Beta
    aliases: [JIRADEMO]
    project_management:
      provider: jira
      site_url: https://example.atlassian.net
      project_key: PERF
    environments:
      QA:
        url: https://beta.example.com
"""


def test_list_project_catalog_reads_projects(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE)
    entries = project_catalog.list_project_catalog(framework_dir=tmp_path)
    assert [item.project_key for item in entries] == ["project1", "project2"]
    alpha = entries[0]
    assert alpha.name == "Alpha"
    assert alpha.aliases == ("AIPTE", "PerfPilotOS")


def test_resolve_project_key_matches_alias_case_insensitively(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE)
    found = project_catalog.resolve_project_key("aipte", framework_dir=tmp_path)
    assert found is not None
    assert found.project_key == "project1"


def test_missing_projects_section_returns_empty_catalog(tmp_path: Path) -> None:
    _write(tmp_path, "environments:\n  QA: {}\n")
    assert project_catalog.list_project_catalog(framework_dir=tmp_path) == []


def test_example_file_is_used_when_real_file_is_absent(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE, example=True)
    entries = project_catalog.list_project_catalog(framework_dir=tmp_path)
    assert [item.project_key for item in entries] == ["project1", "project2"]


def test_resolve_project_context_assembles_capability_blocks(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE)
    context = project_catalog.resolve_project_context(
        "PerfPilotOS",
        environment="qa",
        framework_dir=tmp_path,
    )
    assert context["_resolved_environment"]["url"] == "https://qa.example.com"
    assert context["_resolved_environment"]["project"] == "project1"
    assert context["_cert_profile"]["jks_file"] == "project1.jks"
    assert context["_resolved_load_testing"]["workspace_id"] == "123"
    assert context["_resolved_project_management"]["provider"] == "azure_devops"
    assert context["_resolved_documentation"]["mode"] == "cloud"


def test_unknown_environment_omits_url_and_keeps_other_blocks(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE)
    context = project_catalog.resolve_project_context(
        "project1",
        environment="PROD",
        framework_dir=tmp_path,
    )
    assert "_resolved_environment" not in context
    assert "_resolved_load_testing" in context


def test_jira_block_is_preserved(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE)
    context = project_catalog.resolve_project_context(
        "jirademo",
        framework_dir=tmp_path,
    )
    assert "_resolved_environment" not in context
    assert context["_resolved_project_management"]["provider"] == "jira"
    assert context["_resolved_project_management"]["project_key"] == "PERF"
    assert "_resolved_load_testing" not in context


def test_unknown_project_raises(tmp_path: Path) -> None:
    _write(tmp_path, _SAMPLE)
    with pytest.raises(ProjectNotFoundError):
        project_catalog.resolve_project("missing", framework_dir=tmp_path)


def test_environment_url_requires_a_url(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "projects:\n  p:\n    name: P\n    environments:\n      QA: {}\n",
    )
    with pytest.raises(EnvironmentNotFoundError):
        project_catalog.resolve_environment_url("p", "QA", framework_dir=tmp_path)


def test_stamp_does_not_overwrite_existing_keys() -> None:
    body = {"_resolved_load_testing": {"workspace_id": "kept"}}
    project_catalog.stamp_project_context(
        body,
        {"_resolved_load_testing": {"workspace_id": "new"}, "_cert_profile": {"type": "mtls"}},
    )
    assert body["_resolved_load_testing"]["workspace_id"] == "kept"
    assert body["_cert_profile"]["type"] == "mtls"


def test_payload_helpers_read_metadata() -> None:
    payload = {"metadata": {"project": "AIPTE", "environment": "QA"}}
    assert project_catalog.project_identifier_from_payload(payload) == "AIPTE"
    assert project_catalog.environment_name_from_payload(payload) == "QA"
