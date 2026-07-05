# MCP Performance Suite - Changelog (June 2026)

This document summarizes the enhancements and new features added to the MCP Performance Suite during June 2026.

---

## Table of Contents

- [1. PerfPilot Agents Framework — Foundation](#1-perfpilot-agents-framework--foundation)
- [2. Orchestrator Agent](#2-orchestrator-agent)
- [3. Execution Agent — BlazeMeter](#3-execution-agent--blazemeter)
- [4. FastMCP StreamableHTTP MCP Client](#4-fastmcp-streamablehttp-mcp-client)
- [5. Five Specialist Scaffolds](#5-five-specialist-scaffolds)
- [6. Script Agent — Multi-Turn Tool Loop](#6-script-agent--multi-turn-tool-loop)
- [7. Script Agent — Playwright Integration](#7-script-agent--playwright-integration)
- [8. CopilotKit React Frontend — Initial Implementation](#8-copilotkit-react-frontend--initial-implementation)
- [9. Bug Fixes](#9-bug-fixes)
- [10. Docker Compose — Full Stack](#10-docker-compose--full-stack)
- [Previous Changelogs](#previous-changelogs)

---

## 1. PerfPilot Agents Framework — Foundation

### 1.1 Overview

The major initiative for June 2026 was **Epic 3 — PerfPilot Agents Framework**, an AG2-based multi-agent AI layer that enables autonomous performance testing orchestration. This foundation spans Features 3.2 through 3.7 and establishes the database, identity, transport, and session infrastructure that all specialist agents build on.

### 1.2 perfagent_state Database (Feature 3.2)

A new PostgreSQL database (`perfagent_state`) with 7 JSONB-backed tables providing persistent state management for the multi-agent system:

| Table | SQL File | Purpose |
|-------|----------|---------|
| `agent_sessions` | `002_create_agent_sessions.sql` | Agent session lifecycle and metadata |
| `agent_threads` | `008_create_agent_threads.sql` | ChatGPT-style persistent threads with user ownership |
| `agent_tasks` | `003_create_agent_tasks.sql` | Task tracking with thread association (`003b` adds `thread_id`) |
| `agent_checkpoints` | `004_create_agent_checkpoints.sql` | AG2 agent state snapshots for resumption |
| `conversation_messages` | `005_create_conversation_messages.sql` | Full conversation history per thread (DB is source of truth) |
| `tool_call_traces` | `006_create_tool_call_traces.sql` | Tool invocation audit trail with inputs/outputs |
| `hitl_approvals` | `007_create_hitl_approvals.sql` | Human-in-the-loop approval requests and responses |

A provisioning script (`sql/provision.py`) automates database and table creation.

### 1.3 Multi-LLM Provider Abstraction (Feature 3.3)

A vendor-neutral LLM provider layer supporting three backends with per-agent override and global fallback:

| Backend | Configuration | TLS Support |
|---------|---------------|-------------|
| OpenAI | API key + model name | Standard |
| Azure OpenAI | Endpoint + API key + deployment name | Custom CA bundle |
| Ollama | Local endpoint + model name | Optional |

Each agent can specify its own LLM provider in its `config.yaml`, falling back to the global `agents.yaml` configuration. TLS certificate configuration is supported for corporate proxy environments.

### 1.4 A2A Server (Feature 3.5)

An A2A (Agent-to-Agent) server on port 8001 implementing the full Google A2A protocol:

| Endpoint | Protocol Feature |
|----------|-----------------|
| `/.well-known/agent.json` | Agent discovery — returns agent card with capabilities |
| `/tasks/send` | Synchronous task submission |
| `/tasks/sendSubscribe` | SSE-streaming task submission with real-time progress |
| `/tasks/get` | Task status polling |
| `/tasks/cancel` | Task cancellation |
| Webhook callbacks | Push notifications on task state transitions |

### 1.5 AG-UI / CopilotKit Bridge (Feature 3.6)

A bridge server on port 8002 translating between the AG-UI protocol and the internal agent system, enabling CopilotKit React frontends to communicate with PerfPilot agents:

| Endpoint | Purpose |
|----------|---------|
| `/copilotkit/` | SSE stream for CopilotKit protocol events |
| `/sessions/` | Session CRUD operations |
| `/runs/` | Run management with streaming |
| `/threads/` | Thread CRUD for persistent conversations |
| `/events/` | Event stream for HITL and agent status updates |

### 1.6 Multi-User Thread Isolation (Feature 3.4)

Ownership-guarded thread isolation ensuring data privacy across users. Alice cannot see or interact with Bob's threads, sessions, or conversation history. The ownership guard is enforced at the store layer — every query includes a `user_id` filter derived from the authenticated identity.

### 1.7 Persistent Threads (Feature 3.4)

ChatGPT-style multi-day conversation resumption. Threads persist in the database with full conversation history loaded on reconnection. This enables users to close their browser, return days later, and resume an in-progress performance testing workflow exactly where they left off.

### 1.8 Vendor-Neutral Identity Resolver (Feature 3.3)

A 4-step identity resolution chain that abstracts away the authentication provider:

| Step | Source | When Used |
|------|--------|-----------|
| 1 | Upstream auth headers (reverse proxy / SSO) | Corporate environments with pre-authentication |
| 2 | `X-PerfPilot-Token` header | Direct API access with PerfPilot-issued tokens |
| 3 | Session cookie | Browser-based frontend sessions |
| 4 | Freshly minted anonymous identity | Development / local testing fallback |

### 1.9 Session Middleware

Auto-provisioning session middleware that creates sessions on first request and attaches the resolved identity. Sessions are persisted in the database for audit and resumption.

### 1.10 Files Created / Modified

| File | Purpose |
|------|---------|
| `agent-framework/a2a_server.py` | A2A protocol server (port 8001) |
| `agent-framework/agui_server.py` | AG-UI / CopilotKit bridge server (port 8002) |
| `agent-framework/utils/llm_provider.py` | Multi-LLM provider abstraction (OpenAI / Azure OpenAI / Ollama) |
| `agent-framework/utils/user_identity.py` | Vendor-neutral 4-step identity resolver |
| `agent-framework/utils/auth.py` | Authentication utilities and token validation |
| `agent-framework/utils/session_middleware.py` | Auto-provisioning session middleware |
| `agent-framework/utils/session_store.py` | Session persistence and retrieval |
| `agent-framework/utils/thread_store.py` | Thread CRUD with ownership guard |
| `agent-framework/utils/conversation_store.py` | Conversation message persistence (DB source of truth) |
| `agent-framework/utils/task_store.py` | Task lifecycle management |
| `agent-framework/utils/hitl_store.py` | HITL approval request/response persistence |
| `agent-framework/utils/trace_store.py` | Tool call trace audit logging |
| `agent-framework/utils/db.py` | PostgreSQL connection pool and query helpers |
| `agent-framework/utils/base_agent.py` | Base agent class with shared infrastructure |
| `agent-framework/utils/agents_config.py` | Global agent configuration loader |
| `agent-framework/config/agents.example.yaml` | Global agent configuration template |
| `agent-framework/sql/001_create_perfagent_state.sql` | Database creation DDL |
| `agent-framework/sql/002_create_agent_sessions.sql` | Sessions table DDL |
| `agent-framework/sql/003_create_agent_tasks.sql` | Tasks table DDL |
| `agent-framework/sql/003b_add_thread_id_to_agent_tasks.sql` | Thread ID column addition |
| `agent-framework/sql/004_create_agent_checkpoints.sql` | Checkpoints table DDL |
| `agent-framework/sql/005_create_conversation_messages.sql` | Conversation messages table DDL |
| `agent-framework/sql/006_create_tool_call_traces.sql` | Tool call traces table DDL |
| `agent-framework/sql/007_create_hitl_approvals.sql` | HITL approvals table DDL |
| `agent-framework/sql/008_create_agent_threads.sql` | Threads table DDL |
| `agent-framework/sql/009_create_token_ledger.sql` | Token ledger table DDL |
| `agent-framework/sql/provision.py` | Database provisioning script |
| `agent-framework/sql/README.md` | SQL schema documentation |
| `agent-framework/pyproject.toml` | Python project metadata and dependencies |
| `agent-framework/README.md` | Framework documentation |
| `agent-framework/AGENTS.md` | Architecture decisions, design log, and agent specifications |

---

## 2. Orchestrator Agent

### 2.1 Overview

The Orchestrator Agent (Feature 3.7) is a real AG2 `ConversableAgent` that serves as the central coordinator for the multi-agent system. It receives user requests, determines which specialist agent(s) to delegate to, and manages the end-to-end workflow.

### 2.2 Delegation Tools

Four tools registered on the orchestrator enable structured multi-agent coordination:

| Tool | Purpose |
|------|---------|
| `list_available_specialists` | Returns the catalog of registered specialist agents with their capabilities |
| `delegate_to_specialist` | Routes a task to a specific specialist agent via the A2A protocol |
| `check_task_status` | Polls a delegated task's current state (pending, running, completed, failed) |
| `request_human_approval` | Triggers an HITL approval round-trip for sensitive operations |

### 2.3 DB-Loaded Conversation History

Per Decision 14 in the architecture log, the database is the source of truth for conversation history. On thread resumption, the orchestrator loads the full conversation history from `conversation_messages` rather than relying on in-memory AG2 state. This ensures consistency across server restarts and multi-day sessions.

### 2.4 HITL Approval Round-Trip

The end-to-end HITL approval flow was proven functional: orchestrator requests approval → bridge surfaces the approval request to the frontend → user approves/rejects → response flows back to the orchestrator → workflow proceeds or halts accordingly.

### 2.5 Files Created / Modified

| File | Purpose |
|------|---------|
| `agent-framework/agents/orchestrator/agent.py` | AG2 ConversableAgent with 4 delegation tools |
| `agent-framework/agents/orchestrator/agent_card.json` | A2A agent discovery card |
| `agent-framework/agents/orchestrator/INSTRUCTIONS.md` | System instructions for the orchestrator LLM |
| `agent-framework/agents/orchestrator/config.example.yaml` | Configuration template with LLM provider override |
| `agent-framework/agents/orchestrator/__init__.py` | Package marker |

---

## 3. Execution Agent — BlazeMeter

### 3.1 Overview

The Execution Agent (Feature 3.8) is the first real specialist agent (not a stub). It orchestrates BlazeMeter performance test execution through vendor-agnostic tool abstractions, handling the full lifecycle from test start to artifact extraction.

### 3.2 Vendor-Agnostic Tools

Three tools provide a clean abstraction over the BlazeMeter API:

| Tool | Purpose |
|------|---------|
| `start_performance_test` | Initiates a BlazeMeter test run with configurable parameters |
| `wait_for_completion` | Polls the test run until terminal state (completed, failed, cancelled) |
| `extract_test_run_artifacts` | Executes a 6-step artifact extraction recipe |

### 3.3 6-Step Artifact Extraction Recipe

The `extract_test_run_artifacts` tool orchestrates 6 sequential MCP tool calls to collect all test run data:

| Step | MCP Tool | Artifact |
|------|----------|----------|
| 1 | `blazemeter_get_test_report` | Test report summary |
| 2 | `blazemeter_get_test_sessions` | Session metadata |
| 3 | `blazemeter_get_session_jtl_csv` | JTL results CSV per session |
| 4 | `blazemeter_get_session_logs` | Execution logs per session |
| 5 | `blazemeter_get_session_errors` | Error details per session |
| 6 | `blazemeter_get_monitor_data` | Server monitoring data |

### 3.4 First Live BlazeMeter Run

On 2026-06-14, the first end-to-end BlazeMeter execution was proven: Orchestrator delegates to execution-agent → execution-agent starts test → waits for completion → extracts artifacts → returns results to orchestrator. The A2A contract between orchestrator and execution-agent was validated with zero orchestrator code changes.

### 3.5 Files Created / Modified

| File | Purpose |
|------|---------|
| `agent-framework/agents/execution-agent/agent.py` | BlazeMeter specialist agent with 3 vendor-agnostic tools |
| `agent-framework/agents/execution-agent/agent_card.json` | A2A agent discovery card |
| `agent-framework/agents/execution-agent/INSTRUCTIONS.md` | System instructions for execution LLM |
| `agent-framework/agents/execution-agent/config.example.yaml` | Configuration template |
| `agent-framework/agents/execution-agent/__init__.py` | Package marker |

---

## 4. FastMCP StreamableHTTP MCP Client

### 4.1 Overview

A real FastMCP client (Feature 3.8.2) that routes agent tool calls through PerfPilot Hub's StreamableHTTP endpoint. This enables specialist agents to call MCP tools from within the AG2 multi-agent runtime, using the same gateway infrastructure that Cursor agents connect to.

### 4.2 Per-Agent Namespace Allowlist

Each agent declares which MCP namespaces it is permitted to use. The client enforces this allowlist locally before making any network request:

- Tool names must match a declared `<namespace>_` prefix
- Prefix matching prevents collision (e.g., `perf` does not match `perfanalysis_` tools if only `perfreport` is allowed)
- Violations raise `PermissionError` before any network round-trip, providing fast-fail security

### 4.3 Files Created

| File | Purpose |
|------|---------|
| `agent-framework/utils/mcp_client.py` | FastMCP StreamableHTTP client with namespace allowlist filtering |

---

## 5. Five Specialist Scaffolds

### 5.1 Overview

Five specialist agent scaffolds (Feature 3.9) were created following a consistent four-file pattern. Each agent is pre-configured with its MCP namespace allowlist and ready for implementation beyond the scaffold.

### 5.2 Four-File Pattern

Every specialist agent follows the same directory structure:

| File | Purpose |
|------|---------|
| `agent.py` | AG2 ConversableAgent with namespace-specific tools |
| `agent_card.json` | A2A protocol discovery card with capabilities |
| `INSTRUCTIONS.md` | System instructions for the specialist's LLM |
| `config.example.yaml` | Configuration template with LLM override and namespace allowlist |

### 5.3 Script Agent

- **Namespaces (via gateway):** `jmeter`, `perfmemory`
- **Direct tools:** `browser_*` from Playwright MCP container
- **Scope:** JMeter script generation, HAR/Swagger conversion, correlation analysis, debugging, and browser automation

### 5.4 Monitoring Agent

- **Namespace:** `datadog`
- **Scope:** Host metrics, Kubernetes metrics, APM traces, log collection, and custom query execution

### 5.5 Analysis Agent

- **Namespace:** `perfanalysis`
- **Scope:** Test result analysis, SLA evaluation, bottleneck detection, KPI calculation

### 5.6 Reporting Agent

- **Namespaces:** `perfreport`, `confluence`
- **HITL capability:** Report revision requires human approval before publishing
- **Scope:** Report generation, AI-assisted revision, Confluence publishing

### 5.7 Notifications Agent

- **Architecture:** Vendor-neutral event emitter (not tied to a single MCP namespace)
- **Scope:** MS Teams notifications, future Slack/email integration

### 5.8 Files Created

| File | Purpose |
|------|---------|
| `agent-framework/agents/script-agent/agent.py` | Script agent with JMeter + PerfMemory + Playwright tools |
| `agent-framework/agents/script-agent/agent_card.json` | A2A discovery card |
| `agent-framework/agents/script-agent/INSTRUCTIONS.md` | System instructions |
| `agent-framework/agents/script-agent/config.example.yaml` | Configuration template |
| `agent-framework/agents/script-agent/__init__.py` | Package marker |
| `agent-framework/agents/monitoring-agent/agent.py` | Monitoring agent with Datadog tools |
| `agent-framework/agents/monitoring-agent/agent_card.json` | A2A discovery card |
| `agent-framework/agents/monitoring-agent/INSTRUCTIONS.md` | System instructions |
| `agent-framework/agents/monitoring-agent/config.example.yaml` | Configuration template |
| `agent-framework/agents/monitoring-agent/__init__.py` | Package marker |
| `agent-framework/agents/analysis-agent/agent.py` | Analysis agent with PerfAnalysis tools |
| `agent-framework/agents/analysis-agent/agent_card.json` | A2A discovery card |
| `agent-framework/agents/analysis-agent/INSTRUCTIONS.md` | System instructions |
| `agent-framework/agents/analysis-agent/config.example.yaml` | Configuration template |
| `agent-framework/agents/analysis-agent/__init__.py` | Package marker |
| `agent-framework/agents/reporting-agent/agent.py` | Reporting agent with PerfReport + Confluence tools and HITL |
| `agent-framework/agents/reporting-agent/agent_card.json` | A2A discovery card |
| `agent-framework/agents/reporting-agent/INSTRUCTIONS.md` | System instructions |
| `agent-framework/agents/reporting-agent/config.example.yaml` | Configuration template |
| `agent-framework/agents/reporting-agent/__init__.py` | Package marker |
| `agent-framework/agents/notifications-agent/agent.py` | Notifications agent with vendor-neutral event emitter |
| `agent-framework/agents/notifications-agent/agent_card.json` | A2A discovery card |
| `agent-framework/agents/notifications-agent/INSTRUCTIONS.md` | System instructions |
| `agent-framework/agents/notifications-agent/config.example.yaml` | Configuration template |
| `agent-framework/agents/notifications-agent/__init__.py` | Package marker |

---

## 6. Script Agent — Multi-Turn Tool Loop

### 6.1 Overview

A core async execution loop added to the task executor (late June) enabling agents to run multi-turn tool sequences autonomously. The loop drives the script agent's ability to execute complex workflows (e.g., generate script → correlate → debug → re-run) without requiring manual step-by-step prompting.

### 6.2 `_run_multi_turn_tool_loop`

The `_run_multi_turn_tool_loop` method in `task_executor.py` implements the autonomous execution loop with the following safety mechanisms:

| Feature | Behavior |
|---------|----------|
| Repetition detection | If the agent produces the same tool call sequence on consecutive rounds, the loop exits to prevent infinite loops |
| Max rounds safety valve | Configurable maximum number of rounds (default: bounded) prevents runaway execution |
| SSE progress broadcasting | Each round emits an SSE event with the current round number and tool call summary for frontend visibility |
| Error handling | Tool call failures are fed back to the agent as error messages for self-correction |

### 6.3 MCP Tool Registry

A registry layer (`mcp_tool_registry.py`) that auto-discovers available tools from PerfPilot Hub and filters them by the agent's namespace allowlist. The registry caches tool schemas to avoid repeated discovery calls.

### 6.4 Files Created / Modified

| File | Purpose |
|------|---------|
| `agent-framework/utils/task_executor.py` | Multi-turn tool loop with repetition detection and SSE broadcasting |
| `agent-framework/utils/mcp_tool_registry.py` | MCP tool auto-discovery with namespace filtering and schema caching |

---

## 7. Script Agent — Playwright Integration

### 7.1 Overview

Late June / early July work added persistent Playwright MCP integration to the script agent, enabling browser-based test spec execution with network capture and JMX generation.

### 7.2 Persistent MCP Client

A persistent MCP client maintains long-lived connections to the Microsoft Playwright MCP container. Unlike the gateway-routed tools (which are stateless request/response), browser automation requires stateful sessions — the browser context, cookies, and page state must persist across multiple tool calls within a single multi-turn loop execution.

### 7.3 Stateful Session Holder

The script agent implements a stateful session holder pattern:

| Aspect | Behavior |
|--------|----------|
| Session lifecycle | Browser session is created at the start of a task and persisted across all rounds of the multi-turn loop |
| State persistence | Page state, cookies, localStorage, and network capture buffers survive across tool calls |
| Cleanup | Session is explicitly closed on task completion or error |

### 7.4 `browser_*` Tool Registration

The `browser_*` tools are registered directly from the Playwright MCP container (not routed through PerfPilot Hub), as they require the persistent connection described above. Tool names follow the Playwright MCP naming convention (e.g., `browser_navigate`, `browser_click`, `browser_fill`).

### 7.5 End-to-End Workflow

The integration enables the following automated workflow:

1. Agent receives a test spec (Markdown with step-by-step instructions)
2. Agent launches a browser session via Playwright MCP
3. Agent executes the spec steps using `browser_*` tools in the multi-turn loop
4. Network traffic is captured during execution
5. Captured traffic is passed to JMeter MCP tools for JMX script generation

### 7.6 Files Modified

| File | Purpose |
|------|---------|
| `agent-framework/agents/script-agent/agent.py` | Playwright MCP persistent client and `browser_*` tool registration |
| `agent-framework/utils/task_executor.py` | Stateful session holder for persistent browser state across multi-turn rounds |

---

## 8. CopilotKit React Frontend — Initial Implementation

### 8.1 Overview

An initial CopilotKit-based React frontend providing a chat interface for interacting with PerfPilot agents. Built on Next.js 15 and CopilotKit, it connects to the AG-UI bridge server (port 8002) for real-time streaming.

### 8.2 Features

| Feature | Description |
|---------|-------------|
| Chat interface | CopilotKit React chat component with message threading |
| Thread management | Create, list, switch, and delete persistent threads |
| AG-UI streaming | Real-time SSE integration with the AG-UI bridge for live agent responses |
| Agent catalog panel | Scaffolded panel for browsing available specialist agents (UI shell) |
| Persistent history | Thread history loaded from the database on reconnection |

### 8.3 Files Created

| File | Purpose |
|------|---------|
| `agent-framework/frontend/ui/app/page.tsx` | Main chat page component |
| `agent-framework/frontend/ui/app/layout.tsx` | Root layout with CopilotKit provider |
| `agent-framework/frontend/ui/app/runs/page.tsx` | Runs list page |
| `agent-framework/frontend/ui/app/runs/[id]/page.tsx` | Individual run detail page |
| `agent-framework/frontend/ui/app/api/copilotkit/route.ts` | CopilotKit API route handler (AG-UI bridge proxy) |
| `agent-framework/frontend/ui/package.json` | Next.js 15 + CopilotKit dependencies |
| `agent-framework/frontend/ui/components.json` | shadcn/ui component configuration |
| `agent-framework/frontend/ui/tsconfig.json` | TypeScript configuration |
| `agent-framework/frontend/ui/README.md` | Frontend documentation |
| `agent-framework/frontend/__init__.py` | Package marker |

---

## 9. Bug Fixes

### 9.1 BUG-001: Multi-Turn Tool Loop Refactor

The initial multi-turn tool loop implementation did not properly execute tool calls or feed results back to the agent for the next round. The loop was refactored to:

- Execute each tool call via the MCP client and capture the result
- Feed tool results back as assistant context for the next LLM round
- Handle partial failures (some tools succeed, some fail) gracefully

### 9.2 BUG-002: Persistent Playwright MCP Sessions

The Playwright MCP client was being re-created on each round of the multi-turn loop, losing browser state (cookies, page context, network capture buffers). Fixed by implementing the stateful session holder pattern that persists the MCP client connection across all rounds of a single task execution.

### 9.3 BUG-009: Multiple Fixes

Multiple fixes identified and resolved during smoke testing. Details are documented in the smoke test results.

---

## 10. Docker Compose — Full Stack

### 10.1 Overview

New Docker Compose configurations that bring up the full PerfPilot backend infrastructure with a single `docker compose up -d` command. Platform-specific files are provided for both Windows and macOS.

### 10.2 Containers

| Container | Image | Purpose |
|-----------|-------|---------|
| PerfMemory DB | PostgreSQL + pgvector + Apache AGE | Persistent state for `perfagent_state` database and PerfMemory vector/graph store |
| Gateway MCP | PerfPilot Hub | Super MCP gateway exposing all 9 MCP servers on a single endpoint |
| Playwright MCP | Microsoft Playwright MCP | Browser automation container for `browser_*` tools |

### 10.3 Files Created

| File | Purpose |
|------|---------|
| `docker/docker-compose-full-windows.yaml` | Full-stack compose for Windows |
| `docker/docker-compose-full-mac.yaml` | Full-stack compose for macOS |

---

## Previous Changelogs

| Month | File | Highlights |
|-------|------|------------|
| May 2026 | [CHANGELOG-2026-05.md](docs/changelogs/CHANGELOG-2026-05.md) | PerfMemory Taxonomy, EntraID Correlation Engine, EntraID Debugging Skill, SharePoint MCP, FastMCP v3 Migration, PerfPilot Hub Gateway |
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills Migration, Cursor Subagents, PerfMemory MCP + AGE Graph, MS Teams MCP, KPI Analysis, JMeter Script Validator, Structure Export & HAR-JMX Comparison |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL Editing Tools, Correlation Analysis v0.6/v0.7, AI-Assisted Debugging, Artifact Path Alignment, BlazeMeter Shared Folders |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI Adapter, HAR Adapter, Centralized SLA Config, JMeter Log Analysis, Bottleneck Analyzer v0.2, Multi-Session Artifacts |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-Assisted Report Revision, Datadog Dynamic Limits, Report Enhancements, New Charts |

---

*Last Updated: June 30, 2026*
