# =============================================================================
# PerfPilot Hub — Agent Backend Docker Image
#
# Single-stage build for the Python backend (A2A server + AG-UI server).
# Docker-compose creates two services from this image, each
# with a different CMD:
#
#   A2A Server:  CMD ["python", "a2a_server.py"]     -> port 8101
#   AG-UI Server: CMD ["python", "agui_server.py"]   -> port 8102
#
# Build (standard):
#   docker build -f docker/agent-backend/Dockerfile -t perfpilot-a2a .
#
# Build (corporate CA / HTTPS-intercepting proxy):
#   1. Place your CA PEM bundle(s) in docker/certs/corporate/
#   2. Set ENABLE_CORP_CA=true in docker/.env (or docker/agent-backend/.env
#      for the standalone workflow)
#   3. Rebuild via docker-compose (the compose files pass it as a build arg)
#
# Run A2A server:
#   docker run -p 8101:8101 perfpilot-a2a
#
# Run AG-UI server:
#   docker run -p 8102:8102 perfpilot-a2a python agui_server.py
# =============================================================================

FROM python:3.12-slim

# System dependencies:
#   curl       — health check probe
#   libpq-dev  — asyncpg (PostgreSQL async driver) build requirement
#   ca-certificates — OS certificate store management
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Corporate CA support (default: disabled)
ARG ENABLE_CORP_CA=false

# Copy corporate certs directory (succeeds even if empty)
COPY docker/certs/corporate/ /tmp/corporate-certs/

# If ENABLE_CORP_CA=true and PEM files exist, install for Python/httpx runtime use
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        echo "Corporate CA installed for runtime SSL"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi && rm -rf /tmp/corporate-certs

# Point Python/httpx at the combined CA bundle (OS store + corporate cert if present)
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# ── Python dependencies ──────────────────────────────────────────────
COPY agent-framework/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt truststore

# ── Application source ───────────────────────────────────────────────
COPY agent-framework/backend/ ./

# ── Environment defaults ─────────────────────────────────────────────
ENV PERFPILOT_DOCKER=true

# Ports: A2A server (8101), AG-UI server (8102)
# Only one runs per container instance; both are exposed for flexibility.
EXPOSE 8101 8102

# Liveness probe auto-derives its target port from the running server.
# See docker/agent-backend/healthcheck.sh for the full resolution order:
#   1. HEALTHCHECK_PORT env var (explicit override — set by compose files)
#   2. Auto-detect via /proc/1/cmdline (a2a_server.py -> A2A_PORT,
#      agui_server.py -> AGUI_PORT)
#   3. Fall back to A2A default (8101)
# Compose files pin HEALTHCHECK_PORT explicitly for predictability.
COPY docker/agent-backend/healthcheck.sh /app/healthcheck.sh
RUN sed -i 's/\r$//' /app/healthcheck.sh && chmod +x /app/healthcheck.sh

# The two `# dockerfile-utils: ignore` directives below suppress a
# false-positive "duplicate HEALTHCHECK/CMD" warning from the
# rcjsuen/dockerfile-utils VS Code linter. The parser mis-counts the
# `CMD` clause inside the inline `HEALTHCHECK ... CMD ...` form as a
# separate top-level CMD instruction, then flags both the HEALTHCHECK
# and the real CMD below as duplicates. Docker itself parses this
# correctly — this is a linter-only artifact. Per VALIDATOR.md, the
# ignore comment must appear immediately before the instruction it
# applies to.
# dockerfile-utils: ignore
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD /app/healthcheck.sh || exit 1

# Default: A2A server. Docker-compose overrides CMD for AG-UI instances.
# dockerfile-utils: ignore
CMD ["python", "a2a_server.py"]
