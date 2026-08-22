# 🚀📶 JMeter MCP Server

Welcome to the JMeter MCP Server! 🎉
This is a Python-based MCP server built with **FastMCP 2.0** that partners with the **Playwright MCP** to turn human-readable workflows into JMeter performance test scripts—by using Playwright to run browser automation and capture trace files, then analyzing those traces to produce structured JSON, generate JMeter **JMX** scripts, and provide correlation analysis, results aggregation, and log analysis.

---

## ✨ Features

* **🎭 Playwright Integration**: Parse network traces captured by Playwright MCP agent for seamless browser-to-JMeter script conversion.
* **🌐 Capture network traffic**: Parse Playwright network traces and map them to test steps from spec files.
* **🚫 Configurable domain exclusions**: Filter out APM, analytics, and middleware traffic from capture and analysis.
* **⚙️ Configurable and extensible**: Manage all paths and parameters through `config.yaml` and `jmeter_config.yaml` files.
* **🔍 Analyze correlations**: Identify dynamic values (IDs, tokens, correlation IDs) that flow between requests for parameterization.
* **🏷️ Orphan ID detection**: Flag request-only IDs without prior response sources for manual parameterization.
* **📝 Generate JMeter scripts**: Convert captured network traffic into executable JMX test scripts with proper structure.
* **▶️ Run JMeter tests directly**: Execute JMeter test plans (`.jmx` files) locally.
* **⏹️ Stop active JMeter tests**: Gracefully terminate test executions in progress.
* **📊 Aggregate post-test results**: Parse JMeter JTL output to generate JMeter/BlazeMeter-style summary reports and KPIs.
* **🔬 Deep log analysis**: Analyze JMeter/BlazeMeter log files — group errors by type, API, and root cause with first-occurrence request/response details and JTL correlation.
* **📂 HAR file input adapter**: Convert HAR files from Chrome DevTools, proxy tools (Charles, Fiddler, mitmproxy), or Postman into network capture JSON — an alternative on-ramp to the existing pipeline.
* **📋 Swagger/OpenAPI input adapter**: Convert Swagger 2.x / OpenAPI 3.x API specification files (JSON or YAML) into synthetic network capture JSON — ideal when you have an API spec but no recorded traffic.
* **🤖 AI HITL script editing**: Analyze, add components to, and edit any JMeter JMX script through AI-assisted Human-in-the-Loop tools — with dry-run preview, automatic numbered backups, and a registry of 36+ component types.
* **📦 Component registry**: A central catalog of JMeter component types (controllers, samplers, config elements, extractors, assertions, timers, pre/post processors) with validation schemas and builder functions.
* **🔬 HAR-JMX comparison**: Cross-compare a fresh HAR capture against an existing JMX script to identify API changes (new/removed endpoints, URL/method changes, payload differences, correlation drift, schema changes) — diagnostic only, produces actionable reports without modifying the script.

🧩 Future tools under consideration:

* **OAuth 2.0 / PKCE correlation support** — Authentication flow correlation (Phase 2)
* **Reuse component across scripts** — Cherry-pick components from one JMX and add them to another

---

## 🏁 Prerequisites

* Python 3.12 or higher
* JMeter installed and added to your system `PATH`
* Configured `config.yaml` (copy from `config.example.yaml`)
* Configured `jmeter_config.yaml` for JMeter-specific settings (copy from `jmeter_config.example.yaml`)
* **Cursor IDE** with Playwright MCP enabled (for browser automation workflows)

All configuration is managed through YAML files — no `.env` file is required.

Planned support for additional MCP hosts:

* **Claude Desktop**
* **VS Code** (via MCP extension)

---

## 🚀 Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd mcp-perf-suite/jmeter-mcp
```

### 2. Create Configuration Files

Copy the example configuration and customize for your environment:

```bash
# Copy the example config
cp config.example.yaml config.yaml

# Edit config.yaml with your paths:
# - artifacts_path: auto-resolved; leave empty unless you need a custom location
# - jmeter_home: path to your JMeter installation (required)
# - jmeter_bin_path: path to JMeter bin directory (required)
```

### 3. Set Up Python Environment

#### Option A: Using `uv` (Recommended)

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Run directly with uv (handles dependencies automatically)
uv run jmeter.py
```

#### Option B: Using Virtual Environment

**macOS / Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

**Windows (PowerShell):**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
```

---

## 📝 Configuration Files

### `config.yaml` (Main Configuration)

Copy from `config.example.yaml` and customize. For detailed guidance on all available options, see the [JMeter MCP Configuration Guide](../docs/jmeter_mcp_configuration_guide.md).

```yaml
general:
  enable_debug: False
  enable_logging: True

artifacts:
  # Dynamically resolved to {repo_root}/artifacts when left empty.
  artifacts_path: ""

jmeter:
  jmeter_home: "C:\\path\\to\\apache-jmeter"
  jmeter_bin_path: "C:\\path\\to\\apache-jmeter\\bin"
  jmeter_start_exe: "jmeter.bat"      # Use "jmeter" for Linux/Mac
  jmeter_stop_exe: "stoptest.cmd"     # Use "stoptest.sh" for Linux/Mac

jmeter_log:
  max_description_length: 200        # Max characters for error description in log analysis output
  max_request_length: 500            # Max characters for captured request details
  max_response_length: 500           # Max characters for captured response details
  max_stack_trace_lines: 50          # Max lines to capture from a stack trace
  error_levels:                      # Log levels to treat as issues (WARN excluded by design)
    - "ERROR"
    - "FATAL"

test_specs:
  web_flows_path: "test-specs\\web-flows"
  api_flows_path: "test-specs\\api-flows"
  examples_path: "test-specs\\examples"

browser:
  browser_type: "chrome"
  headless_mode: True
  window_size: "1920,1080"
  implicit_wait: 10
  page_load_timeout: 60
  think_time: 5000

network_capture:
  capture_api_requests: True
  capture_static_assets: False
  capture_fonts: False
  capture_video_streams: False
  capture_third_party: True
  capture_cookies: True
  capture_domain: ""
  # Domains to exclude from capture and correlation analysis
  exclude_domains:
    - "datadoghq.com"
    - "google-analytics.com"
    - "googletagmanager.com"
    - "facebook.com"
    - "doubleclick.net"
    - "newrelic.com"
    - "segment.io"
    - "hotjar.com"
    - "mixpanel.com"
    - "amplitude.com"
    - "sentry.io"
    - "bugsnag.com"
    - "fullstory.com"
    - "logrocket.com"
```

> **Note:** The `logging` section is reserved for future MCP server debugging and is not currently implemented. It is separate from the `jmeter_log` section, which controls how the `analyze_jmeter_log` tool processes JMeter/BlazeMeter log files.

### `jmeter_config.yaml` (JMeter Script Settings)

Controls how JMX scripts are generated. For detailed guidance, see the [JMeter MCP Configuration Guide](../docs/jmeter_mcp_configuration_guide.md).

```yaml
thread_group:
  num_threads: 10
  ramp_time: 100
  loops: 10

cookie_manager:
  enabled: true

user_defined_variables:
  enabled: true
  variables:
    "thinkTime": 5000
    "pacing": 10000

csv_dataset_config:
  enabled: false
  csv_file_path: "testdata_csv"
  filename: "test_data.csv"
  # ... additional CSV settings

controller_config:
  enabled: true
  controller_type: "simple"

http_sampler:
  auto_redirects: true
  post_body_raw: true

test_action_config:
  enabled: true
  action: "pause"
  duration: 5000
  test_action_name: "Think Time"

results_collector_config:
  view_results_tree: false
  aggregate_report: false
  response_time_graph: true
  summary_report: true
```

---

## ▶️ Running the MCP Server

### Option 1: Run with `uv` (Recommended)

```bash
uv run jmeter.py
```

### Option 2: Run with Python

```bash
python jmeter.py
```

Runs with default `stdio` transport — ideal for local runs or Cursor integration.

---

## ⚙️ MCP Server Configuration (`mcp.json`)

Example setup for Cursor or compatible MCP hosts:

```json
{
  "mcpServers": {
    "jmeter": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/your/jmeter-mcp",
        "run",
        "jmeter.py"
      ]
    }
  }
}
```

---

## 🛠️ Tools

The JMeter MCP server exposes the following tools for agents, Cursor, or automation pipelines:

### Browser Automation & Network Capture

| Tool                        | Description                                                                |
| :-------------------------- | :------------------------------------------------------------------------- |
| `archive_playwright_traces` | Archives existing Playwright trace files before a new browser automation run |
| `get_test_specs`            | Discovers available Markdown browser automation specs in `test-specs/`     |
| `get_browser_steps`         | Loads a given Markdown file and parses browser automation test steps       |
| `capture_network_traffic`   | Parses Playwright network traces and maps them to test steps from a spec file |
| `convert_har_to_capture`    | Convert a HAR (HTTP Archive) file to network capture JSON for JMeter script generation |
| `convert_swagger_to_capture`| Convert a Swagger 2.x / OpenAPI 3.x spec file to network capture JSON for JMeter script generation |
| `analyze_network_traffic`   | Analyzes network traffic to identify correlations, dynamic values, and orphan IDs |

### JMeter Script Generation & Execution

| Tool                        | Description                                                                |
| :-------------------------- | :------------------------------------------------------------------------- |
| `generate_jmeter_script`    | Converts captured network traffic JSON into a JMeter JMX script            |
| `list_jmeter_scripts`       | Lists the current JMX scripts available for a given test run               |
| `start_jmeter_test`         | Executes a JMeter test based on configuration or provided JMX file         |
| `get_jmeter_run_status`     | Returns real-time metrics for a running JMeter test by reading JTL file    |
| `stop_jmeter_test`          | Gracefully stops an ongoing JMeter test run                                |
| `generate_aggregate_report` | Parses JMeter JTL results to produce BlazeMeter-style aggregate CSV report |

### AI HITL Script Editing

| Tool                            | Description                                                                |
| :------------------------------ | :------------------------------------------------------------------------- |
| `analyze_jmeter_script`         | Analyze a JMX script's structure, hierarchy, node IDs, and variables — returns a tree view with stable identifiers for targeting specific elements |
| `add_jmeter_component`          | Add new JMeter components (controllers, assertions, timers, config elements, etc.) to an existing JMX script with dry-run preview and automatic backup |
| `edit_jmeter_component`         | Edit existing JMX components — rename, set properties, replace values in request bodies, or toggle enabled/disabled — with dry-run preview |
| `list_jmeter_component_types`   | Browse all 36+ supported component types with metadata, required/optional fields, and validation rules |

> See the [JMeter HITL User Guide](../docs/jmeter_hitl_user_guide.md) for the full workflow, requirements, and best practices.

### HAR-JMX Comparison

| Tool                            | Description                                                                |
| :------------------------------ | :------------------------------------------------------------------------- |
| `compare_har_to_jmx`           | Cross-compare a HAR file against a JMX script to identify API changes — produces JSON and Markdown reports with categorized differences, confidence-scored matching, and actionable remediation guidance |

> See the [HAR-JMX Comparison Guide](../docs/har_jmx_comparison_guide.md) for the full tool reference, matching algorithm details, and usage examples.

### Log Analysis

| Tool                        | Description                                                                |
| :-------------------------- | :------------------------------------------------------------------------- |
| `analyze_jmeter_log`        | Deep analysis of JMeter/BlazeMeter log files — groups errors by type, API, and root cause with first-occurrence request/response details and optional JTL correlation |

---

## 🔁 Typical Workflow

### 1. **Prepare Test Specs**

Create a Markdown spec file in `test-specs/web-flows/` defining the browser steps:

```markdown
# My Application Flow

Step 1: Navigate to https://example.com/
Step 2: Click on "Login" button
Step 3: Enter username and password
Step 4: Click "Submit"
Step 5: Verify dashboard loads

END
```

### 2. **Capture Network Traffic**

Use Playwright MCP agent to execute the browser automation:

1. **Archive previous traces**: `archive_playwright_traces` clears old trace data
2. **Run browser automation**: Playwright agent executes the spec
3. **Capture traffic**: `capture_network_traffic` parses the Playwright traces and maps requests to steps

### 3. **Analyze Correlations**

* `analyze_network_traffic` identifies dynamic values (IDs, tokens, correlation IDs) that flow between requests
* Detects **orphan IDs** — values in request URLs without identifiable source responses
* Outputs `correlation_spec.json` with:
  - High-confidence correlations (source -> usage patterns)
  - Low-confidence orphan IDs (recommend CSV or User Defined Variable parameterization)
  - Parameterization hints (extractor type, strategy)

### 4. **Generate Correlation Naming (Cursor Rules)**

* Apply the `.cursor/rules/jmeter-correlations.mdc` rules to `correlation_spec.json`
* Generates `correlation_naming.json` with meaningful JMeter variable names
* Provides JMeter extractor expressions (JSON Extractor or Regex Extractor)

### 5. **Generate JMeter Script**

* `generate_jmeter_script` converts the captured network traffic JSON into a JMX test plan
* Applies settings from `jmeter_config.yaml` (thread groups, think times, listeners)

### 6. **Edit Script (AI HITL — Optional)**

* `analyze_jmeter_script` inspects the script structure and returns stable `node_id` identifiers for every element
* `add_jmeter_component` adds new components (assertions, timers, config elements, etc.) — always preview with `dry_run=true` first
* `edit_jmeter_component` modifies existing elements (rename, set properties, toggle enabled/disabled)
* Numbered backups are created automatically before each change
* Works on any valid JMX — not just scripts generated by this pipeline

### 7. **Compare HAR Against JMX (Optional)**

When application APIs change and you have a fresh HAR capture:

* `compare_har_to_jmx` cross-compares the HAR against the existing JMX script
* Uses a multi-pass matching algorithm (exact, parameterized, fuzzy) to align HAR requests to JMX samplers
* Analyzes 10 difference categories: URL changes, method changes, payload changes, response schema changes, correlation drift, status code changes, query param changes, and header changes
* Produces a JSON report (for AI consumption) and a Markdown report (for human review) in `artifacts/<test_run_id>/jmeter/analysis/`
* Use the findings to drive targeted `edit_jmeter_component` / `add_jmeter_component` updates

### 8. **Execute Test**

* `start_jmeter_test` runs the generated JMX file
* `get_jmeter_run_status` polls real-time metrics during execution
* `stop_jmeter_test` terminates the test gracefully if needed

### 9. **Generate Reports**

* `generate_aggregate_report` produces a JMeter/BlazeMeter-style aggregate report CSV from JTL results
* Output results available for downstream analysis

### 10. **Analyze Logs**

* `analyze_jmeter_log` performs deep analysis of JMeter or BlazeMeter log files
* Identifies and groups errors by type, API endpoint, and root cause
* Captures first-occurrence request/response details for each unique error
* Correlates log errors with JTL result data when available
* Outputs CSV, JSON, and Markdown reports to `artifacts/<test_run_id>/analysis/`

---

## 📁 Project Structure

```
jmeter-mcp/
├── jmeter.py                     # MCP server entrypoint (FastMCP)
├── services/
│   ├── correlation_analyzer.py   # Backward-compatible wrapper for correlations package
│   ├── correlations/             # Modular correlation analysis package (v0.2.0)
│   │   ├── __init__.py           # Package exports (analyze_traffic)
│   │   ├── analyzer.py           # Main orchestrator (_find_correlations, analyze_traffic)
│   │   ├── classifiers.py        # Value type classification and parameterization strategy
│   │   ├── constants.py          # Regex patterns, header exclusions, configuration
│   │   ├── extractors.py         # Source extraction from responses (JSON, headers, redirects)
│   │   ├── matchers.py           # Usage detection in requests, orphan ID detection
│   │   └── utils.py              # URL normalization, JSON traversal, file loading
│   ├── jmx_editor.py             # AI HITL service: JMX discovery, parsing, backup, node indexing, add/edit/analyze
│   ├── har_jmx_diffengine.py    # HAR-JMX diff engine: extraction, multi-pass matching, difference analysis
│   ├── jmeter_log_analyzer.py    # Deep JMeter/BlazeMeter log analysis service
│   ├── jmeter_runner.py          # Handles JMeter execution, control, and reporting
│   ├── network_capture.py        # URL filtering and capture configuration logic
│   ├── har_adapter.py            # Converts HAR files into step-aware network capture
│   ├── swagger_adapter.py        # Converts Swagger/OpenAPI specs into synthetic network capture
│   ├── playwright_adapter.py     # Parses Playwright traces into step-aware network capture
│   ├── script_generator.py       # Generates JMX scripts from network capture JSON
│   ├── spec_parser.py            # Parses Markdown specs into structured steps
│   ├── helpers/                   # Service helper modules
│   │   ├── analysis_export_helpers.py    # JMX analysis export (JSON/Markdown file builders)
│   │   └── diffengine_report_helpers.py  # HAR-JMX comparison report builders and file persistence
│   └── jmx/                      # JMX builder DSL
│       ├── __init__.py           # Package exports for all builder functions
│       ├── assertions.py         # Response Assertion, Duration Assertion
│       ├── component_registry.py # Central registry of 36+ JMeter component types with validation
│       ├── config_elements.py    # User Defined Variables, CSV Data Sets, HTTP Defaults, Auth Manager, Keystore Config
│       ├── controllers.py        # JMeter Controllers (Simple, Transaction, Loop, If, While, Switch, ForEach, etc.)
│       ├── listeners.py          # JMeter Listeners (View Results Tree, Aggregate Report)
│       ├── oauth2.py             # OAuth 2.0 components (code_challenge, code_verifier, etc.)
│       ├── post_processor.py     # Post-Processor elements (JSON Extractors, RegEx Extractors, JSR223, etc.)
│       ├── pre_processor.py      # Pre-Processor elements (JSR223 PreProcessor, etc.)
│       ├── plan.py               # JMeter Test Plan and Thread Groups
│       ├── samplers.py           # JMeter Samplers (HTTP Request GET/POST/PUT/DELETE, JSR223)
│       └── timers.py             # Constant Timer, Constant Throughput Timer, Random Timer
├── utils/
│   ├── browser_utils.py          # Domain extraction, logging setup, async utilities
│   ├── config.py                 # Loads configuration YAML files
│   ├── file_utils.py             # File handling, discovery, and output utilities
│   └── log_utils.py              # Log parsing utilities (regex, extraction, normalization)
├── config.example.yaml           # Example configuration template
├── jmeter_config.example.yaml    # Example JMeter script generation settings
├── pyproject.toml                # Project metadata and dependencies
├── uv.lock                       # uv dependency lock file
└── README.md                     # This file
```

---

## 🎯 Artifacts Output Structure

When you run tests and analyses, artifacts are organized under `artifacts/<test_run_id>/`:

```
artifacts/
└── <test_run_id>/
    ├── jmeter/
    │   ├── ai-generated_script_*.jmx       # Generated JMeter script
    │   ├── imported_*.jmx                   # Imported external JMX (if applicable)
    │   ├── test-results.csv                 # JTL from headless execution
    │   ├── aggregate_performance_report.csv # Aggregate stats
    │   ├── results_tree.csv                 # View Results Tree listener output
    │   ├── aggregate_report.csv             # Aggregate Report listener output
    │   ├── <test_run_id>.log               # JMeter execution log
    │   ├── correlation_spec.json            # Correlation analysis output
    │   ├── correlation_naming.json          # Variable naming (via Cursor Rules)
    │   ├── analysis/                        # Versioned analysis and comparison reports
    │   │   ├── jmx_structure_*.json          # JMX structure export (from analyze_jmeter_script)
    │   │   ├── jmx_structure_*.md            # JMX structure summary (Markdown)
    │   │   ├── har_jmx_comparison_*.json     # HAR-JMX comparison report (from compare_har_to_jmx)
    │   │   └── har_jmx_comparison_*.md       # HAR-JMX comparison summary (Markdown)
    │   ├── backups/                         # Numbered JMX backups from HITL edits
    │   │   └── *-000001.jmx
    │   ├── network-capture/
    │   │   ├── network_capture_*.json       # Captured network traffic
    │   │   └── capture_manifest.json        # Source provenance
    │   └── testdata_csv/
    │       └── environment.csv              # Environment-specific variables
    ├── analysis/
    │   ├── <source>_log_analysis.csv        # Log analysis issues (tabular)
    │   ├── <source>_log_analysis.json       # Log analysis metadata + full issues
    │   └── <source>_log_analysis.md         # Log analysis report (human-readable)
    └── ...                                  # Other MCP server outputs (see docs/artifacts_guide.md)
```

> For the complete artifacts directory structure across all MCP servers, see the [Artifacts Guide](../docs/artifacts_guide.md).

---

## 🔍 Correlation Analysis Details

The correlation analyzer (v0.2.0) performs the following:

### Phase 1: Source Extraction
- **Response JSON bodies**: Walks JSON up to 5 levels deep, extracting ID-like values
- **Response headers**: Extracts correlation headers (x-request-id, x-correlation-id, etc.)
- **Redirect URLs**: Parses `Location` headers for OAuth params (client_id, state, nonce, etc.)

### Phase 2: Usage Detection
- **Request URLs**: Path segments and query parameters
- **Request headers**: Custom headers (excluding HTTP plumbing like content-length)
- **Request bodies**: JSON fields and form data

### Phase 3: Orphan ID Detection
- Identifies ID-like values in requests without prior response sources
- Suggests parameterization strategy:
  - `csv_dataset` for high-frequency IDs (3+ occurrences)
  - `user_defined_variable` for low-frequency IDs (1-2 occurrences)

### Output Schema
```json
{
  "correlations": [
    {
      "correlation_id": "corr_1",
      "type": "business_id",
      "value_type": "business_id_numeric",
      "confidence": "high",
      "source": { ... },
      "usages": [ ... ],
      "parameterization_hint": {
        "strategy": "extract_and_reuse",
        "extractor_type": "json"
      }
    }
  ],
  "summary": {
    "total_correlations": 27,
    "business_ids": 12,
    "correlation_ids": 0,
    "oauth_params": 4,
    "orphan_ids": 11
  }
}
```

---

## 🚧 Future Enhancements

### Input Adapters
* ~~**HAR file adapter**~~ — ✅ Implemented (`convert_har_to_capture`)
* ~~**Swagger/OpenAPI adapter**~~ — ✅ Implemented (`convert_swagger_to_capture`)

### Script Generation & Editing
* **OAuth 2.0 / PKCE correlation support** for authentication flows
* **Automatic JMX correlation insertion** based on `correlation_naming.json`
* ~~**HITL tools**~~ — ✅ Implemented (`analyze_jmeter_script`, `add_jmeter_component`, `edit_jmeter_component`, `list_jmeter_component_types`)
* ~~**HAR-JMX comparison**~~ — ✅ Implemented (`compare_har_to_jmx`)
* **Reuse component across scripts** — Cherry-pick components from one JMX and add them to another
* **Auto-import from external path** — Accept a full filesystem path for external JMX scripts and copy them into the artifacts folder automatically

### Integration & Infrastructure
* **Integration with BlazeMeter and Datadog MCPs** for unified execution and monitoring
* **LLM-based test analysis** using PerfAnalysis MCP
* **Report generation via PerfReport MCP**

### MCP Host Support
* **Claude Desktop** support
* **VS Code** support (via MCP extension)

---

## 🤝 Contributing

Feel free to open issues or submit pull requests to enhance functionality, add new tools, or improve documentation!

---

Created with ❤️ using FastMCP, JMeter, and the MCP Perf Suite architecture.
