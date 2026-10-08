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

A2A v1 note
-----------
``resolve_test_run_id_outcome`` is the authoritative three-outcome
classifier used at ingress. It returns a ``ResolveResult`` with one of
four outcome values aligned to the A2A §4.1.3 TaskState lifecycle:

  * ``"reuse"``     — an existing ``test_run_id`` was found.
  * ``"mint"``      — a fresh UTC timestamp was minted (script-creation).
  * ``"skip_mint"`` — comparison-report requests, where the PerfReport
                     MCP mints its own ``comparison_id``; the framework
                     captures that value post-hoc via
                     ``task_store.set_test_run_id``.
  * ``"reject"``    — no ID and no classification signal; the caller
                     must transition the task to ``input_required``
                     (A2A TASK_STATE_INPUT_REQUIRED) and surface the
                     ``reason_code`` to the client.

``ensure_test_run_id_for_inbound`` and ``resolve_or_mint_test_run_id``
remain available as backward-compat shims that return ``str | None`` so
existing call sites continue to work; the outcome-aware callers (added
in later phases) use ``resolve_test_run_id_outcome`` directly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
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

# A2A v1 taxonomy: parts[1].metadata.source_type values that signal a
# script-creation request. Anything outside this set either reuses an
# existing ID (post-test flows) or triggers the reject branch.
_SCRIPT_CREATION_SOURCE_TYPES = frozenset({
    "playwright",
    "har",
    "openapi",
})

# ResolveResult.outcome values. These are stable string tags so downstream
# code can switch on them without importing the dataclass.
OUTCOME_REUSE = "reuse"
OUTCOME_MINT = "mint"
OUTCOME_SKIP_MINT = "skip_mint"
OUTCOME_REJECT = "reject"

# Canonical reasonCode for the main reject branch, emitted on the SSE
# TaskStatusUpdateEvent so clients can switch UX on a machine-readable code.
REASON_MISSING_TEST_RUN_ID = "missing_test_run_id"


@dataclass(frozen=True)
class ResolveResult:
    """Three-outcome classifier result from the ingress resolver.

    Attributes:
        id: The resolved or minted ``test_run_id``. ``None`` for
            ``skip_mint`` (comparison report) and ``reject`` outcomes.
        outcome: One of ``"reuse"``, ``"mint"``, ``"skip_mint"``,
            ``"reject"``. See module docstring for A2A mapping.
        reason_code: Short machine-readable code populated on ``reject``
            outcomes (e.g. ``"missing_test_run_id"``). ``None`` otherwise.
        reason_data: Optional supplementary payload for the client — for
            example ``{"candidate_test_run_ids": [...]}`` collected from
            the thread history when the resolver rejects. ``None`` when
            no supplementary data is available.
    """

    id: Optional[str]
    outcome: str
    reason_code: Optional[str] = None
    reason_data: Optional[dict] = None


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


def extract_source_type(payload: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return ``parts[1].metadata.source_type`` lowercased, or payload fallback.

    The A2A v1 server contract puts the script-source classifier in
    ``parts[1].metadata.source_type`` (see server-side data contract
    section 2.2). Web-UI delegations that bypass the parts envelope may
    carry the same value at ``payload["source_type"]``; both are
    normalized to lowercase with surrounding whitespace stripped.

    Returns None when the field is missing, empty, or not a string.
    """
    if not isinstance(payload, Mapping):
        return None

    parts = payload.get("parts")
    if isinstance(parts, Sequence) and not isinstance(parts, (str, bytes)) and len(parts) > 1:
        part1 = parts[1]
        if isinstance(part1, Mapping):
            meta = part1.get("metadata")
            if isinstance(meta, Mapping):
                st = meta.get("source_type")
                if isinstance(st, str) and st.strip():
                    return st.strip().lower()

    st = payload.get("source_type")
    if isinstance(st, str) and st.strip():
        return st.strip().lower()

    return None


def is_comparison_report_request(payload: Optional[Mapping[str, Any]]) -> bool:
    """Return True when the inbound request is a comparison-report build.

    Signals (A2A v1 shape):
      * ``parts[1].metadata.task_type == "comparison_report"``
      * ``parts[1].metadata.test_run_ids`` is a non-empty list

    Web-UI delegation fallback shape:
      * ``payload["comparison"]["test_run_ids"]`` is a non-empty list

    Comparison-report requests are a classification exception: the
    resolver returns ``skip_mint`` for them because the PerfReport MCP
    mints its own ``comparison_id`` and the framework captures that
    value from the MCP tool result (via ``task_store.set_test_run_id``)
    rather than minting one up front.
    """
    if not isinstance(payload, Mapping):
        return False

    parts = payload.get("parts")
    if isinstance(parts, Sequence) and not isinstance(parts, (str, bytes)) and len(parts) > 1:
        part1 = parts[1]
        if isinstance(part1, Mapping):
            meta = part1.get("metadata")
            if isinstance(meta, Mapping):
                task_type = meta.get("task_type")
                if (
                    isinstance(task_type, str)
                    and task_type.strip().lower() == "comparison_report"
                ):
                    ids = meta.get("test_run_ids")
                    if (
                        isinstance(ids, Sequence)
                        and not isinstance(ids, (str, bytes))
                        and len(ids) > 0
                    ):
                        return True

    comparison = payload.get("comparison")
    if isinstance(comparison, Mapping):
        ids = comparison.get("test_run_ids")
        if (
            isinstance(ids, Sequence)
            and not isinstance(ids, (str, bytes))
            and len(ids) > 0
        ):
            return True

    return False


def resolve_test_run_id_outcome(
    *,
    payload: Optional[Mapping[str, Any]] = None,
    user_text: Optional[str] = None,
    parts: Optional[Sequence[Any]] = None,
    thread_test_run_ids: Optional[Sequence[str]] = None,
) -> ResolveResult:
    """Classify an inbound request into one of four outcomes.

    Outcome order:

      1. ``reuse``      — an existing ``test_run_id`` is already present
                          on the payload or in the user's prose.
      2. ``skip_mint``  — comparison-report request (A2A ``task_type``
                          or Web-UI ``comparison.test_run_ids``). The
                          framework defers to the PerfReport MCP's own
                          ``comparison_id`` instead of minting.
      3. ``mint``       — ``parts[1].metadata.source_type`` is in the
                          script-creation set, or the Web-UI prose
                          heuristic matches a script-creation intent.
                          A fresh UTC timestamp is written back to
                          ``payload["test_run_id"]`` for downstream use.
      4. ``reject``     — none of the above; the caller must transition
                          the task to ``input_required`` and surface
                          ``reason_code="missing_test_run_id"`` to the
                          client (A2A §4.1.3 TASK_STATE_INPUT_REQUIRED).

    Args:
        payload: The inbound task payload. When present, resolved and
            minted IDs are written back to ``payload["test_run_id"]``
            so the orchestrator sees the authoritative value.
        user_text: The user's free-form prose message. Used for
            labeled-ID extraction and the Web-UI script-creation
            heuristic fallback.
        parts: The A2A ``parts[]`` array. Passed through to the
            script-creation heuristic for media-type signals; the
            metadata-driven ``source_type`` classification reads the
            parts out of ``payload["parts"]`` directly.
        thread_test_run_ids: Known ``test_run_id`` values from prior
            tasks on the same conversation thread. When the outcome is
            ``reject``, non-empty values are placed in
            ``reason_data["candidate_test_run_ids"]`` so the client UX
            can offer the user a dropdown instead of a free-form prompt.

    Returns:
        A ``ResolveResult`` carrying the outcome, the resolved or
        minted ID (when applicable), and any ``reason_code`` /
        ``reason_data`` for a reject outcome.
    """
    # 1. REUSE — payload fields or inline ID in user prose.
    existing = resolve_test_run_id(payload=payload)
    if not existing:
        existing = extract_test_run_id_from_text(user_text)

    if existing:
        if isinstance(payload, dict):
            payload["test_run_id"] = existing
        log.debug(
            "test_run_id.resolve.reuse",
            extra={"test_run_id": existing},
        )
        return ResolveResult(id=existing, outcome=OUTCOME_REUSE)

    # 2. SKIP_MINT — comparison report; PerfReport MCP mints its own ID.
    if is_comparison_report_request(payload):
        log.info(
            "test_run_id.resolve.skip_mint",
            extra={"classification": "comparison_report"},
        )
        return ResolveResult(id=None, outcome=OUTCOME_SKIP_MINT)

    # 3. MINT — A2A parts[1].metadata.source_type drives the primary path.
    source_type = extract_source_type(payload)
    if source_type and source_type in _SCRIPT_CREATION_SOURCE_TYPES:
        minted = mint_test_run_id()
        if isinstance(payload, dict):
            payload["test_run_id"] = minted
        log.info(
            "test_run_id.resolve.mint",
            extra={
                "test_run_id": minted,
                "source_type": source_type,
                "classification_source": "parts1_metadata",
            },
        )
        return ResolveResult(id=minted, outcome=OUTCOME_MINT)

    # 3b. MINT — Web-UI prose heuristic fallback when parts metadata is absent.
    if is_script_creation_request(
        user_text=user_text, parts=parts, payload=payload,
    ):
        minted = mint_test_run_id()
        if isinstance(payload, dict):
            payload["test_run_id"] = minted
        log.info(
            "test_run_id.resolve.mint",
            extra={
                "test_run_id": minted,
                "classification_source": "prose_heuristic",
            },
        )
        return ResolveResult(id=minted, outcome=OUTCOME_MINT)

    # 4. REJECT — surface the question + reasonCode to the client.
    reason_data: dict[str, Any] = {}
    if thread_test_run_ids:
        unique = list(dict.fromkeys(
            t.strip()
            for t in thread_test_run_ids
            if isinstance(t, str) and t.strip()
        ))
        if unique:
            reason_data["candidate_test_run_ids"] = unique

    log.info(
        "test_run_id.resolve.reject",
        extra={
            "reason_code": REASON_MISSING_TEST_RUN_ID,
            "candidate_count": len(reason_data.get("candidate_test_run_ids", [])),
        },
    )
    return ResolveResult(
        id=None,
        outcome=OUTCOME_REJECT,
        reason_code=REASON_MISSING_TEST_RUN_ID,
        reason_data=reason_data or None,
    )


def ensure_test_run_id_for_inbound(
    *,
    payload: Optional[dict] = None,
    user_text: Optional[str] = None,
    parts: Optional[Sequence[Any]] = None,
) -> Optional[str]:
    """Shared A2A + Web UI ingress: reuse or mint a ``test_run_id``.

    Backward-compat wrapper over ``resolve_test_run_id_outcome``. Returns
    a bare ``str | None`` for callers that pre-date the three-outcome
    classifier. ``reject`` and ``skip_mint`` outcomes both collapse to
    ``None`` on return; outcome-aware callers (orchestrator reject path,
    comparison-report delegation) use ``resolve_test_run_id_outcome``
    directly to distinguish the two.

    Rules:

      1. If an ID is already present (payload / metadata / labeled text),
         reuse it (post-test or continuing an existing run).
      2. Else if this is a pre-script-creation request, mint a UTC timestamp.
      3. Else leave unset (not every chat turn needs an artifact folder).

    Returns:
        The authoritative ``test_run_id``, or None when not applicable.
    """
    result = resolve_test_run_id_outcome(
        payload=payload,
        user_text=user_text,
        parts=parts,
    )
    return result.id
