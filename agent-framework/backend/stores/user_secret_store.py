"""Named secret metadata and encrypted field writes.

List helpers never decrypt. Plaintext exists only in the create path
long enough to call ``encrypt_field``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from . import db

log = logging.getLogger(__name__)


class DuplicateSecretError(Exception):
    """A secret with the same (user, component, name) already exists."""


class SecretNotFoundError(Exception):
    """No secret for this owner and id."""


@dataclass
class UserSecretMeta:
    """Metadata returned to the browser. No ciphertext, no plaintext."""

    id: UUID
    user_id: str
    component: str
    name: str
    field_names: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _field_names_from_row(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


async def list_secrets(user_id: str) -> list[UserSecretMeta]:
    """Return metadata and field *names* for one owner."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                s.id,
                s.user_id,
                s.component,
                s.name,
                s.created_at,
                s.updated_at,
                COALESCE(
                    array_agg(f.field_name ORDER BY f.field_name)
                        FILTER (WHERE f.field_name IS NOT NULL),
                    '{}'
                ) AS field_names
            FROM user_secrets s
            LEFT JOIN user_secret_fields f ON f.secret_id = s.id
            WHERE s.user_id = $1
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.name
            """,
            user_id,
        )
    return [
        UserSecretMeta(
            id=_as_uuid(row["id"]),
            user_id=row["user_id"],
            component=row["component"],
            name=row["name"],
            field_names=_field_names_from_row(row["field_names"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in rows
    ]


async def get_secret(user_id: str, secret_id: UUID) -> Optional[UserSecretMeta]:
    """Owner-scoped lookup. None when missing or owned by someone else."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                s.id,
                s.user_id,
                s.component,
                s.name,
                s.created_at,
                s.updated_at,
                COALESCE(
                    array_agg(f.field_name ORDER BY f.field_name)
                        FILTER (WHERE f.field_name IS NOT NULL),
                    '{}'
                ) AS field_names
            FROM user_secrets s
            LEFT JOIN user_secret_fields f ON f.secret_id = s.id
            WHERE s.id = $1 AND s.user_id = $2
            GROUP BY s.id
            """,
            secret_id,
            user_id,
        )
    if row is None:
        return None
    return UserSecretMeta(
        id=_as_uuid(row["id"]),
        user_id=row["user_id"],
        component=row["component"],
        name=row["name"],
        field_names=_field_names_from_row(row["field_names"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def name_exists(
    user_id: str,
    name: str,
    components: tuple[str, ...],
) -> bool:
    """True when this owner has a secret with ``name`` in ``components``."""
    if not components:
        return False
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        found = await conn.fetchval(
            """
            SELECT 1
            FROM user_secrets
            WHERE user_id = $1 AND name = $2 AND component = ANY($3::text[])
            LIMIT 1
            """,
            user_id,
            name,
            list(components),
        )
    return found is not None


async def create_secret(
    user_id: str,
    *,
    component: str,
    name: str,
    fields: dict[str, str],
) -> UserSecretMeta:
    """Insert metadata and encrypt each field. Raises ``DuplicateSecretError``."""
    name = name.strip()
    encrypted = {}
    for field_name, plaintext in fields.items():
        encrypted[field_name] = _encrypt_field(
            plaintext,
            user_id=user_id,
            component=component,
            name=name,
            field_name=field_name,
        )

    pool = await db.get_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO user_secrets (user_id, component, name)
                    VALUES ($1, $2, $3)
                    RETURNING id, user_id, component, name, created_at, updated_at
                    """,
                    user_id,
                    component,
                    name,
                )
                secret_id = _as_uuid(row["id"])
                for field_name, blob in encrypted.items():
                    await conn.execute(
                        """
                        INSERT INTO user_secret_fields
                            (secret_id, field_name, ciphertext, nonce, key_version)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        secret_id,
                        field_name,
                        blob.ciphertext,
                        blob.nonce,
                        blob.key_version,
                    )
    except Exception as exc:
        if _is_unique_violation(exc):
            raise DuplicateSecretError(
                "A secret with this component and name already exists."
            ) from exc
        raise

    return UserSecretMeta(
        id=secret_id,
        user_id=row["user_id"],
        component=row["component"],
        name=row["name"],
        field_names=sorted(encrypted.keys()),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def delete_secret(user_id: str, secret_id: UUID) -> UserSecretMeta:
    """Delete one owned secret (fields cascade). Raises ``SecretNotFoundError``."""
    existing = await get_secret(user_id, secret_id)
    if existing is None:
        raise SecretNotFoundError("Secret not found.")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_secrets WHERE id = $1 AND user_id = $2",
            secret_id,
            user_id,
        )
    if result == "DELETE 0":
        raise SecretNotFoundError("Secret not found.")
    return existing


def _encrypt_field(
    plaintext: str,
    *,
    user_id: str,
    component: str,
    name: str,
    field_name: str,
):
    """Encrypt one field. Imported lazily so list/delete do not need the key."""
    from utils.secret_crypto import binding_aad, encrypt_field

    return encrypt_field(
        plaintext,
        aad=binding_aad(user_id, component, name, field_name),
    )


def _is_unique_violation(exc: BaseException) -> bool:
    if type(exc).__name__ == "UniqueViolationError":
        return True
    try:
        import asyncpg
    except ImportError:
        return False
    return isinstance(exc, asyncpg.UniqueViolationError)


def secret_to_dict(secret: UserSecretMeta, *, focus_area: str) -> dict[str, Any]:
    """JSON shape for ``GET /api/secrets``. Metadata only."""
    return {
        "id": str(secret.id),
        "name": secret.name,
        "component": secret.component,
        "focus_area": focus_area,
        "field_names": list(secret.field_names),
        "updated_at": (
            secret.updated_at.isoformat() if secret.updated_at is not None else None
        ),
    }
