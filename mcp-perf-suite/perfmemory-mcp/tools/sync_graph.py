"""
PerfMemory Graph Sync Tool

Reconciles Apache AGE graph nodes with current relational data in debug_sessions.
Detects mismatches between Project nodes in the graph and system_under_test /
system_alias values in the relational store, then updates graph nodes to match.

What it syncs:
  - Project.name  <->  debug_sessions.system_under_test
  - Project.alias <->  debug_sessions.system_alias
  - Attempt.project <-> debug_sessions.system_under_test (via session join)

Usage:
  python sync_graph.py                          # Dry-run — show mismatches
  python sync_graph.py --apply                  # Apply graph updates
  python sync_graph.py --project "Shopping Portal" --apply  # Target one project

Requirements:
  - perfmemory-mcp/.env must exist with valid DB credentials
  - PostgreSQL database must be running with Apache AGE extension
  - The perf_knowledge graph must exist (sql/graph/001_create_graph.sql)

No DELETE of graph nodes is supported. Only property updates on existing nodes.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psycopg2
from dotenv import dotenv_values

SCRIPT_DIR = Path(__file__).resolve().parent
PERFMEMORY_DIR = SCRIPT_DIR.parent
DEFAULT_ENV_PATH = PERFMEMORY_DIR / ".env"
LOGS_DIR = SCRIPT_DIR / "logs"

DEFAULT_GRAPH_NAME = "perf_knowledge"


def setup_logging() -> logging.Logger:
    """Configure dual logging: stdout + timestamped log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"sync_graph_{timestamp}.log"

    logger = logging.getLogger("sync_graph")
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
        description="Sync Apache AGE graph nodes with PerfMemory relational data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python sync_graph.py                                        # Dry-run all projects
  python sync_graph.py --apply                                # Apply all fixes
  python sync_graph.py --project "Shopping Portal" --apply    # Target one project
  python sync_graph.py --graph-name perf_knowledge            # Custom graph name
        """,
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute graph updates (without this flag, runs in dry-run mode)",
    )
    parser.add_argument(
        "--project",
        type=str,
        help="Target a specific project (system_under_test) for sync",
    )
    parser.add_argument(
        "--graph-name",
        type=str,
        default=DEFAULT_GRAPH_NAME,
        help=f"Apache AGE graph name (default: {DEFAULT_GRAPH_NAME})",
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


def _esc(value: str) -> str:
    """Escape single quotes for Cypher string literals."""
    if value is None:
        return ""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def init_age_session(conn):
    """Load AGE and set the search path for a connection."""
    old_autocommit = conn.autocommit
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("LOAD 'age';")
        cur.execute("SET search_path = ag_catalog, \"$user\", public;")
    conn.autocommit = old_autocommit


def check_graph_available(conn, graph_name: str) -> bool:
    """Check if the Apache AGE graph exists and is accessible."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT * FROM cypher('{graph_name}', $$
                    MATCH (n) RETURN n LIMIT 1
                $$) AS (v agtype)
            """)
        conn.rollback()
        return True
    except Exception as e:
        conn.rollback()
        print(f"  Graph check failed: {e}")
        return False


def get_relational_projects(conn, project_filter: str = None) -> List[Dict[str, str]]:
    """Get distinct project names and aliases from relational data."""
    query = """
        SELECT DISTINCT system_under_test, system_alias
        FROM debug_sessions
        WHERE system_under_test IS NOT NULL AND system_under_test != ''
    """
    params: list = []
    if project_filter:
        query += " AND system_under_test = %s"
        params.append(project_filter)
    query += " ORDER BY system_under_test"

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [{"name": row[0], "alias": row[1] or ""} for row in rows]


def get_graph_projects(conn, graph_name: str) -> List[Dict[str, str]]:
    """Get all Project nodes from the graph with their properties."""
    projects = []
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT * FROM cypher('{graph_name}', $$
                    MATCH (p:Project)
                    RETURN p.name, p.alias
                $$) AS (name agtype, alias agtype)
            """)
            rows = cur.fetchall()
            for row in rows:
                name = str(row[0]).strip('"') if row[0] else ""
                alias = str(row[1]).strip('"') if row[1] else ""
                projects.append({"name": name, "alias": alias})
    except Exception as e:
        print(f"ERROR: Failed to query graph projects: {e}")
        conn.rollback()
    return projects


def get_graph_attempt_projects(conn, graph_name: str) -> List[Dict[str, str]]:
    """Get distinct Attempt.project values from the graph."""
    attempt_projects = []
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT * FROM cypher('{graph_name}', $$
                    MATCH (a:Attempt)
                    RETURN DISTINCT a.project
                $$) AS (project agtype)
            """)
            rows = cur.fetchall()
            for row in rows:
                val = str(row[0]).strip('"') if row[0] else ""
                if val:
                    attempt_projects.append({"project": val})
    except Exception as e:
        print(f"ERROR: Failed to query graph attempts: {e}")
        conn.rollback()
    return attempt_projects


def find_mismatches(
    relational: List[Dict[str, str]],
    graph_projects: List[Dict[str, str]],
    graph_attempts: List[Dict[str, str]],
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Compare relational data with graph data and find mismatches.

    Returns:
        project_alias_fixes: Project nodes where alias needs updating
        orphan_project_names: Project nodes in graph not in relational data
        attempt_project_fixes: Attempt.project values not matching any relational project
    """
    rel_lookup = {p["name"]: p["alias"] for p in relational}
    rel_names = set(rel_lookup.keys())
    graph_lookup = {p["name"]: p["alias"] for p in graph_projects}
    graph_names = set(graph_lookup.keys())

    project_alias_fixes = []
    for name in graph_names & rel_names:
        graph_alias = graph_lookup[name]
        rel_alias = rel_lookup[name]
        if rel_alias and graph_alias != rel_alias:
            project_alias_fixes.append({
                "name": name,
                "graph_alias": graph_alias,
                "relational_alias": rel_alias,
            })

    orphan_project_names = []
    for name in graph_names - rel_names:
        orphan_project_names.append({
            "graph_name": name,
            "graph_alias": graph_lookup[name],
        })

    attempt_project_fixes = []
    for ap in graph_attempts:
        proj = ap["project"]
        if proj not in rel_names:
            orphan_match = None
            for rel_name in rel_names:
                if proj.lower() == rel_lookup.get(rel_name, "").lower():
                    orphan_match = rel_name
                    break
            attempt_project_fixes.append({
                "current_project": proj,
                "suggested_fix": orphan_match,
            })

    return project_alias_fixes, orphan_project_names, attempt_project_fixes


def apply_project_alias_fixes(
    conn, fixes: List[Dict], graph_name: str, log: logging.Logger
) -> int:
    """Update Project.alias in the graph to match relational data."""
    updated = 0
    with conn.cursor() as cur:
        for fix in fixes:
            name = fix["name"]
            new_alias = fix["relational_alias"]
            try:
                cur.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        MATCH (p:Project {{name: '{_esc(name)}'}})
                        SET p.alias = '{_esc(new_alias)}'
                        RETURN p
                    $$) AS (v agtype)
                """)
                count = cur.rowcount
                updated += count
                log.debug(f"  Updated Project '{name}' alias: '{fix['graph_alias']}' -> '{new_alias}'")
            except Exception as e:
                log.warning(f"  Failed to update Project '{name}' alias: {e}")
                conn.rollback()
                init_age_session(conn)
    conn.commit()
    return updated


def apply_attempt_project_fixes(
    conn, fixes: List[Dict], graph_name: str, log: logging.Logger
) -> int:
    """Update Attempt.project in the graph where a suggested fix exists."""
    updated = 0
    with conn.cursor() as cur:
        for fix in fixes:
            if not fix["suggested_fix"]:
                continue
            old_project = fix["current_project"]
            new_project = fix["suggested_fix"]
            try:
                cur.execute(f"""
                    SELECT * FROM cypher('{graph_name}', $$
                        MATCH (a:Attempt {{project: '{_esc(old_project)}'}})
                        SET a.project = '{_esc(new_project)}'
                        RETURN a
                    $$) AS (v agtype)
                """)
                count = cur.rowcount
                updated += count
                log.debug(f"  Updated {count} Attempt node(s): project '{old_project}' -> '{new_project}'")
            except Exception as e:
                log.warning(f"  Failed to update Attempt.project '{old_project}': {e}")
                conn.rollback()
                init_age_session(conn)
    conn.commit()
    return updated


def main():
    args = parse_args()
    log = setup_logging()

    mode = "APPLY MODE" if args.apply else "DRY RUN"
    log.info("")
    log.info(f"PerfMemory Graph Sync Tool - {mode}")
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Graph:   {args.graph_name}")
    if args.project:
        log.info(f"Project: {args.project}")
    log.info("")

    db_config = load_db_config(args)

    try:
        conn = get_connection(db_config)
        log.info("Database connection: OK")
    except Exception as e:
        log.error(f"Database connection FAILED: {e}")
        sys.exit(1)

    try:
        init_age_session(conn)
        log.info("Apache AGE extension: loaded")
    except Exception as e:
        log.error(f"Failed to load AGE extension: {e}")
        log.error("Ensure Apache AGE is installed in your PostgreSQL instance.")
        conn.close()
        sys.exit(1)

    if not check_graph_available(conn, args.graph_name):
        log.error(f"ERROR: Graph '{args.graph_name}' is not available.")
        log.error("Ensure Apache AGE is installed and the graph has been created.")
        log.error("  psql -f sql/graph/001_create_graph.sql")
        conn.close()
        sys.exit(1)

    log.info(f"Graph '{args.graph_name}': OK")
    log.info("")

    # Gather data from both sides
    log.info("--- Discovery Phase ---")
    relational = get_relational_projects(conn, args.project)
    log.info(f"Relational: {len(relational)} distinct project(s)")

    graph_projects = get_graph_projects(conn, args.graph_name)
    log.info(f"Graph:      {len(graph_projects)} Project node(s)")

    graph_attempts = get_graph_attempt_projects(conn, args.graph_name)
    log.info(f"Graph:      {len(graph_attempts)} distinct Attempt.project value(s)")
    log.info("")

    # Find mismatches
    alias_fixes, orphans, attempt_fixes = find_mismatches(
        relational, graph_projects, graph_attempts
    )

    # Report
    log.info("--- Mismatch Report ---")
    log.info("")

    if alias_fixes:
        log.info("PROJECT ALIAS MISMATCHES (graph alias != relational alias):")
        for fix in alias_fixes:
            log.info(f'  Project "{fix["name"]}": '
                     f'graph alias "{fix["graph_alias"]}" -> "{fix["relational_alias"]}"')
        log.info("")
    else:
        log.info("PROJECT ALIAS: All in sync")
        log.info("")

    if orphans:
        log.info("ORPHAN PROJECT NODES (in graph but not in relational data):")
        for o in orphans:
            log.info(f'  "{o["graph_name"]}" (alias: "{o["graph_alias"]}")')
        log.info("  NOTE: These may be from deleted/renamed sessions. Review manually.")
        log.info("")
    else:
        log.info("ORPHAN PROJECTS: None found")
        log.info("")

    fixable_attempts = [f for f in attempt_fixes if f["suggested_fix"]]
    unfixable_attempts = [f for f in attempt_fixes if not f["suggested_fix"]]

    if fixable_attempts:
        log.info("ATTEMPT PROJECT MISMATCHES (fixable — alias matches a relational project):")
        for fix in fixable_attempts:
            log.info(f'  Attempt.project "{fix["current_project"]}" -> "{fix["suggested_fix"]}"')
        log.info("")

    if unfixable_attempts:
        log.info("ATTEMPT PROJECT MISMATCHES (no automatic fix — review manually):")
        for fix in unfixable_attempts:
            log.info(f'  Attempt.project "{fix["current_project"]}" — no matching relational project')
        log.info("")

    if not alias_fixes and not fixable_attempts:
        log.info("No fixable mismatches found.")
        log.info("")

    total_fixable = len(alias_fixes) + len(fixable_attempts)
    log.info(f"Summary: {total_fixable} fixable mismatch(es), "
             f"{len(orphans)} orphan(s), "
             f"{len(unfixable_attempts)} unfixable")
    log.info("")

    # Apply or dry-run
    if not args.apply:
        log.info("=" * 55)
        log.info("DRY RUN COMPLETE - no changes were made.")
        log.info("Run with --apply to execute the sync.")
        log.info("=" * 55)
        conn.close()
        return

    if total_fixable == 0:
        log.info("Nothing to apply — graph is in sync.")
        conn.close()
        return

    log.info("=" * 55)
    log.info("APPLYING GRAPH SYNC...")
    log.info("=" * 55)
    log.info("")

    if alias_fixes:
        log.info("Updating Project aliases...")
        count = apply_project_alias_fixes(conn, alias_fixes, args.graph_name, log)
        log.info(f"  -> {count} Project node(s) updated")
        log.info("")

    if fixable_attempts:
        log.info("Updating Attempt.project values...")
        count = apply_attempt_project_fixes(conn, fixable_attempts, args.graph_name, log)
        log.info(f"  -> {count} Attempt node(s) updated")
        log.info("")

    log.info("=" * 55)
    log.info("GRAPH SYNC COMPLETE")
    log.info("=" * 55)

    conn.close()


if __name__ == "__main__":
    main()
