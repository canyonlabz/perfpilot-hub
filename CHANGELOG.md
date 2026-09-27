# MCP Performance Suite - Changelog (September 2026)

This document summarizes the enhancements and new features added to the MCP Performance Suite during September 2026.

> **Landed in:**
> - [PR #8 — feat: add GitHub MCP, env/SCM resolvers, BlazeMeter test provisioning, and A2A new-JMX pipeline](https://github.com/canyonlabz/perfpilot-hub/pull/8) *(merged 2026-09-01)*
> - `features/docker-per-mcp-images` branch *(Docker full-stack refactor — see §5)*

---

## Table of Contents

- [1. New MCP: GitHub MCP](#1-new-mcp-github-mcp)
- [2. BlazeMeter — Test Provisioning](#2-blazemeter--test-provisioning)
- [3. A2A — New-JMX Pipeline](#3-a2a--new-jmx-pipeline)
- [4. Repository-Wide Documentation Refactor](#4-repository-wide-documentation-refactor)
- [5. Docker Full-Stack Refactor](#5-docker-full-stack-refactor)
  - [5.1 Overview](#51-overview)
  - [5.2 Health Check Standardization](#52-health-check-standardization)
  - [5.3 Non-Root Users, `tini`, and CRLF Handling](#53-non-root-users-tini-and-crlf-handling)
  - [5.4 Playwright MCP — Microsoft Official Base Image](#54-playwright-mcp--microsoft-official-base-image)
  - [5.5 Agent Frontend — Image Size Optimization](#55-agent-frontend--image-size-optimization)
  - [5.6 Full-Stack Compose Files — YAML Anchors](#56-full-stack-compose-files--yaml-anchors)
  - [5.7 Gateway Standalone Defaults](#57-gateway-standalone-defaults)
  - [5.8 End-to-End Validation](#58-end-to-end-validation)
- [6. Files Created](#6-files-created)
- [7. Files Modified](#7-files-modified)
- [Previous Changelogs](#previous-changelogs)

---

## 1. New MCP: GitHub MCP

A minimal GitHub Contents API bridge for the PerfPilot performance testing pipeline. Enables agents to push generated JMX scripts (and other artifacts) directly to a GitHub repository without needing shell access to `git`.

### 1.1 Tools

| Tool | Purpose |
|------|---------|
| `push_jmx_file` | Push a JMX file to a target repository / branch / path (creates or updates) |
| `ensure_branch` | Create a branch from the default branch if it does not exist |
| `get_default_branch` | Retrieve the default branch name for a repository |

### 1.2 Token Resolution

The MCP supports both **user-attributed** and **user-agnostic** token modes:

- **User-attributed** — token resolved from the request context (per-user commits, respects GitHub audit trails)
- **User-agnostic** — a shared bot token from `.env` (`GITHUB_TOKEN`) used when no per-user token is available

### 1.3 Configuration

- Config: `mcp-perf-suite/github-mcp/config.yaml` (base) + `config.example.yaml` (template)
- Env: `.env.example` documents `GITHUB_TOKEN`, `GITHUB_DEFAULT_OWNER`, `GITHUB_DEFAULT_REPO`
- Docker: `docker/github-mcp/` (Dockerfile, docker-compose.yml, README.md)

### 1.4 Wiring

- Mounted behind the PerfPilot gateway at `/perfpilot-mcp-github/mcp`
- Standalone port: `8118`

---

## 2. BlazeMeter — Test Provisioning

The BlazeMeter MCP gained the ability to **create** tests dynamically, not just execute pre-existing ones. This lets agents provision a full test → run → collect-results loop in a single workflow without manual pre-configuration in the BlazeMeter UI.

Key additions:

- Test creation tools (create, configure, associate with workspace / project)
- Workspace and project resolvers for identifying targets by name instead of ID
- Configuration templates for common test types (functional, load, threads / duration)

Together with the new-JMX pipeline (§3), this makes it possible for the Execution Agent to take a freshly generated JMX, provision a new BlazeMeter test around it, and kick off a run — all in one turn.

---

## 3. A2A — New-JMX Pipeline

The A2A server now supports a full **generate → validate → commit → provision → execute** pipeline for brand-new JMX scripts, orchestrated across the Script, Execution, and (optionally) Notifications agents.

### 3.1 Env / SCM Resolvers

A shared resolver layer under `agent-framework/backend/utils/` centralizes:

- Environment lookups (BlazeMeter workspace, target URL, JMeter version)
- SCM lookups (GitHub owner / repo / default branch, target directory for JMX artifacts)
- Cross-tool coordination so the Script Agent can hand off to Execution Agent without redundant configuration

### 3.2 Pipeline Flow

```text
Script Agent  →  validate JMX (jmeter-mcp)  →  commit (github-mcp)  →  
Execution Agent  →  create BlazeMeter test  →  attach JMX  →  run test  →  
Monitoring / Analysis / Report agents
```

Each step persists progress via `task_call_traces` and emits SSE updates to the CopilotKit UI, so a human watching the FlightDeck sees the full pipeline unfold in real time.

---

## 4. Repository-Wide Documentation Refactor

A large sweep across the repository's public documentation, driven by the PerfPilot-Hub rename and the ongoing structural consolidation.

### 4.1 Root `README.md`

- Renamed project from `mcp-perf-suite` to **PerfPilot-Hub** (technical folder names retained)
- Introduced the **aviation-model naming convention** (Pilot, Copilots, FlightDeck, ACARS, Flight Log, Black Box) for discoverability
- Updated repository structure diagram to reflect the current layout
- Refreshed high-level architecture, example workflow, and roadmap sections
- Added a Docker deployment sub-section pointing to `docker/README.md` as the canonical deployment guide

### 4.2 Per-MCP READMEs

Every MCP server README was updated:

- **FastMCP 3.4.x** version note added where relevant
- Clone / setup instructions adjusted for the new `mcp-perf-suite/` sub-folder layout
- Future-enhancement sections revised to reflect completed work and current goals

### 4.3 Artifacts Folder README

`mcp-perf-suite/artifacts/README.md` was rewritten to clearly document:

- The canonical `artifacts/{test_run_id}/` layout
- Sub-folder purposes (`blazemeter/`, `datadog/`, `analysis/`, `reports/`, `charts/`, `jmeter/`)
- Test-comparison folder pattern (`artifacts/comparisons/{comparison_id}/`)

### 4.4 Agent Framework Docs

- `agent-framework/README.md` refreshed to reflect new module names and current MCP list
- `agent-framework/AGENTS.md` updated for the new folder map and specialist agent status
- `agent-framework/sql/README.md` refreshed with all `perfagent_state` tables and provisioning script usage

---

## 5. Docker Full-Stack Refactor

The largest infrastructure change of the month: a full refactor of every Dockerfile in `docker/` plus both full-stack compose files. Delivered on the `features/docker-per-mcp-images` branch.

### 5.1 Overview

The refactor covers **12 Docker images** — 9 FastMCP servers, Playwright MCP, agent backend (dual A2A + AG-UI), and agent frontend — plus the two full-stack orchestration compose files.

Goals:

- Consistent behavior across every image (health, signal handling, users, permissions)
- Simpler compose files (YAML anchors instead of copy-pasted blocks)
- Cross-OS parity (Windows and macOS full-stack files use the same anchor patterns)
- Standalone-friendly (every sub-folder still ships its own compose + env template)

### 5.2 Health Check Standardization

Every FastMCP MCP image now ships a Docker `HEALTHCHECK` directive that probes its `/health` endpoint with `wget -q -O /dev/null` (GET). Playwright MCP probes `/mcp` with `wget --spider` (accepting both HTTP 200 and 406, since MCP is a stateful protocol that returns 406 for bare GETs). Compose files no longer duplicate the health check — the Dockerfile is the single source of truth.

| Image | Endpoint | Probe |
|-------|----------|-------|
| All 9 FastMCP MCPs (`blazemeter`, `datadog`, `jmeter`, `perfanalysis`, `perfreport`, `confluence`, `perfmemory`, `github`, `gateway`) | `/health` | `wget -q -O /dev/null` (GET) |
| `perfpilot-mcp-playwright` | `/mcp` | `wget --spider` (accept 200 or 406) |
| `perfpilot-a2a`, `perfpilot-agui` | `/health` | `curl -f` |
| `perfpilot-ui` | `/` | `wget --spider` |

### 5.3 Non-Root Users, `tini`, and CRLF Handling

**Non-root `app:app` user** — every Python-based image now runs as a dedicated system user (`app:app`, UID/GID auto-assigned, `/usr/sbin/nologin` shell). No image runs as root at execution time. The Playwright image runs as `node` (its base image's default user); the frontend image runs as `app`.

**`tini` as PID 1** — every image installs `tini` and sets it as PID 1 via `ENTRYPOINT ["/usr/bin/tini", "--", ...]`. This provides clean signal forwarding for `docker stop` / `docker compose down` and reaps zombie processes.

**CRLF stripping** — Windows checkouts occasionally introduce `\r` line endings into shell scripts, which breaks POSIX interpretation. Every Dockerfile now runs `sed -i 's/\r$//'` against its entrypoint script during build, guaranteeing POSIX-clean scripts regardless of the developer's OS.

### 5.4 Playwright MCP — Microsoft Official Base Image

The Playwright MCP image was rebuilt on Microsoft's official Playwright base image, removing the requirement to build the `mcp/playwright:latest` base image separately:

- **Base image:** `mcr.microsoft.com/playwright:v1.63.0-noble` (pinned)
- **MCP layer:** `@playwright/mcp@0.0.80` (pinned)
- **Browser scope:** Chromium only (Firefox and WebKit stripped from `/ms-playwright/`)
- **Entrypoint:** custom `entrypoint.sh` that handles cert import, config injection, and MCP CLI launch
- **Config:** baked-in `/app/config.json` (transport, browser mode, output paths)
- **Certificate handling:**
  - Single-variable env pattern: `PLAYWRIGHT_CERT_FILE` (basename or absolute path) + `PLAYWRIGHT_CERT_PASSPHRASE`
  - Wildcard `AutoSelectCertificateForUrls` Chromium policy default (overridable via `PLAYWRIGHT_CERT_AUTO_SELECT_PATTERN`)
  - Certs imported into both `~/.pki/nssdb` and `~/.local/share/pki/nssdb` for Chromium's NSS database
- **Health check:** built into the image (previously the vendor base image shipped no `HEALTHCHECK`)

### 5.5 Agent Frontend — Image Size Optimization

Two changes reduced the agent frontend image size significantly:

1. **`USER app` before `npm ci`** — The user is created and switched to *before* dependency installation, so all files in `/app/node_modules` are owned by `app:app` from creation. This avoids the layer-bloat that a post-install `chown -R app:app /app` would produce (which would duplicate the entire `node_modules` tree in a new layer).
2. **`.dockerignore` recursion fix** — The root `.dockerignore` now uses `**/node_modules/`, `**/.next/`, `**/.next-*/`, and `**/.env.*` patterns to prevent local build artifacts from being copied into the image and then re-created by `npm ci`.

Result: **-1.61 GB** vs the pre-refactor baseline for `perfpilot-ui:latest`.

### 5.6 Full-Stack Compose Files — YAML Anchors

Both `docker/docker-compose-full-windows.yaml` and `docker/docker-compose-full-mac.yaml` were simplified using YAML anchors:

- **`x-artifacts:`** — shared volumes list (artifacts folder, docker sock, etc.) merged into every service via `volumes: *artifacts`
- **`x-mcp-env:`** — shared environment block (deployment mode, log level, artifact root) merged into every MCP service via `<<: *mcp-env`

Impact:

| File | Before | After | Delta |
|------|--------|-------|-------|
| `docker-compose-full-windows.yaml` | 400 lines | 354 lines | -46 |
| `docker-compose-full-mac.yaml` | 400 lines | 365 lines | -35 |

Both files pass `docker compose config --quiet` cleanly. The Mac file additionally preserves its OS-specific bits (`user: "999:999"` and `PGDATA` on Postgres, `user: "0:0"` on Playwright).

### 5.7 Gateway Standalone Defaults

The gateway MCP standalone `docker/gateway-mcp/docker-compose.yml` now provides `${VAR:-default}` values for all 9 upstream `MCP_URL_*` variables (8 FastMCP siblings + Playwright at the bare `/mcp` path). This means the gateway container runs standalone without requiring the user to first populate every URL in `.env`.

`.env` values still win when set — the defaults are only used when the variable is unset.

### 5.8 End-to-End Validation

The full stack was validated end-to-end on Windows:

- All **13 containers healthy** simultaneously
- BlazeMeter MCP verified via the Execution Agent path (agent → gateway → BlazeMeter MCP → BlazeMeter API)
- Chat history persisted across restarts (Postgres + `perfagent_state`)
- Frontend UI serves at `http://localhost:8080/` with full CopilotKit functionality

macOS validation is planned for the next work cycle.

---

## 6. Files Created

| File | Purpose |
|------|---------|
| `mcp-perf-suite/github-mcp/` | New GitHub MCP server (server, config, tools, README) |
| `docker/github-mcp/` | GitHub MCP Dockerfile, docker-compose.yml, .env.example, README.md |
| `docker/playwright-mcp/entrypoint.sh` | Playwright MCP startup script (cert import, config, CLI launch) |
| `docker/playwright-mcp/config/config.json` | Baked-in Playwright MCP transport + browser config |
| `docker/agent-backend/entrypoint.sh` | Agent backend startup script (A2A or AG-UI dispatch via `$@` or `AGENT_SERVER`) |
| `docker/agent-backend/.dockerignore` | Per-image ignore rules (`.env`, `.venv`, `__pycache__`, logs) |
| `docker/agent-frontend/.dockerignore` | Per-image ignore rules |
| `docs/changelogs/CHANGELOG-2026-07.md` | Rotated July 2026 changelog |
| `docs/changelogs/CHANGELOG-2026-08.md` | New August 2026 changelog |
| `agent-framework/backend/services/env_resolver.py` | Shared env / SCM resolver for cross-agent workflows |

## 7. Files Modified

| File | Changes |
|------|---------|
| `README.md` (root) | PerfPilot-Hub rename, aviation-model naming, updated topology, Docker deployment section |
| `docker/blazemeter-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/datadog-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/jmeter-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/perfanalysis-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/perfreport-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/confluence-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/perfmemory-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/github-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/gateway-mcp/Dockerfile` | Baked `HEALTHCHECK`, `tini`, non-root `app:app` user |
| `docker/gateway-mcp/docker-compose.yml` | 9 `MCP_URL_*` defaults added for standalone use |
| `docker/playwright-mcp/Dockerfile` | Rebuilt on `mcr.microsoft.com/playwright:v1.63.0-noble` + `@playwright/mcp@0.0.80`; NSS DB cert import; entrypoint-driven startup; baked `HEALTHCHECK` |
| `docker/playwright-mcp/docker-compose.yml` | Simplified: entrypoint owns runtime flags; cross-OS `user: "0:0"` retained |
| `docker/playwright-mcp/.env.example` | Rewritten around single `PLAYWRIGHT_CERT_FILE` variable |
| `docker/agent-backend/Dockerfile` | Added `bash` + `tini`; non-root `app:app`; inline `HEALTHCHECK` reading `HEALTHCHECK_PORT`; new entrypoint |
| `docker/agent-frontend/Dockerfile` | `USER app` before `npm ci`; `COPY --chown=app:app`; port 8080 hardcoded; baked `HEALTHCHECK` |
| `docker/docker-compose-full-windows.yaml` | YAML anchors (`x-mcp-env`, `x-artifacts`); simplified from 400 → 354 lines |
| `docker/docker-compose-full-mac.yaml` | YAML anchors; simplified from 400 → 365 lines; macOS-specific user + PGDATA retained |
| `.dockerignore` (root) | `**/node_modules/`, `**/.next/`, `**/.next-*/`, `**/.env.*` (recursion fix) |
| `mcp-perf-suite/blazemeter-mcp/*` | Test provisioning tools + workspace / project resolvers |
| `agent-framework/agents/execution-agent/agent.py` | New-JMX pipeline: create BlazeMeter test + attach JMX + run |
| `agent-framework/agents/script-agent/agent.py` | Commit generated JMX via github-mcp before handoff |
| `agent-framework/README.md` | Refreshed module map + current MCP list |
| `agent-framework/AGENTS.md` | Refreshed folder map + specialist agent status |
| `agent-framework/sql/README.md` | All `perfagent_state` tables + provisioning script docs |
| `mcp-perf-suite/artifacts/README.md` | Canonical layout + sub-folder purpose clarification |
| Every `mcp-perf-suite/*/README.md` | FastMCP 3.4.x note + updated clone / setup instructions |

---

## Previous Changelogs

| Month | Link | Highlights |
|-------|------|------------|
| August 2026 | [CHANGELOG-2026-08.md](docs/changelogs/CHANGELOG-2026-08.md) | Repository Restructuring, Agent Framework Docker Containers, HITL Approval Flow, User Identity Resolution, Task Management UI, Notifications + Reporting Agent Scaffolds |
| July 2026 | [CHANGELOG-2026-07.md](docs/changelogs/CHANGELOG-2026-07.md) | Loop Engineering, LLM Round-Trip Reduction, Context/Token Tracking, Compaction, Token Ledger |
| June 2026 | [CHANGELOG-2026-06.md](docs/changelogs/CHANGELOG-2026-06.md) | PerfPilot Agents Framework, Orchestrator, Execution Agent, Playwright Integration, CopilotKit Frontend |
| May 2026 | [CHANGELOG-2026-05.md](docs/changelogs/CHANGELOG-2026-05.md) | PerfMemory Taxonomy, EntraID Correlation Engine, SharePoint MCP, FastMCP v3 Migration, PerfPilot Hub |
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills Migration, Cursor Subagents, PerfMemory MCP + AGE Graph, MS Teams MCP, KPI Analysis, JMeter Script Validator |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL Editing Tools, Correlation Analysis v0.6/v0.7, AI-Assisted Debugging, Artifact Path Alignment, BlazeMeter Shared Folders |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI Adapter, HAR Adapter, Centralized SLA Config, JMeter Log Analysis, Bottleneck Analyzer v0.2 |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-Assisted Report Revision, Datadog Dynamic Limits, Report Enhancements, New Charts |

---

*Last Updated: September 27, 2026*
