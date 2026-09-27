# MCP Performance Suite - Changelog (August 2026)

This document summarizes the enhancements and new features added to the MCP Performance Suite during August 2026.

> **Merged in:** [PR #7 — Add PerfPilot Agents (AG2): A2A server, specialist agents, and CopilotKit UI](https://github.com/canyonlabz/perfpilot-hub/pull/7) *(2026-08-22)*
>
> This changelog covers only the **new** work landed in PR #7. The June 2026 changelog already documents the AG2 foundation, A2A server, AG-UI bridge, and initial specialist scaffolds — those items are not repeated here.

---

## Table of Contents

- [1. Repository Restructuring](#1-repository-restructuring)
- [2. Docker Deployment — Initial Agent Framework Containers](#2-docker-deployment--initial-agent-framework-containers)
- [3. Additional Specialist Agent Scaffolds](#3-additional-specialist-agent-scaffolds)
- [4. Human-in-the-Loop (HITL) Approval Flow](#4-human-in-the-loop-hitl-approval-flow)
- [5. User Identity Resolution](#5-user-identity-resolution)
- [6. Task Management — Backend + UI](#6-task-management--backend--ui)
- [7. Frontend Polish](#7-frontend-polish)
- [8. Persistence Layer Refactor](#8-persistence-layer-refactor)
- [9. Files Created](#9-files-created)
- [10. Files Modified](#10-files-modified)
- [Previous Changelogs](#previous-changelogs)

---

## 1. Repository Restructuring

The repository underwent a large structural reorganization to prepare for the multi-project **PerfPilot Hub** layout that the agent framework required. Every MCP server that previously lived at the repo root was moved into a shared `mcp-perf-suite/` sub-folder.

### 1.1 MCPs Moved to `mcp-perf-suite/` Sub-folder

| MCP | New Location |
|-----|--------------|
| `blazemeter-mcp` | `mcp-perf-suite/blazemeter-mcp/` |
| `confluence-mcp` | `mcp-perf-suite/confluence-mcp/` |
| `datadog-mcp` | `mcp-perf-suite/datadog-mcp/` |
| `jmeter-mcp` | `mcp-perf-suite/jmeter-mcp/` |
| `msteams-mcp` | `mcp-perf-suite/msteams-mcp/` |
| `perfanalysis-mcp` | `mcp-perf-suite/perfanalysis-mcp/` |
| `perfmemory-mcp` | `mcp-perf-suite/perfmemory-mcp/` |
| `perfreport-mcp` | `mcp-perf-suite/perfreport-mcp/` |
| `sharepoint-mcp` | `mcp-perf-suite/sharepoint-mcp/` |
| `gateway-mcp` | `mcp-perf-suite/gateway-mcp/` (renamed to PerfPilot Hub) |
| `streamlit-ui` | `mcp-perf-suite/streamlit-ui/` |
| `artifacts/` | `mcp-perf-suite/artifacts/` |

### 1.2 Combined `requirements.txt`

A consolidated `mcp-perf-suite/requirements.txt` was added covering the Python dependencies of every MCP server. Individual MCPs still ship their own `requirements.txt`, but the combined file simplifies whole-suite Docker builds and CI validation.

### 1.3 Gateway URL / Port Unification

The gateway MCP's listen port was standardized to `8888` across every configuration reference (agent backend, docker compose, `.env.example` files, and per-MCP `MCP_URL_*` defaults).

---

## 2. Docker Deployment — Initial Agent Framework Containers

August 2026 was the first month the agent framework itself became container-deployable. Three new Dockerfiles were added under `docker/`, each with its own `docker-compose.yml`, `.env.example`, and README.

### 2.1 Agent Backend (Dual Image)

A single Dockerfile under `docker/agent-backend/` produces two container images from one build:

| Image | Container | Port | Command |
|-------|-----------|------|---------|
| `perfpilot-a2a:latest` | `perfpilot-a2a` | `8101` | `python a2a_server.py` |
| `perfpilot-agui:latest` | `perfpilot-agui` | `8102` | `python agui_server.py` |

Both images share `python:3.12-slim` + `libpq-dev` (for `asyncpg`), with health checks pointing at `/health` on each service's port.

### 2.2 Agent Frontend

The Next.js UI (`agent-framework/frontend/ui/`) received its own Dockerfile under `docker/agent-frontend/`, producing `perfpilot-ui:latest` on port `8080`.

### 2.3 Playwright MCP

An initial `docker/playwright-mcp/` Dockerfile was added, layering on top of Microsoft's vendor `mcp/playwright:latest` base image (this base image dependency was later removed in September — see `CHANGELOG.md`).

### 2.4 Corporate CA Build-Arg Pattern

All three new Dockerfiles support an `ENABLE_CORP_CA` build argument. When set to `true`, the build imports a CA PEM bundle from `docker/certs/corporate/` into the image trust store, which allows the container to operate behind HTTPS-intercepting corporate proxies (Zscaler, Norton 360, BlueCoat, etc.).

---

## 3. Additional Specialist Agent Scaffolds

June's release delivered the Orchestrator, Execution, and Script agents. August added scaffolds for two more:

### 3.1 Notifications Agent

- New: `agent-framework/agents/notifications-agent/`
- Files: `agent_card.json`, `config.example.yaml`, `INSTRUCTIONS.md`
- Purpose: MS Teams / SharePoint / email notifications for test start, stop, and results events

### 3.2 Reporting Agent

- New: `agent-framework/agents/reporting-agent/`
- Files: `agent_card.json`, `config.example.yaml`, `INSTRUCTIONS.md`
- Purpose: Drafts executive-friendly performance test reports; supports revision cycles

### 3.3 Script Agent — Additional Wiring

Additional wiring and dispatch logic was added to the existing script agent, including the transition from a stub orchestrator to real dispatch through the specialist agents.

---

## 4. Human-in-the-Loop (HITL) Approval Flow

The orchestrator agent gained a first-class HITL approval mechanism with configurable gates.

### 4.1 Configurable Gates

New section in `config.example.yaml` for the orchestrator agent:

- `hitl.enabled` — global toggle
- `hitl.gates` — per-task-type gates (e.g. `start_test`, `publish_report`)
- Each gate can require approval, be auto-approved, or be skipped

### 4.2 Frontend Polling + UI

The `TaskProgressPanel` component now polls the HITL approvals endpoint. When an approval is pending, an approval card renders inline in the task feed, allowing the user to approve or reject without leaving the chat.

### 4.3 Backend Integration

The orchestrator delegates to the `hitl_approvals` database table (created in June) for approval request/response persistence. Task execution suspends at gates and resumes on approval.

---

## 5. User Identity Resolution

August introduced a proper multi-transport user identity system for the agent framework — replacing the ad-hoc `perfpilot_user_id` cookie used earlier.

### 5.1 Four-Step Resolver Chain

A new `agent-framework/backend/utils/user_identity.py` implements a four-step resolution chain:

1. **Upstream authentication** — middleware-injected identity (for federated deployments)
2. **`X-PerfPilot-Token` header** — IDE / CLI clients pass an opaque token
3. **`perfpilot_token` cookie** — browser users are minted a cookie on first visit
4. **Mint a new opaque token** — if no identity is present, a new user ID is generated

The chain returns a `ResolvedUser` dataclass with the user ID and resolution source.

### 5.2 Session Cookie Configuration

New `SessionCookieConfig` dataclass in `agents_config.py` with structured fields for `max_age_days`, `secure`, and `samesite`. Wired into the session middleware.

### 5.3 User-Scoped Data Access

Task, session, thread, and conversation stores were updated to support user ID filtering. Every read operation now respects ownership; every write records the owner.

### 5.4 Authorization Helpers

New `agent-framework/backend/utils/auth.py` provides ownership checks for resources (threads, tasks, runs). Called from the delegation and retrieval endpoints.

---

## 6. Task Management — Backend + UI

August unified how tasks are discovered, dispatched, and monitored from the Web UI.

### 6.1 New Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /threads/{thread_id}/tasks` | List tasks scoped to a conversation thread |
| `GET /tasks/{task_id}` | Retrieve a single task (owner-filtered) |
| `POST /delegations` | Create + dispatch a specialist task from the Web UI |

### 6.2 `thread_id` on Tasks

The `agent_tasks` table now includes a `thread_id` field. A backfill script populates `thread_id` from historical task payloads to preserve conversation context.

### 6.3 TaskProgressCard Component

The `TaskProgressCard` React component now formats both result payloads and error summaries in a user-friendly way, replacing the earlier raw JSON dump.

### 6.4 Progress Callback Support

Long-running MCP tools can now emit progress callbacks that propagate to the SSE stream and update the TaskProgressCard in real time.

---

## 7. Frontend Polish

Several targeted UI improvements landed in the CopilotKit React frontend:

- 📚 **Agent catalog panel** — API integration + UI components for browsing available specialist agents
- ✍️ **Markdown rendering** — `react-markdown` + `remark-gfm` dependencies for GFM-flavored agent responses
- 🤔 **"Thinking" message** — visible indicator while the agent is processing
- 🛠️ **`showDevConsole` prop** — opt-in dev console on the CopilotKit component
- 🚫 **Hidden CopilotKit Web Inspector** — the custom Tasks panel is now the primary side surface
- 🎨 **CopilotKit v2 CSS bundling fix** — Tailwind v4 subpath imports are now ignored, resolving a Tailwind v4 compatibility conflict

---

## 8. Persistence Layer Refactor

The persistence modules for conversations, sessions, tasks, and HITL approvals were consolidated under a single `stores/` package.

- New: `agent-framework/backend/stores/__init__.py` — package marker + convenience re-exports
- Removed: standalone `conversation_store.py`, `session_store.py`, `task_store.py`, `hitl_store.py` at the module root
- Updated: import paths across the backend to reference the `stores/` package

This makes the persistence layer discoverable as a cohesive unit and enables clean bulk imports in tests.

---

## 9. Files Created

| File | Purpose |
|------|---------|
| `docker/agent-backend/Dockerfile` | Dual-image build for A2A + AG-UI backend |
| `docker/agent-backend/docker-compose.yml` | Standalone compose for both backend services |
| `docker/agent-backend/.env.example` | Backend env template (LLM, MCP, Postgres) |
| `docker/agent-backend/README.md` | Backend Docker deployment guide |
| `docker/agent-frontend/Dockerfile` | Next.js UI image |
| `docker/agent-frontend/docker-compose.yml` | Standalone compose for the UI |
| `docker/agent-frontend/.env.example` | UI env template |
| `docker/agent-frontend/README.md` | Frontend Docker deployment guide |
| `docker/playwright-mcp/Dockerfile` | Initial Playwright MCP image (base image dependency later removed in September) |
| `docker/playwright-mcp/docker-compose.yml` | Standalone compose for Playwright MCP |
| `docker/playwright-mcp/README.md` | Playwright MCP deployment guide |
| `agent-framework/agents/notifications-agent/` | Notifications specialist scaffold |
| `agent-framework/agents/reporting-agent/` | Reporting specialist scaffold |
| `agent-framework/backend/stores/__init__.py` | New persistence package |
| `agent-framework/backend/utils/user_identity.py` | Four-step user identity resolver |
| `agent-framework/backend/utils/auth.py` | Ownership check helpers |
| `mcp-perf-suite/requirements.txt` | Combined requirements for all MCP servers |

## 10. Files Modified

| File | Changes |
|------|---------|
| `agent-framework/agents/orchestrator-agent/config.example.yaml` | HITL gates configuration block |
| `agent-framework/backend/config/agents_config.py` | `SessionCookieConfig` dataclass |
| `agent-framework/backend/middleware/*` | Four-step user identity resolution wiring |
| `agent-framework/backend/api/tasks.py` | New endpoints: thread-scoped list, single-task retrieval, delegation |
| `agent-framework/sql/003b_add_thread_id_to_agent_tasks.sql` | Migration to add `thread_id` to `agent_tasks` |
| `agent-framework/frontend/ui/components/TaskProgressCard.tsx` | Result / error summary formatting |
| `agent-framework/frontend/ui/components/TaskProgressPanel.tsx` | HITL approval polling + card rendering |
| `agent-framework/frontend/ui/package.json` | `react-markdown`, `remark-gfm`, agent catalog deps |
| `agent-framework/frontend/ui/app/page.tsx` | `showDevConsole` toggle, Web Inspector hidden |
| Multiple `mcp-perf-suite/*/config.py` | Relocated to sub-folder + adjusted path resolution |

---

## Previous Changelogs

| Month | Link | Highlights |
|-------|------|------------|
| July 2026 | [CHANGELOG-2026-07.md](docs/changelogs/CHANGELOG-2026-07.md) | Loop Engineering, LLM Round-Trip Reduction, Context/Token Tracking, Compaction, Token Ledger |
| June 2026 | [CHANGELOG-2026-06.md](docs/changelogs/CHANGELOG-2026-06.md) | PerfPilot Agents Framework, Orchestrator, Execution Agent, Playwright Integration, CopilotKit Frontend |
| May 2026 | [CHANGELOG-2026-05.md](docs/changelogs/CHANGELOG-2026-05.md) | PerfMemory Taxonomy, EntraID Correlation Engine, SharePoint MCP, FastMCP v3 Migration, PerfPilot Hub |
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills Migration, Cursor Subagents, PerfMemory MCP + AGE Graph, MS Teams MCP, KPI Analysis, JMeter Script Validator |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL Editing Tools, Correlation Analysis v0.6/v0.7, AI-Assisted Debugging, Artifact Path Alignment, BlazeMeter Shared Folders |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI Adapter, HAR Adapter, Centralized SLA Config, JMeter Log Analysis, Bottleneck Analyzer v0.2 |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-Assisted Report Revision, Datadog Dynamic Limits, Report Enhancements, New Charts |

---

*Last Updated: August 22, 2026*
