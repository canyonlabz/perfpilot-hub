"""PerfPilot Execution Agent -- AG2 ConversableAgent factory + agent tools.

This module ships the four-file pattern's `agent.py` slot per V2 doc
§7.1: construct and return the agent on demand, with all configuration
loaded from the sibling `INSTRUCTIONS.md`, `agent_card.json`, and *one of*
`config.yaml` / `config.example.yaml`. Callers (the A2A task executor in
PBI 3.8.6, the orchestrator's `delegate_to_specialist` tool) import
`build_execution_agent()` and wrap the result.

**What this module ships (state as of PBI 3.8.3):**

- A working `ConversableAgent` factory with the real long-form system
  prompt loaded from `INSTRUCTIONS.md`.
- Per-agent `llm_provider` resolution via the framework's
  `utils.base_agent.resolve_agent_config_path()` candidate walker.
- **One agent tool wired:**
    1. `start_performance_test(test_id)`               -- PBI 3.8.3 (this commit)
- **Two tools still pending:**
    2. `wait_for_completion(run_id, ...)`              -- PBI 3.8.4
    3. `extract_test_run_artifacts(test_run_id, ...)`  -- PBI 3.8.5

`_register_tools(agent)` is now lit up and called from
`build_execution_agent()`, mirroring the orchestrator's PBI 3.7.3
cadence. Each PBI in 3.8.3-3.8.5 adds another tool there.

**Tool design conventions (apply to every agent tool in this module):**

- Module-level functions so smoke tests and PBI 3.8.6's task executor
  can import and exercise them directly without spinning up an LLM.
  The on-disk folder name is `execution-agent` (hyphenated), so callers
  load this module via `importlib.util.spec_from_file_location()` -- not
  Python's normal `from agents.execution-agent.agent import ...` (which
  would be a syntax error).
- Each tool wraps one or more MCP tools through `utils/mcp_client.py`'s
  `MCPClient` async context manager. The tool function manages the
  connection lifecycle in production; smoke tests inject a pre-built
  client via the `_*_with_client` private helper to avoid double
  setup/teardown.
- **Tools NEVER raise for tool-side failures.** They return a structured
  `{"ok": False, "error": {"type": ..., "message": ...}}` dict so the
  orchestrator (and the human ultimately) can narrate the failure
  honestly. The only exceptions a tool surfaces are programmer errors
  (PermissionError from an out-of-namespace MCP call -- which signals
  an `mcp_tools.allowed_namespaces` misconfiguration, not a runtime
  failure).
- Idempotency drives retry policy per tool:
    - `start_performance_test`        -- NOT idempotent (a retry could
      start a duplicate run). Single attempt; surface errors directly.
    - `wait_for_completion`           -- idempotent (status polling).
      Retry transparently up to 3x per `mcp-error-handling` rule.
    - `extract_test_run_artifacts`    -- idempotent per step. Each
      MCP call retries up to 3x with 5-10s back-off for API-based MCPs;
      code-based MCPs (`jmeter_analyze_jmeter_log`) never retry.

**Return-shape convention:**

  Success:  {"ok": True,  "vendor": "blazemeter", ...tool-specific fields..., "raw": <truncated MCP response>}
  Failure:  {"ok": False, "error": {"type": "<ExceptionClass>", "message": "<truncated>"}, ...echoed inputs...}

**What this module still does NOT do (deferred):**

- A2A `task_executor` dispatch (PBI 3.8.6 adds `_run_execution_agent`).
- Card flip to `available` + populated `skills[]` (PBI 3.8.7).

Heavy imports (`autogen`, `yaml`, `fastmcp` via `utils.mcp_client`) live
inside the functions that need them so this module is cheap to import
in tests / IDE indexing that do not exercise the agent.

NOTE: This module deliberately does NOT use `from __future__ import
annotations`. AG2 0.13.3 introspects tool function signatures via
pydantic's `TypeAdapter`, which cannot evaluate stringified `Annotated`
annotations. Keeping annotations as live types makes tool registration
work without per-call `.rebuild()` shenanigans (same constraint that
applies to the orchestrator's `agent.py`).
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Annotated, Any, Optional

log = logging.getLogger(__name__)

from utils.paths import get_framework_dir

AGENT_DIR = Path(__file__).resolve().parent
AGENT_NAME = "execution-agent"
FRAMEWORK_DIR = get_framework_dir()  # agent-framework/backend/

INSTRUCTIONS_PATH = AGENT_DIR / "INSTRUCTIONS.md"
AGENT_CARD_PATH = AGENT_DIR / "agent_card.json"


# =============================================================================
# Factory
# =============================================================================

def build_execution_agent() -> Any:
    """Construct the PerfPilot Execution Agent AG2 ConversableAgent.

    Resolution order for the LLM provider:
      1. Per-agent `llm_provider` block in the first existing of
         `config.yaml` (operator-side override, gitignored) or
         `config.example.yaml` (committed default).
      2. Global fallback `config/agents.yaml -> default_llm_provider`
         via `utils.llm_provider.load_default_provider_config()`.

    Returns:
        An `autogen.ConversableAgent` instance with the long-form
        `INSTRUCTIONS.md` as `system_message` and
        `human_input_mode="NEVER"` (this is a server agent).

        No tools are registered in PBI 3.8.1 -- they land in PBIs
        3.8.3 / 3.8.4 / 3.8.5 (each tool gets its own PBI per the
        F3.8 plan, mirroring the orchestrator's 3.7.3-3.7.6 cadence).

    Raises:
        FileNotFoundError: when one of the four sibling files is missing.
        Anything `LLMProvider.to_ag2_config()` raises when credentials
        are not configured.
    """
    from autogen import ConversableAgent  # type: ignore

    from services.llm_provider import LLMProvider

    system_message = _load_system_message()
    provider_config = _resolve_provider_config()
    provider = LLMProvider(provider_config)

    log.info(
        "Building %s (provider=%s, model=%s)",
        AGENT_NAME, provider.provider, provider.get_model_name(),
    )

    agent = ConversableAgent(
        name=AGENT_NAME,
        system_message=system_message,
        llm_config=provider.to_ag2_config(),
        # Server-side: never block on stdin.
        human_input_mode="NEVER",
        # Allow chain depth for future tool-call -> tool-result ->
        # follow-up reply patterns. PBI 3.8.6's A2A executor dispatches
        # to tool functions directly (bypassing the LLM loop), but the
        # agent is also reachable via the orchestrator's
        # `delegate_to_specialist` and via direct LLM interactions where
        # chain depth matters. 5 is the headroom; deeper chains likely
        # indicate a tool error loop and should terminate.
        max_consecutive_auto_reply=5,
    )

    _register_tools(agent)
    return agent


# =============================================================================
# Internal helpers
# =============================================================================

def _load_system_message() -> str:
    """Read `INSTRUCTIONS.md` as the AG2 `system_message`."""
    if not INSTRUCTIONS_PATH.exists():
        raise FileNotFoundError(
            f"Execution-agent INSTRUCTIONS.md not found at {INSTRUCTIONS_PATH}."
        )
    return INSTRUCTIONS_PATH.read_text(encoding="utf-8-sig")


def _resolve_provider_config() -> dict:
    """Return the merged LLM-provider config for the execution-agent.

    Per-agent overrides win over the global default in
    `config/agents.yaml -> default_llm_provider:`. Env credentials are
    merged in by `utils.llm_provider.merge_env_credentials` regardless
    of which YAML block sourced the behavior keys.
    """
    from services.llm_provider import (
        load_default_provider_config,
        merge_env_credentials,
    )

    agent_block = _load_per_agent_llm_block()
    if agent_block:
        log.debug(
            "Execution-agent using per-agent llm_provider override from %s",
            _resolved_config_filename(),
        )
        return merge_env_credentials(agent_block)

    log.debug("Execution-agent using default_llm_provider from agents.yaml")
    return load_default_provider_config()


def _load_per_agent_llm_block() -> Optional[dict]:
    """Parse the resolved per-agent config and return its ``llm_provider:`` block.

    Delegates YAML loading to ``config_loader.get_agent_config_section()``
    Returns None when neither candidate config file exists,
    the YAML is empty, or the ``llm_provider:`` key is absent / commented out.
    """
    from utils.config_loader import get_agent_config_section

    block = get_agent_config_section("execution-agent", "llm_provider")
    if not block or not isinstance(block, dict):
        return None
    return dict(block)


def _resolved_config_filename() -> str:
    from core.base_agent import resolve_agent_config_path

    path = resolve_agent_config_path(AGENT_DIR)
    return path.name if path else "<none>"


# =============================================================================
# Tool registration
# =============================================================================

def _register_tools(agent: Any) -> None:
    """Wire the F3.8 agent tools onto the ConversableAgent.

    Both decorators are required for each tool:
      - `register_for_llm(...)` advertises the tool in the LLM's tool
        catalog so the model can decide to call it during a generate_reply
        loop (used for orchestrator -> execution-agent chains, future
        LLM-driven flows).
      - `register_for_execution()` tells AG2 that THIS agent is the one
        that actually runs the function (vs. a separate executor agent).

    AG2 0.13.3 uses `api_style='tool'` by default, which is what we want
    (modern OpenAI tools API, not the deprecated function-calling API).
    The same decoration pattern works for both sync and async tool
    functions; AG2 awaits the coroutine when `register_for_execution()`
    invokes it.

    PBIs 3.8.3 / 3.8.4 / 3.8.5 wire `start_performance_test`,
    `wait_for_completion`, and `extract_test_run_artifacts` respectively.
    """
    agent.register_for_llm(
        name="start_performance_test",
        description=(
            "Kick off a performance test run against a test definition that "
            "ALREADY EXISTS in the load-testing tool of record (BlazeMeter "
            "in F3.8; vendor-agnostic by name for future Gatling / Locust / "
            "k6 expansion). Takes the existing test_id and returns the "
            "freshly-minted run_id on success. Starting a test is NOT "
            "idempotent -- this tool makes a single attempt and surfaces "
            "any error directly; do NOT retry on transient failure without "
            "human or orchestrator approval. Always returns a structured "
            "dict ({ok: True, run_id, ...} on success or {ok: False, "
            "error: {...}} on failure); NEVER raises."
        ),
    )(start_performance_test)
    agent.register_for_execution()(start_performance_test)

    agent.register_for_llm(
        name="wait_for_completion",
        description=(
            "Block until the given run_id reaches a terminal state in the "
            "load-testing tool, polling the underlying MCP status endpoint at "
            "`poll_interval_seconds` (default 60s) until `timeout_seconds` "
            "(default 300s) elapses. Returns ok=True on terminal completion "
            "(status='ENDED', timed_out=False), ok=True on test-side failure "
            "(has_error=True with BlazeMeter's reported error string), ok=True "
            "on wait timeout (timed_out=True with the last observed status), "
            "and ok=False ONLY when 3 consecutive MCP polls fail (transient "
            "single-poll failures are absorbed by the natural polling cadence). "
            "Status polling is idempotent and safe to call repeatedly. NEVER "
            "raises."
        ),
    )(wait_for_completion)
    agent.register_for_execution()(wait_for_completion)

    agent.register_for_llm(
        name="extract_test_run_artifacts",
        description=(
            "Execute the 6-step BlazeMeter extractor recipe (see INSTRUCTIONS.md "
            "§4) against a completed run and return the canonical Return Format "
            "JSON. Steps 1-3 (get_run_results / get_artifacts_path / "
            "process_session_artifacts) are CRITICAL: any failure short-circuits "
            "the recipe and returns status='failed'. Steps 4-6 "
            "(get_public_report / get_aggregate_report / analyze_jmeter_log) are "
            "IMPORTANT: failures are recorded but the recipe continues, returning "
            "status='partial' if any IMPORTANT step failed. All BlazeMeter MCP "
            "tools retry up to 3x with back-off on transient errors; "
            "jmeter_analyze_jmeter_log is code-based and NEVER retries per "
            "project rule. Filesystem is NEVER touched -- validation block is "
            "derived from MCP response payloads only. NEVER raises."
        ),
    )(extract_test_run_artifacts)
    agent.register_for_execution()(extract_test_run_artifacts)

    agent.register_for_llm(
        name="provision_performance_test",
        description=(
            "Provision a NEW BlazeMeter test for a freshly-generated JMX. "
            "Resolves the workspace/project via the framework env_resolver "
            "(environment name -> BlazeMeter target) and any A2A overrides, "
            "then calls blazemeter_resolve_project -> blazemeter_create_test -> "
            "blazemeter_upload_test_files in sequence. ALWAYS runs even when "
            "smoke_status='FAIL' (per user contract), but flags "
            "warning='smoke_failed' on the return so the orchestrator can "
            "surface a 'not recommended to run' message. Never uploads to "
            "shared folders (that is a separate tool). Never raises."
        ),
    )(provision_performance_test)
    agent.register_for_execution()(provision_performance_test)


# =============================================================================
# Tool 1 (PBI 3.8.3): start_performance_test
# =============================================================================

# BlazeMeter MCP's `start_test` tool returns a formatted string of the form
# "Run started. Run ID: 12345" (see blazemeter-mcp/services/blazemeter_api.py
# `run_test()`). The wrapper regex-extracts the run_id. Pattern is permissive
# enough to survive minor wording tweaks ("Run id:", " ID:1234") but anchors
# on the "ID:" token so it does not false-match on test_id mentions in the
# response.
_RUN_ID_PATTERN = re.compile(r"Run\s+ID\s*:\s*([A-Za-z0-9_-]+)", re.IGNORECASE)


async def start_performance_test(
    test_id: Annotated[
        str,
        "Identifier of an EXISTING test definition in the load-testing tool "
        "(BlazeMeter test ID, e.g. '12345678'). The test artifact "
        "(JMX / .yaml / recorded scenario) must already be uploaded; this "
        "agent does NOT upload JMX scripts.",
    ],
) -> dict:
    """Kick off a performance test run for a pre-existing test definition.

    Vendor-agnostic by name; in F3.8 wraps the BlazeMeter MCP tool
    `blazemeter_start_test(test_id)` 1:1. Future feature work will swap
    the underlying MCP tool based on a per-test vendor config (Gatling /
    Locust / k6), keeping this agent-tool name stable.

    **Idempotency contract.** Starting a test is NOT idempotent:
    re-issuing this call after a timeout could create a duplicate run
    on the load-testing side. This function therefore makes a SINGLE
    attempt against the MCP and surfaces any error directly -- no
    silent retry. The orchestrator (or human, via HITL) decides
    whether to re-prompt.

    **Connection lifecycle.** Builds and tears down its own `MCPClient`
    in the default invocation path. Smoke tests inject a pre-built
    client via the `_start_performance_test_with_client` helper to
    avoid double connection overhead.

    Args:
        test_id: Identifier of an existing test definition in the
            load-testing tool of record. Stripped of surrounding
            whitespace; empty string returns an `InvalidInput` error.

    Returns:
        Always a dict. **NEVER raises** for tool-side failures.

        On success::

            {
                "ok": True,
                "vendor": "blazemeter",
                "test_id": "<echoed>",
                "run_id": "<minted-by-vendor>",
                "raw": "<truncated MCP response>",
            }

        On failure::

            {
                "ok": False,
                "error": {
                    "type": "<exception class>",
                    "message": "<truncated>",
                },
                "test_id": "<echoed>",
            }
    """
    test_id = (test_id or "").strip()
    if not test_id:
        return _invalid_input_error(
            test_id="",
            message="test_id must be a non-empty string",
        )

    from services.mcp_client import MCPClient, build_client_config

    config = build_client_config(["blazemeter", "jmeter"])
    try:
        async with MCPClient(config) as client:
            return await _start_performance_test_with_client(test_id, client)
    except Exception as e:
        return _make_error(test_id, e)


async def _start_performance_test_with_client(
    test_id: str,
    client: Any,
) -> dict:
    """Inner implementation: single MCP call + structured normalization.

    Exposed (with the leading underscore) so smoke tests can pass a
    pre-connected `MCPClient` and avoid the connection-management
    overhead of repeatedly opening/closing the FastMCP transport.

    A single attempt -- the function never retries, since
    `blazemeter_start_test` is not idempotent.

    Args:
        test_id: Already-validated test ID (non-empty, stripped).
        client: An open `utils.mcp_client.MCPClient`. The caller owns
            the lifecycle.

    Returns:
        The same `{ok, ...}` dict shape documented on
        `start_performance_test`.
    """
    try:
        result = await client.call_tool(
            "blazemeter_start_test", {"test_id": test_id}
        )
    except Exception as e:
        return _make_error(test_id, e)

    raw = _extract_text_or_data(result)
    run_id = _parse_run_id(raw)
    if run_id is None:
        return {
            "ok": False,
            "error": {
                "type": "MalformedResponse",
                "message": (
                    "MCP returned a response but no run_id could be parsed. "
                    "Check the blazemeter-mcp response shape."
                ),
                "raw": _truncate(raw),
            },
            "test_id": test_id,
        }
    return {
        "ok": True,
        "vendor": "blazemeter",
        "test_id": test_id,
        "run_id": str(run_id),
        "raw": _truncate(raw),
    }


# =============================================================================
# Tool helpers (shared across PBIs 3.8.3 / 3.8.4 / 3.8.5)
# =============================================================================

def _parse_run_id(raw: Any) -> Optional[str]:
    """Extract the run_id from the MCP response, handling multiple shapes.

    Handles three shapes:
      - dict with `run_id` / `runId` / `id` key -> use directly
      - formatted string like 'Run started. Run ID: 12345' -> regex extract
      - anything else -> None
    """
    if isinstance(raw, dict):
        for key in ("run_id", "runId", "id"):
            value = raw.get(key)
            if value not in (None, ""):
                return str(value)
    if isinstance(raw, str):
        m = _RUN_ID_PATTERN.search(raw)
        if m:
            return m.group(1)
    return None


def _extract_text_or_data(result: Any) -> Any:
    """Return `.data` if present, else `.content[0].text`, else the raw object.

    FastMCP's `CallToolResult` populates `.data` when the tool returns a
    structured value (dict / dataclass / list of primitives) and
    `.content[0].text` when the tool returns a plain string. This helper
    handles both transparently.
    """
    data = getattr(result, "data", None)
    if data is not None:
        return data
    content = getattr(result, "content", None) or []
    if content:
        text = getattr(content[0], "text", None)
        if text is not None:
            return text
    return result


def _truncate(value: Any, limit: int = 500) -> str:
    """Return `str(value)` truncated to a UI-friendly length."""
    s = str(value)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _make_error(test_id: str, e: BaseException) -> dict:
    """Build the canonical `{ok: False, error: {...}}` dict from an exception."""
    return {
        "ok": False,
        "error": {
            "type": type(e).__name__,
            "message": _truncate(e),
        },
        "test_id": test_id,
    }


def _invalid_input_error(test_id: str, message: str) -> dict:
    """Build the canonical `InvalidInput` error dict (used for client-side validation)."""
    return {
        "ok": False,
        "error": {
            "type": "InvalidInput",
            "message": message,
        },
        "test_id": test_id,
    }


# =============================================================================
# Tool 2 (PBI 3.8.4): wait_for_completion
# =============================================================================

# Status-vocabulary constants. Single source of truth so the parser, the
# terminal-state classifier, and the smoke tests all agree on what
# "terminal" means. BlazeMeter's API documents `ENDED` as the natural
# completion state and `FAILED` / `ERROR` / `ABORTED` as the terminal
# failure states (see blazemeter-mcp/services/blazemeter_api.py
# `get_test_status` for the `has_error` bool's exact criteria).
TERMINAL_SUCCESS_STATUS = "ENDED"
TERMINAL_FAILURE_STATUSES = frozenset({"FAILED", "ERROR", "ABORTED"})

# Safety floor for poll interval. The user-facing default is 60s (see
# `wait_for_completion` signature) but smoke tests need to drive the
# loop at sub-second intervals; this floor prevents foot-gun zeros.
_MIN_POLL_INTERVAL_SECONDS = 0.05

# Hard error threshold: three consecutive polls failing with an exception
# (network, auth, etc.) is treated as a wait-side failure and aborts the
# loop. Transient single-poll failures are absorbed because the natural
# 60-second polling cadence provides better back-off than blind retries.
_MAX_CONSECUTIVE_POLL_FAILURES = 3


async def wait_for_completion(
    run_id: Annotated[
        str,
        "BlazeMeter run_id minted by `start_performance_test` (also called "
        "`master_id` in some BlazeMeter API endpoints). Same value carried "
        "through the rest of the F3.8 pipeline as `test_run_id`.",
    ],
    *,
    poll_interval_seconds: Annotated[
        float,
        "Seconds between status polls. Default 60.0 to stay well under "
        "BlazeMeter's API rate limits; status updates are not granular "
        "enough to benefit from tighter polling.",
    ] = 60.0,
    timeout_seconds: Annotated[
        float,
        "Maximum seconds to wait for terminal state. Default 300.0 (5 "
        "minutes) -- appropriate for short smoke tests; production "
        "callers should raise this for longer real workloads.",
    ] = 300.0,
    on_progress: Optional[Any] = None,
) -> dict:
    """Block until `run_id` reaches a terminal state, polling at intervals.

    In F3.8 wraps the BlazeMeter MCP tool `blazemeter_check_test_status(run_id)`
    in an `asyncio.sleep` loop. The status polling is idempotent, so transient
    per-poll MCP failures are absorbed silently (counted, but not raised) and
    only escalate to a hard error after 3 consecutive failures -- the natural
    60-second polling cadence acts as the back-off.

    **Return-shape contract** (matches `INSTRUCTIONS.md` §3.2):

    Terminal success (BlazeMeter reported `status: 'ENDED'`)::

        {"ok": True, "run_id": ..., "status": "ENDED", "has_error": False,
         "error": None, "timed_out": False, "polls": <int>, "elapsed_seconds": <float>,
         "last_response": "<truncated>"}

    Terminal failure (BlazeMeter reported `has_error: True` or one of
    FAILED / ERROR / ABORTED). The wait OPERATION succeeded (we got a
    deterministic verdict), but the test itself failed -- `ok: True`
    reflects the wait's success; `has_error: True` reflects the test's
    failure. The orchestrator decides whether to still attempt extraction::

        {"ok": True, "run_id": ..., "status": "FAILED" | "ERROR" | "ABORTED" | ...,
         "has_error": True, "error": "<BlazeMeter's error string or object>",
         "timed_out": False, "polls": <int>, "elapsed_seconds": <float>,
         "last_response": "<truncated>"}

    Timeout (deadline reached before BlazeMeter reached terminal)::

        {"ok": True, "run_id": ..., "status": "<last observed, e.g. RUNNING>",
         "has_error": False, "error": None, "timed_out": True, "polls": <int>,
         "elapsed_seconds": <float>, "last_response": "<truncated>",
         "notes": "Reached timeout_seconds=... before terminal state."}

    Hard error (3 consecutive MCP polls raised; or invalid input). Only
    case where `ok: False`::

        {"ok": False, "run_id": ..., "error": {"type": ..., "message": ...,
         "phase": "mcp-call" | "client-setup" | "invalid-input",
         "consecutive_failures": <int, when applicable>}, "polls": <int>}

    **Never raises** for tool-side failures.

    Args:
        run_id: BlazeMeter run identifier. Stripped of surrounding
            whitespace; empty string returns an `InvalidInput` error.
        poll_interval_seconds: Seconds between status polls (default 60).
            Clamped to a floor of 0.05s for smoke-test ergonomics.
        timeout_seconds: Maximum total wait window (default 300). Clamped
            to non-negative values; zero means "single-shot status check".

    Returns:
        A dict as documented above. The shape varies by branch; callers
        should switch on `ok` first, then `timed_out` / `has_error`.
    """
    run_id = (run_id or "").strip()
    if not run_id:
        return {
            "ok": False,
            "run_id": "",
            "error": {
                "type": "InvalidInput",
                "message": "run_id must be a non-empty string",
                "phase": "invalid-input",
            },
            "polls": 0,
        }

    poll = max(_MIN_POLL_INTERVAL_SECONDS, float(poll_interval_seconds))
    timeout = max(0.0, float(timeout_seconds))

    from services.mcp_client import MCPClient, build_client_config

    config = build_client_config(["blazemeter", "jmeter"])
    try:
        async with MCPClient(config) as client:
            return await _wait_for_completion_with_client(
                run_id,
                client,
                poll_interval_seconds=poll,
                timeout_seconds=timeout,
                on_progress=on_progress,
            )
    except Exception as e:
        return {
            "ok": False,
            "run_id": run_id,
            "error": {
                "type": type(e).__name__,
                "message": _truncate(e),
                "phase": "client-setup",
            },
            "polls": 0,
        }


async def _wait_for_completion_with_client(
    run_id: str,
    client: Any,
    *,
    poll_interval_seconds: float = 60.0,
    timeout_seconds: float = 300.0,
    on_progress: Optional[Any] = None,
) -> dict:
    """Inner implementation: status polling loop over a caller-provided client.

    Exposed (underscored) so smoke tests can inject a `_FakeMCPClient`
    or pre-connected real `MCPClient` and exercise the loop without
    re-opening the FastMCP transport per call.

    Args:
        run_id: Pre-validated (non-empty, stripped) run identifier.
        client: An object exposing the async `call_tool(name, args)`
            method -- either `utils.mcp_client.MCPClient` in production
            or a test fake. The caller owns the lifecycle.
        poll_interval_seconds: Already clamped to >= 0.05s.
        timeout_seconds: Already clamped to >= 0.0s.
        on_progress: Optional async callable(str) invoked on each poll
            iteration with a human-readable progress message. Used by
            the task executor to broadcast SSE events. When None, no
            progress callbacks are issued.

    Returns:
        The same `{ok, ...}` dict shape documented on `wait_for_completion`.
    """
    deadline = time.monotonic() + timeout_seconds
    start = time.monotonic()

    polls = 0
    consecutive_failures = 0
    last_status: Optional[str] = None
    last_response: Any = None

    while True:
        polls += 1
        try:
            result = await client.call_tool(
                "blazemeter_check_test_status", {"run_id": run_id}
            )
            payload = _extract_text_or_data(result)
            consecutive_failures = 0
            last_response = payload
        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_POLL_FAILURES:
                return {
                    "ok": False,
                    "run_id": run_id,
                    "error": {
                        "type": type(e).__name__,
                        "message": _truncate(e),
                        "phase": "mcp-call",
                        "consecutive_failures": consecutive_failures,
                    },
                    "polls": polls,
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    "last_response": _truncate(last_response) if last_response is not None else None,
                }
            payload = None  # absorbed transient failure; fall through to sleep

        # Classify a successful poll's payload as terminal-success,
        # terminal-failure, or still-running.
        if isinstance(payload, dict):
            status_raw = payload.get("status")
            status_str = str(status_raw) if status_raw is not None else None
            status_upper = status_str.upper() if status_str else None
            if status_str:
                last_status = status_str
            has_error = bool(payload.get("has_error"))
            err_field = payload.get("error")

            if (
                status_upper == TERMINAL_SUCCESS_STATUS
                and not has_error
            ):
                return {
                    "ok": True,
                    "run_id": run_id,
                    "status": last_status,
                    "has_error": False,
                    "error": None,
                    "timed_out": False,
                    "polls": polls,
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    "last_response": _truncate(payload),
                }

            if has_error or (
                status_upper is not None
                and status_upper in TERMINAL_FAILURE_STATUSES
            ):
                return {
                    "ok": True,
                    "run_id": run_id,
                    "status": last_status,
                    "has_error": True,
                    "error": err_field,
                    "timed_out": False,
                    "polls": polls,
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    "last_response": _truncate(payload),
                }

        # Not terminal yet. Check timeout before sleeping so we never
        # over-sleep the deadline. A single-shot mode (timeout_seconds=0)
        # falls through here on the first iteration and returns timed_out.
        max_polls = int(timeout_seconds / poll_interval_seconds) if poll_interval_seconds > 0 else polls
        if on_progress:
            status_label = last_status or "unknown"
            elapsed = round(time.monotonic() - start, 1)
            try:
                await on_progress(
                    f"Polling Test Run... status: {status_label} "
                    f"(poll {polls}/{max_polls}, elapsed: {elapsed}s)"
                )
            except Exception:
                pass  # progress callback failure must not break the poll loop

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": True,
                "run_id": run_id,
                "status": last_status,
                "has_error": False,
                "error": None,
                "timed_out": True,
                "polls": polls,
                "elapsed_seconds": round(time.monotonic() - start, 2),
                "last_response": _truncate(last_response) if last_response is not None else None,
                "notes": (
                    f"Reached timeout_seconds={timeout_seconds} before "
                    f"terminal state. Last status: {last_status!r}."
                ),
            }

        await asyncio.sleep(min(poll_interval_seconds, remaining))


# =============================================================================
# Tool 3 (PBI 3.8.5): extract_test_run_artifacts
# =============================================================================

# Retry budget for BlazeMeter MCP calls (API-based; safe to retry per
# `mcp-error-handling` rule). The recipe's API-based steps are 1, 2, 3, 4, 5;
# Step 3 has its own built-in retry inside the MCP, but we still retry the
# transport-level call on exception. Step 6 (`jmeter_analyze_jmeter_log`) is
# code-based and NEVER retries per the same rule.
_BLAZEMETER_STEP_MAX_ATTEMPTS = 3
_BLAZEMETER_STEP_RETRY_DELAY_SECONDS = 5.0  # Per project rule: 5-10s between retries

# Step labels (used as keys in the canonical Return Format JSON's `steps`
# block AND in `_mark_remaining_steps_skipped` for short-circuit aborts).
# Order is significant: matches the §4 recipe order in INSTRUCTIONS.md.
_RECIPE_STEPS: tuple[str, ...] = (
    "get_run_results",
    "get_artifacts_path",
    "process_session_artifacts",
    "get_public_report",
    "get_aggregate_report",
    "analyze_jmeter_log",
)
_CRITICAL_STEPS: frozenset[str] = frozenset(
    {"get_run_results", "get_artifacts_path", "process_session_artifacts"}
)

# Parse helpers for `blazemeter_get_run_results` (which returns a formatted
# multi-line STRING -- see blazemeter-mcp/services/blazemeter_api.py
# `get_results_summary`). The patterns anchor on labels rather than position
# to survive minor formatting tweaks.
_START_TIME_PATTERN = re.compile(r"^\s*Start Time:\s*(.+?)\s*$", re.MULTILINE)
_END_TIME_PATTERN = re.compile(r"^\s*End Time:\s*(.+?)\s*$", re.MULTILINE)
_SESSION_ID_PATTERN = re.compile(r"^\s*Session ID:\s*(\[.*?\])\s*$", re.MULTILINE)


async def extract_test_run_artifacts(
    test_run_id: Annotated[
        str,
        "PerfPilot artifact-folder key for the completed run. Equals the "
        "BlazeMeter `run_id` minted by `start_performance_test`; the two "
        "values are 1:1.",
    ],
    test_name: Annotated[
        Optional[str],
        "Optional human-friendly label for the run. Logged in the return "
        "JSON's `notes` field for downstream context; not part of the "
        "canonical schema and may be None.",
    ] = None,
    on_progress: Optional[Any] = None,
) -> dict:
    """Execute the 6-step BlazeMeter extractor recipe over MCP tools.

    Returns the canonical Return Format JSON documented in
    `INSTRUCTIONS.md` §6, populated step-by-step from the responses of
    six underlying MCP tools:

      Step 1 (CRITICAL): `blazemeter_get_run_results(run_id=test_run_id)`
        -> captures `start_time`, `end_time`, `sessionsId`.
      Step 2 (CRITICAL): `blazemeter_get_artifacts_path()`
        -> captures `mcp_artifacts_base_path` (DIAGNOSTIC ONLY; the agent
        never reads or writes this path).
      Step 3 (CRITICAL): `blazemeter_process_session_artifacts(run_id, sessions_id)`
        -> downloads/extracts/combines per-load-generator artifacts.
      Step 4 (IMPORTANT): `blazemeter_get_public_report(run_id)`
        -> captures `public_url` for the reporting-agent's Confluence link.
      Step 5 (IMPORTANT): `blazemeter_get_aggregate_report(run_id)`
        -> writes the aggregate CSV consumed by analysis-agent's SLA pass.
      Step 6 (IMPORTANT): `jmeter_analyze_jmeter_log(test_run_id, log_source="blazemeter")`
        -> writes error-analysis files for analysis-agent's attribution pass.

    **Severity model (INSTRUCTIONS.md §7.1):**
      - Any CRITICAL step failing short-circuits the recipe and returns
        `status: "failed"`; remaining steps are marked `"skipped"`.
      - IMPORTANT step failures are recorded on the step but the recipe
        continues. If any IMPORTANT step fails (and all CRITICAL steps
        succeeded), the final `status` is `"partial"`.
      - All six steps succeeding yields `status: "success"`.

    **Retry policy (project `mcp-error-handling` rule):**
      - BlazeMeter MCP tools (Steps 1-5): retry up to 3 times on
        exception with 5-second back-off between attempts.
      - JMeter MCP tool (Step 6): NEVER retries (code-based MCP).

    **Filesystem invariant:** The agent never inspects the filesystem.
    The Step 7 `validation` block is derived from MCP response payloads
    alone; expected-file-presence reflects each step's reported status,
    not on-disk reality.

    Args:
        test_run_id: PerfPilot artifact-folder key (equals BlazeMeter
            `run_id`). Stripped of surrounding whitespace; empty string
            short-circuits to a failed extraction.
        test_name: Optional informational label, logged in `notes`.

    Returns:
        The canonical Return Format JSON dict (see INSTRUCTIONS.md §6).
        Always returns a dict; NEVER raises for tool-side failures.
    """
    test_run_id = (test_run_id or "").strip()
    if not test_run_id:
        result = _new_extraction_result("")
        for step in _RECIPE_STEPS:
            result["steps"][step]["status"] = "skipped"
        result["status"] = "failed"
        result["notes"] = "test_run_id must be a non-empty string"
        return result

    from services.mcp_client import MCPClient, build_client_config

    config = build_client_config(["blazemeter", "jmeter"])
    try:
        async with MCPClient(config) as client:
            return await _extract_test_run_artifacts_with_client(
                test_run_id, test_name, client, on_progress=on_progress
            )
    except Exception as e:
        result = _new_extraction_result(test_run_id)
        for step in _RECIPE_STEPS:
            result["steps"][step]["status"] = "skipped"
        result["status"] = "failed"
        result["notes"] = (
            f"MCPClient setup failed: {type(e).__name__}: {_truncate(e, 240)}"
        )
        return result


async def _extract_test_run_artifacts_with_client(
    test_run_id: str,
    test_name: Optional[str],
    client: Any,
    *,
    _retry_delay_seconds: float = _BLAZEMETER_STEP_RETRY_DELAY_SECONDS,
    on_progress: Optional[Any] = None,
) -> dict:
    """Inner implementation: 6-step recipe over a caller-provided MCP client.

    Exposed (underscored) so smoke tests can inject `_FakeMCPClient` and
    drive each step with scripted responses without standing up a real
    gateway-mcp + BlazeMeter API.

    Args:
        test_run_id: Pre-validated (non-empty, stripped) artifact-folder key.
        test_name: Optional human-friendly label; logged in `notes`.
        client: Any object exposing async `call_tool(name, args)`. Caller
            owns the lifecycle.
        _retry_delay_seconds: Sleep between BlazeMeter MCP retries.
            Defaults to 5s per project rule; smoke tests override to 0
            for sub-second wall budgets.
        on_progress: Optional async callable(str) invoked before each
            recipe step with a human-readable progress message. Used by
            the task executor to broadcast SSE events.

    Returns:
        The canonical Return Format JSON dict (see INSTRUCTIONS.md §6).
    """
    result = _new_extraction_result(test_run_id)
    notes: list[str] = []
    if test_name:
        notes.append(f"test_name={test_name!r}")

    async def _emit(msg: str) -> None:
        if on_progress:
            try:
                await on_progress(msg)
            except Exception:
                pass

    # ---- Step 1 (CRITICAL): get_run_results -----------------------------
    await _emit("Step 1/6: get_run_results (CRITICAL)")
    step1 = await _call_blazemeter_with_retries(
        client,
        "blazemeter_get_run_results",
        {"run_id": test_run_id},
        retry_delay_seconds=_retry_delay_seconds,
    )
    if not step1["ok"]:
        result["steps"]["get_run_results"] = {
            "status": "failed",
            "error": step1["error"],
        }
        notes.append(f"Step 1 (get_run_results) failed: {step1['error']}")
        return _finalize_critical_failure(result, notes, after_step="get_run_results")

    parsed = _parse_get_run_results(step1["raw"])
    result["start_time"] = parsed["start_time"]
    result["end_time"] = parsed["end_time"]
    sessions_id = parsed["sessions_id"]
    if parsed.get("warnings"):
        notes.extend(parsed["warnings"])

    if not sessions_id:
        result["steps"]["get_run_results"] = {
            "status": "failed",
            "error": "Response contained no sessions_id; cannot run Step 3",
        }
        notes.append(
            "Step 1 (get_run_results) succeeded but returned no sessions_id; "
            "Step 3 requires at least one load-generator session ID."
        )
        return _finalize_critical_failure(result, notes, after_step="get_run_results")

    result["steps"]["get_run_results"] = {"status": "success", "error": None}

    raw1 = step1["raw"]
    if isinstance(raw1, dict):
        kpis = result["kpis"]
        kpis["test_name"] = raw1.get("test_name")
        kpis["duration_seconds"] = raw1.get("duration_seconds")
        kpis["max_virtual_users"] = raw1.get("max_virtual_users")
        kpis["samples_total"] = raw1.get("samples_total")
        kpis["pass_count"] = raw1.get("pass_count")
        kpis["fail_count"] = raw1.get("fail_count")
        kpis["error_count"] = raw1.get("error_count")
        kpis["min_response_time_ms"] = raw1.get("response_time_min_ms")
        kpis["max_response_time_ms"] = raw1.get("response_time_max_ms")
        kpis["avg_response_time_ms"] = raw1.get("response_time_avg_ms")
        kpis["p90_response_time_ms"] = raw1.get("response_time_p90_ms")

    # ---- Step 2 (CRITICAL): get_artifacts_path --------------------------
    await _emit("Step 2/6: get_artifacts_path (CRITICAL)")
    step2 = await _call_blazemeter_with_retries(
        client,
        "blazemeter_get_artifacts_path",
        {},
        retry_delay_seconds=_retry_delay_seconds,
    )
    if not step2["ok"]:
        result["steps"]["get_artifacts_path"] = {
            "status": "failed",
            "error": step2["error"],
        }
        notes.append(f"Step 2 (get_artifacts_path) failed: {step2['error']}")
        return _finalize_critical_failure(result, notes, after_step="get_artifacts_path")

    base_path = _normalize_artifacts_base(step2["raw"])
    if not base_path:
        result["steps"]["get_artifacts_path"] = {
            "status": "failed",
            "error": (
                "blazemeter_get_artifacts_path returned an empty or invalid "
                f"path string: {step2['raw']!r}"
            ),
        }
        notes.append("Step 2 (get_artifacts_path) returned empty/invalid path")
        return _finalize_critical_failure(result, notes, after_step="get_artifacts_path")

    result["mcp_artifacts_base_path"] = base_path
    result["artifacts_path"] = f"{base_path.rstrip('/')}/{test_run_id}/blazemeter/"
    result["steps"]["get_artifacts_path"] = {"status": "success", "error": None}

    # ---- Step 3 (CRITICAL): process_session_artifacts -------------------
    # NOTE: status="partial" from the MCP is a SOFT success at this layer
    # -- the spec (INSTRUCTIONS.md line 377) allows the step to surface as
    # "partial" while still gating CRITICAL pass-through.
    await _emit("Step 3/6: process_session_artifacts (CRITICAL)")
    step3 = await _call_blazemeter_with_retries(
        client,
        "blazemeter_process_session_artifacts",
        {"run_id": test_run_id, "sessions_id": sessions_id},
        retry_delay_seconds=_retry_delay_seconds,
    )
    if not step3["ok"]:
        result["steps"]["process_session_artifacts"] = {
            "status": "failed",
            "retries": 0,
            "error": step3["error"],
        }
        notes.append(f"Step 3 (process_session_artifacts) failed: {step3['error']}")
        return _finalize_critical_failure(result, notes, after_step="process_session_artifacts")

    s3_payload = step3["raw"]
    if isinstance(s3_payload, dict):
        s3_status = s3_payload.get("status", "unknown")
    else:
        s3_status = "unknown"

    if s3_status == "success":
        result["steps"]["process_session_artifacts"] = {
            "status": "success",
            "retries": 0,
            "error": None,
        }
    elif s3_status == "partial":
        # Soft success: CRITICAL gate passes, but the sub-step retains
        # "partial" to surface the degraded outcome. Spec line 377 allows
        # this; the §6 status-driving rule treats CRITICAL steps as
        # success-or-not (binary), so we still proceed to Step 4.
        result["steps"]["process_session_artifacts"] = {
            "status": "partial",
            "retries": 0,
            "error": None,
        }
        completed = s3_payload.get("completed_sessions") if isinstance(s3_payload, dict) else None
        total = s3_payload.get("total_sessions") if isinstance(s3_payload, dict) else None
        notes.append(
            f"Step 3 (process_session_artifacts) partial: "
            f"{completed}/{total} load generators completed"
        )
    else:
        # status="error" or unknown -> all sessions failed -> CRITICAL fail.
        err = (
            s3_payload.get("error")
            if isinstance(s3_payload, dict) and s3_payload.get("error")
            else f"process_session_artifacts returned status={s3_status!r}"
        )
        result["steps"]["process_session_artifacts"] = {
            "status": "failed",
            "retries": 0,
            "error": err,
        }
        notes.append(f"Step 3 (process_session_artifacts) failed: {err}")
        return _finalize_critical_failure(result, notes, after_step="process_session_artifacts")

    # ---- Step 4 (IMPORTANT): get_public_report --------------------------
    important_failures = 0

    await _emit("Step 4/6: get_public_report (IMPORTANT)")
    step4 = await _call_blazemeter_with_retries(
        client,
        "blazemeter_get_public_report",
        {"run_id": test_run_id},
        retry_delay_seconds=_retry_delay_seconds,
    )
    if step4["ok"] and isinstance(step4["raw"], dict):
        payload = step4["raw"]
        public_url = payload.get("public_url")
        err_field = payload.get("error")
        if public_url and not err_field:
            result["steps"]["get_public_report"] = {
                "status": "success",
                "public_url": public_url,
                "error": None,
            }
        else:
            err = err_field or "public_url missing from response"
            result["steps"]["get_public_report"] = {
                "status": "failed",
                "public_url": None,
                "error": _truncate(err, 240),
            }
            notes.append(f"Step 4 (get_public_report) failed: {err}")
            important_failures += 1
    else:
        err = step4["error"] if not step4["ok"] else "non-dict response from get_public_report"
        result["steps"]["get_public_report"] = {
            "status": "failed",
            "public_url": None,
            "error": _truncate(err, 240),
        }
        notes.append(f"Step 4 (get_public_report) failed: {err}")
        important_failures += 1

    # ---- Step 5 (IMPORTANT): get_aggregate_report -----------------------
    await _emit("Step 5/6: get_aggregate_report (IMPORTANT)")
    step5 = await _call_blazemeter_with_retries(
        client,
        "blazemeter_get_aggregate_report",
        {"run_id": test_run_id},
        retry_delay_seconds=_retry_delay_seconds,
    )
    if step5["ok"] and isinstance(step5["raw"], dict):
        payload = step5["raw"]
        s5_status = payload.get("status")
        if s5_status == "success":
            result["steps"]["get_aggregate_report"] = {"status": "success", "error": None}
            agg = payload.get("aggregate_summary")
            if isinstance(agg, dict):
                kpis = result["kpis"]
                kpis["avg_response_time_ms"] = agg.get("avgResponseTime")
                kpis["p90_response_time_ms"] = agg.get("90line")
                kpis["median_response_time_ms"] = agg.get("medianResponseTime")
                kpis["min_response_time_ms"] = agg.get("minResponseTime")
                kpis["max_response_time_ms"] = agg.get("maxResponseTime")
                kpis["avg_throughput"] = agg.get("avgThroughput")
                kpis["error_rate"] = agg.get("errorsRate")
                kpis["avg_bandwidth_bytes"] = agg.get("avgBytes")
                kpis["avg_latency_ms"] = agg.get("avgLatency")
                if agg.get("samples") is not None:
                    kpis["samples_total"] = agg.get("samples")
        else:
            err = payload.get("error") or f"unexpected status={s5_status!r}"
            result["steps"]["get_aggregate_report"] = {
                "status": "failed",
                "error": _truncate(err, 240),
            }
            notes.append(f"Step 5 (get_aggregate_report) failed: {err}")
            important_failures += 1
    else:
        err = step5["error"] if not step5["ok"] else "non-dict response from get_aggregate_report"
        result["steps"]["get_aggregate_report"] = {
            "status": "failed",
            "error": _truncate(err, 240),
        }
        notes.append(f"Step 5 (get_aggregate_report) failed: {err}")
        important_failures += 1

    # ---- Step 6 (IMPORTANT, NO RETRY): analyze_jmeter_log ---------------
    # Code-based MCP tool. Per project `mcp-error-handling` rule: do NOT
    # retry on failure; a retry will not change a deterministic outcome.
    await _emit("Step 6/6: analyze_jmeter_log (IMPORTANT, no retry)")
    step6 = await _call_mcp_once(
        client,
        "jmeter_analyze_jmeter_log",
        {"test_run_id": test_run_id, "log_source": "blazemeter"},
    )
    if step6["ok"] and isinstance(step6["raw"], dict):
        payload = step6["raw"]
        s6_status = payload.get("status")
        total_issues = payload.get("total_issues") or 0
        if s6_status == "OK":
            result["steps"]["analyze_jmeter_log"] = {
                "status": "success",
                "log_analysis_status": "OK",
                "total_issues": total_issues,
                "error": None,
            }
        else:
            err_msg = (
                payload.get("message")
                or payload.get("error")
                or f"log_analysis_status={s6_status!r}"
            )
            result["steps"]["analyze_jmeter_log"] = {
                "status": "failed",
                "log_analysis_status": s6_status,
                "total_issues": total_issues,
                "error": _truncate(err_msg, 240),
            }
            notes.append(f"Step 6 (analyze_jmeter_log) failed: {err_msg}")
            important_failures += 1
    else:
        err = step6["error"] if not step6["ok"] else "non-dict response from analyze_jmeter_log"
        result["steps"]["analyze_jmeter_log"] = {
            "status": "failed",
            "log_analysis_status": None,
            "total_issues": 0,
            "error": _truncate(err, 240),
        }
        notes.append(f"Step 6 (analyze_jmeter_log) failed: {err}")
        important_failures += 1

    # ---- Step 7: Validate (response-derived) ----------------------------
    result["validation"] = _build_validation_block(result["steps"], sessions_id)

    # ---- Step 8: Assemble final status ----------------------------------
    result["status"] = "partial" if important_failures > 0 else "success"
    result["notes"] = "; ".join(notes)
    return result


# =============================================================================
# Tool 3 helpers
# =============================================================================

def _new_extraction_result(test_run_id: str) -> dict:
    """Build the canonical Return Format JSON skeleton (INSTRUCTIONS.md §6).

    Every field present from the start so consumers don't have to defend
    against missing keys; concrete values overwrite the defaults as each
    step lands.
    """
    return {
        "subagent": "execution-agent",
        "status": "failed",  # default; overridden on success/partial
        "test_run_id": test_run_id,
        "start_time": None,
        "end_time": None,
        "mcp_artifacts_base_path": None,
        "artifacts_path": None,
        "kpis": {
            "test_name": None,
            "duration_seconds": None,
            "max_virtual_users": None,
            "samples_total": None,
            "pass_count": None,
            "fail_count": None,
            "error_count": None,
            "avg_response_time_ms": None,
            "p90_response_time_ms": None,
            "median_response_time_ms": None,
            "min_response_time_ms": None,
            "max_response_time_ms": None,
            "avg_throughput": None,
            "error_rate": None,
            "avg_bandwidth_bytes": None,
            "avg_latency_ms": None,
        },
        "steps": {
            "get_run_results":           {"status": "pending", "error": None},
            "get_artifacts_path":        {"status": "pending", "error": None},
            "process_session_artifacts": {"status": "pending", "retries": 0, "error": None},
            "get_public_report":        {"status": "pending", "public_url": None, "error": None},
            "get_aggregate_report":     {"status": "pending", "error": None},
            "analyze_jmeter_log":       {
                "status": "pending",
                "log_analysis_status": None,
                "total_issues": 0,
                "error": None,
            },
        },
        "validation": {
            "test_results_csv": False,
            "aggregate_performance_report_csv": False,
            "jmeter_log": False,
            "session_manifest_json": False,
            "public_report_json": False,
            "blazemeter_log_analysis_json": False,
        },
        "notes": "",
    }


def _finalize_critical_failure(
    result: dict, notes: list[str], *, after_step: str
) -> dict:
    """Mark all steps after `after_step` as skipped and return status='failed'.

    Called when a CRITICAL step (1, 2, or 3) fails. Per the §7.1 severity
    model, the recipe aborts immediately and downstream steps are
    explicitly marked `"skipped"` (not `"failed"`) to distinguish "we
    didn't try" from "we tried and it failed".
    """
    idx = _RECIPE_STEPS.index(after_step)
    for step_name in _RECIPE_STEPS[idx + 1 :]:
        result["steps"][step_name]["status"] = "skipped"
    result["validation"] = _build_validation_block(result["steps"], sessions_id=[])
    result["status"] = "failed"
    result["notes"] = "; ".join(notes)
    return result


def _build_validation_block(steps: dict, sessions_id: list) -> dict:
    """Derive expected-file-presence from MCP-reported step statuses.

    NEVER inspects the filesystem (INSTRUCTIONS.md §9.6 prohibits it).
    A file is marked `true` iff the responsible step's status indicates
    the MCP wrote it -- "success" or "partial" both count for Step 3
    (whose `partial` means SOME load-generator artifacts exist).

    `sessions_id` is unused here -- threaded through for future use if
    the validation block ever needs per-generator-log accounting.
    """
    s = steps
    sa = s.get("process_session_artifacts", {}).get("status")
    return {
        "test_results_csv":                  sa in ("success", "partial"),
        "aggregate_performance_report_csv":  s.get("get_aggregate_report", {}).get("status") == "success",
        "jmeter_log":                        sa in ("success", "partial"),
        "session_manifest_json":             sa in ("success", "partial"),
        "public_report_json":                s.get("get_public_report", {}).get("status") == "success",
        "blazemeter_log_analysis_json":      s.get("analyze_jmeter_log", {}).get("status") == "success",
    }


async def _call_blazemeter_with_retries(
    client: Any,
    tool_name: str,
    args: dict,
    *,
    max_attempts: int = _BLAZEMETER_STEP_MAX_ATTEMPTS,
    retry_delay_seconds: float = _BLAZEMETER_STEP_RETRY_DELAY_SECONDS,
) -> dict:
    """Call an API-based MCP tool with the project's retry policy applied.

    Per `mcp-error-handling`: retry up to 3 times on transient failures
    with 5-10s back-off. Note this retries the TRANSPORT call only --
    if the MCP returns a structured response (even with `status: "failed"`
    inside), we accept that at face value rather than re-trying.

    Returns:
        Success: `{"ok": True, "raw": <payload>, "attempts": <int>}`
        Failure: `{"ok": False, "error": "<truncated>", "attempts": <int>}`

    Never raises.
    """
    last_error: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await client.call_tool(tool_name, args)
            payload = _extract_text_or_data(result)
            return {"ok": True, "raw": payload, "attempts": attempt}
        except PermissionError:
            raise  # programmer error (namespace config); never retry
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay_seconds)
    return {
        "ok": False,
        "error": f"{type(last_error).__name__}: {_truncate(last_error, 240)}",
        "attempts": max_attempts,
    }


async def _call_mcp_once(client: Any, tool_name: str, args: dict) -> dict:
    """Single-attempt MCP call (for code-based tools that must not retry).

    Used for Step 6 (`jmeter_analyze_jmeter_log`) per `mcp-error-handling`'s
    "Do NOT retry on failure" rule for code-based MCPs.

    Returns the same `{ok, raw|error, attempts}` shape as
    `_call_blazemeter_with_retries`. Never raises.
    """
    try:
        result = await client.call_tool(tool_name, args)
        return {"ok": True, "raw": _extract_text_or_data(result), "attempts": 1}
    except PermissionError:
        raise
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {_truncate(e, 240)}",
            "attempts": 1,
        }


def _parse_get_run_results(raw: Any) -> dict:
    """Extract `start_time`, `end_time`, `sessions_id` from a get_run_results response.

    The MCP tool `blazemeter_get_run_results` returns a structured dict.
    The dict branch (isinstance check) is the primary code path. The
    legacy text-parsing branch below is retained for backward
    compatibility but is no longer exercised in normal operation.
    """
    out: dict = {
        "start_time": None,
        "end_time": None,
        "sessions_id": [],
        "warnings": [],
    }
    if isinstance(raw, dict):
        out["start_time"] = _normalize_timestamp(raw.get("start_time"))
        out["end_time"] = _normalize_timestamp(raw.get("end_time"))
        sid = raw.get("sessionsId") or raw.get("sessions_id") or []
        if isinstance(sid, (list, tuple)):
            out["sessions_id"] = [str(x) for x in sid]
        return out

    # --- Legacy text-parsing branch ---
    # Retained for backward compatibility; no longer exercised when the
    # MCP tool returns a structured dict.

    if not isinstance(raw, str):
        out["warnings"].append(
            f"get_run_results returned unexpected type {type(raw).__name__}; "
            "could not parse start_time / end_time / sessions_id"
        )
        return out

    # Defensive: detect known error markers from blazemeter_api.py
    text = raw
    if text.startswith("\u2757") or text.startswith("\u26a0"):
        out["warnings"].append(
            f"get_run_results returned an error/warning string: {text[:140]!r}"
        )
        return out

    m_start = _START_TIME_PATTERN.search(text)
    if m_start:
        out["start_time"] = _normalize_timestamp(m_start.group(1).strip())

    m_end = _END_TIME_PATTERN.search(text)
    if m_end:
        out["end_time"] = _normalize_timestamp(m_end.group(1).strip())

    m_sess = _SESSION_ID_PATTERN.search(text)
    if m_sess:
        raw_list = m_sess.group(1)
        try:
            import ast

            parsed_list = ast.literal_eval(raw_list)
            if isinstance(parsed_list, (list, tuple)):
                out["sessions_id"] = [str(x) for x in parsed_list]
        except (ValueError, SyntaxError):
            inner = raw_list.strip("[]")
            if inner:
                out["sessions_id"] = [
                    item.strip().strip("'\"") for item in inner.split(",")
                ]

    if out["start_time"] is None or out["end_time"] is None:
        out["warnings"].append(
            "get_run_results: could not parse one of start_time / end_time; "
            "Step 5 (aggregate report) may contain fallback timing data"
        )

    return out


def _normalize_timestamp(value: Any) -> Optional[str]:
    """Strip whitespace; map BlazeMeter's 'N/A' marker to None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() in {"N/A", "NONE", "NULL"}:
        return None
    return s


def _normalize_artifacts_base(raw: Any) -> Optional[str]:
    """Pull the base path from a `blazemeter_get_artifacts_path` response.

    The MCP returns either the path string directly or the sentinel
    "No artifacts_path found in config." -- the latter must be treated
    as a failure.
    """
    text = str(raw or "").strip()
    if not text or "No artifacts_path found" in text:
        return None
    return text


# =============================================================================
# Tool 4: provision_performance_test
# =============================================================================
#
# Composite tool that lands a fresh JMX in BlazeMeter as a new test with
# its data files attached. Sits between the script-agent (which pushes
# the JMX to Git and runs a local smoke) and start_performance_test
# (which kicks off the run). Always creates a NEW test - never updates
# an existing one, matching the plan's "always create-new for a new JMX"
# contract.
#
# On smoke_status="FAIL" the tool still runs and flags a warning; the
# HITL gate on this tool + the orchestrator's summary make sure the
# human sees the "not recommended to run" flag before choosing to
# actually start the test.


async def provision_performance_test(
    environment: Annotated[
        Optional[str],
        "Environment name from A2A / Web UI metadata (e.g. 'QA', 'UAT'). "
        "Resolved via env_resolver -> BlazeMeter workspace/project. May be "
        "None when the caller supplies workspace_id / project_id explicitly.",
    ] = None,
    test_name: Annotated[
        Optional[str],
        "Human-friendly test name to display in BlazeMeter. Required.",
    ] = None,
    jmx_path: Annotated[
        Optional[str],
        "Local filesystem path to the JMX file to upload. Required. The "
        "basename becomes BlazeMeter's `configuration.filename`.",
    ] = None,
    data_files: Annotated[
        Optional[list],
        "Optional list of local file paths (CSV / JSON / other data assets) "
        "to upload after the JMX. Order preserved.",
    ] = None,
    smoke_status: Annotated[
        Optional[str],
        "Outcome of the Script Agent's local JMeter smoke test: 'PASS' or "
        "'FAIL'. Anything other than 'PASS' surfaces warning='smoke_failed' "
        "on the return; provisioning still proceeds either way.",
    ] = None,
    workspace_id: Annotated[
        Optional[str],
        "Explicit BlazeMeter workspace id override (from A2A metadata). Wins "
        "over the environments.yaml mapping.",
    ] = None,
    project_id: Annotated[
        Optional[str],
        "Explicit BlazeMeter project id override (from A2A metadata). Wins "
        "over the environments.yaml mapping. When set the resolver skips "
        "workspace/project lookup entirely.",
    ] = None,
) -> dict:
    """Create a new BlazeMeter test and upload the JMX + data files.

    Always executes when required inputs are present, even when the
    caller reports ``smoke_status='FAIL'``. The failure surfaces as
    ``warning='smoke_failed'`` on the return dict; the orchestrator
    (and the HITL gate) show it to the human before any start-test
    approval.

    Returns:
        Always a dict. **NEVER raises** for tool-side failures.

        Success (all uploads OK)::

            {
                "ok": True,
                "vendor": "blazemeter",
                "environment": "QA",
                "test_id": "<new-test-id>",
                "test_name": "...",
                "workspace_id": "...",
                "project_id": "...",
                "uploaded_files": [{"file": "...", "size_mb": 0.1}, ...],
                "smoke_status": "PASS",
                "warning": None,
                "provision_status": "success",   # or "partial"
                "raw_upload": {...},              # truncated MCP response
            }

        With warning (smoke fail or partial upload)::

            {..., "ok": True, "warning": "smoke_failed" | "upload_partial", ...}

        Failure (missing input, project resolution failed, or create_test
        failed)::

            {
                "ok": False,
                "error": {"type": "...", "message": "...", "phase": "..."},
                "environment": "QA",
                "test_name": "...",
                "smoke_status": "PASS/FAIL",
            }
    """
    normalized_smoke = (smoke_status or "").strip().upper() or None
    smoke_warning: Optional[str] = None
    if normalized_smoke and normalized_smoke != "PASS":
        smoke_warning = "smoke_failed"

    if not test_name or not str(test_name).strip():
        return _provision_error(
            environment, test_name, normalized_smoke,
            phase="invalid-input",
            message="test_name must be a non-empty string",
        )
    if not jmx_path or not str(jmx_path).strip():
        return _provision_error(
            environment, test_name, normalized_smoke,
            phase="invalid-input",
            message="jmx_path must be a non-empty string",
        )

    jmx_path_str = str(jmx_path).strip()
    if not Path(jmx_path_str).is_file():
        return _provision_error(
            environment, test_name, normalized_smoke,
            phase="invalid-input",
            message=f"JMX file not found: {jmx_path_str}",
        )

    normalized_data_files: list[str] = []
    if data_files:
        if not isinstance(data_files, (list, tuple)):
            return _provision_error(
                environment, test_name, normalized_smoke,
                phase="invalid-input",
                message="data_files must be a list of file paths",
            )
        for entry in data_files:
            if not entry or not isinstance(entry, str):
                return _provision_error(
                    environment, test_name, normalized_smoke,
                    phase="invalid-input",
                    message=f"invalid data_files entry: {entry!r}",
                )
            if not Path(entry).is_file():
                return _provision_error(
                    environment, test_name, normalized_smoke,
                    phase="invalid-input",
                    message=f"data file not found: {entry}",
                )
            normalized_data_files.append(entry)

    resolved_workspace = (workspace_id or "").strip() or None
    resolved_project = (project_id or "").strip() or None
    resolved_workspace_name: Optional[str] = None
    resolved_project_name: Optional[str] = None

    if environment and (resolved_project is None or resolved_workspace is None):
        try:
            from services.env_resolver import resolve_environment
            overrides: dict[str, Any] = {}
            if resolved_workspace:
                overrides["workspaceId"] = resolved_workspace
            if resolved_project:
                overrides["projectId"] = resolved_project
            env_target = resolve_environment(
                environment, overrides=overrides or None,
            )
        except Exception as e:
            return _provision_error(
                environment, test_name, normalized_smoke,
                phase="env-resolver",
                message=f"{type(e).__name__}: {_truncate(e, 200)}",
            )

        if env_target is None:
            return _provision_error(
                environment, test_name, normalized_smoke,
                phase="env-resolver",
                message=(
                    f"environment '{environment}' not found in "
                    "environments.yaml"
                ),
            )
        bm = env_target.blazemeter
        resolved_workspace = resolved_workspace or bm.workspace_id
        resolved_project = resolved_project or bm.project_id
        resolved_workspace_name = bm.workspace_name
        resolved_project_name = bm.project_name

    if not resolved_project and not resolved_project_name:
        return _provision_error(
            environment, test_name, normalized_smoke,
            phase="env-resolver",
            message=(
                "Could not resolve BlazeMeter project: no project_id override "
                "and environments.yaml did not supply project_id or "
                "project_name."
            ),
        )

    from services.mcp_client import MCPClient, build_client_config

    config = build_client_config(["blazemeter", "jmeter"])
    try:
        async with MCPClient(config) as client:
            return await _provision_performance_test_with_client(
                client=client,
                environment=environment,
                test_name=str(test_name).strip(),
                jmx_path=jmx_path_str,
                data_files=normalized_data_files,
                workspace_id=resolved_workspace,
                project_id=resolved_project,
                workspace_name=resolved_workspace_name,
                project_name=resolved_project_name,
                smoke_status=normalized_smoke,
                smoke_warning=smoke_warning,
            )
    except Exception as e:
        return _provision_error(
            environment, test_name, normalized_smoke,
            phase="client-setup",
            message=f"{type(e).__name__}: {_truncate(e, 200)}",
        )


async def _provision_performance_test_with_client(
    *,
    client: Any,
    environment: Optional[str],
    test_name: str,
    jmx_path: str,
    data_files: list[str],
    workspace_id: Optional[str],
    project_id: Optional[str],
    workspace_name: Optional[str],
    project_name: Optional[str],
    smoke_status: Optional[str],
    smoke_warning: Optional[str],
) -> dict:
    """Inner implementation: 3-step BlazeMeter provisioning over an open client.

    Exposed (underscored) so smoke tests can inject a fake client and
    exercise the flow without a real BlazeMeter API.
    """
    resolve_args: dict[str, Any] = {}
    if workspace_id:
        resolve_args["workspace_id"] = workspace_id
    if project_id:
        resolve_args["project_id"] = project_id
    if workspace_name:
        resolve_args["workspace_name"] = workspace_name
    if project_name:
        resolve_args["project_name"] = project_name

    try:
        resolve_result = await client.call_tool(
            "blazemeter_resolve_project", resolve_args,
        )
    except Exception as e:
        return _provision_error(
            environment, test_name, smoke_status,
            phase="resolve-project",
            message=f"{type(e).__name__}: {_truncate(e, 200)}",
        )
    resolve_payload = _extract_text_or_data(resolve_result)
    if not isinstance(resolve_payload, dict) or resolve_payload.get("status") != "success":
        err = (
            resolve_payload.get("error")
            if isinstance(resolve_payload, dict)
            else f"non-dict response: {_truncate(resolve_payload, 200)}"
        )
        return _provision_error(
            environment, test_name, smoke_status,
            phase="resolve-project",
            message=str(err),
        )

    final_workspace_id = resolve_payload.get("workspace_id")
    final_project_id = resolve_payload.get("project_id")
    if not final_project_id:
        return _provision_error(
            environment, test_name, smoke_status,
            phase="resolve-project",
            message="resolve_project returned success but no project_id",
        )

    jmx_basename = Path(jmx_path).name
    try:
        create_result = await client.call_tool(
            "blazemeter_create_test",
            {
                "project_id": str(final_project_id),
                "name": test_name,
                "jmx_filename": jmx_basename,
            },
        )
    except Exception as e:
        return _provision_error(
            environment, test_name, smoke_status,
            phase="create-test",
            message=f"{type(e).__name__}: {_truncate(e, 200)}",
        )
    create_payload = _extract_text_or_data(create_result)
    if not isinstance(create_payload, dict) or create_payload.get("status") != "success":
        err = (
            create_payload.get("error")
            if isinstance(create_payload, dict)
            else f"non-dict response: {_truncate(create_payload, 200)}"
        )
        return _provision_error(
            environment, test_name, smoke_status,
            phase="create-test",
            message=str(err),
        )

    test_id = create_payload.get("test_id")
    if not test_id:
        return _provision_error(
            environment, test_name, smoke_status,
            phase="create-test",
            message="create_test returned success but no test_id",
        )

    upload_paths = [jmx_path] + list(data_files)
    try:
        upload_result = await client.call_tool(
            "blazemeter_upload_test_files",
            {"test_id": str(test_id), "paths": upload_paths},
        )
    except Exception as e:
        return {
            "ok": False,
            "vendor": "blazemeter",
            "environment": environment,
            "test_name": test_name,
            "test_id": str(test_id),
            "workspace_id": final_workspace_id,
            "project_id": final_project_id,
            "smoke_status": smoke_status,
            "warning": smoke_warning,
            "error": {
                "type": type(e).__name__,
                "message": _truncate(e, 200),
                "phase": "upload-files",
            },
        }
    upload_payload = _extract_text_or_data(upload_result)

    uploaded_files: list[dict] = []
    upload_status = "error"
    if isinstance(upload_payload, dict):
        upload_status = upload_payload.get("status") or "error"
        for row in (upload_payload.get("results") or []):
            if row.get("status") == "success":
                uploaded_files.append({
                    "file": row.get("file"),
                    "size_bytes": row.get("size_bytes"),
                    "size_mb": row.get("size_mb"),
                })

    final_warning = smoke_warning
    if upload_status == "partial" and final_warning is None:
        final_warning = "upload_partial"
    elif upload_status == "error":
        return {
            "ok": False,
            "vendor": "blazemeter",
            "environment": environment,
            "test_name": test_name,
            "test_id": str(test_id),
            "workspace_id": final_workspace_id,
            "project_id": final_project_id,
            "smoke_status": smoke_status,
            "warning": smoke_warning,
            "error": {
                "type": "UploadFailed",
                "message": _truncate(upload_payload, 240),
                "phase": "upload-files",
            },
        }

    return {
        "ok": True,
        "vendor": "blazemeter",
        "environment": environment,
        "test_id": str(test_id),
        "test_name": test_name,
        "workspace_id": final_workspace_id,
        "project_id": final_project_id,
        "uploaded_files": uploaded_files,
        "smoke_status": smoke_status,
        "warning": final_warning,
        "provision_status": upload_status,
        "raw_upload": _truncate(upload_payload, 500),
    }


def _provision_error(
    environment: Optional[str],
    test_name: Optional[str],
    smoke_status: Optional[str],
    *,
    phase: str,
    message: str,
) -> dict:
    """Canonical error dict for provision_performance_test."""
    return {
        "ok": False,
        "vendor": "blazemeter",
        "environment": environment,
        "test_name": test_name,
        "smoke_status": smoke_status,
        "warning": "smoke_failed" if (smoke_status and smoke_status != "PASS") else None,
        "error": {
            "type": "InvalidInput" if phase == "invalid-input" else "ProvisionError",
            "message": message,
            "phase": phase,
        },
    }
