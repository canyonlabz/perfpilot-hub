"""
PerfMemory Session Metadata Update Tool

Updates metadata fields on a single debug_sessions row by session ID.
Supports safe, parameterized UPDATE queries with dry-run preview.

Updateable fields:
  system_under_test, system_alias, service_name, environment, env_type,
  auth_flow_type, auth_alias, script_name, notes, created_by, final_outcome

Non-updateable (by design):
  id, test_run_id, resolution_attempt_id, started_at, completed_at, created_at,
  environment_alias (retained, unused — see ENH-001), total_iterations

Usage:
  python update_session_metadata.py --session-id <UUID> --show
  python update_session_metadata.py --session-id <UUID> --set environment="QA1" --set env_type="qa"
  python update_session_metadata.py --session-id <UUID> --set environment="QA1" --apply

Requirements:
  - perfmemory-mcp/.env must exist with valid DB credentials
  - PostgreSQL database must be running

No DELETE, DROP, or TRUNCATE operations are supported.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from dotenv import dotenv_values

SCRIPT_DIR = Path(__file__).resolve().parent
PERFMEMORY_DIR = SCRIPT_DIR.parent
DEFAULT_ENV_PATH = PERFMEMORY_DIR / ".env"
LOGS_DIR = SCRIPT_DIR / "logs"

UPDATEABLE_FIELDS = [
    "system_under_test",
    "system_alias",
    "service_name",
    "environment",
    "env_type",
    "auth_flow_type",
    "auth_alias",
    "script_name",
    "notes",
    "created_by",
    "final_outcome",
]

DISPLAY_FIELDS = [
    "id",
    "system_under_test",
    "system_alias",
    "service_name",
    "test_run_id",
    "script_name",
    "auth_flow_type",
    "auth_alias",
    "env_type",
    "environment",
    "environment_alias",
    "final_outcome",
    "total_iterations",
    "resolution_attempt_id",
    "created_by",
    "notes",
    "started_at",
    "completed_at",
    "created_at",
]


def setup_logging() -> logging.Logger:
    """Configure dual logging: stdout + timestamped log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"update_session_{timestamp}.log"

    logger = logging.getLogger("update_session_metadata")
    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(console)
    logger.addHandler(fh)

    logger.info(f"Log file: {log_file}")
    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update metadata fields on a PerfMemory debug session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Updateable fields:
  {', '.join(UPDATEABLE_FIELDS)}

Examples:
  python update_session_metadata.py --session-id <UUID> --show
  python update_session_metadata.py --session-id <UUID> --set environment="QA1"
  python update_session_metadata.py --session-id <UUID> --set env_type="qa" --set environment="QA1" --apply
        """,
    )

    parser.add_argument(
        "--session-id",
        type=str,
        required=True,
        help="UUID of the debug session to update",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display current field values for the session (read-only)",
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar='field="value"',
        help='Set a field value (e.g., --set environment="QA1"). Can be repeated.',
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the update (without this flag, runs in dry-run mode)",
    )

    parser.add_argument("--env-file", type=str, default=str(DEFAULT_ENV_PATH),
                        help=f"Path to .env file (default: {DEFAULT_ENV_PATH})")
    parser.add_argument("--host", type=str, help="PostgreSQL host (overrides .env)")
    parser.add_argument("--port", type=int, help="PostgreSQL port (overrides .env)")
    parser.add_argument("--db", type=str, help="PostgreSQL database name (overrides .env)")
    parser.add_argument("--user", type=str, help="PostgreSQL user (overrides .env)")
    parser.add_argument("--password", type=str, help="PostgreSQL password (overrides .env)")

    return parser.parse_args()


def load_db_config(args: argparse.Namespace) -> Dict[str, Any]:
    """Load database configuration from .env file with CLI overrides."""
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"ERROR: .env file not found at: {env_path}")
        print("Create one based on .env.example with your database credentials.")
        sys.exit(1)

    env_values = dotenv_values(env_path)
    return {
        "host": args.host or env_values.get("POSTGRES_HOST", "localhost"),
        "port": int(args.port or env_values.get("POSTGRES_PORT", "5432")),
        "dbname": args.db or env_values.get("POSTGRES_DB", "perfmemory"),
        "user": args.user or env_values.get("POSTGRES_USER", "postgres"),
        "password": args.password or env_values.get("POSTGRES_PASSWORD", ""),
        "sslmode": env_values.get("POSTGRES_SSLMODE", "prefer"),
        "sslrootcert": env_values.get("POSTGRES_SSLROOTCERT", ""),
    }


def get_connection(db_config: Dict[str, Any]):
    """Create a database connection."""
    kwargs = {
        "host": db_config["host"],
        "port": db_config["port"],
        "dbname": db_config["dbname"],
        "user": db_config["user"],
        "password": db_config["password"],
    }
    sslmode = db_config.get("sslmode", "prefer")
    if sslmode and sslmode != "disable":
        kwargs["sslmode"] = sslmode
        sslrootcert = db_config.get("sslrootcert", "")
        if sslrootcert:
            kwargs["sslrootcert"] = sslrootcert
    return psycopg2.connect(**kwargs)


def parse_set_args(set_args: List[str]) -> Dict[str, str]:
    """Parse --set field=value arguments into a dict.

    Validates field names against the allowlist.
    """
    updates: Dict[str, str] = {}
    for arg in set_args:
        if "=" not in arg:
            print(f"ERROR: Invalid --set format: '{arg}'. Expected: field=\"value\"")
            sys.exit(1)

        field, value = arg.split("=", 1)
        field = field.strip()
        value = value.strip().strip('"').strip("'")

        if field not in UPDATEABLE_FIELDS:
            print(f"ERROR: Field '{field}' is not updateable.")
            print(f"Updateable fields: {', '.join(UPDATEABLE_FIELDS)}")
            sys.exit(1)

        updates[field] = value

    return updates


def fetch_session(conn, session_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a session by ID. Returns None if not found."""
    cols = ", ".join(DISPLAY_FIELDS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM debug_sessions WHERE id = %s", (session_id,))
        row = cur.fetchone()

    if row is None:
        return None

    return {DISPLAY_FIELDS[i]: row[i] for i in range(len(DISPLAY_FIELDS))}


def display_session(log: logging.Logger, session: Dict[str, Any], title: str = "Current Values"):
    """Display session fields in a readable format."""
    log.info(f"--- {title} ---")
    max_key_len = max(len(k) for k in DISPLAY_FIELDS)
    for field in DISPLAY_FIELDS:
        value = session.get(field)
        display_value = str(value) if value is not None else "(null)"
        if isinstance(value, str) and value == "":
            display_value = "(empty)"
        updateable = " [updateable]" if field in UPDATEABLE_FIELDS else ""
        log.info(f"  {field:<{max_key_len}}  {display_value}{updateable}")
    log.info("")


def display_changes(log: logging.Logger, session: Dict[str, Any], updates: Dict[str, str]):
    """Display before/after for each changed field."""
    log.info("--- Proposed Changes ---")
    max_key_len = max(len(k) for k in updates)
    for field, new_value in updates.items():
        old_value = session.get(field)
        old_display = str(old_value) if old_value is not None else "(null)"
        if isinstance(old_value, str) and old_value == "":
            old_display = "(empty)"
        log.info(f"  {field:<{max_key_len}}  {old_display}  ->  {new_value}")
    log.info("")


def apply_updates(conn, session_id: str, updates: Dict[str, str], log: logging.Logger) -> int:
    """Execute the UPDATE statement. Returns number of rows affected."""
    set_clauses = [f"{field} = %s" for field in updates]
    values = list(updates.values())
    values.append(session_id)

    sql = f"UPDATE debug_sessions SET {', '.join(set_clauses)} WHERE id = %s"

    log.debug(f"SQL: {sql}")
    log.debug(f"Params: {values}")

    with conn.cursor() as cur:
        cur.execute(sql, values)
        count = cur.rowcount

    conn.commit()
    return count


def main():
    args = parse_args()
    log = setup_logging()

    mode = "APPLY MODE" if args.apply else "DRY RUN"
    log.info("")
    log.info(f"PerfMemory Session Metadata Update Tool - {mode}")
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Session: {args.session_id}")
    log.info("")

    if not args.show and not args.set:
        log.error("ERROR: Provide --show to view fields, or --set to update fields.")
        sys.exit(1)

    db_config = load_db_config(args)

    try:
        conn = get_connection(db_config)
        log.info("Database connection: OK")
    except Exception as e:
        log.error(f"Database connection FAILED: {e}")
        sys.exit(1)

    log.info("")

    session = fetch_session(conn, args.session_id)
    if session is None:
        log.error(f"ERROR: Session not found: {args.session_id}")
        conn.close()
        sys.exit(1)

    if args.show:
        display_session(log, session)
        conn.close()
        return

    updates = parse_set_args(args.set)
    if not updates:
        log.error("ERROR: No valid --set arguments provided.")
        conn.close()
        sys.exit(1)

    display_session(log, session, title="Before")
    display_changes(log, session, updates)

    if not args.apply:
        log.info("=" * 55)
        log.info("DRY RUN COMPLETE - no changes were made.")
        log.info("Run with --apply to execute the update.")
        log.info("=" * 55)
        conn.close()
        return

    log.info("APPLYING UPDATE...")
    count = apply_updates(conn, args.session_id, updates, log)
    log.info(f"  -> {count} row(s) updated")
    log.info("")

    updated_session = fetch_session(conn, args.session_id)
    if updated_session:
        display_session(log, updated_session, title="After")

    log.info("=" * 55)
    log.info("UPDATE COMPLETE")
    log.info("=" * 55)

    conn.close()


if __name__ == "__main__":
    main()
