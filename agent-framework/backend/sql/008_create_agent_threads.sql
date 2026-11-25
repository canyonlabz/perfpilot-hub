-- =============================================================================
-- 008 - Table: agent_threads
-- =============================================================================
-- Persistent conversation container, the durable counterpart to the transient
-- `agent_sessions` row. See V2 doc Section 4.3 and the F3.7 plan in
-- `docs/plans/Epic-3-Implementation-Status.md` Section 5.
--
-- Lifetime model:
--
--   * A `Session` is the in-the-moment runtime - one browser tab connection
--     or one A2A peer attachment. It lives in `agent_sessions` and ends when
--     the connection drops. After Epic 4 EntraID lights up, sessions will
--     definitely not survive past the current conversation.
--   * A `Thread` is the persistent conversation. It survives across sessions,
--     devices, and days. A user opens the Web UI tomorrow with the same
--     CopilotKit `threadId` and resumes where they left off.
--
-- Every thread is user-scoped: `user_id` is always set and threads never
-- cross users (enforced by the CHECK constraint at the bottom of this
-- table). The conversation identifier is unified across surfaces:
--
--   * Web UI / IDE / CLI -> `thread_id` is the CopilotKit `threadId`.
--   * A2A                 -> `thread_id` is the client `contextId`.
--       (Protect ya neck: one user's conversation never crosses over to
--        another's.)
--
-- There is no separate external label. Authorization filters always run
-- server-side on `user_id`.
--
-- `thread_id` is TEXT (not UUID) to match `conversation_messages.thread_id`
-- and `agent_checkpoints.thread_id`, which were laid down in F3.3 with TEXT
-- so callers (CopilotKit, AG2 checkpointer, A2A `contextId`) could supply
-- whatever stable identifier they liked. The server-side DEFAULT generates
-- a UUID hex only when the caller does not supply one (Web UI / CLI).
--
-- Run from the `perfagent_state` database context.
-- Depends on: 002_create_agent_sessions.sql (vocabulary alignment only;
-- no FK because a thread outlives any single session).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_threads (
    -- Internal stable identifier, unified across surfaces. For Web UI
    -- threads this is the CopilotKit `threadId`; for A2A it is the client
    -- `contextId`. The DEFAULT mints a UUID hex only when the caller does
    -- not supply one (Web UI / CLI). A2A always supplies its `contextId`.
    thread_id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,

    -- Owner principal (required -- every thread is user-scoped).
    -- Epic 3: resolved from `X-PerfPilot-Token` header or a server-issued
    -- cookie. Epic 4: EntraID oid claim (set by the EntraID middleware).
    user_id              TEXT NOT NULL,

    -- Origin of the thread. Free-text but expected to mirror the
    -- agent_sessions.source vocabulary:
    --   'web_ui'       - CopilotKit React UI
    --   'cursor'       - Cursor IDE via MCP
    --   'claude'       - Claude IDE via MCP
    --   'a2a_external' - external AI agent framework via A2A protocol
    --   'cli'          - operator CLI / direct script
    source               TEXT NOT NULL,

    -- Display title for the sidebar list. Auto-generated from the first user
    -- message in F3.7.6b unless the operator renames it.
    title                TEXT,

    -- Soft-delete / archive workflow. Reads filter on this column.
    status               TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'archived', 'deleted')),

    -- Free-form JSONB for thread-scoped metadata (e.g. test_run_id anchor,
    -- pinned agent name, UI preferences). No schema; agents own their keys.
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Bumped each time a new conversation_messages row is appended. Used to
    -- order the sidebar list ("most-recently-active first"). NULL until the
    -- first message lands.
    last_message_at      TIMESTAMPTZ,

    -- Every thread is user-scoped; threads never cross users. The column
    -- is already NOT NULL, but the named CHECK documents the invariant.
    CONSTRAINT chk_agent_threads_user_scoped
        CHECK (user_id IS NOT NULL)
);

COMMENT ON TABLE  agent_threads IS 'Persistent conversation container. Survives across sessions, devices, days.';
COMMENT ON COLUMN agent_threads.thread_id          IS 'Internal stable thread ID. Equals CopilotKit threadId for Web UI; server-minted UUID for A2A.';
COMMENT ON COLUMN agent_threads.user_id            IS 'Web UI / IDE / CLI owner. Epic 3: X-User-Id or cookie. Epic 4: EntraID oid.';
COMMENT ON COLUMN agent_threads.external_thread_id IS 'A2A persistent thread label from X-External-Thread-Id header. Connects upstream sessions across time.';
COMMENT ON COLUMN agent_threads.source             IS 'Origin: web_ui, cursor, claude, a2a_external, cli (matches agent_sessions.source).';
COMMENT ON COLUMN agent_threads.title              IS 'Sidebar display title. Auto-generated from first user message; operator-renameable.';
COMMENT ON COLUMN agent_threads.status             IS 'Lifecycle: active, archived, deleted.';
COMMENT ON COLUMN agent_threads.metadata           IS 'Free-form JSONB; agents own their keys.';
COMMENT ON COLUMN agent_threads.last_message_at    IS 'Bumped on each new conversation_messages append; orders the sidebar list.';

-- Sidebar list query: WHERE user_id = $1 AND status = $2 ORDER BY last_message_at DESC.
CREATE INDEX IF NOT EXISTS idx_agent_threads_user
    ON agent_threads (user_id, status, last_message_at DESC NULLS LAST);

-- A2A resumption and Web UI resume both look up by thread_id, which is the
-- PRIMARY KEY -- no additional index is needed.
