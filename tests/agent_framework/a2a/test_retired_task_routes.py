"""Custom PerfPilot A2A task routes must no longer be registered.

Phase 1 of A2A server compliance retires
``/agents/{name}/tasks/send``, ``sendSubscribe``, poll, and cancel.
Discovery used by the Web UI catalog, plus the A2A v1 JSON-RPC and REST
bindings, stay. These tests inspect the FastAPI route table so they
run DB-free.
"""

from __future__ import annotations

from a2a_server import create_app

_RETIRED_PATHS = frozenset({
    "/agents/{agent_name}/tasks/send",
    "/agents/{agent_name}/tasks/sendSubscribe",
    "/agents/{agent_name}/tasks/{task_id}",
    "/agents/{agent_name}/tasks/{task_id}/cancel",
})

_KEPT_PATHS = frozenset({
    "/health",
    "/agents",
    "/agents/{agent_name}/.well-known/agent.json",
    "/.well-known/agent-card.json",
    "/message:send",
    "/message:stream",
    "/tasks/{task_id}",
    "/tasks/{task_id}:cancel",
    "/tasks/{task_id}:subscribe",
    "/tasks",
    "/a2a/v1",
})


def _route_paths() -> set[str]:
    app = create_app()
    return {
        route.path
        for route in app.routes
        if hasattr(route, "path")
    }


def test_retired_custom_task_routes_are_unregistered() -> None:
    """Unregistered paths 404. This suite stays DB-free by checking the
    route table instead of issuing HTTP."""
    paths = _route_paths()
    still_present = _RETIRED_PATHS & paths
    assert not still_present, (
        f"retired custom task routes still registered: {sorted(still_present)}"
    )


def test_discovery_and_a2a_v1_routes_remain() -> None:
    paths = _route_paths()
    missing = _KEPT_PATHS - paths
    assert not missing, (
        f"expected discovery/v1 routes missing: {sorted(missing)}"
    )
