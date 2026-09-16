# 📦 PerfPilot Hub — Docker deployment guide

This folder contains everything needed to run PerfPilot Hub in containers on
your local machine. Each MCP server ships as its own independent Docker image,
each sub-folder ships its own `Dockerfile` + `docker-compose.yml` + `.env.example`,
and two top-level compose files (`docker-compose-full-{mac,windows}.yaml`) bring
the whole stack up together.

> 📌 **Looking for the pre-refactor monolithic Docker setup?**
>
> The Phase 1 refactor (per-MCP images, baked-in configs, `DEPLOYMENT_MODE`
> toggle) replaces the earlier monolithic gateway container. If you prefer the
> older single-image layout — or you need a stable checkpoint to compare
> against — use the **[v1.1.0 release](https://github.com/canyonlabz/perfpilot-hub/releases/tag/v1.1.0)**
> (FastMCP 3.4.x, last stable before the Docker refactor):
>
> ```bash
> git clone --branch v1.1.0 https://github.com/canyonlabz/perfpilot-hub.git
> ```
>
> All releases live at
> [github.com/canyonlabz/perfpilot-hub/releases](https://github.com/canyonlabz/perfpilot-hub/releases).

---

## 🚀 1. Overview

PerfPilot Hub is a collection of Model Context Protocol (MCP) servers plus an
AI-agent framework that automates performance-testing workflows (JMeter runs,
BlazeMeter cloud tests, Datadog observability, PerfAnalysis, PerfReport,
Confluence publishing, and a PostgreSQL-backed memory layer).

**Design principles applied throughout `docker/`:**

- **Per-MCP images.** No monolithic image. Each MCP (`blazemeter`, `datadog`,
  `jmeter`, `perfanalysis`, `perfreport`, `confluence`, `perfmemory`, `github`,
  `playwright`) plus the gateway, agent backend, and UI has its own Dockerfile.
- **Baked-in configs.** All YAML / JSON configs are `COPY`'d into each image at
  build time. There are **zero** `./config/*:ro` bind mounts in any compose
  file — configs never travel over a bind mount at runtime.
- **`DEPLOYMENT_MODE` toggle.** A single env var flips human-readable console
  logs (`local`) into OTel-shaped JSON to stdout (`cloud`). Same image, both
  modes. No dual Dockerfiles.
- **Independence Contract.** Each Dockerfile builds from its own sub-folder
  plus the MCP source tree. Only three tightly-scoped cross-MCP config `COPY`s
  are allowed, all documented in §13.

Local development runs Docker Compose from this folder. Cloud deployment (Azure /
Aspire) is a Phase 2 concern — same images, different orchestration.

---

## 📋 2. Prerequisites

New to Docker? Here's the shortest path to a working stack.

### 2.1 A Docker engine

Pick one — both are confirmed working with this repo:

| Engine | Notes |
|---|---|
| [🐳 Docker Desktop](https://www.docker.com/products/docker-desktop/) | Original, most common. Free for personal / small-business use; check licensing for enterprise. |
| [🐄 Rancher Desktop](https://rancherdesktop.io/) | Open-source alternative, no licensing fees. Uses `containerd` or `dockerd` under the hood. Same `docker` and `docker compose` commands. |

Both must ship **Docker Engine 20.10+** and **Docker Compose v2** (the `docker
compose` command, not the older `docker-compose` script).

**Verify after install:**

```bash
docker --version              # → Docker version 24.x or newer
docker compose version        # → Docker Compose version v2.x.x
docker info                   # → succeeds without errors
```

### 2.2 System resources

The full stack runs **14 containers**. Recommended minimums for a smooth
experience:

- **CPU**: 4 cores (2 minimum, but startup will be slow)
- **RAM**: 8 GB assigned to the Docker VM (6 GB minimum). Set in Docker
  Desktop → Settings → Resources, or Rancher Desktop → Preferences → Virtual Machine.
- **Disk**: ~15 GB free after images build. The JMeter image alone is ~1.5 GB
  (Java + JMeter + plugins).

### 2.3 Operating system

| OS | Status | Notes |
|---|---|---|
| 🪟 Windows 10/11 | ✅ Confirmed | Use `docker-compose-full-windows.yaml`. Docker Desktop with the WSL2 backend is the standard setup. |
| 🍎 macOS 13+ (Intel or Apple Silicon) | ✅ Confirmed | Use `docker-compose-full-mac.yaml`. The Mac file sets `user: "999:999"` and `PGDATA` on the database to work around a Docker Desktop VirtioFS quirk (§9.2). |
| 🐧 Linux | ⚠️ Untested | Should work with the Windows compose file, but has not been validated against this repo. |

### 2.4 Git

You'll need `git` to clone this repo. On Windows install [Git for Windows](https://git-scm.com/download/win);
on macOS `xcode-select --install` or `brew install git`.

### 2.5 What you should have ready before starting

Not everything is required for every workflow, but if you plan to bring the
full stack up you'll want most of these:

- 🔑 **BlazeMeter** API key + secret + account ID + workspace ID
- 🔑 **Datadog** API key + application key
- 🔑 **Confluence** — either a Cloud API token or an on-prem PAT
- 🔑 **OpenAI** or **Azure OpenAI** or **Ollama** access (used by both the agent
  chat model and the PerfMemory embedding model)
- 🔑 **GitHub** personal access token
- 🔒 **PostgreSQL** password (you pick this — used for the local `perfmem-pgvector-age`
  container)
- 🏢 *(optional)* **Corporate CA bundle** (PEM) if your network runs an
  HTTPS-intercepting proxy (Zscaler, Norton 360, BlueCoat, etc.). See §9.

Every secret goes into `docker/.env` (copied from `docker/.env.example`).
Nothing is baked into images.

---

## 🏷️ 3. Canonical naming convention

Every container, image, and compose service uses one of the following
canonical names. There is no legacy `perf-*` or `perfmemory-db` after Phase 1.

| Component | Image / Container name | Sub-folder |
|---|---|---|
| MCP — BlazeMeter | `perfpilot-mcp-blazemeter` | `docker/blazemeter-mcp/` |
| MCP — Datadog | `perfpilot-mcp-datadog` | `docker/datadog-mcp/` |
| MCP — JMeter | `perfpilot-mcp-jmeter` | `docker/jmeter-mcp/` |
| MCP — PerfAnalysis | `perfpilot-mcp-perfanalysis` | `docker/perfanalysis-mcp/` |
| MCP — PerfReport | `perfpilot-mcp-perfreport` | `docker/perfreport-mcp/` |
| MCP — Confluence | `perfpilot-mcp-confluence` | `docker/confluence-mcp/` |
| MCP — PerfMemory | `perfpilot-mcp-perfmemory` | `docker/perfmemory-mcp/` |
| MCP — GitHub | `perfpilot-mcp-github` | `docker/github-mcp/` |
| MCP — Playwright *(vendor)* | `perfpilot-mcp-playwright` | `docker/playwright-mcp/` |
| Gateway (aggregator) | `perfpilot-mcp-gateway` | `docker/gateway-mcp/` |
| Agent backend — A2A | `perfpilot-a2a` | `docker/agent-backend/` |
| Agent backend — AG-UI | `perfpilot-agui` | `docker/agent-backend/` *(same Dockerfile, two image tags)* |
| Frontend | `perfpilot-ui` | `docker/agent-frontend/` |
| Database | `perfmem-pgvector-age` | `docker/postgresql/` |

Compose service names match image names, so `docker compose ps` output and
image tags always align.

---

## 🌐 4. Port allocation

MCPs are grouped in the `81xx` range for easy firewall rules. Agent backend on
`810x`, UI on `8080`, database on the standard `5432`.

| Service | Port | Endpoint path |
|---|---|---|
| `perfmem-pgvector-age` | `5432` | (raw postgres protocol) |
| `perfpilot-ui` | `8080` | `/` |
| `perfpilot-a2a` | `8101` | `/` (FastAPI + SSE) |
| `perfpilot-agui` | `8102` | `/` (FastAPI + CopilotKit bridge) |
| `perfpilot-mcp-blazemeter` | `8110` | `/perfpilot-mcp-blazemeter/mcp` |
| `perfpilot-mcp-datadog` | `8111` | `/perfpilot-mcp-datadog/mcp` |
| `perfpilot-mcp-jmeter` | `8112` | `/perfpilot-mcp-jmeter/mcp` |
| `perfpilot-mcp-perfanalysis` | `8113` | `/perfpilot-mcp-perfanalysis/mcp` |
| `perfpilot-mcp-perfreport` | `8114` | `/perfpilot-mcp-perfreport/mcp` |
| `perfpilot-mcp-confluence` | `8115` | `/perfpilot-mcp-confluence/mcp` |
| `perfpilot-mcp-perfmemory` | `8116` | `/perfpilot-mcp-perfmemory/mcp` |
| `perfpilot-mcp-playwright` | `8117` | `/mcp` *(vendor image, no prefix)* |
| `perfpilot-mcp-github` | `8118` | `/perfpilot-mcp-github/mcp` |
| `perfpilot-mcp-gateway` | `8125` | `/perfpilot-mcp-gateway/mcp` *(aggregates the 8 MCPs above)* |

Cursor / Claude Desktop / any MCP client should point at
`http://localhost:8125/perfpilot-mcp-gateway/mcp` — the gateway exposes every
mounted MCP under its own namespace (`jmeter.*`, `blazemeter.*`, etc.).

---

## 📁 5. Directory layout

```
docker/
├── README.md                              (this file)
├── docker-compose-full-mac.yaml           (macOS full-stack: user + PGDATA workarounds)
├── docker-compose-full-windows.yaml       (Windows/WSL2 full-stack)
├── .env.example                           (union of all secrets; copy to .env)
│
├── certs/                                 (all optional; empty by default)
│   ├── corporate/                         (CA PEM bundles for HTTPS-intercepting proxy)
│   ├── jmeter/                            (JMeter-only: .jks client keystores)
│   └── playwright/                        (Playwright-only: .p12 / .pem test-user certs)
│
├── data/                                  (gitignored; local postgres data lives here)
│   └── pgvectordb/                        (bind-mounted into perfmem-pgvector-age)
│
├── postgresql/                            (perfmem-pgvector-age image)
├── gateway-mcp/                           (perfpilot-mcp-gateway image)
├── playwright-mcp/                        (perfpilot-mcp-playwright image, vendor)
├── agent-backend/                         (perfpilot-a2a + perfpilot-agui images)
├── agent-frontend/                        (perfpilot-ui image)
│
├── blazemeter-mcp/                        (perfpilot-mcp-blazemeter image)
├── datadog-mcp/                           (perfpilot-mcp-datadog image)
├── jmeter-mcp/                            (perfpilot-mcp-jmeter image)
├── perfanalysis-mcp/                      (perfpilot-mcp-perfanalysis image)
├── perfreport-mcp/                        (perfpilot-mcp-perfreport image)
├── confluence-mcp/                        (perfpilot-mcp-confluence image)
├── perfmemory-mcp/                        (perfpilot-mcp-perfmemory image)
└── github-mcp/                            (perfpilot-mcp-github image)
```

Every sub-folder contains at minimum a `Dockerfile`, a `docker-compose.yml`,
and a `.env.example`. Most also contain a `config/` folder (baked into the
image) and a `README.md`.

---

## 🔀 6. Endpoint routing and `MCP_HTTP_PREFIX`

Each FastMCP-based MCP reads three env vars at startup:

- `MCP_TRANSPORT=http` — always `http` in Docker (stdio is the local-dev default)
- `HTTP_PORT=<port>` — the listen port (see §4)
- `MCP_HTTP_PREFIX=/perfpilot-mcp-<name>` — the URL path prefix

And it calls:

```python
mcp.run(
    transport="http",
    host="0.0.0.0",
    port=int(os.environ["HTTP_PORT"]),
    path=os.environ["MCP_HTTP_PREFIX"] + "/mcp",
)
```

Endpoint = `http://<host>:<port>/perfpilot-mcp-<name>/mcp`.

**Playwright is the sole exception.** It wraps Microsoft's vendor image which
does not accept a path prefix — its endpoint is `/mcp` only. Agents call it
directly at `http://perfpilot-mcp-playwright:8117/mcp`, bypassing the gateway.

**The gateway dials each mounted MCP** via `MCP_URL_<NAME>` env vars, which
default to the compose-network DNS values baked into `docker/gateway-mcp/config/config.yaml`:

```
MCP_URL_BLAZEMETER   → http://perfpilot-mcp-blazemeter:8110/perfpilot-mcp-blazemeter/mcp
MCP_URL_DATADOG      → http://perfpilot-mcp-datadog:8111/perfpilot-mcp-datadog/mcp
... etc.
```

Override any of them in `docker/.env` for split-mode / debugging setups.

---

## ⚙️ 7. `DEPLOYMENT_MODE` contract

A single env var switches log format and secret source. **Same image, both
modes.**

### 7.1 `DEPLOYMENT_MODE=local`

- Human-readable console output via structlog's `ConsoleRenderer`.
- Secrets read from `docker/.env` (copied from `docker/.env.example`).
- Used for local development and manual smoke-testing.

Example log line:

```
2026-09-10 23:22:00 [info     ] connected-to-blazemeter        workspace_id=12345
```

### 7.2 `DEPLOYMENT_MODE=cloud`

- OTel-shaped JSON emitted to stdout (fields per OpenTelemetry Logs Semantic
  Conventions: `timestamp`, `severity_text`, `severity_number`, `body`,
  `trace_id`, `span_id`, `attributes`, `resource.service.name`).
- Secrets read from **injected env vars** (Vault / Azure Key Vault / Aspire user-secrets).
  No `.env` file is loaded in cloud mode.
- Used by the Phase 2 Aspire deployment on Azure.

Example log line:

```json
{"timestamp":"2026-09-10T23:22:00.123Z","severity_text":"INFO","severity_number":9,"body":"connected-to-blazemeter","trace_id":"","span_id":"","attributes":{},"resource.service.name":"perfpilot-mcp-blazemeter","workspace_id":12345}
```

---

## 🧩 8. Standalone (per-component) usage

Every sub-folder ships its own `docker-compose.yml` and `.env.example` for
isolated testing. The pattern is always the same:

```bash
cd docker/<component>/
cp .env.example .env         # then edit .env with real values
docker compose up --build -d
```

- Windows PowerShell: `Copy-Item .env.example .env` instead of `cp`.
- The build context is `../..` (repo root), so the compose file can `COPY`
  from both `docker/<component>/` and `mcp-perf-suite/<mcp>/`.

**Standalone gateway** — brings up `perfpilot-mcp-gateway` alone. The 8 MCP
URLs default to `host.docker.internal` in the standalone `.env.example`, so
you can bring up each MCP in its own compose and dial them from the gateway
container without shared networking.

Each sub-folder README (see §14) documents its own healthcheck endpoint,
required secrets, and any component-specific quirks (JMeter's JKS mount,
Playwright's `--config` flags, etc.).

---

## 🏗️ 9. Full-stack usage

Two OS-specific compose files bring the entire 14-container stack up at once.

### 9.1 Windows (Docker Desktop / WSL2)

```powershell
cd docker\
Copy-Item .env.example .env
# Edit .env with your secrets
docker compose -f docker-compose-full-windows.yaml up --build
```

### 9.2 macOS (Docker Desktop / VirtioFS)

```bash
cd docker/
cp .env.example .env
# Edit .env with your secrets
docker compose -f docker-compose-full-mac.yaml up --build
```

The Mac file adds two settings to `perfmem-pgvector-age` that the Windows file
does not need:

- `user: "999:999"` — forces the container process to run as UID 999 (the image's
  postgres user), so it can chown the bind-mounted data folder.
- `PGDATA: /var/lib/postgresql/18/docker/pgdata` — points `initdb` at a
  sub-directory inside the bind mount, so it can create + chown its own folder
  instead of the mount root (which Docker Desktop for Mac's VirtioFS won't
  allow).

Without these two settings, PostgreSQL will fail to initialize on macOS.

### 9.3 Startup and healthchecks

- Every image ships a Dockerfile `HEALTHCHECK` that curls its own MCP endpoint
  (or `pg_isready` for postgres).
- Gateway `depends_on` all 8 mounted MCPs with `condition: service_healthy`,
  so it does not start until every backing MCP responds to a probe.
- Agents (`perfpilot-a2a`, `perfpilot-agui`) `depends_on` the gateway,
  Playwright, and the database — all `service_healthy`.
- UI (`perfpilot-ui`) `depends_on` the agent backend.

Full-stack cold-start typically takes 60–90 seconds. Use `docker compose ps`
to watch health status; use `docker compose logs -f <service>` to trace a
specific container.

### 9.4 Tearing down

```bash
docker compose -f docker-compose-full-<os>.yaml down
```

Postgres data survives because it lives on the host bind mount
(`docker/data/pgvectordb/`). To reset the database entirely, add `-v` (removes
volumes) and delete `docker/data/pgvectordb/` from the host.

---

## 🔐 10. Corporate CA / HTTPS-intercepting proxy

If your machine runs a TLS-intercepting proxy (Zscaler, Norton 360, BlueCoat)
or you're inside a corporate network with a private CA, every image needs the
corporate CA installed into its trust store.

**Enable with two steps:**

1. Place your CA bundle (PEM format, may be a concatenation of multiple certs)
   at `docker/certs/corporate/ca-bundle.pem`.
2. Set `ENABLE_CORP_CA=true` in `docker/.env`.
3. Rebuild every image: `docker compose ... up --build`.

Every Python image installs the CA into:

- The OS trust store (`update-ca-certificates`).
- `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` env vars.

The **Playwright image** additionally installs the CA into:

- The Chromium NSS database (`certutil`).
- Node.js's `NODE_EXTRA_CA_CERTS`.
- Adds `--ignore-https-errors` as a belt-and-suspenders fallback.

The **JMeter image** additionally imports the CA into Java's `cacerts`
keystore (via `keytool`).

Nothing about `ENABLE_CORP_CA=false` (the default) installs any corporate cert
— you can safely ignore this whole section if you're not behind a proxy.

---

## 📜 11. Cert drop points

Three sub-folders under `docker/certs/` are recognized. Every image sees only
the cert store(s) it needs:

| Folder | Consumed by | File types | Purpose |
|---|---|---|---|
| `docker/certs/corporate/` | Every image *(when `ENABLE_CORP_CA=true`)* | `*.pem` | Corporate CA bundles for HTTPS-intercepting proxies. |
| `docker/certs/jmeter/` | `perfpilot-mcp-jmeter` only | `*.jks` | JMeter TLS client keystores (JKS format). File name + password go into `.env` via `JMETER_JKS_FILE` / `JMETER_JKS_PWD`. |
| `docker/certs/playwright/` | `perfpilot-mcp-playwright` only | `*.p12`, `*.pem` | Test-user browser digital certs. CN filter + passphrase go into `.env` via `PLAYWRIGHT_CERT_AUTO_SELECT_CN` / `PLAYWRIGHT_CERT_PASSPHRASE`. |

All three are optional at build time — the folders are committed as empty
directories (each has a `.gitkeep`). JMeter does not read `.p12`/`.pem`;
Playwright does not read `.jks`. Corporate CA is unrelated to either
authentication store.

---

## 🔄 12. Legacy → canonical migration

Anyone who cloned or forked this repo before Phase 1's Docker restructure will
find their local workflows broken until they update to the new names. Here's
the mapping:

| Old (pre-Phase 1) | New (Phase 1+) |
|---|---|
| `perf-gateway:8888` (monolithic image) | `perfpilot-mcp-gateway:8125` (aggregator) + 8 per-MCP images (`8110`–`8118`) |
| `perfmemory-db:5432` (compose service) | `perfmem-pgvector-age:5432` |
| `playwright-mcp:8931` | `perfpilot-mcp-playwright:8117` |
| `agent-a2a:8001` | `perfpilot-a2a:8101` |
| `agent-agui:8002` | `perfpilot-agui:8102` |
| `frontend:3000` | `perfpilot-ui:8080` |
| `docker/.env.gateway` (operator file) | `docker/.env` |
| `docker/.env.gateway.example` | `docker/.env.example` *(union of all secrets)* |
| `docker/Dockerfile.gateway.example` (monolithic) | `docker/<mcp>/Dockerfile` × 9 |
| `docker/entrypoint.sh` (shared) | `docker/<mcp>/entrypoint.sh` × per-MCP |
| `docker/config/<mcp>/config.yaml` (bind-mounted) | `docker/<mcp>/config/config.yaml` *(baked into image)* |
| MCP endpoint `/mcp` (single path) | `/perfpilot-mcp-<name>/mcp` *(namespaced per MCP)* |

Cursor / MCP clients: update the server URL from
`http://localhost:8888/mcp` → `http://localhost:8125/perfpilot-mcp-gateway/mcp`.

---

## 🤝 13. Independence Contract

Every Dockerfile builds from **its own sub-folder** plus the MCP source tree
(`mcp-perf-suite/<mcp>/` or `agent-framework/`) plus the optional cert drop
points. There are exactly **three exceptions** — cross-MCP config file `COPY`s
that must land at a specific sibling path inside the consumer image:

| Consumer image | Shared file | Destination inside image |
|---|---|---|
| `perfpilot-mcp-perfanalysis` | `docker/datadog-mcp/config/environments.json` | `/app/datadog-mcp/environments.json` |
| `perfpilot-mcp-perfreport` | `docker/datadog-mcp/config/environments.json` | `/app/datadog-mcp/environments.json` |
| `perfpilot-mcp-confluence` | `docker/perfreport-mcp/config/chart_schema.yaml` | `/app/perfreport-mcp/chart_schema.yaml` |

These three are the **only** permitted cross-folder `COPY`s. Everything else
must live inside the sub-folder that owns the Dockerfile.

**Manual checklist** when adding or changing a Dockerfile in this folder:

- No `./config/*:ro` bind mounts anywhere in the compose file.
- Every `docker/<mcp>/config/` folder has a corresponding `COPY docker/<mcp>/config/...`
  line in its sibling Dockerfile (configs are baked, not mounted).
- The `utils/logging_config.py` module emits console output when
  `DEPLOYMENT_MODE=local` and OTel-shaped JSON when `DEPLOYMENT_MODE=cloud`.
- Any cross-folder `COPY` must appear in the three-row table above; otherwise
  it violates the contract.

---

## 📚 14. Related documentation

- Per-component READMEs live inside each sub-folder — start there for image
  size, secrets required, healthcheck endpoints, and any quirks specific to
  that MCP:
    - [`postgresql/README.md`](postgresql/README.md)
    - [`gateway-mcp/README.md`](gateway-mcp/README.md)
    - [`playwright-mcp/README.md`](playwright-mcp/README.md)
    - [`agent-backend/README.md`](agent-backend/README.md)
    - [`agent-frontend/README.md`](agent-frontend/README.md)
    - [`blazemeter-mcp/README.md`](blazemeter-mcp/README.md)
    - [`datadog-mcp/README.md`](datadog-mcp/README.md)
    - [`jmeter-mcp/README.md`](jmeter-mcp/README.md)
    - [`perfanalysis-mcp/README.md`](perfanalysis-mcp/README.md)
    - [`perfreport-mcp/README.md`](perfreport-mcp/README.md)
    - [`confluence-mcp/README.md`](confluence-mcp/README.md)
    - [`perfmemory-mcp/README.md`](perfmemory-mcp/README.md)
    - [`github-mcp/README.md`](github-mcp/README.md)
- **Repository-level documentation**:
    - [Repository root README](../README.md) — overall project overview.
    - [`mcp-perf-suite/README.md`](../mcp-perf-suite/README.md) — MCP source tree.
    - [`agent-framework/README.md`](../agent-framework/README.md) — agent backend + UI source tree.
