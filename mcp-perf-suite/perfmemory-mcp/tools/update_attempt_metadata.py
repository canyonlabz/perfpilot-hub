"""
PerfMemory Attempt Metadata Update Tool

Updates metadata fields on a single debug_attempts row by attempt ID.
Supports safe, parameterized UPDATE queries with dry-run preview.

Updateable fields:
  error_category, severity, response_code, outcome, hostname, sampler_name,
  api_endpoint, diagnosis, fix_description, fix_type, component_type,
  test_case_id, test_case_name, test_step_id, test_step_name, manifest_excerpt

Non-updateable (by design):
  id, session_id, iteration_number — structural fields
  symptom_text, embedding, embedding_model — changing text without re-embedding
    creates a mismatch between stored text and its vector representation
  is_verified — use verify_attempt MCP tool
  is_active — use archive_attempt MCP tool
  confirmed_count — system-managed confidence counter
  created_at — timestamp, system-managed

Usage:
  python update_attempt_metadata.py --attempt-id <UUID> --show
  python update_attempt_metadata.py --attempt-id <UUID> --set error_category="HTTP 5xx Error"
  python update_attempt_metadata.py --attempt-id <UUID> --set fix_type="add_extractor" --apply

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
from typing import Any, Dict, List, Optional

import psycopg2
from dotenv import dotenv_values

SCRIPT_DIR = Path(__file__).resolve().parent
PERFMEMORY_DIR = SCRIPT_DIR.parent
DEFAULT_ENV_PATH = PERFMEMORY_DIR / ".env"
LOGS_DIR = SCRIPT_DIR / "logs"

UPDATEABLE_FIELDS = [
    "error_category",
    "severity",
    "response_code",
    "outcome",
    "hostname",
    "sampler_name",
    "api_endpoint",
    "diagnosis",
    "fix_description",
    "fix_type",
    "component_type",
    "test_case_id",
    "test_case_name",
    "test_step_id",
    "test_step_name",
    "manifest_excerpt",
]

PROTECTED_FIELDS = {
    "symptom_text": "Changing symptom_text without re-embedding creates a vector mismatch. Archive this attempt and create a new one instead.",
    "embedding": "Embeddings are system-managed. They are generated from symptom_text by the MCP server.",
    "embedding_model": "Records which model produced the embedding. System-managed.",
    "is_verified": "Use the verify_attempt MCP tool instead.",
    "is_active": "Use the archive_attempt MCP tool instead.",
    "confirmed_count": "System-managed confidence counter. Incremented when matched_attempt_id is provided.",
    "id": "Primary key — immutable.",
    "session_id": "Foreign key to parent session — changing breaks relationships.",
    "iteration_number": "Positional within session — changing breaks ordering.",
    "created_at": "Timestamp — system-managed.",
}

DISPLAY_FIELDS = [
    "id",
    "session_id",
    "iteration_number",
    "error_category",
    "severity",
    "response_code",
    "outcome",
    "hostname",
    "sampler_name",
    "api_endpoint",
    "symptom_text",
    "diagnosis",
    "fix_description",
    "fix_type",
    "component_type",
    "test_case_id",
    "test_case_name",
    "test_step_id",
    "test_step_name",
    "manifest_excerpt",
    "embedding_model",
    "is_verified",
    "is_active",
    "confirmed_count",
    "created_at",
]

MAX_TEXT_DISPLAY = 120


def setup_logging() -> logging.Logger:
    """Configure dual logging: stdout + timestamped log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"update_attempt_{timestamp}.log"

    logger = logging.getLogger("update_attempt_metadata")
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
        description="Update metadata fields on a PerfMemory debug attempt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Updateable fields:
  {', '.join(UPDATEABLE_FIELDS)}

Protected fields (cannot be updated via this tool):
  {', '.join(PROTECTED_FIELDS.keys())}

Examples:
  python update_attempt_metadata.py --attempt-id <UUID> --show
  python update_attempt_metadata.py --attempt-id <UUID> --set error_category="HTTP 5xx Error"
  python update_attempt_metadata.py --attempt-id <UUID> --set fix_type="add_extractor" --apply
        """,
    )

    parser.add_argument(
        "--attempt-id",
        type=str,
        required=True,
        help="UUID of the debug attempt to update",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display current field values for the attempt (read-only)",
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar='field="value"',
        help='Set a field value (e.g., --set error_category="HTTP 5xx Error"). Can be repeated.',
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

    Validates against allowlist and rejects protected fields with explanation.
    """
    updates: Dict[str, str] = {}
    for arg in set_args:
        if "=" not in arg:
            print(f"ERROR: Invalid --set format: '{arg}'. Expected: field=\"value\"")
            sys.exit(1)

        field, value = arg.split("=", 1)
        field = field.strip()
        value = value.strip().strip('"').strip("'")

        if field in PROTECTED_FIELDS:
            print(f"ERROR: Field '{field}' cannot be updated via this tool.")
            print(f"  Reason: {PROTECTED_FIELDS[field]}")
            sys.exit(1)

        if field not in UPDATEABLE_FIELDS:
            print(f"ERROR: Field '{field}' is not recognized.")
            print(f"Updateable fields: {', '.join(UPDATEABLE_FIELDS)}")
            sys.exit(1)

        updates[field] = value

    return updates


def truncate_display(value: Any, max_len: int = MAX_TEXT_DISPLAY) -> str:
    """Truncate long text values for display."""
    if value is None:
        return "(null)"
    s = str(value)
    if isinstance(value, str) and value == "":
        return "(empty)"
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def fetch_attempt(conn, attempt_id: str) -> Optional[Dict[str, Any]]:
    """Fetch an attempt by ID. Returns None if not found."""
    cols = ", ".join(DISPLAY_FIELDS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM debug_attempts WHERE id = %s", (attempt_id,))
        row = cur.fetchone()

    if row is None:
        return None

    return {DISPLAY_FIELDS[i]: row[i] for i in range(len(DISPLAY_FIELDS))}


def display_attempt(log: logging.Logger, attempt: Dict[str, Any], title: str = "Current Values"):
    """Display attempt fields in a readable format."""
    log.info(f"--- {title} ---")
    max_key_len = max(len(k) for k in DISPLAY_FIELDS)
    for field in DISPLAY_FIELDS:
        value = attempt.get(field)
        display_value = truncate_display(value)
        tag = ""
        if field in UPDATEABLE_FIELDS:
            tag = " [updateable]"
        elif field in PROTECTED_FIELDS:
            tag = " [protected]"
        log.info(f"  {field:<{max_key_len}}  {display_value}{tag}")
    log.info("")


def display_changes(log: logging.Logger, attempt: Dict[str, Any], updates: Dict[str, str]):
    """Display before/after for each changed field."""
    log.info("--- Proposed Changes ---")
    max_key_len = max(len(k) for k in updates)
    for field, new_value in updates.items():
        old_value = attempt.get(field)
        old_display = truncate_display(old_value)
        log.info(f"  {field:<{max_key_len}}  {old_display}  ->  {new_value}")
    log.info("")


def apply_updates(conn, attempt_id: str, updates: Dict[str, str], log: logging.Logger) -> int:
    """Execute the UPDATE statement. Returns number of rows affected."""
    set_clauses = [f"{field} = %s" for field in updates]
    values = list(updates.values())
    values.append(attempt_id)

    sql = f"UPDATE debug_attempts SET {', '.join(set_clauses)} WHERE id = %s"

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
    log.info(f"PerfMemory Attempt Metadata Update Tool - {mode}")
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Attempt: {args.attempt_id}")
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

    attempt = fetch_attempt(conn, args.attempt_id)
    if attempt is None:
        log.error(f"ERROR: Attempt not found: {args.attempt_id}")
        conn.close()
        sys.exit(1)

    if args.show:
        display_attempt(log, attempt)
        conn.close()
        return

    updates = parse_set_args(args.set)
    if not updates:
        log.error("ERROR: No valid --set arguments provided.")
        conn.close()
        sys.exit(1)

    display_attempt(log, attempt, title="Before")
    display_changes(log, attempt, updates)

    if not args.apply:
        log.info("=" * 55)
        log.info("DRY RUN COMPLETE - no changes were made.")
        log.info("Run with --apply to execute the update.")
        log.info("=" * 55)
        conn.close()
        return

    log.info("APPLYING UPDATE...")
    count = apply_updates(conn, args.attempt_id, updates, log)
    log.info(f"  -> {count} row(s) updated")
    log.info("")

    updated_attempt = fetch_attempt(conn, args.attempt_id)
    if updated_attempt:
        display_attempt(log, updated_attempt, title="After")

    log.info("=" * 55)
    log.info("UPDATE COMPLETE")
    log.info("=" * 55)

    conn.close()


if __name__ == "__main__":
    main()
