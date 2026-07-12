# 📂 HAR Adapter Guide

### *This guide explains how to use the HAR (HTTP Archive) adapter to convert browser-recorded or proxy-captured HAR files into JMeter test scripts using the JMeter MCP pipeline.*

---

## 📖 1. What Is the HAR Adapter?

The HAR adapter (`convert_har_to_capture`) is an input adapter for the JMeter MCP server. It reads a standard HAR 1.2 file and converts it into the canonical, step-aware network capture JSON format that the existing JMeter MCP pipeline expects.

Think of it as an **ETL step** — it extracts HTTP transactions from a HAR file, transforms them into the internal format, and loads them into the same pipeline used by Playwright-based captures.

### Where It Fits in the Pipeline

```
HAR file (.har)
  └─→ convert_har_to_capture       ← You are here
        └─→ network_capture.json
              └─→ analyze_network_traffic   (optional: correlation analysis)
                    └─→ generate_jmeter_script
                          └─→ .jmx file (ready for JMeter)
```

The adapter produces the same JSON structure as `capture_network_traffic` (the Playwright-based tool), so all downstream tools work identically regardless of the input source.

---

## 🤔 2. When to Use It

| Scenario | Recommended Tool |
|----------|-----------------|
| You have a HAR file from Chrome DevTools, Charles, Fiddler, mitmproxy, or Postman | **`convert_har_to_capture`** |
| You want to automate browser actions and capture traffic live | `capture_network_traffic` (Playwright) |
| You have an OpenAPI/Swagger spec and want to generate synthetic traffic | `convert_swagger_to_capture` (Phase 2 — planned) |

The HAR adapter is ideal when:

- You already have a recorded HAR file and don't need live browser automation
- The application under test requires VPN, SSO, or other conditions that make Playwright automation difficult
- You captured traffic from a proxy tool during manual testing
- You want to quickly prototype a JMeter script from existing recordings

---

## ⚙️ 3. Prerequisites

### 3.1 JMeter MCP Server Running

The HAR adapter is registered as a tool on the JMeter MCP server. Ensure the server is running and accessible from your MCP host (Cursor, Claude Desktop, etc.).

### 3.2 Configuration — Domain Filtering

The adapter respects the `exclude_domains` list in your `config.yaml`. Before converting a HAR file, review and update this list to filter out noise:

```yaml
network_capture:
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
    - "cdn.jsdelivr.net"
    - "data.eu.pendo.io"
```

> **Tip:** After your first conversion, review the generated script. If you see requests to CDN, analytics, or APM domains that shouldn't be in your test, add those domains to the exclusion list and re-run.

### 3.3 Configuration — HTTP/2 Pseudo-Header Stripping

HAR files captured from HTTP/2 browsers may contain pseudo-headers (`:authority`, `:method`, `:path`, `:scheme`). These are stripped by default but can be controlled in `config.yaml`:

```yaml
network_capture:
  exclude_pseudo_headers: true   # default: true
```

Set to `false` if you need to preserve these headers for debugging purposes.

---

## 🚀 4. Step-by-Step Usage

### 4.1 Obtain a HAR File

Export a HAR file from one of these sources:

| Source | How to Export |
|--------|--------------|
| **Chrome DevTools** | Network tab → Right-click → "Save all as HAR with content" |
| **Firefox DevTools** | Network tab → Gear icon → "Save All As HAR" |
| **Charles Proxy** | File → Export Session → HTTP Archive (.har) |
| **Fiddler** | File → Export Sessions → HTTPArchive v1.2 |
| **mitmproxy** | `mitmdump -w output.har` or use mitmweb export |
| **Postman** | History → Export → HAR 1.2 |

### 4.2 Convert HAR to Network Capture

Call the `convert_har_to_capture` tool:

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `test_run_id` | Yes | — | Unique identifier for the test run (e.g., `login-flow-v1`) |
| `har_path` | Yes | — | Full absolute path to the HAR file |
| `step_strategy` | No | `auto` | How to group entries into steps (see Section 5) |
| `time_gap_threshold_ms` | No | `3000` | Gap threshold in milliseconds for `time_gap` strategy |

**Example:**

```
convert_har_to_capture(
    test_run_id = "login-flow-v1",
    har_path = "/path/to/recording.har"
)
```

### 4.3 Generate JMeter Script

Once the network capture JSON is generated, pass it to the script generator:

```
generate_jmeter_script(
    test_run_id = "login-flow-v1",
    json_path = "<network_capture_path from previous step>"
)
```

### 4.4 (Optional) Run Correlation Analysis

For production-ready scripts with dynamic value extraction, run the correlation analysis between steps 4.2 and 4.3:

```
analyze_network_traffic(
    test_run_id = "login-flow-v1"
)
```

This produces a `correlation_spec.json` that identifies dynamic values (tokens, IDs, session data) flowing between requests. Apply the JMeter correlation naming rules to generate `correlation_naming.json` with meaningful JMeter variable names.

---

## 🧩 5. Step Strategies

The adapter groups HAR entries into logical "steps" (which become JMeter Transaction Controllers). Four strategies are available:

### `auto` (Default — Recommended)

Automatically selects the best strategy:
- Uses `page` if the HAR file contains `pageref` fields (most browser-exported HARs do)
- Falls back to `time_gap` if no page references are present

### `page`

Groups entries by their `pageref` field, using HAR page titles as step labels. The adapter extracts readable labels from page titles (typically URLs) by taking the hostname and first path segment.

**Best for:** HAR files exported from browser DevTools.

**Example output:**
```
Step 1: app.example.com              → 2 entries
Step 2: auth.example.com/login       → 4 entries
Step 3: app.example.com/dashboard    → 12 entries
```

### `time_gap`

Groups entries by time gaps between requests. When the gap between consecutive requests exceeds `time_gap_threshold_ms` (default: 3000ms), a new step begins.

**Best for:** HAR files from proxy tools that don't include page references.

**Example output:**
```
Step 1: Request Group   → 8 entries (first burst of activity)
Step 2: Request Group   → 5 entries (after 3+ second pause)
Step 3: Request Group   → 3 entries (after another pause)
```

### `single_step`

Places all entries into one step. Useful for API-only recordings or when you plan to reorganize steps manually.

**Example output:**
```
Step 1: All Requests   → 42 entries
```

---

## 📁 6. Understanding the Output

### 6.1 Output Location

All artifacts are written under the standard artifacts directory:

```
artifacts/<test_run_id>/jmeter/
├── network-capture/
│   ├── network_capture_<timestamp>.json    ← The converted traffic data
│   └── capture_manifest.json               ← Provenance and conversion metadata
└── ai-generated_script_<timestamp>.jmx     ← Generated after script generation step
```

### 6.2 Network Capture JSON Structure

The output JSON is a dictionary keyed by step labels, each containing a list of request entries:

```json
{
  "Step 1: app.example.com": [
    {
      "request_id": "uuid",
      "method": "GET",
      "url": "https://app.example.com/api/data",
      "headers": { "content-type": "application/json" },
      "post_data": "",
      "step": {
        "step_number": 1,
        "instructions": "Step 1: app.example.com",
        "timestamp": "2026-02-21T19:00:00.000000"
      },
      "response": "{ ... }",
      "log_timestamp": "2026-02-21T19:00:00.000000",
      "status": 200,
      "response_headers": { "content-type": "application/json" }
    }
  ]
}
```

### 6.3 Capture Manifest

The `capture_manifest.json` records provenance for traceability:

```json
{
  "source_type": "har",
  "source_file": "recording.har",
  "conversion_tool": "convert_har_to_capture",
  "conversion_timestamp": "2026-02-21T19:00:00.000000",
  "step_strategy": "page",
  "entries_total": 61,
  "entries_filtered": 45,
  "entries_captured": 16,
  "har_version": "1.2",
  "har_creator": "WebInspector"
}
```

Use this to understand how many entries were filtered and which strategy was applied.

---

## 🔍 7. Troubleshooting

### No entries after filtering

**Symptom:** `ValueError: No usable entries after filtering`

**Cause:** All entries were excluded by domain filters, MIME type filters, or status code filters.

**Fix:**
- Check your `config.yaml` `exclude_domains` list — your target domain may be accidentally excluded
- Verify the HAR file contains HTTP/HTTPS requests (not just WebSocket or data URIs)
- Check if `capture_domain` is set in config — if so, only requests to that domain are kept

### HAR file too large

**Symptom:** `ValueError: HAR file too large (X MB). Maximum supported size is 200 MB.`

**Fix:**
- Split the HAR file into smaller recordings
- Use browser DevTools to record only the specific flow you need
- Clear the network tab before starting the recording

### Large HAR file warning (50+ MB)

**Symptom:** Warning in logs: `Large HAR file: X MB — parsing may take a moment`

**Action:** This is informational only. The file will still be processed, but expect longer conversion times.

### Unexpected step grouping

**Symptom:** All entries land in a single step when you expected multiple steps.

**Cause:** The HAR file may not contain `pageref` fields (common with proxy tools).

**Fix:** Explicitly set `step_strategy` to `time_gap` and adjust `time_gap_threshold_ms` as needed:

```
convert_har_to_capture(
    test_run_id = "my-test",
    har_path = "/path/to/recording.har",
    step_strategy = "time_gap",
    time_gap_threshold_ms = 2000
)
```

### Missing response bodies

**Symptom:** The `response` field in the network capture is empty for some entries.

**Cause:** This is a known HAR limitation. Chrome DevTools sometimes omits `content.text` for large responses, streaming responses, or responses received before the DevTools Network tab was open.

**Impact:** The JMeter script will still generate correctly (URLs, headers, and request bodies are preserved). However, correlation analysis may miss dynamic values that appear only in those response bodies. You can manually add extractors post-generation using JMeter's GUI.

---

## 📊 8. Quick Reference

### Filtering Applied Automatically

The adapter automatically excludes:

| Filter | What It Removes |
|--------|----------------|
| OPTIONS requests | CORS preflight requests |
| Binary content types | Images, fonts, video, audio, octet-stream |
| Non-HTTP schemes | Data URIs, WebSocket, blob URLs |
| Failed requests | Status 0 or -1 (aborted/failed) |
| Excluded domains | Domains listed in `config.yaml` `exclude_domains` |
| Static assets | When `capture_static_assets: false` in config |

### Tool Cheat Sheet

```
# Step 1: Convert HAR to network capture
convert_har_to_capture(test_run_id="my-test", har_path="/path/to/file.har")

# Step 2 (optional): Analyze correlations
analyze_network_traffic(test_run_id="my-test")

# Step 3: Generate JMeter script
generate_jmeter_script(test_run_id="my-test", json_path="<path from step 1>")
```
