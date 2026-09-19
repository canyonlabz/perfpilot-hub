# 🗄️ perfmem-pgvector-age

PostgreSQL 18 database image with the `pgvector` extension (vector similarity
search) and Apache AGE (graph database extension). Provides the persistent
backend for the PerfMemory MCP and the agent state store (`perfagent_state`
database).

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfmem-pgvector-age:latest` |
| 📦 Container | `perfmem-pgvector-age` |
| 🌐 Port | `5432` |
| 🐘 Base image | `pgvector/pgvector:pg18` |
| 🕸️ Graph extension | Apache AGE `PG18/v1.7.0-rc0` (compiled from source) |
| 💾 Data location | `docker/data/pgvectordb/` (host bind mount) |

---

## 🎯 Purpose

`perfmem-pgvector-age` is the shared PostgreSQL instance that hosts two
logical databases:

- 🧠 **`perfmemory`** — vector embeddings + graph edges for the PerfMemory MCP
- 🤖 **`perfagent_state`** — LangGraph checkpoint state + short-term conversation
  memory for the agent backend (`perfpilot-a2a` + `perfpilot-agui`)

Both databases live in the same PostgreSQL cluster and share the same user
account (`perfadmin` by default).

---

## 🚀 Quick start (standalone)

Run this database standalone (without the rest of the stack) in three steps:

```bash
# 1. Copy the environment template
cd docker/postgresql/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Edit .env — at minimum change POSTGRES_PASSWORD from `changeme`

# 3. Build and start the container
docker compose up --build -d
```

⚠️ **macOS users:** before running `docker compose up`, uncomment two
settings in `docker/postgresql/docker-compose.yml` — see [§🍎 macOS-specific setup](#-macos-specific-setup)
for details.

Verify it's healthy:

```bash
docker compose ps
#             NAME                    STATUS
# perfmem-pgvector-age        Up 30s (healthy)
```

Connect from your host to verify:

```bash
psql -h localhost -p 5432 -U perfadmin -d perfmemory
# Enter your POSTGRES_PASSWORD when prompted

# Verify the pgvector extension:
perfmemory=> CREATE EXTENSION IF NOT EXISTS vector;
perfmemory=> SELECT '[1,2,3]'::vector;

# Verify the AGE extension:
perfmemory=> CREATE EXTENSION IF NOT EXISTS age;
perfmemory=> LOAD 'age';
```

Shut down the container when finished:

```bash
docker compose down
# Data persists on disk under docker/data/pgvectordb/
```

---

## 🔑 Required credentials

You must set a database password. The image ships with the following
defaults — override in `.env` for anything beyond local development:

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` | `perfadmin` | Superuser account created on first launch |
| `POSTGRES_PASSWORD` | `changeme` | **Change this** — never use the default in shared or production environments |
| `POSTGRES_DB` | `perfmemory` | Initial database created on first launch |
| `POSTGRES_PORT` | `5432` | Host-side port mapping (container-side is always `5432`) |

> 🔒 **Security note** — `.env` contains sensitive credentials and is
> gitignored by default. It must not be committed. Only `.env.example`
> (with default placeholder values) is tracked in the repository.

The `perfagent_state` database used by the agent backend is created on
first agent connection using the same `POSTGRES_USER` credentials.

---

## 💾 Data persistence

PostgreSQL data lives on a **host bind mount** — not a Docker-managed named
volume. The compose file mounts:

```
../data/pgvectordb  →  /var/lib/postgresql/18/docker
```

Data survives `docker compose down` (both standalone and full-stack) because
it's stored on the host filesystem under `docker/data/pgvectordb/`.

**Why a bind mount, not a named volume?**
PostgreSQL 18's `initdb` cannot chown the root of a Docker-managed named
volume — the volume root is owned by root, and `initdb` refuses to run there.
Bind mounts give us direct control over ownership on both Windows and macOS.

**Resetting the database entirely:**

```bash
docker compose down
rm -rf ../data/pgvectordb/*     # PowerShell: Remove-Item ..\data\pgvectordb\* -Recurse -Force
docker compose up --build -d    # initdb will recreate the cluster from scratch
```

---

## 🍎 macOS-specific setup

Docker Desktop for Mac uses **VirtioFS** for bind mounts, which introduces
two ownership quirks that don't exist on Windows / WSL2. Both are handled by
uncommenting two lines in `docker/postgresql/docker-compose.yml`:

### 1. `user: "999:999"`

VirtioFS ships the mounted volume with an ownership that doesn't match the
`postgres` UID inside the image. Forcing the container process to run as
UID 999 (the image's `postgres` user) is the working combination.

### 2. `PGDATA: /var/lib/postgresql/18/docker/pgdata`

`initdb` cannot chown the bind-mount root under VirtioFS. Pointing `PGDATA`
at a sub-directory (`.../docker/pgdata`) lets `initdb` create and own its
own folder instead of the mount root.

Both settings are commented in the standalone compose file — uncomment them
if you're running on macOS. The full-stack `docker-compose-full-mac.yaml`
sets both unconditionally.

Windows / WSL2 does **not** need either setting.

---

## ⚙️ Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | Currently only affects log verbosity of ancillary services; PostgreSQL itself logs to stdout |
| `POSTGRES_USER` | `perfadmin` | Superuser account |
| `POSTGRES_PASSWORD` | `changeme` | Superuser password |
| `POSTGRES_DB` | `perfmemory` | Initial database name |
| `POSTGRES_PORT` | `5432` | Host-side port mapping |

**macOS-only** (uncomment in `docker-compose.yml`):

| Setting | Value | Purpose |
|---|---|---|
| `user` | `"999:999"` | Match `postgres` UID inside the image |
| `PGDATA` | `/var/lib/postgresql/18/docker/pgdata` | Sub-directory inside the bind mount |

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` using `pg_isready`:

- Interval: 10 seconds
- Timeout: 5 seconds
- Retries: 5

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfmem-pgvector-age | jq

# Manual probe:
docker exec perfmem-pgvector-age pg_isready -U perfadmin -d perfmemory
```

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container exits immediately on macOS with `initdb: could not create directory` | Missing `user` and/or `PGDATA` settings on Mac | Uncomment both in `docker-compose.yml` — see [§🍎 macOS-specific setup](#-macos-specific-setup) |
| Container exits immediately on Windows with `permission denied` on the data folder | WSL2 filesystem permissions issue | Verify Docker Desktop has Windows Subsystem for Linux 2 enabled and the repo path is accessible from WSL2 |
| Container repeatedly restarts, logs show `role "perfadmin" does not exist` | `POSTGRES_USER` changed after first initialization | Data is initialized once on empty `PGDATA`. To change the superuser name, delete `docker/data/pgvectordb/` and let `initdb` recreate the cluster |
| `pg_isready` returns "accepting connections" but pgvector or AGE queries fail | Extensions not created in the target database | Run `CREATE EXTENSION IF NOT EXISTS vector;` and `CREATE EXTENSION IF NOT EXISTS age;` — the extensions are installed but not auto-loaded into every database |
| Client applications fail with `password authentication failed` | Wrong `POSTGRES_PASSWORD` in `.env` or mismatched between DB and app configs | Verify the value matches between `docker/postgresql/.env` and any app `.env` files (e.g., `docker/perfmemory-mcp/.env`, `docker/agent-backend/.env`) |
| Data lost after `docker compose down -v` | The `-v` flag removed anonymous / named volumes | Data lives in `docker/data/pgvectordb/` — a bind mount — and is not affected by `docker compose down -v`. Verify the folder still exists on the host. |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfmem-pgvector-age
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA)
- 📂 [`docker/perfmemory-mcp/README.md`](../perfmemory-mcp/README.md) — MCP that consumes the `perfmemory` database
- 📂 [`docker/agent-backend/README.md`](../agent-backend/README.md) — services that consume the `perfagent_state` database
- 📂 [`docker/data/README.md`](../data/README.md) — bind-mount data folder reference
- 🌐 [pgvector documentation](https://github.com/pgvector/pgvector) — vector similarity search extension
- 🌐 [Apache AGE documentation](https://age.apache.org/) — graph database extension for PostgreSQL
