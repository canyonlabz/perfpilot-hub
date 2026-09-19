# 💻 perfpilot-ui

Agent frontend — the Next.js web application that provides the human-facing
chat and workflow UI for PerfPilot Hub. Talks to the agent backend
(`perfpilot-a2a` + `perfpilot-agui`) over HTTP and renders streaming
responses via CopilotKit.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-ui:latest` |
| 📦 Container | `perfpilot-ui` |
| 🌐 Port | `8080` |
| 🔗 Endpoint | `http://localhost:8080/` |
| ⚛️ Framework | Next.js (React) |
| 🟢 Base image | `node:22-slim` |
| 📂 Source code | [`agent-framework/frontend/ui/`](../../agent-framework/frontend/ui/) |

---

## 🎯 Capabilities

The PerfPilot UI is a chat-first interface for driving performance-testing
workflows through the agent backend. Core capabilities include:

- 💬 **Conversational interface** — natural-language interaction with the AI agent
- 🔄 **Streaming responses** — real-time token streaming from A2A / AG-UI via SSE
- 📋 **Session management** — conversation history persisted in the `perfagent_state` database
- 📊 **Rich artifact rendering** — display of test-run summaries, charts, and reports produced by the agent
- 🧩 **CopilotKit integration** — action buttons, generative UI, and tool-use visualization

For the complete UI source and route reference, see the
[`agent-framework/frontend/ui/` source folder](../../agent-framework/frontend/ui/).

---

## 🚀 Quick start (standalone)

⚠️ **Backend dependency.** The UI requires the agent backend (`perfpilot-a2a`
and `perfpilot-agui`) to be running before it can serve useful content.
Bring the backend up first (see [`docker/agent-backend/README.md`](../agent-backend/README.md)),
then run this container in three steps:

```bash
# 1. Copy the environment template (optional — sensible defaults)
cd docker/agent-frontend/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env only if your agent backend is at a non-default location

# 3. Build and start the container
docker compose up --build -d
```

Access the UI:

```
http://localhost:8080/
```

Verify it's running:

```bash
docker compose ps
#            NAME                    STATUS
# perfpilot-ui                Up 30s (healthy)
```

Shut down when finished:

```bash
docker compose down
```

---

## 🔑 Required credentials

**None.** The UI has no external API dependencies of its own. LLM API keys
and MCP credentials all live in the agent backend's `.env`.

---

## ⚙️ Environment variables

All environment variables for this container are optional.

| Variable | Standalone default | Full-stack value | Purpose |
|---|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` | `local` → human-readable console logs. `cloud` → structured JSON to stdout |
| `ENABLE_CORP_CA` | `false` | (unchanged) | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `PORT` | `8080` | `8080` | Next.js listen port (honored via the Next.js `PORT` env var) |
| `FRONTEND_PORT` | `8080` | `8080` | Alias for `PORT` used by the healthcheck |
| `AGUI_BACKEND_URL` | `http://host.docker.internal:8102` | `http://perfpilot-agui:8102` | URL of the AG-UI backend |
| `A2A_BACKEND_URL` | `http://host.docker.internal:8101` | `http://perfpilot-a2a:8101` | URL of the A2A backend |

---

## 📂 Bind mounts

**None.** The UI is stateless — no runtime data is persisted on the host.
The image contains a full copy of the compiled Next.js application.

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls the root
route (`/`) every 60 seconds. First probe fires after a 30-second grace
period (Next.js dev mode takes longer to boot than a Python service).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-ui | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/agent-frontend/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile installs the CA into the OS trust store and sets
`NODE_EXTRA_CA_CERTS` so Next.js (and any client-side calls it makes to
Atlassian, GitHub, or LLM providers via server-side rendering) can validate
certificates issued by the intercepting proxy. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI loads but chat responses never appear | AG-UI backend unreachable | Verify `perfpilot-agui` is running and reachable at `AGUI_BACKEND_URL` from inside the UI container |
| Console shows CORS errors from A2A / AG-UI | Wrong `AGUI_BACKEND_URL` or `A2A_BACKEND_URL` for your deployment mode | Standalone uses `host.docker.internal`; full-stack uses Docker service DNS names (`perfpilot-a2a`, `perfpilot-agui`) |
| Build fails on `npm ci` with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| Container is healthy but the UI is blank | Next.js hot-reload race with dependency install | Wait 15–30 seconds after the container is healthy; `docker compose logs -f perfpilot-ui` shows when Next.js has finished compiling |
| Port `8080` already in use | Another process is bound to `8080` on the host | Change the host-side mapping in `docker-compose.yml` (e.g. `"8090:8080"`) — the container-side port is baked at 8080 |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-ui
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA)
- 📂 [`agent-framework/frontend/ui/`](../../agent-framework/frontend/ui/) — Next.js source code
- 📂 [`docker/agent-backend/README.md`](../agent-backend/README.md) — required backend (A2A + AG-UI)
- 🌐 [Next.js documentation](https://nextjs.org/docs) — framework reference
- 🌐 [CopilotKit documentation](https://docs.copilotkit.ai/) — agent-UI integration protocol
