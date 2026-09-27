# =============================================================================
# PerfPilot Hub — Agent Frontend Docker Image
#
# Single-stage dev-mode build for the Next.js frontend (PerfPilot UI).
# Runs `npm run dev` for hot-reload during development. Deliberately
# stays on dev mode (no multi-stage production build); the runtime is a
# long-running Node.js process that self-recovers on file changes.
#
# The image runs as a non-root `app` user. Node.js handles SIGTERM
# natively for dev-mode, so no tini and no entrypoint.sh are needed —
# `npm run dev` is the CMD directly.
#
# IMPORTANT — layer ordering for Node images:
#   The `app` user is created AND `USER app` is switched to BEFORE
#   `npm ci` runs. This ensures the resulting node_modules directory is
#   already app-owned, avoiding a `chown -R app:app /app` step that
#   would otherwise re-write every one of ~100k node_modules files into
#   a new Docker layer (measured +2.83 GB overhead on this image).
#
# Build (standard):
#   docker build -f docker/agent-frontend/Dockerfile -t perf-agent-frontend .
#
# Build (corporate CA / HTTPS-intercepting proxy):
#   1. Place your CA PEM bundle(s) in docker/certs/corporate/
#   2. Set ENABLE_CORP_CA=true in docker/.env (or docker/agent-frontend/.env
#      for the standalone workflow)
#   3. Rebuild via docker-compose (the compose files pass it as a build arg)
#
# Run:
#   docker run -p 8080:8080 perf-agent-frontend
# =============================================================================

FROM node:22-slim

# System dependencies:
#   curl            — kept for backward compat / manual debugging inside container
#   ca-certificates — OS certificate store management
#   wget            — HEALTHCHECK probe (matches sibling image convention)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Corporate CA support (default: disabled). Runs as root — must happen
# before we switch to the `app` user.
ARG ENABLE_CORP_CA=false
COPY docker/certs/corporate/ /tmp/corporate-certs/

RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        echo "Corporate CA installed for runtime SSL"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi && rm -rf /tmp/corporate-certs

# Point Node.js at the combined CA bundle (OS store + corporate cert if present)
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

# ── Non-root user (create + switch BEFORE npm ci) ────────────────────
# Create the app user, then chown ONLY /app (a single directory, not
# recursive). Switching to USER app before `npm ci` means every file
# in node_modules is created app-owned from the start — no expensive
# recursive chown layer needed.
RUN groupadd --system app \
 && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown app:app /app

USER app

# ── Node.js dependencies (as app user) ───────────────────────────────
# Strictly npm ci: falling back to npm install would resolve newer
# versions than the ones this app type-checks against. `--chown=app:app`
# on COPY sets ownership at copy time (no separate chown layer).
COPY --chown=app:app agent-framework/frontend/ui/package.json \
                     agent-framework/frontend/ui/package-lock.json ./
RUN npm ci

# ── Application source (also app-owned via --chown) ──────────────────
COPY --chown=app:app agent-framework/frontend/ui/ ./

# ── Environment defaults ─────────────────────────────────────────────
# PORT and HOSTNAME are read natively by Next.js dev. Compose can still
# override PORT if the operator wants a different listen port. The
# backend URLs point at loopback by default and get overridden to
# host.docker.internal (standalone compose) or docker DNS names
# (full-stack compose) at runtime.
ENV PERFPILOT_DOCKER=true \
    PORT=8080 \
    HOSTNAME=0.0.0.0 \
    AGUI_BACKEND_URL=http://localhost:8102 \
    A2A_BACKEND_URL=http://localhost:8101

EXPOSE 8080

# Dev-mode HMR recompiles can be slow on first request — generous
# window (60s interval × 5 retries = 5-minute recovery buffer) so a
# late-compiling page never trips the probe.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=5 \
    CMD wget --spider -q http://127.0.0.1:8080/ || exit 1

CMD ["npm", "run", "dev"]
