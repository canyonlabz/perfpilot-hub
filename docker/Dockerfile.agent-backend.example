# =============================================================================
# PerfPilot Hub — Agent Backend Docker Image
#
# Single-stage build for the Python backend (A2A server + AG-UI server).
# Docker-compose creates two services from this image, each
# with a different CMD:
#
#   A2A Server:  CMD ["python", "a2a_server.py"]     -> port 8001
#   AG-UI Server: CMD ["python", "agui_server.py"]   -> port 8002
#
# Build (standard):
#   docker build -f docker/Dockerfile.agent-backend -t perf-agent-backend .
#
# Build (corporate CA / HTTPS-intercepting proxy):
#   1. Place your CA PEM bundle(s) in docker/certs/corporate/
#   2. Set ENABLE_CORP_CA=true in docker/.env.gateway
#   3. Rebuild via docker-compose (the compose files pass it as a build arg)
#
# Run A2A server:
#   docker run -p 8001:8001 perf-agent-backend
#
# Run AG-UI server:
#   docker run -p 8002:8002 perf-agent-backend python agui_server.py
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

# Ports: A2A server (8001), AG-UI server (8002)
# Only one runs per container instance; both are exposed for flexibility.
EXPOSE 8001 8002

# Health check probes the A2A server by default.
# Override HEALTHCHECK_PORT for AG-UI containers.
ENV HEALTHCHECK_PORT=8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -f http://localhost:${HEALTHCHECK_PORT}/health || exit 1

# Default: A2A server. Docker-compose overrides CMD for AG-UI instances.
CMD ["python", "a2a_server.py"]
