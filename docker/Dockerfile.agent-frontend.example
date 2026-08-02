# =============================================================================
# PerfPilot Hub — Agent Frontend Docker Image
#
# Single-stage dev-mode build for the Next.js frontend (PerfPilot UI).
# Runs `npm run dev` for hot-reload during development.
#
# Build (standard):
#   docker build -f docker/Dockerfile.agent-frontend -t perf-agent-frontend .
#
# Build (corporate CA / HTTPS-intercepting proxy):
#   1. Place your CA PEM bundle(s) in docker/certs/corporate/
#   2. Set ENABLE_CORP_CA=true in docker/.env.gateway
#   3. Rebuild via docker-compose (the compose files pass it as a build arg)
#
# Run:
#   docker run -p 3000:3000 perf-agent-frontend
# =============================================================================

FROM node:22-slim

# System dependencies:
#   curl — health check probe
#   ca-certificates — OS certificate store management
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Corporate CA support (default: disabled)
ARG ENABLE_CORP_CA=false

# Copy corporate certs directory (succeeds even if empty)
COPY docker/certs/corporate/ /tmp/corporate-certs/

# If ENABLE_CORP_CA=true and PEM files exist, install for Node.js runtime use
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        echo "Corporate CA installed for runtime SSL"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi && rm -rf /tmp/corporate-certs

# Point Node.js at the combined CA bundle (OS store + corporate cert if present)
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

# ── Node.js dependencies ─────────────────────────────────────────────
COPY agent-framework/frontend/ui/package.json agent-framework/frontend/ui/package-lock.json ./
RUN npm ci

# ── Application source ───────────────────────────────────────────────
COPY agent-framework/frontend/ui/ ./

# ── Environment defaults ─────────────────────────────────────────────
ENV AGUI_BACKEND_URL=http://localhost:8002
ENV A2A_BACKEND_URL=http://localhost:8001
ENV FRONTEND_PORT=3000

EXPOSE 3000

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:${FRONTEND_PORT}/ || exit 1

CMD ["npm", "run", "dev"]
