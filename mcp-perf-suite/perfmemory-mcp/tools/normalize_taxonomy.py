"""
PerfMemory Taxonomy Normalization Tool

Normalizes existing database values to canonical taxonomy names:
  - system_under_test → canonical application name
  - system_alias → backfilled from taxonomy application alias
  - error_category (optional) → canonical error category name
  - env_type (optional) → canonical environment type name
  - environment (optional) → canonical specific environment name
  - Apache AGE graph (optional) → Project.name, Attempt.project

Usage:
  python normalize_taxonomy.py              # Dry-run (preview changes)
  python normalize_taxonomy.py --apply      # Execute normalization

Requirements:
  - perfmemory-mcp/taxonomy.yaml must exist
  - perfmemory-mcp/.env must exist with valid DB credentials
  - PostgreSQL database must be running

This tool is designed to be adaptable — modify the matching logic in
_match_system_to_taxonomy() to suit your team's naming conventions.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import psycopg2
import psycopg2.extras
import yaml
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Paths (relative to this script's location in perfmemory-mcp/tools/)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PERFMEMORY_DIR = SCRIPT_DIR.parent
DEFAULT_TAXONOMY_PATH = PERFMEMORY_DIR / "taxonomy.yaml"
DEFAULT_ENV_PATH = PERFMEMORY_DIR / ".env"
LOGS_DIR = SCRIPT_DIR / "logs"


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    """Configure dual logging: verbose stdout + timestamped log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"normalize_{timestamp}.log"

    logger = logging.getLogger("normalize_taxonomy")
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_fmt)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler.setFormatter(file_fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    logger.info(f"Log file: {log_file}")
    return logger


# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize PerfMemory database values to canonical taxonomy names.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python normalize_taxonomy.py                          # Dry-run preview
  python normalize_taxonomy.py --apply                  # Execute changes
  python normalize_taxonomy.py --apply --fix-error-category --fix-env-type
  python normalize_taxonomy.py --apply --fix-environment --fix-env-type
  python normalize_taxonomy.py --apply --include-graph
  python normalize_taxonomy.py --host localhost --port 5433
        """,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute normalization (without this flag, runs in dry-run mode)",
    )
    parser.add_argument(
        "--fix-error-category",
        action="store_true",
        help="Also normalize error_category in debug_attempts via taxonomy alias resolution",
    )
    parser.add_argument(
        "--fix-env-type",
        action="store_true",
        help="Normalize env_type in debug_sessions via taxonomy environment_types alias resolution",
    )
    parser.add_argument(
        "--fix-environment",
        action="store_true",
        help="Normalize environment (specific name) in debug_sessions via taxonomy environments lookup",
    )
    parser.add_argument(
        "--include-graph",
        action="store_true",
        help="Also update Apache AGE graph (Project.name, Attempt.project)",
    )
    parser.add_argument(
        "--taxonomy",
        type=str,
        default=str(DEFAULT_TAXONOMY_PATH),
        help=f"Path to taxonomy YAML file (default: {DEFAULT_TAXONOMY_PATH})",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=str(DEFAULT_ENV_PATH),
        help=f"Path to .env file for DB credentials (default: {DEFAULT_ENV_PATH})",
    )

    # DB connection overrides
    parser.add_argument("--host", type=str, help="PostgreSQL host (overrides .env)")
    parser.add_argument("--port", type=int, help="PostgreSQL port (overrides .env)")
    parser.add_argument("--db", type=str, help="PostgreSQL database name (overrides .env)")
    parser.add_argument("--user", type=str, help="PostgreSQL user (overrides .env)")
    parser.add_argument("--password", type=str, help="PostgreSQL password (overrides .env)")

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Configuration Loading
# ---------------------------------------------------------------------------
def load_db_config(args: argparse.Namespace) -> Dict[str, Any]:
    """Load database configuration from .env file with CLI overrides."""
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"ERROR: .env file not found at: {env_path}")
        print("Create one based on .env.example with your database credentials.")
        sys.exit(1)

    env_values = dotenv_values(env_path)

    config = {
        "host": args.host or env_values.get("POSTGRES_HOST", "localhost"),
        "port": int(args.port or env_values.get("POSTGRES_PORT", "5432")),
        "dbname": args.db or env_values.get("POSTGRES_DB", "perfmemory"),
        "user": args.user or env_values.get("POSTGRES_USER", "postgres"),
        "password": args.password or env_values.get("POSTGRES_PASSWORD", ""),
        "sslmode": env_values.get("POSTGRES_SSLMODE", "prefer"),
        "sslrootcert": env_values.get("POSTGRES_SSLROOTCERT", ""),
    }

    return config


def load_taxonomy(taxonomy_path: str) -> Dict[str, Any]:
    """Load taxonomy YAML. Exits with error if file is missing."""
    path = Path(taxonomy_path)
    if not path.exists():
        print(f"ERROR: taxonomy.yaml not found at: {path}")
        print("")
        print("The taxonomy file is REQUIRED for normalization.")
        print("To create one:")
        print(f"  1. Copy taxonomy.example.yaml to taxonomy.yaml:")
        print(f"     cp {PERFMEMORY_DIR / 'taxonomy.example.yaml'} {PERFMEMORY_DIR / 'taxonomy.yaml'}")
        print("  2. Customize the 'applications' section with your projects")
        print("  3. Re-run this tool")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data


# ---------------------------------------------------------------------------
# Taxonomy Matching Logic
# ---------------------------------------------------------------------------
class TaxonomyMatcher:
    """Matches database values against taxonomy definitions.

    Provides 3-tier matching for system_under_test and simple alias
    resolution for error_category, env_type, and environment names.

    Designed to be adaptable — modify _match_system_to_taxonomy() for
    custom matching rules specific to your team's naming conventions.
    """

    def __init__(self, taxonomy: Dict[str, Any]):
        self.taxonomy = taxonomy
        self.applications = taxonomy.get("applications", [])
        self.error_categories = taxonomy.get("error_categories", [])
        self.environment_types = taxonomy.get("environment_types", [])
        self.environments = taxonomy.get("environments", [])

        # Build lookup tables
        self._error_lookup = self._build_alias_lookup(self.error_categories)
        self._env_type_lookup = self._build_alias_lookup(self.environment_types)
        self._env_name_lookup = self._build_env_name_lookup()

    def _build_alias_lookup(self, entries: List[Dict]) -> Dict[str, str]:
        """Build case-insensitive alias-to-canonical mapping."""
        lookup: Dict[str, str] = {}
        for entry in entries:
            canonical = entry.get("name", "")
            if not canonical:
                continue
            lookup[canonical.lower()] = canonical
            for alias in entry.get("aliases", []):
                lookup[alias.lower()] = canonical
        return lookup

    def match_system(self, system_under_test: str) -> Optional[Dict[str, str]]:
        """Match a system_under_test value to a taxonomy application.

        Returns dict with 'canonical_name' and 'alias' if matched, None otherwise.

        Matching tiers:
          1. Exact match on application name, alias, or any aliases entry
             (case-insensitive)
          2. Contains match — application name, alias, or aliases entry found
             as substring
          3. No match — returns None
        """
        if not system_under_test:
            return None

        value_lower = system_under_test.lower().strip()

        # Tier 1: Exact match
        for app in self.applications:
            name = app.get("name", "")
            alias = app.get("alias", "")
            if value_lower == name.lower() or value_lower == alias.lower():
                return {"canonical_name": name, "alias": alias}
            for alt in app.get("aliases", []):
                if alt and value_lower == alt.lower():
                    return {"canonical_name": name, "alias": alias}

        # Tier 2: Contains match (taxonomy name, alias, or aliases entry
        # appears in the value). Prefer longer matches to avoid false
        # positives with short aliases.
        best_match = None
        best_match_len = 0

        for app in self.applications:
            name = app.get("name", "")
            alias = app.get("alias", "")

            if name and name.lower() in value_lower:
                if len(name) > best_match_len:
                    best_match = {"canonical_name": name, "alias": alias}
                    best_match_len = len(name)

            if alias and self._alias_in_value(alias.lower(), value_lower):
                if len(alias) > best_match_len:
                    best_match = {"canonical_name": name, "alias": alias}
                    best_match_len = len(alias)

            for alt in app.get("aliases", []):
                if alt and alt.lower() in value_lower:
                    if len(alt) > best_match_len:
                        best_match = {"canonical_name": name, "alias": alias}
                        best_match_len = len(alt)

        return best_match

    def _alias_in_value(self, alias_lower: str, value_lower: str) -> bool:
        """Check if alias appears in value with reasonable boundaries.

        Avoids matching short aliases inside unrelated words. For aliases
        shorter than 3 characters, requires the alias to appear as a
        standalone token (surrounded by non-alphanumeric characters).
        """
        if alias_lower not in value_lower:
            return False

        # For short aliases, check word boundaries
        if len(alias_lower) <= 2:
            idx = value_lower.find(alias_lower)
            while idx >= 0:
                before_ok = idx == 0 or not value_lower[idx - 1].isalnum()
                after_ok = (
                    idx + len(alias_lower) >= len(value_lower)
                    or not value_lower[idx + len(alias_lower)].isalnum()
                )
                if before_ok and after_ok:
                    return True
                idx = value_lower.find(alias_lower, idx + 1)
            return False

        return True

    def _build_env_name_lookup(self) -> Dict[str, str]:
        """Build case-insensitive lookup for specific environment names."""
        lookup: Dict[str, str] = {}
        for env in self.environments:
            name = env.get("name", "")
            if name:
                lookup[name.lower()] = name
        return lookup

    def resolve_error_category(self, value: str) -> Optional[str]:
        """Resolve an error_category to its canonical name. Returns None if no match."""
        if not value:
            return None
        return self._error_lookup.get(value.lower().strip())

    def resolve_env_type(self, value: str) -> Optional[str]:
        """Resolve an env_type to its canonical name via environment_types aliases.

        Returns None if no match.
        """
        if not value:
            return None
        return self._env_type_lookup.get(value.lower().strip())

    def resolve_env_name(self, value: str) -> Optional[str]:
        """Resolve a specific environment name against taxonomy environments.

        Returns the canonical name if found (case-normalized), None otherwise.
        """
        if not value:
            return None
        return self._env_name_lookup.get(value.lower().strip())


# ---------------------------------------------------------------------------
# Database Operations
# ---------------------------------------------------------------------------
def get_connection(db_config: Dict[str, Any]):
    """Create a direct database connection (no pooling needed for a CLI tool)."""
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


def check_column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in the specified table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        """, (table, column))
        return cur.fetchone() is not None


def discover_systems(conn) -> List[Dict[str, Any]]:
    """Query distinct system_under_test values with session counts."""
    has_alias = check_column_exists(conn, "debug_sessions", "system_alias")

    with conn.cursor() as cur:
        if has_alias:
            cur.execute("""
                SELECT system_under_test, system_alias, COUNT(*) as session_count
                FROM debug_sessions
                GROUP BY system_under_test, system_alias
                ORDER BY session_count DESC
            """)
        else:
            cur.execute("""
                SELECT system_under_test, '' as system_alias, COUNT(*) as session_count
                FROM debug_sessions
                GROUP BY system_under_test
                ORDER BY session_count DESC
            """)
        rows = cur.fetchall()

    results = []
    for row in rows:
        results.append({
            "system_under_test": row[0] or "",
            "system_alias": row[1] or "",
            "session_count": row[2],
        })
    return results


def discover_error_categories(conn) -> List[Dict[str, Any]]:
    """Query distinct error_category values with attempt counts."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT error_category, COUNT(*) as attempt_count
            FROM debug_attempts
            WHERE error_category IS NOT NULL AND error_category != ''
            GROUP BY error_category
            ORDER BY attempt_count DESC
        """)
        rows = cur.fetchall()

    return [{"error_category": row[0], "attempt_count": row[1]} for row in rows]


def discover_environments(conn) -> List[Dict[str, Any]]:
    """Query distinct environment values with session counts."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT environment, COUNT(*) as session_count
            FROM debug_sessions
            WHERE environment IS NOT NULL AND environment != ''
            GROUP BY environment
            ORDER BY session_count DESC
        """)
        rows = cur.fetchall()

    return [{"environment": row[0], "session_count": row[1]} for row in rows]


def discover_env_types(conn) -> List[Dict[str, Any]]:
    """Query distinct env_type values with session counts."""
    if not check_column_exists(conn, "debug_sessions", "env_type"):
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT env_type, COUNT(*) as session_count
            FROM debug_sessions
            WHERE env_type IS NOT NULL AND env_type != ''
            GROUP BY env_type
            ORDER BY session_count DESC
        """)
        rows = cur.fetchall()

    return [{"env_type": row[0], "session_count": row[1]} for row in rows]


def apply_system_updates(
    conn, updates: List[Dict[str, str]], log: logging.Logger
) -> int:
    """Execute UPDATE statements for system_under_test normalization."""
    has_alias = check_column_exists(conn, "debug_sessions", "system_alias")
    total_updated = 0

    with conn.cursor() as cur:
        for update in updates:
            old_value = update["old_system"]
            new_name = update["canonical_name"]
            new_alias = update["alias"]

            if has_alias:
                cur.execute("""
                    UPDATE debug_sessions
                    SET system_under_test = %s,
                        system_alias = CASE
                            WHEN system_alias = '' OR system_alias IS NULL THEN %s
                            ELSE system_alias
                        END
                    WHERE system_under_test = %s
                """, (new_name, new_alias, old_value))
            else:
                cur.execute("""
                    UPDATE debug_sessions
                    SET system_under_test = %s
                    WHERE system_under_test = %s
                """, (new_name, old_value))

            count = cur.rowcount
            total_updated += count
            log.debug(f"  Updated {count} sessions: '{old_value}' -> '{new_name}' (alias: {new_alias})")

    conn.commit()
    return total_updated


def apply_alias_backfill(
    conn, records: List[Dict[str, str]], log: logging.Logger
) -> int:
    """Backfill system_alias for sessions where name is already canonical but alias is empty."""
    if not check_column_exists(conn, "debug_sessions", "system_alias"):
        log.warning("  system_alias column not found - skipping alias backfill")
        return 0

    total_updated = 0
    with conn.cursor() as cur:
        for record in records:
            canonical_name = record["canonical_name"]
            alias = record["alias"]

            cur.execute("""
                UPDATE debug_sessions
                SET system_alias = %s
                WHERE system_under_test = %s
                  AND (system_alias = '' OR system_alias IS NULL)
            """, (alias, canonical_name))

            count = cur.rowcount
            total_updated += count
            log.debug(f"  Backfilled {count} sessions: '{canonical_name}' alias set to '{alias}'")

    conn.commit()
    return total_updated


def apply_error_category_updates(
    conn, updates: List[Dict[str, str]], log: logging.Logger
) -> int:
    """Execute UPDATE statements for error_category normalization."""
    total_updated = 0
    with conn.cursor() as cur:
        for update in updates:
            old_value = update["old_value"]
            new_value = update["canonical"]

            cur.execute("""
                UPDATE debug_attempts
                SET error_category = %s
                WHERE error_category = %s
            """, (new_value, old_value))

            count = cur.rowcount
            total_updated += count
            log.debug(f"  Updated {count} attempts: error_category '{old_value}' -> '{new_value}'")

    conn.commit()
    return total_updated


def apply_environment_updates(
    conn, updates: List[Dict[str, str]], log: logging.Logger
) -> int:
    """Execute UPDATE statements for environment (specific name) normalization."""
    total_updated = 0
    with conn.cursor() as cur:
        for update in updates:
            old_value = update["old_value"]
            new_value = update["canonical"]

            cur.execute("""
                UPDATE debug_sessions
                SET environment = %s
                WHERE environment = %s
            """, (new_value, old_value))

            count = cur.rowcount
            total_updated += count
            log.debug(f"  Updated {count} sessions: environment '{old_value}' -> '{new_value}'")

    conn.commit()
    return total_updated


def apply_env_type_updates(
    conn, updates: List[Dict[str, str]], log: logging.Logger
) -> int:
    """Execute UPDATE statements for env_type normalization."""
    if not check_column_exists(conn, "debug_sessions", "env_type"):
        log.warning("  env_type column not found - skipping. Run migration 003 first.")
        return 0

    total_updated = 0
    with conn.cursor() as cur:
        for update in updates:
            old_value = update["old_value"]
            new_value = update["canonical"]

            cur.execute("""
                UPDATE debug_sessions
                SET env_type = %s
                WHERE env_type = %s
            """, (new_value, old_value))

            count = cur.rowcount
            total_updated += count
            log.debug(f"  Updated {count} sessions: env_type '{old_value}' -> '{new_value}'")

    conn.commit()
    return total_updated


# ---------------------------------------------------------------------------
# Graph Operations
# ---------------------------------------------------------------------------
def _esc_cypher(value: str) -> str:
    """Escape single quotes for Cypher string literals."""
    if value is None:
        return ""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def apply_graph_updates(
    conn, updates: List[Dict[str, str]], graph_name: str, log: logging.Logger
) -> Tuple[int, int]:
    """Update Project.name and Attempt.project in the Apache AGE graph.

    Returns (projects_updated, attempts_updated).
    """
    projects_updated = 0
    attempts_updated = 0

    with conn.cursor() as cur:
        # Set search path for AGE
        cur.execute("SET search_path = ag_catalog, '$user', public;")

        for update in updates:
            old_value = update["old_system"]
            new_name = update["canonical_name"]
            new_alias = update["alias"]

            # Update Project nodes
            try:
                cur.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        MATCH (p:Project {{name: '{_esc_cypher(old_value)}'}})
                        SET p.name = '{_esc_cypher(new_name)}',
                            p.alias = '{_esc_cypher(new_alias)}'
                        RETURN p
                    $$) AS (v agtype)
                """)
                count = cur.rowcount
                projects_updated += count
                if count > 0:
                    log.debug(f"  Graph: Updated {count} Project node(s): '{old_value}' -> '{new_name}'")
            except Exception as e:
                log.warning(f"  Graph: Failed to update Project '{old_value}': {e}")
                conn.rollback()
                cur.execute("SET search_path = ag_catalog, '$user', public;")
                continue

            # Update Attempt nodes that reference this project
            try:
                cur.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{project: '{_esc_cypher(old_value)}'}})
                        SET a.project = '{_esc_cypher(new_name)}'
                        RETURN a
                    $$) AS (v agtype)
                """)
                count = cur.rowcount
                attempts_updated += count
                if count > 0:
                    log.debug(f"  Graph: Updated {count} Attempt node(s) project: '{old_value}' -> '{new_name}'")
            except Exception as e:
                log.warning(f"  Graph: Failed to update Attempts for project '{old_value}': {e}")
                conn.rollback()
                cur.execute("SET search_path = ag_catalog, '$user', public;")
                continue

    conn.commit()
    return projects_updated, attempts_updated


def check_graph_available(conn, graph_name: str) -> bool:
    """Check if the Apache AGE graph exists."""
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path = ag_catalog, '$user', public;")
            cur.execute(f"""
                SELECT * FROM cypher('{graph_name}', $$
                    MATCH (n) RETURN n LIMIT 1
                $$) AS (v agtype)
            """)
        conn.rollback()
        return True
    except Exception:
        conn.rollback()
        return False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_header(log: logging.Logger, args: argparse.Namespace, db_config: Dict):
    """Print the tool header with mode and configuration info."""
    mode = "APPLY MODE" if args.apply else "DRY RUN"
    log.info("=" * 65)
    log.info(f"PerfMemory Taxonomy Normalization Tool - {mode}")
    log.info("=" * 65)
    log.info(f"Taxonomy: {args.taxonomy}")
    log.info(f"Database: {db_config['host']}:{db_config['port']}/{db_config['dbname']}")
    log.info(f"Options:  fix-error-category={args.fix_error_category}, "
             f"fix-env-type={args.fix_env_type}, "
             f"fix-environment={args.fix_environment}, "
             f"include-graph={args.include_graph}")
    log.info("")


def print_system_report(
    log: logging.Logger,
    matched: List[Dict],
    alias_backfill: List[Dict],
    already_canonical: List[Dict],
    unmatched: List[Dict],
):
    """Print the system_under_test matching results."""
    log.info("--- System Under Test: Matching Results ---")
    log.info("")

    if matched:
        log.info("MATCHED (will rename + backfill alias):")
        for m in matched:
            sessions = m["session_count"]
            s_word = "session" if sessions == 1 else "sessions"
            log.info(
                f'  "{m["old_system"]}" -> "{m["canonical_name"]}" '
                f'(alias: {m["alias"]}) [{sessions} {s_word}]'
            )
        log.info("")

    if alias_backfill:
        log.info("ALIAS BACKFILL (name is canonical, alias is empty):")
        for m in alias_backfill:
            sessions = m["session_count"]
            s_word = "session" if sessions == 1 else "sessions"
            log.info(
                f'  "{m["system_under_test"]}" -> set alias: "{m["alias"]}" [{sessions} {s_word}]'
            )
        log.info("")

    if already_canonical:
        log.info("ALREADY CANONICAL (no change needed):")
        for m in already_canonical:
            sessions = m["session_count"]
            s_word = "session" if sessions == 1 else "sessions"
            log.info(f'  "{m["system_under_test"]}" [{sessions} {s_word}]')
        log.info("")

    if unmatched:
        log.info("UNMATCHED (not in taxonomy - extend taxonomy or modify matching logic):")
        for m in unmatched:
            sessions = m["session_count"]
            s_word = "session" if sessions == 1 else "sessions"
            log.info(f'  "{m["system_under_test"]}" [{sessions} {s_word}]')
        log.info("")


def print_category_report(
    log: logging.Logger,
    category_name: str,
    matched: List[Dict],
    already_canonical: List[Dict],
    unmatched: List[Dict],
    count_label: str = "attempts",
):
    """Print matching results for error_category or environment."""
    log.info(f"--- {category_name}: Matching Results ---")
    log.info("")

    if matched:
        log.info("MATCHED (will normalize):")
        for m in matched:
            count = m["count"]
            log.info(f'  "{m["old_value"]}" -> "{m["canonical"]}" [{count} {count_label}]')
        log.info("")

    if already_canonical:
        log.info("ALREADY CANONICAL (no change needed):")
        for m in already_canonical:
            count = m["count"]
            log.info(f'  "{m["value"]}" [{count} {count_label}]')
        log.info("")

    if unmatched:
        log.info("UNMATCHED (not in taxonomy):")
        for m in unmatched:
            count = m["count"]
            log.info(f'  "{m["value"]}" [{count} {count_label}]')
        log.info("")


# ---------------------------------------------------------------------------
# Main Workflow
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    log = setup_logging()

    print_header_simple(log, args)

    # Load taxonomy (REQUIRED)
    taxonomy = load_taxonomy(args.taxonomy)
    app_count = len(taxonomy.get("applications", []))
    log.info(f"Taxonomy loaded: {app_count} application(s), "
             f"{len(taxonomy.get('error_categories', []))} error categories, "
             f"{len(taxonomy.get('environment_types', []))} environment types, "
             f"{len(taxonomy.get('environments', []))} environments")
    log.info("")

    # Load DB config
    db_config = load_db_config(args)

    # Print full header
    print_header(log, args, db_config)

    # Connect to database
    try:
        conn = get_connection(db_config)
        log.info("Database connection: OK")
    except Exception as e:
        log.error(f"Database connection FAILED: {e}")
        sys.exit(1)

    matcher = TaxonomyMatcher(taxonomy)

    # Check for taxonomy columns
    has_alias_col = check_column_exists(conn, "debug_sessions", "system_alias")
    if not has_alias_col:
        log.warning("NOTE: 'system_alias' column not found in debug_sessions.")
        log.warning("      Run migration 001_add_taxonomy_columns.sql to add taxonomy columns.")
        log.warning("      system_under_test will still be normalized, but alias backfill will be skipped.")
        log.info("")

    # --- Phase 1: System Under Test Normalization ---
    log.info("")
    log.info("--- Discovery Phase: system_under_test ---")
    systems = discover_systems(conn)
    total_sessions = sum(s["session_count"] for s in systems)
    log.info(f"Found {len(systems)} distinct system_under_test value(s) across {total_sessions} sessions")
    log.info("")

    matched = []
    alias_backfill = []
    already_canonical = []
    unmatched = []

    for system in systems:
        sut = system["system_under_test"]
        if not sut:
            unmatched.append(system)
            continue

        match = matcher.match_system(sut)
        if match is None:
            unmatched.append(system)
        elif match["canonical_name"].lower() == sut.lower().strip():
            current_alias = system.get("system_alias", "")
            expected_alias = match["alias"]
            if (not current_alias) and expected_alias:
                alias_backfill.append({
                    "system_under_test": sut,
                    "canonical_name": match["canonical_name"],
                    "alias": expected_alias,
                    "session_count": system["session_count"],
                })
            else:
                already_canonical.append(system)
        else:
            matched.append({
                "old_system": sut,
                "canonical_name": match["canonical_name"],
                "alias": match["alias"],
                "session_count": system["session_count"],
            })

    print_system_report(log, matched, alias_backfill, already_canonical, unmatched)

    sessions_to_update = sum(m["session_count"] for m in matched)
    alias_to_backfill = sum(m["session_count"] for m in alias_backfill)
    log.info(f"Summary: {sessions_to_update} session(s) to rename, "
             f"{alias_to_backfill} session(s) need alias backfill, "
             f"out of {total_sessions} total")
    log.info("")

    # --- Phase 2: Error Category (optional) ---
    error_matched = []
    error_canonical = []
    error_unmatched = []

    if args.fix_error_category:
        log.info("--- Discovery Phase: error_category ---")
        categories = discover_error_categories(conn)
        log.info(f"Found {len(categories)} distinct error_category value(s)")
        log.info("")

        for cat in categories:
            value = cat["error_category"]
            resolved = matcher.resolve_error_category(value)
            if resolved is None:
                error_unmatched.append({"value": value, "count": cat["attempt_count"]})
            elif resolved.lower() == value.lower().strip():
                error_canonical.append({"value": value, "count": cat["attempt_count"]})
            else:
                error_matched.append({
                    "old_value": value,
                    "canonical": resolved,
                    "count": cat["attempt_count"],
                })

        print_category_report(log, "Error Category", error_matched, error_canonical, error_unmatched)

    # --- Phase 3: env_type (optional) ---
    env_type_matched = []
    env_type_canonical = []
    env_type_unmatched = []

    if args.fix_env_type:
        log.info("--- Discovery Phase: env_type ---")
        env_types = discover_env_types(conn)
        log.info(f"Found {len(env_types)} distinct env_type value(s)")
        log.info("")

        for et in env_types:
            value = et["env_type"]
            resolved = matcher.resolve_env_type(value)
            if resolved is None:
                env_type_unmatched.append({"value": value, "count": et["session_count"]})
            elif resolved.lower() == value.lower().strip():
                env_type_canonical.append({"value": value, "count": et["session_count"]})
            else:
                env_type_matched.append({
                    "old_value": value,
                    "canonical": resolved,
                    "count": et["session_count"],
                })

        print_category_report(
            log, "Environment Type (env_type)", env_type_matched,
            env_type_canonical, env_type_unmatched, count_label="sessions"
        )

    # --- Phase 3b: Environment specific name (optional) ---
    env_matched = []
    env_canonical = []
    env_unmatched = []

    if args.fix_environment:
        log.info("--- Discovery Phase: environment (specific name) ---")
        environments = discover_environments(conn)
        log.info(f"Found {len(environments)} distinct environment value(s)")
        log.info("")

        for env in environments:
            value = env["environment"]
            resolved = matcher.resolve_env_name(value)
            if resolved is None:
                env_unmatched.append({"value": value, "count": env["session_count"]})
            elif resolved == value:
                env_canonical.append({"value": value, "count": env["session_count"]})
            else:
                env_matched.append({
                    "old_value": value,
                    "canonical": resolved,
                    "count": env["session_count"],
                })

        print_category_report(
            log, "Environment (specific name)", env_matched, env_canonical,
            env_unmatched, count_label="sessions"
        )

    # --- Phase 4: Apply or Dry-Run ---
    if not args.apply:
        log.info("=" * 65)
        log.info("DRY RUN COMPLETE - no changes were made.")
        log.info("Run with --apply to execute the normalization.")
        log.info("=" * 65)
        conn.close()
        return

    # Apply mode
    log.info("=" * 65)
    log.info("APPLYING CHANGES...")
    log.info("=" * 65)
    log.info("")

    # Apply system_under_test updates (rename + alias)
    if matched:
        log.info("Normalizing system_under_test...")
        count = apply_system_updates(conn, matched, log)
        log.info(f"  -> {count} session(s) updated")
        log.info("")

    # Apply alias-only backfill (name already canonical, alias empty)
    if alias_backfill:
        log.info("Backfilling system_alias for already-canonical sessions...")
        count = apply_alias_backfill(conn, alias_backfill, log)
        log.info(f"  -> {count} session(s) updated")
        log.info("")

    # Apply error_category updates
    if args.fix_error_category and error_matched:
        log.info("Normalizing error_category...")
        count = apply_error_category_updates(conn, error_matched, log)
        log.info(f"  -> {count} attempt(s) updated")
        log.info("")

    # Apply env_type updates
    if args.fix_env_type and env_type_matched:
        log.info("Normalizing env_type...")
        count = apply_env_type_updates(conn, env_type_matched, log)
        log.info(f"  -> {count} session(s) updated")
        log.info("")

    # Apply environment (specific name) updates
    if args.fix_environment and env_matched:
        log.info("Normalizing environment (specific name)...")
        count = apply_environment_updates(conn, env_matched, log)
        log.info(f"  -> {count} session(s) updated")
        log.info("")

    # Apply graph updates
    if args.include_graph and matched:
        graph_name = "perfmemory_graph"
        log.info(f"Checking Apache AGE graph '{graph_name}'...")

        if check_graph_available(conn, graph_name):
            log.info("  Graph available - normalizing Project and Attempt nodes...")
            projects, attempts = apply_graph_updates(conn, matched, graph_name, log)
            log.info(f"  -> {projects} Project node(s) updated, {attempts} Attempt node(s) updated")
        else:
            log.warning("  Graph not available or empty - skipping graph normalization")
        log.info("")

    log.info("=" * 65)
    log.info("NORMALIZATION COMPLETE")
    log.info("=" * 65)

    conn.close()


def print_header_simple(log: logging.Logger, args: argparse.Namespace):
    """Print minimal header before DB connection is established."""
    mode = "APPLY MODE" if args.apply else "DRY RUN"
    log.info("")
    log.info(f"PerfMemory Taxonomy Normalization Tool - {mode}")
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("")


if __name__ == "__main__":
    main()
