"""Parse upstream A2A request bodies per the Upstream Request Data Contract.

This module handles the ``parts[]`` array, ``metadata`` block, and message
precedence resolution defined in the A2A Upstream Request Data Contract
(``A2A-Upstream-Request-Contract.md``).  It converts enriched upstream
payloads into a single coherent prompt string that the orchestrator LLM
can reason over, while preserving structured context for downstream use.

Supported media types (Contract Section 5):

    text/plain                                       - plain text prompt
    text/markdown                                    - Markdown content (test cases)
    application/json                                 - generic structured JSON
    application/vnd.perfpilot.ado-work-item+json     - ADO work item
    application/vnd.perfpilot.test-cases+json        - structured test case collection
    application/vnd.perfpilot.test-config+json       - test execution configuration

Message resolution order (Contract Appendix A):

    message > text > prompt > first text Part in parts[]

Security notes:

    - This module NEVER reads .env files or credential stores.
    - URL fields in Parts (e.g. ADO work item URLs) are passed through
      as-is; the orchestrator LLM decides whether to surface them.
    - No file-system writes occur here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── PerfPilot media type constants ──────────────────────────────────────────

MEDIA_TEXT_PLAIN = "text/plain"
MEDIA_TEXT_MARKDOWN = "text/markdown"
MEDIA_JSON = "application/json"
MEDIA_ADO_WORK_ITEM = "application/vnd.perfpilot.ado-work-item+json"
MEDIA_TEST_CASES = "application/vnd.perfpilot.test-cases+json"
MEDIA_TEST_CONFIG = "application/vnd.perfpilot.test-config+json"

_KNOWN_MEDIA_TYPES = frozenset({
    MEDIA_TEXT_PLAIN,
    MEDIA_TEXT_MARKDOWN,
    MEDIA_JSON,
    MEDIA_ADO_WORK_ITEM,
    MEDIA_TEST_CASES,
    MEDIA_TEST_CONFIG,
})


# ── Result dataclass ───────────────────────────────────────────────────────

@dataclass
class ParsedRequest:
    """Result of parsing an upstream A2A request body.

    Attributes:
        prompt:         Composed prompt string for the orchestrator LLM.
                        Includes all Parts content formatted for readability.
        parts_summary:  List of dicts summarising each parsed Part
                        (mediaType, has_text, has_data, has_url).
        metadata:       Extracted top-level ``metadata`` block from the
                        request body (upstream_framework, environment, etc.).
        test_run_id:    Resolved test_run_id — top-level value takes
                        precedence, then falls back to test-config Part.
        has_parts:      True when the request contained a non-empty
                        ``parts[]`` array.
    """

    prompt: str = ""
    parts_summary: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    test_run_id: Optional[str] = None
    has_parts: bool = False


# ── Public API ─────────────────────────────────────────────────────────────

def parse_request_body(body: dict) -> ParsedRequest:
    """Parse an upstream A2A request body per the upstream data contract.

    Handles both legacy payloads (just ``message``/``text``/``prompt``)
    and enriched payloads (``parts[]`` + ``metadata``).

    Args:
        body: The raw JSON-decoded request body dict.

    Returns:
        A ``ParsedRequest`` with the composed prompt, parts summary,
        extracted metadata, and resolved test_run_id.
    """
    if not isinstance(body, dict):
        return ParsedRequest()

    result = ParsedRequest()

    # ── Extract metadata ──
    raw_meta = body.get("metadata")
    if isinstance(raw_meta, dict):
        result.metadata = {
            k: v for k, v in raw_meta.items()
            if isinstance(k, str) and v is not None
        }

    # ── Resolve test_run_id (top-level wins) ──
    result.test_run_id = body.get("test_run_id") if isinstance(
        body.get("test_run_id"), str
    ) else None

    # ── Parse parts[] ──
    raw_parts = body.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        return result

    result.has_parts = True
    prompt_sections: list[str] = []

    for idx, part in enumerate(raw_parts):
        if not isinstance(part, dict):
            log.warning("parts[%d]: expected dict, got %s; skipping", idx, type(part).__name__)
            continue

        media_type = _resolve_media_type(part)
        summary: dict[str, Any] = {"index": idx, "mediaType": media_type}

        # ── Text Part ──
        if isinstance(part.get("text"), str):
            text_content = part["text"]
            summary["has_text"] = True
            section = _format_text_part(text_content, media_type, idx)
            if section:
                prompt_sections.append(section)

        # ── Data Part ──
        elif isinstance(part.get("data"), dict):
            data_content = part["data"]
            summary["has_data"] = True
            section = _format_data_part(data_content, media_type, idx)
            if section:
                prompt_sections.append(section)

            # Extract test_run_id from test-config if not set at top level
            if media_type == MEDIA_TEST_CONFIG and result.test_run_id is None:
                config_trid = data_content.get("test_run_id")
                if isinstance(config_trid, str) and config_trid.strip():
                    result.test_run_id = config_trid.strip()

        # ── URL Part ──
        elif isinstance(part.get("url"), str):
            summary["has_url"] = True
            prompt_sections.append(
                f"[Part {idx + 1} — URL reference]\nURL: {part['url']}"
            )

        else:
            summary["empty"] = True
            log.debug("parts[%d]: no text, data, or url field found", idx)

        result.parts_summary.append(summary)

    # ── Compose the final prompt ──
    if prompt_sections:
        result.prompt = "\n\n---\n\n".join(prompt_sections)

    return result


def resolve_user_message(body: dict) -> Optional[str]:
    """Resolve the user's primary message per contract precedence rules.

    Resolution order (Appendix A):
        ``message`` > ``text`` > ``prompt`` > first text Part in ``parts[]``

    Args:
        body: The raw JSON-decoded request body dict.

    Returns:
        The resolved user message string, or ``None`` if no message
        could be extracted.
    """
    if not isinstance(body, dict):
        return None

    for key in ("message", "text", "prompt"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value

    # Fall back to first text Part in parts[]
    raw_parts = body.get("parts")
    if isinstance(raw_parts, list):
        for part in raw_parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = part["text"].strip()
                if text:
                    return text

    return None


# ── Internal helpers ───────────────────────────────────────────────────────

def _resolve_media_type(part: dict) -> str:
    """Determine the effective mediaType for a Part.

    Defaults:
        - ``text/plain`` for Parts with ``text``
        - ``application/json`` for Parts with ``data``
        - ``text/plain`` for Parts with ``url``
    """
    explicit = part.get("mediaType")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if "text" in part:
        return MEDIA_TEXT_PLAIN
    if "data" in part:
        return MEDIA_JSON
    return MEDIA_TEXT_PLAIN


def _format_text_part(text: str, media_type: str, idx: int) -> str:
    """Format a text Part into a prompt section."""
    if not text.strip():
        return ""

    if media_type == MEDIA_TEXT_MARKDOWN:
        part_meta = ""
        return f"[Part {idx + 1} — Markdown content]\n{text}"
    return f"[Part {idx + 1} — Text]\n{text}"


def _format_data_part(data: dict, media_type: str, idx: int) -> str:
    """Format a data Part into a prompt section based on its mediaType."""

    if media_type == MEDIA_ADO_WORK_ITEM:
        return _format_ado_work_item(data, idx)

    if media_type == MEDIA_TEST_CASES:
        return _format_test_cases(data, idx)

    if media_type == MEDIA_TEST_CONFIG:
        return _format_test_config(data, idx)

    # Generic JSON
    return f"[Part {idx + 1} — Structured data ({media_type})]\n{json.dumps(data, indent=2)}"


def _format_ado_work_item(data: dict, idx: int) -> str:
    """Format an ADO work item Part into a readable prompt section."""
    lines = [f"[Part {idx + 1} — ADO Work Item]"]

    wi_id = data.get("id")
    wi_type = data.get("type")
    title = data.get("title")

    if wi_id is not None:
        lines.append(f"  ID: {wi_id}")
    if wi_type:
        lines.append(f"  Type: {wi_type}")
    if title:
        lines.append(f"  Title: {title}")

    url = data.get("url")
    if isinstance(url, str) and url.strip():
        lines.append(f"  URL: {url}")

    state = data.get("state")
    if state:
        lines.append(f"  State: {state}")

    area = data.get("area_path")
    if area:
        lines.append(f"  Area Path: {area}")

    parent = data.get("parent_feature_id")
    if parent is not None:
        lines.append(f"  Parent Feature ID: {parent}")

    tags = data.get("tags")
    if isinstance(tags, list) and tags:
        lines.append(f"  Tags: {', '.join(str(t) for t in tags)}")

    desc = data.get("description")
    if isinstance(desc, str) and desc.strip():
        lines.append(f"  Description: {desc}")

    ac = data.get("acceptance_criteria")
    if isinstance(ac, list) and ac:
        lines.append("  Acceptance Criteria:")
        for criterion in ac:
            lines.append(f"    - {criterion}")

    return "\n".join(lines)


def _format_test_cases(data: dict, idx: int) -> str:
    """Format structured test cases into a readable prompt section."""
    lines = [f"[Part {idx + 1} — Test Cases (structured)]"]

    cases = data.get("test_cases")
    if not isinstance(cases, list):
        lines.append(f"  {json.dumps(data, indent=2)}")
        return "\n".join(lines)

    lines.append(f"  Total test cases: {len(cases)}")

    source = data.get("source")
    if source:
        lines.append(f"  Source: {source}")

    approval = data.get("approval_status")
    if approval:
        lines.append(f"  Approval status: {approval}")

    for i, tc in enumerate(cases):
        if not isinstance(tc, dict):
            continue
        name = tc.get("name", f"Test Case {i + 1}")
        priority = tc.get("priority", "")
        lines.append(f"\n  [{i + 1}] {name}")
        if priority:
            lines.append(f"      Priority: {priority}")
        objective = tc.get("objective")
        if isinstance(objective, str) and objective.strip():
            lines.append(f"      Objective: {objective}")
        steps = tc.get("steps")
        if isinstance(steps, list):
            lines.append(f"      Steps: {len(steps)}")

    return "\n".join(lines)


def _format_test_config(data: dict, idx: int) -> str:
    """Format test configuration into a readable prompt section."""
    lines = [f"[Part {idx + 1} — Test Configuration]"]

    for key in ("environment", "blazemeter_test_id", "vusers",
                "ramp_up_seconds", "duration_seconds", "application_url"):
        value = data.get(key)
        if value is not None:
            lines.append(f"  {key}: {value}")

    # Include any extra keys not in the known set
    known = {"environment", "blazemeter_test_id", "vusers",
             "ramp_up_seconds", "duration_seconds", "application_url",
             "test_run_id"}
    extras = {k: v for k, v in data.items() if k not in known and v is not None}
    if extras:
        for k, v in extras.items():
            lines.append(f"  {k}: {v}")

    return "\n".join(lines)
