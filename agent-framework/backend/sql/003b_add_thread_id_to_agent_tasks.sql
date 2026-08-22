-- =============================================================================
-- 003b - ALTER agent_tasks: add thread_id column
-- =============================================================================
-- Adds a first-class `thread_id` column to `agent_tasks` so tasks can be
-- discovered by conversation (thread) regardless of `test_run_id` state.
--
-- Motivation: Orchestrator-delegated tasks have `test_run_id: null`
-- because the real BlazeMeter run_id only exists AFTER the task completes.
-- The TaskProgressPanel needs to discover tasks by thread (conversation)
-- instead of by test_run_id.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 003_create_agent_tasks.sql
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS thread_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_tasks_thread
    ON agent_tasks (thread_id)
    WHERE thread_id IS NOT NULL;

COMMENT ON COLUMN agent_tasks.thread_id IS
    'Thread (conversation) that originated this task. Enables thread-scoped task queries.';

-- =============================================================================
-- Backfill: populate thread_id from payload JSONB for existing tasks
-- =============================================================================
-- Tasks created before this migration carry thread_id in
-- payload._perfpilot_thread.thread_id but not as a first-class column.
-- This one-time UPDATE backfills them. Runs harmlessly if no rows match.

UPDATE agent_tasks
SET thread_id = payload->'_perfpilot_thread'->>'thread_id'
WHERE thread_id IS NULL
  AND payload->'_perfpilot_thread'->>'thread_id' IS NOT NULL;
