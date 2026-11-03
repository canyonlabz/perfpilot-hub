# `perfagent_state` SQL

DDL for the PerfPilot Agents runtime-state database on the same PostgreSQL
instance as `perfmemory`. No pgvector, no Apache AGE.

## File order

| # | File | Context | Purpose |
|---|---|---|---|
| 001 | [`001_create_perfagent_state.sql`](./001_create_perfagent_state.sql) | `postgres` DB | `CREATE DATABASE perfagent_state` |
| 002 | [`002_create_agent_sessions.sql`](./002_create_agent_sessions.sql) | `perfagent_state` | UI / IDE / A2A sessions |
| 003 | [`003_create_agent_tasks.sql`](./003_create_agent_tasks.sql) | `perfagent_state` | A2A task lifecycle (FK -> agent_sessions) |
| 003b | [`003b_add_thread_id_to_agent_tasks.sql`](./003b_add_thread_id_to_agent_tasks.sql) | `perfagent_state` | Add thread_id column to agent_tasks |
| 004 | [`004_create_agent_checkpoints.sql`](./004_create_agent_checkpoints.sql) | `perfagent_state` | Resumable agent state snapshots |
| 005 | [`005_create_conversation_messages.sql`](./005_create_conversation_messages.sql) | `perfagent_state` | Append-only chat transcript |
| 006 | [`006_create_tool_call_traces.sql`](./006_create_tool_call_traces.sql) | `perfagent_state` | MCP tool-call audit (FK -> agent_tasks) |
| 007 | [`007_create_hitl_approvals.sql`](./007_create_hitl_approvals.sql) | `perfagent_state` | HITL prompt log (FK -> agent_tasks) |
| 008 | [`008_create_agent_threads.sql`](./008_create_agent_threads.sql) | `perfagent_state` | Persistent conversation threads |
| 009 | [`009_create_token_ledger.sql`](./009_create_token_ledger.sql) | `perfagent_state` | LLM token usage tracking |
| 010 | [`010_add_parent_task_id.sql`](./010_add_parent_task_id.sql) | `perfagent_state` | Add parent_task_id for task hierarchies |
| 011 | [`011_extend_agent_tasks_status.sql`](./011_extend_agent_tasks_status.sql) | `perfagent_state` | A2A v1 task status values |
| 012 | [`012_create_users.sql`](./012_create_users.sql) | `perfagent_state` | Web-UI user profile |
| 013 | [`013_create_user_secrets.sql`](./013_create_user_secrets.sql) | `perfagent_state` | Named secret metadata |
| 014 | [`014_create_user_secret_fields.sql`](./014_create_user_secret_fields.sql) | `perfagent_state` | Encrypted secret field values |
| 015 | [`015_create_user_project_bindings.sql`](./015_create_user_project_bindings.sql) | `perfagent_state` | Per-user project token bindings |

Files 002 onward use `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
or equivalent `IF NOT EXISTS` / `DROP CONSTRAINT IF EXISTS` guards so
re-running them is safe. File 001 is gated by an existence check in
[`provision.py`](./provision.py) because PostgreSQL does not support
`CREATE DATABASE IF NOT EXISTS`.

## How to run

### Against an already-running `perfmemory-db` container (development)

```powershell
# from the repo root, with agent-framework/backend/.env populated
python agent-framework/backend/sql/provision.py
```

`provision.py` reads `agent-framework/backend/.env`, connects with admin
credentials, creates the `perfagent_state` database if it does not exist,
then applies files 002 - 015 (including 003b) in order. Idempotent.

Target engine: **PostgreSQL 18** (image `pgvector/pgvector:pg18` / container
`perfmem-pgvector-age`). An operator runs this script. Do not apply it from
unattended automation without review.

### Against a fresh Postgres container

Some Compose files mount this directory into the container's
`/docker-entrypoint-initdb.d/` so the numbered scripts run on first init.
On that path `provision.py` is optional; confirm the mount includes every
script you need (this folder now goes through 015).

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
