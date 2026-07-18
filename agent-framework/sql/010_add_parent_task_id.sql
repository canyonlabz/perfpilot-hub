-- =============================================================================
-- 010 - Add parent_task_id to agent_tasks (ENH-003B)
-- =============================================================================
-- Adds a nullable parent_task_id column to agent_tasks, enabling:
--   - Specialist SSE event proxying to orchestrator subscribers
--   - Task tree visualization (future)
--   - Cascading cancellation (future)
--   - Billing rollup (future)
--
-- The FK references agent_tasks(task_id) so only valid task IDs are stored.
-- NULL means the task is a top-level orchestrator task with no parent.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 003_create_agent_tasks.sql
--
-- Idempotent: uses IF NOT EXISTS patterns.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'agent_tasks'
          AND column_name = 'parent_task_id'
    ) THEN
        ALTER TABLE agent_tasks
            ADD COLUMN parent_task_id UUID REFERENCES agent_tasks(task_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_agent_tasks_parent
    ON agent_tasks (parent_task_id)
    WHERE parent_task_id IS NOT NULL;

COMMENT ON COLUMN agent_tasks.parent_task_id
    IS 'Orchestrator task that delegated this specialist task (NULL for top-level tasks).';
