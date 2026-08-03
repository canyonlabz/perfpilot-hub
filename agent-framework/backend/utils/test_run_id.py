"""Shared test_run_id resolve-or-mint helpers.

Used by both the A2A and AG-UI (Web UI) ingress paths so script-creation
flows get a unique UTC timestamp ID when the caller does not supply one,
while existing-run / post-test flows reuse ``metadata.test_run_id``,
payload, task column, or an ID mentioned in the user message.

Classification when no ID is present:

  * **Pre-script creation** (JMeter / Playwright / HAR / Swagger / etc.)
    → mint ``YYYY-MM-DD-HH-MM-SS`` (UTC).
  * **Post-test execution** (or any non-script request without an ID)
    → leave unset; downstream specialists that need an ID receive one
    from the caller or from a later explicit handoff.

Mint format: ``YYYY-MM-DD-HH-MM-SS`` (UTC).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

log = logging.getLogger(__name__)

# Timestamp-shaped PerfPilot artifact-folder keys.
_TIMESTAMP_ID_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\b")

# Explicit "test_run_id: <value>" / "run_id=<value>" mentions in prose.
_LABELED_ID_RE = re.compile(
    r"\b(?:test_run_id|run_id)\s*[:=]\s*([A-Za-z0-9._\-]+)",
    re.IGNORECASE,
)

# Heuristic phrases that indicate a pre-script-creation request.
_SCRIPT_CREATION_HINTS = (
    "jmeter",
    "jmx",
    "playwright",
    "browser automation",
    "network capture",
    "har",
    "swagger",
    "openapi",
    "test spec",
    "test-spec",
    "create a script",
    "create script",
    "generate a script",
    "generate script",
    "generate jmx",
    "create jmx",
    "script creation",
    "correlation",
    "blazedemo",
)

# A2A Part media types that imply inbound test-spec / script-creation content.
_SCRIPT_CREATION_MEDIA_TYPES = frozenset({
    "text/markdown",
    "application/vnd.azure.devops.testcase+json",
})


def mint_test_run_id() -> str:
    """Mint a new ``test_run_id`` using the UTC wall clock."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S")


def _valid_id(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def resolve_test_run_id(
    *candidates: Any,
    payload: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Return the first valid ``test_run_id`` from candidates and payload.

    Resolution order:
      1. Explicit ``candidates`` (e.g. task column, ContextVar, identity)
      2. ``payload["test_run_id"]``
      3. ``payload["metadata"]["test_run_id"]``
    """
    for candidate in candidates:
        resolved = _valid_id(candidate)
        if resolved:
            return resolved

    if not isinstance(payload, Mapping):
        return None

    resolved = _valid_id(payload.get("test_run_id"))
    if resolved:
        return resolved

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        resolved = _valid_id(metadata.get("test_run_id"))
        if resolved:
            return resolved

    return None


def resolve_or_mint_test_run_id(
    *candidates: Any,
    payload: Optional[dict] = None,
    mint_if_missing: bool = True,
) -> Optional[str]:
    """Resolve an existing ``test_run_id`` or mint one when allowed.

    When a value is resolved or minted and ``payload`` is a mutable dict,
    the value is written back to ``payload["test_run_id"]`` so downstream
    prompt composition and child-task creation see the authoritative ID.

    Args:
        *candidates: Preferred sources (task column, ContextVar, identity).
        payload: Optional task/delegation payload to read from and update.
        mint_if_missing: When True and no ID is found, mint a UTC timestamp.
            When False, return None without minting (e.g. non-script flows).

    Returns:
        The resolved or minted ID, or None when missing and minting is off.
    """
    resolved = resolve_test_run_id(*candidates, payload=payload)
    if resolved:
        if isinstance(payload, dict):
            payload["test_run_id"] = resolved
        return resolved

    if not mint_if_missing:
        return None

    minted = mint_test_run_id()
    if isinstance(payload, dict):
        payload["test_run_id"] = minted
    log.info("resolve_or_mint_test_run_id: no test_run_id provided; minted %s", minted)
    return minted


def extract_test_run_id_from_text(text: Optional[str]) -> Optional[str]:
    """Extract a caller-supplied ``test_run_id`` from free-form user text.

    Prefers an explicitly labeled ``test_run_id:`` / ``run_id:`` value, then
    falls back to a ``YYYY-MM-DD-HH-MM-SS`` timestamp token.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    labeled = _LABELED_ID_RE.search(text)
    if labeled:
        return labeled.group(1).strip()

    stamped = _TIMESTAMP_ID_RE.search(text)
    if stamped:
        return stamped.group(1)

    return None


def is_script_creation_request(
    *,
    user_text: Optional[str] = None,
    parts: Optional[Sequence[Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Return True when the inbound request is a pre-script-creation event.

    Signals (any one is enough):

      * A2A ``parts[]`` with test-spec media types (markdown / ADO test case)
      * Payload already carries ``test_spec_file`` (normalized spec path)
      * User prose mentions JMeter / Playwright / HAR / Swagger / etc.
    """
    if isinstance(payload, Mapping):
        if _valid_id(payload.get("test_spec_file")):
            return True
        nested_parts = payload.get("parts")
        if parts is None and isinstance(nested_parts, list):
            parts = nested_parts

    if isinstance(parts, Sequence):
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            media = part.get("mediaType") or part.get("media_type")
            if isinstance(media, str) and media.strip().lower() in _SCRIPT_CREATION_MEDIA_TYPES:
                return True

    if isinstance(user_text, str) and user_text.strip():
        lowered = user_text.lower()
        if any(hint in lowered for hint in _SCRIPT_CREATION_HINTS):
            return True

    return False


def ensure_test_run_id_for_inbound(
    *,
    payload: Optional[dict] = None,
    user_text: Optional[str] = None,
    parts: Optional[Sequence[Any]] = None,
) -> Optional[str]:
    """Shared A2A + Web UI ingress: reuse or mint a ``test_run_id``.

    Rules:

      1. If an ID is already present (payload / metadata / labeled text),
         reuse it (post-test or continuing an existing run).
      2. Else if this is a pre-script-creation request, mint a UTC timestamp.
      3. Else leave unset (not every chat turn needs an artifact folder).

    Returns:
        The authoritative ``test_run_id``, or None when not applicable.
    """
    existing = resolve_test_run_id(payload=payload)
    if not existing:
        existing = extract_test_run_id_from_text(user_text)

    if existing:
        if isinstance(payload, dict):
            payload["test_run_id"] = existing
        log.debug("ensure_test_run_id_for_inbound: reusing existing id %s", existing)
        return existing

    if is_script_creation_request(
        user_text=user_text, parts=parts, payload=payload,
    ):
        minted = mint_test_run_id()
        if isinstance(payload, dict):
            payload["test_run_id"] = minted
        log.info(
            "ensure_test_run_id_for_inbound: script-creation with no id; minted %s",
            minted,
        )
        return minted

    return None
