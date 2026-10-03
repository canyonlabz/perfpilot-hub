"""Framework-wide Human-in-the-Loop (HITL) policy layer.

Consolidates gate rule definitions, matching logic, and enforcement
(both task-start and per-tool-invocation) into a single module so all
HITL behavior lives in one place and can be extended without touching
the task executor.

Persistence of HITL prompts and decisions remains in
``stores/hitl_store.py`` (unchanged). This module is the policy layer;
that module is the persistence layer.

Config lives in ``agent-framework/backend/config/hitl.yaml`` (or the
committed ``hitl.example.yaml`` fallback), loaded via
``utils.config_loader.load_hitl_config()``. YAML is the sole source of
truth for HITL configuration — there is no in-code default publish-tool
list. When a config key is missing or malformed, resolvers use a
conservative in-code fallback (empty frozenset for tool sets, ``False``
for gate toggles, ``2.0`` / ``300.0`` for polling values) and emit a
WARNING once per process via ``_warn_once()`` so operators notice the
degraded state without log-flooding.

Full operator-facing documentation:
    docs/agent-framework/hitl-configuration.md

Callback-injection design: the enforcers accept an optional
``on_progress`` async callable so they can emit SSE progress updates
without importing ``task_executor.TaskEvent`` / ``_broadcast`` (which
would create a circular import).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from stores import hitl_store, task_store

log = logging.getLogger(__name__)


# =============================================================================
# 0. Warning-dedup helper
# =============================================================================
# All resolvers below can hit their fallback path when the operator's
# YAML is missing a key. We want ONE warning per missing key per process
# so a degraded config surfaces without flooding the logs on every task.

_warned_missing_keys: set[str] = set()
_warned_missing_keys_lock = threading.Lock()


def _warn_once(key: str, message: str) -> None:
    """Emit ``log.warning(message)`` at most once per process per key.

    Used by the resolvers so a degraded HITL config surfaces on first
    access without flooding the logs on every task or tool call. The
    dedup set is process-scoped; a restart resets it. Tests can clear
    it via ``_reset_warning_dedup()``.
    """
    with _warned_missing_keys_lock:
        if key in _warned_missing_keys:
            return
        _warned_missing_keys.add(key)
    log.warning(message)


def _reset_warning_dedup() -> None:
    """Test-only: clear the dedup set so warning-emit tests can re-fire."""
    with _warned_missing_keys_lock:
        _warned_missing_keys.clear()


# =============================================================================
# 1. Publish-tool set resolver (YAML-driven; empty fallback + WARNING)
# =============================================================================
# NOTE: there is NO code-side default publish-tool list. The YAML file
# is the sole source of truth. If the key is missing or malformed, the
# resolver returns an empty frozenset (gate no-op) and logs a WARNING
# once per process so operators see the degraded state.


def get_publish_mcp_tools() -> frozenset[str]:
    """Return the effective publish-tool set.

    Reads ``publish_tools:`` from ``config/hitl.yaml`` (or
    ``hitl.example.yaml`` fallback). YAML is the sole source of truth
    for which tools trigger the publish gate — there is no in-code
    list of default tools.

    When the key is missing or malformed, logs a WARNING (once per
    process) and returns an empty frozenset. This causes the publish
    gate to become a no-op (nothing is gated) — a visibly degraded
    state that operators are expected to notice and fix by restoring
    the ``publish_tools:`` key in their ``hitl.yaml``.

    Returns:
        A frozenset of fully-qualified MCP tool names (e.g.
        ``{"confluence_create_page", "confluence_update_page"}``), or
        an empty frozenset when the YAML key is missing/malformed.
    """
    hitl_cfg = _get_hitl_config()
    override = hitl_cfg.get("publish_tools")
    if isinstance(override, list):
        return frozenset(str(t) for t in override if isinstance(t, str))
    _warn_once(
        "publish_tools",
        "hitl.yaml is missing or malformed 'publish_tools:' — the "
        "publish gate is now a no-op (nothing will be gated). Restore "
        "the key in agent-framework/backend/config/hitl.yaml.",
    )
    return frozenset()


# =============================================================================
# 2. Rule dataclass + rule list
# =============================================================================


@dataclass(frozen=True)
class HitlGateRule:
    """Declarative HITL rule: which (agent, tool) combos require approval.

    Attributes:
        config_key: The key under ``gates:`` in ``hitl.yaml`` that
            enables or disables this rule (e.g.
            ``"require_approval_before_publish"``).
        agent_names: Tuple of agent folder names the rule applies to.
        tools: Frozenset of fully-qualified MCP tool names the rule
            matches, OR ``None`` to match any tool for the listed
            agents. Note: rules using ``tools=None`` fire on every
            task for those agents and should be used sparingly.
        title: Short human-readable title shown on the HITL approval
            card (e.g. ``"Approve Report Publication"``).
        summary_template: Format string for the card body. Placeholders
            are interpolated via ``_SafeFormatMap`` so unknown keys
            render as literal ``{key}`` rather than raising.
    """

    config_key: str
    agent_names: tuple[str, ...]
    tools: Optional[frozenset[str]]
    title: str
    summary_template: str

    def matches(self, agent_name: str, tool: Optional[str]) -> bool:
        """Return True iff this rule applies to (agent_name, tool).

        Semantics:
          - ``tools=None``           → match ANY tool for this agent
          - ``tools=frozenset({..})``→ match only when ``tool`` is one
                                        of the listed names
        """
        if agent_name not in self.agent_names:
            return False
        if self.tools is not None and (tool is None or tool not in self.tools):
            return False
        return True

    def build_prompt(self, agent_name: str, payload: dict) -> dict:
        """Render the HITL prompt dict from this rule + a task payload.

        The payload is expected to contain ``tool`` (str) and ``args``
        (dict). Both are optional — missing values render as literals
        in the summary template via ``_SafeFormatMap``.

        Returns:
            A dict with keys ``title``, ``summary``, ``artifact`` — the
            shape ``hitl_store.create_prompt`` expects.
        """
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        tool = payload.get("tool", "unknown")

        template_vars = {
            "agent_name": agent_name,
            "tool": tool,
            **{k: str(v) for k, v in args.items()},
        }
        summary = self.summary_template.format_map(
            _SafeFormatMap(template_vars)
        )

        artifact: dict[str, Any] = {"tool": tool, "agent": agent_name}
        artifact.update({k: str(v) for k, v in args.items()})
        if payload.get("action"):
            artifact["action"] = payload["action"]

        return {"title": self.title, "summary": summary, "artifact": artifact}


class _SafeFormatMap(dict):
    """Dict subclass that returns ``'{key}'`` for missing keys in ``str.format_map``.

    Lets a summary template reference optional payload fields without
    raising ``KeyError`` when those fields are absent — the placeholder
    is rendered literally instead.
    """

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _build_rules() -> list[HitlGateRule]:
    """Assemble the rule list.

    Called on each match so that config-driven overrides (specifically
    the ``publish_tools`` scope on the publish rule) are picked up
    without a process restart. Cheap — the list is 3 items today.

    Agent-name strings are literal here (not imported constants) so
    this module stays fully self-contained; adding a new rule for a
    new agent does NOT require touching ``task_executor.py``.
    """
    return [
        HitlGateRule(
            config_key="require_approval_before_test_provision",
            agent_names=("execution-agent",),
            tools=frozenset({"provision_performance_test"}),
            title="Approve BlazeMeter Test Provisioning",
            summary_template=(
                "The {agent_name} wants to create a NEW BlazeMeter test "
                "'{test_name}' for environment {environment} and upload the "
                "JMX from {jmx_path}. Approve to provision, or reject to keep "
                "the JMX in Git only."
            ),
        ),
        HitlGateRule(
            config_key="require_approval_before_test_start",
            agent_names=("execution-agent",),
            tools=frozenset({"start_performance_test"}),
            title="Approve Performance Test Start",
            summary_template=(
                "The {agent_name} wants to start BlazeMeter test {test_id}. "
                "Approve to proceed or reject to cancel."
            ),
        ),
        HitlGateRule(
            config_key="require_approval_before_publish",
            agent_names=("reporting-agent",),
            tools=get_publish_mcp_tools(),
            title="Approve Report Publication",
            summary_template=(
                "The {agent_name} wants to invoke {tool}. "
                "Approve to proceed or reject to cancel."
            ),
        ),
    ]


# =============================================================================
# 3. Config loader + gate-enabled check
# =============================================================================


def _get_hitl_config() -> dict:
    """Return the framework-wide HITL config dict.

    Reads from ``agent-framework/backend/config/hitl.yaml`` (with
    ``hitl.example.yaml`` fallback). Cached by the loader.
    """
    from utils import config_loader
    cfg = config_loader.load_hitl_config()
    return cfg if isinstance(cfg, dict) else {}


def _is_gate_enabled(config_key: str, hitl_cfg: Optional[dict] = None) -> bool:
    """Return True if a HITL gate is enabled in config.

    Supports both the new schema (``gates.<key>: true``) and the legacy
    flat schema (``<key>: true``) so migration is a pure rename.

    When the key is absent from BOTH shapes, logs a WARNING (once per
    process per key) and returns ``False``. Defaulting missing gates
    to "open" (no HITL) is a safety-net fallback so a degraded config
    doesn't wedge the whole framework; the WARNING surfaces the
    condition for operator attention.
    """
    if hitl_cfg is None:
        hitl_cfg = _get_hitl_config()
    gates = hitl_cfg.get("gates")
    if isinstance(gates, dict) and config_key in gates:
        return bool(gates.get(config_key, False))
    if config_key in hitl_cfg:
        return bool(hitl_cfg.get(config_key, False))
    _warn_once(
        f"gates.{config_key}",
        f"hitl.yaml is missing gate 'gates.{config_key}' — defaulting "
        f"to false (gate open, no HITL). Add it under 'gates:' in "
        f"agent-framework/backend/config/hitl.yaml.",
    )
    return False


# =============================================================================
# 4. Matcher
# =============================================================================


def find_matching_rule(
    agent_name: str,
    tool: Optional[str],
) -> Optional[HitlGateRule]:
    """Return the first HITL rule that matches (agent, tool) AND is enabled.

    Args:
        agent_name: Specialist agent folder name (e.g. ``"reporting-agent"``).
        tool: Fully-qualified MCP tool name the specialist intends to
            invoke, OR ``None`` when the caller doesn't know yet
            (e.g. Web-UI free-form task at task-start).

    Returns:
        The matching ``HitlGateRule``, or ``None`` if no rule applies
        or the matching rule's config toggle is disabled.
    """
    hitl_cfg = _get_hitl_config()
    for rule in _build_rules():
        if rule.matches(agent_name, tool) and _is_gate_enabled(rule.config_key, hitl_cfg):
            return rule
    return None


# =============================================================================
# 5. Enforcers
# =============================================================================
# Callback signature for emitting SSE progress messages. task_executor
# passes its own broadcaster; HITL stays free of TaskEvent/_broadcast
# imports to avoid a circular dependency.
ProgressCallback = Callable[[str], Awaitable[None]]


async def enforce_at_task_start(
    task: task_store.AgentTask,
    on_progress: Optional[ProgressCallback] = None,
) -> Optional[str]:
    """Config-driven HITL gate — called once at task start.

    Looks for a rule that matches the task's
    ``(agent_name, payload.tool)`` AND whose config key is enabled. If
    a match is found, creates a HITL prompt and waits for the human to
    decide (approve / reject) or for the configured timeout to expire.

    Args:
        task: The ``AgentTask`` being started.
        on_progress: Optional async callable ``(message: str) -> None``
            invoked to emit SSE progress events (waiting / approved).
            When ``None``, no progress events are broadcast.

    Returns:
        ``None`` when no gate applies or when the human approved.
        A rejection reason string when the human rejected or the gate
        timed out — the caller should cancel the task.
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    raw_tool = payload.get("tool")
    tool = raw_tool if isinstance(raw_tool, str) else None

    rule = find_matching_rule(task.agent_name, tool)
    if rule is None:
        return None

    prompt = rule.build_prompt(task.agent_name, payload)

    try:
        approval = await hitl_store.create_prompt(task.task_id, prompt)
    except Exception as exc:
        log.exception("enforce_at_task_start: failed to create HITL prompt")
        return f"Failed to create HITL prompt: {exc}"

    log.info(
        "HITL gate active for task %s (approval_id=%d, rule=%s)",
        task.task_id, approval.id, rule.config_key,
    )

    if on_progress:
        await on_progress("Waiting for human approval...")

    decision = await wait_for_decision(approval.id)

    if decision.approved:
        log.info("HITL gate approved for task %s", task.task_id)
        if on_progress:
            await on_progress("Human approval granted — proceeding with execution")
        return None

    if decision.timed_out:
        timeout = get_default_timeout()
        log.warning(
            "HITL gate timed out for task %s after %.0fs",
            task.task_id, timeout,
        )
        return f"HITL approval timed out after {int(timeout)}s"

    # Rejected (either explicitly by user, or by row-disappeared sentinel).
    reason = decision.feedback or "Rejected by user"
    log.info("HITL gate rejected for task %s: %s", task.task_id, reason)
    return reason


async def enforce_at_tool_call(
    task: task_store.AgentTask,
    fn_name: str,
    fn_args: dict,
    on_progress: Optional[ProgressCallback] = None,
) -> Optional[str]:
    """Per-tool HITL gate — called right before a specialist invokes a tool.

    Complementary to :func:`enforce_at_task_start` (which runs against
    ``payload.tool`` at task start). This hook runs mid-loop so
    Web-UI free-form prompts — which reach the specialist without a
    ``payload.tool`` — still get gated when the specialist's LLM
    decides to invoke a write tool such as ``confluence_create_page``.

    Reuses :func:`find_matching_rule` so behavior stays consistent
    with task-start gating.

    Args:
        task: The ``AgentTask`` the specialist is executing.
        fn_name: The fully-qualified MCP tool name the LLM chose.
        fn_args: The tool arguments the LLM assembled (used to render
            a payload-shaped view for the prompt template).
        on_progress: Optional async callable for SSE progress messages.

    Returns:
        ``None`` when no gate applies or when the human approved.
        A rejection reason string when the human rejected or the gate
        timed out — the caller MUST short-circuit tool execution and
        surface the rejection back to the LLM (as a synthetic tool
        result) so the specialist can react (e.g. abandon publish).
    """
    rule = find_matching_rule(task.agent_name, fn_name)
    if rule is None:
        return None

    # Build a payload-shaped view so the existing prompt template
    # renders the tool name + args exactly like the task-start path.
    synthetic_payload = {"tool": fn_name, "args": dict(fn_args or {})}
    prompt = rule.build_prompt(task.agent_name, synthetic_payload)

    try:
        approval = await hitl_store.create_prompt(task.task_id, prompt)
    except Exception as exc:
        log.exception(
            "enforce_at_tool_call: failed to create HITL prompt for tool "
            "%s on task %s", fn_name, task.task_id,
        )
        return f"Failed to create HITL prompt for tool {fn_name}: {exc}"

    log.info(
        "HITL gate active for task %s tool=%s (approval_id=%d, rule=%s)",
        task.task_id, fn_name, approval.id, rule.config_key,
    )

    if on_progress:
        await on_progress(f"Waiting for human approval before {fn_name}...")

    decision = await wait_for_decision(approval.id)

    if decision.approved:
        log.info(
            "HITL gate approved for task %s tool=%s",
            task.task_id, fn_name,
        )
        if on_progress:
            await on_progress(f"Human approval granted — invoking {fn_name}")
        return None

    if decision.timed_out:
        timeout = get_default_timeout()
        log.warning(
            "HITL gate timed out for task %s tool=%s after %.0fs",
            task.task_id, fn_name, timeout,
        )
        return f"HITL approval timed out after {int(timeout)}s"

    # Rejected (either explicitly by user, or by row-disappeared sentinel).
    reason = decision.feedback or "Rejected by user"
    log.info(
        "HITL gate rejected for task %s tool=%s: %s",
        task.task_id, fn_name, reason,
    )
    return reason


# =============================================================================
# 6. Shared poll-loop primitive (used by enforcers AND request_human_approval)
# =============================================================================

_FALLBACK_POLL_INTERVAL_SECONDS = 2.0
_FALLBACK_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class Decision:
    """Terminal outcome of a HITL poll loop.

    Consumed by both the framework enforcers (which translate to an
    ``Optional[str]`` rejection reason) and the orchestrator's
    ``request_human_approval`` tool (which wraps this into a
    JSON-serializable envelope for the LLM).

    Exactly one of ``approved`` / ``rejected`` / ``timed_out`` is True.
    """

    approved: bool
    rejected: bool
    timed_out: bool
    feedback: Optional[str]
    decided_by: Optional[str]
    approval_id: int


def get_default_poll_interval() -> float:
    """Framework-wide default HITL poll interval (seconds).

    Sourced from ``hitl.yaml::poll_interval_seconds``. If the key is
    missing or malformed, logs a WARNING (once per process) and returns
    ``_FALLBACK_POLL_INTERVAL_SECONDS`` (2.0) so the framework keeps
    running rather than crashing the Web UI on a config gap.
    """
    cfg = _get_hitl_config()
    if "poll_interval_seconds" in cfg:
        try:
            return float(cfg["poll_interval_seconds"])
        except (TypeError, ValueError):
            _warn_once(
                "poll_interval_seconds",
                f"hitl.yaml has malformed 'poll_interval_seconds' "
                f"(value={cfg['poll_interval_seconds']!r}); falling back to "
                f"{_FALLBACK_POLL_INTERVAL_SECONDS}. Fix the value in "
                f"agent-framework/backend/config/hitl.yaml.",
            )
            return _FALLBACK_POLL_INTERVAL_SECONDS
    _warn_once(
        "poll_interval_seconds",
        f"hitl.yaml is missing 'poll_interval_seconds:' — defaulting to "
        f"{_FALLBACK_POLL_INTERVAL_SECONDS}. Set it in "
        f"agent-framework/backend/config/hitl.yaml.",
    )
    return _FALLBACK_POLL_INTERVAL_SECONDS


def get_default_timeout() -> float:
    """Framework-wide default HITL timeout (seconds).

    Sourced from ``hitl.yaml::timeout_seconds``. If the key is missing
    or malformed, logs a WARNING (once per process) and returns
    ``_FALLBACK_TIMEOUT_SECONDS`` (300.0) so the framework keeps running
    rather than crashing the Web UI on a config gap.
    """
    cfg = _get_hitl_config()
    if "timeout_seconds" in cfg:
        try:
            return float(cfg["timeout_seconds"])
        except (TypeError, ValueError):
            _warn_once(
                "timeout_seconds",
                f"hitl.yaml has malformed 'timeout_seconds' "
                f"(value={cfg['timeout_seconds']!r}); falling back to "
                f"{_FALLBACK_TIMEOUT_SECONDS}. Fix the value in "
                f"agent-framework/backend/config/hitl.yaml.",
            )
            return _FALLBACK_TIMEOUT_SECONDS
    _warn_once(
        "timeout_seconds",
        f"hitl.yaml is missing 'timeout_seconds:' — defaulting to "
        f"{_FALLBACK_TIMEOUT_SECONDS}. Set it in "
        f"agent-framework/backend/config/hitl.yaml.",
    )
    return _FALLBACK_TIMEOUT_SECONDS


async def wait_for_decision(
    approval_id: int,
    *,
    poll_interval: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Decision:
    """Poll ``hitl_approvals`` until terminal or timeout. Never raises.

    Shared primitive used by:
      - :func:`enforce_at_task_start` / :func:`enforce_at_tool_call`
        (which convert the ``Decision`` into an ``Optional[str]``
        rejection reason).
      - ``agents.orchestrator.agent.request_human_approval`` (which
        wraps the ``Decision`` into its LLM-facing JSON envelope).

    Args:
        approval_id: The ``hitl_approvals.id`` returned by
            ``hitl_store.create_prompt``.
        poll_interval: Seconds between polls. When ``None``, uses
            :func:`get_default_poll_interval`.
        timeout: Seconds to wait before giving up. When ``None``, uses
            :func:`get_default_timeout`.

    Returns:
        A ``Decision`` reflecting the terminal state:
          - ``approved=True``  → human clicked Approve
          - ``rejected=True``  → human clicked Reject (with optional
                                  feedback), OR the ``hitl_approvals``
                                  row disappeared during polling
                                  (defensive short-circuit)
          - ``timed_out=True`` → deadline elapsed without decision

    Never raises. On persistence errors during polling, logs the
    exception and continues polling.
    """
    poll = max(
        0.1,
        float(poll_interval if poll_interval is not None else get_default_poll_interval()),
    )
    timeout_s = float(
        timeout if timeout is not None else get_default_timeout()
    )
    deadline = asyncio.get_event_loop().time() + max(0.0, timeout_s)

    while asyncio.get_event_loop().time() < deadline:
        try:
            current = await hitl_store.get_approval(approval_id)
        except Exception:
            log.exception("wait_for_decision: poll error (will retry)")
            await asyncio.sleep(poll)
            continue

        if current is None:
            # Row disappeared — treat as a rejection so the caller
            # short-circuits cleanly instead of polling until timeout.
            return Decision(
                approved=False,
                rejected=True,
                timed_out=False,
                feedback="hitl_approvals row disappeared during poll",
                decided_by=None,
                approval_id=approval_id,
            )

        if current.decision == "approved":
            return Decision(
                approved=True,
                rejected=False,
                timed_out=False,
                feedback=current.feedback,
                decided_by=current.decided_by,
                approval_id=approval_id,
            )
        if current.decision == "rejected":
            return Decision(
                approved=False,
                rejected=True,
                timed_out=False,
                feedback=current.feedback,
                decided_by=current.decided_by,
                approval_id=approval_id,
            )

        await asyncio.sleep(poll)

    return Decision(
        approved=False,
        rejected=False,
        timed_out=True,
        feedback=None,
        decided_by=None,
        approval_id=approval_id,
    )
