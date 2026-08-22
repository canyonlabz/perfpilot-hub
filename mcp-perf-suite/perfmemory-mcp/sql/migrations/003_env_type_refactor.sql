-- =============================================================================
-- PerfMemory Migration: 003 — Environment Column Refactor
-- =============================================================================
-- Adds env_type column and repurposes environment to hold specific environment
-- names (e.g., "QA-1", "PERF-1") instead of canonical type values.
--
-- Column role changes:
--   BEFORE:
--     environment       → canonical env type (dev, qa, uat, staging, prod)
--     environment_alias → specific environment name (QA-1, PERF-1, etc.)
--
--   AFTER:
--     env_type          → canonical env type (dev, qa, uat, staging, prod)
--     environment       → specific environment name (QA-1, PERF-1, etc.)
--     environment_alias → RETAINED (unused — preserved for historical reference)
--
-- This script is IDEMPOTENT — safe to run multiple times (ADD COLUMN IF NOT
-- EXISTS, conditional UPDATE with guard clauses).
--
-- Prerequisites:
--   - debug_sessions table exists with environment and environment_alias columns
--     (created by schema_openai.sql / schema_ollama.sql + migration 001)
--
-- Usage:
--   psql -h localhost -U perfadmin -d perfmemory -f 003_env_type_refactor.sql
-- =============================================================================

-- =============================================================================
-- Step 1: Add env_type column
-- =============================================================================
-- Stores the canonical environment type from taxonomy.yaml[environment_types].
-- Replaces the semantic role of the old `environment` column.

ALTER TABLE debug_sessions
    ADD COLUMN IF NOT EXISTS env_type TEXT NOT NULL DEFAULT '';

DO $$ BEGIN RAISE NOTICE 'Step 1 complete: env_type column added (or already exists)'; END $$;

-- =============================================================================
-- Step 2: Backfill env_type from existing environment values
-- =============================================================================
-- The current `environment` column stores canonical type values (dev, qa, etc.).
-- Copy those values into the new env_type column. Only backfills rows where
-- env_type is still empty to avoid overwriting data on re-run.

UPDATE debug_sessions
SET env_type = environment
WHERE environment IS NOT NULL
  AND environment != ''
  AND env_type = '';

DO $$
DECLARE
    backfilled_count INT;
BEGIN
    SELECT COUNT(*) INTO backfilled_count
    FROM debug_sessions WHERE env_type != '';
    RAISE NOTICE 'Step 2 complete: env_type backfilled from environment (% rows with env_type set)', backfilled_count;
END $$;

-- =============================================================================
-- Step 3: Backfill environment from environment_alias
-- =============================================================================
-- The current `environment_alias` column stores specific environment names.
-- Move those values into `environment`, giving it its new role as the specific
-- environment name column.

UPDATE debug_sessions
SET environment = environment_alias
WHERE environment_alias IS NOT NULL
  AND environment_alias != '';

DO $$
DECLARE
    moved_count INT;
BEGIN
    SELECT COUNT(*) INTO moved_count
    FROM debug_sessions
    WHERE environment_alias IS NOT NULL AND environment_alias != '';
    RAISE NOTICE 'Step 3 complete: environment backfilled from environment_alias (% rows)', moved_count;
END $$;

-- =============================================================================
-- Step 3b: Clear stale type values from environment
-- =============================================================================
-- Rows where environment_alias was empty still have the old type value (e.g.,
-- "qa") sitting in the `environment` column. That value now lives in env_type.
-- Clear `environment` for these rows so it doesn't hold a misleading value in
-- its new role as "specific environment name."

UPDATE debug_sessions
SET environment = ''
WHERE (environment_alias IS NULL OR environment_alias = '')
  AND environment IS NOT NULL
  AND environment != ''
  AND env_type != '';

DO $$
DECLARE
    cleared_count INT;
BEGIN
    SELECT COUNT(*) INTO cleared_count
    FROM debug_sessions
    WHERE (environment_alias IS NULL OR environment_alias = '')
      AND environment = ''
      AND env_type != '';
    RAISE NOTICE 'Step 3b complete: cleared stale type values from environment (% rows)', cleared_count;
END $$;

-- =============================================================================
-- Step 4: Add index on env_type
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_sessions_env_type
    ON debug_sessions (env_type);

DO $$ BEGIN RAISE NOTICE 'Step 4 complete: idx_sessions_env_type index created (or already exists)'; END $$;

-- =============================================================================
-- Note: environment_alias column and idx_sessions_env_alias index are RETAINED
-- =============================================================================
-- The environment_alias column is no longer read or written by active code paths
-- but is preserved in the database for historical reference. A future migration
-- can drop it after the beta period if warranted.

-- =============================================================================
-- Validation
-- =============================================================================
-- Run after migration to verify the schema:
--
--   SELECT column_name, data_type, column_default
--   FROM information_schema.columns
--   WHERE table_name = 'debug_sessions'
--     AND column_name IN ('environment', 'env_type', 'environment_alias')
--   ORDER BY column_name;
--
-- Expected result:
--   environment       | text | (environment now holds specific env names)
--   environment_alias | text | '' (retained, unused)
--   env_type          | text | '' (new — holds canonical type)
--
-- Spot check data:
--   SELECT id, environment, env_type, environment_alias
--   FROM debug_sessions LIMIT 10;
--
-- Verify no stale type values remain in environment:
--   SELECT id, environment, env_type, environment_alias
--   FROM debug_sessions
--   WHERE environment IN ('dev', 'qa', 'uat', 'staging', 'perf', 'prod')
--     AND (environment_alias IS NULL OR environment_alias = '');
--   → 0 rows (stale values should have been cleared in Step 3b)
-- =============================================================================
