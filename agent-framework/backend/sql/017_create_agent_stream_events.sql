-- =============================================================================
-- 017 - Table: agent_stream_events
-- =============================================================================
-- Append-only store for the AG2 1.3.2 event stream. One row is one native
-- event, serialized as JSON. A Postgres implementation of ag2.history.Storage
-- reads and writes this table so a later turn can replay the stream.
--
-- This table complements conversation_messages. It does not replace it.
-- conversation_messages remains the human transcript (system, user,
-- assistant, and tool roles) used to render chat. It cannot rebuild a native
-- AG2 event, including tool-call batches, usage, and event ids.
-- agent_stream_events keeps those events intact.
--
-- The Storage methods map onto this table as follows. Their parameters are
-- named stream_id; the column they key is thread_id.
--
--   save_event(event, context)      -> INSERT one row:
--                                        thread_id  = context.stream.id
--                                        event_type = type(event).__name__
--                                        event      = JSON serialization
--   get_history(stream_id)          -> SELECT event WHERE thread_id = $1
--                                        ORDER BY id
--   set_history(stream_id, events)  -> DELETE WHERE thread_id = $1, then
--                                        INSERT the replacement rows
--   drop_history(stream_id)         -> DELETE WHERE thread_id = $1
--
-- Events are JSON only. Do not store a pickle payload in event.
--
-- thread_id is TEXT, the same type used by conversation_messages,
-- agent_checkpoints, and agent_threads. For the Web UI it is the CopilotKit
-- thread id. For A2A it is the client-provided context id. There is no
-- foreign key to agent_threads: a stream may be written before a thread row
-- exists, which is also how conversation_messages works.
-- agent_checkpoints stays. It is a separate per-agent snapshot and is not
-- removed by this script.
--
-- Run from the `perfagent_state` database context.
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_stream_events (
    -- Append order. Replay reads ORDER BY id.
    id           BIGSERIAL PRIMARY KEY,

    -- Conversation key. CopilotKit thread id for the Web UI, and the
    -- client-provided A2A context id for A2A.
    thread_id    TEXT NOT NULL,

    -- AG2 event class name, such as ModelRequest or ToolResultEvent.
    -- Present so a row can be identified without reading the JSON payload.
    event_type   TEXT NOT NULL,

    -- JSON serialization of the AG2 event. Never a pickle payload.
    event        JSONB NOT NULL,

    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  agent_stream_events IS 'Append-only AG2 1.3.2 event stream. Complements the conversation_messages transcript.';
COMMENT ON COLUMN agent_stream_events.id         IS 'Monotonic append order; reads ORDER BY id to replay in production order.';
COMMENT ON COLUMN agent_stream_events.thread_id  IS 'Conversation key. CopilotKit thread id for the Web UI; A2A context id for A2A.';
COMMENT ON COLUMN agent_stream_events.event_type IS 'AG2 event class name. Identifies the row without deserializing event.';
COMMENT ON COLUMN agent_stream_events.event      IS 'JSON-serialized AG2 event. Never a pickle payload.';

-- Replay one thread in append order:
--   SELECT event FROM agent_stream_events WHERE thread_id = $1 ORDER BY id;
CREATE INDEX IF NOT EXISTS idx_agent_stream_events_thread
    ON agent_stream_events (thread_id, id);

-- =============================================================================
-- Rollback, manual. These migrations are otherwise forward-only. Run only to
-- undo this script. Dropping the table also drops its index and sequence.
--
--   DROP TABLE IF EXISTS agent_stream_events;
-- =============================================================================
