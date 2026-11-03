"""Unit tests for ``utils.secret_crypto``.

Uses a throwaway in-process master key. No database and no real user tokens.
"""

from __future__ import annotations

import base64
import os

import pytest

from utils.secret_crypto import (
    MASTER_KEY_ENV,
    EncryptedField,
    SecretCryptoError,
    binding_aad,
    decrypt_field,
    derive_jwt_hmac_key,
    encrypt_field,
    load_master_key,
)


def _install_test_key(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = os.urandom(32)
    monkeypatch.setenv(MASTER_KEY_ENV, base64.b64encode(key).decode("ascii"))
    return key


def test_load_master_key_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    with pytest.raises(SecretCryptoError, match=MASTER_KEY_ENV):
        load_master_key()


def test_load_master_key_rejects_wrong_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, base64.b64encode(b"too-short").decode("ascii"))
    with pytest.raises(SecretCryptoError, match="32 bytes"):
        load_master_key()


def test_load_master_key_rejects_non_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, "not-valid-base64???")
    with pytest.raises(SecretCryptoError, match="base64"):
        load_master_key()


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    aad = binding_aad("user-1", "github", "work-pat", "token")
    encrypted = encrypt_field("dummy-value", aad=aad)
    assert encrypted.key_version == 1
    assert encrypted.nonce
    assert encrypted.ciphertext
    assert decrypt_field(encrypted, aad=aad) == "dummy-value"


def test_decrypt_fails_on_wrong_aad(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    encrypted = encrypt_field(
        "dummy-value",
        aad=binding_aad("user-1", "github", "work-pat", "token"),
    )
    with pytest.raises(SecretCryptoError, match="Decrypt failed"):
        decrypt_field(
            encrypted,
            aad=binding_aad("user-2", "github", "work-pat", "token"),
        )


def test_decrypt_fails_on_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    aad = binding_aad("user-1", "github", "work-pat", "token")
    encrypted = encrypt_field("dummy-value", aad=aad)
    other = os.urandom(32)
    monkeypatch.setenv(MASTER_KEY_ENV, base64.b64encode(other).decode("ascii"))
    with pytest.raises(SecretCryptoError, match="Decrypt failed"):
        decrypt_field(encrypted, aad=aad)


def test_decrypt_does_not_echo_plaintext_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_test_key(monkeypatch)
    aad = binding_aad("user-1", "github", "work-pat", "token")
    encrypted = encrypt_field("super-secret-dummy", aad=aad)
    tampered = EncryptedField(
        ciphertext=encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
        nonce=encrypted.nonce,
        key_version=encrypted.key_version,
    )
    with pytest.raises(SecretCryptoError) as exc_info:
        decrypt_field(tampered, aad=aad)
    assert "super-secret-dummy" not in str(exc_info.value)


def test_hkdf_jwt_key_is_32_bytes_and_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_test_key(monkeypatch)
    first = derive_jwt_hmac_key()
    second = derive_jwt_hmac_key()
    assert len(first) == 32
    assert first == second
    assert first != load_master_key()
