# 📘 Template Authoring Guidelines for PerfReport MCP

### *How to create custom Markdown templates for performance test reports*

---

## 🧭 1. Overview

The **PerfReport MCP Server** generates performance test reports using a Markdown template.
The **Confluence MCP Server** then converts that Markdown into Confluence Storage Format (XHTML) for publishing.

Templates are fully customizable, allowing users to:

* Add sections
* Reorder content
* Change layout
* Add your own descriptions and headings
* Insert supported placeholders that get auto-filled based on analysis data

This guide explains **how to safely create your own templates**, the Markdown formatting rules, and a complete list of supported **placeholders** extracted from [`_build_report_context`](services/report_generator.py). 

---

## 🧩 2. Template Syntax Basics

Placeholders use a simple Mustache-like format:

```
{{PLACEHOLDER_NAME}}
```

Example:

```
**Average Response Time:** {{AVG_RESPONSE_TIME}} ms
```

### ✅ Naming Rules

* ALL CAPS
* Words separated by underscores
* Must match keys created by `_build_report_context()` and related helper functions

---

## 🖋️ 3. Markdown Formatting Rules (Confluence-Safe)

To ensure perfect compatibility with the Confluence MCP parser—**especially after fixing the underscore/italic bug**—these are the rules.

### ✔ Allowed Formatting

| Feature            | Supported | Notes                        |
| ------------------ | --------- | ---------------------------- |
| Headings           | ✔         | `#` through `######`         |
| Bold               | ✔         | `**bold**`                   |
| Italic             | ✔         | `*italic*`                   |
| Inline code        | ✔         | `` `code_here` ``            |
| Code blocks        | ✔         | Fenced: ` `                  |
| Tables             | ✔         | Standard Markdown tables     |
| Lists              | ✔         | Ordered + unordered          |
| Blockquotes        | ✔         | `> Note`                     |
| Horizontal rule    | ✔         | `---`                        |
| Chart placeholders | ✔         | `{{CHART_PLACEHOLDER: SCHEMA_ID}}` |

### ❌ Not Allowed

🚫 `_italic_` (underscore-based italics)
🚫 `__bold__` (underscore-based bold)

These were intentionally disabled to prevent stripping underscores from identifiers like:

```
TC01_TS01_sub01_/api/endpoint
```

---

## 🔠 4. Using Inline Code for APIs, Paths & Identifiers

Always wrap technical identifiers within backticks:

```
`TC01_TS01_sub01_/audit/deliverables/{id}`
```

This ensures:

* Underscores are preserved
* Confluence renders correctly
* No italics/bold parsing occurs

---

## 🧱 5. Placeholder Reference (Single-Run Report)

Extracted from:

* `_build_report_context()`
* `_build_api_table()`
* `_build_sla_summary()`
* `_build_executive_summary()`
* `_build_key_observations()`
* `_build_issues_table()`
* `_build_bottleneck_analysis()`
* `_build_recommendations()`

All code located in:
📄 **services/report_generator.py** 

---

### 🏷️ 5.1 Metadata & Context Placeholders

| Placeholder               | Description                  |
| ------------------------- | ---------------------------- |
| `{{RUN_ID}}`              | Test run ID                  |
| `{{GENERATED_TIMESTAMP}}` | ISO timestamp                |
| `{{MCP_VERSION}}`         | MCP server version           |
| `{{ENVIRONMENT}}`         | Env names from infra summary |
| `{{TEST_TYPE}}`           | Load test or other type      |
| `{{BLAZEMETER_REPORT_LINK}}` | Clickable link to BlazeMeter public report (requires `public_report.json`) |

**BlazeMeter Report Link:**
- The `{{BLAZEMETER_REPORT_LINK}}` placeholder renders as `[View Report](url)` in the Test Configuration table
- Requires the BlazeMeter workflow to call `get_public_report` first, which saves `public_report.json` to `artifacts/{run_id}/blazemeter/`
- If `public_report.json` is not found, this placeholder will show "N/A"

---

### 📊 5.2 Overall Performance Metrics

| Placeholder                                                               | Meaning            |
| ------------------------------------------------------------------------- | ------------------ |
| `{{TOTAL_SAMPLES}}`                                                       | Number of samples  |
| `{{SUCCESS_RATE}}`                                                        | Success percentage |
| `{{AVG_RESPONSE_TIME}}`                                                   | Avg RT (ms)        |
| `{{MIN_RESPONSE_TIME}}`                                                   | Min RT             |
| `{{MAX_RESPONSE_TIME}}`                                                   | Max RT             |
| `{{MEDIAN_RESPONSE_TIME}}`                                                | Median RT          |
| `{{P90_RESPONSE_TIME}}`, `{{P95_RESPONSE_TIME}}`, `{{P99_RESPONSE_TIME}}` | Percentiles        |
| `{{TEST_DURATION}}`                                                       | Duration (seconds) |
| `{{AVG_THROUGHPUT}}`                                                      | Requests/sec       |
| `{{PEAK_THROUGHPUT}}`                                                     | Peak TPS           |

---

### 📡 5.3 API Performance Table

Generated via `_build_api_table()`:

| Placeholder                 | Description                            |
| --------------------------- | -------------------------------------- |
| `{{API_PERFORMANCE_TABLE}}` | Full Markdown table of API performance |

This table includes:

* API Name
* Sample count
* Average, min, max, P95
* Error rate
* SLA flag (✔ / ❌)

---

### 📜 5.4 SLA Summary

| Placeholder       | Description            |
| ----------------- | ---------------------- |
| `{{SLA_SUMMARY}}` | SLA compliance summary |

---

### 🧠 5.5 Executive Summary & Observations

| Placeholder             | Description            |
| ----------------------- | ---------------------- |
| `{{EXECUTIVE_SUMMARY}}` | Auto-generated summary |
| `{{KEY_OBSERVATIONS}}`  | Bullet list            |

---

### ❗ 5.6 Issues & Errors

| Placeholder        | Description                             |
| ------------------ | --------------------------------------- |
| `{{ISSUES_TABLE}}` | Table of errors or “No issues detected” |

---

### 🖥️ 5.7 Infrastructure Metrics

Found via `_extract_infra_peaks()` and infra summaries:

| Placeholder                  | Meaning                   |
| ---------------------------- | ------------------------- |
| `{{PEAK_CPU_USAGE}}`         | Max CPU %                 |
| `{{AVG_CPU_USAGE}}`          | Avg CPU %                 |
| `{{CPU_CORES_ALLOCATED}}`    | Cores allocated           |
| `{{PEAK_MEMORY_USAGE}}`      | Max memory %              |
| `{{AVG_MEMORY_USAGE}}`       | Avg memory %              |
| `{{MEMORY_ALLOCATED}}`       | Total memory (GB)         |
| `{{INFRASTRUCTURE_SUMMARY}}` | Markdown summary of infra |

---

### 🧩 5.8 Correlation Analysis

| Placeholder               | Meaning                  |
| ------------------------- | ------------------------ |
| `{{CORRELATION_SUMMARY}}` | Insight summary          |
| `{{CORRELATION_DETAILS}}` | Correlation matrix table |

---

### 🔍 5.9 Bottlenecks & Recommendations

| Placeholder               | Description                    |
| ------------------------- | ------------------------------ |
| `{{BOTTLENECK_ANALYSIS}}` | Identifies bottlenecks         |
| `{{RECOMMENDATIONS}}`     | Auto-generated recommendations |

---

### 📁 5.10 Source Files

| Placeholder             | Meaning                |
| ----------------------- | ---------------------- |
| `{{SOURCE_FILES_LIST}}` | List of artifacts used |

---

### 📊 5.11 Chart Placeholders

Chart placeholders are replaced with embedded images when publishing to Confluence.

**Format:** `{{CHART_PLACEHOLDER: SCHEMA_ID}}`

#### Performance Charts

| Placeholder | Chart Type | Description |
| ----------- | ---------- | ----------- |
| `{{CHART_PLACEHOLDER: RESP_TIME_P90_VUSERS_DUALAXIS}}` | Dual-axis | P90 response time vs virtual users |
| `{{CHART_PLACEHOLDER: ERROR_RATE_LINE}}` | Single-axis | Error rate over time |
| `{{CHART_PLACEHOLDER: THROUGHPUT_HITS_LINE}}` | Single-axis | Throughput (hits/sec) over time |
| `{{CHART_PLACEHOLDER: TOP_SLOWEST_APIS_BAR}}` | Bar chart | Top slowest APIs by response time |

#### Infrastructure Charts (Single-Run)

| Placeholder | Chart Type | Description |
| ----------- | ---------- | ----------- |
| `{{CHART_PLACEHOLDER: CPU_UTILIZATION_LINE}}` | Single-axis | CPU % for specific host/service |
| `{{CHART_PLACEHOLDER: CPU_UTILIZATION_MULTILINE}}` | Multi-line | CPU % for all hosts/services |
| `{{CHART_PLACEHOLDER: CPU_UTILIZATION_VUSERS_DUALAXIS}}` | Dual-axis | CPU % vs virtual users |
| `{{CHART_PLACEHOLDER: CPU_CORES_LINE}}` | Single-axis | CPU core usage over time (Cores or mCPU) |
| `{{CHART_PLACEHOLDER: MEMORY_UTILIZATION_LINE}}` | Single-axis | Memory % for specific host/service |
| `{{CHART_PLACEHOLDER: MEMORY_UTILIZATION_MULTILINE}}` | Multi-line | Memory % for all hosts/services |
| `{{CHART_PLACEHOLDER: MEMORY_UTILIZATION_VUSERS_DUALAXIS}}` | Dual-axis | Memory % vs virtual users |
| `{{CHART_PLACEHOLDER: MEMORY_USAGE_LINE}}` | Single-axis | Memory usage over time (GB or MB) |

#### Comparison Charts (Multi-Run Reports)

| Placeholder | Chart Type | Description |
| ----------- | ---------- | ----------- |
| `{{CHART_PLACEHOLDER: CPU_PEAK_CORE_COMPARISON_BAR}}` | Vertical bar | Peak CPU core usage across test runs (uses navy-blue gradient) |
| `{{CHART_PLACEHOLDER: CPU_AVG_CORE_COMPARISON_BAR}}` | Vertical bar | Average CPU core usage across test runs (uses navy-blue gradient) |
| `{{CHART_PLACEHOLDER: MEMORY_PEAK_USAGE_COMPARISON_BAR}}` | Vertical bar | Peak memory usage across test runs (uses navy-blue gradient) |
| `{{CHART_PLACEHOLDER: MEMORY_AVG_USAGE_COMPARISON_BAR}}` | Vertical bar | Average memory usage across test runs (uses navy-blue gradient) |

**Chart Color Configuration:**
- Multi-line charts use the `multi_line` palette from `chart_colors.yaml` (10 high-contrast colors)
- Comparison bar charts use the `comparison` palette from `chart_colors.yaml` (navy-blue gradient)
- Colors can be customized in `chart_colors.yaml` or overridden per-chart in `chart_schema.yaml`

**Unit Configuration:**
- CPU charts can display in "cores" or "millicores" (configured in `chart_schema.yaml`)
- Memory charts can display in "gb" or "mb" (configured in `chart_schema.yaml`)

**Important Notes:**
- Use curly braces `{{...}}` NOT square brackets `[...]`
- Schema IDs must be ALL_CAPS with underscores
- Placeholders are preserved during Markdown-to-XHTML conversion
- The Confluence `update_page` tool replaces these with `<ac:image>` markup
- Placeholders without matching chart images are left as-is (visible as text)

**Example Usage in Template:**

```markdown
## 4.1 CPU Utilization

{{CHART_PLACEHOLDER: CPU_UTILIZATION_MULTILINE}}

The chart above shows CPU utilization across all monitored services.
```

---

# 🔢 6. Placeholder Reference (Comparison Report)

These appear in `default_comparison_report_template.md` and comparison generator.

### 🏷️ Per-Run Labels

```
{{RUN_1_LABEL}}, {{RUN_2_LABEL}}, ... {{RUN_5_LABEL}}
{{RUN_1_ID}}, {{RUN_2_ID}}, ... {{RUN_5_ID}}
{{RUN_1_DATE}}, {{RUN_2_DATE}}, ...
{{RUN_1_DURATION}}, {{RUN_1_SAMPLES}}, {{RUN_1_SUCCESS_RATE}}, ...
```

### 📉 Error Rates

```
{{RUN_1_ERROR_COUNT}} ... {{RUN_5_ERROR_COUNT}}
{{RUN_1_ERROR_RATE}} ... {{RUN_5_ERROR_RATE}}
{{RUN_1_ERROR_DELTA}} ... {{RUN_5_ERROR_DELTA}}
{{RUN_1_TOP_ERROR}} ... {{RUN_5_TOP_ERROR}}
```

### 🚨 Issues

```
{{CRITICAL_ISSUES_TABLE}}
{{PERFORMANCE_DEGRADATIONS_ROWS}}
{{INFRASTRUCTURE_CONCERNS_ROWS}}
```

### 🧪 API Comparison Tables

```
{{API_COMPARISON_ROWS}}
{{TOP_OFFENDERS_ROWS}}
```

### 📈 Throughput Comparison

```
{{RUN_1_AVG_THROUGHPUT}} ... {{RUN_5_AVG_THROUGHPUT}}
{{RUN_1_PEAK_THROUGHPUT}} ... {{RUN_5_PEAK_THROUGHPUT}}
{{THROUGHPUT_TREND}}
{{PEAK_THROUGHPUT_TREND}}
{{THROUGHPUT_SUMMARY}}
```

### 🖥️ Infra Comparisons

#### CPU & Memory Utilization (%)

```
{{CPU_COMPARISON_ROWS}}
{{CPU_IMPROVED_COUNT}}
{{CPU_DEGRADED_COUNT}}
{{CPU_STABLE_COUNT}}

{{MEMORY_COMPARISON_ROWS}}
{{MEMORY_IMPROVED_COUNT}}
{{MEMORY_DEGRADED_COUNT}}
{{MEMORY_STABLE_COUNT}}
```

#### CPU & Memory Usage (Cores/mCPU and GB/MB)

```
{{CPU_CORE_COMPARISON_ROWS}}
{{MEMORY_USAGE_COMPARISON_ROWS}}
```

**Unit Configuration for Comparison Tables:**
- CPU Core Usage tables can display in "cores" or "millicores" (configured in `report_config.yaml`)
- Memory Usage tables can display in "gb" or "mb" (configured in `report_config.yaml`)
- Configuration is under `infrastructure_tables.cpu_core_usage.unit.type` and `infrastructure_tables.memory_usage.unit.type`

### 🧮 Correlations

```
{{CORRELATION_INSIGHTS_SECTION}}
{{CORRELATION_KEY_OBSERVATIONS}}
```

### 📊 Comparison Chart Placeholders

Chart placeholders for comparison reports (see section 5.11 for full chart reference):

```
{{CHART_PLACEHOLDER: CPU_PEAK_CORE_COMPARISON_BAR}}
{{CHART_PLACEHOLDER: CPU_AVG_CORE_COMPARISON_BAR}}
{{CHART_PLACEHOLDER: MEMORY_PEAK_USAGE_COMPARISON_BAR}}
{{CHART_PLACEHOLDER: MEMORY_AVG_USAGE_COMPARISON_BAR}}
```

**Notes:**
- Comparison charts use the `comparison` color palette from `chart_colors.yaml` (navy-blue gradient)
- Each test run gets a distinct color from the palette
- Charts are generated per-entity (host/service) and saved to `artifacts/comparisons/{comparison_id}/charts/`

---

# 🧪 7. Testing Your Custom Template

Recommended workflow:

1. Generate a test report:

   ```
   perf-report.generate_performance_test_report
   ```

2. Convert Markdown to Confluence XHTML:

   ```
   confluence.generate_content_from_markdown
   ```

3. Inspect XHTML for:

   * Proper placeholder replacement
   * Table correctness
   * API names with underscores intact
   * Chart placeholders counted correctly

4. Publish to Confluence cloud/on-prem.

---

# 🎉 8. Best Practices

* Use `**bold**` and `*italic*` only
* Wrap API names in inline-code backticks
* Keep table columns aligned for readability
* Keep placeholders on their own lines where possible
* Use chart placeholders sparingly to avoid clutter
* Organize your template into numbered sections like default templates

---

# 📌 9. Example Snippet (Safe & Recommended)

```markdown
## 3.1 API Performance Details

Below is the performance breakdown for each API endpoint:

{{API_PERFORMANCE_TABLE}}

> Tip: For long API names such as `TC01_TS01_sub01_/audit/upload`, inline code preserves formatting.
```
