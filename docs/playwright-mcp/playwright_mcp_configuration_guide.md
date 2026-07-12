# Playwright MCP Configuration Guide

### *This guide explains how to configure the Playwright MCP server for use with the JMeter MCP browser automation workflow, including trace capture, output directory setup, and Cursor IDE integration.*

---

## 1. Overview

The JMeter MCP browser automation workflow uses the
[Playwright MCP](https://github.com/microsoft/playwright-mcp) server to drive a
browser, capture network traffic, and feed it into the JMeter script generation
pipeline. The Playwright MCP server runs as a stdio-based MCP tool within Cursor
and is configured via two files:

1. **`config.json`** — Playwright MCP runtime settings (trace capture, output
   directory)
2. **Cursor `mcp.json`** — Tells Cursor how to launch the Playwright MCP server
   and where to find `config.json`

### Where It Fits in the Pipeline

```
Playwright MCP (browser automation)
  └─→ .playwright-mcp/traces/         ← Trace files (network, resources)
  └─→ .playwright-mcp/console-*.log   ← Console log output
  └─→ .playwright-mcp/page-*.yml      ← Page snapshots (one per step)
        └─→ capture_network_traffic    (JMeter MCP parses traces)
              └─→ network_capture.json
                    └─→ analyze_network_traffic
                          └─→ generate_jmeter_script
                                └─→ .jmx file (ready for JMeter)
```

---

## 2. Prerequisites

- **Node.js** — Required to run `npx` commands
- **Playwright MCP** — Installed automatically via `npx @playwright/mcp@latest`
- **Cursor IDE** — With MCP server support enabled

---

## 3. Playwright MCP Configuration (`config.json`)

The Playwright MCP server requires a `config.json` file that controls trace
capture behavior and output location.

### File Location

The `config.json` file can be placed anywhere on the user's machine. A recommended
location is a `.playwright-mcp/` folder in the user's home directory:

| OS | Recommended Path |
|---|---|
| **macOS/Linux** | `~/.playwright-mcp/config.json` |
| **Windows** | `%USERPROFILE%\.playwright-mcp\config.json` |

### Contents

```json
{
  "saveTrace": true,
  "outputDir": "<absolute_path_to_repo>/.playwright-mcp"
}
```

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `saveTrace` | boolean | Yes | Enables trace file capture. Must be `true` for the JMeter MCP pipeline to work. |
| `outputDir` | string | Yes | Absolute path to the output directory where Playwright MCP writes trace files, console logs, and page snapshots. Must point to the `.playwright-mcp/` directory inside the `mcp-perf-suite` repo so the JMeter MCP server can locate the traces. |

### Examples

**macOS/Linux** (`~/.playwright-mcp/config.json`):

```json
{
  "saveTrace": true,
  "outputDir": "/Users/<username>/Repos/GitHub/mcp-perf-suite/.playwright-mcp"
}
```

**Windows** (`%USERPROFILE%\.playwright-mcp\config.json`):

```json
{
  "saveTrace": true,
  "outputDir": "C:\\Users\\<username>\\Repos\\GitHub\\mcp-perf-suite\\.playwright-mcp"
}
```

> **Important:** The `outputDir` must be an absolute path. Relative paths are not
> supported by the Playwright MCP server. The `outputDir` must point to the
> `.playwright-mcp/` directory inside the repo, even though `config.json` itself
> lives outside the repo.

---

## 4. Cursor IDE Configuration (`mcp.json`)

Cursor's `mcp.json` file defines how MCP servers are launched. The Playwright MCP
entry must point to the `config.json` file created above.

### Location

Cursor's `mcp.json` is typically located at:

| OS | Path |
|---|---|
| **macOS/Linux** | `~/.cursor/mcp.json` |
| **Windows** | `%USERPROFILE%\.cursor\mcp.json` |

It may also be a workspace-level `.cursor/mcp.json` inside the project directory.

### Playwright MCP Entry

Add or update the `playwright` entry in the `mcpServers` section. The `--config`
argument must point to the same `config.json` file created in Section 3.

**macOS/Linux:**

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--config",
        "/Users/<username>/.playwright-mcp/config.json"
      ],
      "type": "stdio"
    }
  }
}
```

**Windows:**

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--config",
        "C:\\Users\\<username>\\.playwright-mcp\\config.json"
      ],
      "type": "stdio"
    }
  }
}
```

---

## 5. Output Directory Structure

When the Playwright MCP server runs a browser automation session with trace
capture enabled, it produces the following output in the `.playwright-mcp/`
directory:

```
<repo_root>/.playwright-mcp/
├── console-<timestamp>.log        # Browser console log (1 file per session)
├── page-<timestamp>.yml           # Page snapshots (1 file per browser step)
├── page-<timestamp>.yml
├── ...
└── traces/                        # Network trace data
    ├── trace-<id>.network         # NDJSON network trace (parsed by JMeter MCP)
    ├── trace-<id>.trace           # Playwright trace events
    ├── trace-<id>.stacks          # Stack trace data
    └── resources/                 # Response bodies referenced by SHA1
        ├── <sha1>.json
        ├── <sha1>.html
        └── ...
```

### File Descriptions

| File Pattern | Count | Description |
|---|---|---|
| `console-<timestamp>.log` | 1 per session | Captures browser console output (errors, warnings, logs) during the automation run. |
| `page-<timestamp>.yml` | 1 per browser step | YAML snapshot of the page state at each step. Multiple files are generated depending on the number of browser automation steps executed. |
| `traces/trace-*.network` | 1+ per session | NDJSON file containing `resource-snapshot` entries with request/response data. This is the primary input for the JMeter MCP `capture_network_traffic` tool. |
| `traces/trace-*.trace` | 1+ per session | Playwright trace events (actions, navigation, etc.). |
| `traces/trace-*.stacks` | 1+ per session | Stack trace data associated with the trace. |
| `traces/resources/*` | Varies | Response body files referenced by SHA1 hash from the network trace entries. |

---

## 6. Trace Archiving

Before each new Playwright browser automation run, the JMeter MCP
`archive_playwright_traces` tool archives all previous output to prevent stale
data contamination.

### What Gets Archived

The archive operation moves the following into a timestamped backup folder:

| Source | Pattern | Description |
|---|---|---|
| `.playwright-mcp/traces/` | Entire directory | Network traces, resources, and trace events |
| `.playwright-mcp/` | `console-*.log` | Browser console log file |
| `.playwright-mcp/` | `page-*.yml` | Page snapshot YAML files |

### Backup Folder Structure

All archived files are placed side-by-side in a single timestamped folder:

```
<repo_root>/.playwright-mcp/
├── traces/                             # Empty (recreated after archive)
└── traces_20260408_143000/             # Timestamped backup folder
    ├── trace-<id>.network              # Trace files (moved from traces/)
    ├── trace-<id>.trace
    ├── trace-<id>.stacks
    ├── resources/                      # Resource files (moved from traces/)
    │   └── ...
    ├── console-20260408_142500.log     # Console log (moved from parent)
    ├── page-20260408_142501.yml        # Page snapshots (moved from parent)
    ├── page-20260408_142510.yml
    └── ...
```

### When to Archive

Archiving is performed automatically by the `archive_playwright_traces` MCP tool,
which is **Step 1** of the Playwright Browser Automation **Skill** workflow. It should
be called before every new browser automation session.

---

## 7. JMeter MCP Integration

The JMeter MCP server (`jmeter-mcp/`) reads from the Playwright MCP output
directory to parse network traces and generate JMeter scripts.

### Traces Directory

The JMeter MCP server resolves the Playwright traces directory internally using
the default relative path `../.playwright-mcp/traces` (relative to the
`jmeter-mcp/` directory). This is not a user-configurable setting — it assumes
the standard repo layout:

```
mcp-perf-suite/
├── .playwright-mcp/          # Playwright MCP output (outputDir target)
│   ├── traces/               # Trace files read by JMeter MCP
│   └── ...
├── jmeter-mcp/               # JMeter MCP server
│   ├── config.windows.yaml
│   ├── config.mac.yaml
│   └── ...
└── ...
```

This is why the `outputDir` in `config.json` (Section 3) must point to the
`.playwright-mcp/` directory inside the repo — the JMeter MCP server expects
traces to be at that location.

### Relevant MCP Tools

| Tool | Purpose |
|---|---|
| `archive_playwright_traces` | Archive previous traces and verbose output files before a new run |
| `capture_network_traffic` | Parse Playwright traces and map to test spec steps |
| `analyze_network_traffic` | Identify correlations in the network capture |
| `generate_jmeter_script` | Generate a JMeter JMX script from the network capture |

---

## 8. Troubleshooting

### Traces Not Being Generated

- Verify `saveTrace` is set to `true` in `config.json`
- Verify `outputDir` in `config.json` is an absolute path and the directory exists
- Verify the `--config` argument in `mcp.json` points to the correct `config.json` path
- Restart Cursor after making changes to `mcp.json` or `config.json`

### "No *.network trace files found" Error

- Check that `.playwright-mcp/traces/` exists and contains `trace-*.network` files
- If the directory is empty, the Playwright MCP session may not have completed
  successfully, or traces were archived by a previous `archive_playwright_traces` call
- Run the browser automation steps again to generate fresh traces

### Console Log or YAML Files Not Archived

- These files (`console-*.log`, `page-*.yml`) are generated in the
  `.playwright-mcp/` parent directory, not inside `traces/`
- The `archive_playwright_traces` tool moves these files alongside the trace files
  into the timestamped backup folder
- If files remain after archiving, verify the file naming patterns match
  `console-*.log` and `page-*.yml`

### Cursor MCP Server Not Picking Up Config Changes

- Restart Cursor after modifying `config.json` or `mcp.json`
- MCP servers are long-running processes — changes to configuration or source code
  require a full Cursor restart to take effect
