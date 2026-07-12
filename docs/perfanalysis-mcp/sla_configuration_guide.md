# 🎯 SLA Configuration Guide

### *This guide explains how to configure Service Level Agreements (SLAs) for the PerfAnalysis MCP server using the centralized `slas.yaml` configuration file. SLAs define the performance thresholds your APIs must meet, enabling automated compliance checks during analysis.*

---

## 1. 🧭 Overview

The MCP Performance Suite uses a **single YAML file** (`slas.yaml`) as the source of truth for all SLA definitions. This replaces the previous approach of scattering SLA values across `config.yaml` and hardcoded defaults.

**Key benefits:**
- One file to manage all SLA thresholds
- Per-API overrides using pattern matching
- Configurable percentile metric (P90, P95, P99)
- Configurable error rate thresholds
- SLA validation feedback via MCP context messages

| File | Location | Purpose |
|------|----------|---------|
| `slas.yaml` | `perfanalysis-mcp/slas.yaml` | Active SLA configuration (you create this) |
| `slas.example.yaml` | `perfanalysis-mcp/slas.example.yaml` | Annotated template with examples |

> **Important:** The `slas.yaml` file is **required**. If it is missing, the analysis will immediately stop with a clear error message. There are no hardcoded fallback values.

---

## 2. ⚡ Quick Start

1. Copy the example file:
   ```bash
   cd perfanalysis-mcp
   cp slas.example.yaml slas.yaml
   ```

2. Edit `slas.yaml` to match your environment's SLA requirements.

3. Run analysis with an optional SLA profile:
   ```
   analyze_test_results(test_run_id="12345", sla_id="order_management")
   ```

---

## 3. 🏗️ Configuration Structure

The `slas.yaml` file has two main sections:

### 3.1 File-Level Default SLA

Applied when no `sla_id` is provided, or when a profile doesn't override a field.

```yaml
default_sla:
  response_time_sla_ms: 5000
  sla_unit: "P90"
  error_rate_threshold: 1.0
```

| Field | Required | Description |
|-------|----------|-------------|
| `response_time_sla_ms` | Yes | Response time threshold in milliseconds |
| `sla_unit` | Yes | Percentile to evaluate against: `P90`, `P95`, or `P99` |
| `error_rate_threshold` | Yes | Maximum acceptable error rate as a percentage |

### 3.2 SLA Profiles

Define one or more SLA profiles under the `slas:` key. Each profile is a named collection of SLA rules.

```yaml
slas:
  - id: "order_management"
    description: "Order Management Service APIs"
    default_sla:
      response_time_sla_ms: 5000
      sla_unit: "P90"
      error_rate_threshold: 1.0
    api_overrides:
      - pattern: "*/orders/export*"
        response_time_sla_ms: 10000
        error_rate_threshold: 2.0
        reason: "Bulk export endpoint, inherently slower"
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier passed as `sla_id` to MCP tools |
| `description` | No | Human-readable description |
| `default_sla` | Yes | Default thresholds for this profile |
| `default_sla.response_time_sla_ms` | Yes | Profile-level response time threshold |
| `default_sla.sla_unit` | No | Inherits from file-level `default_sla` if omitted |
| `default_sla.error_rate_threshold` | No | Inherits from file-level `default_sla` if omitted |
| `api_overrides` | No | List of pattern-based SLA overrides |

---

## 4. 🧮 Pattern Matching and Precedence

API overrides use **glob-style pattern matching** against JMeter sampler labels. The `*` wildcard matches any sequence of characters.

### 4.1 Three-Level Precedence (Most-Specific First)

When resolving the SLA for a given API label, the system checks patterns in this order:

| Priority | Pattern Type | Example | Matches |
|----------|-------------|---------|---------|
| 1 (highest) | Full JMeter label | `TC01_TS02_/api/orders/export` | Exact label match |
| 2 | Test Case + Test Step | `TC01_TS02_*` or `TC01_S02_*` | All APIs under that test step |
| 3 (broadest) | Test Case only | `TC01_*` | All steps and APIs under that test case |

**How it works:**

1. All `api_overrides` patterns are classified by specificity level (1, 2, or 3).
2. Patterns are evaluated in order from most-specific to broadest.
3. Within the same specificity level, the **first match in file order** wins.
4. If no pattern matches, the **profile-level** `default_sla` is used.
5. If no profile is specified (`sla_id` is omitted), the **file-level** `default_sla` is used.

### 4.2 Precedence Example

Given this configuration:

```yaml
slas:
  - id: "checkout_service"
    default_sla:
      response_time_sla_ms: 5000
    api_overrides:
      - pattern: "TC02_TS01_/api/cart/checkout"
        response_time_sla_ms: 2000
        reason: "Critical checkout endpoint"
      - pattern: "TC02_TS01_*"
        response_time_sla_ms: 3000
        reason: "Cart operations"
      - pattern: "TC02_*"
        response_time_sla_ms: 4000
        reason: "All checkout workflow APIs"
```

The resolution for different labels:

| JMeter Label | Matched Pattern | SLA Threshold | Why |
|-------------|----------------|---------------|-----|
| `TC02_TS01_/api/cart/checkout` | `TC02_TS01_/api/cart/checkout` | 2000ms | Exact full label match (priority 1) |
| `TC02_TS01_/api/cart/add` | `TC02_TS01_*` | 3000ms | Test case + step match (priority 2) |
| `TC02_TS03_/api/payment/process` | `TC02_*` | 4000ms | Test case match (priority 3) |
| `TC05_TS01_/api/search/query` | *(none)* | 5000ms | Falls back to profile default_sla |

---

## 5. 🔎 Using SLA Profiles in Analysis

### 5.1 MCP Tool Parameters

Three MCP tools accept the optional `sla_id` parameter:

| Tool | Purpose |
|------|---------|
| `analyze_test_results` | Aggregate SLA compliance check per API |
| `correlate_test_results` | Temporal analysis with SLA threshold |
| `identify_bottlenecks` | Per-endpoint bottleneck detection with SLA |

**Example usage:**

```
# Use a specific SLA profile
analyze_test_results(test_run_id="80247571", sla_id="order_management")

# Use file-level default SLA (no sla_id)
analyze_test_results(test_run_id="80247571")
```

### 5.2 What Happens During Analysis

When `analyze_test_results` runs:

1. Each API's response time percentile (e.g., P90) is compared against its resolved SLA threshold.
2. APIs exceeding their threshold are flagged as non-compliant.
3. If `sla_id` is provided, the SLA pattern validator checks for any `api_override` patterns that don't match actual test result labels and reports them via context messages.

---

## 6. 🚨 Understanding the Error Rate Threshold

The `error_rate_threshold` defines the **maximum acceptable percentage of failed requests** for an API or group of APIs.

### 6.1 What It Means

If you set `error_rate_threshold: 1.0`, it means:
- Up to 1% of requests can fail and the API is still considered compliant.
- If 1.5% of requests fail, the API is flagged as non-compliant.

### 6.2 Why It Matters

Even if an API's response time is within the SLA, a high error rate means users are experiencing failures. Common causes include:

- **HTTP 5xx errors** -- Server-side failures (database timeouts, service crashes)
- **HTTP 4xx errors** -- Client-side issues that may indicate bad test data or auth failures
- **Connection timeouts** -- Network issues or overloaded servers
- **Application errors** -- Business logic failures returned with HTTP 200 but error payloads

### 6.3 Configuration Levels

The error rate threshold can be set at three levels (most specific wins):

```yaml
# Level 1: File-level default (applies to everything)
default_sla:
  error_rate_threshold: 1.0

slas:
  - id: "my_service"
    default_sla:
      # Level 2: Profile-level (applies to all APIs in this profile)
      error_rate_threshold: 1.5
    api_overrides:
      - pattern: "*/batch/import*"
        response_time_sla_ms: 15000
        # Level 3: API-level (applies only to this pattern)
        error_rate_threshold: 3.0
        reason: "Batch imports have higher error tolerance"
```

### 6.4 Guidelines for Setting Error Rate Thresholds

| Scenario | Suggested Threshold | Reasoning |
|----------|-------------------|-----------|
| Customer-facing APIs | 0.1% - 0.5% | Users directly impacted by failures |
| Internal service APIs | 1.0% - 2.0% | Retry mechanisms often handle transient errors |
| Batch/bulk operations | 2.0% - 5.0% | Higher volume, some failures expected |
| Legacy systems | 2.0% - 3.0% | Older systems may have known intermittent issues |

---

## 7. 📢 SLA Validation Feedback

When you provide an `sla_id`, the system automatically validates that all `api_override` patterns match at least one label in your test results. If patterns don't match, you'll receive informational messages like:

```
[INFO] SLA Validator: 1 of 4 api_override pattern(s) in profile 'order_management'
       did not match any test result labels.

       Unmatched patterns:
         - Pattern: '*/oauth/token*' (SLA: 500ms)
           Reason: Critical auth path

       Action: Review patterns in slas.yaml. Common causes:
         - Typo in the pattern string
         - The API was not included in the test run
         - The JMeter label format changed
```

This helps you keep your `slas.yaml` patterns in sync with your actual test scripts.

---

## 8. 🧩 Full Configuration Example

```yaml
version: "1.0"

# NOTE: Throughput SLAs (requests/sec) will be supported in a future version.

# File-level default -- used when no sla_id is provided or as
# the fallback for any field not specified at the profile level.
default_sla:
  response_time_sla_ms: 5000
  sla_unit: "P90"
  error_rate_threshold: 1.0

slas:
  # ===== Order Management APIs =====
  - id: "order_management"
    description: "Order Management Service APIs"
    default_sla:
      response_time_sla_ms: 5000
      sla_unit: "P90"
      error_rate_threshold: 1.0
    api_overrides:
      - pattern: "*/orders/export*"
        response_time_sla_ms: 10000
        error_rate_threshold: 2.0
        reason: "Bulk export endpoint, inherently slower"
      - pattern: "*/oauth/token*"
        response_time_sla_ms: 500
        reason: "Critical auth path"
      - pattern: "TC03_TS01_*"
        response_time_sla_ms: 3000
        reason: "Search workflow with complex queries"
      - pattern: "TC02_*"
        response_time_sla_ms: 4000
        reason: "Checkout workflow"

  # ===== Customer Portal E2E =====
  - id: "customer_portal"
    description: "Customer Portal End-to-End Workflows"
    default_sla:
      response_time_sla_ms: 2000
      sla_unit: "P90"
      error_rate_threshold: 1.5
    api_overrides:
      - pattern: "TC01_*"
        response_time_sla_ms: 3000
        reason: "Initial page load transactions"

  # ===== Legacy Services =====
  - id: "legacy_services"
    description: "Legacy services for baseline comparison"
    default_sla:
      response_time_sla_ms: 8000
      sla_unit: "P90"
      error_rate_threshold: 2.0
```

---

## 9. 🚚 Migration from Legacy Configuration

If you were previously using `response_time_sla` in `config.yaml`:

| Before (config.yaml) | After (slas.yaml) |
|----------------------|-------------------|
| `perf_analysis.response_time_sla: 5000` | `default_sla.response_time_sla_ms: 5000` |
| `bottleneck_analysis.sla_p90_ms: 5000` | Removed -- resolved from `slas.yaml` |
| Hardcoded `5000` in Python code | Removed -- all values from `slas.yaml` |

**Steps:**
1. Create `slas.yaml` from `slas.example.yaml`
2. Set your `default_sla` values
3. Add SLA profiles for your test suites
4. The legacy `response_time_sla` and `sla_p90_ms` settings in `config.yaml` are ignored

---

## 10. ❓ Frequently Asked Questions

**Q: What happens if `slas.yaml` is missing?**
A: Analysis will immediately fail with a `FileNotFoundError`. There are no hardcoded fallback values. This is by design to prevent silent misconfigurations.

**Q: Do I have to provide `sla_id` every time?**
A: No. If you omit `sla_id`, the file-level `default_sla` is used for all APIs. This is the simplest configuration -- just set your default thresholds and run analysis without `sla_id`.

**Q: Can I use regex in patterns?**
A: Patterns use **glob-style** matching (with `*` as wildcard), not regex. For example, `*/api/orders*` matches any label containing `/api/orders`. The `*` wildcard matches any sequence of characters.

**Q: Which percentile should I use for `sla_unit`?**
A: **P90 is the industry standard** for performance testing and is the recommended default. P95 and P99 are useful for stricter SLAs on critical endpoints. The percentile must match what your analysis calculates -- P90, P95, and P99 are all available.

**Q: Does this affect BlazeMeter, Datadog, or JMeter MCPs?**
A: No. SLA configuration only affects the PerfAnalysis and PerfReport MCPs. The other MCPs (BlazeMeter, Datadog, JMeter) do not perform SLA evaluations.
