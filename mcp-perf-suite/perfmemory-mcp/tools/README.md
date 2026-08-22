# PerfMemory Tools

Standalone CLI utilities for PerfMemory database maintenance and data quality.

These tools are designed to be run manually by a PTE or kicked off by Cursor/AI agents.
They operate directly on the PostgreSQL database and use the same `.env` configuration
as the PerfMemory MCP server.

---

## Available Tools

### `normalize_taxonomy.py`

Normalizes existing `system_under_test` values to canonical taxonomy names and backfills
empty `system_alias` fields. Optionally fixes `error_category`, `env_type`, and
`environment` (specific name) drift.

**Prerequisites:**

- Python 3.10+
- `perfmemory-mcp/taxonomy.yaml` must exist (copy from `taxonomy.example.yaml` and customize)
- `perfmemory-mcp/.env` must exist with valid PostgreSQL credentials
- Database must be running and accessible

**Quick Start:**

```bash
# Navigate to the tools directory
cd perfmemory-mcp/tools

# Dry-run (default) — preview what would change, no modifications
python normalize_taxonomy.py

# Apply changes after reviewing dry-run output
python normalize_taxonomy.py --apply

# Also fix error_category, env_type, and environment columns
python normalize_taxonomy.py --apply --fix-error-category --fix-env-type --fix-environment

# Include Apache AGE graph normalization
python normalize_taxonomy.py --apply --include-graph

# Override database connection (defaults to ../.env)
python normalize_taxonomy.py --host localhost --port 5433 --db perfmemory
```

**CLI Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--apply` | Execute normalization (without this flag, runs in dry-run mode) | `false` |
| `--fix-error-category` | Also normalize `error_category` in `debug_attempts` | `false` |
| `--fix-env-type` | Normalize `env_type` in `debug_sessions` via `environment_types` alias resolution | `false` |
| `--fix-environment` | Normalize `environment` (specific name) in `debug_sessions` via `environments` lookup | `false` |
| `--include-graph` | Also update Apache AGE graph (`Project.name`, `Attempt.project`) | `false` |
| `--taxonomy` | Path to taxonomy YAML file | `../taxonomy.yaml` |
| `--env-file` | Path to .env file for DB credentials | `../.env` |
| `--host` | PostgreSQL host (overrides .env) | — |
| `--port` | PostgreSQL port (overrides .env) | — |
| `--db` | PostgreSQL database name (overrides .env) | — |
| `--user` | PostgreSQL user (overrides .env) | — |
| `--password` | PostgreSQL password (overrides .env) | — |

**How Matching Works (3-Tier):**

1. **Exact match** — `system_under_test` matches a taxonomy application `name` or `alias` (case-insensitive)
2. **Contains match** — A taxonomy `name` or `alias` appears as a substring in the `system_under_test` value (e.g., "Shopping Portal (OSP) - Login Flow" matches alias "OSP")
3. **Unmatched** — Value doesn't match any taxonomy application; reported but not modified

**Logging:**

All output is printed to stdout and also written to a timestamped log file in `tools/logs/`:

```
tools/logs/normalize_20260507_223000.log
```

**Adaptability:**

This tool is designed to be modified for your team's needs. The matching logic is
clearly structured so you can add custom matching rules, adjust substring matching
thresholds, or add support for additional columns.

---

### `update_session_metadata.py`

Updates metadata fields on a single `debug_sessions` row by session ID. Supports
a strict field allowlist — only approved fields can be modified. Structural fields
(`id`, `test_run_id`, timestamps) and system-managed fields are non-updateable.

**Prerequisites:**

- Python 3.10+
- `perfmemory-mcp/.env` must exist with valid PostgreSQL credentials
- Database must be running and accessible
- Migration 003 must be applied (for `env_type` field)

**Quick Start:**

```bash
cd perfmemory-mcp/tools

# View current values for a session
python update_session_metadata.py --session-id <UUID> --show

# Dry-run — preview what would change
python update_session_metadata.py --session-id <UUID> --set environment="QA1" --set env_type="qa"

# Apply changes
python update_session_metadata.py --session-id <UUID> --set environment="QA1" --set env_type="qa" --apply
```

**CLI Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--session-id` | UUID of the session to update (required) | — |
| `--show` | Display current field values (read-only) | — |
| `--set` | Set a field value (repeatable, e.g., `--set field="value"`) | — |
| `--apply` | Execute the update (without this flag, runs in dry-run mode) | `false` |
| `--env-file` | Path to .env file | `../.env` |
| `--host/--port/--db/--user/--password` | Database connection overrides | — |

**Updateable fields:**

`system_under_test`, `system_alias`, `service_name`, `environment`, `env_type`,
`auth_flow_type`, `auth_alias`, `script_name`, `notes`, `created_by`, `final_outcome`

**Design rules:**

- Dry-run by default — must pass `--apply` to execute
- Single session per invocation — no bulk updates
- Shows before/after values for all changed fields
- Rejects unrecognized field names
- Parameterized queries only (SQL injection safe)
- No DELETE, DROP, or TRUNCATE

---

### `update_attempt_metadata.py`

Updates metadata fields on a single `debug_attempts` row by attempt ID. Protected
fields (`symptom_text`, `embedding`, `is_verified`, `is_active`, `confirmed_count`)
cannot be updated via this tool — they have dedicated MCP tools or would break
vector consistency if changed.

**Prerequisites:**

- Python 3.10+
- `perfmemory-mcp/.env` must exist with valid PostgreSQL credentials
- Database must be running and accessible

**Quick Start:**

```bash
cd perfmemory-mcp/tools

# View current values for an attempt
python update_attempt_metadata.py --attempt-id <UUID> --show

# Dry-run — preview what would change
python update_attempt_metadata.py --attempt-id <UUID> --set error_category="HTTP 5xx Error"

# Apply changes
python update_attempt_metadata.py --attempt-id <UUID> --set error_category="HTTP 5xx Error" --set severity="Critical" --apply
```

**CLI Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--attempt-id` | UUID of the attempt to update (required) | — |
| `--show` | Display current field values (read-only) | — |
| `--set` | Set a field value (repeatable, e.g., `--set field="value"`) | — |
| `--apply` | Execute the update (without this flag, runs in dry-run mode) | `false` |
| `--env-file` | Path to .env file | `../.env` |
| `--host/--port/--db/--user/--password` | Database connection overrides | — |

**Updateable fields:**

`error_category`, `severity`, `response_code`, `outcome`, `hostname`, `sampler_name`,
`api_endpoint`, `diagnosis`, `fix_description`, `fix_type`, `component_type`,
`test_case_id`, `test_case_name`, `test_step_id`, `test_step_name`, `manifest_excerpt`

**Protected fields (with reasons):**

| Field | Reason |
|-------|--------|
| `symptom_text` | Changing text without re-embedding creates a vector mismatch. Archive and create new. |
| `embedding` | System-managed — generated from `symptom_text` by the MCP server. |
| `is_verified` | Use the `verify_attempt` MCP tool instead. |
| `is_active` | Use the `archive_attempt` MCP tool instead. |
| `confirmed_count` | System-managed confidence counter. |

**Design rules:** Same as `update_session_metadata.py`.

---

### `sync_graph.py`

Reconciles Apache AGE graph nodes with current relational data. Detects mismatches
between Project/Attempt nodes in the graph and `system_under_test`/`system_alias`
values in the relational store. Useful after running `normalize_taxonomy.py` or
manually correcting session metadata.

**Prerequisites:**

- Python 3.10+
- `perfmemory-mcp/.env` must exist with valid PostgreSQL credentials
- Database must be running with the Apache AGE extension
- The `perf_knowledge` graph must exist (`sql/graph/001_create_graph.sql`)

**Quick Start:**

```bash
cd perfmemory-mcp/tools

# Dry-run — show all mismatches
python sync_graph.py

# Apply all fixes
python sync_graph.py --apply

# Target a specific project
python sync_graph.py --project "Valuation Insights" --apply
```

**CLI Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--apply` | Execute graph updates (without this flag, runs in dry-run mode) | `false` |
| `--project` | Target a specific project for sync | all projects |
| `--graph-name` | Apache AGE graph name | `perf_knowledge` |
| `--env-file` | Path to .env file | `../.env` |
| `--host/--port/--db/--user/--password` | Database connection overrides | — |

**What it syncs:**

| Graph Node | Property | Relational Source |
|------------|----------|-------------------|
| `Project` | `name` | `debug_sessions.system_under_test` |
| `Project` | `alias` | `debug_sessions.system_alias` |
| `Attempt` | `project` | `debug_sessions.system_under_test` (via session join) |

**What it reports:**

- **Alias mismatches** — Project nodes where graph alias differs from relational alias
- **Orphan projects** — Project nodes in graph with no matching relational data (informational only — not auto-deleted)
- **Attempt project mismatches** — Attempt nodes referencing a project name not in relational data

**Design rules:**

- Dry-run by default
- Only updates existing nodes — does not create or delete graph nodes
- Orphan nodes are reported but not removed (review manually)
- No DELETE of graph nodes

---

## Logs

Runtime log files are stored in the `logs/` subfolder. These files are gitignored
and will not be committed to the repository. Each tool generates timestamped log files
so you can review past runs.

---

## Dependencies

All dependencies are already part of the PerfMemory MCP requirements:

- `psycopg2-binary` — PostgreSQL driver
- `python-dotenv` — .env file loading
- `PyYAML` — Taxonomy YAML parsing

No additional `pip install` is needed if you already have the PerfMemory MCP environment set up.
