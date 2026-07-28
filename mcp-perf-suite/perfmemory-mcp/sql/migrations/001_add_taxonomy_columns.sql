-- =============================================================================
-- PerfMemory Migration: 001 — Add Taxonomy Columns
-- =============================================================================
-- Adds taxonomy-related columns to debug_sessions and debug_attempts tables.
-- All new columns default to empty string ('') to avoid NULL-related issues
-- in semantic search, string operations, and JSON serialization.
--
-- This script is IDEMPOTENT — safe to run multiple times.
--
-- Prerequisites:
--   - debug_sessions and debug_attempts tables already exist
--     (created by schema_openai.sql or schema_ollama.sql)
--
-- Usage:
--   psql -h localhost -U perfadmin -d perfmemory -f 001_add_taxonomy_columns.sql
-- =============================================================================

-- =============================================================================
-- debug_sessions — New Columns
-- =============================================================================

-- system_alias: Short name or acronym for the application (e.g., "CART", "OSP")
-- Allows PTEs to reference systems by their commonly-used short name.
ALTER TABLE debug_sessions
    ADD COLUMN IF NOT EXISTS system_alias TEXT NOT NULL DEFAULT '';

-- service_name: Specific microservice or API being tested within the application.
-- Enables filtering at the service level (e.g., "cart-service", "auth-api").
ALTER TABLE debug_sessions
    ADD COLUMN IF NOT EXISTS service_name TEXT NOT NULL DEFAULT '';

-- environment_alias: Specific environment name (e.g., "QA1", "QA3", "STG-East").
-- More granular than the generic 'environment' field (dev, qa, staging, etc.).
ALTER TABLE debug_sessions
    ADD COLUMN IF NOT EXISTS environment_alias TEXT NOT NULL DEFAULT '';

-- auth_alias: Human-friendly description of the auth flow implementation.
-- Provides context beyond the categorical auth_flow_type (e.g., "Corporate EntraID SSO with PKCE").
ALTER TABLE debug_sessions
    ADD COLUMN IF NOT EXISTS auth_alias TEXT NOT NULL DEFAULT '';

-- =============================================================================
-- debug_attempts — New Columns
-- =============================================================================

-- test_case_id: Test case identifier (e.g., "TC04", "TC01").
-- Maps to JMeter Transaction Controllers wrapping business test cases.
ALTER TABLE debug_attempts
    ADD COLUMN IF NOT EXISTS test_case_id TEXT NOT NULL DEFAULT '';

-- test_case_name: Human-readable test case name (e.g., "Submit Order", "User Login").
-- Used in reporting and human-AI conversations to reference business flows.
ALTER TABLE debug_attempts
    ADD COLUMN IF NOT EXISTS test_case_name TEXT NOT NULL DEFAULT '';

-- test_step_id: Test step identifier within a test case (e.g., "TS03", "S01").
-- Maps to specific HTTP requests or groups within a Transaction Controller.
ALTER TABLE debug_attempts
    ADD COLUMN IF NOT EXISTS test_step_id TEXT NOT NULL DEFAULT '';

-- test_step_name: Human-readable test step name (e.g., "POST to Pricing API").
-- Used in reporting to provide stakeholder-friendly error context.
ALTER TABLE debug_attempts
    ADD COLUMN IF NOT EXISTS test_step_name TEXT NOT NULL DEFAULT '';

-- =============================================================================
-- New Indexes
-- =============================================================================

-- Index on system_alias for filtering sessions by app short name
CREATE INDEX IF NOT EXISTS idx_sessions_system_alias
    ON debug_sessions (system_alias);

-- Index on service_name for filtering sessions by microservice
CREATE INDEX IF NOT EXISTS idx_sessions_service
    ON debug_sessions (service_name);

-- Index on environment_alias for filtering sessions by specific environment
CREATE INDEX IF NOT EXISTS idx_sessions_env_alias
    ON debug_sessions (environment_alias);

-- Index on test_case_id for filtering attempts by test case
CREATE INDEX IF NOT EXISTS idx_attempts_test_case
    ON debug_attempts (test_case_id);

-- =============================================================================
-- Validation
-- =============================================================================
-- Run after migration to verify columns exist:
--
--   SELECT column_name, data_type, column_default
--   FROM information_schema.columns
--   WHERE table_name = 'debug_sessions'
--     AND column_name IN ('system_alias', 'service_name', 'environment_alias', 'auth_alias');
--
--   SELECT column_name, data_type, column_default
--   FROM information_schema.columns
--   WHERE table_name = 'debug_attempts'
--     AND column_name IN ('test_case_id', 'test_case_name', 'test_step_id', 'test_step_name');
-- =============================================================================
