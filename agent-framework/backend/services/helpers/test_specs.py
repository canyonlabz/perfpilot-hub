"""Persist inbound A2A Playwright test specs to the artifacts tree.

Normalizes ``parts[]`` (via ``a2a.server.helpers.spec_normalizer``) and writes
Markdown under ``artifacts/{test_run_id}/test-specs/``. Propagates the
saved path and ``test_run_id`` through orchestrator ContextVars so
``delegate_to_specialist`` can inject them into child payloads.

Also owns the orchestrator ingress order: resolve/mint ``test_run_id``
first, then persist. ``persist_test_spec_from_parts`` is reuse-only.

Dependency direction: this module imports from ``core/``,
``a2a.server.helpers.spec_normalizer``, ``utils.paths``, the
``test_run_id`` resolver, and orchestrator ContextVars. It must NOT
import ``services.task_executor``.
"""

from __future__ import annotations

import logging
from typing import Optional

from stores import task_store

log = logging.getLogger(__name__)


def persist_test_spec_from_parts(task: task_store.AgentTask) -> None:
    """Normalize and persist a Playwright test spec from A2A parts to disk.

    Scope (strict Playwright gate):

      This helper runs only when the inbound task payload declares
      ``parts[1].metadata.source_type == "playwright"`` (see
      ``core.test_run_id.extract_source_type``). HAR captures, OpenAPI
      specs, and payloads with no ``source_type`` are no-ops — the
      corresponding specialist tools fetch their own source files, so
      the test-specs/ folder is only populated for the browser
      automation path.

    When the gate opens, the parts are normalized to the step-based
    Markdown format expected by ``jmeter_get_browser_steps`` and saved
    under ``artifacts/{test_run_id}/test-specs/{test_run_id}_test_spec.md``.
    The ``test_run_id`` is reused from the task row or payload only —
    minting is NOT allowed here. The orchestrator-level resolver
    (``services.helpers.test_run_id_resolver``) is responsible for
    minting on first contact, so by the time this helper runs the ID
    must already be present. If it's missing, we log a warning and
    return defensively.

    The saved file path is set on ``agent_spec_file_var`` so
    ``delegate_to_specialist()`` can auto-inject it into child task
    payloads. This ensures the Script Agent can use the file directly
    for browser automation without calling ``jmeter_get_test_specs``.

    Error signalling (A2A §4.1.3):

      When Playwright is declared but the normalizer finds no
      recognizable step-based content in the parts, this helper raises
      ``TaskInputRequiredSignal`` with
      ``reason_code="malformed_playwright_test_spec"``. The outer
      ``execute_task`` wrapper catches the signal, transitions the
      task to ``input_required``, and surfaces the question through
      SSE so the client can resend corrected parts.

    Skips silently (no raise) when:
      - Payload is not a dict
      - No ``parts[]`` array in the payload
      - ``source_type`` is not ``"playwright"`` (HAR / OpenAPI / none)
      - ``test_run_id`` is not populated on either the task row or payload
    """
    from core.task_signals import TaskInputRequiredSignal
    from core.test_run_id import extract_source_type, resolve_test_run_id

    if not isinstance(task.payload, dict):
        return

    parts = task.payload.get("parts")
    if not isinstance(parts, list) or not parts:
        return

    # ── Strict Playwright gate ────────────────────────────────────
    # HAR / OpenAPI inbound flows do not route through this helper —
    # their specialist tools fetch their own source files. Only
    # Playwright test specs are materialized to disk here.
    source_type = extract_source_type(task.payload)
    if source_type != "playwright":
        log.debug(
            "persist_test_spec_from_parts.source_type_skip",
            extra={
                "source_type": source_type,
                "task_id": str(task.task_id),
                "parts_count": len(parts),
            },
        )
        return

    # ── Reuse-only test_run_id resolution ────────────────────────
    # The orchestrator-level resolver mints at ingress; by the time
    # this helper runs the ID must already be present. If it's not,
    # the resolver was bypassed somehow — log a warning and skip.
    test_run_id = resolve_test_run_id(
        getattr(task, "test_run_id", None),
        payload=task.payload,
    )
    if not test_run_id:
        log.warning(
            "persist_test_spec_from_parts.missing_test_run_id",
            extra={
                "source_type": source_type,
                "task_id": str(task.task_id),
                "note": (
                    "orchestrator-level resolver should have populated "
                    "test_run_id before this call; skipping spec write"
                ),
            },
        )
        return

    # ── Propagate test_run_id via ContextVar for the LLM ─────────
    from agents.orchestrator.agent import agent_test_run_id_var
    agent_test_run_id_var.set(test_run_id)

    # ── Normalize + validate ─────────────────────────────────────
    from a2a.server.helpers import spec_normalizer

    spec_content = spec_normalizer.normalize_parts_to_spec(parts)
    if not spec_content:
        # Playwright was declared but no step-based content was found.
        # Raise INPUT_REQUIRED so the client can resend corrected
        # parts rather than silently proceeding to a specialist that
        # will fail opaquely.
        log.info(
            "persist_test_spec_from_parts.malformed_playwright_test_spec",
            extra={
                "source_type": source_type,
                "task_id": str(task.task_id),
                "test_run_id": test_run_id,
                "parts_count": len(parts),
            },
        )
        raise TaskInputRequiredSignal(
            reason_code="malformed_playwright_test_spec",
            question_text=(
                "I received a Playwright test-spec request but the "
                "parts payload did not contain any recognizable test "
                "steps. Please resend the test case with step-based "
                "content (for example lines starting with `Step 1:`, "
                "`TC01:`, or an ADO test case JSON carrying "
                "`test_cases[].steps[].action`), or correct the "
                "`source_type` metadata if this is not a Playwright "
                "request."
            ),
            reason_data={
                "source_type": source_type,
                "parts_count": len(parts),
            },
        )

    # ── Write spec file + stash path on ContextVar ───────────────
    from utils.paths import get_artifacts_base

    spec_dir = get_artifacts_base() / test_run_id / "test-specs"

    try:
        spec_dir.mkdir(parents=True, exist_ok=True)
        spec_file = spec_dir / f"{test_run_id}_test_spec.md"
        spec_file.write_text(spec_content, encoding="utf-8")
        spec_path = str(spec_file)

        log.info(
            "persist_test_spec_from_parts.saved",
            extra={
                "test_run_id": test_run_id,
                "spec_path": spec_path,
                "size_bytes": len(spec_content),
            },
        )

        # Propagate the file path via ContextVar so delegate_to_specialist
        # auto-injects it into child task payloads.
        from agents.orchestrator.agent import agent_spec_file_var
        agent_spec_file_var.set(spec_path)

    except OSError:
        log.exception(
            "persist_test_spec_from_parts.write_failed",
            extra={
                "test_run_id": test_run_id,
                "spec_dir": str(spec_dir),
            },
        )


async def resolve_test_run_id_then_persist_spec(
    task: task_store.AgentTask,
    user_message: Optional[str],
    thread_id: Optional[str],
) -> Optional[str]:
    """Resolve ``test_run_id``, then persist a Playwright spec if present.

    On mint the classifier writes the new ID onto ``task.payload``
    before this function calls persist, so the reuse-only writer can
    find it. Raises ``TaskInputRequiredSignal`` when the resolver
    rejects (persist is skipped).

    Args:
        task: Orchestrator task currently executing.
        user_message: Effective user text for this turn (including
            resume answers).
        thread_id: Conversation thread id, or ``None``.

    Returns:
        The resolved ``test_run_id``, or ``None`` for skip_mint /
        start-test allow-null.
    """
    from services.helpers.test_run_id_resolver import (
        resolve_test_run_id_or_signal_input_required,
    )

    test_run_id = await resolve_test_run_id_or_signal_input_required(
        task, user_message, thread_id,
    )
    persist_test_spec_from_parts(task)
    return test_run_id
