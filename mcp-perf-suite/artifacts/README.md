# 📦 Artifacts Directory

Welcome to the `artifacts/` folder! This is the central hub for storing and organizing output files from the MCP servers used in performance testing workflows.

## 🧠 Purpose

This directory holds test run artifacts from:
- 🧪 **BlazeMeter MCP** — Load test results, JTL files, JMeter logs, and session artifacts
- 📊 **Datadog MCP** — Host metrics, Kubernetes metrics, logs, and APM traces (CSV format)
- 🧪 **JMeter MCP** — Generated JMX scripts, network captures, correlation specs, log analysis, and HITL backups
- 🔍 **PerfAnalysis MCP** — Performance analysis, infrastructure analysis, correlation analysis, bottleneck detection, and log analysis
- 📄 **PerfReport MCP** — Formatted reports (Markdown, PDF, Word), charts (PNG), and AI-revised report versions
- 🧷 **Confluence MCP** — Report metadata after publishing to Confluence
- 💬 **MS Teams MCP** — Notification logs for pre-test, post-test, and results notifications
- 📂 **SharePoint MCP** — Upload logs after archiving to SharePoint

Each test run is stored in its own subfolder for modularity and traceability.

## 📁 Structure

```plaintext
artifacts/
├── <test_run_id>/
│   ├── blazemeter/
│   │   ├── test-results.csv                    # Combined JTL from all sessions
│   │   ├── aggregate_performance_report.csv    # BlazeMeter aggregate report
│   │   ├── jmeter.log                          # JMeter execution log (single-session)
│   │   ├── jmeter-1.log ... jmeter-N.log       # JMeter logs (multi-session)
│   │   ├── test_config.json                    # Test configuration snapshot
│   │   └── public_report.json                  # Public report URL metadata
│   ├── datadog/
│   │   ├── host_metrics_<hostname>.csv          # CPU/memory metrics per host
│   │   ├── k8s_metrics_<service_name>.csv       # Kubernetes container metrics
│   │   ├── logs_<query_type>.csv                # Datadog log search results
│   │   └── apm_traces_<query_type>.csv          # APM trace search results
│   ├── jmeter/
│   │   ├── ai-generated_script_*.jmx           # Generated JMeter scripts
│   │   ├── imported_*.jmx                      # Imported external JMX scripts
│   │   ├── test-results.csv                    # JTL from local execution
│   │   ├── correlation_spec.json               # Correlation analysis output
│   │   ├── correlation_naming.json             # Variable naming mappings
│   │   ├── network-capture/                    # Network traffic JSON + manifest
│   │   ├── analysis/                           # JMX structure + HAR-JMX comparison reports
│   │   ├── backups/                            # Numbered JMX backups from HITL edits
│   │   └── testdata_csv/                       # Test data files
│   ├── analysis/
│   │   ├── performance_analysis.{json,csv,md}   # Load test analysis
│   │   ├── infrastructure_analysis.{json,csv,md}# Infrastructure metrics analysis
│   │   ├── correlation_analysis.{json,csv,md}   # Cross-correlation results
│   │   ├── bottleneck_analysis.{json,csv,md}    # Bottleneck detection results
│   │   └── *_log_analysis.{json,csv,md}         # Log analysis results
│   ├── reports/
│   │   ├── performance_report_<test_run_id>.md   # Performance report (Markdown)
│   │   ├── performance_report_<test_run_id>.xhtml# Confluence-formatted report
│   │   ├── report_metadata_<test_run_id>.json    # Report metadata
│   │   └── revisions/                            # AI-revised report versions (v1, v2, ...)
│   ├── charts/
│   │   ├── CPU_UTILIZATION_MULTILINE.png         # Multi-line CPU chart
│   │   ├── RESP_TIME_P90_VUSERS_DUALAXIS.png     # Response time vs VUsers
│   │   └── *.png                                 # Other chart types
│   ├── confluence/
│   │   └── report_metadata.json                  # Confluence page ID and URL
│   └── notifications/
│       └── notification_log.json                 # MS Teams notification history
└── comparisons/
    └── <comparison_id>/
        ├── comparison_report_*.md                # Multi-run comparison report
        ├── comparison_metadata_*.json            # Comparison metadata
        └── charts/                               # Comparison bar charts
```

> For the complete artifacts guide with producer/consumer relationships, see [docs/artifacts_guide.md](../../docs/artifacts_guide.md).
