# MCP Performance Suite - Changelog (July 2026)

This document summarizes the enhancements and new features added to the MCP Performance Suite during July 2026.

---

## Table of Contents

- [Loop Engineering — LLM Call Reduction and Context/Token Tracking](#loop-engineering--llm-call-reduction-and-contexttoken-tracking)
  - [Track A: Reduce LLM Round-Trips](#track-a-reduce-llm-round-trips)
  - [Track B: Context Window and Token Tracking](#track-b-context-window-and-token-tracking)
  - [Validation Results](#validation-results-e2e-proven-july-5-2026)
  - [Files Created](#files-created)
  - [Files Modified](#files-modified)
- [Previous Changelogs](#previous-changelogs)

---

## Loop Engineering — LLM Call Reduction and Context/Token Tracking

Two related enhancements to reduce LLM cost and improve observability in the agent framework's multi-turn tool loop: (1) reduce unnecessary LLM round-trips during browser automation, and (2) implement context window and token tracking with compaction.

**Background:** The Script Agent's multi-turn tool loop makes one LLM API call per loop iteration. For an 8-step browser automation flow, this produces 25 LLM calls over ~48 seconds. Conversation history grew unbounded with no token counting, no compaction, no usage persistence.

### Track A: Reduce LLM Round-Trips

#### A1. Autonomous Execution Directive (INSTRUCTIONS.md)

- Added Section 2.1 "Browser automation loop efficiency" to the script-agent's system prompt
- Instructs the LLM to process browser actions without intermediate commentary
- Directs to only take `browser_snapshot` when element refs are unknown (not before every action)
- Zero-code prompt engineering change — immediate impact on reducing filler turns

#### A2. Optional `tool_choice=required` Config

- Added `force_tool_choice: false` to `config.example.yaml` (opt-in, not default)
- When enabled, sets `tool_choice="required"` on the LLM config, forcing tool calls instead of text filler
- Wired in `agent.py` after `build_llm_config()`

### Track B: Context Window and Token Tracking

#### B1. Context Awareness Directive (INSTRUCTIONS.md)

- Added Section 10 "Context window efficiency" to the script-agent's system prompt
- Guidance: keep tool args minimal, reference artifact paths instead of inlining content, don't repeat prior results
- Zero-code prompt engineering change

#### B2. Tool Call Traces Persistence

- New file: `agent-framework/utils/trace_store.py`
- `insert_tool_trace()` — async CRUD for the existing `tool_call_traces` table
- `schedule_trace_insert()` — fire-and-forget wrapper using `asyncio.create_task()` (never breaks the loop)
- `measure_latency_ms()` — timing utility for per-tool execution measurement
- Integration: wired into `_run_multi_turn_tool_loop` after each `_execute_tool_on_agent` call with timing

#### B3. Token Counting Per Iteration

- Added `tiktoken>=0.12.0` dependency for accurate BPE token counting
- `_estimate_token_count()` — uses tiktoken (gpt-4o encoding) with char/4 fallback if unavailable
- Per-iteration utilization logging at INFO level: `script-agent task <id> round N: ~X tokens, Y% utilization (128000 limit)`
- SSE broadcast enriched with `context_tokens`, `context_utilization_pct`, `context_limit` fields
- New config: `context_limit: 128000` in `config.example.yaml`

#### B4. Compaction Trigger

- `_compact_tool_results()` — replaces old tool result messages with compact references when utilization >= 80%
- Safety guardrails: preserves the most recent N results (configurable) and never compacts results from the current page session (after last `browser_navigate`)
- Idempotent (already-compacted messages are skipped)
- New config: `compaction_threshold: 0.80`, `compaction_preserve_recent: 5`

#### B5. Token Ledger Table

- New migration: `agent-framework/sql/009_create_token_ledger.sql`
- Schema: `task_id`, `agent_name`, `loop_iteration`, `prompt_tokens`, `completion_tokens`, `mcp_overhead_tokens`, `utilization_pct`, `compaction_triggered`, `compacted_count`, `tokens_freed`, `recorded_at`
- `insert_token_ledger_entry()` + `schedule_token_ledger_insert()` in `trace_store.py`
- Fire-and-forget writes persisted every iteration of the tool loop

### Validation Results (E2E Proven July 5, 2026)

- **Smoke test:** 9 unit tests passing (token estimation, compaction logic, full loop integration, DB persistence)
- **Live E2E:** BlazeDemo 8-step browser automation through the CopilotKit Web UI
  - 25 rounds, ~48 seconds, peak 9.2% utilization (11,812 tokens)
  - 25 `tool_call_traces` rows persisted (latency 17ms–1,106ms, avg 335ms)
  - 25 `token_ledger` rows persisted with accurate utilization curve
  - Both AG-UI (port 8002) and A2A (port 8001) surfaces emit the new fields

### Files Created

| File | Purpose |
|------|---------|
| `agent-framework/utils/trace_store.py` | Async CRUD + fire-and-forget persistence for tool_call_traces and token_ledger |
| `agent-framework/sql/009_create_token_ledger.sql` | DDL for the token_ledger observability table |

### Files Modified

| File | Changes |
|------|---------|
| `agent-framework/agents/script-agent/INSTRUCTIONS.md` | Added Section 2.1 (autonomous execution) + Section 10 (context efficiency) |
| `agent-framework/agents/script-agent/config.example.yaml` | Added `context_limit`, `compaction_threshold`, `compaction_preserve_recent`, `force_tool_choice` |
| `agent-framework/agents/script-agent/agent.py` | Added `force_tool_choice` → `tool_choice="required"` wiring |
| `agent-framework/utils/task_executor.py` | Token counting, compaction trigger, trace writes, timing, SSE enrichment |
| `agent-framework/requirements.txt` | Added `tiktoken>=0.12.0` |

---

## Previous Changelogs

| Month | Link | Highlights |
|-------|------|------------|
| June 2026 | [CHANGELOG-2026-06.md](docs/changelogs/CHANGELOG-2026-06.md) | PerfPilot Agents Framework, Orchestrator, Execution Agent, Playwright Integration, CopilotKit Frontend |
| May 2026 | [CHANGELOG-2026-05.md](docs/changelogs/CHANGELOG-2026-05.md) | PerfMemory Taxonomy, EntraID Correlation Engine, SharePoint MCP, FastMCP v3 Migration, PerfPilot Hub |
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills Migration, Cursor Subagents, PerfMemory MCP + AGE Graph, MS Teams MCP, KPI Analysis, JMeter Script Validator |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL Editing Tools, Correlation Analysis v0.6/v0.7, AI-Assisted Debugging, Artifact Path Alignment, BlazeMeter Shared Folders |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI Adapter, HAR Adapter, Centralized SLA Config, JMeter Log Analysis, Bottleneck Analyzer v0.2 |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-Assisted Report Revision, Datadog Dynamic Limits, Report Enhancements, New Charts |

---

*Last Updated: July 5, 2026*
