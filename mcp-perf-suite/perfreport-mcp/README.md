# 🚦 PerfReport MCP Server

Welcome to the **PerfReport MCP Server**!
This Python-based MCP server is built using FastMCP 3.4.x to generate easy-to-share, stakeholder-ready performance reports from your BlazeMeter and Datadog analysis workflows.

---

## ⭐ Features

- 📝 Generate beautiful performance test reports (Markdown, PDF, Word)
- 📊 Create PNG charts for single-axis, dual-axis, and multi-line visualizations
- 📈 Multi-line infrastructure charts showing all hosts/services on one chart
- 📑 Template-driven formatting with chart placeholder support
- 🗂 Compare multiple runs in a single analysis with comparison bar charts
- 🤖 AI-assisted report revision with Human-In-The-Loop (HITL) workflow
- 🔄 Version-tracked revisions (v1, v2, v3...) for iterative refinement
- 🔗 Modular structure with seamless MCP suite integration
- 🖼️ Confluence-ready chart filenames following schema ID conventions

---

## ⚡ Prerequisites

- Python 3.12+
- Access to BlazeMeter and Datadog MCP artifacts
- Setup your `config.yaml` and `chart_colors.yaml` file

---

## 🚀 Getting Started

### 1. Clone the repository

```
git clone https://github.com/canyonlabz/perfpilot-hub.git
cd perfpilot-hub/mcp-perf-suite/perfreport-mcp
```

### 2. Create/activate virtual environment

A virtual environment can be manually activated, or automatically initialized by the MCP Client (e.g. Cursor) on startup.

#### On macOS / Linux
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### On Windows (PowerShell)
```
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### 3. Configure your environment

- Update `config.yaml` and `chart_colors.yaml`
- Ensure `templates/` contains required .md templates

#### 4. Running the MCP server ▶️

*Option 1: Run directly with Python*
`python perfreport.py`

*Option 2: Run using `uv` (Recommended) ⚡

You can use **uv** to simplify setup and execution. It manages dependencies and environments automatically.

- Install `uv` (macOS, Linux, Windows PowerShell)
```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- Run the MCP Server with `uv`

```
uv run perfreport.py
```

---

## 🛎 MCP Tools

These are exposed for Cursor, agent, or CLI use:

### Report Generation Tools

| Tool | Description |
| :-- | :-- |
| `create_performance_test_report` | Generate a report (Markdown, PDF, Word) from a single test run |
| `create_comparison_report` | Compare multiple runs in one report |
| `list_templates` | Show available report templates |
| `get_template_details` | Show details/preview for a specific template |

### AI-Assisted Revision Tools

| Tool | Description |
| :-- | :-- |
| `discover_revision_data` | Scan artifacts to find all data files for AI revision |
| `prepare_revision_context` | Save AI-generated content for a report section (with version tracking) |
| `revise_performance_test_report` | Assemble final revised report from AI-generated sections |

### Chart Generation Tools

| Tool | Description |
| :-- | :-- |
| `create_chart` | Create a PNG chart by chart_id (single-axis, dual-axis, or multi-line) |
| `create_comparison_chart` | Create comparison bar charts for multiple test runs |
| `list_chart_types` | List all available chart types from chart_schema.yaml |

### 📊 Available Chart Types

#### Performance Charts

| Chart ID | Type | Description |
| :-- | :-- | :-- |
| `RESP_TIME_P90_VUSERS_DUALAXIS` | Dual-axis | P90 response time vs virtual users |

#### Infrastructure Charts (Utilization %)

| Chart ID | Type | Description |
| :-- | :-- | :-- |
| `CPU_UTILIZATION_LINE` | Single-axis | CPU % for a specific host/service |
| `CPU_UTILIZATION_VUSERS_DUALAXIS` | Dual-axis | CPU % vs virtual users |
| `CPU_UTILIZATION_MULTILINE` | Multi-line | CPU % for ALL hosts/services |
| `MEMORY_UTILIZATION_LINE` | Single-axis | Memory % for a specific host/service |
| `MEMORY_UTILIZATION_VUSERS_DUALAXIS` | Dual-axis | Memory % vs virtual users |
| `MEMORY_UTILIZATION_MULTILINE` | Multi-line | Memory % for ALL hosts/services |

#### Infrastructure Charts (Raw Usage)

| Chart ID | Type | Description |
| :-- | :-- | :-- |
| `CPU_CORES_LINE` | Single-axis | CPU core usage (millicores) for a host/service |
| `MEMORY_USAGE_LINE` | Single-axis | Memory usage (MB) for a host/service |

#### Performance Charts (SLA & Errors)

| Chart ID | Type | Description |
| :-- | :-- | :-- |
| `ERROR_RATE_LINE` | Single-axis | Error occurrences over time |
| `THROUGHPUT_HITS_LINE` | Single-axis | Transaction throughput (req/sec) |
| `TOP_SLOWEST_APIS_BAR` | Horizontal bar | Top API SLA violators by P90 response time |

#### Infrastructure Charts (Stacked Area)

| Chart ID | Type | Description |
| :-- | :-- | :-- |
| `CPU_UTILIZATION_STACKED` | Stacked area | Per-service CPU utilization (%) by container/pod (k8s only, requires limits) |
| `MEM_UTILIZATION_STACKED` | Stacked area | Per-service Memory utilization (%) by container/pod (k8s only, requires limits) |
| `CPU_USAGE_STACKED` | Stacked area | Per-service raw CPU usage (millicores/cores) by container/pod (k8s only, always available) |
| `MEM_USAGE_STACKED` | Stacked area | Per-service raw Memory usage (MB/GB) by container/pod (k8s only, always available) |

#### Comparison Charts (For Multi-Run Reports)

| Chart ID | Type | Description |
| :-- | :-- | :-- |
| `CPU_PEAK_CORE_COMPARISON_BAR` | Vertical bar | Compare peak CPU usage across test runs |
| `CPU_AVG_CORE_COMPARISON_BAR` | Vertical bar | Compare average CPU usage across test runs |
| `MEMORY_PEAK_USAGE_COMPARISON_BAR` | Vertical bar | Compare peak memory usage across test runs |
| `MEMORY_AVG_USAGE_COMPARISON_BAR` | Vertical bar | Compare average memory usage across test runs |

### 📁 Chart Filename Conventions

Charts are saved to `artifacts/<run_id>/charts/` using standardized filenames:

| Chart Type | Filename Pattern | Example |
| :-- | :-- | :-- |
| Multi-line | `SCHEMA_ID.png` | `CPU_UTILIZATION_MULTILINE.png` |
| Performance | `SCHEMA_ID.png` | `RESP_TIME_P90_VUSERS_DUALAXIS.png` |
| Per-resource | `SCHEMA_ID-<resource>.png` | `CPU_UTILIZATION_LINE-api-gateway.png` |


---

## 🔄 Workflow Example

1. 🏃‍♂️ Generate a report after test analysis
2. 🌟 Visualize results with charts for stakeholders
3. 👥 Revise reports with feedback (business, QA, engineering)
4. 📈 Compare test runs for trends and regression
5. 📂 Download outputs from the artifacts directory

---

## 📎 Output Examples

**Markdown Report**

```
# Performance Report: RUN-20251010-01
- SLA Met: All endpoints ✅
- Peak throughput: 1200 req/sec
- Bottleneck: Database tier 🔎
```

**Returned JSON**

```json
{
  "run_id": "RUN-20251010-01",
  "path": "/artifacts/RUN-20251010-01/reports/performance_report.md"
}
```

**PNG Chart**

```
/artifacts/RUN-20251010-01/charts/response-time.png
```

---

## 🏗 Project Structure

```
perfreport-mcp/
├── perfreport.py                               # MCP entrypoint   
├── services/
│   ├── report_generator.py                     # Single-run report generation
│   ├── comparison_report_generator.py          # Multi-run comparison reports
│   ├── report_revision_generator.py            # AI-assisted report revision assembly
│   ├── revision_data_discovery.py              # Discover data files for AI revision
│   ├── revision_context_manager.py             # Save/manage AI revision content
│   ├── chart_generator.py                      # Single-run chart generation
│   ├── comparison_chart_generator.py           # Multi-run comparison charts
│   ├── template_manager.py                     # Template reading/writing
│   └── charts/                                 # Chart type implementations
│       ├── single_axis_charts.py               # Single-axis line charts
│       ├── dual_axis_charts.py                 # Dual-axis line charts
│       ├── multi_line_charts.py                # Multi-line overlay charts
│       └── comparison_bar_charts.py            # Vertical bar comparison charts
├── utils/
│   ├── config.py                               # Config loading utilities
│   ├── data_loader_utils.py                    # Centralized data loading helper
│   ├── revision_utils.py                       # Path helpers for revision workflow
│   ├── chart_utils.py                          # Chart generation utilities
│   ├── file_utils.py                           # File handling utilities
│   └── report_utils.py                         # Report generation utilities
├── config.yaml                                 # Centralized, environment-agnostic config
├── report_config.yaml                          # Report sections and revision settings
├── chart_colors.yaml                           # Color palettes for charts
├── chart_schema.yaml                           # Chart type definitions and specifications
├── templates/
│   ├── default_report_template.md              # Default single-run report template
│   ├── default_comparison_report_template.md   # Default comparison report template
│   └── ai_*.md                                 # AI-generated template variants
├── README.md
├── pyproject.toml                              # Modern Python project metadata & dependencies
└── requirements.txt                            # Dependencies
```


***

## 🔌 Integration

Works seamlessly with BlazeMeter, Datadog, PerfAnalysis, and Confluence MCP servers
for full-stack, end-to-end performance test reporting.

***

## 🙌 Contributing

💡 Suggestions, issues, and PRs are always welcome!

---

## 📜 License

This project is part of the PerfPilot-Hub suite. See the repository root for license information.

---

Built with ❤️ using FastMCP 3.4.x and Matplotlib.