"""Unit tests for ``services.mcp_context_jwt``.

Uses a throwaway master key. Tokens in this file are test doubles only.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from services.mcp_context_jwt import (
    MCP_JWT_ALGORITHM,
    MCP_JWT_AUDIENCE,
    MCP_JWT_ISSUER,
    MCP_JWT_TTL_SECONDS,
    McpJwtError,
    mint_mcp_context_jwt,
    verify_mcp_context_jwt,
)
from utils.secret_crypto import MASTER_KEY_ENV, derive_jwt_hmac_key


def _install_test_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        MASTER_KEY_ENV,
        base64.b64encode(os.urandom(32)).decode("ascii"),
    )


def test_default_ttl_is_one_hour() -> None:
    assert MCP_JWT_TTL_SECONDS == 3600


def test_mint_requires_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    with pytest.raises(McpJwtError, match="user_id"):
        mint_mcp_context_jwt(user_id="  ")


def test_mint_requires_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    with pytest.raises(McpJwtError, match=MASTER_KEY_ENV):
        mint_mcp_context_jwt(user_id="user-1")


def test_mint_verify_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    token = mint_mcp_context_jwt(
        user_id="user-1",
        name="Ada Lovelace",
        email="ada@example.com",
    )
    claims = verify_mcp_context_jwt(token)
    assert claims.user_id == "user-1"
    assert claims.name == "Ada Lovelace"
    assert claims.email == "ada@example.com"


def test_verify_rejects_tampered_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    token = mint_mcp_context_jwt(user_id="user-1")
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(McpJwtError, match="invalid"):
        verify_mcp_context_jwt(tampered)


def test_verify_rejects_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    hmac_key = derive_jwt_hmac_key()
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {
            "sub": "user-1",
            "name": "",
            "email": "",
            "iss": MCP_JWT_ISSUER,
            "aud": "someone-else",
            "iat": now,
            "exp": now + timedelta(seconds=60),
        },
        hmac_key,
        algorithm=MCP_JWT_ALGORITHM,
    )
    with pytest.raises(McpJwtError, match="invalid"):
        verify_mcp_context_jwt(forged)


def test_verify_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    past = datetime.now(timezone.utc) - timedelta(hours=3)
    token = mint_mcp_context_jwt(user_id="user-1", now=past, ttl_seconds=60)
    with pytest.raises(McpJwtError, match="expired"):
        verify_mcp_context_jwt(token)


def test_verify_rejects_empty_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    with pytest.raises(McpJwtError, match="missing"):
        verify_mcp_context_jwt("")
