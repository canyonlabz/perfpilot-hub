-- =============================================================================
-- 013 - Table: user_secrets
-- =============================================================================
-- One named credential pack per (user, component, token name). The pack
-- holds metadata only. Encrypted field values live in user_secret_fields
-- so any vendor key name (token, api_key, api_secret, app_key, ...) can
-- be stored without a per-vendor schema.
--
-- `component` matches the Settings catalog (github, blazemeter, datadog,
-- confluence-cloud, confluence-onprem, ado, jira, ...).
-- `name` is the user-chosen token name shown in Project dropdowns.
--
-- There is no "active" flag here. The active project row in
-- user_project_bindings chooses which token name each MCP uses.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 012_create_users.sql
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_secrets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    component   TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_secrets_user_component_name
        UNIQUE (user_id, component, name)
);

COMMENT ON TABLE  user_secrets IS
    'Named credential pack metadata. Values are in user_secret_fields. Never store plaintext here.';
COMMENT ON COLUMN user_secrets.component IS
    'Settings catalog component id (github, blazemeter, datadog, confluence-cloud, ...).';
COMMENT ON COLUMN user_secrets.name IS
    'User-chosen token name. Project bindings refer to this name, not the UUID.';

CREATE INDEX IF NOT EXISTS idx_user_secrets_user_component
    ON user_secrets (user_id, component);
