-- =============================================================================
-- 003 - Table: agent_tasks
-- =============================================================================
-- Lifecycle of every A2A task. One row per task, regardless of which agent
-- produced it. Tasks belong to a session (FK to agent_sessions). See V2 doc
-- Sections 9 and 14 for the long-running task patterns this table backs.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 002_create_agent_sessions.sql (FK target)
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The session that contains this task. A session may have many tasks.
    session_id           UUID NOT NULL REFERENCES agent_sessions(session_id),

    -- Denormalized copy of agent_sessions.external_session_id for fast
    -- cross-session lookups (e.g., "all tasks in SDLC trace XYZ").
    external_session_id  TEXT,

    -- Which agent owns this task. One of the seven agent names from
    -- agents.yaml: orchestrator, execution-agent, script-agent,
    -- monitoring-agent, analysis-agent, reporting-agent, notifications-agent.
    agent_name           TEXT NOT NULL,

    -- Task lifecycle state. Free-text + CHECK constraint so we can extend
    -- without an ENUM migration if the protocol grows.
    status               TEXT NOT NULL
                         CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),

    -- Optional link to the canonical artifact directory at
    -- artifacts/{test_run_id}/. Not all tasks have a test run (e.g.,
    -- orchestrator routing tasks).
    test_run_id          TEXT,

    -- The A2A `tasks/send` body the caller sent in.
    payload              JSONB NOT NULL,

    -- Result body returned to the caller; NULL until completed.
    result               JSONB,

    -- Structured error payload; NULL unless status = 'failed' or 'cancelled'.
    error                JSONB,

    -- Webhook URLs to notify on completion (one of the three callback
    -- patterns in V2 Section 14). May be NULL or an empty array.
    subscriber_endpoints JSONB,

    submitted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  agent_tasks IS 'A2A task lifecycle. One row per `tasks/send` invocation.';
COMMENT ON COLUMN agent_tasks.task_id              IS 'A2A task identifier (UUID).';
COMMENT ON COLUMN agent_tasks.session_id           IS 'Containing session (FK to agent_sessions.session_id).';
COMMENT ON COLUMN agent_tasks.external_session_id  IS 'Denormalized SDLC-wide session ID for fast queries.';
COMMENT ON COLUMN agent_tasks.agent_name           IS 'Owning agent (orchestrator or one of six specialists).';
COMMENT ON COLUMN agent_tasks.status               IS 'Lifecycle state: pending, running, completed, failed, cancelled.';
COMMENT ON COLUMN agent_tasks.test_run_id          IS 'Optional artifacts/{test_run_id}/ link.';
COMMENT ON COLUMN agent_tasks.payload              IS 'Inbound A2A `tasks/send` body.';
COMMENT ON COLUMN agent_tasks.result               IS 'Outbound result body; NULL until completed.';
COMMENT ON COLUMN agent_tasks.error                IS 'Structured error; NULL unless status is failed or cancelled.';
COMMENT ON COLUMN agent_tasks.subscriber_endpoints IS 'Webhook URLs to notify on completion (V2 Section 14).';

-- B-tree indexes for common queries (poll-by-task, list-by-session,
-- correlate-by-trace, status filter, recent activity).
CREATE INDEX IF NOT EXISTS idx_agent_tasks_session
    ON agent_tasks (session_id);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_external_session
    ON agent_tasks (external_session_id)
    WHERE external_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_tasks_test_run
    ON agent_tasks (test_run_id)
    WHERE test_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_tasks_status
    ON agent_tasks (status);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_status
    ON agent_tasks (agent_name, status);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_submitted_at
    ON agent_tasks (submitted_at DESC);
