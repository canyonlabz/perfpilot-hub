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

PerfPilot-Hub extends the original [`mcp-perf-suite`](https://github.com/canyonlabz/mcp-perf-suite) project into a broader platform that combines MCP tools, AI agents, an A2A server, an AG-UI backend, and a web frontend under one repository.

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
perfpilot/
├── agent-framework/       # AG2 agents, A2A server, AG-UI backend, CopilotKit frontend
│   ├── frontend/          # CopilotKit / React / Next.js web UI
│   └── backend/           # Python AG-UI server and A2A server
├── mcp-perf-suite/        # Gateway + all MCP servers
│   ├── gateway-mcp/       # PerfPilot Hub — unified MCP gateway via FastMCP
│   ├── blazemeter-mcp/    # BlazeMeter API tools
│   ├── confluence-mcp/    # Confluence publishing tools
│   ├── datadog-mcp/       # Datadog metrics, logs, and APM tools
│   ├── jmeter-mcp/        # JMeter script generation and execution tools
│   ├── perfanalysis-mcp/  # Performance analysis and correlation tools
│   ├── perfmemory-mcp/    # AI memory backed by PostgreSQL, pgvector, and Apache AGE
│   ├── perfreport-mcp/    # Report generation tools
│   ├── artifacts/         # Test artifacts, reports, JTLs, logs, and generated files
│   └── streamlit-ui/      # Web UI for viewing performance test results
├── docker/                # Compose files, Dockerfiles, and config templates
└── docs/                  # Public documentation
```

---

## 🧠 Core Concepts

### 🛩️ PerfPilot Gateway

**PerfPilot Gateway** is the central MCP gateway. Instead of connecting an AI agent to many separate MCP servers, PerfPilot Hub exposes the performance testing toolchain through one MCP endpoint.

It routes requests to specialized MCP servers such as JMeter, BlazeMeter, Datadog, PerfAnalysis, PerfReport, Confluence, PerfMemory, MS Teams, and SharePoint.

### 🤖 Pilot and Copilots

The **Pilot** is the orchestrator agent. It understands the user’s request, creates a plan, delegates work, coordinates progress, and keeps the human in control.

The **Copilots** are specialist agents. Each Copilot focuses on a specific phase of the Performance Testing Lifecycle, such as script generation, test execution, monitoring, analysis, reporting, or notifications.

Together, the Pilot and Copilots form the agent layer of PerfPilot.

Planned and/or evolving agents include:

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
│                                                                  │
│  Surfaces:                                                       │
│     - A2A server for agent-to-agent workflows                    │
│     - AG-UI backend for browser-based human interaction          │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                         PerfPilot Hub                            │
│                                                                  │
│  Unified MCP gateway exposing performance testing tools          │
└───────────────────────────────┬──────────────────────────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
┌───────────────┐       ┌────────────────┐        ┌─────────────────┐
│ JMeter MCP    │       │ BlazeMeter MCP │        │ Datadog MCP     │
└───────────────┘       └────────────────┘        └─────────────────┘
        ▼                       ▼                        ▼
┌───────────────┐       ┌────────────────┐        ┌─────────────────┐
│ PerfAnalysis  │       │ PerfReport     │        │ PerfMemory      │
│ MCP           │       │ MCP            │        │ MCP             │
└───────────────┘       └────────────────┘        └─────────────────┘
        ▼                       ▼                        ▼
┌───────────────┐       ┌────────────────┐        ┌─────────────────┐
│ Confluence    │       │ MS Teams       │        │ SharePoint      │
│ MCP           │       │ MCP            │        │ MCP             │
└───────────────┘       └────────────────┘        └─────────────────┘
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
| Local orchestration          | Docker Compose                   |

---

## 📦 Project Status

PerfPilot is currently being assembled from the original `mcp-perf-suite` project and the experimental agent-framework branch.

| Area                 | Status                                               |
| -------------------- | ---------------------------------------------------- |
| MCP Perf Suite       | Existing project being moved under the new root repo |
| PerfPilot Hub        | Existing MCP gateway concept from `gateway-mcp`      |
| Agent Framework      | Active development                                   |
| A2A Server           | Active development                                   |
| AG-UI Backend        | Active development                                   |
| CopilotKit Web UI    | Active development                                   |
| Docker Compose       | Planned / evolving                                   |
| Public documentation | In progress                                          |

---

## ▶️ Getting Started

> The repository is currently in early setup. Full installation instructions will be added as the folder structure stabilizes.

For now, the expected local development flow will be:

```bash
# Clone the repository
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd mcp-perf-suite
```

Then follow the setup instructions inside each major module:

| Module          | Path               | Purpose                                                            |
| --------------- | ------------------ | ------------------------------------------------------------------ |
| Agent Framework | `agent-framework/` | Multi-agent orchestration, A2A server, AG-UI backend, and frontend |
| MCP Perf Suite  | `mcp-perf-suite/`  | MCP gateway and specialized performance testing MCP servers        |
| Docker          | `docker/`          | Local containers, databases, and service orchestration             |
| Docs            | `docs/`            | Public documentation and architecture notes                        |

---

## 🗺️ Roadmap

Planned areas of work include:

* [ ] Normalize environment configuration across agents, MCPs, and Docker
* [ ] Add root-level Docker Compose orchestration
* [ ] Add one-command local startup for database, MCP gateway, agents, and UI
* [ ] Expand specialist agents beyond the first working vertical slices
* [ ] Add human-in-the-loop approval cards in the Web UI
* [ ] Add persistent multi-thread conversation history
* [ ] Add task progress streaming and test-run result views
* [ ] Add public architecture documentation
* [ ] Add contribution guidelines

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
