-- =============================================================================
-- 004 - Table: agent_checkpoints
-- =============================================================================
-- AG2-style state snapshot per long-running thread. Used by the orchestrator
-- and specialist agents to persist their working state so a container restart
-- mid-run does not lose progress (V2 doc Section 4.2 - "Resumable").
--
-- The composite primary key (thread_id, agent_name) means each agent has
-- exactly one current snapshot per thread. New writes are UPSERTs.
--
-- Run from the `perfagent_state` database context.
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    -- Typically equal to test_run_id, but agents are free to use any stable
    -- thread identifier (e.g., the orchestrator may use the parent task_id).
    thread_id    TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    state        JSONB NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (thread_id, agent_name)
);

COMMENT ON TABLE  agent_checkpoints IS 'AG2-style state snapshot per (thread, agent) for resumable long-running work.';
COMMENT ON COLUMN agent_checkpoints.thread_id  IS 'Stable thread identifier (typically test_run_id or parent task_id).';
COMMENT ON COLUMN agent_checkpoints.agent_name IS 'Owning agent.';
COMMENT ON COLUMN agent_checkpoints.state      IS 'Agent-defined state payload.';

CREATE INDEX IF NOT EXISTS idx_agent_checkpoints_agent
    ON agent_checkpoints (agent_name);
