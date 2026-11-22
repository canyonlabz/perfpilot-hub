"""Token estimation and context-window compaction helpers.

Provides approximate token counts for AG2 message lists (tiktoken when
available, character heuristic otherwise) and a compaction routine that
replaces older tool-result bodies with short references so multi-turn
loops stay under model context limits.

Leaf helper: no imports from ``services.task_executor`` or
``services.specialist_agents``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

_tiktoken_encoder: Any = None
_tiktoken_available: Optional[bool] = None


def get_tiktoken_encoder() -> Any:
    """Lazy-load tiktoken encoder. Returns None if tiktoken is unavailable."""
    global _tiktoken_encoder, _tiktoken_available
    if _tiktoken_available is False:
        return None
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.encoding_for_model("gpt-4o")
        _tiktoken_available = True
        return _tiktoken_encoder
    except (ImportError, Exception):
        _tiktoken_available = False
        log.info("tiktoken not available; using character-based token estimation")
        return None


def estimate_token_count(messages: list[dict]) -> int:
    """Estimate total tokens in a message list.

    Uses tiktoken for accuracy when available, falls back to
    character_count / 4 as a rough heuristic.
    """
    enc = get_tiktoken_encoder()
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, default=str)
        if enc is not None:
            total += len(enc.encode(content))
        else:
            total += len(content) // 4
        # Overhead per message (role, name, structural tokens)
        total += 4
    return total


def compact_tool_results(
    messages: list[dict],
    *,
    preserve_recent: int = 5,
) -> tuple[list[dict], int]:
    """Replace old tool result messages with compact references.

    Preserves:
    - The most recent `preserve_recent` tool results (chronological order)
    - Any tool result that appears after the last `browser_navigate` call
      (current page session safety — the model needs DOM state context)
    - All non-tool messages (system, user, assistant)

    Args:
        messages: The conversation messages list (mutated in place).
        preserve_recent: Number of most-recent tool results to keep intact.

    Returns:
        Tuple of (compacted messages list, number of messages compacted).
    """
    tool_indices = [
        i for i, m in enumerate(messages) if m.get("role") == "tool"
    ]
    if not tool_indices:
        return messages, 0

    # Find the last browser_navigate call to establish page session boundary
    last_navigate_idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list):
            for tc in m["tool_calls"]:
                if isinstance(tc, dict):
                    fn = tc.get("function", {}).get("name", "")
                    if fn == "browser_navigate":
                        last_navigate_idx = i

    # Determine which tool results are eligible for compaction
    protected_indices = set(tool_indices[-preserve_recent:])

    compacted_count = 0
    for idx in tool_indices:
        if idx in protected_indices:
            continue
        if idx > last_navigate_idx and last_navigate_idx >= 0:
            continue

        msg = messages[idx]
        content = msg.get("content", "")
        # Skip if already compacted
        if isinstance(content, str) and content.startswith("[Compacted:"):
            continue

        tool_call_id = msg.get("tool_call_id", "unknown")
        compact_ref = (
            f"[Compacted: tool_call_id={tool_call_id}] "
            f"Result stored in tool_call_traces. "
            f"Original size: ~{len(content)} chars."
        )
        messages[idx] = {
            **msg,
            "content": compact_ref,
        }
        compacted_count += 1

    return messages, compacted_count


# =============================================================================
# Context budget resolver
# =============================================================================
# The compaction routine above answers "how do I shrink history that is already
# too big?" This resolver answers "how big is this agent allowed to get?" —
# turning the ``context_management`` config block into a single resolved budget.
# It lives here, next to ``compact_tool_results``, because the two compose: the
# native path feeds ``compaction_preserve_recent`` into ``compact_tool_results``
# and ``effective_prompt_budget`` into the native ``TokenLimiter``.
#
# Config source (single ``context_management:`` block, model-driven):
#   * GLOBAL default in ``config/agents.yaml`` (``agents.example.yaml`` fallback)
#   * optional PER-AGENT override in ``agents/<name>/config.yaml`` — per-agent
#     keys win, omitted keys inherit the global default. This lets different
#     agents run different-sized models.
#
# Keys (all tokens unless noted):
#   context_limit               — the MODEL's context window (set per model).
#   reserved_completion_tokens  — buffer held back for the in-flight completion.
#   overhead_reserve_tokens     — buffer held back for structural/tool overhead.
#   compaction_threshold        — utilization fraction (0, 1] to compact/warn at.
#   compaction_preserve_recent  — most-recent tool results kept intact.
# Derived:
#   effective_prompt_budget = context_limit
#                             - reserved_completion_tokens
#                             - overhead_reserve_tokens
# Reserving a completion + overhead buffer means the native ``TokenLimiter`` cap
# (and compaction) engages before the model's real window is actually full.
#
# ``context_limit`` is model-specific; set it to the window of the model the
# agent uses. Known windows (max context / max output tokens), verified against
# vendor docs:
#   * openai    gpt-5-mini         :   400,000 /  128,000
#   * anthropic claude-haiku-5-5   : 1,000,000 /  128,000
#
# The ``_FALLBACK_*`` values below are a conservative last resort used ONLY if a
# key is absent from BOTH the global and per-agent blocks (which should not
# happen once ``config/agents.yaml`` carries ``context_management:``). Every
# fallback logs a WARNING (once per key per process) so a degraded config is
# visible rather than silently hard-coded.

_CONTEXT_MANAGEMENT_KEY = "context_management"

_FALLBACK_CONTEXT_LIMIT = 128000
_FALLBACK_RESERVED_COMPLETION_TOKENS = 8000
_FALLBACK_OVERHEAD_RESERVE_TOKENS = 12000
_FALLBACK_COMPACTION_THRESHOLD = 0.80
_FALLBACK_COMPACTION_PRESERVE_RECENT = 5

_warned_budget_keys: set[str] = set()


def _warn_budget_once(key: str, message: str) -> None:
    """Emit ``log.warning(message)`` at most once per process per ``key``."""
    if key in _warned_budget_keys:
        return
    _warned_budget_keys.add(key)
    log.warning(message)


def _reset_budget_warning_dedup() -> None:
    """Test-only: clear the dedup set so warning-emit tests can re-fire."""
    _warned_budget_keys.clear()


@dataclass(frozen=True)
class ContextBudget:
    """Resolved context-window budget for one agent.

    Attributes:
        context_limit: The model's context window (total tokens). Model-driven
            — e.g. 400000 for ``gpt-5-mini``, 1000000 for ``claude-haiku-5-5``.
        reserved_completion_tokens: Tokens held back for the in-flight
            completion so the budget engages before the window is full.
        overhead_reserve_tokens: Tokens held back for structural/tool overhead.
        compaction_threshold: Utilization fraction (0, 1] at which history
            compaction / token warnings should fire.
        compaction_preserve_recent: Number of most-recent tool results
            :func:`compact_tool_results` keeps intact.
        effective_prompt_budget: Hard per-model-call prompt cap a native
            ``TokenLimiter`` enforces, derived as ``context_limit -
            reserved_completion_tokens - overhead_reserve_tokens`` (clamped to
            at least 1).
    """

    context_limit: int
    reserved_completion_tokens: int
    overhead_reserve_tokens: int
    compaction_threshold: float
    compaction_preserve_recent: int
    effective_prompt_budget: int


def _resolve_positive_int(merged: dict, key: str, fallback: int) -> int:
    """Resolve a positive-int key from the merged block, warning on fallback."""
    if key not in merged:
        _warn_budget_once(
            key,
            f"context_management: missing '{key}'; using fallback {fallback}. "
            f"Set it under 'context_management:' in config/agents.yaml.",
        )
        return fallback
    try:
        out = int(merged[key])
    except (TypeError, ValueError):
        _warn_budget_once(
            key,
            f"context_management: '{key}' is malformed (got {merged[key]!r}); "
            f"using fallback {fallback}.",
        )
        return fallback
    if out <= 0:
        _warn_budget_once(
            key,
            f"context_management: '{key}'={out} must be > 0; using fallback "
            f"{fallback}.",
        )
        return fallback
    return out


def _resolve_non_negative_int(merged: dict, key: str, fallback: int) -> int:
    """Resolve a non-negative-int key from the merged block, warning on fallback."""
    if key not in merged:
        _warn_budget_once(
            key,
            f"context_management: missing '{key}'; using fallback {fallback}. "
            f"Set it under 'context_management:' in config/agents.yaml.",
        )
        return fallback
    try:
        out = int(merged[key])
    except (TypeError, ValueError):
        _warn_budget_once(
            key,
            f"context_management: '{key}' is malformed (got {merged[key]!r}); "
            f"using fallback {fallback}.",
        )
        return fallback
    if out < 0:
        _warn_budget_once(
            key,
            f"context_management: '{key}'={out} must be >= 0; using fallback "
            f"{fallback}.",
        )
        return fallback
    return out


def _resolve_fraction(merged: dict, key: str, fallback: float) -> float:
    """Resolve a (0, 1] fraction key from the merged block, warning on fallback."""
    if key not in merged:
        _warn_budget_once(
            key,
            f"context_management: missing '{key}'; using fallback {fallback}. "
            f"Set it under 'context_management:' in config/agents.yaml.",
        )
        return fallback
    try:
        out = float(merged[key])
    except (TypeError, ValueError):
        _warn_budget_once(
            key,
            f"context_management: '{key}' is malformed (got {merged[key]!r}); "
            f"using fallback {fallback}.",
        )
        return fallback
    if not (0.0 < out <= 1.0):
        _warn_budget_once(
            key,
            f"context_management: '{key}'={out} must be in (0, 1]; using "
            f"fallback {fallback}.",
        )
        return fallback
    return out


def resolve_context_budget(
    agent_cm: Optional[dict] = None,
    *,
    global_cm: Optional[dict] = None,
) -> ContextBudget:
    """Resolve a :class:`ContextBudget` from ``context_management`` blocks.

    Merges the global default block with the agent's optional override; keys
    present in the per-agent block win, omitted keys inherit the global value.
    A key absent from both (or malformed / out of range) falls back to a
    conservative ``_FALLBACK_*`` value and logs a WARNING once per key — the
    result is always a usable budget rather than raising on a bad config.

    Args:
        agent_cm: The agent's ``context_management`` dict (per-agent override),
            or ``None`` / non-dict when the agent declares no override.
        global_cm: The global ``context_management`` dict from
            ``config/agents.yaml``, or ``None`` / non-dict when absent.

    Returns:
        A :class:`ContextBudget`.
    """
    g = global_cm if isinstance(global_cm, dict) else {}
    a = agent_cm if isinstance(agent_cm, dict) else {}
    merged = {**g, **a}

    context_limit = _resolve_positive_int(
        merged, "context_limit", _FALLBACK_CONTEXT_LIMIT
    )
    reserved_completion_tokens = _resolve_non_negative_int(
        merged, "reserved_completion_tokens", _FALLBACK_RESERVED_COMPLETION_TOKENS
    )
    overhead_reserve_tokens = _resolve_non_negative_int(
        merged, "overhead_reserve_tokens", _FALLBACK_OVERHEAD_RESERVE_TOKENS
    )
    compaction_threshold = _resolve_fraction(
        merged, "compaction_threshold", _FALLBACK_COMPACTION_THRESHOLD
    )
    compaction_preserve_recent = _resolve_non_negative_int(
        merged, "compaction_preserve_recent", _FALLBACK_COMPACTION_PRESERVE_RECENT
    )

    effective_prompt_budget = (
        context_limit - reserved_completion_tokens - overhead_reserve_tokens
    )
    if effective_prompt_budget < 1:
        log.warning(
            "context_management: reserves (%d + %d) meet or exceed "
            "context_limit (%d); clamping effective_prompt_budget to 1.",
            reserved_completion_tokens, overhead_reserve_tokens, context_limit,
        )
        effective_prompt_budget = 1

    return ContextBudget(
        context_limit=context_limit,
        reserved_completion_tokens=reserved_completion_tokens,
        overhead_reserve_tokens=overhead_reserve_tokens,
        compaction_threshold=compaction_threshold,
        compaction_preserve_recent=compaction_preserve_recent,
        effective_prompt_budget=effective_prompt_budget,
    )


def resolve_context_budget_for_agent(agent_name: str) -> ContextBudget:
    """Load ``agent_name``'s budget from global + per-agent config.

    Reads the global ``context_management`` from ``config/agents.yaml`` and the
    agent's optional override from ``agents/<name>/config.yaml``, then resolves
    them via :func:`resolve_context_budget`. ``config_loader`` is imported
    lazily so this module stays a leaf helper.
    """
    from utils import config_loader

    global_cfg = config_loader.load_global_config()
    agent_cfg = config_loader.load_agent_config(agent_name)
    global_cm = (
        global_cfg.get(_CONTEXT_MANAGEMENT_KEY)
        if isinstance(global_cfg, dict) else None
    )
    agent_cm = (
        agent_cfg.get(_CONTEXT_MANAGEMENT_KEY)
        if isinstance(agent_cfg, dict) else None
    )
    return resolve_context_budget(agent_cm, global_cm=global_cm)
