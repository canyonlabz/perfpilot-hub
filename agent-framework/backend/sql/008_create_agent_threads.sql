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
-- Ownership has exactly two flavours, enforced by the CHECK constraint at
-- the bottom of this table:
--
--   * Web UI / IDE / CLI threads  -> `user_id` is set
--       (Epic 3: resolved from X-User-Id header or server-issued cookie.
--        Epic 4: EntraID oid claim.)
--   * A2A-originated threads      -> `external_thread_id` is set
--       (The persistent label the upstream agent framework sends in
--        X-External-Thread-Id to reconnect across upstream sessions.)
--
-- Authorization filters always run server-side on those two columns. The
-- worlds are intentionally disjoint - a Web UI user cannot enumerate A2A
-- threads and vice versa unless an Epic 4 admin role is later added.
--
-- `thread_id` is TEXT (not UUID) to match `conversation_messages.thread_id`
-- and `agent_checkpoints.thread_id`, which were laid down in F3.3 with TEXT
-- so callers (CopilotKit, AG2 checkpointer) could supply whatever stable
-- identifier they liked. The server-side DEFAULT generates a UUID hex when
-- the caller does not provide one (typical for A2A-minted threads).
--
-- Run from the `perfagent_state` database context.
-- Depends on: 002_create_agent_sessions.sql (vocabulary alignment only;
-- no FK because a thread outlives any single session).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_threads (
    -- Internal stable identifier. For Web UI threads this is the value the
    -- CopilotKit React provider sends as `threadId` on every `RunAgentInput`.
    -- The DEFAULT lets A2A-side callers omit the value and have the server
    -- mint one (returned to the caller as the `X-Thread-Id` response header).
    thread_id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,

    -- Web UI / IDE / CLI owner principal.
    -- Epic 3: resolved from `X-User-Id` header or a server-issued cookie.
    -- Epic 4: EntraID oid claim (set by the EntraID middleware).
    -- NULL for pure-A2A threads.
    user_id              TEXT,

    -- Persistent thread label sent by an upstream A2A caller as the
    -- `X-External-Thread-Id` request header. This is the column that
    -- connects an upstream's Monday session with their Tuesday session
    -- for the same continuing investigation. NULL for Web UI / IDE / CLI
    -- threads. UNIQUE so the A2A executor can do idempotent lookup-or-create.
    external_thread_id   TEXT,

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

    -- Every thread must be owned by either a user (Web UI / IDE / CLI) or
    -- by an external thread label (A2A). Never both NULL.
    CONSTRAINT chk_agent_threads_owner
        CHECK (user_id IS NOT NULL OR external_thread_id IS NOT NULL),

    -- External thread labels are unique across the system. Two different
    -- upstream callers presenting the same label is treated as the same
    -- thread; this is by design and is the documented A2A resumption rule.
    CONSTRAINT uq_agent_threads_external_thread_id
        UNIQUE (external_thread_id)
);

COMMENT ON TABLE  agent_threads IS 'Persistent conversation container. Survives across sessions, devices, days. See F3.7 plan in docs/plans/Epic-3-Implementation-Status.md.';
COMMENT ON COLUMN agent_threads.thread_id          IS 'Internal stable thread ID. Equals CopilotKit threadId for Web UI; server-minted UUID for A2A.';
COMMENT ON COLUMN agent_threads.user_id            IS 'Web UI / IDE / CLI owner. Epic 3: X-User-Id or cookie. Epic 4: EntraID oid.';
COMMENT ON COLUMN agent_threads.external_thread_id IS 'A2A persistent thread label from X-External-Thread-Id header. Connects upstream sessions across time.';
COMMENT ON COLUMN agent_threads.source             IS 'Origin: web_ui, cursor, claude, a2a_external, cli (matches agent_sessions.source).';
COMMENT ON COLUMN agent_threads.title              IS 'Sidebar display title. Auto-generated from first user message; operator-renameable.';
COMMENT ON COLUMN agent_threads.status             IS 'Lifecycle: active, archived, deleted.';
COMMENT ON COLUMN agent_threads.metadata           IS 'Free-form JSONB; agents own their keys.';
COMMENT ON COLUMN agent_threads.last_message_at    IS 'Bumped on each new conversation_messages append; orders the sidebar list.';

-- Sidebar list query: WHERE user_id = $1 AND status = $2 ORDER BY last_message_at DESC.
-- Partial index keeps the A2A-only threads out of the user-side B-tree.
CREATE INDEX IF NOT EXISTS idx_agent_threads_user
    ON agent_threads (user_id, status, last_message_at DESC NULLS LAST)
    WHERE user_id IS NOT NULL;

-- A2A resumption query: WHERE external_thread_id = $1. The UNIQUE constraint
-- already provides an index, but list the intent explicitly for the next
-- maintainer reading this file.
-- (UNIQUE constraint above creates `agent_threads_external_thread_id_key`.)
