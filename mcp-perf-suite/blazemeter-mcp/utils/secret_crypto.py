"""AES-256-GCM helpers for Web-UI user secret fields.

The AG-UI process encrypts field values before they are written to
``user_secret_fields``. The same algorithm is used later by MCP servers
to decrypt. The browser never receives ciphertext or plaintext.

The master key is ``PERFPILOT_SECRETS_MASTER_KEY``: 32 raw bytes,
base64-encoded. The operator must set this value; secrets storage does
not work without it. The key is never logged.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASTER_KEY_ENV = "PERFPILOT_SECRETS_MASTER_KEY"
MASTER_KEY_LENGTH = 32
GCM_NONCE_LENGTH = 12
DEFAULT_KEY_VERSION = 1
_HKDF_SALT = b"perfpilot-secrets-v1"
_HKDF_INFO_JWT = b"perfpilot-mcp-jwt"


class SecretCryptoError(Exception):
    """Master-key or encrypt/decrypt failure. Messages must not include secrets."""


@dataclass(frozen=True)
class EncryptedField:
    """One encrypted ``user_secret_fields`` value."""

    ciphertext: bytes
    nonce: bytes
    key_version: int = DEFAULT_KEY_VERSION


def load_master_key() -> bytes:
    """Return the 32-byte AES key from ``PERFPILOT_SECRETS_MASTER_KEY``.

    Raises:
        SecretCryptoError: if the variable is missing, not valid base64,
            or not exactly 32 decoded bytes.
    """
    raw = os.environ.get(MASTER_KEY_ENV, "").strip()
    if not raw:
        raise SecretCryptoError(
            f"{MASTER_KEY_ENV} is not set. An operator must create a "
            "32-byte key (base64) before user secrets can be stored."
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretCryptoError(
            f"{MASTER_KEY_ENV} is not valid base64."
        ) from exc
    if len(key) != MASTER_KEY_LENGTH:
        raise SecretCryptoError(
            f"{MASTER_KEY_ENV} must decode to {MASTER_KEY_LENGTH} bytes."
        )
    return key


def derive_jwt_hmac_key() -> bytes:
    """Derive the HS256 key from the master key via HKDF-SHA256.

    Operators set one secret. JWT signing material is derived and is
    never written to the environment as a second value.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=MASTER_KEY_LENGTH,
        salt=_HKDF_SALT,
        info=_HKDF_INFO_JWT,
    ).derive(load_master_key())


def binding_aad(
    user_id: str,
    component: str,
    name: str,
    field_name: str,
) -> bytes:
    """Associated data that binds ciphertext to one secret field row."""
    return f"{user_id}|{component}|{name}|{field_name}".encode("utf-8")


def encrypt_field(
    plaintext: str,
    *,
    aad: bytes,
    key_version: int = DEFAULT_KEY_VERSION,
) -> EncryptedField:
    """Encrypt UTF-8 plaintext with AES-256-GCM."""
    if not isinstance(plaintext, str):
        raise SecretCryptoError("Plaintext must be a string.")
    key = load_master_key()
    nonce = os.urandom(GCM_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return EncryptedField(
        ciphertext=ciphertext,
        nonce=nonce,
        key_version=key_version,
    )


def decrypt_field(field: EncryptedField, *, aad: bytes) -> str:
    """Decrypt an ``EncryptedField`` to UTF-8 text.

    Raises:
        SecretCryptoError: on authentication failure or a missing key.
            The exception does not include plaintext or key material.
    """
    key = load_master_key()
    try:
        raw = AESGCM(key).decrypt(field.nonce, field.ciphertext, aad)
    except InvalidTag as exc:
        raise SecretCryptoError(
            "Decrypt failed. The ciphertext, nonce, key, or binding data "
            "does not match."
        ) from exc
    except ValueError as exc:
        raise SecretCryptoError("Decrypt failed. The stored blob is invalid.") from exc
    return raw.decode("utf-8")
