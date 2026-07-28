import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import psycopg2
import psycopg2.pool
import psycopg2.extensions
from pgvector.psycopg2 import register_vector

log = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None

_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def _build_connect_kwargs(db_config: dict) -> dict:
    """Build psycopg2 connection keyword arguments from config.

    Handles SSL parameters conditionally so that local (non-SSL)
    connections don't pass unnecessary keywords.
    """
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
    """Get or create the connection pool (lazy singleton)."""
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            **_build_connect_kwargs(db_config),
        )
    return _pool


def _conn_is_healthy(conn) -> bool:
    """Lightweight ping to verify a connection is still usable."""
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


def _get_conn(db_config: dict):
    """Get a healthy connection from the pool with pgvector registered.

    Discards dead connections and retries up to ``maxconn`` times so a
    single stale connection does not fail the caller.
    """
    pool = _get_pool(db_config)
    max_retries = pool.maxconn
    for attempt in range(max_retries):
        conn = pool.getconn()
        if not _conn_is_healthy(conn):
            log.warning("Discarding unhealthy connection (attempt %d)", attempt + 1)
            pool.putconn(conn, close=True)
            continue
        try:
            register_vector(conn)
        except Exception:
            log.warning("register_vector failed — discarding connection")
            pool.putconn(conn, close=True)
            raise
        return conn
    raise psycopg2.OperationalError(
        f"No healthy connections after {max_retries} attempts"
    )


def _put_conn(conn, *, healthy: bool = True):
    """Return a connection to the pool.

    Auto-detects broken connections regardless of the caller's ``healthy``
    hint, so even if the caller forgets to flag a failure the pool stays clean.

    Args:
        conn: The psycopg2 connection.
        healthy: If False, the connection is closed instead of recycled.
    """
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
            log.warning("Failed to return connection to pool", exc_info=True)


def close_pool():
    """Gracefully close all connections in the pool.

    Called during MCP server shutdown to release database resources.
    """
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
            log.info("Connection pool closed")
        except Exception:
            log.warning("Error closing connection pool", exc_info=True)
        finally:
            _pool = None


def _safe_rollback(conn) -> bool:
    """Attempt a rollback and return whether the connection is still healthy."""
    try:
        conn.rollback()
        return True
    except Exception:
        log.warning("Rollback failed — connection is broken", exc_info=True)
        return False


# =============================================================================
# Session CRUD
# =============================================================================
# Terminology: system_under_test = "application" (taxonomy YAML) = "Project"
# node (Apache AGE graph). These are a one-to-one mapping across layers.
# =============================================================================

def create_session(
    db_config: dict,
    system_under_test: str,
    test_run_id: str,
    script_name: Optional[str] = None,
    auth_flow_type: Optional[str] = None,
    environment: Optional[str] = None,
    created_by: Optional[str] = None,
    notes: Optional[str] = None,
    system_alias: str = "",
    service_name: str = "",
    env_type: str = "",
    auth_alias: str = "",
) -> str:
    """Insert a new debug session. Returns the session UUID as a string."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO debug_sessions
                    (system_under_test, test_run_id, script_name, auth_flow_type,
                     environment, created_by, notes, final_outcome, started_at,
                     system_alias, service_name, env_type, auth_alias)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (system_under_test, test_run_id, script_name, auth_flow_type,
                 environment, created_by, notes, "in_progress",
                 datetime.now(timezone.utc),
                 system_alias, service_name, env_type, auth_alias),
            )
            session_id = str(cur.fetchone()[0])
        conn.commit()
        return session_id
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def close_session(
    db_config: dict,
    session_id: str,
    final_outcome: str,
    resolution_attempt_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    """Finalize a debug session. Returns the total_iterations count."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM debug_attempts WHERE session_id = %s",
                (session_id,),
            )
            total_iterations = cur.fetchone()[0]

            update_fields = [
                "final_outcome = %s",
                "total_iterations = %s",
                "completed_at = %s",
            ]
            params: list = [final_outcome, total_iterations, datetime.now(timezone.utc)]

            if resolution_attempt_id:
                update_fields.append("resolution_attempt_id = %s")
                params.append(resolution_attempt_id)

            if notes:
                update_fields.append("notes = COALESCE(notes, '') || %s")
                params.append(f"\n{notes}")

            params.append(session_id)
            cur.execute(
                f"UPDATE debug_sessions SET {', '.join(update_fields)} WHERE id = %s",
                params,
            )
        conn.commit()
        return total_iterations
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def get_session(db_config: dict, session_id: str) -> Optional[Dict[str, Any]]:
    """Get a session by ID with all its attempts."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, system_under_test, test_run_id, script_name,
                       auth_flow_type, environment, total_iterations,
                       final_outcome, resolution_attempt_id, created_by,
                       notes, started_at, completed_at, created_at,
                       system_alias, service_name, env_type, auth_alias
                FROM debug_sessions WHERE id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            session = _row_to_session(row)

            cur.execute(
                """
                SELECT id, session_id, iteration_number, error_category,
                       severity, response_code, outcome, hostname,
                       sampler_name, api_endpoint, symptom_text, diagnosis,
                       fix_description, fix_type, component_type,
                       manifest_excerpt, embedding_model, is_verified,
                       is_active, confirmed_count, created_at,
                       test_case_id, test_case_name, test_step_id, test_step_name
                FROM debug_attempts
                WHERE session_id = %s
                ORDER BY iteration_number
                """,
                (session_id,),
            )
            attempts = [_row_to_attempt(r) for r in cur.fetchall()]

        return {"session": session, "attempts": attempts}
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def list_sessions_filtered(
    db_config: dict,
    system_under_test: Optional[str] = None,
    environment: Optional[str] = None,
    env_type: Optional[str] = None,
    final_outcome: Optional[str] = None,
    system_alias: Optional[str] = None,
    service_name: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List sessions with optional filters. Returns session metadata only.

    The ``environment`` filter matches on the specific environment name column.
    The ``env_type`` filter matches on the canonical environment type column.
    Both can be used independently or together.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        conditions = []
        params: list = []

        if system_under_test or system_alias:
            sys_parts = []
            if system_under_test:
                sys_parts.append("system_under_test = %s")
                params.append(system_under_test)
                sys_parts.append("system_alias = %s")
                params.append(system_under_test)
            if system_alias:
                sys_parts.append("system_alias = %s")
                params.append(system_alias)
                if not system_under_test:
                    sys_parts.append("system_under_test = %s")
                    params.append(system_alias)
            conditions.append(f"({' OR '.join(sys_parts)})")
        if environment:
            conditions.append("environment = %s")
            params.append(environment)
        if env_type:
            conditions.append("env_type = %s")
            params.append(env_type)
        if final_outcome:
            conditions.append("final_outcome = %s")
            params.append(final_outcome)
        if service_name:
            conditions.append("service_name = %s")
            params.append(service_name)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, system_under_test, test_run_id, script_name,
                       auth_flow_type, environment, total_iterations,
                       final_outcome, resolution_attempt_id, created_by,
                       notes, started_at, completed_at, created_at,
                       system_alias, service_name, env_type, auth_alias
                FROM debug_sessions
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params,
            )
            return [_row_to_session(r) for r in cur.fetchall()]
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


# =============================================================================
# Attempt CRUD
# =============================================================================

def create_attempt(
    db_config: dict,
    session_id: str,
    iteration_number: int,
    symptom_text: str,
    outcome: str,
    embedding: List[float],
    embedding_model: str,
    error_category: Optional[str] = None,
    severity: Optional[str] = None,
    response_code: Optional[str] = None,
    hostname: Optional[str] = None,
    sampler_name: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    diagnosis: Optional[str] = None,
    fix_description: Optional[str] = None,
    fix_type: Optional[str] = None,
    component_type: Optional[str] = None,
    manifest_excerpt: Optional[str] = None,
    test_case_id: str = "",
    test_case_name: str = "",
    test_step_id: str = "",
    test_step_name: str = "",
) -> str:
    """Insert a new debug attempt with its embedding. Returns the attempt UUID."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO debug_attempts
                    (session_id, iteration_number, symptom_text, outcome,
                     error_category, severity, response_code, hostname,
                     sampler_name, api_endpoint, diagnosis, fix_description,
                     fix_type, component_type, manifest_excerpt,
                     test_case_id, test_case_name, test_step_id, test_step_name,
                     embedding_model, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (session_id, iteration_number, symptom_text, outcome,
                 error_category, severity, response_code, hostname,
                 sampler_name, api_endpoint, diagnosis, fix_description,
                 fix_type, component_type, manifest_excerpt,
                 test_case_id, test_case_name, test_step_id, test_step_name,
                 embedding_model, embedding),
            )
            attempt_id = str(cur.fetchone()[0])
        conn.commit()
        return attempt_id
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def increment_confirmed(db_config: dict, attempt_id: str) -> int:
    """Increment confirmed_count on an attempt. Returns the new count."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE debug_attempts
                SET confirmed_count = confirmed_count + 1
                WHERE id = %s
                RETURNING confirmed_count
                """,
                (attempt_id,),
            )
            result = cur.fetchone()
            if not result:
                raise ValueError(f"Attempt not found: {attempt_id}")
            new_count = result[0]
        conn.commit()
        return new_count
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def archive_attempt_by_id(db_config: dict, attempt_id: str) -> bool:
    """Set is_active = FALSE on an attempt. Returns True if found."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE debug_attempts SET is_active = FALSE WHERE id = %s",
                (attempt_id,),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def verify_attempt_by_id(db_config: dict, attempt_id: str) -> bool:
    """Set is_verified = TRUE on an attempt. Returns True if found."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE debug_attempts SET is_verified = TRUE WHERE id = %s",
                (attempt_id,),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


# =============================================================================
# Vector Search
# =============================================================================

def find_similar(
    db_config: dict,
    embedding: List[float],
    system_under_test: Optional[str] = None,
    system_alias: Optional[str] = None,
    error_category: Optional[str] = None,
    service_name: Optional[str] = None,
    threshold: float = 0.75,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Semantic similarity search on debug attempts.

    Joins with debug_sessions for metadata filtering. Only returns
    active attempts (is_active = TRUE).

    When system_under_test (application name) is provided, checks both
    s.system_under_test and s.system_alias for matches (OR logic). When
    system_alias is also provided, it is included in the OR clause as an
    additional alias check.
    """
    conn = _get_conn(db_config)
    _healthy = True
    try:
        conditions = ["a.is_active = TRUE"]
        params: list = [embedding, embedding, threshold]

        if system_under_test or system_alias:
            sys_parts = []
            if system_under_test:
                sys_parts.append("s.system_under_test = %s")
                params.append(system_under_test)
                sys_parts.append("s.system_alias = %s")
                params.append(system_under_test)
            if system_alias:
                sys_parts.append("s.system_alias = %s")
                params.append(system_alias)
                if not system_under_test:
                    sys_parts.append("s.system_under_test = %s")
                    params.append(system_alias)
            conditions.append(f"({' OR '.join(sys_parts)})")
        if error_category:
            conditions.append("a.error_category = %s")
            params.append(error_category)
        if service_name:
            conditions.append("s.service_name = %s")
            params.append(service_name)

        where = " AND ".join(conditions)
        params.append(top_k)

        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    a.id, a.session_id, a.iteration_number,
                    a.symptom_text, a.diagnosis, a.fix_description,
                    a.fix_type, a.outcome, a.error_category, a.severity,
                    a.hostname, a.sampler_name, a.api_endpoint,
                    a.component_type, a.confirmed_count, a.is_verified,
                    s.system_under_test, s.environment, s.auth_flow_type,
                    1 - (a.embedding <=> %s::vector) AS similarity,
                    s.system_alias, s.service_name,
                    a.test_case_id, a.test_case_name,
                    a.test_step_id, a.test_step_name,
                    s.env_type
                FROM debug_attempts a
                JOIN debug_sessions s ON a.session_id = s.id
                WHERE 1 - (a.embedding <=> %s::vector) >= %s
                  AND {where}
                ORDER BY similarity DESC
                LIMIT %s
                """,
                params,
            )
            return [_row_to_match(r) for r in cur.fetchall()]
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


# =============================================================================
# Stats
# =============================================================================

def get_stats(
    db_config: dict,
    system_under_test: Optional[str] = None,
) -> Dict[str, Any]:
    """Get aggregate statistics from the memory store."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            system_filter = ""
            params: list = []
            if system_under_test:
                system_filter = "WHERE system_under_test = %s"
                params = [system_under_test]

            cur.execute(
                f"SELECT COUNT(*) FROM debug_sessions {system_filter}", params
            )
            total_sessions = cur.fetchone()[0]

            if system_under_test:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM debug_attempts a
                    JOIN debug_sessions s ON a.session_id = s.id
                    WHERE s.system_under_test = %s
                    """,
                    [system_under_test],
                )
            else:
                cur.execute("SELECT COUNT(*) FROM debug_attempts")
            total_attempts = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT system_under_test, COUNT(*)
                FROM debug_sessions {system_filter}
                GROUP BY system_under_test ORDER BY COUNT(*) DESC
                """,
                params,
            )
            by_system = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                f"""
                SELECT final_outcome, COUNT(*)
                FROM debug_sessions {system_filter}
                GROUP BY final_outcome ORDER BY COUNT(*) DESC
                """,
                params,
            )
            by_outcome = {row[0]: row[1] for row in cur.fetchall()}

            if system_under_test:
                cur.execute(
                    """
                    SELECT
                        SUM(CASE WHEN a.is_verified THEN 1 ELSE 0 END),
                        SUM(CASE WHEN a.is_active THEN 1 ELSE 0 END)
                    FROM debug_attempts a
                    JOIN debug_sessions s ON a.session_id = s.id
                    WHERE s.system_under_test = %s
                    """,
                    [system_under_test],
                )
            else:
                cur.execute(
                    """
                    SELECT
                        SUM(CASE WHEN is_verified THEN 1 ELSE 0 END),
                        SUM(CASE WHEN is_active THEN 1 ELSE 0 END)
                    FROM debug_attempts
                    """
                )
            row = cur.fetchone()
            verified_count = row[0] or 0
            active_count = row[1] or 0

        return {
            "total_sessions": total_sessions,
            "total_attempts": total_attempts,
            "by_system": by_system,
            "by_outcome": by_outcome,
            "verified_count": verified_count,
            "active_count": active_count,
        }
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


# =============================================================================
# Row Mappers
# =============================================================================

def get_attempt_by_id(db_config: dict, attempt_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single debug attempt by its UUID, enriched with session context."""
    conn = _get_conn(db_config)
    _healthy = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.session_id, a.iteration_number, a.error_category,
                       a.severity, a.response_code, a.outcome, a.hostname,
                       a.sampler_name, a.api_endpoint, a.symptom_text, a.diagnosis,
                       a.fix_description, a.fix_type, a.component_type,
                       a.manifest_excerpt, a.embedding_model, a.is_verified,
                       a.is_active, a.confirmed_count, a.created_at,
                       a.test_case_id, a.test_case_name, a.test_step_id, a.test_step_name,
                       s.system_under_test, s.environment, s.test_run_id,
                       s.system_alias, s.service_name, s.env_type
                FROM debug_attempts a
                JOIN debug_sessions s ON s.id = a.session_id
                WHERE a.id = %s
                """,
                (attempt_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            attempt = _row_to_attempt(row[:25])
            attempt["system_under_test"] = row[25]
            attempt["environment"] = row[26]
            attempt["test_run_id"] = row[27]
            attempt["system_alias"] = row[28]
            attempt["service_name"] = row[29]
            attempt["env_type"] = row[30]
            return attempt
    except Exception:
        _healthy = _safe_rollback(conn)
        raise
    finally:
        _put_conn(conn, healthy=_healthy)


def _row_to_session(row) -> Dict[str, Any]:
    return {
        "id": str(row[0]),
        "system_under_test": row[1],
        "test_run_id": row[2],
        "script_name": row[3],
        "auth_flow_type": row[4],
        "environment": row[5],
        "total_iterations": row[6],
        "final_outcome": row[7],
        "resolution_attempt_id": str(row[8]) if row[8] else None,
        "created_by": row[9],
        "notes": row[10],
        "started_at": row[11].isoformat() if row[11] else None,
        "completed_at": row[12].isoformat() if row[12] else None,
        "created_at": row[13].isoformat() if row[13] else None,
        "system_alias": row[14] if len(row) > 14 else "",
        "service_name": row[15] if len(row) > 15 else "",
        "env_type": row[16] if len(row) > 16 else "",
        "auth_alias": row[17] if len(row) > 17 else "",
    }


def _row_to_attempt(row) -> Dict[str, Any]:
    return {
        "id": str(row[0]),
        "session_id": str(row[1]),
        "iteration_number": row[2],
        "error_category": row[3],
        "severity": row[4],
        "response_code": row[5],
        "outcome": row[6],
        "hostname": row[7],
        "sampler_name": row[8],
        "api_endpoint": row[9],
        "symptom_text": row[10],
        "diagnosis": row[11],
        "fix_description": row[12],
        "fix_type": row[13],
        "component_type": row[14],
        "manifest_excerpt": row[15],
        "embedding_model": row[16],
        "is_verified": row[17],
        "is_active": row[18],
        "confirmed_count": row[19],
        "created_at": row[20].isoformat() if row[20] else None,
        "test_case_id": row[21] if len(row) > 21 else "",
        "test_case_name": row[22] if len(row) > 22 else "",
        "test_step_id": row[23] if len(row) > 23 else "",
        "test_step_name": row[24] if len(row) > 24 else "",
    }


def _row_to_match(row) -> Dict[str, Any]:
    return {
        "attempt_id": str(row[0]),
        "session_id": str(row[1]),
        "iteration_number": row[2],
        "symptom_text": row[3],
        "diagnosis": row[4],
        "fix_description": row[5],
        "fix_type": row[6],
        "outcome": row[7],
        "error_category": row[8],
        "severity": row[9],
        "hostname": row[10],
        "sampler_name": row[11],
        "api_endpoint": row[12],
        "component_type": row[13],
        "confirmed_count": row[14],
        "is_verified": row[15],
        "system_under_test": row[16],
        "environment": row[17],
        "auth_flow_type": row[18],
        "similarity": round(float(row[19]), 4),
        "system_alias": row[20] if len(row) > 20 else "",
        "service_name": row[21] if len(row) > 21 else "",
        "test_case_id": row[22] if len(row) > 22 else "",
        "test_case_name": row[23] if len(row) > 23 else "",
        "test_step_id": row[24] if len(row) > 24 else "",
        "test_step_name": row[25] if len(row) > 25 else "",
        "env_type": row[26] if len(row) > 26 else "",
    }
