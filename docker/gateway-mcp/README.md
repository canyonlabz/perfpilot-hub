# 🌐 perfpilot-mcp-gateway

Gateway MCP server — a slim Python-only aggregator that mounts the eight
gateway-mounted MCPs (blazemeter, datadog, jmeter, perfanalysis, perfreport,
confluence, perfmemory, github) as a single unified endpoint. Clients only
need to know one URL to reach the entire suite.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-gateway:latest` |
| 📦 Container | `perfpilot-mcp-gateway` |
| 🌐 Port | `8125` |
| 🔗 Endpoint | `http://localhost:8125/perfpilot-mcp-gateway/mcp` |
| 🩺 Health probe | `http://localhost:8125/health` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/gateway-mcp/`](../../mcp-perf-suite/gateway-mcp/) |

---

## 🎯 Capabilities

The Gateway MCP does not expose its own performance-testing tools. Instead,
it uses FastMCP's `create_proxy()` + `mount()` API to aggregate eight upstream
MCPs over streamable-HTTP transport. Every mounted tool appears under a
namespace matching the upstream MCP's name:

- `jmeter.*` — tools from `perfpilot-mcp-jmeter`
- `blazemeter.*` — tools from `perfpilot-mcp-blazemeter`
- `datadog.*` — tools from `perfpilot-mcp-datadog`
- `perfanalysis.*` — tools from `perfpilot-mcp-perfanalysis`
- `perfreport.*` — tools from `perfpilot-mcp-perfreport`
- `confluence.*` — tools from `perfpilot-mcp-confluence`
- `perfmemory.*` — tools from `perfpilot-mcp-perfmemory`
- `github.*` — tools from `perfpilot-mcp-github`

Playwright is **not** mounted through the gateway — the vendor image does not
support HTTP path prefixes. Agents call `perfpilot-mcp-playwright:8117/mcp`
directly.

For the complete mount configuration, see the
[`gateway-mcp` source folder](../../mcp-perf-suite/gateway-mcp/).

---

## 🚀 Quick start (standalone)

⚠️ **Upstream dependency.** The gateway is a proxy — it does nothing on its
own. In standalone mode you must first bring up the upstream MCPs and set
`MCP_URL_<name>` for each MCP the gateway should mount. Any MCP with an
empty URL is skipped silently at startup.

The full-stack compose (`docker-compose-full-{mac,windows}.yaml`) handles
this automatically via Docker network DNS baked into `config.yaml`.

Standalone in three steps:

```bash
# 1. Copy the environment template
cd docker/gateway-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Uncomment and populate MCP_URL_* for each upstream MCP already running
#    on the host (use host.docker.internal for Docker Desktop)

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#             NAME                    STATUS
# perfpilot-mcp-gateway       Up 30s (healthy)
```

Test the endpoint:

```bash
# Health probe (returns JSON with mounted MCP status):
curl http://localhost:8125/health

# MCP endpoint:
curl http://localhost:8125/perfpilot-mcp-gateway/mcp
```

Shut down the container when finished:

```bash
docker compose down
```

---

## 🔑 Required credentials

**None.** The gateway itself has no external API dependencies — it only
forwards requests to upstream MCPs. Credentials for the upstream MCPs live
in each of their own `.env` files (or the unified `docker/.env` in full-stack
mode).

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `MCP_URL_JMETER` | *(from baked config)* | URL of the JMeter MCP. Empty = skip mounting. |
| `MCP_URL_BLAZEMETER` | *(from baked config)* | URL of the BlazeMeter MCP. Empty = skip mounting. |
| `MCP_URL_DATADOG` | *(from baked config)* | URL of the Datadog MCP. Empty = skip mounting. |
| `MCP_URL_PERFANALYSIS` | *(from baked config)* | URL of the PerfAnalysis MCP. Empty = skip mounting. |
| `MCP_URL_PERFREPORT` | *(from baked config)* | URL of the PerfReport MCP. Empty = skip mounting. |
| `MCP_URL_CONFLUENCE` | *(from baked config)* | URL of the Confluence MCP. Empty = skip mounting. |
| `MCP_URL_PERFMEMORY` | *(from baked config)* | URL of the PerfMemory MCP. Empty = skip mounting. |
| `MCP_URL_GITHUB` | *(from baked config)* | URL of the GitHub MCP. Empty = skip mounting. |

In the full-stack compose, `config.yaml` provides defaults that resolve to
Docker service DNS names (e.g., `http://perfpilot-mcp-blazemeter:8110/perfpilot-mcp-blazemeter/mcp`).
Setting any `MCP_URL_*` in `.env` overrides the baked default at runtime.

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

**None.** The gateway is stateless and produces no artifacts.

---

## 🗂️ Baked-in configuration

The Gateway MCP ships one configuration file baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/gateway-mcp/config/config.yaml` | `/app/gateway-mcp/config.yaml` | Default upstream MCP URLs (Docker DNS names for full-stack mode) |

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its `/health`
endpoint every 30 seconds. First probe fires after a 20-second grace period.

Unlike the other MCPs, the gateway's healthcheck targets `/health` (a
dedicated JSON endpoint) rather than `/mcp`. This is because the gateway's
`/health` endpoint returns the mount status of each upstream MCP, which is
more informative than a bare protocol probe.

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-gateway | jq

# Explicit health probe:
curl http://localhost:8125/health
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/gateway-mcp/.env`
3. Rebuild: `docker compose up --build -d`

The gateway itself typically talks to upstream MCPs over plain HTTP inside
the Docker network, but the CA install keeps its SSL context consistent with
the rest of the stack (useful if any `MCP_URL_*` points at an HTTPS endpoint
across a proxy boundary). See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Gateway starts but `/health` shows zero mounted MCPs | All `MCP_URL_*` values empty or the baked `config.yaml` was not built into the image | Check `docker compose logs perfpilot-mcp-gateway` at startup; each mount attempt logs success or the reason it was skipped |
| Tool calls return "unknown tool" through the gateway | The upstream MCP is not mounted (see above) or the tool name is missing the namespace prefix | MCP tools are exposed as `<namespace>.<tool>`, e.g. `blazemeter.get_workspaces` — verify your client is using the namespaced name |
| Tools return network errors (`connection refused`, `no route to host`) | `MCP_URL_*` points at an unreachable endpoint | For standalone mode, verify each upstream MCP is running on the host and reachable via `host.docker.internal:<port>` |
| Endpoint returns `404` | Wrong URL path | The MCP endpoint is `/perfpilot-mcp-gateway/mcp`; the health probe is `/health`. Neither is just `/mcp`. |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-gateway
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, routing, corporate CA)
- 📂 [`mcp-perf-suite/gateway-mcp/`](../../mcp-perf-suite/gateway-mcp/) — MCP source code + mount configuration
- 🌐 [FastMCP documentation](https://gofastmcp.com/) — official FastMCP framework docs
- 📄 Per-MCP READMEs — each of the 8 mounted MCPs has its own README under `docker/<mcp>-mcp/`
