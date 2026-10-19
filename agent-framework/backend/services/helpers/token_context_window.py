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
