# 🚀 Aspire — PerfPilot Hub Orchestration

This folder is the **.NET Aspire** control plane for the entire PerfPilot Hub
stack. Running one command here brings up Postgres, every MCP server, the
agent backends, and the Web UI — wired together with health checks,
dependency ordering, and a live dashboard.

> **First-time setup?** → See
> [`docs/aspire/aspire-setup-guide.md`](../docs/aspire/aspire-setup-guide.md)
> for prerequisites, cert trust, and the full `dotnet user-secrets`
> provisioning walk-through. This README assumes you've already done that.

---

## 📂 What's in this folder

| File | Purpose |
|---|---|
| [`apphost.cs`](./apphost.cs) | Single-file C# AppHost — declares every resource, environment, bind mount, and wait relationship |
| [`aspire.config.json`](./aspire.config.json) | Project-level Aspire config — points at `apphost.cs` and defines launch profiles |
| [`apphost.run.json`](./apphost.run.json) | Dev launch profiles (dashboard / OTLP / resource-service ports) |
| `.gitkeep` | Keeps the folder tracked when empty |

No `.csproj`, no solution file — this is a modern single-file AppHost using
Aspire 13.x `#:sdk` / `#:package` / `#:property` directives.

---

## ⚡ Quick start

From this folder:

```powershell
aspire run
```

That's it. The dashboard opens automatically; every resource starts in
dependency order. Hit `Ctrl+C` to stop everything.

**Dashboard URLs** (set by [`apphost.run.json`](./apphost.run.json)):

| Protocol | URL |
|---|---|
| 🔒 HTTPS (default) | <https://localhost:17014> |
| 🌐 HTTP (fallback) | <http://localhost:15233> |

---

## 🎮 Common commands

### 🟢 Lifecycle

```powershell
aspire run              # foreground run; Ctrl+C stops everything
aspire start            # background run; returns the shell prompt
aspire ps               # list running AppHosts
aspire stop             # tear down the running AppHost gracefully
aspire stop --force     # ⚠️ destructive — force-kill resources; use only if a stop hangs
```

### 🔍 Inspect

```powershell
aspire describe                     # show the resource graph
aspire describe --include-hidden    # include Aspire-internal resources
aspire logs <resource-name>         # stream a single resource's logs
aspire doctor                       # run the built-in environment diagnostic
```

### 🔐 Secrets (Aspire-native wrapper around `dotnet user-secrets`)

```powershell
aspire secret list                       # list stored secrets for this AppHost
aspire secret set Parameters:<name> <v>  # add / update a secret
```

> Both `aspire secret ...` and `dotnet user-secrets ...` write to the same
> store (`UserSecretsId = perfpilot-aspire-apphost`). Use whichever fits
> your workflow. The setup guide uses `dotnet user-secrets` throughout
> because the commands are explicit about naming.

### 🛡️ Certificates

```powershell
aspire certs trust      # one-time trust of the Aspire dev HTTPS certificate
```

---

## 🧱 What's orchestrated

Running `aspire run` brings up 13 resources:

| Group | Resource | Port |
|---|---|---|
| 🗄️ **Infra** | `perfmem-pgvector-age` | 5432 |
| 🔌 **FastMCP (via gateway)** | `perfpilot-mcp-blazemeter` | 8110 |
| | `perfpilot-mcp-datadog` | 8111 |
| | `perfpilot-mcp-jmeter` | 8112 |
| | `perfpilot-mcp-perfanalysis` | 8113 |
| | `perfpilot-mcp-perfreport` | 8114 |
| | `perfpilot-mcp-confluence` | 8115 |
| | `perfpilot-mcp-perfmemory` | 8116 |
| | `perfpilot-mcp-github` | 8118 |
| 🎭 **MCP (direct)** | `perfpilot-mcp-playwright` | 8117 |
| 🚪 **Gateway** | `perfpilot-mcp-gateway` | 8125 |
| 🤖 **Agent backend** | `perfpilot-a2a` | 8101 |
| | `perfpilot-agui` | 8102 |
| 🖥️ **Frontend** | `perfpilot-ui` | dynamic |

See [`apphost.cs`](./apphost.cs) for exact wiring, dependencies, and
environment variables.

---

## 🔄 Typical day-to-day workflow

```powershell
# 1. Start everything in the background and get your prompt back
cd aspire
aspire start

# 2. Confirm it came up
aspire ps

# 3. Open the dashboard, do your work, iterate on code
#    (Resources with WithExternalHttpEndpoints are hot-reloadable)

# 4. When done
aspire stop
```

For foreground / `Ctrl+C` style sessions, just use `aspire run` instead
of `aspire start`.

---

## 🩺 Smoke checks

Once the dashboard shows every resource as 🟢 **Healthy**, confirm the
stack from PowerShell:

```powershell
# Gateway (routes to the 8 FastMCPs)
curl http://localhost:8125/perfpilot-mcp-gateway/health

# Agent backends
curl http://localhost:8101/health   # A2A
curl http://localhost:8102/health   # AG-UI

# PerfMemory via gateway
curl http://localhost:8125/perfpilot-mcp-perfmemory/health

# Playwright MCP (direct, not gateway-mounted)
curl http://localhost:8117/mcp
```

Then open the Web UI at the port shown for `perfpilot-ui` in the dashboard.

---

## 🧰 Troubleshooting

See [`docs/aspire/aspire-setup-guide.md § Troubleshooting`](../docs/aspire/aspire-setup-guide.md#-troubleshooting)
for the full catalog, including:

- 🔒 HTTPS / dashboard cert errors → `aspire certs trust`
- 🔑 Secrets not injected → `dotnet user-secrets list --id perfpilot-aspire-apphost`
- 🗄️ Postgres data-layout check (PG18+ bind-mount convention)
- 👤 Web UI shows empty conversation history → anonymous `perfpilot_token` identity
- 🔌 Port already in use → defaults are `5432, 8101, 8102, 8110-8118, 8125`
- 🐳 Docker Desktop not running
- 🎭 Playwright certificate errors

Quick first steps for anything weird:

```powershell
aspire doctor           # environment diagnostic
aspire describe         # confirm the resource graph matches your expectations
aspire logs <resource>  # stream the specific resource's output
```

---

## 📚 Related reading

- 📘 [`docs/aspire/aspire-setup-guide.md`](../docs/aspire/aspire-setup-guide.md) — Full setup, secrets, and troubleshooting walkthrough
- 📙 [`docs/README.md`](../docs/README.md) — Documentation hub index
- 🌐 [Aspire docs (aspire.dev)](https://aspire.dev) — Official docs; also browsable offline via `aspire docs list` / `aspire docs search`
- 📜 [`.cursor/rules/project-and-coding-guidelines.mdc`](../.cursor/rules/project-and-coding-guidelines.mdc) — Project rules and security guardrails
