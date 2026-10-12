-- =============================================================================
-- 011 - ALTER agent_tasks: extend status CHECK with A2A v1 spec states
-- =============================================================================
-- The original CHECK, defined inline in 003_create_agent_tasks.sql, allowed
-- only ('pending', 'running', 'completed', 'failed', 'cancelled'). The A2A
-- v1 TaskState enum (A2A specification §4.1.3) defines three additional
-- states that this schema must recognize so PerfPilot can respond with
-- fully spec-compliant task lifecycles:
--
--   * input_required  — task is paused awaiting additional client input.
--                       Non-terminal. The client resumes by sending a new
--                       message with the same taskId + contextId (A2A §6.3).
--                       Maps to A2A TASK_STATE_INPUT_REQUIRED.
--
--   * rejected        — agent decided not to perform the task. Terminal.
--                       Maps to A2A TASK_STATE_REJECTED.
--
--   * auth_required   — task is paused awaiting authentication. Non-terminal.
--                       Maps to A2A TASK_STATE_AUTH_REQUIRED.
--
-- PostgreSQL auto-named the original inline CHECK constraint using its
-- standard {table}_{column}_check convention, which produces
-- 'agent_tasks_status_check'. This migration drops that constraint and
-- re-adds it with the full set of A2A v1 state values.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 003_create_agent_tasks.sql
--
-- Idempotent: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT. Safe to re-run.
-- No row updates and no data migration are required — existing rows
-- carry only legacy status values that remain in the new allow-list.
-- =============================================================================

ALTER TABLE agent_tasks
    DROP CONSTRAINT IF EXISTS agent_tasks_status_check;

ALTER TABLE agent_tasks
    ADD CONSTRAINT agent_tasks_status_check
    CHECK (status IN (
        'pending',
        'running',
        'completed',
        'failed',
        'cancelled',
        'input_required',
        'rejected',
        'auth_required'
    ));

COMMENT ON COLUMN agent_tasks.status IS
    'A2A v1 task lifecycle (A2A specification §4.1.3). Values map as: '
    'pending=SUBMITTED, running=WORKING, completed=COMPLETED, '
    'failed=FAILED, cancelled=CANCELED, input_required=INPUT_REQUIRED, '
    'rejected=REJECTED, auth_required=AUTH_REQUIRED. '
    'Terminal states: completed, failed, cancelled, rejected. '
    'Interrupted (non-terminal) states: input_required, auth_required.';
