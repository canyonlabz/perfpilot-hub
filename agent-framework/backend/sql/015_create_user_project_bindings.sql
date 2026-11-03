-- =============================================================================
-- 015 - Table: user_project_bindings
-- =============================================================================
-- Per-user Settings → Projects row. Project identity (display name, aliases,
-- workspace/space URLs) stays in environments.yaml. This table stores only
-- the user's enable/active flags and the token *names* bound to each
-- integration.
--
-- At most one row per user may have is_active = TRUE. Switching the active
-- project in the Web UI updates this flag; MCP servers resolve credentials
-- from the active row.
--
-- Token-name columns are plain text (not FKs) so deleting a secret can
-- clear the binding in application code without a cross-table rename trap.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 012_create_users.sql
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_project_bindings (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    project_key            TEXT NOT NULL,
    is_enabled             BOOLEAN NOT NULL DEFAULT FALSE,
    is_active              BOOLEAN NOT NULL DEFAULT FALSE,
    confluence_token_name  TEXT,
    blazemeter_token_name  TEXT,
    datadog_token_name     TEXT,
    github_token_name      TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_project_bindings_user_project
        UNIQUE (user_id, project_key)
);

COMMENT ON TABLE  user_project_bindings IS
    'Per-user project enable/active flags and Secrets token-name bindings. Project config stays in environments.yaml.';
COMMENT ON COLUMN user_project_bindings.project_key IS
    'Top-level key from environments.yaml (case stored as written; lookups are case-insensitive in application code).';
COMMENT ON COLUMN user_project_bindings.is_active IS
    'When TRUE this is the project Web-UI chats and MCP resolve use. At most one per user.';
COMMENT ON COLUMN user_project_bindings.confluence_token_name IS
    'user_secrets.name for confluence-cloud or confluence-onprem. NULL means None selected.';
COMMENT ON COLUMN user_project_bindings.blazemeter_token_name IS
    'user_secrets.name for blazemeter. NULL means None selected.';
COMMENT ON COLUMN user_project_bindings.datadog_token_name IS
    'user_secrets.name for datadog. NULL means None selected.';
COMMENT ON COLUMN user_project_bindings.github_token_name IS
    'user_secrets.name for github. NULL means None selected.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_project_bindings_one_active
    ON user_project_bindings (user_id)
    WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_user_project_bindings_user
    ON user_project_bindings (user_id, is_enabled, is_active);
