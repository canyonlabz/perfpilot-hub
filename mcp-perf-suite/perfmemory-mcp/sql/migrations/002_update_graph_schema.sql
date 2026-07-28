-- =============================================================================
-- PerfMemory Migration: 002 — Update Graph Schema (Apache AGE)
-- =============================================================================
-- Adds the Service vertex label and new edge types to the perf_knowledge graph.
-- Also adds an 'alias' property to existing Project nodes (backfilled as '').
--
-- This script is IDEMPOTENT in intent. AGE does not enforce unique constraints
-- on node properties, so running this multiple times may create duplicate
-- placeholder nodes. Run ONCE per database.
--
-- Prerequisites:
--   - Apache AGE extension loaded
--   - perf_knowledge graph exists (created by 001_create_graph.sql)
--
-- Usage:
--   psql -h localhost -U perfadmin -d perfmemory -f 002_update_graph_schema.sql
-- =============================================================================

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- =============================================================================
-- Step 1: Create Service vertex label
-- =============================================================================
-- Service nodes represent microservices/APIs within an application (Project).
-- Properties:
--   name TEXT        — canonical service name (e.g., "cart-service")
--   application TEXT — the parent application's system_under_test value
--
-- Edges involving Service:
--   (Project)-[:HAS_SERVICE]->(Service)    — application owns this service
--   (Attempt)-[:TARGETS_SERVICE]->(Service) — debug attempt relates to this service

SELECT * FROM cypher('perf_knowledge', $$
    CREATE (n:Service {name: '__placeholder__', application: '__placeholder__'})
    RETURN n
$$) AS (v agtype);

SELECT * FROM cypher('perf_knowledge', $$
    MATCH (n:Service {name: '__placeholder__', application: '__placeholder__'})
    DELETE n
    RETURN count(n)
$$) AS (v agtype);

-- =============================================================================
-- Step 2: Add 'alias' property to existing Project nodes
-- =============================================================================
-- Backfill existing Project nodes with an empty alias property so that
-- all Project nodes have a consistent schema going forward.

DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                MATCH (p:Project)
                WHERE p.alias IS NULL
                SET p.alias = ''''
                RETURN p.name
            $q$) AS (name agtype)'
        )
    LOOP
        -- No-op loop body; the SET in Cypher does the work.
        NULL;
    END LOOP;
    RAISE NOTICE 'Step 2 complete: alias property added to existing Project nodes';
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Step 2: No Project nodes to update or alias already set';
END;
$$;

-- =============================================================================
-- Edge Labels Reference (created at runtime by MCP tools)
-- =============================================================================
-- The following edges are created by graph_manager.py during ingestion.
-- They are documented here for reference but not pre-created.
--
-- HAS_SERVICE: (Project)-[:HAS_SERVICE]->(Service)
--   Created when a session has a non-empty service_name and graph is enabled.
--   Links the application to its service.
--
-- TARGETS_SERVICE: (Attempt)-[:TARGETS_SERVICE]->(Service)
--   Created when an attempt's parent session has a non-empty service_name.
--   Links the debug attempt to the specific service it relates to.
--
-- Note: The existing edge types remain unchanged:
--   BELONGS_TO:  (Attempt)-[:BELONGS_TO]->(Project)
--   HAS_ERROR:   (Attempt)-[:HAS_ERROR]->(ErrorPattern)
--   FIXED_BY:    (Attempt)-[:FIXED_BY]->(FixPattern)
--   SIMILAR_TO:  (Attempt)-[:SIMILAR_TO]->(Attempt)
-- =============================================================================
