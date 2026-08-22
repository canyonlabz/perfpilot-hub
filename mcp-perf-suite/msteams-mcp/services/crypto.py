"""
Encryption utilities for credential storage.

Uses machine-specific key derivation to encrypt sensitive data at rest.
Compatible with the TypeScript implementation's scrypt parameters.
"""

import hashlib
import json
import os
import socket
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SALT = b"teams-mcp-credential-salt-v1"
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
IV_LENGTH = 16
CURRENT_VERSION = 1


def _derive_key() -> bytes:
    """Derive a 256-bit key from machine-specific values (hostname:username)."""
    machine_id = f"{socket.gethostname()}:{os.getlogin()}"
    return hashlib.scrypt(
        machine_id.encode("utf-8"),
        salt=SALT,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_LENGTH,
    )


def encrypt(plaintext: str) -> dict[str, Any]:
    """Encrypt a string value, returning a dict with iv, content, tag, version."""
    key = _derive_key()
    iv = os.urandom(IV_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

    ciphertext = ciphertext_with_tag[:-16]
    tag = ciphertext_with_tag[-16:]

    return {
        "iv": iv.hex(),
        "content": ciphertext.hex(),
        "tag": tag.hex(),
        "version": CURRENT_VERSION,
    }


def decrypt(data: dict[str, Any]) -> str:
    """Decrypt data that was produced by encrypt()."""
    version = data.get("version", 0)
    if version != CURRENT_VERSION:
        raise ValueError(f"Unsupported encryption version: {version}")

    key = _derive_key()
    iv = bytes.fromhex(data["iv"])
    ciphertext = bytes.fromhex(data["content"])
    tag = bytes.fromhex(data["tag"])

    aesgcm = AESGCM(key)
    plaintext_bytes = aesgcm.decrypt(iv, ciphertext + tag, None)
    return plaintext_bytes.decode("utf-8")


def is_encrypted(data: Any) -> bool:
    """Check if data looks like our encrypted format."""
    if not isinstance(data, dict):
        return False
    return (
        isinstance(data.get("iv"), str)
        and isinstance(data.get("content"), str)
        and isinstance(data.get("tag"), str)
        and isinstance(data.get("version"), int)
    )
