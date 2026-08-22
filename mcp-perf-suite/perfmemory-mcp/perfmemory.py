# PerfMemory MCP Server
# Persistent memory and lessons learned layer for JMeter script debugging.
import asyncio
import atexit
import logging

from fastmcp import FastMCP, Context
from typing import Optional, Dict, Any

from services.embeddings import EmbeddingProvider
from services import session_manager as sm
from services import graph_manager as gm
from services.taxonomy import TaxonomyResolver
from utils.config import load_config

log = logging.getLogger(__name__)

mcp = FastMCP("perfmemory")

_config = load_config()
_embedder = EmbeddingProvider(_config["embedding"])
_taxonomy = TaxonomyResolver(_config.get("taxonomy", {}).get("path", ""))


def _graph_enabled() -> bool:
    return _config.get("graph", {}).get("enabled", False)


def _shutdown():
    """Release database connections and HTTP clients on exit."""
    log.info("PerfMemory shutdown — releasing resources")
    sm.close_pool()
    if _graph_enabled():
        gm.close_pool()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_embedder.close())
        else:
            loop.run_until_complete(_embedder.close())
    except RuntimeError:
        pass

atexit.register(_shutdown)


# =============================================================================
# Batch 1 — Core Tools
# =============================================================================

@mcp.tool()
async def store_debug_session(
    system_under_test: str,
    test_run_id: str,
    ctx: Context,
    script_name: Optional[str] = None,
    auth_flow_type: Optional[str] = None,
    environment: Optional[str] = None,
    created_by: Optional[str] = None,
    notes: Optional[str] = None,
    system_alias: Optional[str] = "",
    service_name: Optional[str] = "",
    auth_alias: Optional[str] = "",
    strict_taxonomy: Optional[bool] = False,
) -> Dict[str, Any]:
    """Create a new debug session record in the memory store.

    Called at the start of a debug workflow to establish the session context.
    Returns a session_id used to link subsequent debug attempts.

    The ``environment`` parameter accepts either a specific environment name
    (e.g., "QA1", "STG-East") or a canonical environment type (e.g., "qa",
    "staging"). Resolution logic:

      1. Check taxonomy.yaml[environments] — if matched, the input is treated
         as a specific environment name and env_type is auto-derived from its
         ``.type`` field.
      2. Check taxonomy.yaml[environment_types] — if matched, the input is
         treated as a canonical type. env_type is set accordingly and
         environment is left empty (no specific name provided).
      3. If neither matched, the value is stored as-is in environment with
         env_type left empty. A taxonomy warning is emitted.

    Args:
        system_under_test: The application being tested (e.g., "Online Shopping Portal").
            Maps to Project.name in the knowledge graph. Use system_alias for the
            short name.
        test_run_id: Links to the artifact structure (artifacts/{test_run_id}/)
        ctx: MCP context
        script_name: The JMX filename being debugged
        auth_flow_type: Authentication flow type (none, oauth_pkce, oauth_auth_code,
            saml, token_chain, custom_sso, entra_id, other)
        environment: Specific environment name (e.g., "QA1", "STG-East") or canonical
            type (e.g., "qa", "staging"). env_type is auto-derived when a specific
            name is provided.
        created_by: PTE name or username
        notes: Freeform session notes
        system_alias: Short name or acronym for the application (e.g., "CART", "OSP")
        service_name: Specific microservice being tested (e.g., "cart-service")
        auth_alias: Human-friendly auth flow description (e.g., "Corporate EntraID SSO")
        strict_taxonomy: If true, reject unknown taxonomy values. If false (default),
            warn but proceed with the insert.

    Returns:
        dict with keys: status, session_id, message, taxonomy_warnings (if any)
    """
    _ = ctx
    try:
        env_input = environment or ""
        resolved_environment = ""
        resolved_env_type = ""

        # Step 1: Check if input is a specific environment name
        env_entry = _taxonomy.resolve_environment(env_input)
        if env_entry:
            resolved_environment = env_entry.get("name", env_input)
            resolved_env_type = env_entry.get("type", "")
        else:
            # Step 2: Check if input is a canonical environment type
            resolved_type = _taxonomy.resolve_alias("environment_types", env_input)
            type_valid, _, _ = _taxonomy.validate("environment_types", resolved_type)
            if type_valid and resolved_type:
                resolved_env_type = resolved_type
                resolved_environment = ""
            elif env_input:
                # Step 3: Unrecognized — store as-is in environment
                resolved_environment = env_input

        resolved_auth = _taxonomy.resolve_alias("auth_flow_types", auth_flow_type or "")

        taxonomy_warnings = _taxonomy.validate_session_fields(
            system_under_test=system_under_test,
            environment=resolved_environment,
            env_type=resolved_env_type,
            auth_flow_type=resolved_auth,
            system_alias=system_alias or "",
        )

        if strict_taxonomy and taxonomy_warnings:
            return {
                "status": "ERROR",
                "message": "Taxonomy validation failed (strict_taxonomy=true)",
                "taxonomy_warnings": taxonomy_warnings,
            }

        session_id = sm.create_session(
            _config["database"],
            system_under_test=system_under_test,
            test_run_id=test_run_id,
            script_name=script_name,
            auth_flow_type=resolved_auth or auth_flow_type,
            environment=resolved_environment,
            created_by=created_by,
            notes=notes,
            system_alias=system_alias or "",
            service_name=service_name or "",
            env_type=resolved_env_type,
            auth_alias=auth_alias or "",
        )

        result: Dict[str, Any] = {
            "status": "OK",
            "session_id": session_id,
            "message": f"Debug session created (id={session_id})",
        }
        if taxonomy_warnings:
            result["taxonomy_warnings"] = taxonomy_warnings
        return result
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


@mcp.tool()
async def store_debug_attempt(
    session_id: str,
    iteration_number: int,
    symptom_text: str,
    outcome: str,
    ctx: Context,
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
    matched_attempt_id: Optional[str] = None,
    test_case_id: Optional[str] = "",
    test_case_name: Optional[str] = "",
    test_step_id: Optional[str] = "",
    test_step_name: Optional[str] = "",
) -> Dict[str, Any]:
    """Embed a symptom and store a debug attempt linked to a session.

    Called after each debug iteration to record what was tried and the outcome.
    The symptom_text is embedded using the configured embedding provider and
    stored as a vector for future similarity search.

    If matched_attempt_id is provided (fix was based on a memory match),
    the confirmed_count on that matched attempt is incremented.

    Args:
        session_id: UUID of the parent debug session
        iteration_number: Attempt number within the session (1, 2, 3...)
        symptom_text: Structured symptom description (gets embedded as vector)
        outcome: Result of this attempt (resolved, failed, environment_issue,
            test_data_issue, authentication_issue, needs_investigation)
        ctx: MCP context
        error_category: From log analyzer (HTTP 4xx Error, Authentication Error, etc.)
        severity: Error severity (Critical, High, Medium)
        response_code: HTTP status code
        hostname: Host where the error occurred
        sampler_name: The failing JMeter sampler
        api_endpoint: The failing URL/endpoint
        diagnosis: Root cause determination (plain language)
        fix_description: What fix was attempted (plain language)
        fix_type: Categorized fix (add_extractor, move_extractor, edit_request_body,
            edit_header, edit_correlation, other)
        component_type: JMeter component type (json_extractor, regex_extractor, etc.)
        manifest_excerpt: Raw debug manifest iteration text for reference
        matched_attempt_id: UUID of a previously matched attempt whose fix was applied.
            If provided, that attempt's confirmed_count is incremented.
        test_case_id: Test case identifier (e.g., "TC04")
        test_case_name: Human-readable test case name (e.g., "Submit Price Check Request")
        test_step_id: Test step identifier (e.g., "S03")
        test_step_name: Human-readable test step name (e.g., "POST to Pricing API")

    Returns:
        dict with keys: status, attempt_id, embedding_model, message.
        If matched_attempt_id provided: also confirmed_match_id, new_confirmed_count.
        If taxonomy warnings: also taxonomy_warnings.
    """
    _ = ctx
    try:
        resolved_error_cat = _taxonomy.resolve_alias("error_categories", error_category or "")
        taxonomy_warnings = _taxonomy.validate_attempt_fields(
            error_category=resolved_error_cat,
        )

        embedding = await _embedder.embed(symptom_text)
        model_name = _embedder.get_model_name()

        attempt_id = sm.create_attempt(
            _config["database"],
            session_id=session_id,
            iteration_number=iteration_number,
            symptom_text=symptom_text,
            outcome=outcome,
            embedding=embedding,
            embedding_model=model_name,
            error_category=resolved_error_cat or error_category,
            severity=severity,
            response_code=response_code,
            hostname=hostname,
            sampler_name=sampler_name,
            api_endpoint=api_endpoint,
            diagnosis=diagnosis,
            fix_description=fix_description,
            fix_type=fix_type,
            component_type=component_type,
            manifest_excerpt=manifest_excerpt,
            test_case_id=test_case_id or "",
            test_case_name=test_case_name or "",
            test_step_id=test_step_id or "",
            test_step_name=test_step_name or "",
        )

        result: Dict[str, Any] = {
            "status": "OK",
            "attempt_id": attempt_id,
            "embedding_model": model_name,
            "message": f"Debug attempt stored (id={attempt_id})",
        }

        if matched_attempt_id:
            new_count = sm.increment_confirmed(_config["database"], matched_attempt_id)
            result["confirmed_match_id"] = matched_attempt_id
            result["new_confirmed_count"] = new_count

        # Graph layer: create nodes and deterministic edges
        if _graph_enabled():
            graph_cfg = _config["graph"]
            graph_name = graph_cfg["graph_name"]

            session_data = sm.get_session(_config["database"], session_id)
            project = session_data["session"]["system_under_test"] if session_data else "unknown"
            project_alias = session_data["session"].get("system_alias", "") if session_data else ""
            session_service = session_data["session"].get("service_name", "") if session_data else ""

            graph_ok = gm.create_attempt_node(
                _config["database"],
                graph_name=graph_name,
                attempt_id=attempt_id,
                project=project,
                project_alias=project_alias,
                service_name=session_service,
                error_category=resolved_error_cat or error_category,
                fix_type=fix_type,
                outcome=outcome,
                response_code=response_code,
                component_type=component_type,
            )
            result["graph_node_created"] = graph_ok

            if graph_ok and error_category:
                edge_count = gm.create_cross_project_edges(
                    _config["database"],
                    graph_name=graph_name,
                    attempt_id=attempt_id,
                    error_category=resolved_error_cat or error_category,
                    response_code=response_code,
                    project=project,
                )
                result["graph_cross_project_edges"] = edge_count

            if graph_ok:
                similar_for_edges = sm.find_similar(
                    _config["database"],
                    embedding=embedding,
                    threshold=graph_cfg["embedding_edge_threshold"],
                    top_k=graph_cfg["max_embedding_edges"],
                )
                edge_candidates = [
                    {
                        "attempt_id": m["attempt_id"],
                        "similarity": m["similarity"],
                        "cross_project": m.get("system_under_test", "") != project,
                    }
                    for m in similar_for_edges
                    if m["attempt_id"] != attempt_id
                ]
                if edge_candidates:
                    emb_edges = gm.create_embedding_edges(
                        _config["database"],
                        graph_name=graph_name,
                        attempt_id=attempt_id,
                        similar_attempt_ids=edge_candidates,
                    )
                    result["graph_embedding_edges"] = emb_edges

        if taxonomy_warnings:
            result["taxonomy_warnings"] = taxonomy_warnings
        return result
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


@mcp.tool()
async def find_similar_attempts(
    symptom_text: str,
    ctx: Context,
    system_under_test: Optional[str] = None,
    system_alias: Optional[str] = None,
    service_name: Optional[str] = None,
    error_category: Optional[str] = None,
    top_k: Optional[int] = None,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Search the memory store for previously stored attempts similar to a symptom.

    Called before starting a debug loop to check if a similar issue has been
    encountered before. Returns matches ranked by cosine similarity with
    a recommendation on how to proceed.

    Args:
        symptom_text: The current error symptom to search for
        ctx: MCP context
        system_under_test: Filter by application name (optional, narrows search).
            Maps to Project.name in the knowledge graph.
        system_alias: Filter by app alias/short name (optional). If provided
            and taxonomy is loaded, resolves to the canonical system_under_test.
        service_name: Filter by microservice name (optional, narrows search)
        error_category: Filter by error category (optional, narrows search)
        top_k: Max number of results to return (defaults to config VECTOR_TOP_K)
        threshold: Minimum similarity score (defaults to config SIMILARITY_THRESHOLD)

    Returns:
        dict with keys: status, matches_found, matches, recommendation.
        Each match includes: attempt_id, session_id, symptom_text, diagnosis,
        fix_description, fix_type, outcome, similarity, confirmed_count, is_verified,
        system_alias, service_name, test_case_id, test_case_name, test_step_id, test_step_name.
        recommendation is one of: apply_known_fix, review_suggestions, no_match.
    """
    _ = ctx
    try:
        search_config = _config["search"]
        effective_top_k = top_k if top_k is not None else search_config["top_k"]
        effective_threshold = threshold if threshold is not None else search_config["threshold"]

        effective_system = system_under_test
        if not effective_system and system_alias:
            app = _taxonomy.resolve_application(system_alias)
            if app:
                effective_system = app.get("name", system_alias)

        resolved_error_cat = _taxonomy.resolve_alias("error_categories", error_category or "") if error_category else None

        embedding = await _embedder.embed(symptom_text)

        vector_matches = sm.find_similar(
            _config["database"],
            embedding=embedding,
            system_under_test=effective_system,
            system_alias=system_alias,
            error_category=resolved_error_cat or error_category,
            service_name=service_name,
            threshold=effective_threshold,
            top_k=effective_top_k,
        )
        for m in vector_matches:
            m["source"] = "vector"

        graph_matches = []
        if _graph_enabled() and (resolved_error_cat or error_category or effective_system):
            graph_cfg = _config["graph"]
            graph_results = gm.find_graph_related(
                _config["database"],
                graph_name=graph_cfg["graph_name"],
                error_category=resolved_error_cat or error_category,
                current_project=effective_system,
                limit=effective_top_k,
            )
            for gr in graph_results:
                graph_matches.append(gr)

        # Merge: deduplicate by attempt_id, mark "both" if found in both
        merged = {}
        vw = _config.get("graph", {}).get("vector_weight", 0.6)
        gw = _config.get("graph", {}).get("graph_weight", 0.4)

        for m in vector_matches:
            aid = m["attempt_id"]
            m["combined_score"] = round(m.get("similarity", 0) * vw, 4)
            merged[aid] = m

        for gm_match in graph_matches:
            aid = gm_match["attempt_id"]
            if aid in merged:
                merged[aid]["source"] = "both"
                merged[aid]["combined_score"] = round(
                    merged[aid].get("similarity", 0) * vw + 1.0 * gw, 4
                )
                merged[aid]["graph_path"] = gm_match.get("graph_path", "")
            else:
                gm_match["combined_score"] = round(1.0 * gw, 4)
                merged[aid] = gm_match

        matches = sorted(merged.values(), key=lambda x: x.get("combined_score", 0), reverse=True)
        matches = matches[:effective_top_k]

        if matches:
            top = matches[0]
            top_sim = top.get("similarity", top.get("combined_score", 0))
            if top_sim > 0.85 or top.get("source") == "both":
                recommendation = "apply_known_fix"
            elif top_sim > 0.60:
                recommendation = "review_suggestions"
            else:
                recommendation = "no_match"
        else:
            recommendation = "no_match"

        return {
            "status": "OK",
            "matches_found": len(matches),
            "matches": matches,
            "recommendation": recommendation,
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "matches_found": 0,
            "matches": [],
            "recommendation": "no_match",
        }


# =============================================================================
# Batch 2 — Session Management Tools
# =============================================================================

@mcp.tool()
async def close_debug_session(
    session_id: str,
    final_outcome: str,
    ctx: Context,
    resolution_attempt_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Finalize a debug session with its outcome.

    Called when debugging is complete. Sets the final_outcome, completed_at
    timestamp, total_iterations count, and optionally links the resolving attempt.

    Args:
        session_id: UUID of the debug session to close
        final_outcome: Final result (resolved, unresolved, environment_issue,
            test_data_issue, authentication_issue, iteration_limit_reached,
            needs_investigation)
        ctx: MCP context
        resolution_attempt_id: UUID of the attempt that resolved the issue
        notes: Additional notes to append to the session

    Returns:
        dict with keys: status, message, total_iterations
    """
    _ = ctx
    try:
        total_iterations = sm.close_session(
            _config["database"],
            session_id=session_id,
            final_outcome=final_outcome,
            resolution_attempt_id=resolution_attempt_id,
            notes=notes,
        )
        return {
            "status": "OK",
            "message": f"Session closed with outcome: {final_outcome}",
            "total_iterations": total_iterations,
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


@mcp.tool()
async def list_sessions(
    ctx: Context,
    system_under_test: Optional[str] = None,
    system_alias: Optional[str] = None,
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    env_type: Optional[str] = None,
    final_outcome: Optional[str] = None,
    limit: Optional[int] = 20,
) -> Dict[str, Any]:
    """Browse stored debug sessions with optional filters.

    Returns session metadata only (no attempts). Use get_session_detail
    to retrieve a full session with all its attempts.

    The ``environment`` parameter accepts either a specific environment name
    or a canonical type. Resolution logic:

      1. Check taxonomy.yaml[environments] — if matched, filter by specific
         environment name (e.g., "QA1" filters on environment = 'QA1').
      2. Check taxonomy.yaml[environment_types] — if matched, filter by
         canonical type (e.g., "qa" filters on env_type = 'qa', returning
         sessions across all QA environments).
      3. If neither matched, attempt literal match on both columns
         (environment OR env_type).

    The ``env_type`` parameter is an explicit filter on the canonical
    environment type column. Use this when you specifically want type-level
    filtering without the resolution logic above.

    Args:
        ctx: MCP context
        system_under_test: Filter by application name.
            Maps to Project.name in the knowledge graph.
        system_alias: Filter by app alias/short name (e.g., "CART", "OSP")
        service_name: Filter by microservice name
        environment: Filter by specific environment name (e.g., "QA1",
            "STG-East") or canonical type (e.g., "qa", "staging"). See
            resolution logic above.
        env_type: Filter by canonical environment type (e.g., "qa", "uat",
            "staging", "prod"). Direct column match — no resolution.
        final_outcome: Filter by outcome (resolved, unresolved, etc.)
        limit: Maximum number of sessions to return (default 20)

    Returns:
        dict with keys: status, count, sessions (list of session metadata dicts)
    """
    _ = ctx
    try:
        resolved_environment = None
        resolved_env_type = env_type

        if environment:
            # Step 1: Check if input is a specific environment name
            env_entry = _taxonomy.resolve_environment(environment)
            if env_entry:
                resolved_environment = env_entry.get("name", environment)
            else:
                # Step 2: Check if input is a canonical environment type
                resolved_type = _taxonomy.resolve_alias("environment_types", environment)
                type_valid, _, _ = _taxonomy.validate("environment_types", resolved_type)
                if type_valid and resolved_type:
                    resolved_env_type = resolved_type
                else:
                    # Step 3: Literal match — pass to both columns via environment
                    resolved_environment = environment
                    if not resolved_env_type:
                        resolved_env_type = environment

        sessions = sm.list_sessions_filtered(
            _config["database"],
            system_under_test=system_under_test,
            environment=resolved_environment,
            env_type=resolved_env_type,
            final_outcome=final_outcome,
            system_alias=system_alias,
            service_name=service_name,
            limit=limit or 20,
        )
        return {
            "status": "OK",
            "count": len(sessions),
            "sessions": sessions,
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e), "count": 0, "sessions": []}


@mcp.tool()
async def get_session_detail(
    session_id: str,
    ctx: Context,
) -> Dict[str, Any]:
    """Get a full debug session with all its attempts.

    Returns complete session metadata and all attempts ordered by
    iteration_number. Used during HITL review to inspect the full
    debug story.

    Args:
        session_id: UUID of the debug session
        ctx: MCP context

    Returns:
        dict with keys: status, session (dict), attempts (list ordered by
        iteration_number)
    """
    _ = ctx
    try:
        result = sm.get_session(_config["database"], session_id)
        if not result:
            return {
                "status": "ERROR",
                "message": f"Session not found: {session_id}",
                "session": {},
                "attempts": [],
            }
        return {
            "status": "OK",
            "session": result["session"],
            "attempts": result["attempts"],
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "session": {},
            "attempts": [],
        }


# =============================================================================
# Batch 3 — Maintenance Tools
# =============================================================================

@mcp.tool()
async def archive_attempt(
    attempt_id: str,
    ctx: Context,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Archive a debug attempt by setting is_active to FALSE.

    Used when a lesson becomes outdated (API changed, issue no longer occurs).
    The attempt remains in the database for audit trail but is excluded from
    future similarity searches.

    Args:
        attempt_id: UUID of the attempt to archive
        ctx: MCP context
        reason: Optional reason for archiving

    Returns:
        dict with keys: status, message
    """
    _ = ctx
    try:
        found = sm.archive_attempt_by_id(_config["database"], attempt_id)
        if not found:
            return {"status": "ERROR", "message": f"Attempt not found: {attempt_id}"}
        msg = f"Attempt archived (id={attempt_id})"
        if reason:
            msg += f" — reason: {reason}"
        return {"status": "OK", "message": msg}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


@mcp.tool()
async def verify_attempt(
    attempt_id: str,
    ctx: Context,
) -> Dict[str, Any]:
    """Mark a debug attempt as human-verified.

    Sets is_verified to TRUE. Used during HITL review when a human confirms
    that the lesson stored in this attempt is correct and reliable.

    Args:
        attempt_id: UUID of the attempt to verify
        ctx: MCP context

    Returns:
        dict with keys: status, message
    """
    _ = ctx
    try:
        found = sm.verify_attempt_by_id(_config["database"], attempt_id)
        if not found:
            return {"status": "ERROR", "message": f"Attempt not found: {attempt_id}"}
        return {"status": "OK", "message": f"Attempt verified (id={attempt_id})"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


@mcp.tool()
async def get_memory_stats(
    ctx: Context,
    system_under_test: Optional[str] = None,
) -> Dict[str, Any]:
    """Get overview statistics of the memory store.

    Returns counts of sessions and attempts, broken down by system,
    outcome, verification status, and active status.

    Args:
        ctx: MCP context
        system_under_test: Filter stats to a specific application (optional)

    Returns:
        dict with keys: status, total_sessions, total_attempts,
        by_system, by_outcome, verified_count, active_count
    """
    _ = ctx
    try:
        stats = sm.get_stats(_config["database"], system_under_test)
        return {"status": "OK", **stats}
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "total_sessions": 0,
            "total_attempts": 0,
        }


# =============================================================================
# Batch 4 — Graph Tools (Apache AGE)
# =============================================================================

def _get_attempt_detail(attempt_id: str) -> Optional[Dict[str, Any]]:
    """Fetch full attempt details from the relational store to enrich graph results."""
    try:
        return sm.get_attempt_by_id(_config["database"], attempt_id)
    except Exception:
        log.warning("Could not fetch attempt detail for %s", attempt_id)
        return None


@mcp.tool()
async def find_cross_project_patterns(
    error_category: str,
    ctx: Context,
    current_project: Optional[str] = None,
    response_code: Optional[str] = None,
    fix_type: Optional[str] = None,
    max_hops: Optional[int] = 2,
    limit: Optional[int] = 5,
    enrich: Optional[bool] = True,
) -> Dict[str, Any]:
    """Search the knowledge graph for cross-project patterns matching an error.

    Uses Apache AGE graph traversal to find resolved attempts from other
    projects that share the same ErrorPattern or are connected via SIMILAR_TO
    edges. This is a graph-only search — no vector similarity is used.

    Useful as a fallback when find_similar_attempts returns no matches,
    or to proactively discover fixes from other projects.

    Args:
        error_category: Error category to search for (e.g. "HTTP 4xx Error",
            "Authentication Error", "Correlation Error"). Aliases are resolved
            automatically (e.g., "auth error" → "Authentication Error").
        ctx: MCP context
        current_project: Exclude results from this project (optional, for
            cross-project discovery). Accepts application name or alias —
            resolved via taxonomy before querying the graph.
        response_code: Filter by HTTP response code (optional)
        fix_type: Filter by fix type (optional)
        max_hops: Maximum SIMILAR_TO edge hops for multi-hop discovery (default 2)
        limit: Maximum results to return (default 5)
        enrich: If true, fetch full attempt details from the relational store
            for each result (default true)

    Returns:
        dict with keys: status, matches_found, matches (list of graph results).
        Each match includes: attempt_id, project, fix_type, error_category,
        response_code, component_type, outcome, graph_path.
        If enrich=true, also includes: symptom_text, diagnosis, fix_description,
        sampler_name, api_endpoint, confirmed_count, is_verified,
        system_alias, service_name, test_case_id, test_case_name.
    """
    _ = ctx
    if not _graph_enabled():
        return {
            "status": "ERROR",
            "message": "Graph layer is disabled in config (graph.enabled = false)",
            "matches_found": 0,
            "matches": [],
        }
    try:
        resolved_error_cat = _taxonomy.resolve_alias("error_categories", error_category) or error_category

        effective_project = current_project
        if current_project:
            app = _taxonomy.resolve_application(current_project)
            if app:
                effective_project = app.get("name", current_project)

        graph_cfg = _config["graph"]
        results = gm.find_cross_project_patterns(
            _config["database"],
            graph_name=graph_cfg["graph_name"],
            error_category=resolved_error_cat,
            current_project=effective_project,
            response_code=response_code,
            fix_type=fix_type,
            max_hops=max_hops or 2,
            limit=limit or 5,
        )

        if enrich:
            for r in results:
                detail = _get_attempt_detail(r["attempt_id"])
                if detail:
                    r["symptom_text"] = detail.get("symptom_text")
                    r["diagnosis"] = detail.get("diagnosis")
                    r["fix_description"] = detail.get("fix_description")
                    r["sampler_name"] = detail.get("sampler_name")
                    r["api_endpoint"] = detail.get("api_endpoint")
                    r["confirmed_count"] = detail.get("confirmed_count")
                    r["is_verified"] = detail.get("is_verified")
                    r["system_alias"] = detail.get("system_alias", "")
                    r["service_name"] = detail.get("service_name", "")
                    r["test_case_id"] = detail.get("test_case_id", "")
                    r["test_case_name"] = detail.get("test_case_name", "")

        return {
            "status": "OK",
            "matches_found": len(results),
            "matches": results,
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e),
            "matches_found": 0,
            "matches": [],
        }


@mcp.tool()
async def get_related_issues(
    attempt_id: str,
    ctx: Context,
    max_hops: Optional[int] = 2,
    include_same_project: Optional[bool] = True,
    enrich: Optional[bool] = True,
) -> Dict[str, Any]:
    """Explore the graph neighborhood of a specific debug attempt.

    Returns the attempt's connected ErrorPattern and FixPattern nodes,
    plus neighboring attempts linked via SIMILAR_TO edges. Useful for
    understanding the structural context of a known issue and finding
    related fixes.

    Args:
        attempt_id: UUID of the debug attempt to explore
        ctx: MCP context
        max_hops: Maximum SIMILAR_TO edge hops (default 2)
        include_same_project: Include neighbors from the same project (default true)
        enrich: If true, fetch full attempt details for each neighbor (default true)

    Returns:
        dict with keys: status, attempt_id, project, error_patterns,
        fix_patterns, neighbors.
        error_patterns: list of {error_category, response_code}
        fix_patterns: list of {fix_type, component_type}
        neighbors: list of {attempt_id, project, outcome, fix_type, error_category}
        If enrich=true, neighbors also include: symptom_text, diagnosis,
        fix_description, sampler_name, api_endpoint, confirmed_count, is_verified,
        system_alias, service_name, test_case_id, test_case_name.
    """
    _ = ctx
    if not _graph_enabled():
        return {
            "status": "ERROR",
            "message": "Graph layer is disabled in config (graph.enabled = false)",
        }
    try:
        graph_cfg = _config["graph"]
        result = gm.get_related_issues(
            _config["database"],
            graph_name=graph_cfg["graph_name"],
            attempt_id=attempt_id,
            max_hops=max_hops or 2,
            include_same_project=include_same_project if include_same_project is not None else True,
        )

        if "error" in result:
            return {"status": "ERROR", "message": result["error"]}

        if enrich and result.get("neighbors"):
            for n in result["neighbors"]:
                detail = _get_attempt_detail(n["attempt_id"])
                if detail:
                    n["symptom_text"] = detail.get("symptom_text")
                    n["diagnosis"] = detail.get("diagnosis")
                    n["fix_description"] = detail.get("fix_description")
                    n["sampler_name"] = detail.get("sampler_name")
                    n["api_endpoint"] = detail.get("api_endpoint")
                    n["confirmed_count"] = detail.get("confirmed_count")
                    n["is_verified"] = detail.get("is_verified")
                    n["system_alias"] = detail.get("system_alias", "")
                    n["service_name"] = detail.get("service_name", "")
                    n["test_case_id"] = detail.get("test_case_id", "")
                    n["test_case_name"] = detail.get("test_case_name", "")

        return {"status": "OK", **result}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


# =============================================================================
# Server Startup
# =============================================================================

if __name__ == "__main__":
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        print("Shutting down PerfMemory MCP…")
