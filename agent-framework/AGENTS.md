# AGENTS.md - agent-framework

Development context for Cursor / Claude IDE working inside `agent-framework/`.
This file is read by AI coding assistants on session start; humans should treat
it as the table of contents for the agent layer.

## What lives here

`agent-framework/` is the AG2-based agent layer for the MCP Perf Suite. It runs
in its own Docker container (`perf-agents`) alongside `perf-gateway`,
`perfmemory-db`, and the external Microsoft `playwright-mcp` container.
Architecture and design rationale:
[../docs/plans/AG2-Framework-and-Architecture-V2.md](../docs/plans/AG2-Framework-and-Architecture-V2.md).

The folder name on disk is `agent-framework/` (with hyphen) for engineering
clarity. The user-facing brand is "PerfPilot Agents". Do not rename either.

## Folder map

> **Status legend:** ✅ implemented · 🟡 partial · ⏭ planned · — not yet started.
> Running progress for every Epic 3 feature lives in
> [`../docs/plans/Epic-3-Implementation-Status.md`](../docs/plans/Epic-3-Implementation-Status.md).
> Read that file first when picking up work in a new conversation.

| Path | Purpose | Status / Filled by |
|---|---|---|
| `a2a_server.py` | FastAPI entrypoint for the A2A surface (port 8001). Path-based routing of all agents under `/agents/{name}/...`. Real orchestrator dispatch + A2A thread resolution (Decision 17) lands in F3.7.8. | ✅ F3.5; extended in F3.7.8 |
| `agui_server.py` | FastAPI entrypoint for the AG-UI / CopilotKit bridge (port 8002). `/copilotkit/` is a custom history-aware endpoint that loads `conversation_messages` per `thread_id`, persists each turn, and dispatches to the real orchestrator (per Decisions 14 + 17). Owner-checked thread CRUD endpoints under `/api/threads/*`. | ✅ F3.6 (backend); extended in F3.7.6b (thread CRUD) and F3.7.7 (real orchestrator + DB-loaded history) |
| `agents/` | One subfolder per agent, four-file pattern (`agent.py`, `agent_card.json`, `INSTRUCTIONS.md`, plus one of `config.yaml` / `config.example.yaml`). See "Per-agent `config.yaml` schema" below for the candidate-resolution convention. | ✅ F3.7.1-3.7.6 — orchestrator scaffolded with all four delegation tools registered (`list_available_specialists` / `delegate_to_specialist` / `check_task_status` / `request_human_approval`) and `agent_card.json` `status: available` (v0.3.0); ✅ F3.8.1-3.8.7 — execution-agent fully scaffolded and wired: PBI 3.8.1 scaffolded the four-file pattern (`status: in_development`, `skills: []`, v0.1.0); PBI 3.8.3 wired `start_performance_test(test_id)` (vendor-agnostic name, wraps `blazemeter_start_test` 1:1, NOT idempotent → single attempt); PBI 3.8.4 wired `wait_for_completion(run_id, *, poll_interval_seconds=60.0, timeout_seconds=300.0)` (polls `blazemeter_check_test_status` until terminal; absorbs transient single-poll failures; `ok=True` for ENDED / has_error / timed_out — `ok=False` only on 3 consecutive MCP failures); PBI 3.8.5 wired `extract_test_run_artifacts(test_run_id, test_name=None)` (6-step recipe over 6 MCP tools; CRITICAL Steps 1-3 short-circuit on failure → `status: "failed"`; IMPORTANT Steps 4-6 record + continue → `status: "partial"` if any failed; BlazeMeter steps retry 3x with 5s back-off; jmeter step never retries per `mcp-error-handling`; filesystem NEVER touched — validation block is response-derived; returns canonical Return Format JSON from `INSTRUCTIONS.md` §6); PBI 3.8.6 wired the dispatcher in `task_executor.py`; PBI 3.8.7 flipped `status: available` + bumped to v0.2.0 + populated `skills[]` with the three skill descriptors mirroring the orchestrator's contract shape (and migrated `STUB_AGENT` in `smoke_test_a2a_server.py` from `execution-agent` → `monitoring-agent` since the former is no longer stub-routed); PBI 3.8.8 added `scripts/smoke_test_execution_agent.py` (gitignored, live BlazeMeter run against test_id=14491287; ≤5 min wall; proves start → wait → extract end-to-end against the real MCP stack); PBI 3.8.9 extended `scripts/smoke_test_orchestrator.py` §5d with orchestrator → execution-agent E2E via A2A (zero orchestrator-code changes — proves the F3.7→F3.8 contract; two deterministic paths: 5d.1 happy dispatch with validation short-circuit, 5d.2 dispatcher `InvalidPayload` envelope; no BlazeMeter traffic); vendor-agnostic agent-tool names with BlazeMeter-only MCP wiring today, multi-vendor plug-in via the same names in a future feature; ✅ F3.9 — five specialist stubs scaffolded (four-file pattern each, `status: in_development`, `skills: []`, v0.1.0): **script-agent** (`enabled: false` — Playwright MCP container gating; `mcp_namespaces: [jmeter, perfmemory]` via gateway + `browser_*` direct from Playwright MCP; dual test_run_id lifecycle for script-creation vs test-execution phases), **monitoring-agent** (`enabled: true`; `mcp_namespaces: [datadog]`; Datadog host/K8s/APM/log extraction scoped to test run time windows), **analysis-agent** (`enabled: true`; `mcp_namespaces: [perfanalysis]`; SLA validation, bottleneck attribution, log-error root-cause bucketing), **reporting-agent** (`enabled: true`; `mcp_namespaces: [perfreport, confluence]`; chart generation, report assembly, multi-round HITL revision, Confluence publishing; `capabilities.human_in_the_loop: true`), **notifications-agent** (`enabled: true`; `mcp_namespaces: []` — vendor-neutral event emitter, adapters wire in Epic 4); `smoke_test_a2a_server.py` extended with card-discovery assertions for all four enabled stubs (7-8 checks each: name, status, version, skills, required A2A fields, metadata.pbi_scaffolded_at); F3.10 (promotions) |
| `workflows/` | Agent-to-agent Python pipelines (NOT Cursor Skills — both retained, neither replaces the other). | ⏭ F3.11 |
| `utils/` | Shared agent-layer infrastructure (see breakdown below). | mixed |
| `config/` | Runtime YAML: `agents.yaml` (global LLM fallback + per-agent enable/disable + TLS settings + `web_ui.session_cookie` tunables), `agents.example.yaml` (committed template). | ✅ F3.4; extended in F3.7.0b (cookie block) and F3.13 |
| `sql/` | DDL for the `perfagent_state` database (seven JSONB-only tables: six from F3.3 plus `agent_threads` from F3.7.0). | ✅ F3.3, extended in F3.7.0 |
| `pyproject.toml`, `requirements.txt` | Package metadata and dependencies. | ✅ F3.2; bumped in F3.4, F3.5, F3.6 |
| `.env.example` | Environment template (no real credentials). | ✅ F3.2 |

### `utils/` module map

| Module | Purpose | Status / Filled by |
|---|---|---|
| `db.py` | `asyncpg` connection-pool helper for `perfagent_state` | ✅ F3.3 |
| `session_store.py` | CRUD over `agent_sessions` (create / get / touch / end / list) | ✅ F3.3, extended in F3.6.4 (`list_sessions`); column rename `user_identity` -> `user_id` in F3.7-prep |
| `thread_store.py` | CRUD over `agent_threads` (create / get / get_by_external_thread_id / list_for_user / touch / set_title / archive / delete). Threads are the persistent conversation containers that survive across sessions. | ✅ F3.7.0 |
| `agents_config.py` | Loads `config/agents.yaml`, caches enable/disable map; resolves `web_ui.session_cookie` tunables via `get_session_cookie_config()` | ✅ F3.5; extended in F3.7.0b (cookie config) |
| `user_identity.py` | Four-step Epic 3 token resolver: upstream-auth placeholder (vendor-agnostic — see Decision 20) → `X-PerfPilot-Token` header → `perfpilot_token` cookie → mint fresh opaque token. Returns `ResolvedUser`; exposes `set_user_id_cookie` for middleware response-side use. | ✅ F3.7.0b |
| `session_middleware.py` | Resolves `X-Session-Id` / `X-External-Session-Id` request headers, runs the `user_identity.resolve_user_id` chain, persists rows in `agent_sessions`, attaches `session_id` / `external_session_id` / `user_id` to `request.state`, sets the `perfpilot_token` cookie when minted | ✅ F3.5; extended in F3.7.0b (user-identity resolver + cookie) |
| `task_store.py` | CRUD over `agent_tasks`; `RunSummary` aggregate; `list_runs` / `list_tasks_for_run` (both gained an optional `user_id` filter for owner-filtering in F3.7.0b) | ✅ F3.5, extended in F3.6.6 + F3.7.0b |
| `task_executor.py` | Background runner with in-process pub/sub bus and webhook delivery; dispatches to `_run_orchestrator()` (real AG2, history-loading, persistence) for `agent_name="orchestrator"`, to `_run_execution_agent()` (explicit `payload.tool` dispatch over the 3 F3.8 agent tools — no LLM loop; module loaded via `importlib` because the folder is hyphenated; envelope echoes `tool` / `action` / `test_run_id` for audit) for `agent_name="execution-agent"`, and to `_run_stub_agent()` for every other name until F3.9 promotes the remaining specialists. | ✅ F3.5; extended in F3.7.8 (orchestrator dispatch) and PBI 3.8.6 (execution-agent dispatch — closes the F3.7→F3.8 contract) |
| `hitl_store.py` | CRUD over `hitl_approvals`: `create_prompt`, `record_decision`, `get_approval`, `get_pending_for_task`, `list_for_task` | ✅ F3.6.5 |
| `conversation_store.py` | CRUD over `conversation_messages`: `append_message`, `list_for_thread`, `count_for_thread`, `delete_all_for_thread`. Powers PBI 3.7.6b's `GET /api/threads/{tid}/messages` and PBI 3.7.7's DB-as-source-of-truth history loader in `/copilotkit/`. JSONB content shape allows string / dict / tool-call frames without schema migrations. | ✅ F3.7.6b |
| `llm_provider.py` | OpenAI / Azure OpenAI / Ollama abstraction; `to_ag2_config()` returns AG2-compatible `llm_config` dict; TLS via `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` | ✅ F3.4 |
| `auth.py` | Ownership guard for multi-user safety: `requires_owner(resource_owner, requesting_user)` raises `HTTPException(401/403)`. Convenience lookups `owner_of_session(session_id)` and `owner_of_task(task_id)` walk to the underlying `agent_sessions.user_id`. | ✅ F3.7.0b (promoted from F3.13 placeholder); Epic 4 layers in claim / role checks on top, vendor-agnostic (Decision 20) |
| `mcp_client.py` | FastMCP `StreamableHTTP` client setup (called by specialists). `MCPClient` async context manager wraps `fastmcp.Client` + `StreamableHttpTransport` against `gateway-mcp`. `list_tools()` and `call_tool()` enforce the per-agent `mcp_tools.allowed_namespaces` allowlist; `call_tool()` raises `PermissionError` for out-of-namespace names BEFORE any network round-trip. `is_tool_allowed()` uses `<ns>_` prefix matching so `"blazemeter"` allows `"blazemeter_start_test"` but not `"blazemetersomething"`. Wildcard `["*"]` is the orchestrator convention. | ✅ PBI 3.8.2 (real FastMCP wiring; F3.13 will add auth headers + OTel spans) |
| `base_agent.py` | Shared agent-folder loader: `CANDIDATE_CONFIG_FILES = ("config.yaml", "config.example.yaml")`, `resolve_agent_config_path(agent_folder)`, `load_agent_definition(agent_folder)`, `discover_agents(agents_root)`, `synthesize_stub_card(name)`, `read_agent_card(folder)` (with stub fallback — also consumed by the orchestrator's `list_available_specialists` tool). All file reads use `encoding="utf-8-sig"` for Windows BOM tolerance. Real `ConversableAgent` factory + MCP wiring still slated for F3.8 when extracted from per-agent code. | ✅ F3.2 (skeleton); F3.7.1 (candidate-resolution + BOM-tolerant reads); F3.7.3 (shared stub card synthesis consumed by orchestrator); F3.8 (real factory) |
| `otel.py` | Epic 4 readiness — reads session / task IDs; default no-op | — F3.13 |

## Conventions

1. **Additions only.** New files and new modules. Do not modify existing MCP
   server code or Dockerfiles without explicit approval. See
   [../.cursor/rules/project-and-coding-guidelines.mdc](../.cursor/rules/project-and-coding-guidelines.mdc).
2. **Async everywhere.** All agent code, MCP calls, DB access, and LLM calls
   are `async`. Sync wrappers around third-party libraries belong in `utils/`.
3. **Lazy heavy imports.** `ag2`, `fastmcp`, `asyncpg`, and LLM SDKs are
   imported inside functions, not at module top, so structural smoke tests run
   without a fully populated virtualenv.
4. **Four-file pattern.** Every agent folder under `agents/` has exactly:
   `agent.py`, `agent_card.json`, `INSTRUCTIONS.md`, and **one of**
   `config.yaml` (operator-side override, gitignored) or `config.example.yaml`
   (committed default). The loader (`utils/base_agent.resolve_agent_config_path`)
   walks the candidate list in that priority order — so a local `config.yaml`
   wins, otherwise the committed example is used. Same `<file>.yaml` /
   `<file>.example.yaml` split that the global
   `agent-framework/config/agents.yaml` and the MCP servers in this repo
   already use. No exceptions.
5. **MCP-mediated I/O.** Agents reach external systems only through MCP tools
   served by `gateway-mcp`. No direct vendor SDK calls from agent code.
   Exception: the script-agent's call to the Microsoft Playwright MCP is also
   mediated, just to a different MCP server (Container 4).
6. **Vendor-agnostic events.** Notifications-agent emits a `TestRunCompleted`
   event with structured fields. Routing to Teams / SharePoint / Slack / etc.
   is an Epic 4 adapter concern, not an agent concern.
7. **Config-driven behavior.** LLM provider selection (per-agent and global
   fallback), MCP namespace allowlists, agent enable/disable, auth on/off,
   OTel on/off - all in YAML, not Python.
8. **Test scripts go in gitignored `scripts/`** at the repo root, not inside
   `agent-framework/`.
9. **Three IDs travel with every task** (Section 4.3 of the V2 doc):
   `external_session_id` (optional, propagated from upstream SDLC),
   `session_id` (required, per UI/IDE/A2A connection), and `task_id`
   (required, per A2A task).

## Per-agent `config.yaml` schema

Each agent folder under `agents/<name>/` has a per-agent config file
(created in F3.7+). The loader in `utils/base_agent.py` walks two
candidates in priority order — same convention every MCP server in this
repo uses:

| Filename | Tracked in git? | Purpose |
|---|---|---|
| `config.yaml`         | ❌ (`.gitignore`'d via `agent-framework/agents/*/config.yaml`) | Operator-side override. Loaded first if present. |
| `config.example.yaml` | ✅ (committed)                                                 | Public default. Loaded as fallback. Smoke tests + fresh clones rely on this. |

Operators / engineers copy `config.example.yaml` → `config.yaml` and edit
the copy. The example file stays unmodified in the branch so smoke tests
have a deterministic baseline.

Files are read with `encoding='utf-8-sig'` so any UTF-8 BOM emitted by
Windows editors / tools is transparently stripped before YAML / JSON
parsing.

The schema below is consumed by `utils/llm_provider.py` and the agent
loader in `utils/base_agent.py`:

```yaml
# Per-agent override for the LLM. If this entire block is omitted, the agent
# falls back to `config/agents.yaml -> default_llm_provider`. Credentials
# (api_key / endpoint) are NEVER stored here - they come from `.env` and
# are merged at runtime by `utils/llm_provider.py::merge_env_credentials`.
llm_provider:
  provider: openai          # one of: openai | azure_openai | ollama
  openai_model: gpt-4o-mini # for provider=openai
  # azure_deployment: gpt-4o      # for provider=azure_openai
  # ollama_model: llama3.1        # for provider=ollama
  temperature: 0.2          # 0.1 for analysis/reporting, 0.4+ for creative

# MCP namespace allowlist. Each entry corresponds to a namespace prefix on
# the gateway-mcp tool catalog (e.g. "blazemeter_*", "datadog_*"). The
# orchestrator and agent-loader filter the MCP tool catalog through this
# list so each agent only sees the tools it is supposed to use.
mcp_tools:
  allowed_namespaces:
    - blazemeter
    - datadog

# Optional A2A discovery metadata. If absent, sensible defaults are derived
# from the agent name and folder.
a2a:
  display_name: "Execution Agent"
  description: "Runs BlazeMeter test runs end to end."
  tags: ["execution", "blazemeter"]
```

The global fallback file `config/agents.yaml` (or `agents.example.yaml`) holds
the `default_llm_provider` block plus the per-agent enable/disable map. A
disabled agent is not instantiated at startup and returns `404` on its A2A
agent card. TLS settings (`ssl_verification: ca_bundle | system | disabled`)
also live here; the actual CA bundle path comes from `REQUESTS_CA_BUNDLE` or
`SSL_CERT_FILE` env vars - same convention as `blazemeter-mcp`,
`datadog-mcp`, and `confluence-mcp`.

## Where to look first

- **Implementation progress (Epic 3 features 3.1–3.14, decisions log,
  next-PBI plan):**
  [../docs/plans/Epic-3-Implementation-Status.md](../docs/plans/Epic-3-Implementation-Status.md)
- **Architecture overview, sequence diagrams, port assignments:** V2 doc
  Sections 4 and 5
- **Endpoint inventories:**
  - A2A on port 8001 (protocol-standard paths): V2 doc Section 9.2
  - AG-UI on port 8002 (browser-friendly `/api/*` prefix; `/copilotkit/`
    served by AG2's native `AGUIStream(agent).build_asgi()`):
    V2 doc Section 10 — see also Decision 4 / Decision 6 in the status doc
    for the rationale behind dropping the `/api/perfpilot/*` prefix and
    skipping the CopilotKit Python SDK
- **Database schema (six tables):** V2 doc Section 12.3
- **LLM provider pattern to mirror:**
  [../perfmemory-mcp/services/embeddings.py](../perfmemory-mcp/services/embeddings.py)
- **Long-running task patterns (poll / SSE / webhook):** V2 doc Section 14
- **HITL multi-round revise loop pattern:** V2 doc Section 15

## Cursor rules in force

- [../.cursor/rules/project-and-coding-guidelines.mdc](../.cursor/rules/project-and-coding-guidelines.mdc):
  no refactoring, additions only, smoke-test each enhancement, scripts go in
  gitignored `scripts/`.
- [../.cursor/rules/prerequisites.mdc](../.cursor/rules/prerequisites.mdc): MCP
  credentials are gitignored, do not validate `.env` files upfront.
- [../.cursor/rules/mcp-error-handling.mdc](../.cursor/rules/mcp-error-handling.mdc):
  retry policy and error reporting format per MCP type.
- [../.cursor/rules/skill-execution-rules.mdc](../.cursor/rules/skill-execution-rules.mdc):
  do not skip or reorder steps in skill workflows.

## HITL gate

Every PBI is reviewed before the next begins. Do not push to
`ag2-agent-framework`; the user reviews and approves all changes locally.

```
draft -> HITL review -> approve / reject -> revise if needed -> approve -> next PBI
```
