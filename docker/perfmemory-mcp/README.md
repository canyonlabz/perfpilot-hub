# 🧠 perfpilot-mcp-perfmemory

PerfMemory MCP server — a lessons-learned memory layer for performance-testing
work. Stores debug sessions, past fixes, and searchable knowledge in a
PostgreSQL 18 database with `pgvector` (embeddings) and Apache AGE (graph)
extensions.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-perfmemory:latest` |
| 📦 Container | `perfpilot-mcp-perfmemory` |
| 🌐 Port | `8116` |
| 🔗 Endpoint | `http://localhost:8116/perfpilot-mcp-perfmemory/mcp` |
| 🐍 Base image | `python:3.12-slim` (+ `libpq-dev` for PostgreSQL client) |
| 🗄️ Database dependency | `perfmem-pgvector-age` (PostgreSQL 18 + pgvector + Apache AGE) |
| 📂 Source code | [`mcp-perf-suite/perfmemory-mcp/`](../../mcp-perf-suite/perfmemory-mcp/) |

---

## 🎯 Capabilities

The PerfMemory MCP exposes a set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) build up and query institutional
knowledge across performance-testing engagements. Core capabilities include:

- 💾 **Store** debug sessions, attempted fixes, and outcomes tagged by symptom and root cause
- 🔍 **Search** past fixes by symptom, error signature, or free-text description (semantic + keyword hybrid)
- 📥 **Ingest** existing lessons-learned documents (Markdown, debug manifests, incident post-mortems) into the memory layer
- 🕸️ **Graph relationships** between symptoms, causes, and fixes via Apache AGE
- 🏷️ **Taxonomy classification** — group entries by pre-defined categories from `taxonomy.yaml`
- 🎯 **Retrieval-augmented workflows** — feed relevant past fixes into the agent's context before starting a new debug session

For the complete tool inventory, see the
[`perfmemory-mcp` source folder](../../mcp-perf-suite/perfmemory-mcp/).

---

## 🚀 Quick start (standalone)

⚠️ **Database dependency.** This MCP requires a running PostgreSQL 18 +
`pgvector` + Apache AGE instance. In standalone mode you must **either**:

- Bring up the [`perfpilot-mcp-postgresql`](../postgresql/README.md) container first, or
- Point `POSTGRES_HOST` at an external Postgres instance with the same extensions installed

Then run this MCP standalone in three steps:

```bash
# 1. Copy the environment template
cd docker/perfmemory-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env with embedding provider credentials and Postgres connection
#    details (see next sections)

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#              NAME                     STATUS
# perfpilot-mcp-perfmemory     Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8116/perfpilot-mcp-perfmemory/mcp
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

PerfMemory needs two credential sets: one for the **embedding provider** (used
to embed stored content for semantic search) and one for the **PostgreSQL**
database connection.

### 🤖 Embedding provider

Choose one of three providers via `EMBEDDING_PROVIDER`:

#### `openai`

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key *(sensitive — must not be shared or committed)* |
| `OPENAI_EMBEDDING_MODEL` | Embedding model name (default: `text-embedding-3-small`) |

#### `azure_openai`

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key *(sensitive — must not be shared or committed)* |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint, e.g. `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment name for the embedding model |
| `AZURE_OPENAI_API_VERSION` | API version, e.g. `2024-02-15-preview` |

#### `ollama` (local, no cloud dependency)

| Variable | Purpose |
|---|---|
| `OLLAMA_BASE_URL` | Ollama server URL. Inside Docker use `http://host.docker.internal:11434` |
| `OLLAMA_EMBEDDING_MODEL` | Embedding model name (default: `nomic-embed-text`) |

### 🗄️ PostgreSQL connection

| Variable | Standalone default | Full-stack value | Purpose |
|---|---|---|---|
| `POSTGRES_HOST` | `localhost` | `perfmem-pgvector-age` | Database host |
| `POSTGRES_PORT` | `5432` | `5432` | Database port |
| `POSTGRES_DB` | `perfmemory` | `perfmemory` | Database name |
| `POSTGRES_USER` | `perfadmin` | `perfadmin` | Database user |
| `POSTGRES_PASSWORD` | *(required)* | *(required)* | Database password *(sensitive)* |
| `POSTGRES_SSLMODE` | `prefer` | `prefer` | SSL mode (`disable`, `prefer`, `require`, `verify-ca`, `verify-full`) |
| `POSTGRES_SSLROOTCERT` | *(unset)* | *(unset)* | Optional path to CA cert for `verify-ca` / `verify-full` |

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with empty values) is tracked in the repository.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `EMBEDDING_PROVIDER` | `openai` | One of `openai`, `azure_openai`, `ollama` |
| `OPENAI_*`, `AZURE_OPENAI_*`, `OLLAMA_*` | *(varies)* | See Embedding provider block above |
| `POSTGRES_*` | *(see table above)* | See PostgreSQL connection block above |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | Ingestion sources (Markdown lessons-learned docs, debug manifests) and any export destinations |

The `artifacts/` folder is shared across every PerfPilot MCP, so PerfMemory
can ingest content produced by upstream MCPs and expose retrieved memory to
downstream agent workflows.

---

## 🗂️ Baked-in configuration

The PerfMemory MCP ships two configuration files baked into the image at
build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/perfmemory-mcp/config/config.yaml` | `/app/perfmemory-mcp/config.yaml` | Vector store, embedding, and search settings |
| `docker/perfmemory-mcp/config/taxonomy.yaml` | `/app/perfmemory-mcp/taxonomy.yaml` | Category hierarchy for classifying stored entries |

The `taxonomy.yaml` file is the primary place you'll customize this MCP for
your organization's categorization scheme. Edit it in the repo and rebuild
the image to take effect.

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its own endpoint
every 30 seconds. The first probe fires after a 20-second grace period (image
cold start).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-perfmemory | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/perfmemory-mcp/.env`
3. Rebuild: `docker compose up --build -d`

The Dockerfile automatically installs the CA into the OS trust store and points
`SSL_CERT_FILE` + `REQUESTS_CA_BUNDLE` at it. This is required for cloud
embedding providers (OpenAI, Azure OpenAI) when the client is behind an
intercepting proxy. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container restart loop with `psycopg2.OperationalError: could not connect to server` | Postgres not running, wrong host, or wrong credentials | Verify the [`perfpilot-mcp-postgresql`](../postgresql/README.md) container is up and `.env` values match |
| Container restart loop with `psycopg2.OperationalError: FATAL: database "perfmemory" does not exist` | Postgres running but database not initialized | Restart the postgres container; the entrypoint auto-creates the `perfmemory` database on first launch |
| Embedding calls fail with `401 Unauthorized` | Wrong API key for the selected `EMBEDDING_PROVIDER` | Regenerate the key and update `.env` |
| Embedding calls succeed but semantic search returns unrelated results | Wrong embedding model or mismatched dimensions between stored and query embeddings | Verify `OPENAI_EMBEDDING_MODEL` (or Azure / Ollama equivalent) matches the model used to store the original entries; regenerate embeddings if needed |
| Ollama connection refused | Wrong `OLLAMA_BASE_URL` for Docker networking | Use `http://host.docker.internal:11434`, not `http://localhost:11434`, from inside the container |
| Build fails with `SSL certificate problem` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-perfmemory/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30s after `up` | Normal — health check is still in its grace period | Wait ~20–30s and try again; `docker compose ps` will show `(healthy)` when ready |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-perfmemory
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA)
- 📂 [`mcp-perf-suite/perfmemory-mcp/`](../../mcp-perf-suite/perfmemory-mcp/) — MCP source code + tool inventory
- 🗄️ [`docker/postgresql/README.md`](../postgresql/README.md) — required PostgreSQL 18 + pgvector + AGE backend
- 🌐 [pgvector documentation](https://github.com/pgvector/pgvector) — vector similarity search extension
- 🌐 [Apache AGE documentation](https://age.apache.org/) — graph database extension for PostgreSQL
