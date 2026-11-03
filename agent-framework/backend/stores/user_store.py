"""CRUD over the ``users`` table.

``user_id`` is the opaque ``perfpilot_token`` cookie value today. Email
is self-asserted and used only as a recovery hint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from . import db

log = logging.getLogger(__name__)

_SELECT_COLS = (
    "user_id, display_name, email, oid, created_at, updated_at"
)


@dataclass
class UserProfile:
    """Materialized ``users`` row. Never includes secrets."""

    user_id: str
    display_name: Optional[str]
    email: Optional[str]
    oid: Optional[str]
    created_at: datetime
    updated_at: datetime


def _row_to_user(row: Any) -> UserProfile:
    return UserProfile(
        user_id=row["user_id"],
        display_name=row["display_name"],
        email=row["email"],
        oid=row["oid"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    if not isinstance(email, str):
        raise ValueError("email must be a string.")
    cleaned = email.strip()
    return cleaned or None


def _normalize_name(display_name: Optional[str]) -> Optional[str]:
    if display_name is None:
        return None
    if not isinstance(display_name, str):
        raise ValueError("display_name must be a string.")
    cleaned = display_name.strip()
    return cleaned or None


async def get_user(user_id: str) -> Optional[UserProfile]:
    """Return the profile for ``user_id``, or None."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLS} FROM users WHERE user_id = $1",
            user_id,
        )
    return _row_to_user(row) if row is not None else None


async def get_or_create_user(user_id: str) -> UserProfile:
    """Return the profile, inserting an empty row when the cookie is new."""
    if not user_id or not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id is required.")
    user_id = user_id.strip()
    existing = await get_user(user_id)
    if existing is not None:
        return existing
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO users (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO UPDATE
                SET user_id = users.user_id
            RETURNING {_SELECT_COLS}
            """,
            user_id,
        )
    return _row_to_user(row)


async def update_user(
    user_id: str,
    *,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    update_display_name: bool = False,
    update_email: bool = False,
) -> Optional[UserProfile]:
    """Patch profile fields that the caller marked as present."""
    if not update_display_name and not update_email:
        return await get_user(user_id)

    assignments: list[str] = ["updated_at = NOW()"]
    args: list[Any] = []
    if update_display_name:
        args.append(_normalize_name(display_name))
        assignments.append(f"display_name = ${len(args)}")
    if update_email:
        args.append(_normalize_email(email))
        assignments.append(f"email = ${len(args)}")
    args.append(user_id)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE users
            SET {", ".join(assignments)}
            WHERE user_id = ${len(args)}
            RETURNING {_SELECT_COLS}
            """,
            *args,
        )
    return _row_to_user(row) if row is not None else None


async def find_by_email(email: str) -> Optional[UserProfile]:
    """Return the newest profile whose email matches (case-insensitive)."""
    cleaned = _normalize_email(email)
    if cleaned is None:
        return None
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_SELECT_COLS}
            FROM users
            WHERE email IS NOT NULL AND lower(email) = lower($1)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            cleaned,
        )
    return _row_to_user(row) if row is not None else None


def profile_to_dict(user: UserProfile) -> dict[str, Any]:
    """JSON shape for ``GET /api/me``. No secrets."""
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "email": user.email,
        "oid": user.oid,
    }
