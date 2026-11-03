"""Resolve Web-UI user secret fields from a JWT + perfagent_state.

No Authorization header → A2A / env path (return None).
A present JWT that fails verify, or a valid JWT with no binding /
fields → error. Never fall back to env when a JWT is present.
Never log field values, JWTs, or the master key.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import asyncpg

from utils.mcp_context_jwt import McpJwtError, verify_mcp_context_jwt
from utils.secret_crypto import EncryptedField, binding_aad, decrypt_field

log = logging.getLogger(__name__)

_TOKEN_COLUMN = {
    "github": "github_token_name",
    "blazemeter": "blazemeter_token_name",
    "datadog": "datadog_token_name",
    "confluence": "confluence_token_name",
}

_CONFLUENCE_COMPONENT_TO_MODE = {
    "confluence-cloud": "cloud",
    "confluence-onprem": "onprem",
}

_pool: Optional[asyncpg.Pool] = None


class CredentialResolveError(Exception):
    """Web-UI credential lookup failed. Message is safe to return to the user."""


def _bearer_token() -> Optional[str]:
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers(include={"authorization"})
    except Exception:
        return None
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    raw = raw.strip()
    if not raw:
        return None
    kind, _, rest = raw.partition(" ")
    if kind.lower() != "bearer" or not rest.strip():
        raise CredentialResolveError("MCP context token is invalid.")
    return rest.strip()


async def _pool_conn() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        required = (
            "PERFAGENT_STATE_HOST",
            "PERFAGENT_STATE_PORT",
            "PERFAGENT_STATE_DB",
            "PERFAGENT_STATE_USER",
            "PERFAGENT_STATE_PASSWORD",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise CredentialResolveError(
                "Web-UI secrets database is not configured on this MCP."
            )
        _pool = await asyncpg.create_pool(
            host=os.environ["PERFAGENT_STATE_HOST"],
            port=int(os.environ["PERFAGENT_STATE_PORT"]),
            database=os.environ["PERFAGENT_STATE_DB"],
            user=os.environ["PERFAGENT_STATE_USER"],
            password=os.environ["PERFAGENT_STATE_PASSWORD"],
            min_size=1,
            max_size=4,
        )
    return _pool


async def resolve_user_fields(integration: str) -> Optional[dict[str, str]]:
    """Return decrypted fields for the active binding, or None for A2A.

    ``integration`` is github / blazemeter / datadog / confluence.
    """
    column = _TOKEN_COLUMN.get(integration)
    if column is None:
        raise CredentialResolveError(f"Unknown integration: {integration}")
    try:
        compact = _bearer_token()
    except CredentialResolveError:
        raise
    if compact is None:
        return None
    try:
        claims = verify_mcp_context_jwt(compact)
    except McpJwtError as exc:
        raise CredentialResolveError(str(exc)) from exc

    pool = await _pool_conn()
    async with pool.acquire() as conn:
        binding = await conn.fetchrow(
            f"""
            SELECT project_key, {column} AS token_name
            FROM user_project_bindings
            WHERE user_id = $1 AND is_active
            LIMIT 1
            """,
            claims.user_id,
        )
        if binding is None or not binding["token_name"]:
            project = binding["project_key"] if binding else "(none active)"
            raise CredentialResolveError(
                f"No token configured for {integration} on project {project}."
            )
        token_name = binding["token_name"]
        secret = await conn.fetchrow(
            """
            SELECT id, component, name
            FROM user_secrets
            WHERE user_id = $1 AND name = $2
            LIMIT 1
            """,
            claims.user_id,
            token_name,
        )
        if secret is None:
            raise CredentialResolveError(
                f"No token configured for {integration} on project "
                f"{binding['project_key']}."
            )
        rows = await conn.fetch(
            """
            SELECT field_name, ciphertext, nonce, key_version
            FROM user_secret_fields
            WHERE secret_id = $1
            """,
            secret["id"],
        )

    if not rows:
        raise CredentialResolveError(
            f"No token configured for {integration} on project "
            f"{binding['project_key']}."
        )
    fields: dict[str, str] = {}
    for row in rows:
        aad = binding_aad(
            claims.user_id,
            secret["component"],
            secret["name"],
            row["field_name"],
        )
        fields[row["field_name"]] = decrypt_field(
            EncryptedField(
                ciphertext=row["ciphertext"],
                nonce=row["nonce"],
                key_version=row["key_version"],
            ),
            aad=aad,
        )
    return fields


async def resolve_confluence_mode() -> Optional[str]:
    """Return ``cloud`` / ``onprem`` from the Web-UI Settings component.

    No Authorization header → ``None`` (A2A keeps the caller ``mode``).
    JWT present → map ``confluence-cloud`` / ``confluence-onprem`` from
    the active project's bound secret. Never returns values or logs them.
    """
    try:
        compact = _bearer_token()
    except CredentialResolveError:
        raise
    if compact is None:
        return None
    try:
        claims = verify_mcp_context_jwt(compact)
    except McpJwtError as exc:
        raise CredentialResolveError(str(exc)) from exc

    pool = await _pool_conn()
    async with pool.acquire() as conn:
        binding = await conn.fetchrow(
            """
            SELECT project_key, confluence_token_name AS token_name
            FROM user_project_bindings
            WHERE user_id = $1 AND is_active
            LIMIT 1
            """,
            claims.user_id,
        )
        if binding is None or not binding["token_name"]:
            project = binding["project_key"] if binding else "(none active)"
            raise CredentialResolveError(
                f"No token configured for confluence on project {project}."
            )
        secret = await conn.fetchrow(
            """
            SELECT component
            FROM user_secrets
            WHERE user_id = $1 AND name = $2
            LIMIT 1
            """,
            claims.user_id,
            binding["token_name"],
        )

    if secret is None:
        raise CredentialResolveError(
            f"No token configured for confluence on project "
            f"{binding['project_key']}."
        )
    mode = _CONFLUENCE_COMPONENT_TO_MODE.get(secret["component"])
    if mode is None:
        raise CredentialResolveError(
            "Active Confluence secret component must be confluence-cloud "
            "or confluence-onprem."
        )
    return mode
