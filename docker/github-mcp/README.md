# 🐙 perfpilot-mcp-github

GitHub MCP server — interact with GitHub repositories, issues, pull requests,
and workflows to support performance-testing workflows that need version-control
context (e.g., linking a test run to a specific PR or commit).

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-github:latest` |
| 📦 Container | `perfpilot-mcp-github` |
| 🌐 Port | `8118` |
| 🔗 Endpoint | `http://localhost:8118/perfpilot-mcp-github/mcp` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/github-mcp/`](../../mcp-perf-suite/github-mcp/) |

---

## 🎯 Capabilities

The GitHub MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) query and update GitHub resources
in the context of a performance-testing workflow. Core capabilities include:

- 📂 **Repository queries** — resolve repository metadata, default branch, and recent activity
- 🔍 **Commit lookups** — fetch commit metadata, diffs, and author details for correlating test runs to code changes
- 🎯 **Issue tracking** — read, create, and update issues (e.g., "open a bug for a discovered regression")
- 🔄 **Pull request context** — read PR titles, descriptions, review status, and file lists
- 📋 **Workflow / Actions integration** — inspect workflow runs and job status
- 🏷️ **Tag and release lookups** — resolve version tags and release notes for a specific test target

For the complete tool inventory, see the
[`github-mcp` source folder](../../mcp-perf-suite/github-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template
cd docker/github-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env with your GitHub Personal Access Token

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#            NAME                   STATUS
# perfpilot-mcp-github         Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8118/perfpilot-mcp-github/mcp
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

A **GitHub Personal Access Token (PAT)** is required. Create one at
[github.com/settings/tokens](https://github.com/settings/tokens).

- **Fine-grained PAT** (recommended) — scope to specific repositories and
  minimum required permissions (typically `Contents: Read`, `Issues: Read/Write`,
  `Pull requests: Read`).
- **Classic PAT** — use the `repo` scope for full repository access. Prefer
  fine-grained PATs unless your target repositories require classic scopes.

| Variable | Purpose |
|---|---|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT *(sensitive — must not be shared or committed)*. `GITHUB_TOKEN` is accepted as a fallback variable name for compatibility with GitHub Actions workflows. |

Add it to `docker/github-mcp/.env` (created from `.env.example`).

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with empty values) is tracked in the repository.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | *(required)* | See above |
| `GITHUB_TOKEN` | *(fallback)* | Accepted as an alias for `GITHUB_PERSONAL_ACCESS_TOKEN` |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Optional output destination for exported issues, PR summaries, or workflow logs |

The `artifacts/` folder is shared across every PerfPilot MCP, so GitHub-related
exports end up in the same tree that PerfAnalysis and PerfReport read from
downstream.

---

## 🗂️ Baked-in configuration

The GitHub MCP ships one configuration file baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/github-mcp/config/config.yaml` | `/app/github-mcp/config.yaml` | API pagination limits, retry policy, default owner/repo hints |

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its own endpoint
every 30 seconds. The first probe fires after a 20-second grace period (image
cold start).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-github | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/github-mcp/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile automatically installs the CA into the OS trust store and points
`SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` at it. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Tools return `401 Unauthorized` | Missing, expired, or revoked PAT | Regenerate a PAT at [github.com/settings/tokens](https://github.com/settings/tokens) and update `.env` |
| Tools return `403 Forbidden` | PAT lacks the required scopes for the requested operation | For fine-grained PATs, add the necessary repository permissions; for classic PATs, use the `repo` scope |
| Tools return `404 Not Found` on a repository or issue | Repository is private and PAT does not have access, or wrong owner/repo path | Verify PAT scope covers the target repository and the owner/name in the request are correct |
| Rate-limit errors (`403` with `x-ratelimit-remaining: 0`) | GitHub API rate limits hit | Wait for the reset window (headers include reset time); consider using an authenticated PAT (5000 req/hr) if using unauthenticated calls |
| Build fails with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-github/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-github
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA)
- 📂 [`mcp-perf-suite/github-mcp/`](../../mcp-perf-suite/github-mcp/) — MCP source code + tool inventory
- 🌐 [GitHub REST API documentation](https://docs.github.com/en/rest) — official API reference
- 🔑 [GitHub PAT management](https://github.com/settings/tokens) — create and manage tokens
- 📄 [Fine-grained vs classic PATs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) — guidance on which to use
