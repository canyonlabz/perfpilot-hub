# ✈️ 🌐 PerfPilot Web UI

The browser-based chat interface for PerfPilot Agents. Built with Next.js 15,
CopilotKit, and Tailwind CSS — connects to the AG-UI bridge for real-time 
streaming conversations with the PerfPilot orchestrator agent.

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
- Proxy `/api/*` requests to the AG-UI backend on **port 8102**
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

The Web UI requires the AG-UI backend (port 8102), the database, and the
MCP gateway to be running. The simplest way to start everything is with
**.NET Aspire**:

```powershell
cd aspire
aspire run
```

Aspire starts the database, every MCP server, both agent backends, **and**
the frontend automatically. Open the Aspire dashboard to find the frontend
URL (the `perfpilot-ui` resource).

See [`aspire/README.md`](../../../aspire/README.md) for commands and
[`docs/aspire/aspire-setup-guide.md`](../../../docs/aspire/aspire-setup-guide.md)
for first-time secrets setup.

> **Alternative:** If you prefer Docker Compose, see
> [`docker/README.md`](../../../docker/README.md). After starting Compose,
> launch the backend and frontend manually:
>
> ```bash
> # Terminal 1 — AG-UI backend
> cd agent-framework && python agui_server.py
>
> # Terminal 2 — Frontend
> cd agent-framework/frontend/ui && npm run dev
> ```

### What to look for

Once running, open the frontend in your browser. You should see:
- A green "Connected" badge in the header
- A thread sidebar on the left
- The chat panel ready for input

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
│   └── Forwards to http://localhost:8102/copilotkit/
│       (with browser cookie for user identity)
│
├── Next.js Rewrites (next.config.js)
│   ├── /api/* → http://localhost:8102/api/*
│   └── /health → http://localhost:8102/health
│
└── AG-UI Bridge (port 8102, agui_server.py)
    └── AG2 Orchestrator → Specialist Agents
```

### Key files

| File | Purpose |
|------|---------|
| `app/page.tsx` | Main page — manages `activeThreadId`, wraps chat in `<CopilotKit>` provider |
| `app/api/copilotkit/route.ts` | CopilotKit Runtime API route — proxies to AG-UI backend with cookie forwarding |
| `components/chat/chat-panel.tsx` | Unified chat panel — DB history + live streaming + react-markdown; also exposes attached GitHub SCM metadata to the orchestrator via `useCopilotReadable` |
| `components/sidebar/thread-sidebar.tsx` | Thread list with CRUD operations |
| `components/layout/header.tsx` | PerfPilot header with health indicator + Agents / Tasks / GitHub toggle buttons |
| `components/github/github-creds-card.tsx` | Session-only GitHub credential capture card (URL + PAT + optional branch/path) with privacy disclosures |
| `lib/api.ts` | API helpers (health, threads, messages) |
| `lib/github-creds.ts` | AES-256-GCM encrypted, session-scoped storage for GitHub credentials via Web Crypto API |
| `lib/types.ts` | TypeScript interfaces (Thread, Message, etc.) |
| `next.config.js` | Proxy rewrites + webpack config for CopilotKit v2 CSS exclusion |

---

## Planned UI Enhancements

| Feature | Status |
|---------|--------|
| Agent catalog panel (cards with status, skills) | Completed |
| SSE task streaming (real-time progress display) | Completed |
| HITL approve/reject inline cards | Completed |
| GitHub credentials card (session-scoped, encrypted, drives new-JMX pipeline) | Completed |
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

The default ports (3000 for frontend, 8102 for backend) may conflict with other
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

### Backend AG-UI port (default: 8102)

| # | File | What to change |
|---|------|----------------|
| 1 | `.env` | Set `AGUI_PORT=8082` (or your chosen port) |
| 2 | `frontend/ui/next.config.js` | Update both `destination` URLs from `http://localhost:8102` to `http://localhost:8082` |
| 3 | `frontend/ui/app/api/copilotkit/route.ts` | Update the `HttpAgent` URL from `http://localhost:8102/copilotkit/` to `http://localhost:8082/copilotkit/` |

### Backend A2A port (default: 8101)

| # | File | What to change |
|---|------|----------------|
| 1 | `.env` | Set `A2A_PORT=8081` (or your chosen port) |

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
| AG-UI backend | 8102 | `AGUI_PORT` | `next.config.js`, `route.ts` |
| A2A server | 8101 | `A2A_PORT` | Orchestrator specialist delegation is in-process |
| CORS origins | localhost:3000 | `AGUI_CORS_ORIGINS` | `agui_server.py` (fallback list) |
| Gateway MCP | 8125 | `GATEWAY_MCP_URL` | `.env` |
| PostgreSQL | 5432 | `PERFAGENT_STATE_PORT` | `.env` |

> **Tip:** All backend port settings read from environment variables first,
> falling back to defaults only if unset. The `.env` file in `agent-framework/`
> is the single place to configure them — see `.env.example` for the full
> template.
