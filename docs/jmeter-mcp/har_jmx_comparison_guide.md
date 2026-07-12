# 🔬 HAR-JMX Comparison Guide

### *This guide explains how to use the `compare_har_to_jmx` tool to cross-compare a fresh HAR capture against an existing JMeter JMX script, identifying API changes that require script updates.*

---

## 📖 1. What Is the HAR-JMX Comparison Tool?

The `compare_har_to_jmx` tool is a diagnostic tool on the JMeter MCP server. It takes a HAR (HTTP Archive) file and an existing JMeter JMX script, aligns the HAR requests to JMX samplers using a multi-pass matching algorithm, and produces a detailed report of differences categorized by severity.

**Key principle:** This tool is diagnostic only — it identifies what changed and produces a report. It does **not** modify the JMX script. Actual fixes are applied via the existing `edit_jmeter_component` / `add_jmeter_component` HITL tools using the `node_id` references from the report.

### Where It Fits in the Pipeline

```
Fresh HAR capture (.har)
  └─→ compare_har_to_jmx                     ← You are here
        ├─→ har_jmx_comparison_*.json           (machine-readable report)
        ├─→ har_jmx_comparison_*.md             (human-readable summary)
        └─→ PTE reviews findings
              └─→ edit_jmeter_component / add_jmeter_component
                    └─→ Updated .jmx script
```

### Relationship to `analyze_jmeter_script`

The `compare_har_to_jmx` tool can optionally consume the versioned `jmx_structure_*.json` file produced by `analyze_jmeter_script`. If the structure file is fresh (its `jmx_last_modified` matches the JMX file's current modification time), the tool uses the pre-parsed node index to speed up processing. If the file is stale or not provided, the tool parses the JMX from scratch.

---

## 🤔 2. When to Use It

| Scenario | Recommended Tool |
|----------|-----------------|
| Application APIs changed and you need to update your JMeter script | **`compare_har_to_jmx`** |
| You want to generate a new JMeter script from a HAR file | `convert_har_to_capture` → `generate_jmeter_script` |
| You want to analyze a JMX script's structure and get node IDs | `analyze_jmeter_script` |
| You want to manually edit specific JMX components | `edit_jmeter_component` / `add_jmeter_component` |

The comparison tool is ideal when:

- Your application has undergone API changes (new endpoints, URL restructuring, payload changes)
- You need to identify what's different between a fresh browser recording and your existing test script
- You want a systematic, categorized list of script updates needed instead of manual diffing
- You're debugging correlation failures caused by API response structure changes

---

## ⚙️ 3. Tool Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `test_run_id` | `str` | Yes | — | Identifies the artifact directory for output files |
| `har_file_path` | `str` | Yes | — | Absolute path to the HAR file |
| `jmx_file_path` | `str` | No | `""` | Path to the JMX file. If empty, auto-discovers via `discover_jmx_file` |
| `jmx_structure_file` | `str` | No | `""` | Path to a `jmx_structure_*.json` file from `analyze_jmeter_script` |
| `correlation_spec_file` | `str` | No | `""` | Path to `correlation_spec.json` for richer correlation drift detection |
| `strict_matching` | `bool` | No | `False` | When `True`, disables Pass 3 (fuzzy matching) to reduce false positives |
| `output_format` | `str` | No | `"both"` | `"json"`, `"markdown"`, or `"both"` |

### Return Value

```json
{
  "status": "OK",
  "message": "Comparison complete: 32 matched, 4 new, 2 possibly removed",
  "test_run_id": "run-001",
  "har_file": "chrome_capture_20260420.har",
  "jmx_file": "TC01_Checkout_Flow.jmx",
  "summary": {
    "matched_no_changes": 28,
    "new_endpoints": 4,
    "removed_endpoints": 2,
    "url_method_changes": 1,
    "payload_changes": 3,
    "correlation_drift": 2,
    "status_code_changes": 1
  },
  "match_stats": {
    "pass_1_exact": 20,
    "pass_2_parameterized": 10,
    "pass_3_fuzzy": 2,
    "total_matched": 32,
    "confidence_high": 28,
    "confidence_medium": 3,
    "confidence_low": 1
  },
  "exported_files": {
    "json": "artifacts/run-001/jmeter/analysis/har_jmx_comparison_20260420_213000.json",
    "markdown": "artifacts/run-001/jmeter/analysis/har_jmx_comparison_20260420_213000.md"
  },
  "error": null
}
```

---

## 🔄 4. Multi-Pass Matching Algorithm

The matching algorithm aligns HAR entries to JMX samplers through four sequential passes. Each pass operates on the **remaining unmatched** entries/samplers from previous passes.

### Pass 1 — Exact Match

**Strategy:** Compare HAR `url_path` against JMX samplers that have **no** `${...}` placeholders in their URL pattern.

**Criteria:** `method` + exact normalized path equality (case-insensitive, trailing slashes stripped).

**Confidence:** High

**Example:**

| HAR | JMX Sampler |
|-----|-------------|
| `GET /api/v1/config` | `TC01_TS01_GET_/api/v1/config` (path: `/api/v1/config`) |

### Pass 2 — Parameterized Regex Match

**Strategy:** For JMX samplers with `${...}` in their URL or `{param}`/`{{param}}` in their sampler name, convert to regex and match against HAR `url_path`.

**Regex conversion:**
- URL pattern `${customerId}` → `[^/]+`
- Sampler name `{customerId}` or `{{customerId}}` → `[^/]+`

**Confidence:**
- **High** — if exactly one JMX sampler matches the HAR entry
- **Medium** — if multiple JMX samplers match (disambiguation heuristic selects the best candidate based on pattern specificity)

**Example:**

| HAR | JMX Sampler | Regex |
|-----|-------------|-------|
| `GET /api/v1/customer/12345` | `TC01_TS02_GET_/api/v1/customer/{customerId}` (path: `/api/v1/customer/${customerId}`) | `^/api/v1/customer/[^/]+$` |

### Pass 3 — Fuzzy Path-Segment Match

> **Skipped when `strict_matching=True`**

**Strategy:** Tokenize URL paths into segments and score overlap, allowing for version number differences.

**Scoring:**
- Exact segment match: **1.0 point**
- Version number difference (`v1` vs `v2`): **0.5 point**
- Placeholder segment (`${var}` or `{param}`): **0.8 point**
- No match: **0.0 points**
- Score = total points / max(HAR segments, JMX segments)

**Confidence:**
- **Medium** — overlap score > 80%
- **Low** — overlap score 50-80%
- Below 50% — no match

**Example:**

| HAR | JMX Sampler | Score |
|-----|-------------|-------|
| `GET /api/v2/user/profile` | `TC01_TS03_GET_/api/v1/user/profile` | 0.875 (v1↔v2 = 0.5, rest exact) |

### Pass 4 — Unmatched Classification

Entries and samplers that remain unmatched after Passes 1-3:

- **HAR entries with no JMX match** → Classified as **New Endpoints** (need to be added to the JMX)
- **JMX samplers with no HAR match** → Classified as **Possibly Removed** (may have been removed from the application, or may simply not have been exercised during this particular HAR capture)

### Strict Matching Mode

The `strict_matching` parameter controls whether Pass 3 runs:

| Mode | Passes | Use Case |
|------|--------|----------|
| `strict_matching=False` (default) | 1, 2, 3, 4 | Broad coverage — includes fuzzy matches that may surface false positives |
| `strict_matching=True` | 1, 2, 4 | Conservative — only exact and parameterized matches |

**Recommended approach:** Run with `strict_matching=True` first to see high-confidence results, then rerun with `strict_matching=False` if you suspect there are URL structure changes that Pass 3 might catch.

---

## 📊 5. Difference Categories

For each matched pair (HAR entry ↔ JMX sampler), the tool compares across 10 categories:

### High Severity

| Category | Detection Logic | When It Fires |
|----------|----------------|---------------|
| **URL change** | Same logical endpoint (matched via fuzzy Pass 3), different actual path | URL restructuring, version changes |
| **Method change** | Same URL path, different HTTP method | `GET` → `POST` conversions, etc. |
| **Correlation drift** | Extractor JSONPath or regex no longer matches HAR response structure | API response schema changes that break dynamic value extraction |

Correlation drift is the most impactful category — it directly causes JMeter script failures at runtime. The tool checks:

1. **JSON extractors** — whether the JSONPath expression exists in the HAR response body schema
2. **Regex extractors** — whether the regex pattern matches the HAR response content
3. **Correlation spec** (if provided) — whether the variable's source field has moved or been renamed

When a JSONPath doesn't match, the tool attempts to find the closest matching key by leaf name and suggests a replacement path.

### Medium Severity

| Category | Detection Logic | When It Fires |
|----------|----------------|---------------|
| **Payload field added** | Key in HAR request body not present in JMX body template | New required fields in API requests |
| **Payload field removed** | Key in JMX body template not in HAR request body | Deprecated fields |
| **Payload field type changed** | Same key, different JSON type | Schema evolution (string → object, etc.) |
| **Response schema change** | Extractor path not found in HAR response structure | API response restructuring |

Payload comparison handles JMX bodies with `${...}` placeholders by sanitizing them before JSON parsing.

### Low Severity

| Category | Detection Logic | When It Fires |
|----------|----------------|---------------|
| **Status code change** | HAR response status differs from JMX `ResponseAssertion` expected values | Backend behavior changes |
| **Query param change** | Different query parameter keys between HAR and JMX URL pattern | API contract changes |
| **Header change** | Content-Type mismatch (e.g., HAR is JSON but JMX body doesn't parse as JSON) | Format migration |

---

## 📝 6. Output Reports

Reports are saved to `artifacts/<test_run_id>/jmeter/analysis/` with timestamped filenames.

### JSON Report (`har_jmx_comparison_<timestamp>.json`)

Machine-readable report designed for consumption by AI agents and debugging/HITL editing skills. Contains:

- **metadata** — file names, entry/sampler counts, strict_matching flag, generation timestamp
- **summary** — rolled-up counts per difference category
- **match_stats** — per-pass counts and confidence breakdown
- **matches** — full match records with HAR entry details, JMX sampler details (including node_ids, extractors, assertions), and categorized differences
- **new_endpoints** — unmatched HAR entries with suggested insertion location (after which `node_id`, in which parent controller)
- **removed_endpoints** — unmatched JMX samplers with `possibly_removed` classification

### Markdown Report (`har_jmx_comparison_<timestamp>.md`)

Human-readable executive summary organized by severity:

1. **Executive Summary** — category count table
2. **High Severity** — new endpoints (with suggested JMX location), correlation drift (with current/suggested paths), URL/method changes
3. **Medium Severity** — payload changes (field-level detail), response schema changes
4. **Low Severity** — status code, query parameter, and header changes
5. **Possibly Removed Endpoints** — JMX samplers with no HAR match

### File Retention

Comparison reports share the `max_analysis_files` rotation count from `config.example.yaml` (`jmx_editing.max_analysis_files`, default: 10). When the count is exceeded, the oldest files with the `har_jmx_comparison_` prefix are pruned.

---

## 🔧 7. Configuration

### `config.example.yaml` Settings

```yaml
jmx_editing:
  max_analysis_files: 10        # Max versioned files to retain (shared with structure exports)

har_jmx_comparison:
  schema_comparison_depth: 3    # Max nesting depth for JSON body schema comparison
```

**`schema_comparison_depth`** controls how many levels of JSON nesting are compared for both request body and response body schemas. Increase this for deeply nested API responses. For extractor paths specifically, the tool traces the full path regardless of this setting (because that's what actually breaks correlations).

### Domain/Path Filtering

The tool reuses the existing `network_capture.exclude_domains` and URL filtering configuration from `config.example.yaml`. HAR entries matching excluded domains or paths are filtered out before comparison, just as they are during normal network capture.

---

## 🚀 8. Usage Examples

### Basic Comparison

```
compare_har_to_jmx(
    test_run_id="run-001",
    har_file_path="/path/to/chrome_capture.har"
)
```

The tool auto-discovers the JMX file from the artifacts directory.

### With Structure File (Faster)

```
compare_har_to_jmx(
    test_run_id="run-001",
    har_file_path="/path/to/chrome_capture.har",
    jmx_structure_file="/path/to/artifacts/run-001/jmeter/analysis/jmx_structure_20260420_200000.json"
)
```

### Strict Matching Only

```
compare_har_to_jmx(
    test_run_id="run-001",
    har_file_path="/path/to/chrome_capture.har",
    strict_matching=True
)
```

### With Correlation Spec

```
compare_har_to_jmx(
    test_run_id="run-001",
    har_file_path="/path/to/chrome_capture.har",
    correlation_spec_file="/path/to/artifacts/run-001/jmeter/correlation_spec.json"
)
```

---

## 🔁 9. Recommended Workflow

### Step 1: Analyze the Existing Script

```
analyze_jmeter_script(test_run_id="run-001")
```

This produces `jmx_structure_*.json` and `jmx_structure_*.md` files that serve as a warm cache for the comparison tool.

### Step 2: Capture a Fresh HAR

Record a new HAR file from Chrome DevTools (or proxy tool) while performing the same workflow against the updated application.

### Step 3: Run Comparison (Strict First)

```
compare_har_to_jmx(
    test_run_id="run-001",
    har_file_path="/path/to/new_capture.har",
    jmx_structure_file="/path/to/jmx_structure_*.json",
    correlation_spec_file="/path/to/correlation_spec.json",
    strict_matching=True
)
```

Review the Markdown report for high-confidence findings.

### Step 4: Broaden If Needed

If you suspect URL structure changes (e.g., version bumps), rerun without strict matching:

```
compare_har_to_jmx(
    test_run_id="run-001",
    har_file_path="/path/to/new_capture.har",
    strict_matching=False
)
```

### Step 5: Apply Fixes

Use the JSON report's `node_id` references to drive targeted edits:

- **New endpoints** → `add_jmeter_component` using the `suggested_location`
- **Correlation drift** → `edit_jmeter_component` to update extractor paths
- **Payload changes** → `edit_jmeter_component` to update request bodies
- **URL/method changes** → `edit_jmeter_component` to update sampler properties

### Step 6: Re-Analyze and Verify

```
analyze_jmeter_script(test_run_id="run-001")
```

Re-run the analysis to refresh the structure files and verify the changes look correct.

---

## ⚠️ 10. Known Limitations & Edge Cases

| Limitation | Notes |
|------------|-------|
| **Fully parameterized URLs** (`${base_url}${endpoint}`) | Falls back to sampler testname URL extraction; if that also fails, marked as "unresolvable — manual review" |
| **Dynamic URL construction via scripting** (BeanShell/JSR223) | Out of scope for automated matching; script-driven URLs require manual review |
| **HAR from incomplete workflows** | "Removed endpoints" classification uses "Possibly Removed" to account for partial captures |
| **Redirect chains** | The tool compares against the final URL path after redirects |
| **Disabled JMX samplers** | Included in comparison but flagged as disabled; a match suggests re-enabling |
| **Regex extractor drift detection** | Limited to testing the regex against a JSON representation of the response schema (not the full raw response body) |
| **Multiple HAR entries for the same endpoint** | Grouped by method + path; significant count differences may indicate loop/iteration changes |

---

*Created: April 21, 2026*
