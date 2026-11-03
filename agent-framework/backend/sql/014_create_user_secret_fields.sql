-- =============================================================================
-- 014 - Table: user_secret_fields
-- =============================================================================
-- Generic encrypted key/value fields for one user_secrets row.
-- `field_name` is a free string. Application code decrypts to a
-- dict[str, str] and each MCP reads the keys it needs.
--
-- Ciphertext is AES-256-GCM. nonce is required for decrypt. key_version
-- supports later rotation of PERFPLOT_SECRETS_MASTER_KEY.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 013_create_user_secrets.sql
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_secret_fields (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    secret_id    UUID NOT NULL REFERENCES user_secrets (id) ON DELETE CASCADE,
    field_name   TEXT NOT NULL,
    ciphertext   BYTEA NOT NULL,
    nonce        BYTEA NOT NULL,
    key_version  INT NOT NULL DEFAULT 1,
    CONSTRAINT uq_user_secret_fields_secret_field
        UNIQUE (secret_id, field_name)
);

COMMENT ON TABLE  user_secret_fields IS
    'Encrypted field values for a named secret. Generic key/value; no per-vendor columns.';
COMMENT ON COLUMN user_secret_fields.field_name IS
    'Free-string key (token, api_key, api_secret, app_key, or a future vendor field).';
COMMENT ON COLUMN user_secret_fields.ciphertext IS
    'AES-256-GCM ciphertext. Never log or return this to the browser.';
COMMENT ON COLUMN user_secret_fields.nonce IS
    'Per-field nonce for AES-256-GCM.';
COMMENT ON COLUMN user_secret_fields.key_version IS
    'Which app-key version produced this ciphertext. Default 1.';

CREATE INDEX IF NOT EXISTS idx_user_secret_fields_secret
    ON user_secret_fields (secret_id);
