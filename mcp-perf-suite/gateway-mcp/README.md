# 🛩️ PerfPilot Hub

The **PerfPilot Hub** is the central MCP gateway for the MCP Perf Suite. It gives AI
agents a single endpoint into the performance testing lifecycle, routing them to
specialized MCP servers for JMeter, BlazeMeter, Datadog, analysis, reporting,
collaboration, and debugging memory.

Built on FastMCP v3.4.x's `create_proxy()` composition — each MCP server is
proxied through the gateway with full isolation.

> "Connect your AI agent to **PerfPilot Hub** and get the full performance testing
> toolchain through one MCP endpoint."

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Cursor / Claude / AI Agent                             │
│  (connects to ONE MCP endpoint)                         │
└───────────────────────────┬─────────────────────────────┘
                            │ stdio (local) or http (Aspire/Docker)
                            ▼
┌─────────────────────────────────────────────────────────┐
│  PerfPilot Hub                                          │
│  gateway-mcp/gateway.py               port 8125 (http)  │
│  FastMCP("perfpilot-hub")                               │
├─────────────────────────────────────────────────────────┤
│  8 servers mounted via create_proxy():                  │
│                                                         │
│  ⚡ jmeter       │ 🔥 blazemeter  │ 📊 datadog         │
│  🔬 perfanalysis │ 📝 perfreport  │ 📚 confluence      │
│  🧠 perfmemory   │ 🐙 github                           │
└─────────────────────────────────────────────────────────┘

Not gateway-mounted (require browser-based auth):
  💬 msteams   │ 📁 sharepoint
```

In **stdio mode** (local Cursor), each server runs as its own subprocess.
In **http mode** (Aspire / Docker), the gateway proxies to each MCP server's
HTTP endpoint.

---

## 📋 Prerequisites

- ✅ Python 3.12+
- ✅ All 8 gateway-mounted MCP servers set up with their own `.venv` and dependencies installed
- ✅ FastMCP v3.4.x+ installed in the gateway venv

---

## 🖥️ Setup — Windows

```powershell
# Navigate to the gateway directory
cd "C:\<path-to-repo>\mcp-perf-suite\gateway-mcp"

# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Verify installation
pip show fastmcp
```

### ▶️ Run PerfPilot Hub

```powershell
# With venv activated:
python gateway.py

# Or without activating:
.\.venv\Scripts\python.exe gateway.py
```

---

## 🍎 Setup — macOS

```bash
# Navigate to the gateway directory
cd ~/<path-to-repo>/mcp-perf-suite/gateway-mcp

# Create a virtual environment
python3 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify installation
pip show fastmcp
```

### ▶️ Run PerfPilot Hub

```bash
# With venv activated:
python gateway.py

# Or without activating:
.venv/bin/python gateway.py
```

---

## ⚙️ Configuration

PerfPilot Hub is configured via `config.yaml`:

```yaml
server:
  name: "perfpilot-hub"
  transport: "stdio"       # "stdio" for local, "http" for Aspire/Docker
  host: "0.0.0.0"
  port: 8125

servers:
  # Core servers (gateway-mounted)
  jmeter: true
  blazemeter: true
  datadog: true
  perfanalysis: true
  perfreport: true
  confluence: true
  perfmemory: true
  github: true
  # Not gateway-mounted (browser-based auth)
  # msteams: false
  # sharepoint: false
```

### 🔀 Environment Variable Overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_TRANSPORT` | `stdio` | Transport mode: `stdio` or `http` |
| `GATEWAY_HOST` | `0.0.0.0` | Bind address (http mode only) |
| `GATEWAY_PORT` | `8125` | Listen port (http mode only) |

---

## 🏷️ Namespace Mapping

All tools are namespaced with their server prefix:

| Server | Namespace | Example Tool |
|--------|-----------|--------------|
| ⚡ JMeter | `jmeter` | `jmeter_generate_jmeter_script` |
| 🔥 BlazeMeter | `blazemeter` | `blazemeter_get_workspaces` |
| 📊 Datadog | `datadog` | `datadog_collect_host_metrics` |
| 🔬 PerfAnalysis | `perfanalysis` | `perfanalysis_analyze_test_results` |
| 📝 PerfReport | `perfreport` | `perfreport_create_performance_test_report` |
| 📚 Confluence | `confluence` | `confluence_publish_page` |
| 🧠 PerfMemory | `perfmemory` | `perfmemory_store_debug_session` |
| 🐙 GitHub | `github` | `github_push_jmx_to_branch` |

> **Not gateway-mounted:** MS Teams (`msteams`) and SharePoint (`sharepoint`)
> require browser-based authentication and are accessed directly when configured.

---

## 🔌 Cursor Integration

Once PerfPilot Hub is tested, replace all individual MCP entries in `mcp.json` with one:

```json
{
  "mcpServers": {
    "perfpilot-hub": {
      "command": "C:\\<path-to-repo>\\mcp-perf-suite\\gateway-mcp\\.venv\\Scripts\\python.exe",
      "args": ["gateway.py"],
      "cwd": "C:\\<path-to-repo>\\mcp-perf-suite\\gateway-mcp"
    }
  }
}
```

---

## 🧪 Smoke Testing

```powershell
# Start PerfPilot Hub in stdio mode (will block waiting for input)
.\.venv\Scripts\python.exe gateway.py

# If it starts without errors, the hub is working.
# Press Ctrl+C to stop.
```

---

## 🗺️ PerfPilot Ecosystem

| Component | Purpose | Status |
|-----------|---------|--------|
| 🚀 **PerfPilot Hub** | MCP Gateway — single endpoint to all perf tools | ✅ Completed |
| 🤖 **PerfPilot Orchestrator** | A2A Server — external AI Agent communication | ✅ Completed |
| 🧠 **PerfMemory DB** | PostgreSQL + pgvector + Apache AGE | ✅ Completed |
| 🐳 **Docker Deployment** | Containerized hub + database | ✅ Completed |
| 🖥️ **FlightDeck Web UI** | CopilotKit / React chat interface | ✅ Completed |
| ✈️ **PerfPilot Agents** | AG2-based multi-agent orchestration layer | ✅ Completed |
