# ✈️ PerfPilot Agents — Backend

> **The Python server and AI agent runtime that powers PerfPilot.**

---

## Overview

The `backend/` directory contains everything needed to run the PerfPilot AI agent system. This is where the orchestrator and specialist agents live, where requests from the Web UI and external AI frameworks are handled, and where all business logic, data persistence, and agent coordination takes place.

The backend serves two audiences through two server surfaces:

| Surface | Port | Who uses it | What it does |
|---------|------|-------------|--------------|
| **AG-UI server** | `8002` | Humans via the Web UI (browser) | Powers the chat interface, conversation history, and human approval workflows |
| **A2A server** | `8001` | Other AI agent frameworks (machine-to-machine) | Lets upstream and downstream AI systems discover and communicate with PerfPilot agents |

Both surfaces route to the **same agent runtime**. A message sent from the Web UI and a task submitted by an external AI framework hit the same orchestrator, run through the same specialists, and persist to the same database. There is one backend with two entry points.

---

## Folder Structure

```text
backend/
├── a2a_server.py              # A2A server entrypoint (port 8001)
├── agui_server.py             # AG-UI server entrypoint (port 8002)
│
├── agents/                    # AI agents — one folder per agent
│   ├── orchestrator/          # Coordinates the full workflow
│   ├── script-agent/          # Generates performance test scripts
│   ├── execution-agent/       # Runs tests via BlazeMeter
│   ├── monitoring-agent/      # Pulls metrics from Datadog during tests
│   ├── analysis-agent/        # Correlates results and identifies bottlenecks
│   ├── reporting-agent/       # Drafts performance reports
│   └── notifications-agent/   # Sends status updates to stakeholders
│
├── config/                    # Global YAML configuration files
├── sql/                       # Database schemas and migration scripts
│
├── core/                      # Shared models and foundational types (planned)
├── services/                  # Business logic, orchestration, and integrations
├── stores/                    # Data persistence (database read/write operations)
├── utils/                     # Utility and helper functions
│
└── workflows/                 # Agent-to-agent workflow pipelines
```

> **Note:** The `core/` folder is part of a planned architecture refinement. It may be consolidated into the existing folders as the structure stabilizes.

---

## What Each Folder Does

### `agents/`

Contains one subfolder for each AI agent. Every agent follows a consistent **four-file pattern**:

| File | Purpose |
|------|---------|
| `agent.py` | The agent's Python logic — what it does and how it does it |
| `agent_card.json` | A discovery card that describes the agent's name, capabilities, and status |
| `INSTRUCTIONS.md` | Natural-language instructions the agent follows when processing requests |
| `config.yaml` | Per-agent settings such as which LLM to use and which MCP tools it can access |

This pattern makes every agent self-contained and easy to understand at a glance. Adding a new specialist means creating a new folder with these four files.

### `config/`

Global configuration files that apply across all agents. This includes the default LLM provider settings, the per-agent enable/disable map, and shared runtime tunables. Individual agents can override these defaults in their own `config.yaml`.

### `sql/`

Database schema definitions and migration scripts for the `perfagent_state` PostgreSQL database. This database stores conversation history, session state, task progress, human approval records, and execution traces — everything the agents need to persist state across restarts and resume conversations days later.

### `services/`

Business logic and orchestration code. This is where the core work happens:

- **Task execution** — running agent workflows, coordinating multi-step operations, and managing the lifecycle of each request from start to finish
- **LLM provider abstraction** — a single interface that works with OpenAI, Azure OpenAI, or Ollama so agents are not locked to one AI model provider
- **MCP client** — the connection layer that routes agent tool calls through PerfPilot Hub (the unified MCP gateway) to reach JMeter, BlazeMeter, Datadog, and other performance testing tools
- **User identity resolution** — determining who is making a request, whether it comes from the Web UI, a direct API call, or an upstream AI framework

### `stores/`

The data persistence layer. Each store module handles read and write operations for a specific area of the database:

- **Sessions** — tracking who is connected and when
- **Threads** — persistent conversations that survive across browser sessions
- **Tasks** — the lifecycle of each agent request (created, running, completed, failed)
- **Conversations** — the full message history for every thread
- **HITL approvals** — records of human-in-the-loop decisions (approved, rejected, pending)
- **Traces** — execution logs for debugging and auditing agent tool calls

### `utils/`

Stateless helper functions used across the backend. This includes configuration file loading, encoding helpers, and reusable formatting utilities. Unlike `services/`, nothing in `utils/` connects to external systems or manages state.

### `workflows/`

Agent-to-agent workflow pipelines that chain multiple specialists together for end-to-end operations. For example, a full performance testing workflow might chain: script generation → test execution → metric collection → analysis → report generation → delivery.

### `a2a_server.py` and `agui_server.py`

The two server entrypoints sit directly at the `backend/` root. `a2a_server.py` starts the A2A server on port 8001 for machine-to-machine agent communication. `agui_server.py` starts the AG-UI server on port 8002 for browser-based human interaction through the Web UI. Both are FastAPI applications launched with Uvicorn.

### `core/` *(planned)*

Will contain foundational types and shared data models that multiple layers depend on — such as the A2A protocol models, error types, and shared definitions. These are currently part of `utils/` and may be separated into their own layer for clarity, or may remain in `utils/` if the separation is not needed.

---

## The Agents

PerfPilot uses a **pilot-and-copilots model**. One orchestrator coordinates the mission, and specialist agents handle the work for each phase of the performance testing lifecycle.

### 🎯 Orchestrator

The orchestrator is the central coordinator. When a request comes in — whether from a human in the Web UI or from another AI framework — the orchestrator:

1. Understands what is being asked
2. Creates a plan
3. Delegates work to the right specialists
4. Tracks progress across all delegated tasks
5. Requests human approval when a consequential decision is needed
6. Returns the final result

The orchestrator never calls performance testing tools directly. It delegates to specialists, and the specialists do the hands-on work.

### Specialists

Each specialist focuses on one phase of the performance testing lifecycle:

| Agent | What it does |
|-------|--------------|
| 📝 **Script Agent** | Generates or adapts JMeter performance test scripts. Can record browser sessions via Playwright, convert HAR files, process Swagger/OpenAPI specs, or modify existing scripts. |
| 🚀 **Execution Agent** | Launches performance tests through BlazeMeter, monitors progress, and collects test artifacts (results, logs, JTL files) when the run completes. |
| 📊 **Monitoring Agent** | Pulls infrastructure and application metrics from Datadog during and after a test — host metrics, Kubernetes metrics, APM traces, and logs. |
| 🔍 **Analysis Agent** | Correlates load test results with monitoring data to identify bottlenecks, validate SLA thresholds, and surface root causes. |
| 📄 **Reporting Agent** | Drafts performance test reports, generates charts, and publishes to Confluence. Supports multi-round revision — if the human sends the report back with feedback, the agent revises and resubmits. |
| 📣 **Notifications Agent** | Sends test status updates and result summaries to stakeholders through MS Teams, SharePoint, or other notification channels. |

All specialists access external systems exclusively through **MCP tools** served by PerfPilot Hub. Each agent has a namespace allowlist in its configuration that controls which tools it can use — the execution agent can call BlazeMeter tools but not Confluence tools, for example.

---

## Human-in-the-Loop

PerfPilot agents are autonomous in *how* they do the work — generating scripts, correlating metrics, drafting reports — but they never take a consequential action without human approval:

- **Launching a load test** — the execution agent requests approval before starting
- **Publishing a report** — the reporting agent requests approval before sending to Confluence
- **Spending cloud resources** — any action with cost implications is gated

When an agent needs approval, it creates a prompt that appears in the Web UI (or is sent to an external framework via A2A). The human can approve, reject, or reject with feedback. If rejected with feedback, the agent revises and resubmits — creating a natural back-and-forth revision loop until the human is satisfied.

All approval records are persisted in the database, so nothing is lost if the browser is closed or the server restarts.

---

## How the Backend Fits In

The backend is one part of the larger PerfPilot ecosystem:

```text
┌─────────────────────────────────────────────────┐
│           agent-framework/                      │
│                                                 │
│   ┌──────────────┐       ┌──────────────┐       │
│   │   frontend/  │       │   backend/   │       │
│   │              │       │              │       │
│   │  Web UI      │◄─────►│  Agents      │       │
│   │  (React /    │ AG-UI │  Servers     │       │
│   │   Next.js)   │       │  Database    │       │
│   └──────────────┘       └──────┬───────┘       │
│                                 │               │
└─────────────────────────────────┼───────────────┘
                                  │ MCP
                                  ▼
                    ┌──────────────────────────┐
                    │     mcp-perf-suite/      │
                    │                          │
                    │  Gateway MCP             │
                    │  + JMeter, BlazeMeter,   │
                    │    Datadog, Confluence,  │
                    │    PerfMemory, and more  │
                    └──────────────────────────┘
```

- The **frontend** is the browser-based Web UI where humans interact with PerfPilot
- The **backend** is the agent runtime, servers, and database that power everything behind the scenes
- **mcp-perf-suite** provides the performance testing tools that agents call through PerfPilot Hub

---

## Persistent Conversations

The backend is built for **multi-user, multi-thread, persistent conversations** from day one:

- **Thread isolation** — each user's conversation history is private and invisible to other users
- **Persistent history** — close the browser, come back the next day, and the conversation picks up where it left off
- **Multi-device** — the same conversation is accessible from any browser or device
- **A2A thread resumption** — external AI frameworks can resume a prior conversation by passing a thread identifier, or start a new one and receive a thread ID back for future reference

All state lives in the `perfagent_state` PostgreSQL database, not in browser memory or server-side caches.

---

## Technology

| Area | Technology |
|------|------------|
| Agent framework | AG2 |
| Agent-to-agent protocol | A2A |
| Tool protocol | MCP (via PerfPilot Hub) |
| Backend servers | Python, FastAPI, Uvicorn |
| Database | PostgreSQL (with pgvector and Apache AGE) |
| LLM providers | OpenAI, Azure OpenAI, Ollama (pluggable) |

---

## Getting Started

For setup instructions, prerequisites, and how to run the backend alongside the database, MCP gateway, and Web UI, see the [Agent Framework README](../README.md#-try-it-today).

---

## License

MIT — see [LICENSE](../../LICENSE).
