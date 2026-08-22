# 📦 Artifacts Directory

Welcome to the `artifacts/` folder! This is the central hub for storing and organizing output files from various MCP servers used in performance testing workflows.

## 🧠 Purpose

This directory holds test run artifacts from:
- 🧪 **BlazeMeter MCP** – Load test configurations, results, and logs
- 📊 **APM MCP** – KPI metrics like CPU and memory usage (define in YAML or JSON)
- 🔍 **Performance Analysis MCP** – Will consume these artifacts for deeper insights
- 📄 **Performance Report MCP** – Generates formatted reports (Markdown, PDF or Word) from analysis outputs
- 🧷 **Confluence MCP** – Publishes reports and charts to Confluence spaces and pages

Each test run is stored in its own subfolder for modularity and traceability.

## 📁 Structure

```plaintext
artifacts/
├── <test_run_id>/
│   ├── blazemeter/
│   │   ├── aggregate_performance_report.csv
│   │   ├── jmeter.log
│   │   ├── test_config.json
│   │   └── test-results.csv
│   ├── datadog/
│   │   ├── host_metrics_[host_name].csv
│   │   ├── k8s_metrics_[service_name].csv
│   │   └── logs_<log_query_type>.csv
│   ├── analysis/
│   │   ├── correlation_analysis.json
│   │   ├── correlation_analysis.md
│   │   ├── correlation_analysis.csv
│   │   ├── infrastructure_analysis.json
│   │   ├── infrastructure_analysis.csv
│   │   ├── infrastructure_analysis.md
│   │   ├── performance_analysis.json
│   │   ├── performance_analysis.csv
│   │   └── performance_analysis.md
│   ├── reports/
│   │   ├── performance_report_<test_run_id>.md
│   │   ├── performance_report_<test_run_id>.xhtml
│   │   └── report_metadata_<test_run_id>.json
│   └── charts/
│       ├── cpu_metric_<service_name>.png
│       ├── memory_metric_<service_name>.png
│       └── p90_vs_vusers_dual_axis.png
└── comparisons/
    ├── comparison_metadata_<list_of_run_ids>.json
    └── comparison_report_<list_of_run_ids>.md
```
