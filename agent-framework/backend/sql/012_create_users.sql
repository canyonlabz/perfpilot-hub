-- =============================================================================
-- 012 - Table: users
-- =============================================================================
-- Web-UI identity row for Bring-Your-Own-Token. Today `user_id` is the
-- opaque `perfpilot_token` cookie value (same string stored on
-- agent_sessions.user_id and agent_threads.user_id). Later an identity
-- provider can fill `oid` and keep `user_id` stable or remap it.
--
-- Email is self-asserted until that identity provider lands. It is used
-- only as a recovery hint (resume / start fresh / temporary session).
--
-- Run from the `perfagent_state` database context.
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT,
    email         TEXT,
    oid           TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_oid UNIQUE (oid)
);

COMMENT ON TABLE  users IS
    'Web-UI user profile. user_id is the cookie principal today; oid is reserved for a future identity provider.';
COMMENT ON COLUMN users.user_id      IS 'Opaque perfpilot_token today; may become an IdP subject later.';
COMMENT ON COLUMN users.display_name IS 'Settings Profile display name. Not a secret.';
COMMENT ON COLUMN users.email        IS 'Self-asserted work email used as a recovery hint. Not cryptographically bound.';
COMMENT ON COLUMN users.oid          IS 'Future identity-provider object id. NULL until that integration lands.';

CREATE INDEX IF NOT EXISTS idx_users_email_lower
    ON users (lower(email))
    WHERE email IS NOT NULL;
