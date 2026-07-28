# 🧠📚 PerfMemory MCP Server

Welcome to the PerfMemory MCP Server! 🚀  
This is a Python-based MCP server built with **FastMCP 2.0** that introduces a persistent "memory layer" for performance testing — enabling AI agents like Claude/Cursor to learn from past debugging sessions and apply those lessons to future JMeter script creation and troubleshooting.

Powered by a **PostgreSQL + pgvector + Apache AGE** database with HNSW indexing and a knowledge graph, PerfMemory MCP stores structured debug sessions, failed attempts, and successful resolutions, allowing agents to query semantically similar issues before taking action. By leveraging embeddings, vector search, and **graph traversal across projects**, it transforms historical "lessons learned" into actionable intelligence — reducing trial-and-error and accelerating script fixes.

---

## ✨ Features

* **🔍 Semantic Similarity Search**: Find past debug attempts that match a current symptom using vector cosine similarity — no exact keyword match needed.
* **🕸️ Knowledge Graph (Apache AGE)**: Cross-project issue discovery via graph traversal — find related fixes even when symptoms are phrased differently or belong to different projects.
* **📝 Debug Session Tracking**: Record the full story of a debug workflow — from the first error through every attempt to the final resolution.
* **🧩 Structured Lessons Learned**: Each attempt captures the symptom, diagnosis, fix, error category, severity, and outcome — not just freeform text.
* **✅ Confidence Signals**: Track which fixes have been human-verified (`is_verified`) and how many times a fix has been reused successfully (`confirmed_count`).
* **📦 Archive & Lifecycle**: Mark outdated lessons as inactive so they stop appearing in search results, while keeping them for audit trail.
* **🔀 Flexible Embedding Providers**: Choose between OpenAI, Azure OpenAI, or Ollama for generating vector embeddings — local or cloud.
* **☁️ Cloud-Ready**: SSL/TLS support for Azure, AWS, and GCP hosted PostgreSQL. TCP keepalives and connection health checks for production reliability.

---

## 🧬 What Gets Embedded (and What Doesn't)

PerfMemory embeds only the `symptom_text` field — the structured description of what went wrong. The `diagnosis` and `fix_description` are stored as plain text columns but are **not** part of the embedding vector.

This is by design: the embedding captures the *question* (what went wrong), while the structured metadata carries the *answer* (what fixed it). When you search with a new symptom, cosine similarity matches against past symptoms, then returns the associated diagnosis and fix. If the fix text were embedded too, it would skew similarity toward fixes rather than symptoms — meaning a search for "401 on /oauth2/authorize" might match a past fix that mentions OAuth rather than a past symptom that describes the same error pattern.

**Embedded:** `symptom_text` (structured error description → vector)

**Not embedded (stored as metadata):** `diagnosis`, `fix_description`, `fix_type`, `component_type`, `manifest_excerpt`, `test_run_id`, `session_id`, timestamps

For details on the structured symptom template and embedding strategy, see `sql/schema/README.md`.

---

## 🏁 Prerequisites

* Python 3.12 or higher
* **PostgreSQL 18+** with the **pgvector** and **Apache AGE** extensions enabled (see `docker/Dockerfile.pgvector-age`)
* An embedding provider configured (one of the following):
  * **OpenAI** — requires an API key
  * **Azure OpenAI** — requires API key, endpoint, and deployment name
  * **Ollama** — requires a running Ollama instance with an embedding model pulled (e.g., `nomic-embed-text`)
* **Cursor IDE** (or compatible MCP host)

For database setup instructions, see the [pgvector Installation Guide](../docs/pgvector_installation_guide.md).

For example prompts, workflows, and tips, see the [PerfMemory User Guide](../docs/perfmemory_user_guide.md).

---

## 🚀 Getting Started

### 1. Set Up the Database

Follow the [pgvector Installation Guide](../docs/pgvector_installation_guide.md) to:
- Start a PostgreSQL + pgvector container via Docker Compose
- Connect with `psql` and enable the vector extension

### 2. Apply the Schema

Run the appropriate schema script against your database:

```bash
# For OpenAI or Azure OpenAI embeddings (1536 dimensions)
psql -h localhost -U perfadmin -d perfmemory -f sql/schema/schema_openai.sql

# For Ollama nomic-embed-text embeddings (768 dimensions)
psql -h localhost -U perfadmin -d perfmemory -f sql/schema/schema_ollama.sql
```

This creates the `debug_sessions` and `debug_attempts` tables, the HNSW vector index, and all B-tree indexes for metadata filtering.

### 2b. Apply the Graph Schema (Optional)

If using Apache AGE for the knowledge graph layer:

```bash
psql -h localhost -U perfadmin -d perfmemory -f sql/graph/001_create_graph.sql
```

This creates the `perf_knowledge` graph with vertex labels for Attempt, Project, ErrorPattern, and FixPattern. See `sql/graph/README.md` for details.

Set `graph.enabled: true` in `config.yaml` to activate graph features.

### 3. Configure Environment

Copy the example `.env` file and fill in your credentials:

```bash
cp .env.example .env
```

At minimum, configure:
- **Embedding provider** — set `EMBEDDING_PROVIDER` and the corresponding API key/endpoint
- **Database connection** — set `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- **SSL** (cloud only) — set `POSTGRES_SSLMODE` and optionally `POSTGRES_SSLROOTCERT`

### 4. Set Up Python Environment

#### Option A: Using `uv` (Recommended)

```bash
uv run perfmemory.py
```

#### Option B: Using Virtual Environment

**Windows (PowerShell):**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
```

**macOS / Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

---

## 🏷️ Taxonomy & Alias Resolution

PerfMemory includes a taxonomy system that standardizes naming conventions across teams. This ensures consistent semantic search when multiple team members use different names for the same applications, services, or environments.

### How It Works

1. Copy `taxonomy.example.yaml` to `taxonomy.yaml` and customize for your team
2. Define your applications, services, environments, and their aliases
3. When storing sessions/attempts, aliases are automatically resolved to canonical names
4. When searching, you can filter by alias — the system resolves it before querying

### Configuration

In `config.yaml`:

```yaml
taxonomy:
  path: taxonomy.yaml    # relative to perfmemory-mcp/
  strict: false          # true = reject inserts for undefined values
```

### Strictness Modes

- **`strict: false`** (default) — Unrecognized taxonomy values produce a `taxonomy_warnings` field in the response, but the insert proceeds. Recommended for teams that are incrementally standardizing.
- **`strict: true`** — Inserts are rejected if a taxonomy value is not defined. Use when the team has agreed on a complete taxonomy and wants enforcement.

The `strict_taxonomy` parameter on `store_debug_session` overrides the config-level setting per call.

### Taxonomy Categories

The taxonomy YAML defines both **core categories** (shipped with the project) and **user-defined sections**:

**Core categories** (extensible by adding entries to the YAML):
- `environment_types` — dev, qa, uat, staging, prod, performance, dr
- `auth_flow_types` — none, oauth_pkce, saml, entra_id, msal_pkce, custom_sso, etc.
- `error_categories` — HTTP 4xx/5xx, Authentication Error, Correlation Error, etc.
- `protocol_types` — http, https, grpc, websocket, etc.

**User-defined sections:**
- `applications` — Your apps with names, aliases, services, and auth type
- `environments` — Your specific environments (QA1, STG-East, PROD-US, etc.)

### Terminology Mapping

The same entity is referred to by different names across layers:

| Layer | Term | Example |
| :---- | :--- | :------ |
| Taxonomy YAML | `applications[].name` | "Online Shopping Portal" |
| Relational DB | `debug_sessions.system_under_test` | "Online Shopping Portal" |
| Knowledge Graph | `(:Project {name: ...})` | `(:Project {name: "Online Shopping Portal", alias: "OSP"})` |

These are a **one-to-one mapping**: application = project = system_under_test.

### Migration for Existing Users

If you already have data in PerfMemory, run the migration scripts to add the new columns:

```bash
psql -h localhost -U perfadmin -d perfmemory -f sql/migrations/001_add_taxonomy_columns.sql
psql -h localhost -U perfadmin -d perfmemory -f sql/migrations/002_update_graph_schema.sql
psql -h localhost -U perfadmin -d perfmemory -f sql/migrations/003_env_type_refactor.sql
```

Existing data is preserved — new columns default to empty strings (`''`). Migration 003 adds the `env_type` column, backfills it from existing `environment` values, and repurposes `environment` to hold specific environment names. The `environment_alias` column is retained but no longer actively used.

---

## ⚙️ MCP Server Configuration (`mcp.json`)

Example setup for Cursor or compatible MCP hosts:

```json
{
  "mcpServers": {
    "perfmemory": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/your/perfmemory-mcp",
        "run",
        "perfmemory.py"
      ]
    }
  }
}
```

---

## 🛠️ Tools

The PerfMemory MCP server exposes 11 tools organized into four groups:

### Core Tools

| Tool | Description |
| :--- | :---------- |
| `store_debug_session` | Create a new debug session to track a debugging workflow. Accepts optional taxonomy fields (`system_alias`, `service_name`, `auth_alias`) for standardized naming. The `environment` parameter accepts a specific environment name (e.g., "QA1") or canonical type (e.g., "qa") — `env_type` is auto-derived via taxonomy resolution. Returns a `session_id` for linking attempts. |
| `store_debug_attempt` | Embed a symptom and store a debug attempt linked to a session. Records the symptom, diagnosis, fix, and outcome. Accepts optional test context (`test_case_id`, `test_case_name`, `test_step_id`, `test_step_name`). If `matched_attempt_id` is provided, increments the matched attempt's `confirmed_count`. |
| `find_similar_attempts` | Search the memory store for past attempts that match a symptom. Accepts `system_alias` and `service_name` as additional filters with alias resolution. Returns matches ranked by cosine similarity with a recommendation (`apply_known_fix`, `review_suggestions`, or `no_match`). |

### Session Management Tools

| Tool | Description |
| :--- | :---------- |
| `close_debug_session` | Finalize a session with its outcome (`resolved`, `unresolved`, etc.) and optionally link the resolving attempt. |
| `list_sessions` | Browse stored debug sessions with optional filters (system, alias, service, environment, env_type, outcome). The `environment` parameter supports smart resolution — specific names, canonical types, or literal match. |
| `get_session_detail` | Retrieve a full session with all its attempts ordered by iteration number. |

### Maintenance Tools

| Tool | Description |
| :--- | :---------- |
| `archive_attempt` | Mark an attempt as inactive — excludes it from future searches while preserving it for audit. |
| `verify_attempt` | Mark an attempt as human-verified — signals high confidence in the lesson. |
| `get_memory_stats` | Get overview statistics: total sessions, attempts, breakdowns by system and outcome, verified and active counts. |

### Graph Tools (Apache AGE)

These tools require `graph.enabled: true` in `config.yaml` and a running PostgreSQL instance with the Apache AGE extension. They operate on the `perf_knowledge` knowledge graph.

| Tool | Description |
| :--- | :---------- |
| `find_cross_project_patterns` | Search the knowledge graph for resolved attempts from other projects that share the same error pattern. Uses graph traversal (not vector similarity) — useful as a fallback when `find_similar_attempts` returns no matches, or to proactively discover fixes across projects. |
| `get_related_issues` | Explore the graph neighborhood of a specific attempt. Returns connected ErrorPattern and FixPattern nodes, plus neighboring attempts linked via SIMILAR_TO edges. Useful for understanding the structural context of a known issue. |

---

## 🔁 Typical Workflow

### During JMeter Script Debugging

```
1. Encounter an error in a JMeter script
         │
         ▼
2. Call find_similar_attempts with the symptom
         │
    ┌────┴────┐
    │ Match?  │
    └────┬────┘
    Yes  │  No
    │    │
    ▼    ▼
3a. Apply   3b. Call find_cross_project_patterns
known fix       (graph fallback)
    │            │
    │       ┌────┴────┐
    │       │ Match?  │
    │       └────┬────┘
    │       Yes  │  No
    │       │    │
    │       ▼    ▼
    │   3c. Review  3d. Start a new
    │   & apply     debug session
    │       │            │
    ▼       ▼            ▼
4a. Store   4b. Store   4c. Debug iteratively,
attempt     attempt     storing each attempt
(with       (with           │
matched_id) matched_id)     ▼
                         4d. Close session
                             with outcome
```

### Step by Step

1. **Check memory first** — Before starting a debug loop, call `find_similar_attempts` with the current error symptom.
2. **If a match is found** — Review the diagnosis and fix from the matched attempt. Apply it and store the new attempt with `matched_attempt_id` to increment the original's confidence.
3. **If no vector match** — Call `find_cross_project_patterns` with the error category to search the knowledge graph for resolved fixes from other projects. This graph-only search can find related issues even when symptoms are phrased differently.
4. **If still no match** — Call `store_debug_session` to start tracking. After each debug iteration, call `store_debug_attempt` to record the symptom, what was tried, and the outcome.
5. **Explore related issues** — Use `get_related_issues` on any attempt to see its graph neighborhood (connected error patterns, fix patterns, and similar attempts).
6. **Close the session** — When debugging is complete, call `close_debug_session` with the final outcome and the ID of the resolving attempt.

---

## 📁 Project Structure

```
perfmemory-mcp/
├── perfmemory.py              # MCP server entrypoint (FastMCP)
├── services/
│   ├── embeddings.py          # Embedding provider abstraction (OpenAI, Azure, Ollama)
│   ├── graph_manager.py       # Apache AGE/Cypher graph operations
│   ├── session_manager.py     # Connection pool, CRUD operations, vector search
│   └── taxonomy.py            # Taxonomy resolver and alias validation
├── utils/
│   └── config.py              # Config loader (YAML + environment variables)
├── sql/
│   ├── schema/
│   │   ├── schema_openai.sql  # Tables + indexes for 1536-dim embeddings
│   │   └── schema_ollama.sql  # Tables + indexes for 768-dim embeddings
│   ├── graph/
│   │   ├── 001_create_graph.sql               # Graph schema (vertex/edge labels)
│   │   ├── 002_seed_graph_from_existing_data.sql  # Backfill graph from relational data
│   │   └── README.md                          # Graph schema documentation
│   └── migrations/
│       ├── 001_add_taxonomy_columns.sql       # Add taxonomy columns to existing tables
│       ├── 002_update_graph_schema.sql        # Add Service nodes and alias property
│       └── 003_env_type_refactor.sql          # Add env_type, repurpose environment column
├── .env.example               # Example environment configuration
├── config.example.yaml        # Example YAML config (search, graph, embedding, taxonomy)
├── taxonomy.example.yaml      # Example taxonomy definitions (copy to taxonomy.yaml)
├── pyproject.toml             # Project metadata and dependencies
└── README.md                  # This file
```

---

## 🗄️ Database Schema

PerfMemory uses two tables with a parent-child relationship:

### `debug_sessions` — The debugging story

Tracks the overall debugging workflow for a JMeter script issue.

| Column | Purpose |
| :----- | :------ |
| `system_under_test` | The application being tested — maps to Project.name in the knowledge graph |
| `system_alias` | Short name / alias for the application (e.g., "CART", "OSP") |
| `service_name` | Microservice name within the application |
| `test_run_id` | Links to the artifact structure |
| `script_name` | The JMX file being debugged |
| `auth_flow_type` | Authentication flow (none, oauth_pkce, saml, entra_id, etc.) |
| `auth_alias` | Human-readable auth label (e.g., "Corporate SSO Flow") |
| `env_type` | Canonical environment type (dev, qa, uat, staging, prod) |
| `environment` | Specific environment name (e.g., "QA1", "STG-East") |
| `environment_alias` | *(Retained, unused)* — preserved for historical reference |
| `final_outcome` | How the session ended (resolved, unresolved, etc.) |
| `resolution_attempt_id` | FK to the attempt that fixed the issue |

### `debug_attempts` — Individual iterations

Each row is one debug iteration within a session.

| Column | Purpose |
| :----- | :------ |
| `symptom_text` | Structured error description (embedded as vector) |
| `diagnosis` | Root cause determination |
| `fix_description` | What fix was applied |
| `fix_type` | Categorized fix (add_extractor, edit_header, etc.) |
| `outcome` | Result of this attempt (resolved, failed, etc.) |
| `embedding` | Vector embedding for similarity search |
| `test_case_id` | Business test case identifier (e.g., "TC01") |
| `test_case_name` | Human-readable test case name |
| `test_step_id` | Test step identifier within the test case (e.g., "S03") |
| `test_step_name` | Human-readable test step name |
| `is_verified` | Human-reviewed and confirmed |
| `is_active` | Included in search results (FALSE = archived) |
| `confirmed_count` | Number of times this fix was successfully reused |

### Indexing

| Index | Type | Purpose |
| :---- | :--- | :------ |
| `idx_attempts_embedding` | HNSW (cosine) | Fast approximate nearest-neighbor vector search |
| `idx_attempts_error_category` | B-tree | Filter by error category |
| `idx_attempts_outcome` | B-tree | Filter by attempt outcome |
| `idx_attempts_session_id` | B-tree | Join attempts to sessions |
| `idx_attempts_hostname` | B-tree | Filter by hostname |
| `idx_attempts_test_case` | B-tree | Filter by test case ID |
| `idx_sessions_system` | B-tree | Filter by system under test |
| `idx_sessions_environment` | B-tree | Filter by specific environment name |
| `idx_sessions_env_type` | B-tree | Filter by canonical environment type |
| `idx_sessions_outcome` | B-tree | Filter by session outcome |
| `idx_sessions_system_alias` | B-tree | Filter by system alias |
| `idx_sessions_service` | B-tree | Filter by service name |
| `idx_sessions_env_alias` | B-tree | *(Retained, unused)* — index on environment_alias |

---

## 🔧 Configuration Reference

All configuration is managed through environment variables (via `.env` file locally or platform-injected in cloud).

### Embedding Provider

| Variable | Default | Description |
| :------- | :------ | :---------- |
| `EMBEDDING_PROVIDER` | `openai` | Provider to use: `openai`, `azure_openai`, or `ollama` |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | — | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | `text-embedding-3-small` | Azure deployment name |
| `AZURE_OPENAI_API_VERSION` | `2024-02-15-preview` | Azure API version |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama instance URL |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model |

### Database

| Variable | Default | Description |
| :------- | :------ | :---------- |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `perfmemory` | Database name |
| `POSTGRES_USER` | `perfadmin` | Database user |
| `POSTGRES_PASSWORD` | — | Database password |
| `POSTGRES_SSLMODE` | `prefer` | SSL mode (`disable`, `prefer`, `require`, `verify-full`) |
| `POSTGRES_SSLROOTCERT` | — | Path to CA certificate (for `verify-ca` / `verify-full`) |

### Search

| Variable | Default | Description |
| :------- | :------ | :---------- |
| `VECTOR_TOP_K` | `5` | Max results from similarity search |
| `SIMILARITY_THRESHOLD` | `0.60` | Minimum cosine similarity score to return a match |

### Graph (config.yaml)

These settings are configured in `config.yaml` (not environment variables). See `config.example.yaml` for reference.

| Setting | Default | Description |
| :------ | :------ | :---------- |
| `graph.enabled` | `false` | Enable the Apache AGE knowledge graph layer |
| `graph.graph_name` | `perf_knowledge` | Name of the AGE graph |
| `graph.vector_weight` | `0.6` | Weight for vector similarity in hybrid scoring |
| `graph.graph_weight` | `0.4` | Weight for graph matches in hybrid scoring |
| `graph.embedding_edge_threshold` | `0.82` | Minimum similarity to create SIMILAR_TO edges |
| `graph.max_embedding_edges` | `3` | Max embedding-based edges per attempt |
| `search.ef_search` | `40` | HNSW search candidates — increase for better recall at scale |

### General

| Variable | Default | Description |
| :------- | :------ | :---------- |
| `DEBUG` | `false` | Enable debug logging |

### Taxonomy (config.yaml)

These settings are configured in `config.yaml` under the `taxonomy` section.

| Setting | Default | Description |
| :------ | :------ | :---------- |
| `taxonomy.path` | `taxonomy.yaml` | Path to taxonomy YAML (relative to perfmemory-mcp/) |
| `taxonomy.strict` | `false` | Reject inserts for undefined taxonomy values when true |

---

## 🚧 Future Enhancements

* **FastMCP 3.0 Migration** — Migrate to FastMCP 3.0 with async database drivers (`asyncpg`/`psycopg3`) for improved concurrency.
* **Structured Symptom Templates** — Standardize how symptoms are formatted before embedding to improve similarity scores.
* **Correlation Patterns Table** — Store common correlation patterns (separate from debug attempts) for reuse across scripts.
* **Data Retention Policies** — Auto-archive old attempts and configurable TTL.
* **Backup & Migration** — Database dump/restore tooling for moving data between environments.
* **Multi-Tenancy** — Team-level data isolation for shared deployments.
* **A2A Protocol Integration** — Agent-to-Agent handoff for Playwright test cases to JMeter script generation.

---

## 🤝 Contributing

Feel free to open issues or submit pull requests to enhance functionality, add new tools, or improve documentation!

---

Created with ❤️ using FastMCP, PostgreSQL, pgvector, Apache AGE, and the MCP Perf Suite architecture.
