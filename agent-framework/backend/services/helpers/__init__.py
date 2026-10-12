"""Service-layer helper modules.

Small, focused helpers that compose the primitives in ``core/`` and
``stores/`` into higher-level operations consumed by ``services/*``
and ``a2a/server/*``. Each helper is designed around one specific
concern (e.g. INPUT_REQUIRED message composition, ``test_run_id``
resolution) rather than one specific agent or ingress path, so the
same helper can be reused across the A2A and Web-UI entry points.

Dependency direction (strict):

    services/helpers/* → core/*, stores/*, a2a/shared/*

Never the reverse. ``core/*`` and ``stores/*`` must remain importable
without any ``services.*`` symbols on the module graph.
"""
