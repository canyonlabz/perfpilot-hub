# 🚀 Aspire Setup & Configuration Guide

This guide walks you through standing up the PerfPilot Hub locally using
[.NET Aspire](https://aspire.dev). Aspire is the orchestration layer that
starts, wires, and monitors every container, Python server, and JavaScript
app in the suite from a single command.

The AppHost is a **single-file C# AppHost** (`aspire/apphost.cs`) — no
`.csproj`, no solution file. It uses the Aspire 13.x
`#:sdk` / `#:package` / `#:property` directive style.

---

## 📋 Prerequisites

Install the following before running Aspire:

| Tool | Minimum Version | Notes |
|---|---|---|
| **.NET SDK** | 10.0 | Required by Aspire 13.x and single-file AppHosts |
| **Aspire CLI** | 13.6.0 | `irm https://aspire.dev/install.ps1 \| iex` (Windows) |
| **Docker Desktop** | 29.x | Must be running; Aspire launches containers through Docker |
| **Node.js** | 20.x | Required for the frontend (`AddJavaScriptApp`) |

Verify everything is installed:

```powershell
dotnet --list-sdks        # must list a 10.x SDK
aspire --version          # must report 13.6.0 or newer
docker version            # must show Docker running
node --version            # must show 20.x or newer
```

Run the built-in diagnostic to catch common issues:

```powershell
aspire doctor
```

> **First-time setup only — trust the developer certificate:**
>
> ```powershell
> aspire certs trust
> ```
>
> Required so the Aspire dashboard and resources can use HTTPS locally.

---

## 📁 Repository Layout

Everything Aspire-related lives under `aspire/`:

```
aspire/
└── apphost.cs    ← Single-file AppHost (resources, env, secrets, waits)
```

Resources defined in `apphost.cs` reference assets in two places:

- `docker/` — Dockerfiles for every containerized resource (gateway, FastMCPs,
  Playwright MCP, PostgreSQL, agent backend).
- `agent-framework/frontend/ui/` — Next.js frontend started via npm.

---

## 🧱 What's Orchestrated

Running `aspire run` from `aspire/` brings up **13 resources**:

| Group | Resource | Port | Notes |
|---|---|---|---|
| **Infra** | `perfmem-pgvector-age` | 5432 | PostgreSQL + pgvector + Apache AGE |
| **FastMCP (via gateway)** | `perfpilot-mcp-blazemeter` | 8110 | |
| | `perfpilot-mcp-datadog` | 8111 | |
| | `perfpilot-mcp-jmeter` | 8112 | |
| | `perfpilot-mcp-perfanalysis` | 8113 | |
| | `perfpilot-mcp-perfreport` | 8114 | |
| | `perfpilot-mcp-confluence` | 8115 | |
| | `perfpilot-mcp-perfmemory` | 8116 | Reads shared Postgres on `perfmemory` DB |
| | `perfpilot-mcp-github` | 8118 | Pushes JMX to non-main branches |
| **MCP (direct)** | `perfpilot-mcp-playwright` | 8117 | Not gateway-mounted (stateful browser sessions) |
| **Gateway** | `perfpilot-mcp-gateway` | 8125 | Single entry point for Cursor / agents |
| **Agent backend** | `perf-a2a-server` | 8101 | Python / FastAPI — Agent-to-Agent protocol |
| | `perf-agui-server` | 8102 | Python / FastAPI — CopilotKit bridge |
| **Frontend** | `perf-frontend` | dynamic | Next.js / CopilotKit |

**Not included** (require browser-based auth tokens — not container-friendly):
`msteams-mcp`, `sharepoint-mcp`.

**Not currently wired:** Azure DevOps MCP — the agent backend is not configured
with an `ADO_PAT` or `MCP_URL_ADO`. SCM integration flows through GitHub only.

---

## 🔐 Secrets Setup

Secrets are **never** stored in `.env` files, source code, or `appsettings.json`.
Locally they live in the .NET user-secrets store; in the cloud they're sourced
from **HashiCorp Vault** and injected into containers as environment variables.
Aspire's `AddParameter(..., secret: true)` reads the same key names from either
source, so the AppHost code is identical in both environments.

The AppHost declares a `UserSecretsId` of `perfpilot-aspire-apphost`, so
`dotnet user-secrets` picks the right store without needing `--id` or
`--project` arguments.

### Setting every required secret

Run each command from the repository root. Replace `<...>` placeholders with
real values. Values marked **secret** must be rotated if ever exposed.

```powershell
# ── Postgres (used by perfmemory, A2A, AG-UI) ────────────────────────────
dotnet user-secrets set "Parameters:postgres-user"     "<perfadmin>"            --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:postgres-password" "<postgres-password>"    --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:postgres-db"       "<perfagent_state>"      --id perfpilot-aspire-apphost

# ── OpenAI (used by perfmemory, A2A, AG-UI) ──────────────────────────────
dotnet user-secrets set "Parameters:openai-api-key" "<openai-api-key>" --id perfpilot-aspire-apphost

# ── Playwright MCP (certificate handling) ────────────────────────────────
dotnet user-secrets set "Parameters:playwright-cert-passphrase"     "<passphrase>" --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:playwright-cert-auto-select-cn" "<cert-cn>"    --id perfpilot-aspire-apphost

# ── JMeter MCP (JKS keystore) ────────────────────────────────────────────
dotnet user-secrets set "Parameters:jmeter-jks-pwd" "<jks-password>" --id perfpilot-aspire-apphost

# ── BlazeMeter MCP ───────────────────────────────────────────────────────
dotnet user-secrets set "Parameters:blazemeter-api-key"      "<bm-api-key>"       --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:blazemeter-api-secret"   "<bm-api-secret>"    --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:blazemeter-account-id"   "<bm-account-id>"    --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:blazemeter-workspace-id" "<bm-workspace-id>"  --id perfpilot-aspire-apphost

# ── Datadog MCP ──────────────────────────────────────────────────────────
dotnet user-secrets set "Parameters:dd-api-key"      "<dd-api-key>"              --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:dd-app-key"      "<dd-app-key>"              --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:dd-api-base-url" "<https://api.datadoghq.com>" --id perfpilot-aspire-apphost

# ── Confluence MCP (v2 API) ──────────────────────────────────────────────
dotnet user-secrets set "Parameters:confluence-v2-base-url"  "<https://yourcompany.atlassian.net/wiki>" --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:confluence-v2-user"      "<name@company.com>"                       --id perfpilot-aspire-apphost
dotnet user-secrets set "Parameters:confluence-v2-api-token" "<confluence-api-token>"                   --id perfpilot-aspire-apphost

# ── AI Assistant / SCM integration ───────────────────────────────────────
dotnet user-secrets set "Parameters:github-personal-access-token" "<github-personal-access-token>" --id perfpilot-aspire-apphost
```

### Verifying what's stored

```powershell
# List all secrets (values redacted)
dotnet user-secrets list --id perfpilot-aspire-apphost

# Show the on-disk location (never commit this file)
# Windows: %APPDATA%\Microsoft\UserSecrets\perfpilot-aspire-apphost\secrets.json
```

> **Alternative — Aspire CLI wrapper:** `aspire secret set Parameters:<name> <value>`
> auto-discovers the AppHost and writes to the same store. Either tool works.
> Use whichever matches your workflow.

## ▶️ Running Locally

Start every resource from the `aspire/` folder:

```powershell
cd aspire
aspire run
```

Aspire prints a dashboard URL (defaults to `https://localhost:17123` or a
nearby port). Open it to:

* Watch each resource go from `Starting` → `Running` → `Healthy`
* Stream logs per resource
* Inspect environment variables (secrets appear masked)
* Trigger start/stop/restart on individual resources

**Stop everything:**

```powershell
# Ctrl+C in the aspire run terminal, OR from another terminal:
aspire stop
```

**Run in the background** (returns the shell prompt):

```powershell
aspire start
aspire ps     # list running AppHosts
aspire stop   # tears them down
```

---

## 🧪 Smoke Checks

Once all resources are `Running`, confirm the stack with these probes:

```powershell
# Gateway health
curl http://localhost:8125/perfpilot-mcp-gateway/health

# A2A health
curl http://localhost:8101/health

# AG-UI health
curl http://localhost:8102/health

# PerfMemory via gateway
curl http://localhost:8125/perfpilot-mcp-perfmemory/health

# Playwright MCP (direct)
curl http://localhost:8117/mcp
```

Open the frontend at the port shown in the Aspire dashboard (base path
`/perfpilot-ui`).

---

## 🧰 Troubleshooting

### Aspire dashboard won't start / HTTPS errors

Run the one-time cert setup:

```powershell
aspire certs trust
```

### Secrets not injected (resources crash on first call)

List what's actually stored:

```powershell
dotnet user-secrets list --id perfpilot-aspire-apphost
```

If a required `Parameters:<name>` is missing or shows an obviously placeholder
value, set it with the matching `dotnet user-secrets set` command above and
restart the AppHost.

### Postgres bind mount is empty / Apache AGE missing

The PostgreSQL image is a *custom* build (pgvector + AGE). The first run
takes several minutes because the image compiles AGE from source. Watch logs
in the dashboard — the `perfmem-pgvector-age` resource shows `Healthy` only
after the init SQL under `docker/postgresql/init/` runs successfully.

For pgvector/AGE install reference material, see:

* `docs/database/pgvector_installation_guide.md`
* `docs/database/apache_age_installation_guide.md`

### Port already in use

Default ports: `5432, 8101, 8102, 8110-8118, 8125`. Stop any existing
`docker compose` stack or other services holding those ports before
`aspire run`.

### Docker Desktop not running

Aspire fails fast with a clear message. Start Docker Desktop and re-run.

### Playwright certificate errors

If browser automation hangs or fails on first HTTPS request, confirm the
`playwright-cert-passphrase` and `playwright-cert-auto-select-cn` secrets
are set and that the certificate files referenced by the Playwright MCP
container are mounted at the paths expected by `docker/playwright-mcp/`.

---

## ☁️ Production Deployment

Locally: `dotnet user-secrets`. In the cloud: **HashiCorp Vault** is the
source of record for every secret. The same `Parameters:<name>` keys
declared in `apphost.cs` are populated as environment variables inside each
container at startup — the AppHost code and resource definitions do not
change between environments; only the source of configuration does.

No `.env` files are involved in either environment.

---

## 📚 Related Reading

* [`.cursor/rules/project-and-coding-guidelines.mdc`](../../.cursor/rules/project-and-coding-guidelines.mdc) — Security & secrets guardrail
* [`docs/README.md`](../README.md) — Full documentation index
* [`docs/agent-framework/a2a_new_jmx_pipeline_guide.md`](../agent-framework/a2a_new_jmx_pipeline_guide.md) — Upstream A2A integration reference
* [Aspire docs (aspire.dev)](https://aspire.dev) — Official product documentation (also browsable via `aspire docs list` / `aspire docs search`)
