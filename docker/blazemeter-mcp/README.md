# 🚀 perfpilot-mcp-blazemeter

BlazeMeter MCP server — drive distributed cloud load tests on the BlazeMeter
platform and pull their results back into your performance-analysis workflow.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-blazemeter:latest` |
| 📦 Container | `perfpilot-mcp-blazemeter` |
| 🌐 Port | `8110` |
| 🔗 Endpoint | `http://localhost:8110/perfpilot-mcp-blazemeter/mcp` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/blazemeter-mcp/`](../../mcp-perf-suite/blazemeter-mcp/) |

---

## 🎯 Capabilities

The BlazeMeter MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) drive an end-to-end cloud load-test
lifecycle. Core capabilities include:

- 🔍 **Discover** your accounts, workspaces, projects, and test definitions
- 🚀 **Trigger** tests, master sessions, and multi-test collections
- 📊 **Monitor** live progress and pull real-time metrics
- 📥 **Download** artifacts (CSVs, logs, HAR files) into the shared
  `mcp-perf-suite/artifacts/` folder
- 🔄 **Feed** results into PerfAnalysis / PerfReport downstream

For the complete tool inventory, see the
[`blazemeter-mcp` source folder](../../mcp-perf-suite/blazemeter-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template
cd docker/blazemeter-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env with your BlazeMeter credentials (see next section)

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#              NAME                     STATUS
# perfpilot-mcp-blazemeter     Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8110/perfpilot-mcp-blazemeter/mcp
# A 200 / 406 / streaming response indicates the server is alive. MCP is a
# stateful protocol, so a bare curl does not perform a full handshake — use
# an MCP client (Cursor, Claude Desktop, etc.) for functional validation.
```

Shut down the container when finished:

```bash
docker compose down
```

---

## 🔑 Required credentials

A **BlazeMeter account** with API access is required. Obtain API credentials
from the BlazeMeter UI: **Settings → API Keys**.

| Variable | Purpose |
|---|---|
| `BLAZEMETER_API_KEY` | API key (public identifier) |
| `BLAZEMETER_API_SECRET` | API secret *(sensitive — must not be shared or committed)* |
| `BLAZEMETER_ACCOUNT_ID` | Numeric account ID (from the URL or account settings) |
| `BLAZEMETER_WORKSPACE_ID` | Numeric workspace ID (from the URL or workspace settings) |

Add them to `docker/blazemeter-mcp/.env` (created from `.env.example`).

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with empty values) is tracked in the repository.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `BLAZEMETER_API_KEY` | *(required)* | See above |
| `BLAZEMETER_API_SECRET` | *(required)* | See above |
| `BLAZEMETER_ACCOUNT_ID` | *(required)* | See above |
| `BLAZEMETER_WORKSPACE_ID` | *(required)* | See above |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Where downloaded test artifacts (CSVs, logs, HARs) land |

The `artifacts/` folder is shared across every PerfPilot MCP, so downloads
from BlazeMeter end up in the same tree that PerfAnalysis and PerfReport read
from downstream.

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its own endpoint
every 30 seconds. The first probe fires after a 20-second grace period (image
cold start).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-blazemeter | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/blazemeter-mcp/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile automatically installs the CA into the OS trust store and points
`SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` at it. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container restart loop with `KeyError` on a `BLAZEMETER_*` variable | `.env` missing or credentials not filled in | Verify `docker/blazemeter-mcp/.env` exists and has all 4 `BLAZEMETER_*` values populated |
| Build fails with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-blazemeter/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |
| Tools return `401 Unauthorized` | API credentials are wrong or the workspace ID doesn't match your account | Double-check the values in `.env` against your BlazeMeter UI |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-blazemeter
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, cert drop points)
- 📂 [`mcp-perf-suite/blazemeter-mcp/`](../../mcp-perf-suite/blazemeter-mcp/) — MCP source code + tool inventory
- 🌐 [BlazeMeter API documentation](https://api.blazemeter.com/api-docs/) — official API reference
