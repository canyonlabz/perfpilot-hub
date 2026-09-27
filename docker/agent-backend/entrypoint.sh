#!/usr/bin/env bash
# =============================================================================
# perfpilot-agent-backend entrypoint
#
# Shared entrypoint for the A2A and AG-UI servers, which run from the
# same image. Two branches:
#
#   1. If the container receives explicit CLI args (as it does from every
#      compose file — `command: ["python", "a2a_server.py"]` or
#      `command: ["python", "agui_server.py"]`), those args win. This is
#      the primary code path in this repo.
#
#   2. If no args are passed (e.g. `docker run perfpilot-a2a` without a
#      trailing command), the AGENT_SERVER env var selects which server
#      to launch. Default is `a2a`.
#
# Both branches `exec` the Python process so `tini` (PID 1) stays in
# place and can forward signals cleanly.
# =============================================================================
set -euo pipefail

if [[ $# -gt 0 ]]; then
  echo "[perfpilot-agent-backend] starting: $*"
  exec "$@"
fi

case "${AGENT_SERVER:-a2a}" in
  agui)
    echo "[perfpilot-agent-backend] starting AG-UI on :${HEALTHCHECK_PORT:-8102}"
    exec python agui_server.py
    ;;
  a2a|*)
    echo "[perfpilot-agent-backend] starting A2A on :${HEALTHCHECK_PORT:-8101}"
    exec python a2a_server.py
    ;;
esac
