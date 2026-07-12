# Installing Apache AGE with pgvector

Apache AGE (A Graph Extension) adds openCypher graph query support to PostgreSQL. PerfMemory uses AGE alongside pgvector to provide a knowledge graph layer for cross-project issue discovery via graph traversal.

> **Prerequisites:**
> - PostgreSQL 18+ with pgvector already installed (see [pgvector Installation Guide](pgvector_installation_guide.md))
> - A Docker-compatible container runtime installed and running
>   - **Rancher Desktop** (enterprise/team use): https://rancherdesktop.io/
>   - **Docker Desktop** (personal use): https://www.docker.com/products/docker-desktop/

---

## Step 1: Build the Docker Image

PerfMemory provides a custom Dockerfile that extends the `pgvector/pgvector:pg18` base image with Apache AGE compiled from source.

### Standard Build

Copy the example Dockerfile and build:

```bash
cd docker/
cp Dockerfile.pgvector-age.example Dockerfile.pgvector-age
```

Review `Dockerfile.pgvector-age` to confirm the AGE version:

```dockerfile
ARG AGE_VERSION=PG18/v1.7.0-rc0
```

> **Note:** Apache AGE for PG18 is currently at Release Candidate status. Check [https://github.com/apache/age/tags](https://github.com/apache/age/tags) for the latest PG18-compatible tag.

Build and start the container:

```bash
docker compose -f docker-compose-<os-version>.yaml up -d --build
```

### Corporate Environment Build (TLS/SSL Certificate Issues)

If you are behind a corporate TLS inspection proxy (e.g., Zscaler, Netskope), the `git clone` step during the Docker build may fail with:

```
fatal: unable to access 'https://github.com/apache/age.git/': server certificate verification failed. CAfile: none CRLfile: none
```

This happens because the container does not have your organization's root and intermediate CA certificates in its trust store.

**To fix this:**

1. Locate your organization's CA bundle PEM file on your host machine (e.g., `~/.ssl/ca-bundle.pem`)

2. Copy the CA bundle into the `docker/` directory:

   ```bash
   cp ~/.ssl/ca-bundle.pem /<root-to-repo>/mcp-perf-suite/docker/ca-bundle.pem
   ```

3. Update `Dockerfile.pgvector-age` to copy the bundle into the container and update the trust store. Add `ca-certificates` to the `apt-get install` list and add the `COPY` / `update-ca-certificates` steps before the `git clone`:

   ```dockerfile
   FROM pgvector/pgvector:pg18

   ARG AGE_VERSION=PG18/v1.7.0-rc0

   RUN apt-get update && apt-get install -y \
       build-essential \
       git \
       flex \
       bison \
       ca-certificates \
       postgresql-server-dev-18 \
       && rm -rf /var/lib/apt/lists/*

   COPY ca-bundle.pem /usr/local/share/ca-certificates/corporate-ca-bundle.crt
   RUN update-ca-certificates

   RUN git clone --branch ${AGE_VERSION} https://github.com/apache/age.git /tmp/age && \
       cd /tmp/age && \
       make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config && \
       make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config install && \
       rm -rf /tmp/age

   RUN echo "shared_preload_libraries = 'age'" >> /usr/share/postgresql/18/postgresql.conf.sample
   ```

4. Build the image:

   ```bash
   docker compose -f docker-compose-<os-version>.yaml up -d --build
   ```

5. After a successful build, you can remove the CA bundle from the `docker/` directory — it is baked into the Docker image at build time:

   ```bash
   rm docker/ca-bundle.pem
   ```

> **Important:** Both `Dockerfile.pgvector-age` and `ca-bundle.pem` are gitignored since they contain environment-specific configuration. Only `Dockerfile.pgvector-age.example` is committed to the repository.

### SSL for Embedding API Calls

If your embedding provider (OpenAI, Azure OpenAI) is also accessed through a corporate proxy, Python's HTTP client needs to trust the same CA bundle. Add the following to your `perfmemory-mcp/.env` file, pointing to the CA bundle on your **host machine** (not the Docker container):

```bash
# SSL Certificate Configuration
SSL_CERT_FILE=/path/to/your/ca-bundle.pem
```

This sets the `SSL_CERT_FILE` environment variable that Python's `ssl` module and `httpx`/`requests` libraries use to verify TLS certificates for outbound API calls.

---

## Step 2: Verify the Container is Running

```bash
docker ps
```

You should see a container named `perfmem-pgvector-age` running.

---

## Step 3: Connect and Enable Extensions

Connect to the database:

```bash
psql -h localhost -U perfadmin -d perfmemory -p 5432
```

Enable both extensions (pgvector should already be enabled if migrating from an existing database):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
```

Verify both extensions are installed:

```sql
SELECT * FROM pg_extension;
```

You should see `vector` and `age` listed:

```
  oid  | extname | extowner | extnamespace | extrelocatable | extversion | extconfig | extcondition
-------+---------+----------+--------------+----------------+------------+-----------+--------------
 13579 | plpgsql |       10 |           11 | f              | 1.0        |           |
 16389 | vector  |       10 |         2200 | t              | 0.8.2      |           |
 16XXX | age     |       10 |        16XXX | f              | 1.7.0      |           |
```

---

## Step 4: Apply the PerfMemory Schema

If this is a fresh install, apply the relational schema first:

```bash
# For OpenAI or Azure OpenAI embeddings (1536 dimensions)
psql -h localhost -U perfadmin -d perfmemory -f perfmemory-mcp/sql/schema/schema_openai.sql

# For Ollama nomic-embed-text embeddings (768 dimensions)
psql -h localhost -U perfadmin -d perfmemory -f perfmemory-mcp/sql/schema/schema_ollama.sql
```

---

## Step 5: Create the Knowledge Graph

Apply the graph schema to create the `perf_knowledge` graph and its vertex labels:

```bash
psql -h localhost -U perfadmin -d perfmemory -f perfmemory-mcp/sql/graph/001_create_graph.sql
```

### Verify the Graph

```sql
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Check the graph exists
SELECT * FROM ag_catalog.ag_graph;

-- Check the vertex and edge labels
SELECT * FROM ag_catalog.ag_label;
```

You should see the `perf_knowledge` graph and labels for `Attempt`, `Project`, `ErrorPattern`, and `FixPattern`.

---

## Step 6: Seed the Graph from Existing Data (Optional)

If you have existing debug sessions and attempts in the relational tables and are adding the graph layer after the fact, run the seed script to backfill the graph:

```bash
psql -h localhost -U perfadmin -d perfmemory -f perfmemory-mcp/sql/graph/002_seed_graph_from_existing_data.sql
```

> **Important:** This script should only be run **once**. It uses `CREATE` (not `MERGE`) for Attempt nodes, so running it again will produce duplicates. For fresh installs where no data exists yet, skip this step — the MCP tools will create graph nodes at ingestion time.

---

## Step 7: Enable the Graph Layer in Configuration

Edit `perfmemory-mcp/config.yaml` and set `graph.enabled` to `true`:

```yaml
graph:
  enabled: true
  graph_name: perf_knowledge
  vector_weight: 0.6
  graph_weight: 0.4
  embedding_edge_threshold: 0.82
  max_embedding_edges: 3
```

See `perfmemory-mcp/config.example.yaml` for the full configuration reference.

---

## Graph Structure Reference

For details on the graph schema (vertex labels, edge labels, properties), see [`perfmemory-mcp/sql/graph/README.md`](../perfmemory-mcp/sql/graph/README.md).

| Vertex Label | Description |
|---|---|
| `Attempt` | Maps 1:1 to a `debug_attempts` row |
| `Project` | One per distinct `system_under_test` |
| `ErrorPattern` | One per distinct `(error_category, response_code)` pair |
| `FixPattern` | One per distinct `(fix_type, component_type)` pair |

| Edge Label | Direction | Description |
|---|---|---|
| `BELONGS_TO` | Attempt -> Project | Every attempt belongs to a project |
| `HAS_ERROR` | Attempt -> ErrorPattern | Links attempt to its error classification |
| `FIXED_BY` | Attempt -> FixPattern | Links resolved attempts to the fix that worked |
| `SIMILAR_TO` | Attempt -> Attempt | Cross-project structural or embedding-based similarity |

---

## Troubleshooting

- **MacBook-specific issues** (permissions, UID/GID, PG18 startup): See [MacBook Troubleshooting Guide](troubleshooting/pgvector-fix-on-macbook.md)
- **Rancher Desktop issues** (mount types, file sharing, `chown` errors): See [Rancher Desktop Troubleshooting Guide](troubleshooting/rancher-desktop-fix.md)
- **TLS certificate errors during Docker build**: See [Corporate Environment Build](#corporate-environment-build-tlsssl-certificate-issues) above

