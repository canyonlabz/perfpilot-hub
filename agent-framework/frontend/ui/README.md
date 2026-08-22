# PerfPilot Web UI

The browser-based chat interface for PerfPilot Agents. Built with Next.js 15,
CopilotKit, and Tailwind CSS — connects to the AG-UI bridge on port 8002 for
real-time streaming conversations with the PerfPilot orchestrator agent.

---

## Prerequisites

| Dependency | Version | Purpose |
|------------|---------|---------|
| Node.js | 25.x (tested with 25.2.1) | JavaScript runtime |
| npm | 11.x (tested with 11.6.2) | Package manager |

### Windows

```powershell
# Install Node.js via winget (includes npm)
winget install OpenJS.NodeJS

# Verify installation
node --version   # expected: v25.x.x
npm --version    # expected: 11.x.x
```

### macOS

```bash
# Install Node.js via Homebrew (includes npm)
brew install node

# Verify installation
node --version   # expected: v25.x.x
npm --version    # expected: 11.x.x
```

---

## Installation

```bash
cd agent-framework/frontend/ui
npm install
```

This installs all dependencies including CopilotKit, Tailwind CSS, shadcn/ui
components, react-markdown, and the AG-UI client SDK.

---

## Configuration

No manual configuration is required for local development. The app is
pre-configured to:

- Run on **port 3000**
- Proxy `/api/*` requests to the AG-UI backend on **port 8002**
- Proxy `/health` to the backend health endpoint
- Connect to the `perfpilot-orchestrator` agent via CopilotKit

The proxy rewrites are defined in `next.config.js`. If your backend runs on a
different port, update the `destination` URLs there.

---

## Running

> **Important:** The frontend requires the backend services to be running first.
> See [Startup Order](#startup-order) below.

```bash
cd agent-framework/frontend/ui
npm run dev
```

The dev server starts on `http://localhost:3000`. Open this in your browser.

### Other commands

| Command | Purpose |
|---------|---------|
| `npm run dev` | Start development server with hot reload |
| `npm run build` | Production build |
| `npm run start` | Start production server (after build) |
| `npm run lint` | Run ESLint |

---

## Startup Order

The PerfPilot Web UI depends on several backend services that must be started
in sequence:

### Step 1 — Start the database and MCP gateway

```bash
# From the repo root — use the FULL compose variant
# Windows:
docker compose -f docker/docker-compose-full-windows.yaml up -d

# macOS:
docker compose -f docker/docker-compose-full-mac.yaml up -d
```

This starts:
- **PerfMemory PostgreSQL** (pgvector + AGE) — includes the `perfagent_state`
  database used by the agent framework for threads, sessions, and conversation
  history
- **Gateway MCP** — the unified MCP endpoint that agents route tool calls through

Wait until the containers are healthy before proceeding.

### Step 2 — Start the AG-UI backend

```bash
cd agent-framework
python agui_server.py
```

This starts the AG-UI bridge on **port 8002**, which:
- Serves the `/copilotkit/` SSE streaming endpoint
- Hosts thread CRUD under `/api/threads/*`
- Hosts the conversation message history under `/api/threads/{id}/messages`
- Connects to PostgreSQL for persistent conversation state
- Dispatches to the real AG2 orchestrator agent

Wait for `Uvicorn running on http://0.0.0.0:8002` before proceeding.

### Step 3 — Start the frontend

```bash
cd agent-framework/frontend/ui
npm run dev
```

Open `http://localhost:3000` in your browser. You should see:
- A green "Connected" badge in the header
- A thread sidebar on the left
- The chat panel ready for input

### Stopping

Stop in reverse order:
1. Stop the frontend (`Ctrl+C`)
2. Stop the backend (`Ctrl+C`)
3. Stop the Docker containers: `docker compose -f docker/docker-compose-full-windows.yaml down`

---

## Current State

### What's working

| Feature | Status | 
|---------|--------|
| Project scaffold, health probe | Done | 
| Chat interface with streaming responses | Done |
| Thread management sidebar (create, rename, archive, delete, switch) | Done | 
| Persistent chat history across restarts (PostgreSQL + localStorage) | Done |
| Markdown rendering for agent responses (react-markdown + remark-gfm) | Done | 
| Real-time streaming with inline "thinking" indicator | Done |
| Single unified chat pane (no dual-pane split) | Done | 
| Stop generation button | Done | 

### Architecture

```
Browser (localhost:3000)
│
├── CopilotKit React SDK (useCopilotChat hook)
│   └── POST /api/copilotkit
│
├── Next.js API Route (CopilotKit Runtime + HttpAgent)
│   └── Forwards to http://localhost:8002/copilotkit/
│       (with browser cookie for user identity)
│
├── Next.js Rewrites (next.config.js)
│   ├── /api/* → http://localhost:8002/api/*
│   └── /health → http://localhost:8002/health
│
└── AG-UI Bridge (port 8002, agui_server.py)
    └── AG2 Orchestrator → Specialist Agents
```

### Key files

| File | Purpose |
|------|---------|
| `app/page.tsx` | Main page — manages `activeThreadId`, wraps chat in `<CopilotKit>` provider |
| `app/api/copilotkit/route.ts` | CopilotKit Runtime API route — proxies to AG-UI backend with cookie forwarding |
| `components/chat/chat-panel.tsx` | Unified chat panel — DB history + live streaming + react-markdown |
| `components/sidebar/thread-sidebar.tsx` | Thread list with CRUD operations |
| `components/layout/header.tsx` | PerfPilot header with health indicator |
| `lib/api.ts` | API helpers (health, threads, messages) |
| `lib/types.ts` | TypeScript interfaces (Thread, Message, etc.) |
| `next.config.js` | Proxy rewrites + webpack config for CopilotKit v2 CSS exclusion |

---

## Planned UI Enhancements

| Feature | Status |
|---------|--------|
| Agent catalog panel (cards with status, skills) | Completed |
| SSE task streaming (real-time progress display) | Completed |
| HITL approve/reject inline cards | Completed |
| Test-run results display (`/runs` pages) | In Progress |
| Polish — dark/light mode, responsive layout, skeletons, error boundaries | Not started |

---

## Tech Stack

| Layer | Choice | Version |
|-------|--------|---------|
| Runtime | Node.js | 25.x |
| Framework | Next.js 15 (App Router) | ^15.0.0 |
| Chat SDK | @copilotkit/react-core + @copilotkit/react-ui | ^1.60.1 |
| AG-UI Client | @ag-ui/client | ^0.0.57 |
| CopilotKit Runtime | @copilotkit/runtime | ^1.60.1 |
| Markdown | react-markdown + remark-gfm | ^10.1.0 / ^4.0.1 |
| Styling | Tailwind CSS 3.4 + shadcn/ui | latest |
| Language | TypeScript | ^5.6.0 |

---

## Changing Ports

The default ports (3000 for frontend, 8002 for backend) may conflict with other
services on your machine. All port references are configurable — here is the
complete list of files to update.

### Frontend port (default: 3000)

Next.js picks port 3000 by default. To change it, pass `--port` to the dev
command:

```bash
npm run dev -- --port 3001
```

No file edits are needed for the frontend port itself. However, the **backend
CORS allowlist** must be updated to accept the new origin (see below).

### Backend AG-UI port (default: 8002)

| # | File | What to change |
|---|------|----------------|
| 1 | `.env` | Set `AGUI_PORT=8082` (or your chosen port) |
| 2 | `frontend/ui/next.config.js` | Update both `destination` URLs from `http://localhost:8002` to `http://localhost:8082` |
| 3 | `frontend/ui/app/api/copilotkit/route.ts` | Update the `HttpAgent` URL from `http://localhost:8002/copilotkit/` to `http://localhost:8082/copilotkit/` |

### Backend A2A port (default: 8001)

| # | File | What to change |
|---|------|----------------|
| 1 | `.env` | Set `A2A_PORT=8081` (or your chosen port) |
| 2 | `agents/orchestrator/agent.py` | The default is overridden via env var `PERFPILOT_A2A_BASE_URL`. Set it in `.env`: `PERFPILOT_A2A_BASE_URL=http://127.0.0.1:8081` |

### CORS (when frontend port changes)

The AG-UI backend allows `http://localhost:3000` and `http://127.0.0.1:3000`
by default. If the frontend runs on a different port, set the env var:

```bash
# In agent-framework/.env
AGUI_CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001
```

### Quick reference — all port-related settings

| Setting | Default | Env var | Files with hardcoded references |
|---------|---------|---------|--------------------------------|
| Frontend dev server | 3000 | (CLI `--port`) | — |
| AG-UI backend | 8002 | `AGUI_PORT` | `next.config.js`, `route.ts` |
| A2A server | 8001 | `A2A_PORT` | `orchestrator/agent.py` (overridable via `PERFPILOT_A2A_BASE_URL`) |
| CORS origins | localhost:3000 | `AGUI_CORS_ORIGINS` | `agui_server.py` (fallback list) |
| Gateway MCP | 8000 | `GATEWAY_MCP_URL` | `.env` |
| PostgreSQL | 5432 | `PERFAGENT_STATE_PORT` | `.env` |

> **Tip:** All backend port settings read from environment variables first,
> falling back to defaults only if unset. The `.env` file in `agent-framework/`
> is the single place to configure them — see `.env.example` for the full
> template.
