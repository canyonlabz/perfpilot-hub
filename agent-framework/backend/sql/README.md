# `perfagent_state` SQL

DDL for the PerfPilot Agents runtime-state database. Six JSONB-only tables on
the same PostgreSQL instance as `perfmemory`. No pgvector, no Apache AGE.

## File order

| # | File | Context | Purpose |
|---|---|---|---|
| 001 | [`001_create_perfagent_state.sql`](./001_create_perfagent_state.sql) | `postgres` DB | `CREATE DATABASE perfagent_state` |
| 002 | [`002_create_agent_sessions.sql`](./002_create_agent_sessions.sql) | `perfagent_state` | UI / IDE / A2A sessions (V2 doc Section 4.3) |
| 003 | [`003_create_agent_tasks.sql`](./003_create_agent_tasks.sql) | `perfagent_state` | A2A task lifecycle (FK -> agent_sessions) |
| 004 | [`004_create_agent_checkpoints.sql`](./004_create_agent_checkpoints.sql) | `perfagent_state` | Resumable agent state snapshots |
| 005 | [`005_create_conversation_messages.sql`](./005_create_conversation_messages.sql) | `perfagent_state` | Append-only chat transcript |
| 006 | [`006_create_tool_call_traces.sql`](./006_create_tool_call_traces.sql) | `perfagent_state` | MCP tool-call audit (FK -> agent_tasks) |
| 007 | [`007_create_hitl_approvals.sql`](./007_create_hitl_approvals.sql) | `perfagent_state` | HITL prompt log (FK -> agent_tasks) |

Files 002 - 007 use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`
so re-running them is safe. File 001 is gated by an existence check in
[`provision.py`](./provision.py) because PostgreSQL does not support
`CREATE DATABASE IF NOT EXISTS`.

## How to run

### Against an already-running `perfmemory-db` container (development)

```powershell
# from the repo root, with agent-framework/.env populated
python agent-framework/sql/provision.py
```

`provision.py` reads `agent-framework/.env`, connects with admin credentials,
creates the `perfagent_state` database if it does not exist, then applies
files 002 - 007 in order. Idempotent.

### Against a fresh `perfmemory-db` container (Feature 3.12)

In Feature 3.12 the `docker-compose-a2a-local-{windows,mac}.yaml` files mount
this directory into the Postgres container's `/docker-entrypoint-initdb.d/`
so files 001 - 007 run automatically on first container init. The provisioning
script `provision.py` is not needed in that path.

## Idempotency

| Statement | Guard |
|---|---|
| `CREATE DATABASE perfagent_state` | Existence check in `provision.py` |
| `CREATE TABLE` | `IF NOT EXISTS` |
| `CREATE INDEX` | `IF NOT EXISTS` |
| `COMMENT ON ...` | always re-issued; harmless |

## Schema reference

See V2 doc Section 12.3 for the design rationale and the full column-level
documentation. Each `.sql` file also carries inline `COMMENT ON COLUMN`
statements that are queryable via `\d+ <table_name>` in `psql`.
