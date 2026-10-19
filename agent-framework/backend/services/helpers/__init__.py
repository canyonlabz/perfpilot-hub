"""Service-layer helper modules.

Small, focused helpers that compose the primitives in ``core/`` and
``stores/`` into higher-level operations consumed by ``services/*``
and ``a2a/server/*``. Each helper is designed around one specific
concern rather than one specific agent or ingress path, so the same
helper can be reused across the A2A and Web-UI entry points.

Current siblings include: INPUT_REQUIRED message composition,
``test_run_id`` resolution, Playwright test-spec persistence, MCP
pass-through, token/context-window helpers, and the shared AG2
multi-turn tool loop.

Dependency direction (strict):

    services/helpers/* → core/*, stores/*, a2a/shared/*, a2a/server/*
                         (plus sibling helpers, hitl_gates,
                         subscriptions, and orchestrator ContextVars
                         when needed)

Never import ``services.task_executor`` or ``services.specialist_agents``.
``core/*`` and ``stores/*`` must remain importable without any
``services.*`` symbols on the module graph.
"""
