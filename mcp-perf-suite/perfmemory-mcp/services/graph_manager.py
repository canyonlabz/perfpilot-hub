import json
import logging
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.pool
import psycopg2.extensions

log = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None

_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def _build_connect_kwargs(db_config: dict) -> dict:
    kwargs = {
        "host": db_config["host"],
        "port": db_config["port"],
        "dbname": db_config["dbname"],
        "user": db_config["user"],
        "password": db_config["password"],
        **_KEEPALIVE_KWARGS,
    }
    sslmode = db_config.get("sslmode", "prefer")
    if sslmode and sslmode != "disable":
        kwargs["sslmode"] = sslmode
        sslrootcert = db_config.get("sslrootcert", "")
        if sslrootcert:
            kwargs["sslrootcert"] = sslrootcert
    return kwargs


def _get_pool(db_config: dict) -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            **_build_connect_kwargs(db_config),
        )
    return _pool


def _conn_is_healthy(conn) -> bool:
    try:
        if conn.closed:
            return False
        status = conn.info.transaction_status
        if status == psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN:
            return False
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        if status == psycopg2.extensions.TRANSACTION_STATUS_INTRANS:
            conn.rollback()
        return True
    except Exception:
        return False


_GRAPH_QUERY_TIMEOUT_MS = 10000


def _get_conn(db_config: dict):
    """Get a healthy connection with AGE search_path and query timeout configured."""
    pool = _get_pool(db_config)
    max_retries = pool.maxconn
    for attempt in range(max_retries):
        conn = pool.getconn()
        if not _conn_is_healthy(conn):
            log.warning("Discarding unhealthy graph connection (attempt %d)", attempt + 1)
            pool.putconn(conn, close=True)
            continue
        try:
            with conn.cursor() as cur:
                cur.execute("SET search_path = ag_catalog, \"$user\", public")
                cur.execute(f"SET statement_timeout = {_GRAPH_QUERY_TIMEOUT_MS}")
            conn.commit()
        except Exception:
            log.warning("Failed to configure AGE connection — discarding")
            pool.putconn(conn, close=True)
            raise
        return conn
    raise psycopg2.OperationalError(
        f"No healthy graph connections after {max_retries} attempts"
    )


def _put_conn(conn, *, healthy: bool = True):
    if _pool is not None and conn is not None:
        try:
            if conn.closed:
                healthy = False
            elif conn.info.transaction_status == psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN:
                healthy = False
        except Exception:
            healthy = False
        try:
            _pool.putconn(conn, close=not healthy)
        except Exception:
            log.warning("Failed to return graph connection to pool", exc_info=True)


def close_pool():
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
            log.info("Graph connection pool closed")
        except Exception:
            log.warning("Error closing graph connection pool", exc_info=True)
        finally:
            _pool = None


def _safe_rollback(conn) -> bool:
    try:
        conn.rollback()
        return True
    except Exception:
        log.warning("Graph rollback failed — connection is broken", exc_info=True)
        return False


def _parse_agtype(value) -> Any:
    """Parse an agtype value into a Python primitive.

    AGE returns agtype which psycopg2 sees as a string. Values may be
    JSON-encoded strings, numbers, booleans, or vertex/edge maps.
    """
    if value is None:
        return None
    s = str(value)
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() == "null":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        pass
    return s


def _cypher(conn, graph_name: str, query: str, params: Optional[dict] = None) -> List[tuple]:
    """Execute a Cypher query and return parsed rows.

    Args:
        conn: psycopg2 connection with AGE search_path set.
        graph_name: Name of the AGE graph.
        query: openCypher query string (without the cypher() wrapper).
        params: Optional parameter dict passed as JSON to cypher().

    Returns:
        List of tuples with parsed agtype values.
    """
    with conn.cursor() as cur:
        if params:
            cur.execute(
                f"SELECT * FROM cypher('{graph_name}', $${query}$$, %s) AS (result agtype)",
                (json.dumps(params),),
            )
        else:
            cur.execute(
                f"SELECT * FROM cypher('{graph_name}', $${query}$$) AS (result agtype)",
            )
        rows = cur.fetchall()
    return [tuple(_parse_agtype(col) for col in row) for row in rows]


# =============================================================================
# Graph Write Operations (called at ingestion time)
# =============================================================================
# Terminology: "Project" nodes in the graph = "applications" in the taxonomy YAML
# = system_under_test in the relational DB. The `project` parameter throughout
# this module refers to the application name.
# =============================================================================

def create_attempt_node(
    db_config: dict,
    graph_name: str,
    attempt_id: str,
    project: str,
    project_alias: str = "",
    service_name: str = "",
    error_category: Optional[str] = None,
    fix_type: Optional[str] = None,
    outcome: str = "",
    response_code: Optional[str] = None,
    component_type: Optional[str] = None,
) -> bool:
    """Create an Attempt node and its deterministic edges.

    The `project` param is the application name (= system_under_test).
    MERGE ensures one Project node per application in the graph.

    Creates:
      - Attempt node
      - Project node (MERGE) + BELONGS_TO edge
      - ErrorPattern node (MERGE) + HAS_ERROR edge (if error_category provided)
      - FixPattern node (MERGE) + FIXED_BY edge (if resolved with fix_type)
      - Service node (MERGE) + HAS_SERVICE + TARGETS_SERVICE edges (if service_name provided)
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            # Attempt node
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    CREATE (:Attempt {{
                        attempt_id: '{attempt_id}',
                        project: '{_esc(project)}',
                        error_category: '{_esc(error_category or "")}',
                        fix_type: '{_esc(fix_type or "")}',
                        outcome: '{_esc(outcome)}',
                        response_code: '{_esc(response_code or "")}',
                        component_type: '{_esc(component_type or "")}'
                    }})
                $$) AS (v agtype)"""
            )

            # MERGE Project + BELONGS_TO (include alias property)
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MERGE (p:Project {{name: '{_esc(project)}'}})
                    ON CREATE SET p.alias = '{_esc(project_alias)}'
                    ON MATCH SET p.alias = CASE WHEN p.alias = '' THEN '{_esc(project_alias)}' ELSE p.alias END
                $$) AS (v agtype)"""
            )
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (a:Attempt {{attempt_id: '{attempt_id}'}}),
                          (p:Project {{name: '{_esc(project)}'}})
                    CREATE (a)-[:BELONGS_TO]->(p)
                $$) AS (e agtype)"""
            )

            # MERGE ErrorPattern + HAS_ERROR
            if error_category:
                rc = response_code or "unknown"
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MERGE (:ErrorPattern {{
                            error_category: '{_esc(error_category)}',
                            response_code: '{_esc(rc)}'
                        }})
                    $$) AS (v agtype)"""
                )
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{attempt_id: '{attempt_id}'}}),
                              (ep:ErrorPattern {{
                                  error_category: '{_esc(error_category)}',
                                  response_code: '{_esc(rc)}'
                              }})
                        CREATE (a)-[:HAS_ERROR]->(ep)
                    $$) AS (e agtype)"""
                )

            # MERGE FixPattern + FIXED_BY
            if outcome == "resolved" and fix_type:
                ct = component_type or "unknown"
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MERGE (:FixPattern {{
                            fix_type: '{_esc(fix_type)}',
                            component_type: '{_esc(ct)}'
                        }})
                    $$) AS (v agtype)"""
                )
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{attempt_id: '{attempt_id}'}}),
                              (fp:FixPattern {{
                                  fix_type: '{_esc(fix_type)}',
                                  component_type: '{_esc(ct)}'
                              }})
                        CREATE (a)-[:FIXED_BY]->(fp)
                    $$) AS (e agtype)"""
                )

            # MERGE Service + HAS_SERVICE + TARGETS_SERVICE
            if service_name:
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MERGE (svc:Service {{name: '{_esc(service_name)}', application: '{_esc(project)}'}})
                    $$) AS (v agtype)"""
                )
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (p:Project {{name: '{_esc(project)}'}}),
                              (svc:Service {{name: '{_esc(service_name)}', application: '{_esc(project)}'}})
                        MERGE (p)-[:HAS_SERVICE]->(svc)
                    $$) AS (e agtype)"""
                )
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{attempt_id: '{attempt_id}'}}),
                              (svc:Service {{name: '{_esc(service_name)}', application: '{_esc(project)}'}})
                        CREATE (a)-[:TARGETS_SERVICE]->(svc)
                    $$) AS (e agtype)"""
                )

        conn.commit()
        return True
    except Exception:
        log.error("Failed to create graph nodes for attempt %s", attempt_id, exc_info=True)
        _healthy = _safe_rollback(conn)
        return False
    finally:
        _put_conn(conn, healthy=_healthy)


def create_cross_project_edges(
    db_config: dict,
    graph_name: str,
    attempt_id: str,
    error_category: str,
    response_code: Optional[str],
    project: str,
) -> int:
    """Create deterministic SIMILAR_TO edges to attempts from other projects
    that share the same ErrorPattern.

    Returns the number of edges created.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        rc = response_code or "unknown"
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (ep:ErrorPattern {{
                               error_category: '{_esc(error_category)}',
                               response_code: '{_esc(rc)}'
                           }})
                          <-[:HAS_ERROR]-(other:Attempt)
                    WHERE other.attempt_id <> '{attempt_id}'
                      AND other.project <> '{_esc(project)}'
                    RETURN other.attempt_id
                $$) AS (attempt_id agtype)"""
            )
            related = cur.fetchall()

            count = 0
            for row in related:
                other_id = _parse_agtype(row[0])
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{attempt_id: '{attempt_id}'}}),
                              (b:Attempt {{attempt_id: '{other_id}'}})
                        CREATE (a)-[:SIMILAR_TO {{
                            match_type: 'error_pattern',
                            cross_project: true,
                            similarity: 0.0
                        }}]->(b)
                    $$) AS (e agtype)"""
                )
                count += 1

        conn.commit()
        return count
    except Exception:
        log.error("Failed to create cross-project edges for %s", attempt_id, exc_info=True)
        _healthy = _safe_rollback(conn)
        return 0
    finally:
        _put_conn(conn, healthy=_healthy)


def create_embedding_edges(
    db_config: dict,
    graph_name: str,
    attempt_id: str,
    similar_attempt_ids: List[Dict[str, Any]],
) -> int:
    """Create embedding-based SIMILAR_TO edges from pgvector search results.

    Args:
        similar_attempt_ids: List of dicts with keys 'attempt_id', 'similarity',
                             'system_under_test' (from the vector search results).

    Returns the number of edges created.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        count = 0
        with conn.cursor() as cur:
            for match in similar_attempt_ids:
                other_id = match["attempt_id"]
                sim = match["similarity"]
                cross = match.get("cross_project", False)
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{attempt_id: '{attempt_id}'}}),
                              (b:Attempt {{attempt_id: '{other_id}'}})
                        CREATE (a)-[:SIMILAR_TO {{
                            match_type: 'embedding',
                            cross_project: {'true' if cross else 'false'},
                            similarity: {sim}
                        }}]->(b)
                    $$) AS (e agtype)"""
                )
                count += 1
        conn.commit()
        return count
    except Exception:
        log.error("Failed to create embedding edges for %s", attempt_id, exc_info=True)
        _healthy = _safe_rollback(conn)
        return 0
    finally:
        _put_conn(conn, healthy=_healthy)


# =============================================================================
# Graph Read Operations (called at query time)
# =============================================================================

def find_graph_related(
    db_config: dict,
    graph_name: str,
    error_category: Optional[str] = None,
    response_code: Optional[str] = None,
    current_project: Optional[str] = None,
    fix_type: Optional[str] = None,
    max_hops: int = 2,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Find related attempts via graph traversal.

    Traverses ErrorPattern and FixPattern nodes to find structurally
    related attempts, optionally filtering to cross-project results.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        results = []

        with conn.cursor() as cur:
            # Path 1: Via shared ErrorPattern
            if error_category:
                project_filter = ""
                if current_project:
                    project_filter = f"AND related.project <> '{_esc(current_project)}'"

                ep_props = f"error_category: '{_esc(error_category)}'"
                if response_code:
                    ep_props += f", response_code: '{_esc(response_code)}'"

                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (ep:ErrorPattern {{{ep_props}}})
                              <-[:HAS_ERROR]-(related:Attempt)
                        WHERE related.outcome = 'resolved'
                          {project_filter}
                        RETURN related.attempt_id, related.project,
                               related.fix_type, related.error_category,
                               related.response_code, related.component_type
                        LIMIT {limit}
                    $$) AS (attempt_id agtype, project agtype, fix_type agtype,
                            error_category agtype, response_code agtype,
                            component_type agtype)"""
                )
                for row in cur.fetchall():
                    results.append({
                        "attempt_id": _parse_agtype(row[0]),
                        "project": _parse_agtype(row[1]),
                        "fix_type": _parse_agtype(row[2]),
                        "error_category": _parse_agtype(row[3]),
                        "response_code": _parse_agtype(row[4]),
                        "component_type": _parse_agtype(row[5]),
                        "graph_path": f"ErrorPattern({error_category})",
                        "source": "graph",
                    })

            # Path 2: Via shared FixPattern
            if fix_type:
                ct_filter = ""
                project_filter = ""
                if current_project:
                    project_filter = f"AND related.project <> '{_esc(current_project)}'"

                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (fp:FixPattern {{fix_type: '{_esc(fix_type)}'}})
                              <-[:FIXED_BY]-(related:Attempt)
                        WHERE related.outcome = 'resolved'
                          {project_filter}
                        RETURN related.attempt_id, related.project,
                               related.fix_type, related.error_category,
                               related.response_code, related.component_type
                        LIMIT {limit}
                    $$) AS (attempt_id agtype, project agtype, fix_type agtype,
                            error_category agtype, response_code agtype,
                            component_type agtype)"""
                )
                for row in cur.fetchall():
                    aid = _parse_agtype(row[0])
                    if not any(r["attempt_id"] == aid for r in results):
                        results.append({
                            "attempt_id": aid,
                            "project": _parse_agtype(row[1]),
                            "fix_type": _parse_agtype(row[2]),
                            "error_category": _parse_agtype(row[3]),
                            "response_code": _parse_agtype(row[4]),
                            "component_type": _parse_agtype(row[5]),
                            "graph_path": f"FixPattern({fix_type})",
                            "source": "graph",
                        })

        return results[:limit]
    except Exception:
        log.error("Graph traversal failed", exc_info=True)
        return []
    finally:
        _put_conn(conn, healthy=_healthy)


def find_cross_project_patterns(
    db_config: dict,
    graph_name: str,
    error_category: str,
    current_project: Optional[str] = None,
    response_code: Optional[str] = None,
    fix_type: Optional[str] = None,
    max_hops: int = 2,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Dedicated cross-project pattern discovery via graph traversal.

    Answers: "has this class of issue been seen and resolved in any other
    project (application)?" The current_project param accepts the application
    name (= system_under_test) to exclude from results.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        results = []

        with conn.cursor() as cur:
            project_filter = ""
            if current_project:
                project_filter = f"AND related.project <> '{_esc(current_project)}'"

            ep_props = f"error_category: '{_esc(error_category)}'"
            if response_code:
                ep_props += f", response_code: '{_esc(response_code)}'"

            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (ep:ErrorPattern {{{ep_props}}})
                          <-[:HAS_ERROR]-(related:Attempt)
                          -[:BELONGS_TO]->(p:Project)
                    WHERE related.outcome = 'resolved'
                      {project_filter}
                    RETURN related.attempt_id, p.name, related.fix_type,
                           related.error_category, related.response_code,
                           related.component_type, related.outcome
                    LIMIT {limit}
                $$) AS (attempt_id agtype, project agtype, fix_type agtype,
                        error_category agtype, response_code agtype,
                        component_type agtype, outcome agtype)"""
            )
            for row in cur.fetchall():
                results.append({
                    "attempt_id": _parse_agtype(row[0]),
                    "project": _parse_agtype(row[1]),
                    "fix_type": _parse_agtype(row[2]),
                    "error_category": _parse_agtype(row[3]),
                    "response_code": _parse_agtype(row[4]),
                    "component_type": _parse_agtype(row[5]),
                    "outcome": _parse_agtype(row[6]),
                    "graph_path": f"ErrorPattern({error_category}/{response_code or '*'})",
                })

            # Also check via SIMILAR_TO edges for multi-hop discovery
            if max_hops > 1:
                cur.execute(
                    f"""SELECT * FROM cypher('{graph_name}', $$
                        MATCH (start:Attempt)-[:SIMILAR_TO*1..{max_hops}]->(related:Attempt)
                              -[:BELONGS_TO]->(p:Project)
                        WHERE start.error_category = '{_esc(error_category)}'
                          AND related.outcome = 'resolved'
                          {project_filter}
                        RETURN DISTINCT related.attempt_id, p.name,
                               related.fix_type, related.error_category,
                               related.response_code, related.component_type,
                               related.outcome
                        LIMIT {limit}
                    $$) AS (attempt_id agtype, project agtype, fix_type agtype,
                            error_category agtype, response_code agtype,
                            component_type agtype, outcome agtype)"""
                )
                for row in cur.fetchall():
                    aid = _parse_agtype(row[0])
                    if not any(r["attempt_id"] == aid for r in results):
                        results.append({
                            "attempt_id": aid,
                            "project": _parse_agtype(row[1]),
                            "fix_type": _parse_agtype(row[2]),
                            "error_category": _parse_agtype(row[3]),
                            "response_code": _parse_agtype(row[4]),
                            "component_type": _parse_agtype(row[5]),
                            "outcome": _parse_agtype(row[6]),
                            "graph_path": f"SIMILAR_TO(hops<={max_hops})",
                        })

        return results[:limit]
    except Exception:
        log.error("Cross-project pattern search failed", exc_info=True)
        return []
    finally:
        _put_conn(conn, healthy=_healthy)


def get_related_issues(
    db_config: dict,
    graph_name: str,
    attempt_id: str,
    max_hops: int = 2,
    include_same_project: bool = True,
) -> Dict[str, Any]:
    """Get the graph neighborhood of a specific attempt.

    Returns the attempt's connected ErrorPattern, FixPattern, and
    neighboring attempts via edges.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        result: Dict[str, Any] = {
            "attempt_id": attempt_id,
            "project": None,
            "error_patterns": [],
            "fix_patterns": [],
            "neighbors": [],
        }

        with conn.cursor() as cur:
            # Get the attempt's project
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (a:Attempt {{attempt_id: '{attempt_id}'}})
                          -[:BELONGS_TO]->(p:Project)
                    RETURN p.name
                $$) AS (project agtype)"""
            )
            row = cur.fetchone()
            if row:
                result["project"] = _parse_agtype(row[0])

            # Get connected ErrorPatterns
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (a:Attempt {{attempt_id: '{attempt_id}'}})
                          -[:HAS_ERROR]->(ep:ErrorPattern)
                    RETURN ep.error_category, ep.response_code
                $$) AS (error_category agtype, response_code agtype)"""
            )
            for row in cur.fetchall():
                result["error_patterns"].append({
                    "error_category": _parse_agtype(row[0]),
                    "response_code": _parse_agtype(row[1]),
                })

            # Get connected FixPatterns
            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (a:Attempt {{attempt_id: '{attempt_id}'}})
                          -[:FIXED_BY]->(fp:FixPattern)
                    RETURN fp.fix_type, fp.component_type
                $$) AS (fix_type agtype, component_type agtype)"""
            )
            for row in cur.fetchall():
                result["fix_patterns"].append({
                    "fix_type": _parse_agtype(row[0]),
                    "component_type": _parse_agtype(row[1]),
                })

            # Get neighboring attempts via SIMILAR_TO
            project_filter = ""
            if not include_same_project and result["project"]:
                project_filter = f"AND neighbor.project <> '{_esc(result['project'])}'"

            cur.execute(
                f"""SELECT * FROM cypher('{graph_name}', $$
                    MATCH (a:Attempt {{attempt_id: '{attempt_id}'}})
                          -[s:SIMILAR_TO]-(neighbor:Attempt)
                    WHERE neighbor.attempt_id <> '{attempt_id}'
                      {project_filter}
                    RETURN neighbor.attempt_id, neighbor.project,
                           neighbor.outcome, neighbor.fix_type,
                           neighbor.error_category
                $$) AS (attempt_id agtype, project agtype, outcome agtype,
                        fix_type agtype, error_category agtype)"""
            )
            for row in cur.fetchall():
                result["neighbors"].append({
                    "attempt_id": _parse_agtype(row[0]),
                    "project": _parse_agtype(row[1]),
                    "outcome": _parse_agtype(row[2]),
                    "fix_type": _parse_agtype(row[3]),
                    "error_category": _parse_agtype(row[4]),
                })

        return result
    except Exception:
        log.error("Failed to get related issues for %s", attempt_id, exc_info=True)
        return {"attempt_id": attempt_id, "error": "Graph query failed"}
    finally:
        _put_conn(conn, healthy=_healthy)


# =============================================================================
# Helpers
# =============================================================================

def _esc(value: str) -> str:
    """Escape single quotes for Cypher string literals."""
    if value is None:
        return ""
    return value.replace("'", "\\'").replace("\\", "\\\\")
