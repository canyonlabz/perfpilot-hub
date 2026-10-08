"""Web-UI equivalent of the orchestrator-side ``test_run_id`` resolver.

The A2A ingress path raises ``TaskInputRequiredSignal`` when the
resolver outcome is ``reject`` so the task row transitions to
``input_required`` (A2A §4.1.3 TASK_STATE_INPUT_REQUIRED). The Web-UI
path cannot raise that signal — the chat loop expects to run the LLM,
not short-circuit the dispatch. Instead this module translates the
``reject`` outcome into a *system hint* that nudges the orchestrator
LLM to ask the user naturally.

On top of the pure classifier, this module adds two Web-UI policies:

  1. **Implicit REUSE** — when the resolver returns ``reject`` and the
     thread has *exactly one* known ``test_run_id``, treat that
     candidate as the authoritative value for the turn. This matches
     the natural flow where a user runs one test and then asks follow-
     up questions without re-stating the ID.
  2. **Ask-the-user hint** — when the resolver returns ``reject`` and
     the thread has *multiple* known IDs, return a one-line
     ``system_hint`` that the caller injects into the LLM context
     so the orchestrator asks the user to pick one.

The ``WebUIResolveResult`` dataclass carries all three outputs the
caller needs: the resolved ID (if any), the full candidate list (so
``_caller_identity`` can expose it to ``delegate_to_specialist``), and
the optional system hint.

Dependency direction: imports from ``core/`` and sibling helpers only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from core.test_run_id import (
    OUTCOME_MINT,
    OUTCOME_REJECT,
    OUTCOME_REUSE,
    OUTCOME_SKIP_MINT,
    resolve_test_run_id_outcome,
)
from services.helpers.thread_prefetch import list_known_test_run_ids_for_thread

log = logging.getLogger(__name__)


# =============================================================================
# WebUIResolveResult
# =============================================================================


@dataclass(frozen=True)
class WebUIResolveResult:
    """Outcome of a Web-UI ``test_run_id`` resolution.

    Attributes:
        test_run_id: The authoritative ID for this turn (REUSE or MINT
            from the classifier, or an implicit-REUSE pick when the
            thread has exactly one known candidate). ``None`` when
            the Web UI has nothing to offer — the LLM proceeds without
            a known ID (e.g. SKIP_MINT, or REJECT with no candidates).
        known_test_run_ids: Distinct IDs from the thread's prior tasks,
            newest-first. Forwarded to ``_caller_identity`` so
            specialists called via ``delegate_to_specialist`` on
            comparison intents can see them.
        system_hint: A one-line system message the caller should inject
            into the LLM context so the orchestrator asks the user to
            pick one. ``None`` unless the resolver outcome was
            ``reject`` AND the thread has multiple known IDs.
        outcome: The underlying classifier outcome string (``reuse`` /
            ``mint`` / ``skip_mint`` / ``reject``) for logging and
            diagnostic purposes.
    """

    test_run_id: Optional[str]
    known_test_run_ids: list[str] = field(default_factory=list)
    system_hint: Optional[str] = None
    outcome: Optional[str] = None


# =============================================================================
# Hint composition
# =============================================================================


def _compose_ask_user_hint(candidates: Sequence[str]) -> str:
    """Build the one-line hint injected into the LLM context on multi-ID REJECT.

    The hint is phrased as a short directive the orchestrator LLM
    can incorporate naturally into its next reply — not as a template
    the user ever sees verbatim. Keeping it concise avoids crowding
    the system-message slot with runtime context.
    """
    pretty = ", ".join(f"`{c}`" for c in candidates[:10])
    return (
        f"Known `test_run_id`s on this thread: {pretty}. "
        "Ask the user which one applies if not stated."
    )


# =============================================================================
# Resolver
# =============================================================================


async def resolve_test_run_id_for_webui(
    *,
    user_text: Optional[str],
    thread_id: Optional[str],
    payload: Optional[dict] = None,
    parts: Optional[Sequence[Any]] = None,
) -> WebUIResolveResult:
    """Resolve ``test_run_id`` for a Web-UI chat turn.

    Collects prior candidates on the thread, runs the pure classifier,
    then applies the two Web-UI policies (implicit REUSE, ask-user
    hint). Never raises ``TaskInputRequiredSignal`` — the Web UI
    expects the LLM to drive the follow-up conversation.

    Args:
        user_text: The newest user prose from the browser.
        thread_id: The resolved conversation thread. ``None`` disables
            thread-candidate prefetch (the resolver still runs; only
            the implicit-REUSE and ask-user-hint policies are skipped).
        payload: Optional task payload. Rarely used on the Web-UI path
            today — included for parity with the A2A resolver so
            future hints like ``payload["test_run_id"]`` or
            ``payload["comparison"]`` propagate cleanly.
        parts: Optional parts array. Same rationale as ``payload``.

    Returns:
        A ``WebUIResolveResult`` describing the chosen ID, the known
        candidate list, and an optional ``system_hint``.
    """
    candidates = await list_known_test_run_ids_for_thread(thread_id)

    outcome = resolve_test_run_id_outcome(
        payload=payload,
        user_text=user_text,
        parts=parts,
        thread_test_run_ids=candidates or None,
    )

    # REUSE / MINT: the classifier already returned the authoritative
    # ID. Pass through unchanged — no system hint, candidates are still
    # returned so delegate_to_specialist can see them.
    if outcome.outcome in (OUTCOME_REUSE, OUTCOME_MINT):
        log.debug(
            "webui_resolve.pass_through",
            extra={
                "outcome": outcome.outcome,
                "test_run_id": outcome.id,
                "candidate_count": len(candidates),
            },
        )
        return WebUIResolveResult(
            test_run_id=outcome.id,
            known_test_run_ids=candidates,
            system_hint=None,
            outcome=outcome.outcome,
        )

    # SKIP_MINT: comparison-report path; the PerfReport MCP mints its
    # own comparison_id later. Return no ID and no hint; the LLM
    # proceeds and the specialist envelope captures the comparison ID
    # on the way back.
    if outcome.outcome == OUTCOME_SKIP_MINT:
        log.debug(
            "webui_resolve.skip_mint",
            extra={"candidate_count": len(candidates)},
        )
        return WebUIResolveResult(
            test_run_id=None,
            known_test_run_ids=candidates,
            system_hint=None,
            outcome=outcome.outcome,
        )

    # REJECT: the classifier found no signal. Apply the Web-UI
    # policies.
    if outcome.outcome == OUTCOME_REJECT:
        if len(candidates) == 1:
            # Policy 1 — implicit REUSE on a single-candidate thread.
            picked = candidates[0]
            log.info(
                "webui_resolve.implicit_reuse",
                extra={"test_run_id": picked},
            )
            return WebUIResolveResult(
                test_run_id=picked,
                known_test_run_ids=candidates,
                system_hint=None,
                outcome=outcome.outcome,
            )
        if len(candidates) > 1:
            # Policy 2 — multi-candidate thread; nudge the LLM to ask.
            hint = _compose_ask_user_hint(candidates)
            log.info(
                "webui_resolve.ask_user_hint",
                extra={"candidate_count": len(candidates)},
            )
            return WebUIResolveResult(
                test_run_id=None,
                known_test_run_ids=candidates,
                system_hint=hint,
                outcome=outcome.outcome,
            )
        # No candidates — fresh thread. The LLM proceeds; a later turn
        # may trigger a MINT once the user provides more context.
        log.debug("webui_resolve.reject_no_candidates")
        return WebUIResolveResult(
            test_run_id=None,
            known_test_run_ids=candidates,
            system_hint=None,
            outcome=outcome.outcome,
        )

    # Unknown outcome — defensive. Treat like a no-op reject.
    log.warning(
        "webui_resolve.unknown_outcome",
        extra={"outcome": outcome.outcome},
    )
    return WebUIResolveResult(
        test_run_id=outcome.id,
        known_test_run_ids=candidates,
        system_hint=None,
        outcome=outcome.outcome,
    )
