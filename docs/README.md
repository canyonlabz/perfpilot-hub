# 📚 Documentation Overview

Welcome to the **Documentation Hub** for **PerfPilot-Hub**!
This folder contains all reference materials, specifications, templates, and technical guides used across the suite of MCP servers.

The goal of this directory is to help end-users, contributors, and performance engineers quickly understand:

* How each MCP server works
* How to configure them
* How to customize templates
* How to use the available tools
* How the MCP servers integrate across the ecosystem

---

## 📁 What’s Inside This Folder

This folder will grow over time. For now, here are the key documents:

### 📘 **1. Template Authoring Guidelines (`report_template_guidelines.md`)**

A detailed guide explaining how to create custom Markdown templates for performance reports, including:

* Supported Markdown syntax
* Placeholder reference
* Rules for Confluence-safe formatting
* Examples for API tables, summaries, and insights
* How the report generator fills in template variables

➡️ *Use this if you want to customize the look & layout of performance reports.*

---

### 📂 **2. HAR Adapter Guide (`har_adapter_guide.md`)**

A practical guide for Performance Test Engineers on converting HAR files into JMeter scripts using the `convert_har_to_capture` tool, including:

* When to use the HAR adapter vs. Playwright or Swagger inputs
* Configuration prerequisites (domain filtering, pseudo-header stripping)
* Step-by-step usage with parameter reference
* Step strategy selection (auto, page, time_gap, single_step)
* Output structure and capture manifest
* Troubleshooting common issues

➡️ *Use this if you have a HAR file and want to generate a JMeter script from it.*

---

### 📦 **3. Artifacts Guide (`artifacts_guide.md`)**

A comprehensive guide explaining how the MCP Performance Suite manages test data through the local filesystem, including:

* Why the suite is local-first (no database, no cloud storage)
* The role of `test_run_id` and how it organizes everything
* The `artifacts_path` configuration across all MCP servers
* Full directory structure with every subfolder and file explained
* Vendor folder conventions (`blazemeter/` vs `jmeter/`)
* How the AI HITL tools use artifacts as state (backups as revision history)
* Key files and their producer/consumer relationships
* Tips for managing and backing up artifacts

➡️ *Use this to understand where test data lives and how MCP servers share it.*

---

### 🤖 **4. JMeter HITL Editing Guide (`jmeter_hitl_user_guide.md`)**

A comprehensive guide for using the AI-assisted Human-in-the-Loop tools to analyze, add to, and edit JMeter JMX scripts, including:

* What scripts are supported (any valid JMX, not just AI-generated)
* Requirements: `test_run_id` and artifact folder conventions
* How to import an external/pre-existing JMX script
* The four script generation pipelines (External, Playwright, HAR, Swagger)
* Step-by-step HITL workflow: analyze → add → edit → verify
* Safety features: dry run, automatic backups, node ID stability
* Supported component types (36+ across 8 categories)
* Best practices and V2 roadmap

➡️ *Use this if you want to leverage AI to modify JMeter scripts without manually editing XML.*

---

### 🧠 **5. PerfMemory & Debugging User Guide (`perfmemory_user_guide.md`)**

A practical guide showing how PerfMemory MCP and JMeter MCP work together for AI-driven script debugging, including:

* Example prompts for every use case (search, store, ingest, browse, maintain)
* Both natural language prompts and underlying tool calls (for Cursor, Claude CLI, Codex CLI, etc.)
* Skills reference: which skills activate and when
* Integrated workflow walkthrough: what the PTE types vs. what the agent does
* Tips for writing high-quality symptom text for better search results
* FAQ covering embedding providers, team sharing, and offline use

➡️ *Use this if you want to debug JMeter scripts with AI memory — or if you're setting up PerfMemory for your team.*

---

### ⚙️ **6. MCP Configuration References (Coming Soon)** 

Planned documents:

#### **`config_reference.md`**

A unified explanation of the `config.yaml` files used across all MCP servers, including:

* Common fields (`general`, `artifacts`, `logging`, etc.)
* Server-specific sections
* Examples from:

  * `blazemeter-mcp`
  * `datadog-mcp`
  * `perfreport-mcp`
  * `confluence-mcp`
  * Any future MCP servers added to the suite

➡️ *This will help users understand exactly how to configure each server consistently.*

---

### 🔧 **7. MCP Tool Index (Coming Soon)**

Planned document:

#### **`mcp_tools_index.md`**

A single reference listing *all* tools exposed by each MCP server.
For each server, we will include:

* Server name
* Tool name
* Purpose
* Input arguments
* Example usage

Example snippet:

```
blazemeter-mcp
  • get_test_runs
  • start_test_run
  • stop_test_run
  • get_aggregate_report
```

➡️ *Useful for developers integrating MCP servers into automated pipelines or toolchains.*

> **Current state:** The suite runs on **FastMCP 3.4.x**. Each MCP server can run standalone or be accessed through the **PerfPilot Hub** (Gateway MCP) — a unified MCP gateway that exposes all servers and their tools under one endpoint. See [gateway-mcp/README.md](../mcp-perf-suite/gateway-mcp/README.md) for details.

---

### 🔬 **8. PerfAnalysis Analytical Techniques Guide (`perfanalysis_techniques_guide.md`)**

A comprehensive reference explaining every statistical algorithm and data science technique used by the PerfAnalysis MCP server, including:

* All statistical algorithms: percentiles, Pearson correlation, linear regression, Z-score anomaly detection, rolling median smoothing, Median Absolute Deviation (MAD), and more
* All data science techniques: SLA validation, temporal correlation, trend classification, outlier suppression, sustained degradation detection, multi-factor severity scoring
* The five-step analysis pipeline with workflow diagrams
* Deep-dive into the bottleneck detection engine: Phase 1 core detectors, Phase 2 infrastructure cross-reference, Phase 3 KPI-driven bottlenecks
* What PerfAnalysis produces (all output files and JSON structures)
* Configuration knobs that drive analysis behavior
* A glossary of performance testing and data science terms

Written in plain language for junior-to-mid-level Performance Test Engineers.

➡️ *Use this to understand how PerfAnalysis analyzes test results and what techniques it uses to identify bottlenecks.*

---

### 📊 **9. Large File Handling (`large_file_handling.md`)**

A technical reference explaining how the PerfAnalysis MCP handles large JTL/CSV files (200+ MB), including:

* Memory optimisation techniques (column selection, category dtypes, explicit type maps)
* Configurable row limits (`max_jtl_rows`)
* Why chunked processing is not used and when it would be appropriate
* Memory estimates and recommendations by file size
* Multi-engine concurrency correction

➡️ *Use this to understand the design decisions behind large file handling and known limitations.*

---

### 🔗 **10. A2A New-JMX Pipeline Guide (`agent-framework/a2a_new_jmx_pipeline_guide.md`)**

A reference for upstream AI agent frameworks and the PerfPilot Web UI that
need to drive the *new-JMX* pipeline (generate → push to Git → local
smoke → provision BlazeMeter test), including:

* Which surfaces the pipeline supports (A2A, Web UI, Cursor/MCP)
* Full A2A metadata schema (`environment`, `scm`, `blazemeter`,
  `test_run_id`)
* A minimal example A2A payload that a `curl` or Python client can send
* User-attributed vs user-agnostic GitHub token flow (per-request vs
  server env var)
* Which HITL gates apply to the pipeline and where they fire

➡️ *Use this if you are integrating an upstream framework (or a
custom UI) with the PerfPilot orchestrator's script-creation pipeline.*

---

### 🧩 **11. Architecture & Flow Docs (Future Expansion)**

Potential documents coming later:

* `architecture_overview.md`
* `data_flow_across_mcp_servers.md`
* `report_generation_pipeline.md`
* `how_network_capture_integrates_with_jmeter.md`

➡️ *These give new contributors a high-level picture of the entire ecosystem.*

---

## 🧭 Suggested Folder Structure (Growing)

```
docs/
│
├── README.md                         ← You are here
├── artifacts_guide.md                ← Artifacts folder & local-first architecture
├── har_adapter_guide.md              ← HAR-to-JMeter conversion guide
├── jmeter_hitl_user_guide.md         ← AI HITL editing guide
├── perfmemory_user_guide.md         ← PerfMemory + debugging workflows & example prompts
├── report_template_guidelines.md     ← Performance report template rules
├── large_file_handling.md            ← Large JTL file handling & limitations
├── perfanalysis_techniques_guide.md ← Analytical techniques & algorithms reference
│
├── agent-framework/
│   └── a2a_new_jmx_pipeline_guide.md ← A2A + Web UI new-JMX pipeline metadata reference
│
├── config_reference.md        ← (Planned)
├── mcp_tools_index.md         ← (Planned)
│
├── changelogs/
│   ├── CHANGELOG-2026-01.md   ← January 2026 changes
│   ├── CHANGELOG-2026-02.md   ← February 2026 changes
│   ├── CHANGELOG-2026-03.md   ← March 2026 changes
│   ├── CHANGELOG-2026-04.md   ← April 2026 changes
│   ├── CHANGELOG-2026-05.md   ← May 2026 changes
│   └── CHANGELOG-2026-06.md   ← June 2026 changes
│
├── architecture_overview.md   ← (Future)
└── examples/                  ← Example templates, configs, outputs
```

---

## 🚀 Future Improvements

As more MCP servers are added or enhanced, this docs folder will evolve to include:

* Detailed examples
* Troubleshooting guides
* Contribution guidelines
* Best practices for large-scale performance testing
* How to extend or override MCP server behavior
