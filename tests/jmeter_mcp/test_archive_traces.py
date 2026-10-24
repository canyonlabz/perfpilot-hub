"""A2A-E9: archive traces degrades on Permission denied; capture can continue."""

from __future__ import annotations

import logging
from pathlib import Path


def test_remove_archived_source_returns_error_on_permission(
    playwright_adapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "resources"
    target.mkdir()
    (target / "chunk.bin").write_bytes(b"x")

    def _deny(_path):
        raise PermissionError("Permission denied when archiving traces: resources")

    monkeypatch.setattr(playwright_adapter.shutil, "rmtree", _deny)
    error = playwright_adapter._remove_archived_source(str(target))
    assert error is not None
    assert "Permission denied" in error
    assert target.exists()


def test_archive_returns_backup_when_resources_delete_fails(
    playwright_adapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    traces = tmp_path / "traces"
    resources = traces / "resources"
    resources.mkdir(parents=True)
    (resources / "chunk.bin").write_bytes(b"old")
    (traces / "trace.network").write_text("ndjson\n", encoding="utf-8")

    monkeypatch.setattr(playwright_adapter, "PLAYWRIGHT_TRACES_DIR", str(traces))

    real_rmtree = playwright_adapter.shutil.rmtree

    def _rmtree(path, *args, **kwargs):
        if Path(path).name == "resources":
            raise PermissionError("Permission denied: traces/resources")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(playwright_adapter.shutil, "rmtree", _rmtree)
    caplog.set_level(logging.WARNING, logger=playwright_adapter.log.name)

    result = playwright_adapter.archive_existing_traces()

    assert result.archived_path is not None
    assert result.degraded is True
    assert "Permission denied" in result.detail
    backup = Path(result.archived_path)
    assert (backup / "resources" / "chunk.bin").is_file()
    assert (backup / "trace.network").is_file()
    assert resources.exists()
    assert any("delete_degraded" in rec.getMessage() for rec in caplog.records)


def test_archive_ok_when_cleanup_succeeds(
    playwright_adapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "trace.network").write_text("ndjson\n", encoding="utf-8")
    monkeypatch.setattr(playwright_adapter, "PLAYWRIGHT_TRACES_DIR", str(traces))

    result = playwright_adapter.archive_existing_traces()

    assert result.archived_path is not None
    assert result.degraded is False
    assert result.detail == ""
    assert not any(traces.iterdir())
    assert (Path(result.archived_path) / "trace.network").is_file()


def test_archive_no_action_when_empty(
    playwright_adapter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traces = tmp_path / "traces"
    monkeypatch.setattr(playwright_adapter, "PLAYWRIGHT_TRACES_DIR", str(traces))
    result = playwright_adapter.archive_existing_traces()
    assert result.archived_path is None
    assert result.degraded is False
