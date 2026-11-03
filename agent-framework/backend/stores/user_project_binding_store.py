"""Per-user project enable/active flags and token-name bindings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from . import db

log = logging.getLogger(__name__)

_TOKEN_COLUMNS = (
    "confluence_token_name",
    "blazemeter_token_name",
    "datadog_token_name",
    "github_token_name",
)

_SELECT_COLS = (
    "id, user_id, project_key, is_enabled, is_active, "
    "confluence_token_name, blazemeter_token_name, "
    "datadog_token_name, github_token_name, created_at, updated_at"
)


class BindingError(Exception):
    """Invalid binding payload."""


@dataclass
class UserProjectBinding:
    """One ``user_project_bindings`` row. Token columns are names only."""

    id: UUID
    user_id: str
    project_key: str
    is_enabled: bool
    is_active: bool
    confluence_token_name: Optional[str]
    blazemeter_token_name: Optional[str]
    datadog_token_name: Optional[str]
    github_token_name: Optional[str]
    created_at: datetime
    updated_at: datetime


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _row_to_binding(row: Any) -> UserProjectBinding:
    return UserProjectBinding(
        id=_as_uuid(row["id"]),
        user_id=row["user_id"],
        project_key=row["project_key"],
        is_enabled=bool(row["is_enabled"]),
        is_active=bool(row["is_active"]),
        confluence_token_name=row["confluence_token_name"],
        blazemeter_token_name=row["blazemeter_token_name"],
        datadog_token_name=row["datadog_token_name"],
        github_token_name=row["github_token_name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _empty_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BindingError("Token names must be strings.")
    cleaned = value.strip()
    return cleaned or None


async def get_active_binding(user_id: str) -> Optional[UserProjectBinding]:
    """Return the single active binding for ``user_id``, or None."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_SELECT_COLS}
            FROM user_project_bindings
            WHERE user_id = $1 AND is_active
            LIMIT 1
            """,
            user_id,
        )
    return _row_to_binding(row) if row is not None else None


async def list_bindings(user_id: str) -> list[UserProjectBinding]:
    """All binding rows for one owner."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_SELECT_COLS}
            FROM user_project_bindings
            WHERE user_id = $1
            ORDER BY project_key
            """,
            user_id,
        )
    return [_row_to_binding(row) for row in rows]


async def replace_bindings(
    user_id: str,
    rows: list[dict[str, Any]],
) -> list[UserProjectBinding]:
    """Upsert the given rows. At most one ``is_active`` may be true."""
    active_count = sum(1 for row in rows if bool(row.get("is_active")))
    if active_count > 1:
        raise BindingError("At most one project may be active.")

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE user_project_bindings
                SET is_active = FALSE, updated_at = NOW()
                WHERE user_id = $1 AND is_active
                """,
                user_id,
            )
            for raw in rows:
                project_key = str(raw["project_key"]).strip()
                await conn.execute(
                    """
                    INSERT INTO user_project_bindings (
                        user_id, project_key, is_enabled, is_active,
                        confluence_token_name, blazemeter_token_name,
                        datadog_token_name, github_token_name
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (user_id, project_key) DO UPDATE SET
                        is_enabled = EXCLUDED.is_enabled,
                        is_active = EXCLUDED.is_active,
                        confluence_token_name = EXCLUDED.confluence_token_name,
                        blazemeter_token_name = EXCLUDED.blazemeter_token_name,
                        datadog_token_name = EXCLUDED.datadog_token_name,
                        github_token_name = EXCLUDED.github_token_name,
                        updated_at = NOW()
                    """,
                    user_id,
                    project_key,
                    bool(raw.get("is_enabled")),
                    bool(raw.get("is_active")),
                    _empty_name(raw.get("confluence_token_name")),
                    _empty_name(raw.get("blazemeter_token_name")),
                    _empty_name(raw.get("datadog_token_name")),
                    _empty_name(raw.get("github_token_name")),
                )
    return await list_bindings(user_id)


async def clear_token_name(user_id: str, token_name: str) -> None:
    """Clear any binding columns that pointed at a deleted secret name."""
    if not token_name:
        return
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for column in _TOKEN_COLUMNS:
                await conn.execute(
                    f"""
                    UPDATE user_project_bindings
                    SET {column} = NULL, updated_at = NOW()
                    WHERE user_id = $1 AND {column} = $2
                    """,
                    user_id,
                    token_name,
                )


def binding_to_dict(binding: UserProjectBinding) -> dict[str, Any]:
    """JSON fragment for one persisted binding."""
    return {
        "project_key": binding.project_key,
        "is_enabled": binding.is_enabled,
        "is_active": binding.is_active,
        "confluence_token_name": binding.confluence_token_name,
        "blazemeter_token_name": binding.blazemeter_token_name,
        "datadog_token_name": binding.datadog_token_name,
        "github_token_name": binding.github_token_name,
    }
