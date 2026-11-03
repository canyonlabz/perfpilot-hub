"""Unit tests for ``stores.user_store``. Fake pool only — no live SQL."""

from __future__ import annotations

from datetime import datetime, timezone

from stores import user_store


def _row(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "user_id": "user-1",
        "display_name": "Ada",
        "email": "ada@example.com",
        "oid": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


async def test_get_or_create_returns_existing(fake_pool) -> None:
    fake_pool.conn.fetchrow_result = _row()
    profile = await user_store.get_or_create_user("user-1")
    assert profile.display_name == "Ada"
    assert "INSERT" not in fake_pool.conn.calls[0][0]


async def test_get_or_create_inserts_when_missing(fake_pool) -> None:
    fake_pool.conn.fetchrow_result = None

    async def _fetchrow(sql: str, *args):
        fake_pool.conn.calls.append((sql, args))
        if "INSERT" in sql:
            return _row(display_name=None, email=None)
        return None

    fake_pool.conn.fetchrow = _fetchrow  # type: ignore[method-assign]
    profile = await user_store.get_or_create_user("user-1")
    assert profile.user_id == "user-1"
    assert profile.email is None
    assert any("INSERT" in sql for sql, _ in fake_pool.conn.calls)


async def test_update_user_patches_only_marked_fields(fake_pool) -> None:
    fake_pool.conn.fetchrow_result = _row(display_name="Ada Lovelace")
    await user_store.update_user(
        "user-1",
        display_name="Ada Lovelace",
        update_display_name=True,
    )
    sql, args = fake_pool.conn.calls[0]
    set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    assert "display_name" in set_clause
    assert "email" not in set_clause
    assert args[0] == "Ada Lovelace"
    assert args[-1] == "user-1"


async def test_find_by_email_uses_lower(fake_pool) -> None:
    fake_pool.conn.fetchrow_result = _row()
    found = await user_store.find_by_email("  ADA@example.com ")
    assert found is not None
    sql, args = fake_pool.conn.calls[0]
    assert "lower(email)" in sql
    assert args[0] == "ADA@example.com"


async def test_profile_to_dict_has_no_secret_keys() -> None:
    now = datetime.now(timezone.utc)
    payload = user_store.profile_to_dict(
        user_store.UserProfile(
            user_id="u",
            display_name="A",
            email="a@example.com",
            oid=None,
            created_at=now,
            updated_at=now,
        )
    )
    assert set(payload) == {"user_id", "display_name", "email", "oid"}
