# ✈️ PerfPilot-Hub

> **An open-source AI co-pilot for the Performance Testing Lifecycle — from test creation to execution, monitoring, analysis, reporting, and delivery.**

> ⚠️ **This project is under active development.**
> Repository structure, APIs, setup instructions, Docker files, and documentation may change frequently while the platform is being assembled.

---

## 🚀 Overview

**PerfPilot-Hub** is an AI-assisted performance testing platform that brings together:

* 🤖 **Multi-agent orchestration** for performance testing workflows
* 🛩️ **Gateway MCP**, a unified MCP gateway for performance testing tools
* 🧪 **JMeter script generation and execution**
* 🔥 **BlazeMeter test execution and result collection**
* 📊 **Datadog metrics, logs, and APM correlation**
* 🧠 **PerfMemory**, a persistent AI memory layer for debugging and lessons learned
* 📄 **Performance analysis and reporting**
* 💬 **Collaboration integrations** such as Confluence, MS Teams, and SharePoint
* 🖥️ **A browser-based CopilotKit / React UI** for human-in-the-loop workflows

PerfPilot-Hub extends the original `mcp-perf-suite` project into a broader platform that combines MCP tools, AI agents, an A2A server, an AG-UI backend, and a web frontend under one repository. The repository has been renamed from `mcp-perf-suite` to [`perfpilot-hub`](https://github.com/canyonlabz/perfpilot-hub) to reflect this evolution.

---

## 🎯 Vision

Performance testing often requires a human to move between many disconnected tools:

1. Capture or design a user flow
2. Generate a JMeter script
3. Debug correlation and test data issues
4. Execute a load test
5. Monitor infrastructure and application metrics
6. Analyze bottlenecks
7. Write a report
8. Publish results
9. Notify stakeholders

**PerfPilot-Hub** is designed to turn that fragmented process into an agent-assisted workflow.

The goal is not to remove the human from the process. The goal is to give performance engineers an AI co-pilot that can handle the repetitive work while keeping humans in control of consequential decisions such as launching tests, approving reports, and publishing results.

---

## ✈️ Naming Convention: The PerfPilot-Hub Aviation Model

PerfPilot-Hub uses an aviation-inspired naming model to make the platform easier to understand, remember, and explain.

Performance testing has many parallels to aviation: every test needs a plan, a controlled launch, real-time monitoring, telemetry, communication, analysis, and a final flight record. PerfPilot applies that metaphor to the Performance Testing Lifecycle while keeping the actual repository structure practical and developer-friendly.

The codebase uses clear folder names such as `agent-framework/`, `mcp-perf-suite/`, `docker/`, and `docs/`. The aviation names are used in the documentation and product language so users can quickly understand how the pieces fit together.

| Product Name      | Technical Component                        | Meaning                                                                                                          |
| ----------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| **PerfPilot-Hub**     | Overall framework and orchestrator concept | The pilot coordinating the full performance testing mission                                                      |
| **Pilot**             | Orchestrator agent                         | Plans the workflow, delegates tasks, and keeps humans in control                                                 |
| **Copilots**          | Specialized agents                         | Domain-specific agents that assist with scripting, execution, monitoring, analysis, reporting, and notifications |
| **Gateway MCP**       | `mcp-perf-suite/gateway-mcp/`              | The central MCP gateway that gives agents one endpoint for the full performance testing toolchain                |
| **FlightDeck**        | `agent-framework/frontend/`                | The human-facing web UI where users interact with PerfPilot                                                      |
| **ACARS**             | A2A server inside `agent-framework/`       | The agent-to-agent communication layer for upstream and downstream AI frameworks                                 |
| **Flight Log**        | Test artifacts, reports, and run history   | The record of what happened during a performance testing mission                                                 |
| **Black Box**         | PerfMemory and persisted debugging context | The memory layer that helps agents recall prior issues, fixes, and lessons learned                               |

In short: **PerfPilot-Hub is the pilot, the specialist agents are copilots, and PerfPilot Hub is the airport-style tool hub where they access the systems needed to complete the mission.**

This gives users a simple mental model:

> “My PerfPilots are handling my performance testing workflow, while I stay in control from the FlightDeck.”

---

## 🧭 Repository Structure

```text
perfpilot-hub/
├── agent-framework/       # AG2 agents, A2A server, AG-UI backend, CopilotKit frontend
│   ├── frontend/          # CopilotKit / React / Next.js web UI (FlightDeck)
│   └── backend/           # Python AG-UI server and A2A server
├── aspire/                # .NET Aspire AppHost — recommended local orchestration
├── mcp-perf-suite/        # Gateway + all MCP servers
│   ├── gateway-mcp/       # PerfPilot Hub — unified MCP gateway via FastMCP 3.4.x
│   ├── blazemeter-mcp/    # BlazeMeter API integration (optional)
│   ├── confluence-mcp/    # Confluence publishing integration (optional)
│   ├── datadog-mcp/       # Datadog metrics, logs, and APM integration (optional)
│   ├── github-mcp/        # GitHub Contents API integration (optional)
│   ├── jmeter-mcp/        # JMeter script generation and execution tools
│   ├── msteams-mcp/       # MS Teams notifications (optional, local only)
│   ├── perfanalysis-mcp/  # Performance analysis and correlation tools
│   ├── perfmemory-mcp/    # AI memory backed by PostgreSQL, pgvector, and Apache AGE
│   ├── perfreport-mcp/    # Report generation tools
│   ├── sharepoint-mcp/    # SharePoint artifact storage (optional, local only)
│   ├── artifacts/         # Test artifacts, reports, JTLs, logs, and generated files
│   └── streamlit-ui/      # Web UI for viewing performance test results
├── docker/                # Per-MCP Dockerfiles, compose files, and deployment (see docker/README.md)
├── tests/                 # Unit tests and regression coverage (CI in progress)
└── docs/                  # Public documentation
```

---

## 🧠 Core Concepts

### 🛩️ PerfPilot Gateway

**PerfPilot Gateway** is the central MCP gateway. Instead of connecting an AI agent to many separate MCP servers, PerfPilot Hub exposes the performance testing toolchain through one MCP endpoint.

It routes requests to eight gateway-mounted MCP servers: JMeter, BlazeMeter, Datadog, PerfAnalysis, PerfReport, Confluence, PerfMemory, and GitHub. Playwright MCP runs as a direct endpoint (not gateway-mounted). MS Teams and SharePoint run as local stdio servers and are not part of the gateway.

### 🤖 Pilot and Copilots

The **Pilot** is the orchestrator agent. It understands the user’s request, creates a plan, delegates work, coordinates progress, and keeps the human in control.

The **Copilots** are specialist agents. Each Copilot focuses on a specific phase of the Performance Testing Lifecycle, such as script generation, test execution, monitoring, analysis, reporting, or notifications.

Together, the Pilot and Copilots form the agent layer of PerfPilot.

Current agents:

| Agent                  | Purpose                                                         |
| ---------------------- | --------------------------------------------------------------- |
| 🎯 Orchestrator Agent  | Coordinates the full workflow and delegates work to specialists |
| 📝 Script Agent        | Generates or adapts performance test scripts                    |
| 🚀 Execution Agent     | Starts and monitors performance test execution                  |
| 📊 Monitoring Agent    | Pulls infrastructure, application, logs, and APM data           |
| 🔍 Analysis Agent      | Correlates test results with monitoring data                    |
| 📄 Reporting Agent     | Drafts performance test reports                                 |
| 📣 Notifications Agent | Sends summaries, links, and status updates to stakeholders      |

### 🖥️ FlightDeck

**FlightDeck** is the human-facing web UI. It gives users a cockpit-style command surface for chatting with PerfPilot, reviewing workflow progress, approving human-in-the-loop actions, and viewing results.

The implementation lives under `agent-framework/frontend/`, but the product experience is called **PerfPilot FlightDeck**.

It is built around:

* Next.js
* React
* CopilotKit
* AG-UI
* A Python backend
* Persistent conversation and workflow state

### 📡 ACARS

**ACARS** is the agent-to-agent communication layer. In aviation, ACARS is associated with structured aircraft communication. In PerfPilot, the term represents the A2A server that allows upstream and downstream AI frameworks to communicate with the PerfPilot agent runtime.

The implementation lives inside `agent-framework/backend/`.

> "PerfPilot gives performance engineers the feeling that “my PerfPilots are handling my performance testing workflow” — while the human remains in command."

---

## 🏗️ High-Level Architecture

```text
┌──────────────────────────────────────────────────────────────────┐
│                          Human Users                             │
│                                                                  │
│  Browser UI / Cursor / Claude / Other AI Agent Frameworks        │
└───────────────────────────────┬──────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                                   ▼
┌──────────────────────┐             ┌──────────────────────┐
│  AG-UI (port 8102)   │             │  A2A (port 8101)     │
│  Browser / Web UI    │             │  External AI agents  │
└──────────┬───────────┘             └──────────┬───────────┘
           └─────────────────┬──────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                       PerfPilot Agent Layer                      │
│                                                                  │
│  Orchestrator Agent                                              │
│     ├── Script Agent                                             │
│     ├── Execution Agent                                          │
│     ├── Monitoring Agent                                         │
│     ├── Analysis Agent                                           │
│     ├── Reporting Agent                                          │
│     └── Notifications Agent                                      │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Gateway MCP (port 8125)                       │
│         8 mounted servers via FastMCP create_proxy()             │
├──────────┬──────────┬──────────┬──────────┬──────────────────────┤
│ JMeter   │BlazeMeter│ Datadog  │PerfAnalys│ PerfReport           │
│ MCP      │ MCP      │ MCP      │ MCP      │ MCP                  │
├──────────┴──────────┼──────────┴──────────┴──────────────────────┤
│ Confluence MCP      │ PerfMemory MCP      │ GitHub MCP           │
└─────────────────────┴─────────────────────┴──────────────────────┘

  Direct (not gateway-mounted):
  ┌─────────────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Playwright MCP      │  │ MS Teams MCP │  │ SharePoint   │
  │ (port 8117)         │  │ (local stdio)│  │ MCP (stdio)  │
  └─────────────────────┘  └──────────────┘  └──────────────┘
```

---

## 🔄 Example Workflow

A future end-to-end PerfPilot workflow may look like this:

1. A user submits a request through the Web UI, Cursor, Claude, or an upstream AI framework.
2. The Orchestrator Agent creates a performance testing plan.
3. The Script Agent generates or updates a JMeter script.
4. The human reviews and approves the generated script.
5. The Execution Agent starts a test through BlazeMeter or another load testing backend.
6. The Monitoring Agent collects metrics, logs, and traces during the test.
7. The Analysis Agent correlates load test results with application and infrastructure telemetry.
8. The Reporting Agent drafts an executive-friendly report.
9. The human reviews, revises, and approves the report.
10. The report is published to Confluence and/or archived to SharePoint.
11. Stakeholders are notified through MS Teams or another notification channel.

---

## 🧰 Technology Stack

| Layer                        | Technology                       |
| ---------------------------- | -------------------------------- |
| Agent framework              | AG2                              |
| Agent-to-agent communication | A2A                              |
| Tool protocol                | MCP                              |
| MCP gateway                  | FastMCP                          |
| Backend APIs                 | Python / FastAPI                 |
| Browser UI                   | Next.js, React, CopilotKit       |
| Database                     | PostgreSQL                       |
| Vector memory                | pgvector                         |
| Knowledge graph memory       | Apache AGE                       |
| Load testing                 | JMeter, BlazeMeter               |
| Observability                | Datadog                          |
| Reporting / collaboration    | Confluence, SharePoint, MS Teams |
| Local orchestration          | .NET Aspire                      |

---

## 📦 Project Status

| Area                 | Status              |
| -------------------- | ------------------- |
| MCP Perf Suite       | ✅ Working          |
| Gateway MCP          | ✅ Working          |
| Agent Framework      | ✅ Working          |
| A2A Server           | ✅ Working          |
| AG-UI Backend        | ✅ Working          |
| CopilotKit Web UI    | Active development  |
| Aspire orchestration | ✅ Working          |
| Docker images        | ✅ Working          |
| Public documentation | In progress         |

---

## ▶️ Getting Started

The recommended way to run PerfPilot locally is with [.NET Aspire](https://aspire.dev). One command starts the database, all MCP servers, the gateway, the agent backends, and the Web UI.

```bash
# 1. Clone the repository
git clone https://github.com/canyonlabz/perfpilot-hub.git
cd perfpilot-hub

# 2. Set your secrets (one-time)
#    See docs/aspire/aspire-setup-guide.md for the full list
dotnet user-secrets set "Parameters:openai-api-key" "<your-key>" --id perfpilot-aspire-apphost

# 3. Start everything
cd aspire
aspire run
```

The Aspire dashboard opens automatically. All 14 resources start in dependency order with health checks.

See [`docs/aspire/aspire-setup-guide.md`](docs/aspire/aspire-setup-guide.md) for prerequisites, the full secrets list, and troubleshooting.

> **Optional integrations:** BlazeMeter, Datadog, Confluence, GitHub, MS Teams, and SharePoint each require their own API keys or tokens. PerfPilot works without them — you just won't have access to those specific tools. Set only the secrets you need.

### 🐳 Docker Compose (optional)

Docker Compose files are included in `docker/` for building individual images or running the stack without Aspire. See [`docker/README.md`](docker/README.md) for details.

---

## 🗺️ Roadmap

Planned areas of work include:

* [x] ~~One-command local startup~~ — ✅ `aspire run` starts all 14 resources
* [x] ~~Persistent multi-thread conversation history~~ — ✅ PostgreSQL-backed threads survive restarts
* [x] ~~Specialist agents for scripting, execution, monitoring, analysis, and reporting~~ — ✅ Working
* [ ] Human-in-the-loop approval cards in the Web UI
* [ ] Task progress streaming and test-run result views in the Web UI
* [ ] Contribution guidelines

---

## 🤝 Contributing

Contributions, ideas, and feedback are welcome.

This project is still early and moving quickly, so please expect breaking changes while the architecture stabilizes.

---

## 📜 License

This project is licensed under the MIT License.

See the `LICENSE` file for details.

---

Created with ❤️ for performance engineers, quality engineers, SREs, and AI-assisted testing workflows.
