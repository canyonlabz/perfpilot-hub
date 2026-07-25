-- =============================================================================
-- 007 - Table: hitl_approvals
-- =============================================================================
-- Record of every HITL (Human-In-The-Loop) approval / rejection / pending
-- prompt. Powers the multi-round revise loop pattern (V2 doc Section 15) used
-- heavily by the reporting-agent.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 003_create_agent_tasks.sql (FK target)
-- =============================================================================

CREATE TABLE IF NOT EXISTS hitl_approvals (
    id             BIGSERIAL PRIMARY KEY,

    -- Cascade-delete: when a task is purged, its HITL audit goes with it.
    task_id        UUID NOT NULL REFERENCES agent_tasks(task_id) ON DELETE CASCADE,

    -- Full HITL prompt as presented to the human (rendered text + structured
    -- diff payload + revise-options).
    prompt         JSONB NOT NULL,

    -- Decision state. 'pending' rows are open prompts awaiting a human.
    decision       TEXT NOT NULL
                   CHECK (decision IN ('approved', 'rejected', 'pending')),

    -- Free-text feedback the human provides on rejection (drives the next
    -- revise round). NULL when decision is 'approved' or 'pending'.
    feedback       TEXT,

    -- Epic 3: free-text identifier supplied by the UI/IDE.
    -- Epic 4: EntraID principal once auth middleware is on.
    decided_by     TEXT,

    -- Set when decision transitions away from 'pending'. NULL while pending.
    decided_at     TIMESTAMPTZ
);

COMMENT ON TABLE  hitl_approvals IS 'Audit log of HITL approval prompts and their outcomes.';
COMMENT ON COLUMN hitl_approvals.task_id    IS 'Owning task (FK; cascades on delete).';
COMMENT ON COLUMN hitl_approvals.prompt     IS 'Full prompt payload presented to the human.';
COMMENT ON COLUMN hitl_approvals.decision   IS 'approved, rejected, or pending.';
COMMENT ON COLUMN hitl_approvals.feedback   IS 'Free-text rejection feedback that drives the next revise round.';
COMMENT ON COLUMN hitl_approvals.decided_by IS 'Epic 3: free-text. Epic 4: EntraID principal.';

CREATE INDEX IF NOT EXISTS idx_hitl_approvals_task
    ON hitl_approvals (task_id);

CREATE INDEX IF NOT EXISTS idx_hitl_approvals_pending
    ON hitl_approvals (task_id)
    WHERE decision = 'pending';
