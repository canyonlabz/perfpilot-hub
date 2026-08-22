-- =============================================================================
-- 002 - Table: agent_sessions
-- =============================================================================
-- One row per UI / IDE / A2A session. A session is the broader conversational
-- context within which one or more A2A tasks happen. See V2 doc Section 4.3
-- for the three-ID model (external_session_id > session_id > task_id).
--
-- Run from the `perfagent_state` database context (provision.py handles the
-- context switch).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Optional global session ID propagated from an upstream SDLC coordinator
    -- (e.g., a QA Functional framework). Opaque to us. Used as the trace ID
    -- correlation key in OpenTelemetry once Epic 4 lights up.
    external_session_id  TEXT,

    -- Where this session originated. Free-text but expected values are:
    --   'web_ui'        - CopilotKit React UI
    --   'cursor'        - Cursor IDE via MCP
    --   'claude'        - Claude IDE via MCP
    --   'a2a_external'  - external AI agent framework via A2A protocol
    --   'cli'           - operator CLI / direct script
    source               TEXT NOT NULL,

    -- Epic 3: free-text user hint resolved from the X-User-Id header or a
    -- server-issued cookie (best-effort; may be NULL).
    -- Epic 4: ties this to an EntraID principal (the oid claim) once auth
    -- middleware is on. Column name matches agent_threads.user_id for
    -- naming consistency across the schema.
    user_id              TEXT,

    -- UI-specific data, client info, browser fingerprint, etc.
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,

    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- NULL while the session is active. Populated when the session ends
    -- (explicit close, browser tab close, A2A connection drop, or timeout).
    ended_at             TIMESTAMPTZ,

    -- Refreshed on every inbound interaction; used for inactivity timeout.
    last_activity_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  agent_sessions IS 'PerfPilot Agents internal session per UI/IDE/A2A connection. See V2 doc Section 4.3.';
COMMENT ON COLUMN agent_sessions.session_id          IS 'Internal session identifier (UUID).';
COMMENT ON COLUMN agent_sessions.external_session_id IS 'Optional SDLC-wide session ID from upstream coordinator; opaque.';
COMMENT ON COLUMN agent_sessions.source              IS 'Origin of the session: web_ui, cursor, claude, a2a_external, cli.';
COMMENT ON COLUMN agent_sessions.user_id             IS 'Epic 3: resolved from X-User-Id header or server-issued cookie. Epic 4: EntraID oid claim.';
COMMENT ON COLUMN agent_sessions.metadata            IS 'Free-form JSONB payload (UI client info, etc.).';
COMMENT ON COLUMN agent_sessions.last_activity_at    IS 'Last inbound activity; used for inactivity timeout.';

-- B-tree indexes for common lookups
CREATE INDEX IF NOT EXISTS idx_agent_sessions_external
    ON agent_sessions (external_session_id)
    WHERE external_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_sessions_source
    ON agent_sessions (source);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_active
    ON agent_sessions (last_activity_at)
    WHERE ended_at IS NULL;
