# 📋 PerfPilot-Hub — Changelog (November 2026)

This document summarizes the enhancements added to PerfPilot-Hub during November 2026.

---

## 📑 Table of Contents

- [🔧 1. Caller identity snapshot/restore](#1-caller-identity-snapshotrestore)
- [🛠️ 2. Execution-agent delegation and MCP dispatch](#2-execution-agent-delegation-and-mcp-dispatch)
- [📊 3. Structured task results injection](#3-structured-task-results-injection)
- [✨ 4. Files created](#4-files-created)
- [🔧 5. Files modified](#5-files-modified)
- [📚 Previous Changelogs](#previous-changelogs)

---

## 🔧 1. Caller identity snapshot/restore

**Branch:** `fix/agent-delegation-and-caller-identity`

The module-level `_caller_identity` dict in `agents/orchestrator/agent.py`
carries per-request identity (user_id, thread_id, session_id, etc.) across
AG2's thread isolation boundary. When the Web UI's inline-drain mechanism
starts a delegated specialist task in the same turn, the child task's
`_clear_web_ui_caller()` wiped the dict — killing subsequent delegations
in the same orchestrator turn.

**Fix:** replaced wipe-on-exit with snapshot/restore semantics.

- Added `snapshot_caller_identity()` — returns a shallow copy of the
  current `_caller_identity` dict before entering a child context.
- Added `restore_caller_identity(snapshot)` — restores the dict from a
  prior snapshot on exit instead of wiping all fields.
- Renamed `_clear_web_ui_caller()` to `_restore_caller_snapshot(snapshot)`
  in `specialist_agents.py` — restores the snapshot instead of clearing.
- Rewrote `_restore_web_ui_caller()` to return
  `tuple[bool, dict | None]` — captures the snapshot and skips restore
  when a live `session_id` is already present (inline-drain scenario).
- Both call sites updated: `_run_execution_agent` finally block and
  `_run_mcp_specialist_agent` finally block.

---

## 🛠️ 2. Execution-agent delegation and MCP dispatch

**Branch:** `fix/agent-delegation-and-caller-identity`

Three related sub-fixes address the execution-agent's MCP tool handling:

### 2a. Prompt corrections

The orchestrator's INSTRUCTIONS.md §9.6 listed 14 MCP tool names with
incomplete parameter schemas. The orchestrator has no access to MCP tools
and cannot validate arguments — that is the specialist's job via FastMCP
v3.x docstring-derived schemas.

- **Orchestrator INSTRUCTIONS.md:** Removed the MCP pass-through table
  (§9.6). Replaced with intent-based delegation guidance. Added §11
  item 3: "Do not name MCP tools or set MCP tool arguments when
  delegating to the execution-agent." Updated §2 table row.
- **Execution-agent INSTRUCTIONS.md:** Added §5.1 documenting
  intent-based payloads (no `tool` field). Three-point guidance:
  inspect FastMCP catalog, prefer composites, use individual
  `blazemeter_*` tools only for specific operations.

### 2b. Dispatcher rewrite

The MCP pass-through branch in `_run_execution_agent` bypassed the
execution-agent's LLM, forwarding args verbatim via
`call_mcp_tool_passthrough`. The orchestrator (with no MCP schemas)
became the only reasoning agent — schema mismatches caused Pydantic
validation errors that wasted retry budget.

- Replaced the MCP pass-through block with routing to
  `_run_mcp_specialist_agent()` — the execution-agent's LLM now
  validates args against live FastMCP schemas.
- Three-way routing: **composite tools** (direct Python dispatch),
  **intent-based** (no `tool` field → LLM dispatch),
  **MCP tool names** (`blazemeter_*`/`jmeter_*` → LLM dispatch).
- Removed unused `call_mcp_tool_passthrough` and
  `passthrough_process_session_artifacts` imports.

### 2c. Retry policy fix

`call_mcp_tool_passthrough` retried all exceptions 3 times including
deterministic Pydantic validation errors, wasting 10+ seconds per bad
call.

- Added `_is_validation_error(exc)` helper — detects `validation error`,
  `unexpected_keyword_argument`, `missing_argument`, and
  `missing required argument` markers (case-insensitive).
- Both `call_mcp_tool_passthrough` and
  `call_mcp_tool_passthrough_for_specialist` now short-circuit on
  validation errors: return immediately on attempt 1 with
  `error.type = "ValidationError"`. Transient errors still retry.

---

## 📊 3. Structured task results injection

**Branch:** `fix/agent-delegation-and-caller-identity`

Two gaps prevented structured data from prior tasks being available in
subsequent conversation turns:

1. `_build_completion_summary` only extracted `reply_text` / `summary` /
   `message` — execution-agent pass-through results (which carry data in
   `tool_result` but no `reply_text`) produced a bare "completed
   successfully" message.
2. `agui_server.py` only injected `known_test_run_ids` — structured
   fields like `sessions_id`, artifact paths, and public URLs were lost
   between turns.

**Fix:**

- **New helper:** `services/helpers/task_result_fields.py` — whitelisted
  key-field extractor. Maps 10 tool names to their safe-to-surface
  fields. Unknown tools get fallback extraction (`status`, `ok`).
  Normalizes `sessionsId` → `sessions_id`. Excludes `None` values and
  non-dict inputs.
- **Completion summary enrichment** (`task_executor.py`): when no
  `reply_text`/`summary`/`message` is present and the result contains a
  `tool_result` dict, extracts whitelisted key fields and formats them
  as JSON in the conversation thread.
- **Known-task-results injection** (`agui_server.py`): added
  `MAX_TASK_RESULTS_INJECTION = 20` module-level constant. After the
  existing secrets hint injection, queries terminal tasks for the
  thread, extracts key fields, and injects a
  `SystemMessage(id="webui-known-task-results")` so the orchestrator
  LLM sees prior outcomes on subsequent turns.

---

## ✨ 4. Files created

| File | Purpose |
|------|---------|
| `agent-framework/backend/services/helpers/task_result_fields.py` | Whitelisted key-field extractor for task results |
| `tests/agent_framework/services/test_caller_identity_lifecycle.py` | 10 regression tests for snapshot/restore caller identity |
| `tests/agent_framework/services/test_mcp_passthrough_retry_policy.py` | 12 tests for validation error short-circuit and transient retry |
| `tests/agent_framework/services/test_execution_agent_dispatch.py` | 8 tests for three-way dispatch routing |
| `tests/agent_framework/services/test_task_result_fields.py` | 16 tests for whitelisted key-field extraction |
| `tests/agent_framework/services/test_completion_summary_enrichment.py` | 7 tests for completion summary enrichment |

## 🔧 5. Files modified

| File | Changes |
|------|---------|
| `agent-framework/backend/agents/orchestrator/agent.py` | Added `snapshot_caller_identity()` and `restore_caller_identity()` helpers |
| `agent-framework/backend/services/specialist_agents.py` | Snapshot/restore in `_restore_web_ui_caller`; replaced MCP pass-through with LLM dispatch; removed unused pass-through imports |
| `agent-framework/backend/services/helpers/mcp_passthrough.py` | Added `_is_validation_error()` guard; validation errors return on attempt 1 |
| `agent-framework/backend/services/task_executor.py` | `_build_completion_summary` enriched with key-field extraction for execution-agent results |
| `agent-framework/backend/agui_server.py` | `MAX_TASK_RESULTS_INJECTION = 20` constant; known-task-results system hint injection |
| `agent-framework/backend/agents/orchestrator/INSTRUCTIONS.md` | Removed MCP pass-through table (§9.6); added intent-based delegation; added §11 item 3 prohibition |
| `agent-framework/backend/agents/execution-agent/INSTRUCTIONS.md` | Added §5.1 intent-based delegation payload documentation |

---

## 📚 Previous Changelogs

| Month | Link | Highlights |
|-------|------|------------|
| October 2026 | [CHANGELOG-2026-10.md](docs/changelogs/CHANGELOG-2026-10.md) | Framework-assigned test_run_id, A2A compliance pass, .NET Aspire orchestration, Web UI visual port, bring-your-own tokens |
| September 2026 | [CHANGELOG-2026-09.md](docs/changelogs/CHANGELOG-2026-09.md) | GitHub MCP, BlazeMeter test provisioning, A2A new-JMX pipeline, documentation refactor, Docker full-stack refactor |
| August 2026 | [CHANGELOG-2026-08.md](docs/changelogs/CHANGELOG-2026-08.md) | Repository restructuring, agent-framework containers, HITL approval flow, user identity, task management UI |
| July 2026 | [CHANGELOG-2026-07.md](docs/changelogs/CHANGELOG-2026-07.md) | Loop engineering, LLM round-trip reduction, context and token tracking, compaction |
| June 2026 | [CHANGELOG-2026-06.md](docs/changelogs/CHANGELOG-2026-06.md) | PerfPilot Agents framework, orchestrator, Execution Agent, Playwright integration, CopilotKit frontend |
| May 2026 | [CHANGELOG-2026-05.md](docs/changelogs/CHANGELOG-2026-05.md) | PerfMemory taxonomy, correlation engine, SharePoint MCP, FastMCP v3 migration, PerfPilot Hub |
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills migration, Cursor subagents, PerfMemory MCP, MS Teams MCP, KPI analysis, JMeter script validator |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL editing tools, correlation analysis, AI-assisted debugging, artifact path alignment |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI adapter, HAR adapter, centralized SLA config, JMeter log analysis |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-assisted report revision, Datadog dynamic limits, report enhancements, new charts |

---

*Last Updated: November 8, 2026*
