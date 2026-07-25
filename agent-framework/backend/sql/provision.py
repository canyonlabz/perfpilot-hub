"""Provision the `perfagent_state` database (Feature 3.3 - PBI 3.3.3).

Connects to the existing PostgreSQL instance (the same one that hosts
`perfmemory`), creates the `perfagent_state` database if it does not exist,
and applies the six table-creation scripts in order.

Idempotent: safe to re-run against an already-provisioned instance. Existing
data is never modified or dropped.

Usage from the repo root:

    python agent-framework/sql/provision.py

Reads connection settings from `agent-framework/.env` (PERFAGENT_STATE_HOST,
PERFAGENT_STATE_PORT, PERFAGENT_STATE_USER, PERFAGENT_STATE_PASSWORD,
PERFAGENT_STATE_DB). The user / password must have CREATE DATABASE privileges
on the cluster (Epic 3 testing typically uses the same `postgres` superuser
that perfmemory-mcp uses). Epic 4 will split these into least-privilege roles.

Synchronous psycopg2 is used here (not asyncpg) because:

- DDL is one-shot and synchronous in nature
- CREATE DATABASE cannot be wrapped in a transaction, so we rely on
  psycopg2's autocommit mode for that single statement
- The runtime asyncpg pool lives in `utils/db.py` and is created elsewhere
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# psycopg2 is provided by perfmemory-mcp's deps which are already installed.
import psycopg2
from psycopg2 import sql

# python-dotenv is in agent-framework/requirements.txt.
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("provision")

# File order; the per-table scripts (002 - 008) all run against the
# perfagent_state database after CREATE DATABASE. F3.3 landed 002 - 007;
# F3.7.0 added 008 (agent_threads, the persistent conversation container).
PER_TABLE_SQL_FILES = (
    "002_create_agent_sessions.sql",
    "003_create_agent_tasks.sql",
    "004_create_agent_checkpoints.sql",
    "005_create_conversation_messages.sql",
    "006_create_tool_call_traces.sql",
    "007_create_hitl_approvals.sql",
    "008_create_agent_threads.sql",
)

TARGET_DB_NAME = "perfagent_state"


def _repo_paths() -> tuple[Path, Path, Path]:
    """Resolve repo root, agent-framework dir, and sql dir from this file's location."""
    sql_dir = Path(__file__).resolve().parent
    framework_dir = sql_dir.parent
    repo_root = framework_dir.parent
    return repo_root, framework_dir, sql_dir


def _load_env(framework_dir: Path) -> dict[str, str]:
    """Load `agent-framework/.env` and pull the PERFAGENT_STATE_* variables out.

    Raises:
        SystemExit: if the .env file does not exist or required variables are missing.
    """
    env_path = framework_dir / ".env"
    if not env_path.exists():
        log.error("agent-framework/.env not found at %s", env_path)
        log.error("Copy .env.example to .env and fill in values, then re-run.")
        raise SystemExit(2)

    load_dotenv(env_path, override=False)

    required = (
        "PERFAGENT_STATE_HOST",
        "PERFAGENT_STATE_PORT",
        "PERFAGENT_STATE_DB",
        "PERFAGENT_STATE_USER",
        "PERFAGENT_STATE_PASSWORD",
    )
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        raise SystemExit(2)

    expected_db = os.environ["PERFAGENT_STATE_DB"]
    if expected_db != TARGET_DB_NAME:
        log.warning(
            "PERFAGENT_STATE_DB is '%s' but provision.py creates '%s'; using '%s'",
            expected_db,
            TARGET_DB_NAME,
            TARGET_DB_NAME,
        )

    settings = {
        "host": os.environ["PERFAGENT_STATE_HOST"],
        "port": os.environ["PERFAGENT_STATE_PORT"],
        "user": os.environ["PERFAGENT_STATE_USER"],
        "password": os.environ["PERFAGENT_STATE_PASSWORD"],
        "sslmode": os.environ.get("PERFAGENT_STATE_SSLMODE", "prefer"),
    }
    sslrootcert = os.environ.get("PERFAGENT_STATE_SSLROOTCERT", "").strip()
    if sslrootcert:
        settings["sslrootcert"] = sslrootcert
    return settings


def _connect(dbname: str, settings: dict[str, str]):
    """Open a psycopg2 connection to a specific database with the loaded settings."""
    return psycopg2.connect(dbname=dbname, **settings)


def _ensure_database(settings: dict[str, str]) -> bool:
    """Create the `perfagent_state` database if it does not exist.

    Returns:
        True if the database was created in this call, False if it already existed.
    """
    log.info(
        "Connecting to postgres database on %s:%s as user '%s' to check / create '%s'",
        settings["host"],
        settings["port"],
        settings["user"],
        TARGET_DB_NAME,
    )
    # Note: we do NOT use `with _connect(...) as conn:` here because the
    # context-manager form of a psycopg2 connection wraps cursor operations
    # in an implicit transaction. CREATE DATABASE cannot run inside a
    # transaction, so we switch the connection to autocommit immediately
    # after opening it and close it explicitly in `finally`.
    conn = _connect("postgres", settings)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TARGET_DB_NAME,))
            existed = cur.fetchone() is not None
            if existed:
                log.info("Database '%s' already exists - skipping CREATE DATABASE", TARGET_DB_NAME)
                return False

            log.info("Creating database '%s' ...", TARGET_DB_NAME)
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET_DB_NAME)))
            log.info("Database '%s' created.", TARGET_DB_NAME)
            return True
    finally:
        conn.close()


def _apply_table_sql(sql_dir: Path, settings: dict[str, str]) -> None:
    """Apply files 002 - 007 against the perfagent_state database in order."""
    log.info("Applying %d table-creation scripts to '%s' ...", len(PER_TABLE_SQL_FILES), TARGET_DB_NAME)
    with _connect(TARGET_DB_NAME, settings) as conn:
        with conn.cursor() as cur:
            for filename in PER_TABLE_SQL_FILES:
                path = sql_dir / filename
                if not path.exists():
                    raise FileNotFoundError(f"Expected SQL file not found: {path}")
                log.info("  applying %s", filename)
                with open(path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
        conn.commit()
    log.info("All %d table-creation scripts applied.", len(PER_TABLE_SQL_FILES))


def _verify_schema(settings: dict[str, str]) -> bool:
    """Confirm all seven tables exist in the perfagent_state database."""
    expected_tables = (
        "agent_sessions",
        "agent_tasks",
        "agent_checkpoints",
        "conversation_messages",
        "tool_call_traces",
        "hitl_approvals",
        "agent_threads",
    )
    with _connect(TARGET_DB_NAME, settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                ORDER BY table_name
                """,
                (list(expected_tables),),
            )
            present = {row[0] for row in cur.fetchall()}

    missing = [t for t in expected_tables if t not in present]
    if missing:
        log.error("Schema verification FAILED. Missing tables: %s", ", ".join(missing))
        return False

    log.info("Schema verified - all %d tables present:", len(expected_tables))
    for t in expected_tables:
        log.info("  [OK] %s", t)
    return True


def main() -> int:
    repo_root, framework_dir, sql_dir = _repo_paths()
    log.info("Repo root        : %s", repo_root)
    log.info("Agent framework  : %s", framework_dir)
    log.info("SQL dir          : %s", sql_dir)

    try:
        settings = _load_env(framework_dir)
    except SystemExit as exc:
        return int(getattr(exc, "code", 2))

    try:
        _ensure_database(settings)
        _apply_table_sql(sql_dir, settings)
    except psycopg2.Error as exc:
        log.error("PostgreSQL error: %s", exc)
        return 1
    except FileNotFoundError as exc:
        log.error("File not found: %s", exc)
        return 1

    if not _verify_schema(settings):
        return 1

    log.info("Provisioning complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
