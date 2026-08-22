-- =============================================================================
-- PerfMemory Schema: Ollama (768 dimensions)
-- =============================================================================
-- Embedding model: nomic-embed-text (768 dimensions)
-- Compatible with: Ollama running locally
--
-- Prerequisites:
--   1. PostgreSQL 18+ with pgvector extension available
--   2. Database 'perfmemory' created
--   3. Connected as a user with CREATE privileges
--
-- Usage:
--   psql -h localhost -U perfadmin -d perfmemory -f schema_ollama.sql
-- =============================================================================

-- Enable pgvector extension (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- Table: debug_sessions
-- One row per debug session. Holds session-level metadata.
-- =============================================================================
CREATE TABLE IF NOT EXISTS debug_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    system_under_test       TEXT NOT NULL,
    test_run_id             TEXT NOT NULL,
    script_name             TEXT,
    auth_flow_type          TEXT,
    environment             TEXT,  -- specific environment name (e.g., "QA1", "STG-East")
    total_iterations        INT,
    final_outcome           TEXT NOT NULL,
    resolution_attempt_id   UUID,
    created_by              TEXT,
    notes                   TEXT,
    started_at              TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW(),

    -- Taxonomy fields (added in migration 001, updated in migration 003)
    system_alias            TEXT NOT NULL DEFAULT '',
    service_name            TEXT NOT NULL DEFAULT '',
    env_type                TEXT NOT NULL DEFAULT '',
    environment_alias       TEXT NOT NULL DEFAULT '',  -- retained, unused (see migration 003)
    auth_alias              TEXT NOT NULL DEFAULT ''
);

-- =============================================================================
-- Table: debug_attempts
-- One row per debug iteration. Holds attempt-level data with embedding.
-- =============================================================================
CREATE TABLE IF NOT EXISTS debug_attempts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL REFERENCES debug_sessions(id),
    iteration_number    INT NOT NULL,

    -- Metadata (filtering)
    error_category      TEXT,
    severity            TEXT,
    response_code       TEXT,
    outcome             TEXT NOT NULL,

    -- Stored (returned with search results)
    hostname            TEXT,
    sampler_name        TEXT,
    api_endpoint        TEXT,
    symptom_text        TEXT NOT NULL,
    diagnosis           TEXT,
    fix_description     TEXT,
    fix_type            TEXT,
    component_type      TEXT,
    manifest_excerpt    TEXT,

    -- Test case / step context (added in migration 001)
    test_case_id        TEXT NOT NULL DEFAULT '',
    test_case_name      TEXT NOT NULL DEFAULT '',
    test_step_id        TEXT NOT NULL DEFAULT '',
    test_step_name      TEXT NOT NULL DEFAULT '',

    -- System
    embedding_model     TEXT NOT NULL,
    embedding           vector(768),
    is_verified         BOOLEAN DEFAULT FALSE,
    is_active           BOOLEAN DEFAULT TRUE,
    confirmed_count     INT DEFAULT 1,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- Foreign key: debug_sessions.resolution_attempt_id -> debug_attempts.id
-- Added after both tables exist to avoid circular dependency during creation.
-- =============================================================================
ALTER TABLE debug_sessions
    ADD CONSTRAINT fk_resolution_attempt
    FOREIGN KEY (resolution_attempt_id)
    REFERENCES debug_attempts(id);

-- =============================================================================
-- Indexes
-- =============================================================================

-- HNSW vector index for semantic similarity search
CREATE INDEX IF NOT EXISTS idx_attempts_embedding
    ON debug_attempts
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- B-tree indexes on debug_attempts for metadata filtering
CREATE INDEX IF NOT EXISTS idx_attempts_error_category
    ON debug_attempts (error_category);

CREATE INDEX IF NOT EXISTS idx_attempts_outcome
    ON debug_attempts (outcome);

CREATE INDEX IF NOT EXISTS idx_attempts_session_id
    ON debug_attempts (session_id);

CREATE INDEX IF NOT EXISTS idx_attempts_hostname
    ON debug_attempts (hostname);

-- B-tree indexes on debug_sessions for metadata filtering
CREATE INDEX IF NOT EXISTS idx_sessions_system
    ON debug_sessions (system_under_test);

CREATE INDEX IF NOT EXISTS idx_sessions_environment
    ON debug_sessions (environment);

CREATE INDEX IF NOT EXISTS idx_sessions_outcome
    ON debug_sessions (final_outcome);

-- B-tree indexes on taxonomy columns
CREATE INDEX IF NOT EXISTS idx_sessions_system_alias
    ON debug_sessions (system_alias);

CREATE INDEX IF NOT EXISTS idx_sessions_service
    ON debug_sessions (service_name);

CREATE INDEX IF NOT EXISTS idx_sessions_env_type
    ON debug_sessions (env_type);

CREATE INDEX IF NOT EXISTS idx_sessions_env_alias
    ON debug_sessions (environment_alias);

CREATE INDEX IF NOT EXISTS idx_attempts_test_case
    ON debug_attempts (test_case_id);
