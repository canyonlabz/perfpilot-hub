#!/bin/sh
# =============================================================================
# healthcheck.sh — port-aware liveness probe for perfpilot-a2a / perfpilot-agui
#
# The agent-backend image is used by two Compose services (perfpilot-a2a and
# perfpilot-agui) that listen on different ports. Docker runs this script
# from the image's HEALTHCHECK instruction inside the container, so we can
# detect which server is actually running and probe the correct port
# automatically.
#
# Resolution order (first match wins):
#   1. HEALTHCHECK_PORT env var (explicit override — used by compose files)
#   2. Server auto-detection from /proc/1/cmdline:
#        - agui_server.py running -> AGUI_PORT (default 8102)
#        - a2a_server.py running  -> A2A_PORT  (default 8101)
#   3. Fall back to A2A default (8101)
#
# The compose files always set HEALTHCHECK_PORT explicitly, so branch 1
# takes effect in every deployed configuration. Branches 2 and 3 are a
# safety net for direct `docker run` usage where no HEALTHCHECK_PORT is
# provided.
# =============================================================================

if [ -n "$HEALTHCHECK_PORT" ]; then
    PORT="$HEALTHCHECK_PORT"
elif grep -q 'agui_server.py' /proc/1/cmdline 2>/dev/null; then
    PORT="${AGUI_PORT:-8102}"
elif grep -q 'a2a_server.py' /proc/1/cmdline 2>/dev/null; then
    PORT="${A2A_PORT:-8101}"
else
    PORT="${A2A_PORT:-8101}"
fi

exec curl -f "http://localhost:${PORT}/health"
