-- =============================================================================
-- 005 - Table: conversation_messages
-- =============================================================================
-- Full conversation history per task / thread. Append-only log used by the
-- AG-UI bridge to render the chat transcript and by agents to reconstruct
-- multi-turn context after a restart.
--
-- Run from the `perfagent_state` database context.
-- =============================================================================

CREATE TABLE IF NOT EXISTS conversation_messages (
    id           BIGSERIAL PRIMARY KEY,

    -- Same thread identifier convention as agent_checkpoints.thread_id.
    thread_id    TEXT NOT NULL,

    agent_name   TEXT NOT NULL,

    -- Message author per the standard chat-completion roles.
    role         TEXT NOT NULL
                 CHECK (role IN ('system', 'user', 'assistant', 'tool')),

    -- Full message content as JSONB so we can carry tool-call frames,
    -- structured outputs, and rich attachments without schema migrations.
    content      JSONB NOT NULL,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  conversation_messages IS 'Append-only conversation log per (thread, agent).';
COMMENT ON COLUMN conversation_messages.role    IS 'Chat role: system, user, assistant, or tool.';
COMMENT ON COLUMN conversation_messages.content IS 'Full message body (JSONB to allow tool calls, attachments, etc.).';

-- Time-ordered retrieval per thread is the dominant query pattern.
CREATE INDEX IF NOT EXISTS idx_conversation_thread_time
    ON conversation_messages (thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_conversation_agent
    ON conversation_messages (agent_name);
