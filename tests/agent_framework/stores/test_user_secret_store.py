"""Unit tests for ``stores.user_secret_store``. No live database."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from stores import user_secret_store


class _Blob:
    ciphertext = b"cipher"
    nonce = b"nonce-12byte"
    key_version = 1


@pytest.fixture
def fake_encrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        user_secret_store,
        "_encrypt_field",
        lambda *args, **kwargs: _Blob(),
    )


def _meta_row(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid4(),
        "user_id": "user-1",
        "component": "github",
        "name": "work-pat",
        "created_at": now,
        "updated_at": now,
        "field_names": ["token"],
    }
    base.update(overrides)
    return base


async def test_list_secrets_returns_field_names_only(fake_pool) -> None:
    fake_pool.conn.fetch_result = [_meta_row()]
    items = await user_secret_store.list_secrets("user-1")
    assert items[0].name == "work-pat"
    assert items[0].field_names == ["token"]
    assert "ciphertext" not in fake_pool.conn.calls[0][0]


async def test_create_secret_encrypts_and_inserts(fake_pool, fake_encrypt) -> None:
    secret_id = uuid4()
    now = datetime.now(timezone.utc)
    fake_pool.conn.fetchrow_result = {
        "id": secret_id,
        "user_id": "user-1",
        "component": "github",
        "name": "work-pat",
        "created_at": now,
        "updated_at": now,
    }
    created = await user_secret_store.create_secret(
        "user-1",
        component="github",
        name="work-pat",
        fields={"token": "ghp_not-a-real-token"},
    )
    assert created.id == secret_id
    assert created.field_names == ["token"]
    insert_sql = " ".join(sql for sql, _ in fake_pool.conn.calls)
    assert "INSERT INTO user_secrets" in insert_sql
    assert "INSERT INTO user_secret_fields" in insert_sql
    field_args = [args for sql, args in fake_pool.conn.calls if "user_secret_fields" in sql][0]
    assert field_args[1] == "token"
    assert isinstance(field_args[2], (bytes, bytearray))
    assert b"ghp_not-a-real-token" not in field_args[2]


async def test_create_secret_duplicate_raises(fake_pool, fake_encrypt) -> None:
    class UniqueViolationError(Exception):
        pass

    fake_pool.conn.fetchrow_side_effect = UniqueViolationError()
    with pytest.raises(user_secret_store.DuplicateSecretError):
        await user_secret_store.create_secret(
            "user-1",
            component="github",
            name="work-pat",
            fields={"token": "x"},
        )


async def test_delete_secret_missing_raises(fake_pool) -> None:
    fake_pool.conn.fetchrow_result = None
    with pytest.raises(user_secret_store.SecretNotFoundError):
        await user_secret_store.delete_secret("user-1", uuid4())


async def test_secret_to_dict_is_metadata_only() -> None:
    now = datetime.now(timezone.utc)
    payload = user_secret_store.secret_to_dict(
        user_secret_store.UserSecretMeta(
            id=uuid4(),
            user_id="user-1",
            component="github",
            name="work-pat",
            field_names=["token"],
            created_at=now,
            updated_at=now,
        ),
        focus_area="scm",
    )
    assert "token" not in payload
    assert payload["field_names"] == ["token"]
    assert payload["focus_area"] == "scm"
