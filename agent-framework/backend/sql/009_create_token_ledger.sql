-- =============================================================================
-- 009 - Table: token_ledger
-- =============================================================================
-- Per-iteration token usage and context pressure metrics for the multi-turn
-- tool loop. Enables cost accounting, compaction effectiveness analysis, and
-- UI dashboards showing token consumption trends across test runs.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 003_create_agent_tasks.sql (FK target)
-- =============================================================================

CREATE TABLE IF NOT EXISTS token_ledger (
    id                    BIGSERIAL PRIMARY KEY,

    -- Cascade-delete: when a task is purged, its token metrics go with it.
    task_id               UUID NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,

    agent_name            TEXT NOT NULL,

    -- Which iteration of the multi-turn tool loop this row captures.
    loop_iteration        INTEGER NOT NULL,

    -- Token counts from tiktoken estimation (pre-LLM-call snapshot).
    prompt_tokens         INTEGER NOT NULL,

    -- Completion tokens from the LLM response (when available from AG2 usage).
    completion_tokens     INTEGER,

    -- Estimated tokens contributed by MCP tool results in this iteration.
    mcp_overhead_tokens   INTEGER,

    -- Percentage of model context window used (0.0 – 1.0 scale).
    utilization_pct       REAL NOT NULL,

    -- Whether compaction was triggered this iteration.
    compaction_triggered  BOOLEAN NOT NULL DEFAULT FALSE,

    -- Number of tool results compacted (0 if compaction was not triggered).
    compacted_count       INTEGER NOT NULL DEFAULT 0,

    -- Tokens freed by compaction (0 if not triggered).
    tokens_freed          INTEGER NOT NULL DEFAULT 0,

    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  token_ledger IS 'Per-iteration token usage metrics for the multi-turn tool loop.';
COMMENT ON COLUMN token_ledger.task_id             IS 'Owning task (FK; cascades on delete).';
COMMENT ON COLUMN token_ledger.loop_iteration      IS 'Loop round number (1-indexed).';
COMMENT ON COLUMN token_ledger.prompt_tokens       IS 'Estimated prompt tokens before LLM call.';
COMMENT ON COLUMN token_ledger.utilization_pct     IS 'Context window utilization (0.0-1.0).';
COMMENT ON COLUMN token_ledger.compaction_triggered IS 'Whether compaction ran this iteration.';

CREATE INDEX IF NOT EXISTS idx_token_ledger_task
    ON token_ledger (task_id);

CREATE INDEX IF NOT EXISTS idx_token_ledger_task_iteration
    ON token_ledger (task_id, loop_iteration);

CREATE INDEX IF NOT EXISTS idx_token_ledger_recorded_at
    ON token_ledger (recorded_at DESC);
