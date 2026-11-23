-- =============================================================================
-- 016 - Columns: user_project_bindings.ado_token_name, jira_token_name
-- =============================================================================
-- Adds the project-tracking token-name columns used by Settings → Projects.
-- A user can already store an Azure DevOps PAT and a Jira token in
-- user_secrets. These columns record which of those secret names is bound
-- to a project. NULL means no token is selected for that integration.
--
-- Confluence stays one column because cloud and on-prem are two secret
-- components of the same integration. Azure DevOps and Jira are separate
-- integrations, so each gets its own column.
--
-- 015_create_user_project_bindings.sql creates the table without these
-- columns. Run this script on any database that has already applied 015,
-- including a fresh install that is applying the scripts in order.
--
-- The Confluence comment is restated so its wording stays with this repo:
-- a Confluence binding may name either a cloud or an on-prem secret.
--
-- Run from the `perfagent_state` database context.
-- Depends on: 015_create_user_project_bindings.sql
-- Idempotent: ADD COLUMN IF NOT EXISTS.
-- =============================================================================

ALTER TABLE user_project_bindings
    ADD COLUMN IF NOT EXISTS ado_token_name TEXT;

ALTER TABLE user_project_bindings
    ADD COLUMN IF NOT EXISTS jira_token_name TEXT;

COMMENT ON COLUMN user_project_bindings.ado_token_name IS
    'user_secrets.name for ado. NULL means None selected.';
COMMENT ON COLUMN user_project_bindings.jira_token_name IS
    'user_secrets.name for jira. NULL means None selected.';
COMMENT ON COLUMN user_project_bindings.confluence_token_name IS
    'user_secrets.name for confluence-cloud or confluence-onprem. NULL means None selected.';
