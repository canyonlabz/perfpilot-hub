# 📚 perfpilot-mcp-confluence

Confluence MCP server — publish performance-test reports and comparison
analyses to Confluence Cloud (v2 API) or Confluence Server / Data Center
(v1 API, on-prem).

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-confluence:latest` |
| 📦 Container | `perfpilot-mcp-confluence` |
| 🌐 Port | `8115` |
| 🔗 Endpoint | `http://localhost:8115/perfpilot-mcp-confluence/mcp` |
| 🐍 Base image | `python:3.12-slim` |
| 📂 Source code | [`mcp-perf-suite/confluence-mcp/`](../../mcp-perf-suite/confluence-mcp/) |

---

## 🎯 Capabilities

The Confluence MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) publish PerfReport outputs directly
to a Confluence space. Core capabilities include:

- 📄 **Page creation** — create new pages under a parent, populate with report Markdown
- ✏️ **Page updates** — update existing pages with revised content while preserving page history
- 🖼️ **Attachment uploads** — upload chart PNGs, comparison charts, and referenced artifacts as page attachments
- 📁 **Space and page discovery** — list spaces, browse page trees, resolve page IDs by title
- 🔀 **Cloud and on-prem support** — both Confluence Cloud (v2 REST API) and on-prem Confluence Server / Data Center (v1 REST API) with a single MCP

For the complete tool inventory, see the
[`confluence-mcp` source folder](../../mcp-perf-suite/confluence-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template
cd docker/confluence-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env with either the Cloud (V2) OR on-prem (V1) credential
#    block — you do not need both.

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#              NAME                     STATUS
# perfpilot-mcp-confluence     Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8115/perfpilot-mcp-confluence/mcp
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

The Confluence MCP supports **two authentication modes** — choose one based on
your Confluence deployment.

### 🌐 Confluence Cloud (v2 API)

For instances hosted at `*.atlassian.net`. Obtain an API token from
[id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).

| Variable | Purpose |
|---|---|
| `CONFLUENCE_V2_BASE_URL` | Your Confluence Cloud base URL, e.g. `https://your-org.atlassian.net/wiki` |
| `CONFLUENCE_V2_USER` | Your Atlassian account email address |
| `CONFLUENCE_V2_API_TOKEN` | API token *(sensitive — must not be shared or committed)* |

### 🏢 Confluence Server / Data Center (v1 API, on-prem)

For self-hosted Confluence instances. Obtain a Personal Access Token (PAT)
from your user profile: **Profile → Settings → Personal Access Tokens**.

| Variable | Purpose |
|---|---|
| `CONFLUENCE_V1_BASE_URL` | Your on-prem Confluence base URL, e.g. `https://confluence.yourcompany.com` |
| `CONFLUENCE_V1_USER` | Your on-prem Confluence username |
| `CONFLUENCE_V1_PAT` | Personal Access Token *(sensitive — must not be shared or committed)* |

Add the appropriate block to `docker/confluence-mcp/.env` (created from
`.env.example`). Only populate the block that matches your deployment — leave
the other block empty.

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with empty values) is tracked in the repository.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `CONFLUENCE_V2_*` | *(one block required)* | See Cloud (v2) credentials above |
| `CONFLUENCE_V1_*` | *(one block required)* | See on-prem (v1) credentials above |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Reads reports and chart PNGs produced by PerfReport for publishing |

The `artifacts/` folder is shared across every PerfPilot MCP, so the Confluence
MCP publishes PerfReport outputs directly from the shared tree.

---

## 🗂️ Baked-in configuration

The Confluence MCP ships one configuration file baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/confluence-mcp/config/config.yaml` | `/app/confluence-mcp/config.yaml` | Pagination limits, retry policy |

---

## 🤝 Shared configuration (Independence Contract)

The Confluence MCP consumes one whitelisted configuration file from a
sibling MCP:

| Source (in repo) | Destination (in image) | Reason |
|---|---|---|
| `docker/perfreport-mcp/config/chart_schema.yaml` | `/app/perfreport-mcp/chart_schema.yaml` | Confluence needs the same chart schema PerfReport used to generate images so it can render the correct chart types on the destination page |

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
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-confluence | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/confluence-mcp/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile automatically installs the CA into the OS trust store and points
`SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` at it. On-prem Confluence instances
behind a corporate proxy typically require this. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Tools return `401 Unauthorized` | Wrong credential block populated or invalid token | Verify the correct V1 or V2 block is filled in for your deployment; regenerate the API token / PAT if needed |
| Tools return `404 Not Found` on space or page lookup | Wrong `BASE_URL` or missing `/wiki` suffix (Cloud) | Cloud URLs must end in `/wiki`; on-prem URLs typically do not |
| Container restart loop with `KeyError` | Neither V1 nor V2 block populated | Add credentials for either Cloud (v2) or on-prem (v1) to `docker/confluence-mcp/.env` |
| Build fails with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-confluence/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |
| Published pages have broken chart images | Chart PNGs not uploaded as attachments before page content references them | Verify the PerfReport output includes both the report Markdown and the chart PNG files under `artifacts/{test_run_id}/charts/` |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-confluence
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, Independence Contract)
- 📂 [`mcp-perf-suite/confluence-mcp/`](../../mcp-perf-suite/confluence-mcp/) — MCP source code + tool inventory
- 📂 [`docker/perfreport-mcp/README.md`](../perfreport-mcp/README.md) — upstream MCP that produces reports for publishing
- 🌐 [Confluence Cloud REST API v2](https://developer.atlassian.com/cloud/confluence/rest/v2/) — official API reference
- 🌐 [Confluence Server REST API v1](https://developer.atlassian.com/server/confluence/confluence-server-rest-api/) — official API reference
