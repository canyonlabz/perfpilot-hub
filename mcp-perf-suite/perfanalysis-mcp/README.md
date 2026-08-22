# Performance Analysis MCP Server 🎉

The Performance Analysis MCP Server is a Python-based MCP server built with **FastMCP** that provides comprehensive analysis of load testing results correlated with infrastructure metrics. It identifies performance bottlenecks, validates SLA compliance, and generates actionable insights for optimization.

***

## ✨ Features

- **Load Test Results Analysis**: Process JMeter JTL files for response time aggregates, error rates, throughput, and per-API SLA compliance validation
- **Centralized SLA Configuration**: Define SLA thresholds in a single `slas.yaml` file with per-profile defaults and per-API overrides using pattern matching
- **Infrastructure Metrics Analysis**: Analyze CPU and memory metrics from both traditional hosts and Kubernetes services during test execution
- **Cross-Correlation Analysis**: Identify relationships between performance degradation and infrastructure resource constraints with temporal analysis
- **Bottleneck Detection**: Two-phase analysis engine that detects latency degradation, error rate increases, throughput plateaus, infrastructure saturation, and per-endpoint bottlenecks with sustained-degradation validation
- **Log Analysis**: Analyze JMeter/BlazeMeter logs and Datadog APM logs for errors grouped by type and API, correlated with performance data
- **SLA Pattern Validation**: Automatic detection of misconfigured SLA patterns with actionable feedback via MCP context messages
- **Multiple Output Formats**: Export results in JSON, CSV, and Markdown formats for downstream reporting and analysis
- **Consistent Architecture**: Built using the same modular patterns as BlazeMeter, Datadog, and PerfReport MCP servers for seamless integration

***

## 🏁 Prerequisites

- Python 3.12.4 or higher installed
- Access to BlazeMeter and Datadog artifact folders containing test results and metrics
- Completed BlazeMeter and Datadog data collection from previous test runs
- `slas.yaml` configuration file (copy from `slas.example.yaml` and customize)

***

## 🚀 Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd perfanalysis-mcp
```

### 2. Create & Activate a Python Virtual Environment

This ensures the MCP server dependencies do not affect your global Python environment.

#### On macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### On Windows (PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure SLA Thresholds

Copy the example SLA configuration and customize for your environment:

```bash
cp slas.example.yaml slas.yaml
```

See the [SLA Configuration Guide](../docs/sla-configuration-guide.md) for detailed setup instructions.

### 4. Configure Server Settings

Ensure your `config.yaml` is configured with the correct artifacts path pointing to your BlazeMeter and Datadog data. See `config.example.yaml` for reference.

***

## ▶️ Running the MCP Server

### Option 1: Run Directly with Python

```bash
python perfanalysis.py
```

This runs the MCP server with the default `stdio` transport -- ideal for running locally or integrating with Cursor AI.

### Option 2: Run Using `uv` (Recommended) ⚡️

You can use **uv** to simplify setup and execution. It manages dependencies and environments automatically.

#### Install `uv` (macOS, Linux, Windows PowerShell)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Run the MCP Server with `uv`

```bash
uv run perfanalysis.py
```

***

## ⚙️ MCP Server Configuration (`mcp.json`)

You can configure how Cursor or other MCP hosts start the server by adding to your `mcp.json` file:

```json
{
  "mcpServers": {
    "perfanalysis": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/your/perfanalysis-mcp",
        "run",
        "perfanalysis.py"
      ]
    }
  }
}
```

Replace `/path/to/your/perfanalysis-mcp` with your local path.

***

## 🛠️ MCP Tools

Your MCP server exposes these tools for Cursor, agents, or other MCP clients:

### Active Tools

| Tool | Description |
| :-- | :-- |
| `analyze_test_results` | Analyze load test results (JTL CSV format) for performance statistics, per-API SLA compliance, and statistical insights. Accepts optional `sla_id` for per-API SLA resolution. |
| `analyze_environment_metrics` | Analyze infrastructure metrics (CPU/Memory) from Datadog hosts and Kubernetes services |
| `correlate_test_results` | Cross-correlate load test and infrastructure data with temporal analysis. Accepts optional `sla_id` for SLA threshold resolution. |
| `identify_bottlenecks` | Two-phase bottleneck analysis: detects latency degradation, error rate increases, throughput plateaus, infrastructure saturation, and per-endpoint bottlenecks. Accepts optional `sla_id` and `baseline_run_id`. |
| `analyze_logs` | Analyze JMeter/BlazeMeter logs and Datadog APM logs for errors grouped by type and API |
| `get_analysis_status` | Get current analysis completion status for a test run |

### Disabled Tools (Future)

| Tool | Description |
| :-- | :-- |
| `detect_anomalies` | Detect statistical anomalies in performance and infrastructure metrics with configurable sensitivity |
| `compare_test_runs` | Compare multiple test runs for trend analysis (maximum 5 runs) |
| `summary_analysis` | Generate executive summary with insights |

***

## 🔁 Typical Workflow

A standard Performance Analysis workflow for comprehensive performance insights:

1. **Analyze Test Results**
    - `analyze_test_results(test_run_id, sla_id="my_profile")`: Process load test data for response time statistics, error analysis, and per-API SLA compliance
2. **Analyze Infrastructure Metrics**
    - `analyze_environment_metrics(test_run_id, environment)`: Process Datadog CPU/Memory metrics from hosts and Kubernetes services
3. **Cross-Correlate Data**
    - `correlate_test_results(test_run_id, sla_id="my_profile")`: Identify relationships between performance degradation and infrastructure constraints
4. **Identify Bottlenecks**
    - `identify_bottlenecks(test_run_id, sla_id="my_profile")`: Detect sustained performance degradation thresholds and limiting factors
5. **Analyze Logs**
    - `analyze_logs(test_run_id)`: Analyze JMeter and Datadog logs for errors correlated with performance data

> **Note**: AI-powered report revision and executive summaries are handled by the **PerfReport MCP** server using the Human-In-The-Loop (HITL) revision workflow.

***

## 🎯 SLA Configuration

SLA thresholds are defined in `slas.yaml` (not in `config.yaml`). This provides:

- **File-level defaults**: Applied when no `sla_id` is provided
- **Named SLA profiles**: Per-profile defaults with per-API overrides
- **Three-level pattern matching**: Full JMeter label > TC#\_TS# > TC# (most-specific first)
- **Configurable percentile**: P90 (default), P95, or P99
- **Error rate thresholds**: Configurable at file, profile, and API levels

```yaml
# slas.yaml
default_sla:
  response_time_sla_ms: 5000
  sla_unit: "P90"
  error_rate_threshold: 1.0

slas:
  - id: "order_management"
    description: "Order Management Service APIs"
    default_sla:
      response_time_sla_ms: 5000
    api_overrides:
      - pattern: "*/orders/export*"
        response_time_sla_ms: 10000
        reason: "Bulk export endpoint"
```

See the full [SLA Configuration Guide](../docs/sla-configuration-guide.md) for detailed usage, pattern matching precedence, and examples.

***

## 📊 Output Examples

### Performance Analysis JSON

```json
{
  "overall_stats": {
    "total_samples": 256000,
    "success_rate": 99.53,
    "avg_response_time": 340,
    "p90_response_time": 560,
    "error_count": 1212
  },
  "sla_analysis": {
    "compliance_rate": 0.98,
    "total_apis": 15,
    "compliant_apis": 14,
    "non_compliant_apis": 1,
    "sla_unit": "P90"
  }
}
```

### Correlation Analysis CSV

```csv
metric_1,metric_2,correlation_coefficient,p_value,significance
avg_response_time,cpu_utilization,0.72,0.001,high
error_rate,memory_usage,0.45,0.02,medium
```

### Bottleneck Analysis Summary

```markdown
## Executive Summary

**Performance degradation detected at 150 concurrent users**
(2 bottleneck(s), 1 capacity risk(s))

| Metric | Value |
|--------|-------|
| Threshold Concurrency | 150 |
| Baseline P90 | 245ms |
| Peak P90 | 1,280ms |
| Error Rate at Peak | 3.2% |
```

***

## 📁 Project Structure

```
perfanalysis-mcp/
├── perfanalysis.py                # MCP server entrypoint (FastMCP)
├── services/
│   ├── performance_analyzer.py    # Core analysis logic and workflows
│   ├── bottleneck_analyzer.py     # Two-phase bottleneck detection engine
│   ├── apm_analyzer.py            # Infrastructure metrics analysis
│   ├── log_analyzer.py            # JMeter/Datadog log analysis
│   └── ai_analyst.py              # AI-powered insights (used by PerfReport HITL)
├── utils/
│   ├── config.py                  # Config loader utility
│   ├── file_processor.py          # CSV/file processing and output formatting
│   ├── statistical_analyzer.py    # Statistical analysis and SLA compliance
│   └── sla_config.py              # SLA config loader, resolver, and validator
├── slas.yaml                      # SLA configuration (per-profile, per-API)
├── slas.example.yaml              # Annotated SLA configuration template
├── config.yaml                    # Server configuration
├── config.example.yaml            # Annotated config template
├── pyproject.toml                 # Python project metadata & dependencies
├── requirements.txt               # Dependencies
└── README.md                      # This file
```

***

## 🔧 Configuration Files

### `slas.yaml` (SLA Thresholds)

The single source of truth for all SLA definitions. See [SLA Configuration Guide](../docs/sla-configuration-guide.md).

### `config.yaml` (Server Settings)

```yaml
# Performance Analysis Settings
perf_analysis:
  # SLA thresholds are defined in slas.yaml (not here).
  load_tool: "blazemeter"           # Options: blazemeter, jmeter, gatling
  apm_tool: "datadog"               # Options: datadog, newrelic, appdynamics
  statistical_confidence: 0.95
  anomaly_sensitivity:
    low: 3.0      # Standard deviations
    medium: 2.5
    high: 2.0

  # Infrastructure utilization thresholds
  resource_thresholds:
    cpu:
      high: 80
      low: 20
    memory:
      high: 85
      low: 15

  # Bottleneck analysis settings
  bottleneck_analysis:
    bucket_seconds: 60
    warmup_buckets: 2
    sustained_buckets: 2
    persistence_ratio: 0.6
    rolling_window_buckets: 3
    latency_degrade_pct: 25.0
    error_rate_degrade_abs: 5.0
    throughput_plateau_pct: 5.0
```

***

## 🧪 Analysis Capabilities

### Statistical Methods

- **Correlation Analysis**: Pearson and Spearman correlation for linear and non-linear relationships
- **Anomaly Detection**: Z-score analysis with configurable standard deviation thresholds
- **Temporal Analysis**: Time-bucketed performance-infrastructure correlation with SLA violation tracking
- **SLA Validation**: Per-API compliance checking against configurable percentile thresholds (P90/P95/P99) from `slas.yaml`
- **Bottleneck Detection**: Sustained-degradation validation with persistence ratio, outlier filtering, and multi-factor severity classification

### Bottleneck Analyzer (v0.2)

The `identify_bottlenecks` tool detects six categories of performance degradation:

| Category | What It Detects |
|----------|----------------|
| Latency Degradation | P90 response time increases beyond threshold from baseline |
| Error Rate Increase | Error rate rises above absolute threshold |
| Throughput Plateau | Throughput stops scaling with increasing concurrency |
| Infrastructure Saturation | CPU or Memory utilization exceeds configured thresholds |
| Resource-Performance Coupling | Latency degradation coincides with infrastructure stress |
| Per-Endpoint Bottlenecks | Specific endpoints degrade earlier than others under load |

Key features: two-phase analysis (JTL detection + infrastructure cross-reference), sustained-degradation validation, transient spike filtering, capacity risk detection, raw metrics fallback for missing K8s limits, and per-endpoint SLA resolution.

***

## 🚧 Future Enhancements

- **Temporal Correlation**: Per-identifier correlation (service/host) to preserve hot spot visibility
- **Insight Enrichment**: Highlight top APIs per "interesting" window, summarize constraints, and surface actionable findings
- **Custom Metrics**: Support for additional performance and infrastructure metrics
- **Predictive Analysis**: Machine learning models for performance forecasting
- **Dashboard Integration**: Export data optimized for reporting dashboards
- **Multi-Environment Comparison**: Cross-environment performance analysis
- **Throughput SLAs**: Requests/sec thresholds in `slas.yaml`

***

## 🔗 Integration with Performance Testing Suite

This MCP server is designed to work seamlessly with the other MCP servers in the suite for end-to-end performance testing workflows:

1. **BlazeMeter MCP** -- Execute load tests and collect performance artifacts
2. **Datadog MCP** -- Gather infrastructure metrics during test execution
3. **PerfAnalysis MCP** -- Correlate data, validate SLA compliance, and identify bottlenecks
4. **PerfReport MCP** -- Create formatted reports, charts, and AI-revised content via HITL workflow
5. **Confluence MCP** -- Publish reports to Confluence with embedded charts

***

## 🤝 Contributing

Feel free to open issues or submit pull requests to enhance the analysis capabilities!

***

Created with ❤️ using FastMCP, Pandas, and SciPy
