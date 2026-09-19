# 📊 perfpilot-mcp-perfanalysis

PerfAnalysis MCP server — process JMeter and BlazeMeter test results, correlate
them with observability metrics, and produce structured analysis artifacts
(SLA verdicts, bottleneck reports, top-N breakdowns).

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-perfanalysis:latest` |
| 📦 Container | `perfpilot-mcp-perfanalysis` |
| 🌐 Port | `8113` |
| 🔗 Endpoint | `http://localhost:8113/perfpilot-mcp-perfanalysis/mcp` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/perfanalysis-mcp/`](../../mcp-perf-suite/perfanalysis-mcp/) |

---

## 🎯 Capabilities

The PerfAnalysis MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) turn raw test-run artifacts into
structured performance-analysis outputs. Core capabilities include:

- 📈 **Response-time analysis** — percentiles, averages, standard deviations, per-transaction breakdowns
- ❌ **Error analysis** — error rates, error clustering, root-cause hints
- 🎯 **SLA verdicts** — apply pre-defined SLA thresholds from `slas.yaml` and emit pass/fail per transaction
- 🔥 **Bottleneck detection** — resource-exhaustion signatures (CPU, memory, DB, network) using metrics from the Datadog MCP
- 🏆 **Top-N breakdowns** — slowest / most-error-prone / most-called endpoints
- 🔗 **Cross-run correlation** — pair BlazeMeter results with the corresponding Datadog observability window

For the complete tool inventory, see the
[`perfanalysis-mcp` source folder](../../mcp-perf-suite/perfanalysis-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template (optional — no external secrets required)
cd docker/perfanalysis-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. No credentials needed — defaults are safe

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#              NAME                     STATUS
# perfpilot-mcp-perfanalysis  Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8113/perfpilot-mcp-perfanalysis/mcp
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

**None.** PerfAnalysis operates on artifacts already present on disk (produced
by BlazeMeter, JMeter, and Datadog MCPs). It has no external API dependencies.

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
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Test-run artifacts (BlazeMeter CSVs, JMeter JTLs, Datadog exports) — read as input, analysis outputs written back to `artifacts/{test_run_id}/analysis/` |

The `artifacts/` folder is shared across every PerfPilot MCP, so PerfAnalysis
reads inputs produced by upstream MCPs (BlazeMeter, JMeter, Datadog) and writes
outputs consumed downstream by PerfReport.

---

## 🗂️ Baked-in configuration

The PerfAnalysis MCP ships two configuration files baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/perfanalysis-mcp/config/config.yaml` | `/app/perfanalysis-mcp/config.yaml` | Analysis settings, resource thresholds, bottleneck detection tuning |
| `docker/perfanalysis-mcp/config/slas.yaml` | `/app/perfanalysis-mcp/slas.yaml` | SLA definitions for response times and error-rate thresholds |

The `slas.yaml` file is the primary place you'll customize this MCP for your
project's performance targets. Edit it in the repo and rebuild the image to
take effect.

---

## 🤝 Shared configuration (Independence Contract)

PerfAnalysis is one of two MCPs that consume a whitelisted configuration file
from a sibling MCP:

| Source (in repo) | Destination (in image) | Reason |
|---|---|---|
| `docker/datadog-mcp/config/environments.json` | `/app/datadog-mcp/environments.json` | PerfAnalysis reads Datadog environment definitions to correlate test-run windows with observability data |

This is one of exactly three cross-MCP configuration copies allowed by the
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
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-perfanalysis | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/perfanalysis-mcp/.env`
3. Rebuild: `docker compose up --build -d`

PerfAnalysis itself makes few outbound HTTPS calls, but the CA install keeps
Python's SSL context consistent with the rest of the stack. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Analysis tools return "artifact not found" | Test-run artifacts not present in the mounted `artifacts/` folder | Verify the upstream MCP (BlazeMeter / JMeter / Datadog) has completed and written to `artifacts/{test_run_id}/` |
| SLA verdicts are all `pass` regardless of actual results | `slas.yaml` empty or misconfigured | Edit `docker/perfanalysis-mcp/config/slas.yaml` and rebuild the image |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-perfanalysis/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |
| Bottleneck detection reports "no metrics available" | `environments.json` doesn't match the actual test environment | Verify `docker/datadog-mcp/config/environments.json` has an entry for your target environment |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-perfanalysis
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, Independence Contract)
- 📂 [`mcp-perf-suite/perfanalysis-mcp/`](../../mcp-perf-suite/perfanalysis-mcp/) — MCP source code + tool inventory
- 📂 [`docker/datadog-mcp/README.md`](../datadog-mcp/README.md) — companion MCP that provides `environments.json`
- 📂 [`docker/perfreport-mcp/README.md`](../perfreport-mcp/README.md) — downstream MCP that consumes PerfAnalysis outputs
