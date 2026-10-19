"""A2A v1 request-payload reading helpers.

Pure extraction / formatting over the inbound task body conventions used
by the A2A v1 and legacy PerfPilot envelopes. These helpers only read
payload shape — they do not touch the DB, filesystem, or service layer.

Consumed by ``services.task_executor`` (orchestrator ingress) and covered
by the resume-flow unit tests under ``tests/agent_framework/a2a/``.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def extract_thread_id_from_payload(payload: Any) -> Optional[str]:
    """Return the resolved A2A thread_id stamped by `a2a_server` at request time.

    The A2A server stamps `payload["_perfpilot_thread"] = {"thread_id":
    "<internal>", "external_thread_id": "<label>"}` after resolving
    Decision 17 thread lookup-or-create. Older callers / smoke clients
    that bypass the resolver get `None` and the orchestrator runs single-
    turn (no history load, no history persist).
    """
    if not isinstance(payload, dict):
        return None
    block = payload.get("_perfpilot_thread")
    if isinstance(block, dict):
        tid = block.get("thread_id")
        if isinstance(tid, str) and tid:
            return tid
    return None


def extract_resume_messages_from_payload(payload: Any) -> list[dict]:
    """Pull A2A §6.3 resume messages (if any) out of the task payload.

    When a client responds to a ``TASK_STATE_INPUT_REQUIRED`` by
    re-sending a message with the same ``taskId`` + ``contextId``, the
    ingress handler calls ``task_store.append_resume_message`` which
    appends the follow-up to ``payload["resume_messages"]`` as a JSONB
    array. On the resumed execution, this helper returns that list so
    the orchestrator can incorporate the new turns.

    Args:
        payload: The task payload JSON dict.

    Returns:
        A list of resume-message dicts (newest-last). Returns an empty
        list when the payload is not a dict, when the field is missing,
        or when the field is malformed. Each dict is expected to carry
        at least one of ``message_text`` / ``parts`` plus ``role`` and
        ``received_at`` — but this helper does not validate shape; it
        only guards against type errors so downstream code can iterate
        safely.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("resume_messages")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def resume_message_to_text(resume_message: dict) -> Optional[str]:
    """Resolve a resume message dict to a plain-text user turn.

    Resolution order:
      1. ``resume_message["message_text"]`` (populated when the
         inbound body carried a top-level ``message`` string or when
         ``_normalize_a2a_v1_body`` promoted the first text Part).
      2. First ``text`` Part in ``resume_message["parts"]`` (fallback
         for resume envelopes that only carry structured Parts).

    Returns ``None`` when no text can be extracted — the caller
    should skip empty resume messages rather than appending blank
    user turns to the LLM context.
    """
    text = resume_message.get("message_text")
    if isinstance(text, str) and text.strip():
        return text
    parts = resume_message.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_text = part.get("text")
            if isinstance(part_text, str) and part_text.strip():
                return part_text
    return None


def extract_user_message_from_payload(payload: Any) -> Optional[str]:
    """Pull the user's prompt out of the A2A task body.

    Resolution order per the A2A Upstream Request Data Contract
    (Appendix A):
      1. ``payload["message"]``
      2. ``payload["text"]``
      3. ``payload["prompt"]``
      4. First ``text`` Part in ``payload["parts"]``
      5. Composed prompt from all Parts (when ``parts[]`` is present)
      6. Whole payload (stringified) as a last resort

    When ``parts[]`` is present and contains structured content (ADO
    work items, test cases, test configuration), the Parts parser
    composes a rich prompt that includes all Parts content formatted
    for the orchestrator LLM.  The top-level ``message`` field (when
    present) is prepended as the primary user intent.
    """
    if not isinstance(payload, dict):
        return str(payload) if payload is not None else None

    # ── Step 1-3: Top-level message fields (contract precedence) ──
    top_level_message = None
    for key in ("message", "text", "prompt"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            top_level_message = value
            break

    # ── Step 4-5: Parts parsing ──
    from a2a.shared import parts_parser

    parsed = parts_parser.parse_request_body(payload)

    if parsed.has_parts and parsed.prompt:
        metadata_context = format_metadata_context(parsed.metadata)
        base = (
            f"{top_level_message}\n\n{parsed.prompt}"
            if top_level_message
            else parsed.prompt
        )
        if metadata_context:
            return f"{base}\n\n{metadata_context}"
        return base

    if top_level_message:
        # Append metadata context if available from the request
        metadata_context = format_metadata_context(parsed.metadata)
        if metadata_context:
            return f"{top_level_message}\n\n{metadata_context}"
        return top_level_message

    # ── Step 4 fallback: first text Part (no composed prompt) ──
    fallback = parts_parser.resolve_user_message(payload)
    if fallback:
        return fallback

    # ── Step 6: stringify the public payload ──
    public = {k: v for k, v in payload.items() if not k.startswith("_")}
    if public:
        return json.dumps(public)
    return None


def format_metadata_context(metadata: dict) -> str:
    """Format extracted upstream metadata as context lines for the LLM.

    Only includes metadata fields that carry actionable context.
    Returns an empty string when nothing useful is present.

    Nested ``scm`` and ``blazemeter`` blocks are surfaced as sub-lines
    so the orchestrator can decide whether to delegate script pushes
    and BlazeMeter provisioning.
    """
    if not metadata:
        return ""

    lines: list[str] = []
    scalar_keys = {
        "upstream_framework": "Upstream framework",
        "environment": "Target environment",
        "requested_workflow": "Requested workflow",
        "test_run_id": "Test run id",
    }
    for key, label in scalar_keys.items():
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"- {label}: {value}")

    scm = metadata.get("scm")
    if isinstance(scm, dict):
        parts: list[str] = []
        url = scm.get("url")
        if isinstance(url, str) and url.strip():
            parts.append(f"url={url.strip()}")
        branch = scm.get("branch")
        if isinstance(branch, str) and branch.strip():
            parts.append(f"branch={branch.strip()}")
        path = scm.get("path")
        if isinstance(path, str) and path.strip():
            parts.append(f"path={path.strip()}")
        provider = scm.get("provider")
        if isinstance(provider, str) and provider.strip():
            parts.append(f"provider={provider.strip()}")
        if parts:
            lines.append("- SCM target: " + ", ".join(parts))

    bm = metadata.get("blazemeter")
    if isinstance(bm, dict):
        parts = []
        for src_key, label in (
            ("workspaceId", "workspaceId"),
            ("workspace_id", "workspaceId"),
            ("projectId", "projectId"),
            ("project_id", "projectId"),
            ("testName", "testName"),
            ("test_name", "testName"),
        ):
            val = bm.get(src_key)
            if isinstance(val, (str, int)) and str(val).strip():
                parts.append(f"{label}={val}")
        if parts:
            deduped: list[str] = []
            seen: set[str] = set()
            for p in parts:
                if p not in seen:
                    seen.add(p)
                    deduped.append(p)
            lines.append("- BlazeMeter target: " + ", ".join(deduped))

    if not lines:
        return ""
    return "Upstream context:\n" + "\n".join(lines)
