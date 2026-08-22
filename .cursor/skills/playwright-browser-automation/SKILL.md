---
name: playwright-browser-automation
description: >-
  Run Playwright browser automation to capture network traffic and generate a JMeter
  JMX script. Use when the user mentions browser automation, Playwright recording,
  test spec execution, browser-based network capture, or generating a JMeter script
  from a live browser session.
---

# Playwright Browser Automation to JMeter Script

## When to Use This Skill

- User wants to run a browser automation workflow to capture network traffic
- User mentions Playwright, browser automation, test spec execution, or browser recording
- User wants to generate a JMeter script from live browser interactions
- User has a test spec (Markdown file) and wants to execute it with Playwright

---

## Reference

This section provides context for humans and capable models. For the step-by-step
execution instructions, skip to the **Execution** section below.

### What This Workflow Does

This workflow bridges **Playwright browser automation** with **JMeter script generation**.
It simulates realistic end-user behavior in a browser, captures the network traffic
generated during that session, and converts it into a parameterized JMeter load test script.

1. Archives any old Playwright traces to prevent conflicts
2. Loads a test spec (Markdown file with browser steps) and lets the user select one
3. Executes each browser step using Playwright with think time between steps
4. Captures the network traffic from the Playwright trace
5. Analyzes the capture to identify dynamic values (correlations) and auto-generates
   variable names for them
6. Generates a parameterized JMeter JMX script from the capture and naming data

### Test Spec Format

Test specs are Markdown files stored in `jmeter-mcp/test-specs/`. Each file contains
numbered steps that describe browser actions in natural language. The `get_browser_steps`
tool parses these files into executable steps.

Test specs can come from:
- **Manual creation** — Written directly as Markdown
- **ADO conversion** — Converted from Azure DevOps test cases using the skill at
  `.cursor/skills/ado-test-case-conversion/SKILL.md`

### Think Time Configuration

Think time simulates realistic user pauses between browser interactions.

- Config location: `jmeter-mcp/config.windows.yaml`/`jmeter-mcp/config.mac.yaml` (or `config.yaml`) under `browser.think_time`
- Default: `5000` milliseconds (5 seconds)
- Apply after each step completes, **except** after the final step
- Convert milliseconds to seconds for the `browser_wait_for` tool: `think_time_seconds = think_time_ms / 1000`

### Playwright Traces

- The `saveTrace: true` config (in `.playwright-mcp/config.json`) enables the tracing
  **capability**, but does NOT auto-record. You must explicitly call
  `browser_start_tracing` before browser steps and `browser_stop_tracing` after.
- `browser_start_tracing` creates `.trace` and `.network` files in the traces directory.
  `browser_stop_tracing` finalizes them with all captured data.
- Traces are saved to `<repo_root>/.playwright-mcp/traces/`
- Old traces must be archived before each new run to prevent stale data contamination
- Video/streaming files (`.m3u8`, `.ts`) are excluded from capture via the
  `capture_video_streams: False` config setting
- When the gateway runs in Docker, trace files must be accessible inside the container
  at `/app/.playwright-mcp/traces/` (via shared volume or `docker cp`)

### Tool Reference

| Tool | Purpose | Key Parameters |
|------|---------|---------------|
| `archive_playwright_traces` | Archive old traces before a new run | `test_run_id` (optional) |
| `get_test_specs` | Discover available test spec files | `test_run_id` (optional) |
| `get_browser_steps` | Parse a spec file into executable steps | `test_run_id`, `filename` |
| `capture_network_traffic` | Parse Playwright traces and map to spec steps | `test_run_id`, `spec_file` |
| `analyze_network_traffic` | Identify correlations and auto-generate variable names | `test_run_id` |
| `generate_jmeter_script` | Generate a JMX script from the network capture | `test_run_id`, `json_path` |

### Browser Interaction Tools (Playwright MCP)

These are the Playwright browser tools used during step execution:

| Tool | Purpose |
|------|---------|
| `browser_navigate` | Navigate to a URL |
| `browser_snapshot` | Get current page state and element references |
| `browser_click` | Click an element by ref |
| `browser_type` | Append text to a field (does not clear first) |
| `browser_fill` | Clear and replace text in a field or contenteditable |
| `browser_select_option` | Select a dropdown option |
| `browser_wait_for` | Wait for a specified duration (seconds) |
| `browser_handle_dialog` | Handle alert, confirm, or prompt dialogs |
| `browser_scroll` | Scroll the page or a container |
| `browser_start_tracing` | Start trace recording (call BEFORE browser steps) |
| `browser_stop_tracing` | Stop trace recording and finalize trace files (call AFTER all browser steps) |

### Related Rules

These Cursor Rules apply when using this skill:

- **`prerequisites.mdc`** — `test_run_id` and artifact structure validation
- **`skill-execution-rules.mdc`** — Follow steps in order, collect inputs first, do not skip
- **`mcp-error-handling.mdc`** — MCP tool error handling (retry policy, reporting format)
- **`browser-automation-guardrails.mdc`** — Think time, keep browser open, snapshot before interact, dialog handling
- **`jmeter-script-guardrails.mdc`** — Applies to downstream HITL editing and debugging after script generation

### Notes

- The correlation naming file (`correlation_naming.json`) is auto-generated by the
  `analyze_network_traffic` tool. The user can optionally review and adjust variable
  names using the correlation naming skill at `.cursor/skills/jmeter-correlation-naming/SKILL.md`.
- The `correlation_config.yaml` file (in `jmeter-mcp/`) allows users to define custom
  correlation naming conventions, application-specific variable name overrides, and
  extractor regex templates. It includes standard mappings for OAuth/SSO parameters,
  token fields, timestamp patterns, and source-location-to-extractor-type rules. The
  local `correlation_config.yaml` is gitignored; `correlation_config.example.yaml` is
  the committed reference template.
- After script generation, the user can proceed to HITL editing
  (`.cursor/skills/jmeter-hitl-editing/SKILL.md`) or debugging
  (`.cursor/skills/jmeter-debugging/SKILL.md`).
- Browser sessions capture real network traffic, so correlation analysis typically
  finds meaningful dynamic values to parameterize (OAuth tokens, session IDs, CSRF
  tokens, etc.).

---

## Execution

Follow these steps exactly, in order. Each step has one action unless noted otherwise.

---

### Collect Inputs

Ask the user for the following values. Do not proceed until all required values are collected.

```
REQUIRED:
  test_run_id = [ask user]
```

---

### Step 1 — Archive Existing Traces

**Input:** `test_run_id`

**Action:** Call MCP tool `archive_playwright_traces`

```
archive_playwright_traces(
  test_run_id = {test_run_id}
)
```

**Expected response:**

```json
{
  "status": "OK" or "NO_ACTION",
  "message": "...",
  "archived_path": "..." or null,
  "test_run_id": "{test_run_id}" or null
}
```

**Save:** Note the `status` value.

- `"OK"` — Old traces were archived successfully. Proceed.
- `"NO_ACTION"` — No old traces existed. Proceed.

**On error:** If `status` is `"ERROR"`, stop. Report the full error message to the user.

---

### Step 2 — Get Test Specs and Select

**Input:** `test_run_id`

**Action:** Call MCP tool `get_test_specs`

```
get_test_specs(
  test_run_id = {test_run_id}
)
```

**Expected response:** A list of available test spec files with metadata.

**Present** the list of specs to the user and let them choose which spec to execute.

**Save:** `spec_filename` = the filename the user selected.

**On error:** If `status` is `"ERROR"` or no specs are found, stop. Inform the user that
no test specs were found. They may need to create one first (manually or via the ADO
conversion skill).

---

### Step 3 — Get Browser Steps

**Input:** `test_run_id`, `spec_filename` (saved from Step 2)

**Action:** Call MCP tool `get_browser_steps`

```
get_browser_steps(
  test_run_id = {test_run_id},
  filename    = {spec_filename}
)
```

**Expected response:** A list of browser automation steps parsed from the spec file.

**Save:** `browser_steps` = the list of steps from the response.
**Save:** `total_steps` = the number of steps.

**Present** the steps to the user for confirmation before executing.

**On error:** If the tool returns an error, stop. Report the error to the user.

---

### Step 4 — Execute Browser Steps

**Input:** `browser_steps` (saved from Step 3), think time configuration

**Pre-requisite:** Read `browser.think_time` from `jmeter-mcp/config.windows.yaml`/`jmeter-mcp/config.mac.yaml`
(or `config.yaml`). Default: `5000` milliseconds. Convert to seconds:
`think_time_seconds` = `think_time_ms / 1000`.

**Action:** Execute each browser step sequentially using Playwright browser tools.

**Before executing any steps, start trace recording:**

```
browser_start_tracing()
```

Verify the response confirms "Trace recording started" and lists the `.trace` and
`.network` file paths.

**For each step (index 1 through total_steps):**

1. Take a snapshot before interacting:

```
browser_snapshot()
```

2. Execute the step action using the appropriate browser tool based on the step instruction:
   - Navigate: use `browser_navigate`
   - Click: use `browser_click` with the element ref from the snapshot
   - Type/Fill: use `browser_type` (append) or `browser_fill` (clear and replace)
   - Select dropdown: use `browser_select_option`
   - Handle dialog: use `browser_handle_dialog`
   - Use **exact element refs** from the snapshot — do not guess

3. If a dialog or pop-up appears, handle it immediately with `browser_handle_dialog`.

4. **If this is NOT the final step**, apply think time:

```
browser_wait_for(
  time = {think_time_seconds}
)
```

5. **If this IS the final step**, do NOT apply think time.

**On error during a step:**
- Take a new snapshot and retry once.
- If the step still fails after retry, report the failure and continue to the next step.
- Do **not** close the browser on errors.

**After all steps are complete:**

1. Stop trace recording:

```
browser_stop_tracing()
```

2. Verify trace files exist in `.playwright-mcp/traces/` (look for `.network` and `.trace` files).
3. Confirm the flow is complete to the user.
4. Keep the browser open for manual inspection.
5. Notify the user that trace recording has been stopped and network traffic is saved to `.playwright-mcp/traces/`.

**Save:** `spec_file_path` = the full path to the spec file used (needed for Step 5).

---

### Step 5 — Capture Network Traffic

**Input:** `test_run_id`, `spec_file_path` (saved from Step 4)

**Action:** Call MCP tool `capture_network_traffic`

```
capture_network_traffic(
  test_run_id = {test_run_id},
  spec_file   = {spec_file_path}
)
```

**Expected response:**

```json
{
  "status": "OK",
  "network_capture_path": "artifacts/{test_run_id}/jmeter/network-capture/network_capture_YYYYMMDD_HHMMSS.json",
  "test_run_id": "{test_run_id}"
}
```

**Save:** `capture_path` = value of `network_capture_path` from the response.

**On error:** If `status` is `"ERROR"`, stop. Report the full error message to the user.

---

### Step 6 — Analyze Network Traffic

**Input:** `test_run_id`

**Action:** Call MCP tool `analyze_network_traffic`

```
analyze_network_traffic(
  test_run_id = {test_run_id}
)
```

**Expected response:**

```json
{
  "status": "OK",
  "correlation_spec_path": "artifacts/{test_run_id}/jmeter/correlation_spec.json",
  "correlation_naming_path": "artifacts/{test_run_id}/jmeter/correlation_naming.json",
  "count": 7,
  "summary": { "total_correlations": 7, "business_ids": 3, "..." : "..." }
}
```

**Save:** `correlation_count` = value of `count` from the response.

**On error:** If `status` is `"ERROR"`, stop. Report the full error message to the user.

This step produces two files:
- `correlation_spec.json` — raw correlation analysis
- `correlation_naming.json` — auto-generated variable names (no manual step needed)

---

### Step 7 — Generate JMeter JMX Script

**Input:** `test_run_id`, `capture_path` (saved from Step 5)

**Action:** Call MCP tool `generate_jmeter_script`

```
generate_jmeter_script(
  test_run_id = {test_run_id},
  json_path   = {capture_path}
)
```

**Expected response:**

```json
{
  "status": "success",
  "jmx_path": "artifacts/{test_run_id}/jmeter/ai-generated_script_YYYYMMDD_HHMMSS.jmx",
  "message": "JMX script generated successfully: ..."
}
```

**Save:** `jmx_path` = value of `jmx_path` from the response.

**On error:** If `status` is `"error"`, stop. Report the full error message to the user.

> Note: This tool returns lowercase `"success"` / `"error"`, unlike the other tools
> which return uppercase `"OK"` / `"ERROR"`.

---

### Step 8 — Report to User

**Input:** `test_run_id`, `jmx_path` (saved from Step 7), `correlation_count` (saved from Step 6)

**Action:** Present the results to the user.

Tell the user:
- The JMX script was created at `{jmx_path}`
- `{correlation_count}` correlations were detected and parameterized
- The following artifacts were generated:

```
artifacts/{test_run_id}/jmeter/
├── network-capture/
│   └── network_capture_<timestamp>.json
├── capture_manifest.json
├── correlation_spec.json
├── correlation_naming.json
├── ai-generated_script_<timestamp>.jmx
└── testdata_csv/
    └── environment.csv
```

Ask the user:
- "Do you want to review or adjust the correlation variable names?" — If yes, follow
  the skill at `.cursor/skills/jmeter-correlation-naming/SKILL.md` (Scenario A).
- "Do you want to proceed with HITL editing or debugging?" — If yes, proceed to the
  appropriate downstream workflow.

---

## Error Handling

These rules apply to every step:

- If any MCP tool returns an error status, stop immediately.
- Report the full error message to the user.
- Do NOT write code to fix MCP tool issues.
- Do NOT proceed to the next step if the current step failed (except Step 4 browser
  execution, where individual step failures are reported and the next step is attempted).
- Do NOT close the browser on errors — leave it open for manual inspection.
- Ask the user for next steps.
