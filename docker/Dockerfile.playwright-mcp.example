# =============================================================================
# PerfPilot Hub — Playwright MCP with Optional Corporate CA Certificates
#
# Layers corporate CA trust onto the locally-built Playwright MCP base image
# so Chromium can navigate sites behind an HTTPS-intercepting proxy.
#
# Three-layer defense (when ENABLE_CORP_CA=true):
#   1. OS cert store  (update-ca-certificates) — for Node.js via NODE_EXTRA_CA_CERTS
#   2. NSS database   (certutil)               — for Chromium's native cert validation
#   3. --ignore-https-errors CLI flag           — belt-and-suspenders fallback
#      (passed via command in docker-compose)
#
# Prerequisite:
#   The base image mcp/playwright:latest must exist locally.
#   Build it from the microsoft/playwright-mcp repo (pinned to v0.0.74):
#
#     cd /path/to/playwright-mcp && git checkout v0.0.74
#     docker build -t mcp/playwright:latest .
#
# Build (standard — no corporate CA):
#   docker build -f docker/Dockerfile.playwright-mcp -t playwright-mcp:latest .
#
# Build (corporate CA / HTTPS-intercepting proxy):
#   1. Place your CA PEM bundle(s) in docker/certs/corporate/
#   2. Set ENABLE_CORP_CA=true in docker/.env.gateway (or pass as build-arg)
#   3. Rebuild via docker-compose (the compose files pass it as a build arg)
# =============================================================================

FROM mcp/playwright:latest

USER root

# Corporate CA support (default: disabled)
ARG ENABLE_CORP_CA=false

# libnss3-tools provides certutil for Chromium's NSS certificate database
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3-tools \
    && rm -rf /var/lib/apt/lists/*

# Copy corporate certs directory (succeeds even if empty)
COPY docker/certs/corporate/ /tmp/corporate-certs/

# ── OS certificate store (Node.js) ───────────────────────────────────
# If ENABLE_CORP_CA=true and PEM files exist, install into OS trust store
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        echo "Corporate CA installed into OS trust store"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi

# ── Chromium NSS database ────────────────────────────────────────────
# Chromium on Linux reads certs from ~/.pki/nssdb, not the OS store.
# If ENABLE_CORP_CA=true, split the PEM bundle and import each cert.
RUN mkdir -p /home/node/.pki/nssdb && \
    certutil -d sql:/home/node/.pki/nssdb -N --empty-password && \
    if [ "$ENABLE_CORP_CA" = "true" ] && [ -f /usr/local/share/ca-certificates/corporate-ca.crt ]; then \
        awk 'BEGIN {c=0} /-----BEGIN CERTIFICATE-----/{c++} \
             c > 0 {print > "/tmp/cert-" c ".pem"}' \
             /usr/local/share/ca-certificates/corporate-ca.crt && \
        for cert in /tmp/cert-*.pem; do \
            certutil -d sql:/home/node/.pki/nssdb \
                -A -t "C,," \
                -n "corporate-$(basename $cert .pem)" \
                -i "$cert" || true; \
        done && \
        rm -f /tmp/cert-*.pem && \
        echo "Corporate CA imported into Chromium NSS database"; \
    else \
        echo "Chromium NSS: no corporate certs to import"; \
    fi && \
    chown -R node:node /home/node/.pki && \
    rm -rf /tmp/corporate-certs

# Point Node.js at the combined CA bundle (OS store + corporate cert if present)
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

USER node
