# 🐕 perfpilot-mcp-datadog

Datadog MCP server — query host, Kubernetes, and application observability
data from Datadog and feed it into your performance-analysis workflow.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-datadog:latest` |
| 📦 Container | `perfpilot-mcp-datadog` |
| 🌐 Port | `8111` |
| 🔗 Endpoint | `http://localhost:8111/perfpilot-mcp-datadog/mcp` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/datadog-mcp/`](../../mcp-perf-suite/datadog-mcp/) |

---

## 🎯 Capabilities

The Datadog MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) pull observability data into
performance-analysis workflows. Core capabilities include:

- 🖥️ **Host metrics** — CPU, memory, disk, network for individual hosts
- ☸️ **Kubernetes metrics** — pod, node, container-level resource usage
- 🕸️ **APM traces** — service traces, spans, and latency percentiles
- 📜 **Log queries** — filter, aggregate, and correlate application logs
- 📈 **Custom timeseries** — arbitrary Datadog metric queries defined in `custom_queries.json`
- 🌍 **Environment awareness** — pre-defined host / k8s / service groupings per environment (`environments.json`)

For the complete tool inventory, see the
[`datadog-mcp` source folder](../../mcp-perf-suite/datadog-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template
cd docker/datadog-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env with your Datadog credentials (see next section)

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#             NAME                    STATUS
# perfpilot-mcp-datadog       Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8111/perfpilot-mcp-datadog/mcp
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

A **Datadog account** with API and Application key access is required.
Obtain credentials from the Datadog UI: **Organization Settings → API Keys**
and **Application Keys**.

| Variable | Purpose |
|---|---|
| `DD_API_KEY` | Datadog API key *(sensitive — must not be shared or committed)* |
| `DD_APP_KEY` | Datadog Application key *(sensitive — must not be shared or committed)* |
| `DD_API_BASE_URL` | Regional API base URL (default: `https://api.datadoghq.com`) |

Add them to `docker/datadog-mcp/.env` (created from `.env.example`).

> 🌍 **Datadog region matters.** If your organization is on the EU site
> (`datadoghq.eu`), the US3 site (`us3.datadoghq.com`), or the US5 site
> (`us5.datadoghq.com`), set `DD_API_BASE_URL` accordingly. Using the wrong
> region returns `403 Forbidden` on every call.

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with empty values) is tracked in the repository.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `DD_API_KEY` | *(required)* | See above |
| `DD_APP_KEY` | *(required)* | See above |
| `DD_API_BASE_URL` | `https://api.datadoghq.com` | Regional Datadog API endpoint (see security note above) |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Where downloaded metrics, logs, and APM exports land |

The `artifacts/` folder is shared across every PerfPilot MCP, so Datadog
exports end up in the same tree that PerfAnalysis and PerfReport read from
downstream.

---

## 🗂️ Baked-in configuration

The Datadog MCP ships three configuration files baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/datadog-mcp/config/config.yaml` | `/app/datadog-mcp/config.yaml` | Pagination limits, retry policy |
| `docker/datadog-mcp/config/environments.json` | `/app/datadog-mcp/environments.json` | Host / k8s / service definitions per environment |
| `docker/datadog-mcp/config/custom_queries.json` | `/app/datadog-mcp/custom_queries.json` | Reusable log, APM, and metric queries |

Edit these files in the repo and rebuild the image to take effect. The
`environments.json` and `custom_queries.json` files are the primary place
you'll customize this MCP for your infrastructure.

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its own endpoint
every 30 seconds. The first probe fires after a 20-second grace period (image
cold start).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-datadog | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/datadog-mcp/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile automatically installs the CA into the OS trust store and points
`SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` at it. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container restart loop with `KeyError` on a `DD_*` variable | `.env` missing or credentials not filled in | Verify `docker/datadog-mcp/.env` exists and both `DD_API_KEY` and `DD_APP_KEY` are populated |
| Tools return `403 Forbidden` | Wrong Datadog region (`DD_API_BASE_URL`) | Confirm your Datadog site in the UI and update `DD_API_BASE_URL` accordingly |
| Tools return `401 Unauthorized` | Invalid API key or Application key | Regenerate keys in the Datadog UI and update `.env` |
| Build fails with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-datadog/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-datadog
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, cert drop points)
- 📂 [`mcp-perf-suite/datadog-mcp/`](../../mcp-perf-suite/datadog-mcp/) — MCP source code + tool inventory
- 🌐 [Datadog API documentation](https://docs.datadoghq.com/api/latest/) — official API reference
- 🌍 [Datadog site list](https://docs.datadoghq.com/getting_started/site/) — region and `DD_API_BASE_URL` reference
