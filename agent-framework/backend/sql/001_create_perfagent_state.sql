-- =============================================================================
-- 001 - Create the perfagent_state database
-- =============================================================================
-- Runs in the `postgres` (or any non-perfagent_state) database context.
--
-- PostgreSQL does NOT support `CREATE DATABASE IF NOT EXISTS`, and CREATE
-- DATABASE cannot be wrapped in a DO block or transaction. The provisioning
-- script `agent-framework/sql/provision.py` performs an existence check
-- before invoking this file - so this statement is only executed when the
-- database does not already exist.
--
-- Safe to run from a fresh Postgres container init script
-- (/docker-entrypoint-initdb.d/) or from provision.py against an existing
-- `perfmemory-db` container.
-- =============================================================================

CREATE DATABASE perfagent_state;

COMMENT ON DATABASE perfagent_state IS
    'PerfPilot Agents runtime state. Sessions, tasks, checkpoints, conversations, tool traces, HITL approvals. JSONB-only - no pgvector, no Apache AGE.';
