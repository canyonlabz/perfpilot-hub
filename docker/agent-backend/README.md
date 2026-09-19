# 🤖 perfpilot-a2a + perfpilot-agui

Agent backend services — the LangGraph-based Python backend that powers
PerfPilot Hub's AI-driven performance-testing workflows. A single Dockerfile
produces two container images:

- **`perfpilot-a2a`** — Agent-to-Agent (A2A) server (SSE-based orchestration)
- **`perfpilot-agui`** — AG-UI bridge (CopilotKit-compatible frontend adapter)

Both containers share the same source code and dependencies; only the
entrypoint command and listen port differ.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | A2A | AG-UI |
|---|---|---|
| 🐳 Image | `perfpilot-a2a:latest` | `perfpilot-agui:latest` |
| 📦 Container | `perfpilot-a2a` | `perfpilot-agui` |
| 🌐 Port | `8101` | `8102` |
| 🔗 Endpoint | `http://localhost:8101/` | `http://localhost:8102/` |
| 🩺 Health probe | `http://localhost:8101/health` | `http://localhost:8102/health` |
| ▶️ Entrypoint | `python a2a_server.py` | `python agui_server.py` |
| 🐍 Base image | `python:3.12-slim` (+ `libpq-dev` for `asyncpg`) | Same |
| 📂 Source code | [`agent-framework/backend/`](../../agent-framework/backend/) | Same |

Both services build from the same Dockerfile — Docker's layer cache means
the second image is essentially free after the first is built.

---

## 🎯 Capabilities

The agent backend orchestrates AI-driven performance-testing workflows by
combining LangGraph reasoning with the PerfPilot MCP suite. Core capabilities
include:

- 🧠 **LangGraph orchestration** — multi-step reasoning graphs with state persistence
- 💬 **Multi-model support** — OpenAI, Azure OpenAI, or Ollama as the chat model
- 🔧 **MCP tool binding** — connects to the gateway MCP for all performance-testing tools
- 🎭 **Playwright integration** — dedicated connection to `perfpilot-mcp-playwright` for browser automation
- 💾 **Persistent state** — LangGraph checkpoints stored in the `perfagent_state` PostgreSQL database
- 🔄 **Streaming responses** — SSE (Server-Sent Events) for A2A, CopilotKit-compatible endpoints for AG-UI
- 🔌 **Split-mode support** — bypass the gateway and connect to individual MCPs directly (useful for debugging or Aspire deployments)

For the complete tool inventory and orchestration flow, see the
[`agent-framework/backend/` source folder](../../agent-framework/backend/).

---

## 🚀 Quick start (standalone)

⚠️ **Multiple dependencies.** The agent backend needs the following services
running before it can operate:

- **PostgreSQL** (`perfmem-pgvector-age`) — for the `perfagent_state` database
- **Gateway MCP** (`perfpilot-mcp-gateway`) — for all performance-testing tools
- **Playwright MCP** (`perfpilot-mcp-playwright`) — for browser automation

Bring these up first (either standalone or via the full-stack compose), then
run the agent backend in three steps:

```bash
# 1. Copy the environment template
cd docker/agent-backend/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env with your LLM provider credentials, MCP endpoints, and
#    Postgres connection details (see next sections)

# 3. Build and start both containers
docker compose up --build -d
```

The `perfpilot-agui` container depends on `perfpilot-a2a`, so both will be
started in the correct order.

Verify they're healthy:

```bash
docker compose ps
#            NAME                    STATUS
# perfpilot-a2a               Up 30s (healthy)
# perfpilot-agui              Up 30s (healthy)
```

Test the endpoints:

```bash
curl http://localhost:8101/health    # A2A
curl http://localhost:8102/health    # AG-UI
```

Shut down when finished:

```bash
docker compose down
```

---

## 🔑 Required credentials

The agent backend needs three credential sets: **LLM provider**, **MCP
endpoints**, and **PostgreSQL** connection.

### 🧠 LLM provider

Choose one of three providers via `LLM_PROVIDER`. This is the chat model
used by the agent for reasoning — separate from the embedding model used by
PerfMemory.

#### `openai`

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key *(sensitive — must not be shared or committed)* |
| `OPENAI_MODEL` | Chat model name (default: `gpt-4o-mini`) |

#### `azure_openai`

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key *(sensitive — must not be shared or committed)* |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint, e.g. `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name for the chat model |
| `AZURE_OPENAI_API_VERSION` | API version, e.g. `2024-02-15-preview` |

#### `ollama` (local, no cloud dependency)

| Variable | Purpose |
|---|---|
| `OLLAMA_BASE_URL` | Ollama server URL. Inside Docker use `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | Chat model name (e.g. `llama3.1`) |

### 🌐 MCP endpoints

| Variable | Standalone default | Full-stack value | Purpose |
|---|---|---|---|
| `GATEWAY_MCP_URL` | `http://host.docker.internal:8125/perfpilot-mcp-gateway/mcp` | `http://perfpilot-mcp-gateway:8125/perfpilot-mcp-gateway/mcp` | Gateway aggregator for the 8 mounted MCPs |
| `PLAYWRIGHT_MCP_URL` | `http://host.docker.internal:8117/mcp` | `http://perfpilot-mcp-playwright:8117/mcp` | Direct connection to the Playwright MCP (bypasses gateway) |

**Optional split mode** — when any `MCP_URL_<name>` variable is set, the
agent connects to that MCP directly instead of via the gateway. Useful for
debugging or Aspire deployments where each MCP has its own address:

| Variable | Purpose |
|---|---|
| `MCP_URL_JMETER`, `MCP_URL_BLAZEMETER`, `MCP_URL_DATADOG`, `MCP_URL_PERFANALYSIS`, `MCP_URL_PERFREPORT`, `MCP_URL_CONFLUENCE`, `MCP_URL_PERFMEMORY`, `MCP_URL_GITHUB` | Per-MCP override URLs |

### 🗄️ PostgreSQL (`perfagent_state`)

| Variable | Standalone default | Full-stack value | Purpose |
|---|---|---|---|
| `PERFAGENT_STATE_HOST` | `host.docker.internal` | `perfmem-pgvector-age` | Database host |
| `PERFAGENT_STATE_PORT` | `5432` | `5432` | Database port |
| `PERFAGENT_STATE_DB` | `perfagent_state` | `perfagent_state` | Database name (auto-created on first launch) |
| `PERFAGENT_STATE_USER` | `perfadmin` | `perfadmin` | Database user |
| `PERFAGENT_STATE_PASSWORD` | *(required)* | *(required)* | Database password *(sensitive)* |
| `PERFAGENT_STATE_SSLMODE` | `prefer` | `prefer` | SSL mode |
| `PERFAGENT_STATE_SSLROOTCERT` | *(unset)* | *(unset)* | Optional CA cert path |

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with empty values) is tracked in the repository.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `A2A_PORT` | `8101` | A2A listen port (compose sets this per-service) |
| `AGUI_PORT` | `8102` | AG-UI listen port (compose sets this per-service) |
| `LLM_PROVIDER` | `openai` | One of `openai`, `azure_openai`, `ollama` |
| `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` | *(baked)* | Point Python at the OS trust store; only override if you have a custom CA bundle |
| `AUTH_ENABLED` | `false` | Toggle authentication middleware |
| `OTEL_ENABLED` | `false` | Toggle OpenTelemetry export |
| `OTEL_EXPORTER_ENDPOINT` | *(unset)* | OTLP endpoint for trace / metric export |
| `OTEL_SERVICE_NAME` | `perfpilot-agents` | Service name in OTel exports |

See the credential sections above for LLM, MCP, and Postgres variables.

---

## 📂 Bind mounts

**None.** The agent backend is stateless in terms of local filesystem — all
state lives in the `perfagent_state` database and the shared `artifacts/`
tree (accessed via the MCPs, not directly).

---

## 🩺 Health check

Each container ships a Docker `HEALTHCHECK` that curls its own `/health`
endpoint every 30 seconds. First probe fires after a 15-second grace period.

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-a2a | jq
docker inspect --format='{{json .State.Health}}' perfpilot-agui | jq
```

The A2A container probes `HEALTHCHECK_PORT=8101`; the AG-UI container probes
`HEALTHCHECK_PORT=8102`. Both are set via compose per-service.

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/agent-backend/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile installs the `truststore` Python library, which routes SSL
operations through the OS trust store instead of hard-coded CA bundles.
Combined with `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` env vars, this covers
all LLM API calls (OpenAI, Azure) and MCP client connections.

See the [top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container restart loop with `asyncpg.exceptions.InvalidPasswordError` | Wrong `PERFAGENT_STATE_PASSWORD` | Verify the value matches the actual `POSTGRES_PASSWORD` set in `docker/postgresql/.env` (or the full-stack `.env`) |
| Container restart loop with `httpx.ConnectError: All connection attempts failed` on the gateway | Gateway not running or `GATEWAY_MCP_URL` unreachable | Verify `perfpilot-mcp-gateway` is up (`docker compose ps` in `docker/gateway-mcp/`) and reachable from inside the agent container |
| LLM calls return `401 Unauthorized` | Wrong `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY` or Azure `_DEPLOYMENT` name typo | Regenerate the key and verify the deployment name matches Azure's portal |
| Tools not found by the agent | Gateway is up but no MCPs are mounted (see `/health` on port 8125) | Verify each upstream MCP is running and `MCP_URL_*` is set in `docker/gateway-mcp/.env` |
| `perfpilot-agui` starts before `perfpilot-a2a` is ready | Race between `depends_on` and A2A initialization | The `depends_on` clause only waits for start, not health — if the race is fatal, add a delay to `perfpilot-agui` or wait for A2A's health probe to pass |
| Endpoint returns `404` on `/health` | Wrong port (A2A vs AG-UI) | A2A: 8101, AG-UI: 8102. Both expose `/health` |
| Build fails with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-a2a
docker compose logs -f perfpilot-agui
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA)
- 📂 [`agent-framework/backend/`](../../agent-framework/backend/) — agent backend source code
- 📂 [`docker/agent-frontend/README.md`](../agent-frontend/README.md) — Next.js UI that consumes A2A + AG-UI
- 📂 [`docker/gateway-mcp/README.md`](../gateway-mcp/README.md) — gateway aggregator used by the agent
- 📂 [`docker/playwright-mcp/README.md`](../playwright-mcp/README.md) — browser automation used by the agent
- 📂 [`docker/postgresql/README.md`](../postgresql/README.md) — required database backend
- 🌐 [LangGraph documentation](https://langchain-ai.github.io/langgraph/) — agent orchestration framework
- 🌐 [CopilotKit documentation](https://docs.copilotkit.ai/) — AG-UI bridge protocol
