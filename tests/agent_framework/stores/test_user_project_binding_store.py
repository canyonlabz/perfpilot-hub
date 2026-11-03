"""Unit tests for ``stores.user_project_binding_store``. No live SQL."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from stores import user_project_binding_store


def _binding_row(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid4(),
        "user_id": "user-1",
        "project_key": "QA",
        "is_enabled": True,
        "is_active": True,
        "confluence_token_name": None,
        "blazemeter_token_name": None,
        "datadog_token_name": None,
        "github_token_name": "work-pat",
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


async def test_get_active_binding_returns_row(fake_pool) -> None:
    fake_pool.conn.fetchrow_result = _binding_row()
    row = await user_project_binding_store.get_active_binding("user-1")
    assert row is not None
    assert row.project_key == "QA"
    assert row.github_token_name == "work-pat"
    sql, args = fake_pool.conn.calls[0]
    assert "is_active" in sql
    assert args == ("user-1",)


async def test_replace_bindings_rejects_two_active(fake_pool) -> None:
    with pytest.raises(user_project_binding_store.BindingError, match="one project"):
        await user_project_binding_store.replace_bindings(
            "user-1",
            [
                {"project_key": "QA", "is_active": True},
                {"project_key": "UAT", "is_active": True},
            ],
        )
    assert fake_pool.conn.calls == []


async def test_replace_bindings_clears_active_then_upserts(fake_pool) -> None:
    fake_pool.conn.fetch_result = [_binding_row()]
    rows = await user_project_binding_store.replace_bindings(
        "user-1",
        [{"project_key": "QA", "is_enabled": True, "is_active": True}],
    )
    assert rows[0].project_key == "QA"
    sqls = [sql for sql, _ in fake_pool.conn.calls]
    assert any("is_active = FALSE" in sql for sql in sqls)
    assert any("ON CONFLICT (user_id, project_key)" in sql for sql in sqls)


async def test_clear_token_name_updates_all_columns(fake_pool) -> None:
    await user_project_binding_store.clear_token_name("user-1", "work-pat")
    columns = [
        "confluence_token_name",
        "blazemeter_token_name",
        "datadog_token_name",
        "github_token_name",
    ]
    updated = " ".join(sql for sql, _ in fake_pool.conn.calls)
    for column in columns:
        assert column in updated
