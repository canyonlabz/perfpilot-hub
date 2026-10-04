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
```

The user-secrets store lives **outside the repo** and is per-user, per-OS.
Never commit the file, but it's useful to know where it is when debugging:

| OS | Location of `secrets.json` |
|---|---|
| **Windows** | `C:\Users\<username>\AppData\Roaming\Microsoft\UserSecrets\perfpilot-aspire-apphost\secrets.json` |
| **macOS / Linux** | `~/.microsoft/usersecrets/perfpilot-aspire-apphost/secrets.json` |

The folder is created on the first `dotnet user-secrets set ...` call —
you do not need to pre-create it. The `UserSecretsId` segment of the path
(`perfpilot-aspire-apphost`) comes from the `#:property UserSecretsId=...`
directive at the top of `aspire/apphost.cs`.

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

### PostgreSQL data layout check (PG18+ bind-mount convention)

The AppHost (and all three compose files) mount `docker/data/pgvectordb/`
into the container and point `PGDATA` at the `pgdata/` subdirectory inside
that mount:

```text
host:     docker/data/pgvectordb/pgdata/<cluster files>
container: /var/lib/postgresql/18/docker/pgdata/<cluster files>
                                        ↑
                            PGDATA points here
```

This is the PG18+ version-specific layout and is required so `initdb` has
an empty target without touching anything at the mount root.

**On a healthy install**, these two checks must both hold:

```powershell
Test-Path .\docker\data\pgvectordb\pgdata\PG_VERSION   # must return True
Test-Path .\docker\data\pgvectordb\PG_VERSION          # must return False
```

> **Why this matters.** PG 17 and earlier used a flat layout — cluster
> files sat directly at the Postgres data directory with no `pgdata/`
> subdirectory. If `PG_VERSION` ever appears at the **mount root** instead
> of inside `pgdata/`, it means Postgres was previously run with its
> `PGDATA` pointed at the mount root (either explicitly, or by inheriting
> a pre-PG18 image default). Starting a PG18+ container against that
> layout with `PGDATA=/var/lib/postgresql/18/docker/pgdata` will cause
> `initdb` to run against the empty `pgdata/` subdirectory, leaving the
> existing cluster **orphaned** (not deleted — the files are still at the
> mount root, just one level up from where the new container is looking).
>
> If you catch this before writing new data: stop the container, back up
> or `pg_dump` the orphan files from `docker/data/pgvectordb/`, then move
> them (or dump+restore) into `docker/data/pgvectordb/pgdata/` so the new
> layout picks them up.

### Web UI shows empty conversation history after a rebuild

The current Web UI identifies users by an **anonymous browser cookie**
(authentication is not implemented yet). Agent-framework tables such as
`agent_threads`, `agent_sessions`, and `conversation_messages` are scoped
by that identity, so history that belongs to a previous browser profile
or an earlier browser session will not appear in a new one — even though
the data is still in the database.

**How to confirm this is an identity mismatch, not data loss:**

1. In Edge / Chrome DevTools → Application → Cookies for the UI origin,
   note the value of `perfpilot_token`. This maps 1-to-1 to `user_id` in
   `agent_sessions` and `agent_threads`.
2. Connect to the `perfagent_state` database (`psql -h localhost -U <user>
   -d perfagent_state`) and run:

   ```sql
   SELECT user_id, COUNT(*) AS threads
   FROM agent_threads
   WHERE status = 'active'
   GROUP BY user_id
   ORDER BY threads DESC;
   ```

3. If an older `user_id` has your historical threads and the UI is
   currently showing a different `user_id`, your data is intact — the UI
   is just filtering by the newer identity.

To view the historical threads, copy the older `perfpilot_token` value
back into the browser cookie for the UI origin. This is a developer-only
workaround and will become unnecessary once real authentication lands.

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
