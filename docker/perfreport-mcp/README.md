# 📝 perfpilot-mcp-perfreport

PerfReport MCP server — generate publication-ready performance-test reports
from PerfAnalysis outputs, including narrative sections, charts, tables, and
optional Confluence publishing.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-perfreport:latest` |
| 📦 Container | `perfpilot-mcp-perfreport` |
| 🌐 Port | `8114` |
| 🔗 Endpoint | `http://localhost:8114/perfpilot-mcp-perfreport/mcp` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/perfreport-mcp/`](../../mcp-perf-suite/perfreport-mcp/) |

---

## 🎯 Capabilities

The PerfReport MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) turn PerfAnalysis outputs into
polished, human-readable reports. Core capabilities include:

- 📄 **Report generation** — Markdown reports with executive summary, key observations, SLA verdicts, and issue tables
- 📊 **Chart rendering** — PNG chart images (response time trends, error rates, throughput, resource utilization)
- 🔄 **Revision workflow** — AI-assisted iterative refinement of executive summary and key-observations sections with version tracking
- 📚 **Comparison reports** — multi-run side-by-side analysis with comparison charts
- 🎨 **Custom theming** — configurable color palettes and chart schemas
- 📤 **Publishing hooks** — reports are structured for direct upload to Confluence via the Confluence MCP

For the complete tool inventory, see the
[`perfreport-mcp` source folder](../../mcp-perf-suite/perfreport-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template (optional — no external secrets required)
cd docker/perfreport-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. No credentials needed — defaults are safe

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#             NAME                    STATUS
# perfpilot-mcp-perfreport    Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8114/perfpilot-mcp-perfreport/mcp
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

**None.** PerfReport operates on PerfAnalysis outputs already present on disk
and produces reports locally. It has no external API dependencies. Publishing
generated reports to Confluence is handled by the separate
[Confluence MCP](../confluence-mcp/README.md), which has its own credentials.

---

## ⚙️ Environment variables

All environment variables for this MCP are optional.

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Reads PerfAnalysis outputs (`analysis/`); writes reports to `reports/` and chart images to `charts/` |

The `artifacts/` folder is shared across every PerfPilot MCP, so PerfReport
consumes PerfAnalysis outputs directly and produces artifacts that the
Confluence MCP can pick up for publishing.

---

## 🗂️ Baked-in configuration

The PerfReport MCP ships four configuration files baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/perfreport-mcp/config/config.yaml` | `/app/perfreport-mcp/config.yaml` | Global report settings |
| `docker/perfreport-mcp/config/report_config.yaml` | `/app/perfreport-mcp/report_config.yaml` | Section definitions, revisable sections, unit display |
| `docker/perfreport-mcp/config/chart_schema.yaml` | `/app/perfreport-mcp/chart_schema.yaml` | Chart definitions and unit configuration |
| `docker/perfreport-mcp/config/chart_colors.yaml` | `/app/perfreport-mcp/chart_colors.yaml` | Color palettes for charts |

The `chart_schema.yaml` and `chart_colors.yaml` files are the primary place
you'll customize this MCP for your organization's visual identity. Edit them
in the repo and rebuild the image to take effect.

---

## 🤝 Shared configuration (Independence Contract)

PerfReport is one of two MCPs that consume a whitelisted configuration file
from a sibling MCP:

| Source (in repo) | Destination (in image) | Reason |
|---|---|---|
| `docker/datadog-mcp/config/environments.json` | `/app/datadog-mcp/environments.json` | PerfReport reads Datadog environment definitions to label charts and report sections with the correct environment context |

PerfReport is also the **source** of a whitelisted file consumed by the
Confluence MCP:

| File (from perfreport-mcp) | Consumed by | Reason |
|---|---|---|
| `docker/perfreport-mcp/config/chart_schema.yaml` | `perfpilot-mcp-confluence` | The Confluence MCP renders charts on the destination page and needs the same schema PerfReport used to generate them |

These are two of exactly three cross-MCP configuration copies allowed by the
Independence Contract. See [`docker/README.md` §13](../README.md#-13-independence-contract)
for the full policy.

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its own endpoint
every 30 seconds. The first probe fires after a 20-second grace period (image
cold start).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-perfreport | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/perfreport-mcp/.env`
3. Rebuild: `docker compose up --build -d`

PerfReport itself makes few outbound HTTPS calls, but the CA install keeps
Python's SSL context consistent with the rest of the stack. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Report generation returns "analysis artifact not found" | PerfAnalysis has not yet written outputs for the given `test_run_id` | Verify `artifacts/{test_run_id}/analysis/` exists and contains PerfAnalysis JSON outputs |
| Charts rendered without expected colors | `chart_colors.yaml` not aligned with `chart_schema.yaml` | Verify both files reference the same series names, then rebuild the image |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-perfreport/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |
| Environment name in charts is wrong or missing | `environments.json` doesn't match the actual test environment | Verify `docker/datadog-mcp/config/environments.json` has an entry for your target environment |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-perfreport
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, Independence Contract)
- 📂 [`mcp-perf-suite/perfreport-mcp/`](../../mcp-perf-suite/perfreport-mcp/) — MCP source code + tool inventory
- 📂 [`docker/perfanalysis-mcp/README.md`](../perfanalysis-mcp/README.md) — upstream MCP that produces analysis outputs
- 📂 [`docker/confluence-mcp/README.md`](../confluence-mcp/README.md) — downstream MCP that publishes reports
- 📂 [`docker/datadog-mcp/README.md`](../datadog-mcp/README.md) — companion MCP that provides `environments.json`
