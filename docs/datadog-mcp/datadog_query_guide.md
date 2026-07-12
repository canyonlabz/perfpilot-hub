# 📘 Datadog APM, Log & KPI Timeseries Query Guide

### *A visual & intuitive guide to building queries inside the Datadog MCP Server*

---

## 🎯 1. Overview

The Datadog MCP Server supports a flexible, layered system for defining and executing both **APM Trace** and **Log Search** queries.

Queries can come from multiple sources:

| Source                                                 | Meaning                                   |
| ------------------------------------------------------ | ----------------------------------------- |
| 🔧 **Built-in templates**                              | Immediate, environment-tag driven queries |
| 🧩 **Environment-aware templates**                     | Queries built from services, hosts, k8s   |
| ✍️ **Inline custom queries**                           | User supplies raw Datadog query           |
| 📁 **Reusable custom queries** (`custom_queries.json`) | User-based, shareable templates           |

---

## 🧠 2. Query Resolution Order (Priority System)

When a tool receives a `query_type`, it resolves it in this strict order:

| Priority | Source                       | Description                                                    |
| -------- | ---------------------------- | -------------------------------------------------------------- |
| **1**    | Inline custom query          | `"custom"` + `custom_query` wins over everything               |
| **2**    | Built-in template            | Standard templates like `all_errors`, `slow_requests`          |
| **3**    | `custom_queries.json`        | Reusable global user-based templates                           |
| **4**    | Env-driven dynamic templates | `service_errors`, `host_errors`, `kubernetes_errors`           |

---

## 🚀 3. Built-in APM Templates

Templates that work anywhere without extra configuration.

| Query Type        | Description         | Example                          |
| ----------------- | ------------------- | -------------------------------- |
| `all_errors`      | All error traces    | `env:uat status:error`           |
| `http_500_errors` | HTTP 500 exceptions | `@http.status_code:500`          |
| `http_errors`     | Any 4xx/5xx         | `@http.status_code:[400 TO 599]` |
| `slow_requests`   | Requests >1s        | `@duration:>1000000000`          |

---

## 📊 4. Built-in Log Templates

| Query Type    | Description            | Example                                          | 
| ------------- | ---------------------- | ------------------------------------------------ | 
| `all_errors`  | Error-level logs       | `status:error OR level:ERROR`                    |
| `warnings`    | Warning logs           | `level:WARNING`                                  | 
| `http_errors` | HTTP errors in logs    | `@http.status_code:[400 TO 599]`                 | 
| `api_errors`  | API logs with failures | `@http.method:* AND @http.status_code:[400-599]` |

---

## 🏗️ 5. Environment-Based Dynamic Templates

These depend on what’s defined inside `environments.json`.

---

### 🔧 5.1 `service_errors`

Given:

```json
"services": [
  {"service_name": "checkout-api"},
  {"service_name": "auth-service"}
]
```

Generated query:

```
(env:uat) AND (service:checkout-api OR service:auth-service) AND status:error
```

---

### 🖥️ 5.2 `host_errors`

Given:

```json
"hosts": [
  {"hostname": "vm001"},
  {"hostname": "vm002"}
]
```

Generated query:

```
(env:uat) AND (host:vm001 OR host:vm002) AND status:error
```

---

### ☸️ 5.3 `kubernetes_errors`

Given:

```json
"kubernetes": {
  "services": [
    {"service_filter": "auth-*"},
    {"service_filter": "orders-api"}
  ]
}
```

Generated query:

```
(env:uat) AND (kube_service:auth-* OR kube_service:orders-api) AND status:error
```

---

## ✍️ 6. Inline Custom Query (Maximum Flexibility)

Set:

```json
{
  "query_type": "custom",
  "custom_query": "service:auth-api env:uat @duration:>5000000000"
}
```

You are giving the Datadog search engine the exact query to execute.
No templates apply.

---

## 📁 7. Global Custom Queries via `custom_queries.json`

This is the recommended way to define reusable project-level queries.

---

### 📁 File Location

```
datadog-mcp/custom_queries.json
```

---

### 📁 File Structure

```jsonc
{
  "schema_version": "1.0",

  "apm_queries": {
    "app_500_errors": {
      "description": "Application HTTP 500 errors",
      "query": "service:app-service env:qa @http.status_code:500"
    }
  },

  "log_queries": {
    "app_error_logs": {
      "description": "Application errors",
      "query": "service:app-service status:error"
    }
  }
}
```

---

## 🧮 8. Summary of All Query Sources

| Source                                | Description                | Recommended?         |
| ------------------------------------- | -------------------------- | -------------------- |
| Built-in templates                    | Common patterns            | ✅ Yes               |
| Env-based queries                     | Service/host/k8s filtering | ✅ Yes               |
| Inline custom                         | Fully manual               | ⚠️ Use when needed   |
| Global custom (`custom_queries.json`) | Reusable                   | 🌟 **Best practice** |

---

## 🧪 9. Example MCP Tool Usage

### 🔵 APM Example

```json
{
  "env_name": "qa1",
  "query_type": "app_500_errors",
  "start_time": "2024-12-01T00:00:00Z",
  "end_time": "2024-12-02T00:00:00Z"
}
```

### 🟠 Logs Example

```json
{
  "env_name": "qa2",
  "query_type": "service_errors",
  "start_time": "2024-12-05T00:00:00Z",
  "end_time": "2024-12-05T23:59:59Z"
}
```

---

## 📈 10. KPI Timeseries Queries (`kpi_queries`)

The `get_kpi_timeseries` tool executes custom Datadog V2 timeseries queries defined in the `kpi_queries` section of `custom_queries.json`. It outputs standardized CSV files using the same 10-column schema as CPU/Memory tools.

---

### 📈 10.1 Template Syntax: Double Curly Braces `{{placeholder}}`

KPI queries use `{{placeholder}}` syntax for dynamic value substitution. Double curly braces are used to avoid conflicts with Datadog's native tag filter syntax which uses single curly braces `{}`.

**Supported Placeholders:**

| Placeholder | Source | Used For |
| --- | --- | --- |
| `{{env_tag}}` | `env_config["env_tag"]` | All queries |
| `{{service_filter}}` | k8s service's `service_filter` or `kube_service` | K8s service queries |
| `{{hostname}}` | host's `hostname` | Host-based queries |
| `{{kube_namespace}}` | `env_config["kube_namespace"]` or `namespace` | K8s pod queries |
| `{{pod_filter}}` | pod's `pod_filter` or `kube_service` | K8s pod queries |

**Placeholder-driven iteration:** The presence of entity-level placeholders determines how many times the query executes. For example, a query containing `{{service_filter}}` will automatically iterate over all k8s services defined in the environment.

---

### 📈 10.2 Query Group Structure

Each entry in `kpi_queries` is a **query group** — one or more related Datadog queries sent together in a single API call. The structure mirrors the Datadog V2 timeseries API body.

```jsonc
{
  "kpi_queries": {
    "latency_percentiles": {
      "description": "Latency P90, P95, P99, Max",
      "scope": "k8s",
      "interval": 300000,
      "queries": [
        {
          "data_source": "metrics",
          "name": "p90",
          "query": "p90:trace.aspnet_core.request{env:{{env_tag}},service:{{service_filter}},span.kind:server}"
        },
        {
          "data_source": "metrics",
          "name": "p95",
          "query": "p95:trace.aspnet_core.request{env:{{env_tag}},service:{{service_filter}},span.kind:server}"
        }
      ],
      "formulas": [
        { "formula": "p90" },
        { "formula": "p95" }
      ]
    }
  }
}
```

**Field Reference:**

| Field | Required | Description |
| --- | --- | --- |
| `description` | Yes | Human-readable label for the query group |
| `scope` | No | `"k8s"` or `"host"`. Auto-detected from placeholders if omitted. |
| `interval` | No | Rollup interval in milliseconds. Default: `300000` (5 min). |
| `target_entity` | No | Entity name for static queries. Used in CSV filename. |
| `queries` | Yes | Array of Datadog query objects. |
| `queries[].data_source` | Yes | Always `"metrics"` for timeseries. |
| `queries[].name` | Yes | Query reference name — becomes the `metric` column in CSV output. **Must be unique within the group.** |
| `queries[].query` | Yes | Datadog metric query string. May contain `{{placeholders}}`. |
| `formulas` | Yes | Array of formula objects referencing query names. |

---

### 📈 10.3 Naming Conventions for `queries[].name`

The `name` field serves as both the formula reference for the Datadog API and the `metric` column value in CSV output. Use descriptive, unique names.

When copying queries from Chrome DevTools, **replace generic names** like `"query1"` with meaningful identifiers:

| Instead of | Use |
| --- | --- |
| `query1` (GC gen0) | `gc_size_gen0` |
| `query1` (CPU user) | `dotnet_cpu_user` |
| `query1` (connections) | `connections_total` |

**Duplicate names are blocked:** If duplicate `name` values are found within a query group, the tool stops and requires you to fix the names before re-running.

---

### 📈 10.4 Static vs Template Queries

**Template queries** contain `{{placeholder}}` tokens and iterate automatically over all matching entities in the environment.

**Static queries** have hardcoded values (no placeholders) and execute once. They require a `target_entity` field for the CSV filename and are validated against the loaded environment's `env_tag` — a mismatch produces a **blocking error** that stops execution.

**Static query example:**

```jsonc
{
  "my_app_latency": {
    "description": "My App - Latency P90 (hardcoded for QA)",
    "scope": "k8s",
    "target_entity": "my-app-web",
    "interval": 300000,
    "queries": [
      {
        "data_source": "metrics",
        "name": "p90",
        "query": "p90:trace.aspnet_core.request{env:my-qa-env.tag,service:my-app-web,span.kind:server}"
      }
    ],
    "formulas": [{ "formula": "p90" }]
  }
}
```

---

### 📈 10.5 CSV Output

**Filename pattern:** `kpi_metrics_[entity_name].csv`

**Schema (same 10-column format as CPU/Memory):**

```csv
env_name,env_tag,scope,hostname,filter,container_or_pod,timestamp_utc,metric,value,unit
```

**Data ordering:** Metric-type blocks — all datapoints for one metric are written chronologically before the next metric.

**Group tag handling:**
- Container/pod tags (`kube_container_name`, `kube_pod_name`, `kube_service`) go into the `container_or_pod` column
- Non-container tags (e.g., `http.status_code`) are encoded into the `metric` column as `name[tag_key:tag_value]`

**Example output (HTTP Errors with group tags):**

```csv
env_name,env_tag,scope,hostname,filter,container_or_pod,timestamp_utc,metric,value,unit
QA-1,my.env.tag,k8s,,my-service,,2026-02-20T15:57:30,errors[http.status_code:415],4,errors
QA-1,my.env.tag,k8s,,my-service,,2026-02-20T15:58:00,errors[http.status_code:415],3,errors
```

---

### 📈 10.6 Backup on Re-Run

When `get_kpi_timeseries` is invoked and KPI CSV files already exist, they are automatically backed up to `artifacts/{run_id}/datadog/backups/` with an incrementing suffix (e.g., `kpi_metrics_[entity]_000001.csv`) **before** any new API calls are made.

---

### 📈 10.7 Chrome DevTools Workflow

To capture new KPI queries from Datadog:

1. Open the Datadog dashboard with the desired KPI widget
2. Open Chrome DevTools → Network tab
3. Filter requests to `timeseries`
4. Find the V2 timeseries request and copy the JSON payload
5. Extract the `queries` and `formulas` arrays
6. Replace hardcoded `env:` and `service:` values with `{{env_tag}}` and `{{service_filter}}` placeholders
7. Rename any generic `"query1"` names to descriptive identifiers
8. Add the entry to `kpi_queries` in `custom_queries.json`

---

### 📈 10.8 Example MCP Tool Usage

```json
{
  "env_name": "QA",
  "query_names": ["latency_percentiles", "gc_heap_size"],
  "start_time": "2026-02-20T15:00:00Z",
  "end_time": "2026-02-20T16:30:00Z",
  "run_id": "81380224"
}
```
