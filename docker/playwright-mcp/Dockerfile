# syntax=docker/dockerfile:1.7
# =============================================================================
# perfpilot-mcp-playwright
#
# Built FROM the official Microsoft Playwright base image, which ships
# Chromium + chromium-headless-shell + ffmpeg pre-installed under
# /ms-playwright. This eliminates any build-time egress to
# cdn.playwright.dev — everything the @playwright/mcp package needs at
# runtime is already present in the base layer.
#
# What this Dockerfile adds on top of the base image:
#   - `@playwright/mcp@0.0.80` (Streamable HTTP MCP server, pinned to
#     the playwright-core 1.63.x line that matches the base image's
#     pre-installed Chromium revision exactly)
#   - `tini` for clean PID 1 signal handling
#   - `libnss3-tools` for `certutil` and `pk12util` used to seed
#     Chromium's NSS database at build time (corporate CAs) and at
#     runtime (optional mTLS client certs, via entrypoint.sh)
#   - Optional corporate CA trust — OS store + per-user NSS DBs — so
#     Chromium trusts HTTPS-intercepting proxies cleanly
#   - Optional Chromium `AutoSelectCertificateForUrls` policy dirs so
#     entrypoint.sh can wire mTLS at container start
#   - A `node` user (the base image ships `pwuser` and `ubuntu` but not
#     `node`; we create it so `/home/node/*` paths line up with the
#     entrypoint and config.json expectations)
#   - Firefox + WebKit stripped (Chromium-only project; both saved
#     ~600 MB per image pull)
#
# Corporate CA trust is opt-in via ENABLE_CORP_CA=true + PEM files in
# docker/certs/corporate/. Same pattern as every other MCP in this repo.
#
# Build (standard):
#   docker build -f docker/playwright-mcp/Dockerfile -t perfpilot-mcp-playwright:latest .
#
# Build (corporate CA / HTTPS-intercepting proxy):
#   1. Place your CA PEM bundle(s) in docker/certs/corporate/
#   2. Rebuild with `--build-arg ENABLE_CORP_CA=true` (compose passes it
#      automatically when ENABLE_CORP_CA=true is set in your .env).
# =============================================================================

FROM mcr.microsoft.com/playwright:v1.63.0-noble

LABEL org.opencontainers.image.title="perfpilot-mcp-playwright"
LABEL org.opencontainers.image.description="PerfPilot MCP - Playwright (Streamable HTTP) with optional corporate CA and mTLS client-cert support."

USER root

# PLAYWRIGHT_BROWSERS_PATH is set by the base image; redeclared here for
# clarity. PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD is a build-time guardrail so
# nothing transitively attempts to reach cdn.playwright.dev.
ENV DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /app

# Strip unused browsers. Chromium + chromium-headless-shell + ffmpeg stay.
RUN rm -rf /ms-playwright/firefox-* /ms-playwright/webkit-*

# The base image already ships bash, ca-certificates, wget, curl, and the
# libnss3 runtime. Install only what's missing:
#   - tini            used by ENTRYPOINT as PID 1
#   - libnss3-tools   provides certutil + pk12util for the NSS DB imports
#                     performed at build time here and at runtime in
#                     entrypoint.sh
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tini \
        libnss3-tools \
 && rm -rf /var/lib/apt/lists/*

# Base image ships `pwuser` (uid 1001) and `ubuntu` (uid 1000) but not
# `node`. Create it so `USER node` works and /home/node/* paths resolve.
RUN useradd --create-home --home-dir /home/node --shell /bin/bash --user-group node

# ── OS certificate store (opt-in corporate CA) ────────────────────────
# Copy the corporate certs directory (Dockerfile COPY succeeds even if
# no files match the wildcard destination, but the directory must
# exist). docker/certs/corporate/ is gitignored; commit a .gitkeep so
# COPY has something to reference.
ARG ENABLE_CORP_CA=false
COPY docker/certs/corporate/ /tmp/corporate-certs/

RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cp /tmp/corporate-certs/*.pem /usr/local/share/ca-certificates/ && \
        for f in /usr/local/share/ca-certificates/*.pem; do \
            mv "$f" "${f%.pem}.crt"; \
        done && \
        update-ca-certificates && \
        echo "Corporate CA installed into OS trust store"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi

ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    NPM_CONFIG_CAFILE=/etc/ssl/certs/ca-certificates.crt

# Playwright MCP server. No `playwright-core install-deps chromium` or
# `install --no-shell chromium` — the base image already ships Chromium
# r1243 (CFT 153.0.8010.12) which is exactly what @playwright/mcp@0.0.80's
# pinned playwright-core (1.63.0-alpha-2026-08-31) expects. Nothing here
# touches cdn.playwright.dev.
RUN npm i --omit=dev @playwright/mcp@0.0.80

# ── Chromium NSS databases (legacy + XDG) ─────────────────────────────
# Chromium on Linux reads ~/.pki/nssdb (legacy) and
# ~/.local/share/pki/nssdb (XDG), not the OS store. Bootstrap both
# databases and (if opt-in) import the corporate CAs so intercepted TLS
# chains verify without --ignore-https-errors.
RUN mkdir -p /home/node/.pki/nssdb /home/node/.local/share/pki/nssdb \
 && certutil -d sql:/home/node/.pki/nssdb -N --empty-password \
 && certutil -d sql:/home/node/.local/share/pki/nssdb -N --empty-password \
 && if [ "$ENABLE_CORP_CA" = "true" ]; then \
        for cert in /usr/local/share/ca-certificates/*.crt; do \
            [ -f "$cert" ] || continue; \
            certutil -d sql:/home/node/.pki/nssdb \
                -A -t "C,," \
                -n "corporate-$(basename "$cert" .crt)" \
                -i "$cert" || true; \
            certutil -d sql:/home/node/.local/share/pki/nssdb \
                -A -t "C,," \
                -n "corporate-$(basename "$cert" .crt)" \
                -i "$cert" || true; \
        done && \
        echo "Corporate CA imported into Chromium NSS databases"; \
    else \
        echo "Chromium NSS: no corporate certs to import"; \
    fi \
 && mkdir -p /home/node/output \
 && chown -R node:node /home/node/.pki /home/node/.local /home/node/output /app \
 && rm -rf /tmp/corporate-certs

# ── Client certificates (optional mTLS) ───────────────────────────────
# Bake .p12 / .pfx / .pem client certs from docker/certs/playwright/.
# The directory is gitignored (commit a .gitkeep so COPY succeeds).
# entrypoint.sh imports them into NSS at container start when
# PLAYWRIGHT_CERT_FILE + PLAYWRIGHT_CERT_PASSPHRASE are set.
COPY docker/certs/playwright/ /certs/

# ── Runtime config + entrypoint ───────────────────────────────────────
COPY docker/playwright-mcp/config/config.json /app/config.json
COPY docker/playwright-mcp/entrypoint.sh      /usr/local/bin/entrypoint.sh

RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p \
        /etc/chromium/policies/managed \
        /etc/opt/chrome/policies/managed \
        /etc/opt/chrome_for_testing/policies/managed \
 && chown node:node \
        /etc/chromium/policies/managed \
        /etc/opt/chrome/policies/managed \
        /etc/opt/chrome_for_testing/policies/managed \
 && chown -R node:node /certs /app/config.json

USER node

ENV HTTP_PORT=8117 \
    PERFPILOT_DOCKER=true

EXPOSE 8117

# @playwright/mcp serves Streamable HTTP at the bare /mcp path and does
# not expose a dedicated /health endpoint. `wget --spider` on /mcp
# returns HTTP 405 (Method Not Allowed) for a HEAD probe, which proves
# the socket is reachable and the MCP is serving. rc=0 (2xx) and rc=8
# (4xx/5xx) are both treated as healthy; anything else (connection
# refused, timeout) fails the probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD wget --spider -q "http://127.0.0.1:${HTTP_PORT:-8117}/mcp" 2>/dev/null; \
      rc=$?; [ $rc -eq 0 ] || [ $rc -eq 8 ]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
