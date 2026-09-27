# =============================================================================
# PerfPilot Hub — Agent Backend Docker Image
#
# Single Dockerfile shared by two Compose services (perfpilot-a2a on 8101
# and perfpilot-agui on 8102). Only the entrypoint arguments and
# HEALTHCHECK_PORT env var differ between the two.
#
#   A2A Server:   command: ["python", "a2a_server.py"]     -> port 8101
#   AG-UI Server: command: ["python", "agui_server.py"]    -> port 8102
#
# The image runs as a non-root `app` user under tini for clean signal
# propagation. Compose `command:` arguments take precedence over the
# built-in AGENT_SERVER selection inside entrypoint.sh (see §3.9 of the
# refactor plan).
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
# Run A2A server directly:
#   docker run -p 8101:8101 perfpilot-a2a
#
# Run AG-UI server directly (compose does this by overriding command):
#   docker run -p 8102:8102 -e HEALTHCHECK_PORT=8102 perfpilot-a2a python agui_server.py
# =============================================================================

FROM python:3.12-slim

# System dependencies:
#   bash            — required by entrypoint.sh (`#!/usr/bin/env bash`)
#   tini            — used as PID 1 for clean signal forwarding to Python
#   curl            — HEALTHCHECK probe (curl -f)
#   ca-certificates — OS certificate store management
#   libpq-dev       — asyncpg/psycopg build requirement
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    tini \
    curl \
    ca-certificates \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Corporate CA support (default: disabled). Same three-layer pattern as
# the sibling FastMCP images: PEM(s) go into the OS trust store when
# ENABLE_CORP_CA=true; Python via `truststore` picks it up automatically.
ARG ENABLE_CORP_CA=false
COPY docker/certs/corporate/ /tmp/corporate-certs/

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
# Docker-scoped mirror lives under docker/agent-backend/ to match the
# per-image convention used by the FastMCP MCPs (docker/<mcp>/requirements.txt).
# Kept in sync with agent-framework/backend/requirements.txt.
COPY docker/agent-backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt truststore

# ── Application source ───────────────────────────────────────────────
COPY agent-framework/backend/ ./

# ── Entrypoint + non-root user ───────────────────────────────────────
# Compose `command:` args reach the entrypoint as $@ and win over the
# AGENT_SERVER env var branch. Both branches ultimately exec the target
# python process so tini stays PID 1.
COPY docker/agent-backend/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && groupadd --system app \
 && useradd  --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app

USER app

# ── Runtime configuration ────────────────────────────────────────────
# AGENT_SERVER picks the process when compose does NOT pass a `command:`.
# HEALTHCHECK_PORT tells the probe which port to hit (8101 for A2A,
# 8102 for AG-UI). Compose overrides both via `environment:` per service.
ARG AGENT_SERVER=a2a
ARG HEALTHCHECK_PORT=8101
ENV PERFPILOT_DOCKER=true \
    AGENT_SERVER=${AGENT_SERVER} \
    HEALTHCHECK_PORT=${HEALTHCHECK_PORT}

# Ports: A2A server (8101), AG-UI server (8102).
# Only one runs per container instance; both are exposed for flexibility.
EXPOSE 8101 8102

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f "http://127.0.0.1:${HEALTHCHECK_PORT}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
