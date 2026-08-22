-- =============================================================================
-- 006 - Table: tool_call_traces
-- =============================================================================
-- Audit trail of every MCP tool an agent fires. Powers debugging, cost
-- accounting, and the OpenTelemetry span pre-wiring (V2 doc Section 17).
--
-- Run from the `perfagent_state` database context.
-- Depends on: 003_create_agent_tasks.sql (FK target)
-- =============================================================================

CREATE TABLE IF NOT EXISTS tool_call_traces (
    id             BIGSERIAL PRIMARY KEY,

    -- Cascade-delete: when a task is purged, its tool-call audit goes with it.
    task_id        UUID NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,

    agent_name     TEXT NOT NULL,

    -- Tool name follows the namespace convention `<namespace>_<tool>` from
    -- the gateway-mcp catalog (e.g., `blazemeter_run_test`,
    -- `datadog_get_metrics`). Stored verbatim for audit fidelity.
    mcp_tool_name  TEXT NOT NULL,

    args           JSONB NOT NULL,
    result         JSONB,
    error          JSONB,
    latency_ms     INTEGER,

    called_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  tool_call_traces IS 'Audit trail of every MCP tool call fired by an agent.';
COMMENT ON COLUMN tool_call_traces.task_id       IS 'Owning task (FK; cascades on delete).';
COMMENT ON COLUMN tool_call_traces.mcp_tool_name IS 'Namespaced tool name from the gateway catalog.';
COMMENT ON COLUMN tool_call_traces.latency_ms    IS 'Round-trip latency in milliseconds.';

CREATE INDEX IF NOT EXISTS idx_tool_call_traces_task
    ON tool_call_traces (task_id);

CREATE INDEX IF NOT EXISTS idx_tool_call_traces_tool
    ON tool_call_traces (mcp_tool_name);

CREATE INDEX IF NOT EXISTS idx_tool_call_traces_called_at
    ON tool_call_traces (called_at DESC);
