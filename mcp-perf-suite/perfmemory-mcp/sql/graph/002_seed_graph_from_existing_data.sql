-- =============================================================================
-- PerfMemory Knowledge Graph: Seed from Existing Data
-- =============================================================================
-- Run AFTER 001_create_graph.sql.
-- Reads existing debug_sessions and debug_attempts rows and creates
-- corresponding graph nodes and deterministic edges.
--
-- This script is idempotent in intent but AGE does not enforce unique
-- constraints on node properties. Run it ONCE on an existing database.
-- For a fresh install, skip this script — the MCP tools will create
-- graph nodes at ingestion time.
--
-- Usage:
--   psql -h localhost -U perfadmin -d perfmemory -f 002_seed_graph_from_existing_data.sql
-- =============================================================================

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- -----------------------------------------------------------------------------
-- Step 1: Create Project nodes from distinct system_under_test values
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT DISTINCT system_under_test
        FROM public.debug_sessions
        WHERE system_under_test IS NOT NULL
    LOOP
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                MERGE (:Project {name: %L})
            $q$) AS (v agtype)',
            rec.system_under_test
        );
    END LOOP;
    RAISE NOTICE 'Step 1 complete: Project nodes created';
END;
$$;

-- -----------------------------------------------------------------------------
-- Step 2: Create ErrorPattern nodes from distinct (error_category, response_code)
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT DISTINCT
            COALESCE(error_category, 'unknown') AS error_category,
            COALESCE(response_code, 'unknown') AS response_code
        FROM public.debug_attempts
        WHERE error_category IS NOT NULL
    LOOP
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                MERGE (:ErrorPattern {error_category: %L, response_code: %L})
            $q$) AS (v agtype)',
            rec.error_category,
            rec.response_code
        );
    END LOOP;
    RAISE NOTICE 'Step 2 complete: ErrorPattern nodes created';
END;
$$;

-- -----------------------------------------------------------------------------
-- Step 3: Create FixPattern nodes from distinct (fix_type, component_type)
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT DISTINCT
            fix_type,
            COALESCE(component_type, 'unknown') AS component_type
        FROM public.debug_attempts
        WHERE fix_type IS NOT NULL
          AND outcome = 'resolved'
    LOOP
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                MERGE (:FixPattern {fix_type: %L, component_type: %L})
            $q$) AS (v agtype)',
            rec.fix_type,
            rec.component_type
        );
    END LOOP;
    RAISE NOTICE 'Step 3 complete: FixPattern nodes created';
END;
$$;

-- -----------------------------------------------------------------------------
-- Step 4: Create Attempt nodes and edges
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT
            a.id::text AS attempt_id,
            s.system_under_test AS project,
            a.error_category,
            a.response_code,
            a.fix_type,
            a.component_type,
            a.outcome
        FROM public.debug_attempts a
        JOIN public.debug_sessions s ON a.session_id = s.id
        WHERE a.is_active = TRUE
    LOOP
        -- Create Attempt node
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                CREATE (:Attempt {
                    attempt_id: %L,
                    project: %L,
                    error_category: %L,
                    fix_type: %L,
                    outcome: %L,
                    response_code: %L,
                    component_type: %L
                })
            $q$) AS (v agtype)',
            rec.attempt_id,
            rec.project,
            COALESCE(rec.error_category, ''),
            COALESCE(rec.fix_type, ''),
            rec.outcome,
            COALESCE(rec.response_code, ''),
            COALESCE(rec.component_type, '')
        );

        -- BELONGS_TO edge: Attempt -> Project
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                MATCH (a:Attempt {attempt_id: %L}),
                      (p:Project {name: %L})
                CREATE (a)-[:BELONGS_TO]->(p)
            $q$) AS (e agtype)',
            rec.attempt_id,
            rec.project
        );

        -- HAS_ERROR edge: Attempt -> ErrorPattern
        IF rec.error_category IS NOT NULL THEN
            EXECUTE format(
                'SELECT * FROM cypher(''perf_knowledge'', $q$
                    MATCH (a:Attempt {attempt_id: %L}),
                          (ep:ErrorPattern {error_category: %L, response_code: %L})
                    CREATE (a)-[:HAS_ERROR]->(ep)
                $q$) AS (e agtype)',
                rec.attempt_id,
                rec.error_category,
                COALESCE(rec.response_code, 'unknown')
            );
        END IF;

        -- FIXED_BY edge: Attempt -> FixPattern (resolved attempts with a known fix)
        IF rec.outcome = 'resolved' AND rec.fix_type IS NOT NULL THEN
            EXECUTE format(
                'SELECT * FROM cypher(''perf_knowledge'', $q$
                    MATCH (a:Attempt {attempt_id: %L}),
                          (fp:FixPattern {fix_type: %L, component_type: %L})
                    CREATE (a)-[:FIXED_BY]->(fp)
                $q$) AS (e agtype)',
                rec.attempt_id,
                rec.fix_type,
                COALESCE(rec.component_type, 'unknown')
            );
        END IF;
    END LOOP;
    RAISE NOTICE 'Step 4 complete: Attempt nodes and edges created';
END;
$$;

-- -----------------------------------------------------------------------------
-- Step 5: Create cross-project SIMILAR_TO edges via shared ErrorPattern
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT DISTINCT
            a1.id::text AS attempt_id_1,
            a2.id::text AS attempt_id_2,
            s1.system_under_test AS project_1,
            s2.system_under_test AS project_2
        FROM public.debug_attempts a1
        JOIN public.debug_sessions s1 ON a1.session_id = s1.id
        JOIN public.debug_attempts a2 ON a1.error_category = a2.error_category
            AND COALESCE(a1.response_code, '') = COALESCE(a2.response_code, '')
            AND a1.id < a2.id
        JOIN public.debug_sessions s2 ON a2.session_id = s2.id
        WHERE s1.system_under_test != s2.system_under_test
          AND a1.error_category IS NOT NULL
          AND a1.is_active = TRUE
          AND a2.is_active = TRUE
    LOOP
        EXECUTE format(
            'SELECT * FROM cypher(''perf_knowledge'', $q$
                MATCH (a:Attempt {attempt_id: %L}),
                      (b:Attempt {attempt_id: %L})
                CREATE (a)-[:SIMILAR_TO {
                    match_type: ''error_pattern'',
                    cross_project: true,
                    similarity: 0.0
                }]->(b)
            $q$) AS (e agtype)',
            rec.attempt_id_1,
            rec.attempt_id_2
        );
    END LOOP;
    RAISE NOTICE 'Step 5 complete: Cross-project SIMILAR_TO edges created';
END;
$$;
