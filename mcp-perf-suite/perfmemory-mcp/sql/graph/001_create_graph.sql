-- =============================================================================
-- PerfMemory Knowledge Graph: Apache AGE Setup
-- =============================================================================
-- Prerequisites:
--   1. PostgreSQL 18 with Apache AGE extension installed
--   2. pgvector extension already enabled (schema_openai.sql or schema_ollama.sql)
--   3. debug_sessions and debug_attempts tables already exist
--
-- Usage:
--   psql -h localhost -U perfadmin -d perfmemory -f 001_create_graph.sql
--
-- Validation:
--   SELECT * FROM ag_catalog.ag_graph;
--   SELECT * FROM ag_catalog.ag_label;
-- =============================================================================

-- Enable AGE extension (idempotent)
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- =============================================================================
-- Create the knowledge graph
-- =============================================================================
-- AGE errors if the graph already exists, so wrap in a DO block for safety.
DO $$
BEGIN
    PERFORM ag_catalog.create_graph('perf_knowledge');
    RAISE NOTICE 'Graph perf_knowledge created successfully';
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Graph perf_knowledge already exists, skipping creation';
END;
$$;

-- =============================================================================
-- Vertex Labels
-- =============================================================================
-- AGE creates labels on first use in a Cypher CREATE statement. There is no
-- CREATE LABEL DDL. The statements below create a temporary placeholder node
-- per label and immediately delete it, so the labels exist for tooling and
-- introspection before any real data is loaded.
--
-- Label: Attempt
--   Maps 1:1 to a debug_attempts row.
--   Properties:
--     attempt_id     TEXT  -- UUID from debug_attempts.id
--     project        TEXT  -- system_under_test from the parent session
--     error_category TEXT  -- from debug_attempts.error_category
--     fix_type       TEXT  -- from debug_attempts.fix_type
--     outcome        TEXT  -- from debug_attempts.outcome
--     response_code  TEXT  -- from debug_attempts.response_code
--     component_type TEXT  -- from debug_attempts.component_type
--
-- Label: Project
--   One node per distinct system_under_test.
--   Properties:
--     name           TEXT  -- the system_under_test value
--     alias          TEXT  -- short name / acronym (e.g., "CART", "OSP")
--
-- Label: ErrorPattern
--   One node per distinct (error_category, response_code) pair.
--   Properties:
--     error_category TEXT
--     response_code  TEXT
--
-- Label: FixPattern
--   One node per distinct (fix_type, component_type) pair.
--   Properties:
--     fix_type       TEXT
--     component_type TEXT
--
-- Label: Service
--   One node per distinct service within an application.
--   Properties:
--     name           TEXT  -- canonical service name (e.g., "cart-service")
--     application    TEXT  -- parent application's system_under_test value
-- =============================================================================

-- Create Attempt label
-- Properties: attempt_id, project (= application name / system_under_test),
--   error_category, fix_type, outcome, response_code, component_type
SELECT * FROM cypher('perf_knowledge', $$
    CREATE (n:Attempt {attempt_id: '__placeholder__'})
    RETURN n
$$) AS (v agtype);

SELECT * FROM cypher('perf_knowledge', $$
    MATCH (n:Attempt {attempt_id: '__placeholder__'})
    DELETE n
    RETURN count(n)
$$) AS (v agtype);

-- Create Project label
-- Terminology: Project = application (taxonomy YAML) = system_under_test (relational DB)
-- Properties: name (application name), alias (short name from taxonomy)
SELECT * FROM cypher('perf_knowledge', $$
    CREATE (n:Project {name: '__placeholder__'})
    RETURN n
$$) AS (v agtype);

SELECT * FROM cypher('perf_knowledge', $$
    MATCH (n:Project {name: '__placeholder__'})
    DELETE n
    RETURN count(n)
$$) AS (v agtype);

-- Create ErrorPattern label
SELECT * FROM cypher('perf_knowledge', $$
    CREATE (n:ErrorPattern {error_category: '__placeholder__', response_code: '__placeholder__'})
    RETURN n
$$) AS (v agtype);

SELECT * FROM cypher('perf_knowledge', $$
    MATCH (n:ErrorPattern {error_category: '__placeholder__', response_code: '__placeholder__'})
    DELETE n
    RETURN count(n)
$$) AS (v agtype);

-- Create FixPattern label
SELECT * FROM cypher('perf_knowledge', $$
    CREATE (n:FixPattern {fix_type: '__placeholder__', component_type: '__placeholder__'})
    RETURN n
$$) AS (v agtype);

SELECT * FROM cypher('perf_knowledge', $$
    MATCH (n:FixPattern {fix_type: '__placeholder__', component_type: '__placeholder__'})
    DELETE n
    RETURN count(n)
$$) AS (v agtype);

-- Create Service label
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
-- Edge Labels (reference only)
-- =============================================================================
-- Edges are created by the MCP tools at ingestion time. They are documented
-- here for reference but not pre-created.
--
-- BELONGS_TO:       (Attempt)-[:BELONGS_TO]->(Project)
--                   Created for every attempt. Links attempt to its project.
--
-- HAS_ERROR:        (Attempt)-[:HAS_ERROR]->(ErrorPattern)
--                   Created when error_category is not null.
--
-- FIXED_BY:         (Attempt)-[:FIXED_BY]->(FixPattern)
--                   Created when outcome = 'resolved' and fix_type is not null.
--
-- HAS_SERVICE:      (Project)-[:HAS_SERVICE]->(Service)
--                   Created when a session has a non-empty service_name.
--                   Links the application to its service.
--
-- TARGETS_SERVICE:  (Attempt)-[:TARGETS_SERVICE]->(Service)
--                   Created when an attempt's parent session has a non-empty
--                   service_name. Links the debug attempt to its service.
--
-- SIMILAR_TO:       (Attempt)-[:SIMILAR_TO]->(Attempt)
--                   Properties:
--                     similarity    FLOAT  -- cosine similarity (if embedding-based)
--                     match_type    TEXT   -- 'embedding', 'error_pattern',
--                                            'fix_pattern', or 'composite'
--                     cross_project BOOL   -- true if attempts are from different projects
--                   Created from two sources:
--                     1. Deterministic: shared ErrorPattern across different projects
--                     2. Embedding: pgvector cosine similarity > 0.82 (top-3 per attempt)
-- =============================================================================
