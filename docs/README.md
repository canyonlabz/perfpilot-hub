# 📚 Documentation Overview

Welcome to the **Documentation Hub** for **PerfPilot Hub**!
This folder contains all reference materials, specifications, templates, and technical guides used across the suite of MCP servers and the agent framework.

The goal of this directory is to help end-users, contributors, and performance engineers quickly understand:

* How each MCP server works
* How to configure them
* How to customize templates
* How to use the available tools
* How the MCP servers and agent framework integrate across the ecosystem

---

## 📁 Folder Structure

```
docs/
├── README.md                          ← You are here
├── artifacts_guide.md
├── large_file_handling.md
├── metrics_calculations_and_display.md
│
├── a2a/
│   └── a2a-server-data-contracts.md
│
├── agent-framework/
│   ├── a2a_new_jmx_pipeline_guide.md
│   └── hitl-configuration.md
│
├── aspire/
│   └── aspire-setup-guide.md
│
├── changelogs/
│   ├── CHANGELOG-2026-01.md
│   ├── CHANGELOG-2026-02.md
│   ├── CHANGELOG-2026-03.md
│   ├── CHANGELOG-2026-04.md
│   ├── CHANGELOG-2026-05.md
│   ├── CHANGELOG-2026-06.md
│   ├── CHANGELOG-2026-07.md
│   ├── CHANGELOG-2026-08.md
│   └── CHANGELOG-2026-09.md
│
├── database/
│   ├── apache_age_installation_guide.md
│   ├── apache_age_viewer_guide.md
│   ├── perfmemory_user_guide.md
│   └── pgvector_installation_guide.md
│
├── datadog-mcp/
│   └── datadog_query_guide.md
│
├── engineering/
│   ├── development-workflow.md
│   └── technology-stack.md
│
├── jmeter-mcp/
│   ├── correlation_analysis_guide.md
│   ├── har_adapter_guide.md
│   ├── har_jmx_comparison_guide.md
│   ├── jmeter_hitl_user_guide.md
│   └── jmeter_mcp_configuration_guide.md
│
├── perfanalysis-mcp/
│   ├── perfanalysis_techniques_guide.md
│   └── sla_configuration_guide.md
│
├── perfreport-mcp/
│   └── report_template_guidelines.md
│
├── playwright-mcp/
│   └── playwright_mcp_configuration_guide.md
│
└── troubleshooting/
    ├── age-viewer-troubleshooting.md
    ├── docker-gateway-macos-build-and-runtime.md
    ├── msteams-mcp-troubleshooting.md
    ├── norton-360-ssl-interception.md
    ├── pgvector-fix-on-macbook.md
    ├── rancher-desktop-fix.md
    └── rancher-desktop-port-forwarding.md
```

---

## 🗂️ Guide Index

### 🚀 Aspire & Orchestration

| Document | Description |
|----------|-------------|
| [`aspire/aspire-setup-guide.md`](aspire/aspire-setup-guide.md) | Full Aspire setup, secrets provisioning, smoke checks, and troubleshooting |

### 🤖 Agent Framework

| Document | Description |
|----------|-------------|
| [`agent-framework/a2a_new_jmx_pipeline_guide.md`](agent-framework/a2a_new_jmx_pipeline_guide.md) | A2A + Web UI new-JMX pipeline metadata reference for upstream integrations |
| [`agent-framework/hitl-configuration.md`](agent-framework/hitl-configuration.md) | Human-in-the-Loop approval configuration and behavior |
| [`a2a/a2a-server-data-contracts.md`](a2a/a2a-server-data-contracts.md) | A2A server data contracts and request/response schemas |

### 🔌 MCP Server Guides

| Document | Description |
|----------|-------------|
| [`jmeter-mcp/jmeter_mcp_configuration_guide.md`](jmeter-mcp/jmeter_mcp_configuration_guide.md) | JMeter MCP configuration reference |
| [`jmeter-mcp/jmeter_hitl_user_guide.md`](jmeter-mcp/jmeter_hitl_user_guide.md) | AI-assisted Human-in-the-Loop JMeter script editing |
| [`jmeter-mcp/har_adapter_guide.md`](jmeter-mcp/har_adapter_guide.md) | HAR-to-JMeter conversion guide |
| [`jmeter-mcp/har_jmx_comparison_guide.md`](jmeter-mcp/har_jmx_comparison_guide.md) | HAR vs JMX comparison reference |
| [`jmeter-mcp/correlation_analysis_guide.md`](jmeter-mcp/correlation_analysis_guide.md) | JMeter correlation analysis and variable naming |
| [`datadog-mcp/datadog_query_guide.md`](datadog-mcp/datadog_query_guide.md) | Datadog custom query configuration |
| [`perfanalysis-mcp/perfanalysis_techniques_guide.md`](perfanalysis-mcp/perfanalysis_techniques_guide.md) | Analytical techniques and algorithms reference |
| [`perfanalysis-mcp/sla_configuration_guide.md`](perfanalysis-mcp/sla_configuration_guide.md) | SLA threshold configuration |
| [`perfreport-mcp/report_template_guidelines.md`](perfreport-mcp/report_template_guidelines.md) | Performance report template authoring rules |
| [`playwright-mcp/playwright_mcp_configuration_guide.md`](playwright-mcp/playwright_mcp_configuration_guide.md) | Playwright MCP configuration and browser automation |

### 🗄️ Database

| Document | Description |
|----------|-------------|
| [`database/perfmemory_user_guide.md`](database/perfmemory_user_guide.md) | PerfMemory + debugging workflows and example prompts |
| [`database/pgvector_installation_guide.md`](database/pgvector_installation_guide.md) | pgvector installation and setup |
| [`database/apache_age_installation_guide.md`](database/apache_age_installation_guide.md) | Apache AGE graph extension installation |
| [`database/apache_age_viewer_guide.md`](database/apache_age_viewer_guide.md) | AGE Viewer setup and usage |

### 📦 General Reference

| Document | Description |
|----------|-------------|
| [`artifacts_guide.md`](artifacts_guide.md) | Artifacts folder structure and local-first architecture |
| [`large_file_handling.md`](large_file_handling.md) | Large JTL/CSV file handling and memory optimization |
| [`metrics_calculations_and_display.md`](metrics_calculations_and_display.md) | Metrics calculations and display conventions |

### ⚙️ Engineering

| Document | Description |
|----------|-------------|
| [`engineering/technology-stack.md`](engineering/technology-stack.md) | Technology inventory — observed versions, constraints, and official references |
| [`engineering/development-workflow.md`](engineering/development-workflow.md) | Development workflow, coding standards, and testing conventions |

### 🩺 Troubleshooting

| Document | Description |
|----------|-------------|
| [`troubleshooting/docker-gateway-macos-build-and-runtime.md`](troubleshooting/docker-gateway-macos-build-and-runtime.md) | Docker gateway macOS build and runtime issues |
| [`troubleshooting/pgvector-fix-on-macbook.md`](troubleshooting/pgvector-fix-on-macbook.md) | pgvector fix for macOS |
| [`troubleshooting/rancher-desktop-fix.md`](troubleshooting/rancher-desktop-fix.md) | Rancher Desktop troubleshooting |
| [`troubleshooting/rancher-desktop-port-forwarding.md`](troubleshooting/rancher-desktop-port-forwarding.md) | Rancher Desktop port forwarding setup |
| [`troubleshooting/norton-360-ssl-interception.md`](troubleshooting/norton-360-ssl-interception.md) | Norton 360 SSL interception workaround |
| [`troubleshooting/msteams-mcp-troubleshooting.md`](troubleshooting/msteams-mcp-troubleshooting.md) | MS Teams MCP troubleshooting |
| [`troubleshooting/age-viewer-troubleshooting.md`](troubleshooting/age-viewer-troubleshooting.md) | AGE Viewer troubleshooting |

### Monthly Changelogs

Monthly archives live in [`changelogs/`](changelogs/). The current month's
changes are tracked in the repository-root [`CHANGELOG.md`](../CHANGELOG.md).

---

## 🧭 Related READMEs

| Location | Description |
|----------|-------------|
| [`README.md`](../README.md) | Repository root — project overview and getting started |
| [`aspire/README.md`](../aspire/README.md) | Aspire orchestration quick start |
| [`docker/README.md`](../docker/README.md) | Docker Compose deployment guide |
| [`agent-framework/README.md`](../agent-framework/README.md) | Agent framework architecture and agents |
| [`agent-framework/frontend/ui/README.md`](../agent-framework/frontend/ui/README.md) | Web UI (Next.js + CopilotKit) |
| [`mcp-perf-suite/README.md`](../mcp-perf-suite/README.md) | MCP source tree overview |
| [`mcp-perf-suite/gateway-mcp/README.md`](../mcp-perf-suite/gateway-mcp/README.md) | Gateway MCP (unified endpoint) |
