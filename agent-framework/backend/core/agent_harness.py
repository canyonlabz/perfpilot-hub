"""Native AG2 harness shared by every PerfPilot agent.

Configures AG2 1.3.x built-in middleware and observers from each agent's
existing ``config.yaml`` loop-limit keys and its ``context_management`` budget:

- **HITL** — ``services.hitl_gates.HitlGateMiddleware`` with
  ``hitl_store_hook`` as the agent's ``hitl_hook`` (``hitl_approvals`` is the
  authority on both the A2A and AG-UI surfaces).
- **Repetition throttle** — :class:`ToolRepeatThrottle`, a ``LoopDetector``
  with per-tool limits (``max_consecutive_repeats`` / ``polling_tools`` +
  ``polling_max_consecutive_repeats``) that raises a FATAL alert.
  ``AlertPolicy`` (via ``AssemblerMiddleware``) turns it into a ``HaltEvent``
  and ``HaltCheckMiddleware`` stops the next model call.
- **Round cap** — :class:`ToolRoundLimit` ends the turn after
  ``max_tool_rounds`` model calls.
- **Context protection** — ``TokenLimiter`` trims what is sent on each model
  call to the effective prompt budget. Stored history is not modified, and
  :class:`ToolTraceMiddleware` persists every full tool result.
- **Cost guard** — ``TokenMonitor`` warnings, scaled by ``max_tool_rounds``.
  Warnings never halt a run.
- **Accounting** — :class:`ToolTraceMiddleware` writes ``tool_call_traces``
  and :class:`TokenLedgerObserver` writes ``token_ledger``, both through the
  existing fire-and-forget helpers in ``stores.trace_store`` (the same rows the
  legacy loop wrote; no new persistence surface).

Middleware order matters: AG2 treats the first entry as outermost, so the
trace middleware sits inside the HITL gate — a call the gate refuses is never
recorded as executed.

This module imports no transport code (A2A, AG-UI, task executor), so it moves
with the agent runtime. Usage::

    harness = build_agent_harness("reporting-agent")
    agent = create_agent(definition, tools=tools, **harness.agent_kwargs())
    reply = await agent.ask(message, variables={"task_id": str(task_id)})

``AlertPolicy`` and the observers keep state for each run, and AG2 binds an
observer to one run at a time, so call ``agent_kwargs()`` once per agent
instance and do not share that instance across concurrent runs.

AG2 is a hard runtime dependency of this harness, so (unlike
``core.base_agent``) its symbols are imported at module scope: the throttle,
round cap, and accounting classes subclass AG2 base classes at definition time.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ag2.assembly import AssemblerMiddleware
from ag2.events import (
    BaseEvent,
    HaltEvent,
    ModelMessage,
    ModelResponse,
    ObserverAlert,
    Severity,
    ToolCallEvent,
    ToolErrorEvent,
    ToolResultEvent,
    UsageEvent,
)
from ag2.middleware import BaseMiddleware, HaltCheckMiddleware, Middleware, TokenLimiter
from ag2.observers import BaseObserver, LoopDetector, TokenMonitor
from ag2.policies import AlertPolicy
from ag2.watch import EventWatch

from services import hitl_gates
from services.hitl_gates import _parse_arguments, _task_id_from
from services.helpers.token_context_window import resolve_context_budget_for_agent
from stores import trace_store

log = logging.getLogger(__name__)

THROTTLE_SOURCE = "tool-repeat-throttle"
ROUND_LIMIT_SOURCE = "round-limit"
TOKEN_LEDGER_SOURCE = "token-ledger"


# =============================================================================
# Settings
# =============================================================================


@dataclass(frozen=True)
class HarnessSettings:
    """Resolved loop limits and context budget for one agent."""

    max_tool_rounds: int
    max_consecutive_repeats: int
    polling_tools: frozenset[str]
    polling_max_consecutive_repeats: int
    effective_prompt_budget: int
    compaction_threshold: float

    @property
    def token_warn_threshold(self) -> int:
        """Cumulative prompt-token count (across the turn) that warrants a warning."""
        return int(self.compaction_threshold * self.effective_prompt_budget * self.max_tool_rounds)

    @property
    def token_alert_threshold(self) -> int:
        """Cumulative prompt-token count (across the turn) that warrants an alert."""
        return int(self.effective_prompt_budget * self.max_tool_rounds)


def load_harness_settings(agent_name: str) -> HarnessSettings:
    """Read an agent's loop limits and context budget from its config.

    Loop limits come from the agent's ``config.yaml`` (via
    ``utils.config_loader``); the context budget comes from the
    ``context_management`` resolver, which merges the global default with any
    per-agent override. Defaults match the values the orchestrator and
    specialist runners used before the harness existed (orchestrator:
    10 rounds / polling 15; specialists: 7 rounds / polling 30).
    """
    from utils import config_loader

    cfg = config_loader.load_agent_config(agent_name)
    is_orchestrator = agent_name == "orchestrator"
    budget = resolve_context_budget_for_agent(agent_name)
    raw_polling = cfg.get("polling_tools", [])
    return HarnessSettings(
        max_tool_rounds=int(cfg.get("max_tool_rounds", 10 if is_orchestrator else 7)),
        max_consecutive_repeats=int(cfg.get("max_consecutive_repeats", 3)),
        polling_tools=frozenset(raw_polling if isinstance(raw_polling, list) else []),
        polling_max_consecutive_repeats=int(
            cfg.get("polling_max_consecutive_repeats", 15 if is_orchestrator else 30)
        ),
        effective_prompt_budget=budget.effective_prompt_budget,
        compaction_threshold=budget.compaction_threshold,
    )


# =============================================================================
# Repetition throttle (LoopDetector)
# =============================================================================


class ToolRepeatThrottle(LoopDetector):
    """``LoopDetector`` with per-tool limits whose alert halts the turn.

    Tools in ``polling_tools`` are status checks that are expected to repeat,
    so they use ``polling_max_consecutive_repeats``; every other tool uses
    ``max_consecutive_repeats``. The alert is FATAL so ``AlertPolicy`` emits a
    ``HaltEvent``. Observers react after a call is published, so the call that
    reaches the limit still runs and the next model call is stopped.
    """

    def __init__(
        self,
        *,
        max_consecutive_repeats: int,
        polling_tools: Iterable[str] = (),
        polling_max_consecutive_repeats: int,
        name: str = THROTTLE_SOURCE,
    ) -> None:
        super().__init__(
            window_size=max(max_consecutive_repeats, polling_max_consecutive_repeats),
            repeat_threshold=max_consecutive_repeats,
            name=name,
        )
        self._default_limit = max_consecutive_repeats
        self._polling_tools = frozenset(polling_tools)
        self._polling_limit = polling_max_consecutive_repeats

    def limit_for(self, tool_name: str) -> int:
        """Return the consecutive-repeat limit that applies to ``tool_name``."""
        return self._polling_limit if tool_name in self._polling_tools else self._default_limit

    async def process(self, events: list[BaseEvent], ctx: Any) -> ObserverAlert | None:
        for envelope in events:
            call = envelope.source_event
            if not isinstance(call, ToolCallEvent):
                continue
            limit = self.limit_for(call.name)
            # LoopDetector compares the tail of its window against one
            # threshold; set it per call so each tool gets its own limit.
            self._repeat_threshold = limit
            alert = await super().process([envelope], ctx)
            if alert is None:
                continue
            label = "polling " if call.name in self._polling_tools else ""
            return ObserverAlert(
                source=self.name,
                severity=Severity.FATAL,
                message=(
                    f"[Loop exited: tool '{call.name}' called with identical "
                    f"arguments {limit} times consecutively ({label}threshold)]"
                ),
                data={**alert.data, "tool": call.name, "limit": limit},
            )
        return None


# =============================================================================
# Round cap
# =============================================================================


class ToolRoundLimit(BaseMiddleware):
    """End the turn after ``max_tool_rounds`` model calls.

    A round is one model call plus the tools it requested. AG2 builds this
    middleware once per turn, so the count starts at zero on every ``ask()``.
    The limit is recorded as a ``HaltEvent`` (source ``round-limit``) so the
    caller can tell a capped turn from a normal reply.
    """

    def __init__(self, event: BaseEvent, context: Any, *, max_tool_rounds: int) -> None:
        super().__init__(event, context)
        self._max_tool_rounds = max_tool_rounds
        self._calls = 0

    async def on_llm_call(
        self, call_next: Any, events: Sequence[BaseEvent], context: Any
    ) -> ModelResponse:
        if self._calls >= self._max_tool_rounds:
            message = (
                f"[Max tool rounds ({self._max_tool_rounds}) reached — "
                "task may be incomplete]"
            )
            log.warning("%s", message)
            await context.send(HaltEvent(reason=message, source=ROUND_LIMIT_SOURCE))
            return ModelResponse(message=ModelMessage(content=message))
        self._calls += 1
        return await call_next(events, context)


# =============================================================================
# Accounting (tool_call_traces + token_ledger)
# =============================================================================


def _result_text(result: ToolResultEvent) -> str:
    texts: list[str] = []
    for part in result.result.parts:
        content = getattr(part, "content", None)
        if isinstance(content, str):
            texts.append(content)
    return "".join(texts)


def _error_payload(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:500]}


class ToolTraceMiddleware(BaseMiddleware):
    """Write one ``tool_call_traces`` row per executed tool call.

    Wraps tool execution, so each row pairs the call's arguments with its full
    result text, the error (a raised tool error, or an
    ``{"ok": false, "error": ...}`` result), and measured latency. Stream
    observers may receive a result before its call, so tracing happens here
    rather than in an observer. Registered inside the HITL gate so a call the
    gate refused is not recorded as executed. Runs without a ``task_id`` run
    variable are not persisted.
    """

    def __init__(self, event: BaseEvent, context: Any, *, agent_name: str) -> None:
        super().__init__(event, context)
        self._agent_name = agent_name

    async def on_tool_execution(
        self, call_next: Any, event: ToolCallEvent, context: Any
    ) -> ToolResultEvent:
        task_id = _task_id_from(context)
        started_ns = time.perf_counter_ns()
        try:
            result = await call_next(event, context)
        except Exception as exc:
            if task_id is not None:
                error = _error_payload(exc)
                self._write(
                    task_id, event, json.dumps({"ok": False, "error": error}),
                    error, started_ns,
                )
            raise
        if task_id is not None:
            self._record(task_id, event, result, started_ns)
        return result

    def _record(
        self, task_id: Any, call: ToolCallEvent, event: ToolResultEvent, started_ns: int
    ) -> None:
        error: Any = None
        if isinstance(event, ToolErrorEvent):
            error = _error_payload(event.error)
            result = json.dumps({"ok": False, "error": error})
        else:
            result = _result_text(event)
            try:
                parsed = json.loads(result)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                error = parsed.get("error")
        self._write(task_id, call, result, error, started_ns)

    def _write(
        self, task_id: Any, call: ToolCallEvent, result: str, error: Any, started_ns: int
    ) -> None:
        trace_store.schedule_trace_insert(
            task_id=task_id,
            agent_name=self._agent_name,
            tool_name=call.name,
            args=_parse_arguments(call.arguments),
            result=result,
            error=error,
            latency_ms=trace_store.measure_latency_ms(started_ns),
        )


class TokenLedgerObserver(BaseObserver):
    """Write one ``token_ledger`` row per model call from ``UsageEvent``.

    Records real prompt and completion tokens, with ``utilization_pct``
    measured against the effective prompt budget. Compaction usage and usage
    forwarded from sub-agents are skipped. Runs without a ``task_id`` run
    variable are not persisted.
    """

    def __init__(
        self, *, agent_name: str, effective_prompt_budget: int,
        name: str = TOKEN_LEDGER_SOURCE,
    ) -> None:
        super().__init__(name, watch=EventWatch(UsageEvent))
        self._agent_name = agent_name
        self._budget = effective_prompt_budget
        self._seen: set[str] = set()
        self._iteration = 0

    async def process(self, events: list[BaseEvent], ctx: Any) -> ObserverAlert | None:
        task_id = _task_id_from(ctx)
        for envelope in events:
            event = envelope.source_event
            if isinstance(event, UsageEvent):
                self._record(event, task_id)
        return None

    def _record(self, event: UsageEvent, task_id: Any) -> None:
        if event.event_id in self._seen:
            return
        self._seen.add(event.event_id)
        if event.kind != "model_call" or event.task_id is not None:
            return
        self._iteration += 1
        if task_id is None:
            return
        prompt_tokens = int(event.usage.prompt_tokens or 0)
        completion = event.usage.completion_tokens
        trace_store.schedule_token_ledger_insert(
            task_id=task_id,
            agent_name=self._agent_name,
            loop_iteration=self._iteration,
            prompt_tokens=prompt_tokens,
            utilization_pct=(prompt_tokens / self._budget) if self._budget else 0.0,
            completion_tokens=int(completion) if completion is not None else None,
        )


# =============================================================================
# Composer
# =============================================================================


@dataclass(frozen=True)
class AgentHarness:
    """Native middleware, observers, and HITL hook for one agent."""

    agent_name: str
    settings: HarnessSettings

    @property
    def hitl_hook(self) -> Callable[..., Any]:
        return hitl_gates.hitl_store_hook

    def middleware(self) -> tuple[Any, ...]:
        """Fresh middleware factories, in AG2 registration order (outermost first)."""
        s = self.settings
        return (
            Middleware(AssemblerMiddleware, policies=[AlertPolicy()]),
            Middleware(HaltCheckMiddleware),
            Middleware(ToolRoundLimit, max_tool_rounds=s.max_tool_rounds),
            TokenLimiter(s.effective_prompt_budget),
            Middleware(hitl_gates.HitlGateMiddleware, agent_name=self.agent_name),
            Middleware(ToolTraceMiddleware, agent_name=self.agent_name),
        )

    def observers(self) -> tuple[Any, ...]:
        """Fresh observer instances (they keep per-run state)."""
        s = self.settings
        return (
            ToolRepeatThrottle(
                max_consecutive_repeats=s.max_consecutive_repeats,
                polling_tools=s.polling_tools,
                polling_max_consecutive_repeats=s.polling_max_consecutive_repeats,
            ),
            TokenMonitor(
                warn_threshold=s.token_warn_threshold,
                alert_threshold=s.token_alert_threshold,
            ),
            TokenLedgerObserver(
                agent_name=self.agent_name,
                effective_prompt_budget=s.effective_prompt_budget,
            ),
        )

    def agent_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``core.base_agent.create_agent``."""
        return {
            "middleware": self.middleware(),
            "observers": self.observers(),
            "hitl_hook": self.hitl_hook,
        }


def build_agent_harness(
    agent_name: str,
    *,
    settings: Optional[HarnessSettings] = None,
) -> AgentHarness:
    """Build the native harness for ``agent_name`` from its existing config."""
    resolved = settings if settings is not None else load_harness_settings(agent_name)
    log.info(
        "build_agent_harness('%s'): max_tool_rounds=%d repeats=%d polling=%d "
        "budget=%d token_warn=%d token_alert=%d",
        agent_name, resolved.max_tool_rounds, resolved.max_consecutive_repeats,
        resolved.polling_max_consecutive_repeats, resolved.effective_prompt_budget,
        resolved.token_warn_threshold, resolved.token_alert_threshold,
    )
    return AgentHarness(agent_name=agent_name, settings=resolved)
