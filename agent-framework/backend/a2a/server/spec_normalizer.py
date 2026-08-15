"""Normalize A2A ``parts[]`` content into step-based Markdown test specs.

This module extracts browser automation test case content from incoming
A2A request ``parts[]`` arrays and converts it into the step-based
Markdown format expected by ``jmeter_get_browser_steps`` and the
Playwright browser automation pipeline.

Supported input formats:

    - ``text/markdown`` or ``text/plain`` — passed through as-is
      (assumed to already be in step format)
    - ``application/vnd.azure.devops.testcase+json`` — structured ADO
      test cases with ``test_cases[].steps[].action``; rendered as
      ``Step N: <action>`` lines with ``END TASK`` footer
    - ``application/json`` — generic JSON; step extraction attempted
      if ``test_cases[].steps[]`` structure is present

Target output format::

    Step 1: Navigate to https://demoblaze.com/.
    Step 2: Click on 'Laptops' under the 'Categories' menu.
    ...
    Step N: <final step>
    END TASK

Security notes:

    - This module NEVER reads .env files or credential stores.
    - No network calls are made.
    - File-system writes are handled by the caller (task_executor),
      not this module.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from a2a.shared.media_types import (
    MEDIA_ADO_TESTCASE,
    MEDIA_JSON,
    MEDIA_TEXT_MARKDOWN,
    MEDIA_TEXT_PLAIN,
)

log = logging.getLogger(__name__)

# Media types that carry test spec content as plain text
_TEXT_SPEC_TYPES = frozenset({MEDIA_TEXT_MARKDOWN, MEDIA_TEXT_PLAIN})

# Media types that carry structured test case data
_DATA_SPEC_TYPES = frozenset({MEDIA_ADO_TESTCASE, MEDIA_JSON})


# ── Public API ─────────────────────────────────────────────────────────────


def normalize_parts_to_spec(parts: list[dict]) -> Optional[str]:
    """Extract and normalize test spec content from A2A ``parts[]``.

    Scans the parts array for test case content and converts it into
    the step-based Markdown format used by ``jmeter_get_browser_steps``.

    Args:
        parts: The raw ``parts[]`` list from the A2A request body.

    Returns:
        A normalized Markdown string with step-based content and an
        ``END TASK`` footer, or ``None`` if no test spec content was
        found in the parts.
    """
    if not isinstance(parts, list) or not parts:
        return None

    spec_sections: list[str] = []

    for idx, part in enumerate(parts):
        if not isinstance(part, dict):
            continue

        media_type = _resolve_media_type(part)
        section = _extract_spec_from_part(part, media_type, idx)
        if section:
            spec_sections.append(section)

    if not spec_sections:
        return None

    combined = "\n\n".join(spec_sections)

    if not combined.rstrip().upper().endswith("END TASK"):
        combined = combined.rstrip() + "\nEND TASK\n"

    return combined


# ── Internal helpers ───────────────────────────────────────────────────────


def _resolve_media_type(part: dict) -> str:
    """Determine the effective mediaType for a Part.

    Mirrors the resolution logic in ``a2a_parts_parser._resolve_media_type``
    but is kept local to avoid coupling.
    """
    explicit = part.get("mediaType")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if "text" in part:
        return MEDIA_TEXT_PLAIN
    if "data" in part:
        return MEDIA_JSON
    return MEDIA_TEXT_PLAIN


def _extract_spec_from_part(
    part: dict, media_type: str, idx: int
) -> Optional[str]:
    """Extract test spec content from a single Part.

    Returns the spec content as a string, or ``None`` if the Part
    does not contain recognizable test spec content.
    """
    # ── Text parts (markdown / plain text) ──
    if media_type in _TEXT_SPEC_TYPES and isinstance(part.get("text"), str):
        text = part["text"].strip()
        if text and _looks_like_test_spec(text):
            log.info(
                "parts[%d]: extracting test spec from %s text part",
                idx,
                media_type,
            )
            return text
        return None

    # ── Structured ADO test case data ──
    if media_type == MEDIA_ADO_TESTCASE and isinstance(part.get("data"), dict):
        rendered = _render_ado_testcase_steps(part["data"], idx)
        if rendered:
            log.info(
                "parts[%d]: extracted ADO test case steps from %s",
                idx,
                media_type,
            )
        return rendered

    # ── Generic JSON — attempt step extraction ──
    if media_type == MEDIA_JSON and isinstance(part.get("data"), dict):
        rendered = _render_ado_testcase_steps(part["data"], idx)
        if rendered:
            log.info(
                "parts[%d]: extracted test case steps from generic JSON",
                idx,
            )
        return rendered

    return None


def _looks_like_test_spec(text: str) -> bool:
    """Heuristic check whether text content looks like a test spec.

    Returns True if the text contains step-like patterns (e.g.,
    ``Step 1:``, ``TC01:``, ``TS01:``, ``Test Case 1:``).
    """
    import re

    step_pattern = re.compile(
        r"^\s*(step|tc|ts|test case|test step)\s*\d",
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(step_pattern.search(text))


def _render_ado_testcase_steps(data: dict, idx: int) -> Optional[str]:
    """Render structured ADO test case data into step-based Markdown.

    Expects the structure defined in the A2A data contract::

        {
            "test_cases": [
                {
                    "name": "Test Case Name",
                    "steps": [
                        {
                            "number": 1,
                            "action": "Navigate to '/activities'.",
                            "expected_result": "Page is displayed."
                        }
                    ]
                }
            ]
        }

    Multiple test cases are concatenated with sequential step numbering.

    Args:
        data: The ``data`` field from a structured Part.
        idx: Part index (for logging).

    Returns:
        Rendered step-based Markdown string, or ``None`` if no steps
        could be extracted.
    """
    test_cases = data.get("test_cases")
    if not isinstance(test_cases, list) or not test_cases:
        return None

    lines: list[str] = []
    step_number = 1

    for tc_idx, tc in enumerate(test_cases):
        if not isinstance(tc, dict):
            continue

        steps = tc.get("steps")
        if not isinstance(steps, list) or not steps:
            log.debug(
                "parts[%d].test_cases[%d]: no steps found, skipping",
                idx,
                tc_idx,
            )
            continue

        tc_name = tc.get("name", f"Test Case {tc_idx + 1}")

        if len(test_cases) > 1:
            lines.append(f"# {tc_name}")
            lines.append("")

        for step in steps:
            if not isinstance(step, dict):
                continue

            action = step.get("action", "").strip()
            if not action:
                continue

            lines.append(f"Step {step_number}: {action}")

            expected = step.get("expected_result", "").strip()
            if expected:
                lines.append(f"    - Expected: {expected}")

            step_number += 1

        if len(test_cases) > 1:
            lines.append("")

    if not lines:
        return None

    return "\n".join(lines)
