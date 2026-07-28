# PerfMemory Vector Database Schema Design

## Overview

The `perfmemory` database is a pgvector-backed vector store that serves as the "memory" and "lessons learned" layer for JMeter script debugging within the `mcp-perf-suite`. It stores debug session history -- including both failed attempts and successful resolutions -- so AI agents can query past experience before starting a new debug cycle.

This document defines the schema for two tables, their columns, allowed values, indexing strategy, and embedding provider guidance.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Cursor / AI Agent                          │
│  (calls MCP tools)                          │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  PerfMemory MCP Tools                       │
│  store_debug_lesson()                       │
│  find_similar_lesson()                      │
│  (same interface regardless of provider)    │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  Embedding Adapter Layer                    │
│  get_embedding(text) → float[]              │
│  ┌────────────┬──────────────┬────────────┐ │
│  │  OpenAI    │ Azure OpenAI │   Ollama   │ │
│  │  (1536)    │  (1536)      │   (768)    │ │
│  └────────────┴──────────────┴────────────┘ │
│  Selected via config.yaml                   │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│  pgvector Database (perfmemory)             │
│  vector(1536) or vector(768)                │
│  (dimension matches the configured model)   │
└─────────────────────────────────────────────┘
```

## Table Design

Two tables with a parent-child relationship:

- **`debug_sessions`** -- one row per debug session (session-level metadata)
- **`debug_attempts`** -- one row per debug iteration (attempt-level data with embeddings)

A debug session contains multiple attempts. Each attempt represents one iteration of the debug loop: a symptom was observed, a diagnosis was made, a fix was attempted, and the outcome was recorded. One attempt may be the resolution; the others are failed attempts that serve as guardrails for future queries.

---

## Table 1: `debug_sessions`

One row per debug session. Holds session-level metadata and links to the resolving attempt.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Unique session identifier. UUID avoids ID collisions when merging databases from multiple PTEs. |
| `system_under_test` | `TEXT` | `NOT NULL` | The application being tested. Maps to Project.name in the knowledge graph and applications[].name in the taxonomy YAML. |
| `system_alias` | `TEXT` | `NOT NULL DEFAULT ''` | Short name or alias for the application (e.g., "CART", "OSP"). Used for cross-team standardization. |
| `service_name` | `TEXT` | `NOT NULL DEFAULT ''` | Microservice name within the application (e.g., "auth-service", "cart-api"). |
| `test_run_id` | `TEXT` | `NOT NULL` | Links to the mcp-perf-suite artifact structure (`artifacts/{test_run_id}/`). |
| `script_name` | `TEXT` | | The JMX filename that was being debugged. |
| `auth_flow_type` | `TEXT` | | The authentication flow type of the script. See allowed values below. |
| `auth_alias` | `TEXT` | `NOT NULL DEFAULT ''` | Human-readable auth label (e.g., "Corporate SSO Flow", "Partner OAuth"). |
| `env_type` | `TEXT` | `NOT NULL DEFAULT ''` | Canonical environment type (dev, qa, uat, staging, prod). See allowed values below. |
| `environment` | `TEXT` | | Specific environment name (e.g., "QA1", "STG-East", "PROD-US"). |
| `environment_alias` | `TEXT` | `NOT NULL DEFAULT ''` | *(Retained, unused)* — preserved for historical reference. No longer written by active code paths. |
| `total_iterations` | `INT` | | How many debug attempts were made in this session. |
| `final_outcome` | `TEXT` | `NOT NULL` | The final result of the debug session. See allowed values below. |
| `resolution_attempt_id` | `UUID` | `REFERENCES debug_attempts(id)` | FK to the specific attempt that resolved the issue. NULL if unresolved. |
| `created_by` | `TEXT` | | PTE name or username. Useful when sharing databases across a team. |
| `notes` | `TEXT` | | Freeform notes the PTE wants to attach to this session. |
| `started_at` | `TIMESTAMPTZ` | `NOT NULL` | When the debug session began. |
| `completed_at` | `TIMESTAMPTZ` | | When the debug session ended. |
| `created_at` | `TIMESTAMPTZ` | `DEFAULT NOW()` | When this row was inserted into the database. |

### Allowed Values: `auth_flow_type`

| Value | Description |
|---|---|
| `none` | No authentication in this script |
| `oauth_pkce` | OAuth with PKCE (Proof Key for Code Exchange) |
| `oauth_auth_code` | OAuth Authorization Code flow |
| `saml` | SAML-based authentication |
| `token_chain` | Token exchange flows |
| `custom_sso` | Custom Single Sign-On implementation |
| `entra_id` | Microsoft Entra ID |
| `msal_pkce` | Microsoft Authentication Library with PKCE |
| `other` | Authentication flow not listed above |
| `NULL` | Not yet determined or unknown |

### Allowed Values: `env_type`

| Value | Description |
|---|---|
| `dev` | Development environment |
| `qa` | Quality Assurance environment |
| `uat` | User Acceptance Testing environment |
| `staging` | Staging / pre-production environment |
| `perf` | Performance environment |
| `prod` | Production environment |

These are the canonical environment types defined in `taxonomy.yaml[environment_types]`. The column is freeform `TEXT` so teams can extend with additional types.

### `environment` Column

The `environment` column holds the **specific environment name** (e.g., "QA1", "STG-East") as defined in `taxonomy.yaml[environments]`. This is freeform `TEXT` — teams can use whatever environment names apply to their organization.

### Allowed Values: `final_outcome`

| Value | Description |
|---|---|
| `resolved` | The script issue was successfully fixed |
| `unresolved` | Could not determine or apply a fix |
| `environment_issue` | Server down, connection refused, infrastructure problem |
| `test_data_issue` | Bad, expired, or missing test data |
| `authentication_issue` | 401/403 errors due to credentials or auth configuration |
| `iteration_limit_reached` | Hit the maximum debug iteration limit (5) without resolution |
| `needs_investigation` | Requires manual human analysis beyond what the agent can do |

---

## Table 2: `debug_attempts`

One row per debug iteration. Holds the symptom, diagnosis, fix, outcome, and the embedding vector.

### Metadata Columns (used for SQL `WHERE` filtering before vector search)

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | `PRIMARY KEY DEFAULT gen_random_uuid()` | Unique attempt identifier. |
| `session_id` | `UUID` | `NOT NULL REFERENCES debug_sessions(id)` | Links this attempt to its parent session. |
| `iteration_number` | `INT` | `NOT NULL` | Which attempt within the session (1, 2, 3...). |
| `error_category` | `TEXT` | | Standardized error category from the JMeter log analyzer. See allowed values below. |
| `severity` | `TEXT` | | Error severity from the JMeter log analyzer: `Critical`, `High`, `Medium`. |
| `response_code` | `TEXT` | | HTTP status code (e.g., `401`, `500`). |
| `outcome` | `TEXT` | `NOT NULL` | The result of this specific attempt. See allowed values below. |

### Stored Columns (returned with search results for the agent to reason about)

| Column | Type | Constraints | Description |
|---|---|---|---|
| `hostname` | `TEXT` | | The host where the error occurred (e.g., `login.mycompany.com`). Useful for distinguishing integration points within an end-to-end workflow. |
| `sampler_name` | `TEXT` | | The failing JMeter sampler (e.g., `TC01_S03_Login_OAuth_Token`). |
| `api_endpoint` | `TEXT` | | The failing URL/endpoint. |
| `symptom_text` | `TEXT` | `NOT NULL` | Structured symptom description. **This is the text that gets embedded into a vector.** |
| `diagnosis` | `TEXT` | | What was determined to be the root cause (plain language). |
| `fix_description` | `TEXT` | | What fix was attempted (plain language). |
| `fix_type` | `TEXT` | | Categorized fix type. See allowed values below. |
| `component_type` | `TEXT` | | JMeter component type involved in the fix. See allowed values below. |
| `test_case_id` | `TEXT` | `NOT NULL DEFAULT ''` | Business test case identifier (e.g., "TC01"). Maps to JMeter controller structure. |
| `test_case_name` | `TEXT` | `NOT NULL DEFAULT ''` | Human-readable test case name (e.g., "User Login Flow"). |
| `test_step_id` | `TEXT` | `NOT NULL DEFAULT ''` | Test step identifier within the test case (e.g., "S03"). |
| `test_step_name` | `TEXT` | `NOT NULL DEFAULT ''` | Human-readable test step name (e.g., "Submit Credentials"). |
| `manifest_excerpt` | `TEXT` | | Raw text from the debug manifest iteration section. Provides rich context for the agent beyond the structured fields. |

### System Columns

| Column | Type | Constraints | Description |
|---|---|---|---|
| `embedding_model` | `TEXT` | `NOT NULL` | Which embedding model produced this vector (e.g., `text-embedding-3-small`). Critical for migration awareness. |
| `embedding` | `vector(N)` | | The semantic vector. Dimension depends on provider: `vector(1536)` for OpenAI/Azure OpenAI, `vector(768)` for Ollama nomic. |
| `is_verified` | `BOOLEAN` | `DEFAULT FALSE` | Has a human confirmed this lesson is correct? |
| `is_active` | `BOOLEAN` | `DEFAULT TRUE` | Soft delete flag. Set to FALSE when a lesson becomes outdated (e.g., API changed, issue no longer occurs). |
| `confirmed_count` | `INT` | `DEFAULT 1` | Confidence signal. Incremented when duplicate lessons are merged during deduplication. |
| `created_at` | `TIMESTAMPTZ` | `DEFAULT NOW()` | When this row was inserted into the database. |

### Allowed Values: `error_category`

These values come from the JMeter log analyzer's standardized categories:

| Value | Severity | Description |
|---|---|---|
| `HTTP 5xx Error` | Critical | Server-side errors (500-599) |
| `HTTP 4xx Error` | High | Client-side errors (400-499) |
| `Script Execution Failure` | Critical | JMeter script execution errors |
| `Connection Error` | Critical | Connection refused, reset, etc. |
| `Timeout Error` | High | Request or connection timeouts |
| `SSL/TLS Error` | High | Certificate or SSL handshake failures |
| `Thread/Concurrency Error` | Critical | Thread group or concurrency issues |
| `DNS Resolution Error` | Critical | DNS lookup failures |
| `Authentication Error` | High | Auth-specific errors |
| `Fatal JMeter Error` | Critical | JMeter FATAL-level errors |
| `Custom Logic Error` | High | Errors from JSR223/custom scripts |
| `General Error` | Medium | Uncategorized errors |

### Allowed Values: `outcome`

| Value | Description |
|---|---|
| `resolved` | This fix resolved the issue |
| `failed` | This fix was attempted but did not resolve the issue |
| `environment_issue` | The issue is environmental, not a script problem |
| `test_data_issue` | The issue is caused by test data, not the script |
| `authentication_issue` | The issue is caused by credentials or auth configuration |
| `needs_investigation` | Requires manual analysis |

### Allowed Values: `fix_type`

| Value | Description |
|---|---|
| `add_extractor` | Added a new extractor (JSON, Regex, Boundary) |
| `move_extractor` | Moved an extractor from one sampler to another |
| `edit_request_body` | Modified request body parameterization |
| `edit_header` | Modified headers (auth tokens, CSRF, etc.) |
| `edit_correlation` | Fixed a correlation value or expression |
| `other` | Fix type not listed above |

### Allowed Values: `component_type`

| Value | Description |
|---|---|
| `json_extractor` | JSON Path Extractor |
| `regex_extractor` | Regular Expression Extractor |
| `boundary_extractor` | Boundary Extractor |
| `header_manager` | HTTP Header Manager |
| `cookie_manager` | HTTP Cookie Manager |
| `jsr223_preprocessor` | JSR223 PreProcessor |
| `jsr223_postprocessor` | JSR223 PostProcessor |

This list is not exhaustive. The column is freeform `TEXT` to accommodate any JMeter component type.

---

## Indexes

### HNSW Vector Index

The HNSW (Hierarchical Navigable Small World) index on the `embedding` column enables fast approximate nearest neighbor search:

```sql
CREATE INDEX idx_attempts_embedding ON debug_attempts
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

- `vector_cosine_ops` -- cosine similarity, the standard metric for text embeddings
- `m = 16` -- connections per node in the graph (higher = better recall, more memory)
- `ef_construction = 64` -- build quality (higher = better index, slower to build)

### B-tree Metadata Indexes

Standard PostgreSQL indexes on frequently filtered columns:

```sql
CREATE INDEX idx_attempts_error_category ON debug_attempts (error_category);
CREATE INDEX idx_attempts_outcome ON debug_attempts (outcome);
CREATE INDEX idx_attempts_session_id ON debug_attempts (session_id);
CREATE INDEX idx_attempts_hostname ON debug_attempts (hostname);
CREATE INDEX idx_attempts_test_case ON debug_attempts (test_case_id);
CREATE INDEX idx_sessions_system ON debug_sessions (system_under_test);
CREATE INDEX idx_sessions_environment ON debug_sessions (environment);
CREATE INDEX idx_sessions_env_type ON debug_sessions (env_type);
CREATE INDEX idx_sessions_outcome ON debug_sessions (final_outcome);
CREATE INDEX idx_sessions_system_alias ON debug_sessions (system_alias);
CREATE INDEX idx_sessions_service ON debug_sessions (service_name);
CREATE INDEX idx_sessions_env_alias ON debug_sessions (environment_alias);  -- retained, unused
```

---

## Embedding Strategy

### What Gets Embedded

The `symptom_text` column contains the text that is converted into a vector. This text should follow a **structured template** for consistency across all lessons:

```
error_category: {error_category}
sampler: {sampler_name}
endpoint: {api_endpoint}
symptom: {error_message}
```

The structured format ensures embeddings are comparable across lessons stored by different PTEs at different times.

### What Does NOT Get Embedded

The following are stored as metadata or payload columns but are **not** included in the embedding:

- `test_run_id`, `session_id` -- unique per run, adds noise to the vector
- `fix_description`, `fix_type` -- the fix is the answer, not the question; embedding it would skew similarity toward fixes rather than symptoms
- `manifest_excerpt` -- too verbose, would drown out the symptom signal
- Timestamps -- pure noise for semantic similarity

### Query-Time Embedding

At query time, the agent has only the current error (not yet diagnosed). The query embedding uses the same template with available fields:

```
error_category: {error_category}
sampler: {sampler_name}
endpoint: {api_endpoint}
symptom: {current_error_message}
```

This asymmetry (stored lessons have slightly richer context, queries have the immediate symptom) works well with cosine similarity as long as the symptom signal is consistent.

---

## Embedding Provider Guide

### Provider Options

| Provider | Model | Dimensions | API Key Required | Notes |
|---|---|---|---|---|
| OpenAI | `text-embedding-3-small` | 1536 | Yes (OpenAI API key) | Recommended default. Best quality for the cost. |
| Azure OpenAI | `text-embedding-3-small` | 1536 | Yes (Azure OpenAI key) | Same model as OpenAI. Vectors are identical and interchangeable. |
| Ollama | `nomic-embed-text` | 768 | No (runs locally) | Fully local, no data leaves the machine. Good for air-gapped or privacy-sensitive environments. |

### Critical Rules

1. **All vectors in a database must come from the same model.** Vectors from different models exist in different mathematical spaces. Similarity scores between them are meaningless.
2. **Choose a provider and stick with it.** Switching providers requires re-embedding all existing data.
3. **OpenAI and Azure OpenAI are interchangeable.** They use the same models and produce identical vectors. You can switch between them without re-embedding.
4. **The schema script must match your provider.** Use `schema_openai.sql` for OpenAI/Azure OpenAI (1536 dims) or `schema_ollama.sql` for Ollama (768 dims).

### Schema Scripts

Located in `perfmemory-mcp/sql/schema/`:

- `schema_openai.sql` -- for OpenAI and Azure OpenAI (`vector(1536)`)
- `schema_ollama.sql` -- for Ollama nomic-embed-text (`vector(768)`)

---

## Query Flow

When the AI agent starts debugging and encounters an error:

1. **Filter** -- Join `debug_attempts` with `debug_sessions`. Apply metadata filters: `system_under_test`, `error_category`, `is_active = TRUE`.
2. **Vector search** -- Find attempts where `embedding` is most similar to the current symptom's embedding.
3. **Read results** -- Each result includes the past symptom, diagnosis, fix description, and whether it resolved or failed.
4. **Group by session** -- Group results by `session_id` to reconstruct full debug stories: "attempt 1 tried X and failed, attempt 2 tried Y and resolved it."
5. **Decide** -- Use the few-shot context to make an informed decision about which fix to try first.

---

## Future Enhancements

- **Migration/backup tooling** -- Export, re-embed, and reimport data when changing embedding models or migrating to a shared database.
- **Deduplication logic** -- Merge near-duplicate lessons from multiple PTEs, incrementing `confirmed_count`.
- **Correlation patterns table** -- Store working correlation configurations for different auth flow types.
- **Confidence decay** -- Reduce trust in older lessons that haven't been confirmed recently.
- **Team sharing** -- Dockerized shared pgvector instance with tenant isolation per team/project.
