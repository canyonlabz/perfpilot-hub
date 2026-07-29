# Datadog MCP Server

Welcome to the Datadog MCP Server! 🎉 This is a Python-based MCP server built with **FastMCP** to seamlessly integrate with Datadog's monitoring APIs for performance testing correlation and infrastructure metrics collection.

***

## ✨ Features

- **Environment-based configuration**: Load host and Kubernetes service definitions from `environments.json` for organized metric collection.
- **Host metrics collection**: Retrieve CPU and memory metrics for traditional hosts using Datadog's v1 API.
- **Kubernetes metrics collection**: Fetch container-level CPU metrics for microservices using Datadog's v2 timeseries API.
- **Log search**: Query Datadog logs using built-in templates, environment-aware dynamic queries, or reusable custom queries.
- **APM trace collection**: Retrieve APM traces from Datadog with the same flexible query system—built-in templates, environment-driven queries, and custom query support.
- **Custom query templates**: Define reusable project-level log and APM queries in `custom_queries.json` for consistent, shareable query definitions across your team.
- **Performance testing integration**: Output structured CSV files for downstream analysis and correlation with BlazeMeter test results.
- **Flexible environment schema**: Support both traditional hosts and Kubernetes clusters in a single environment configuration.
- **Robust error handling**: Comprehensive validation and context-aware error reporting throughout the workflow.
- **Consistent architecture**: Built using the same patterns as the BlazeMeter MCP Server for seamless integration.

***

## 🏁 Prerequisites

- Python 3.12.4 or higher installed
- Datadog API Key and Application Key (set in `.env`)
- Configured `environments.json` file defining your infrastructure

***

## 🚀 Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd datadog-mcp
```


### 2. Create \& Activate a Python Virtual Environment

This ensures the MCP server dependencies do not affect your global Python environment.

#### On macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```


#### On Windows (PowerShell)

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```


### 3. Configure Environment Variables

Create a `.env` file in the project root with your Datadog API credentials:

```env
DD_API_KEY=your_datadog_api_key_here
DD_APP_KEY=your_datadog_application_key_here
DD_API_BASE_URL=your_datadog_base_url_here
```


### 4. Configure Your Infrastructure

NOTE: When configuring environments.json, Host or Kubernetes settings can be customized per service—or fall back to config.yaml defaults if unspecified.

Create an `environments.json` file defining your environments, hosts, and Kubernetes services:

```json
{
  "schema_version": "1.0",
  "environments": {
    "QA": {
      "env_tag": "qa",
      "metadata": {
        "platform": "Windows Server 2025",
        "description": "QA environment for web/app/db tier"
      },
      "tags": ["team:qa"],
      "services": [
        {"service_name": "serviceA", "type": "web"},
        {"service_name": "serviceB", "type": "app"}
      ],
      "hosts": [
        {"hostname": "qa-web-01", "description": "webserver"},
        {"hostname": "qa-app-01", "description": "application server"},
        {"hostname": "qa-db-01", "description": "database"}
      ],
      "kubernetes": {
        "services": [
          {
            "service_filter": "*products*",
            "description": "Products microservices"
          },
          {
            "kube_service": "*auth*", 
            "description": "Authentication services"
          }
        ],
        "pods": [
          {
            "pod_filter": "app-web*",
            "description": "App Web Pod"
          },
          {
            "kube_service": "app-worker*",
            "description": "App Worker Pod"
          }
        ]
      }
    }
  }
}
```


***

## ▶️ Running the MCP Server

### Option 1: Run Directly with Python

```bash
python datadog.py
```

This runs the MCP server with the default `stdio` transport — ideal for running locally or integrating with Cursor AI.

### Option 2: Run Using `uv` (Recommended) ⚡️

You can use **uv** to simplify setup and execution. It manages dependencies and environments automatically.

#### Install `uv` (macOS, Linux, Windows PowerShell)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```


#### Run the MCP Server with `uv`

```bash
uv run datadog.py
```


***

## ⚙️ MCP Server Configuration (`mcp.json`)

You can configure how Cursor or other MCP hosts start the server by adding to your `mcp.json` file:

```json
{
  "mcpServers": {
    "datadog": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/your/datadog-mcp",
        "run",
        "datadog.py"
      ]
    }
  }
}
```

Replace `/path/to/your/datadog-mcp` with your local path.

***

## 🛠️ Usage

Your MCP server exposes these primary tools for Cursor, agents, or other MCP clients:


| Tool | Description |
| :-- | :-- |
| `load_environment` | Load environment configuration from `environments.json` and store in context |
| `get_host_metrics` | Retrieve CPU and memory metrics for all hosts in the current environment |
| `get_kubernetes_metrics` | Fetch CPU metrics for Kubernetes containers/services in the current environment |
| `get_logs` | Search Datadog logs using built-in templates, environment-aware queries, or custom queries from `custom_queries.json` |
| `get_apm_traces` | Retrieve APM traces from Datadog using built-in templates, environment-aware queries, or custom queries from `custom_queries.json` |


***

## 🔁 Typical Workflow

A standard Datadog MCP workflow for performance testing correlation:

1. **Load Environment Configuration**
    - `load_environment`: Load the desired environment (e.g., "QA", "UAT") and store configuration in context.
2. **Collect Infrastructure Metrics**
    - `get_host_metrics`: Retrieve CPU and memory metrics for traditional hosts during your performance test window.
    - **OR** `get_kubernetes_metrics`: Collect container-level CPU metrics for microservices during the test.
3. **Collect Logs**
    - `get_logs`: Query Datadog logs for the test window using built-in templates (e.g., `http_errors`, `all_errors`) or custom queries defined in `custom_queries.json`.
4. **Collect APM Traces**
    - `get_apm_traces`: Retrieve APM traces for the test window using built-in templates (e.g., `http_errors`, `slow_requests`) or custom queries defined in `custom_queries.json`.
5. **Analyze Results**
    - CSV artifacts are automatically saved to `artifacts/{run_id}/datadog/` for downstream analysis.
    - Correlate infrastructure metrics, logs, and APM traces with BlazeMeter performance test results.

> **Note:** Both `get_logs` and `get_apm_traces` support a flexible query system with built-in templates, environment-aware dynamic queries, and reusable custom queries via `custom_queries.json`. See [Custom Query Configuration](#-custom-query-configuration-custom_queriesjson) below and the full [Datadog Query Guide](../docs/datadog_query_guide.md) for details.

***

## 📊 CSV Output Examples

MCP’s APM module emits post-test metrics using a unified CSV schema that supports host/VM and containerized (Kubernetes) infrastructures.

### Host Metrics CSV

```csv
env_name,env_tag,scope,hostname,filter,container_or_pod,timestamp_utc,metric,value,unit,derived_pct
STG,my_stg_env,host,my_hostname_of_vm,,,2024-07-22T12:29:00,system.cpu.user,0.7486701287759618,%,
STG,my_stg_env,host,my_hostname_of_vm,,,2024-07-22T12:29:00,system.mem.used,8945780736.0,B,
```

### Kubernetes Metrics CSV

```csv
env_name,env_tag,scope,hostname,filter,container_or_pod,timestamp_utc,metric,value,unit,derived_pct
QA,my_qa_env,k8s,,my-k8-service-api*,my-k8-pod-name,2025-09-19T14:41:50,kubernetes.cpu.usage.total,11095701.891776,nanocores,
QA,my_qa_env,k8s,,my-k8-service-api*,my-k8-pod-name,2025-09-19T14:41:50,kubernetes.memory.usage,394260480.0,bytes,
```

### 📌 Important Note on Kubernetes Service Filtering

When using **wildcard filters** (e.g., `*products*`, `*auth*`) in your `environments.json` configuration, all containers matching that pattern will be output to the same CSV file under the same `service_filter` value. This provides a consolidated view of all related services.

If you need **more granular breakdown** with separate CSV entries for each service, the recommendation is to avoid wildcards and define each service explicitly on its own line in the `kubernetes.services` array:

```json
"kubernetes": {
  "services": [
    {
      "service_filter": "product-api",
      "description": "Product API service"
    },
    {
      "service_filter": "product-worker", 
      "description": "Product background worker"
    }
  ]
}
```

This approach gives you individual service-level metrics that are easier to analyze and correlate with specific performance test components.

***

## 🔍 Custom Query Configuration (`custom_queries.json`)

The `get_logs` and `get_apm_traces` tools support reusable custom query templates defined in a `custom_queries.json` file. This is the recommended way to define project-level queries that your team can share and reuse.

### Setup

Copy the provided example file to create your own configuration:

```bash
cp custom_queries.example.json custom_queries.json
```

Then edit `custom_queries.json` to define your project-specific queries:

```json
{
  "schema_version": "1.0",
  "apm_queries": {
    "app_500_errors": {
      "description": "Application Services - HTTP 500 errors",
      "query": "service:(my-app-web OR my-worker-web) env:qa-west @http.status_code:500"
    },
    "app_slow_requests": {
      "description": "Application Services - Slow requests (>5s)",
      "query": "service:(my-app-web OR my-worker-web) env:qa-west @duration:>5000000000"
    }
  },
  "log_queries": {
    "app_error_logs": {
      "description": "Application Services - Application error logs",
      "query": "service:my-app-web status:error"
    },
    "app_exception_logs": {
      "description": "Application Services - Logs with stack traces",
      "query": "service:my-app-web \"Exception\""
    }
  }
}
```

Custom query types are referenced by name when calling the tools (e.g., `query_type="app_500_errors"`).

> **📘 Full documentation:** See the [Datadog APM & Log Query Guide](../docs/datadog_query_guide.md) for a complete reference on query resolution order, built-in templates, environment-based dynamic queries, and custom query best practices.

***

## 📁 Project Structure

```
datadog-mcp/
├── datadog.py                        # MCP server entrypoint (FastMCP)
├── services/
│   ├── datadog_api.py                # Datadog metrics API & helper functions
│   ├── datadog_logs.py               # Datadog log search & helper functions
│   └── datadog_apm.py                # Datadog APM trace collection & helper functions
├── utils/
│   ├── config.py                     # Utility for loading config.yaml
│   └── datadog_config_loader.py      # Loader for environments.json & custom_queries.json
├── environments.json                 # Environment/infrastructure definitions
├── custom_queries.json               # Custom log & APM query templates (copy from .example.json)
├── custom_queries.example.json       # Example custom queries file
├── config.yaml                       # Centralized, environment-agnostic config
├── pyproject.toml                    # Modern Python project metadata & dependencies
├── requirements.txt                  # Dependencies
├── README.md                         # This file
└── .env                              # Local environment variables (API keys)
```


***

## 🔧 Configuration Files

### `config.yaml`

```yaml
artifacts:
  # Dynamically resolved to {repo_root}/artifacts when left empty.
  artifacts_path: ""

datadog:
  # Dynamically resolved to {repo_root}/datadog-mcp/environments.json when left empty.
  environments_json_path: ""
  # Dynamically resolved to {repo_root}/datadog-mcp/custom_queries.json when left empty.
  custom_queries_json_path: ""
  time_zone: "America/New_York"
  log_page_limit: 1000    # Number of log entries to fetch per page
```

> **Note:** Path settings (`artifacts_path`, `environments_json_path`, `custom_queries_json_path`) are dynamically resolved at startup. Leave them empty to use the defaults, or set an explicit absolute path for a custom location.


### Environment Schema

- **env_tag**: Technical Datadog environment tag for filtering
- **hosts**: List of hostnames for traditional host monitoring
- **kubernetes.services**: Service filters for container monitoring
- **metadata**: Environment descriptions and specifications

***

## 🚧 Future Enhancements

- **Custom metric support**: Allow arbitrary Datadog metric queries

***

## 🤝 Integration with Performance Testing

This MCP server is designed to work alongside the **BlazeMeter MCP Server** for complete performance testing workflows:

1. **Start BlazeMeter test** → Get `run_id`, start/end times
2. **Load Datadog environment** → Configure infrastructure monitoring
3. **Collect infrastructure metrics** → Host or Kubernetes CPU/memory metrics
4. **Collect logs & APM traces** → Error logs, slow requests, HTTP failures
5. **Analyze correlation** → Compare infrastructure load, logs, and traces with performance results

***

## 🤝 Contributing

Feel free to open issues or submit pull requests!

***

Created with ❤️ using FastMCP and Datadog APIs
