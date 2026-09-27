# =============================================================================
# perfpilot-mcp-perfanalysis
#
# Build from repo root:
#   docker build -f docker/perfanalysis-mcp/Dockerfile -t perfpilot-mcp-perfanalysis:local .
# =============================================================================

FROM python:3.12-slim

# Base tooling:
#   bash            required by entrypoint.sh
#   tini            PID 1 for clean signal forwarding
#   ca-certificates OS trust store management
#   wget            HEALTHCHECK probe against the /health endpoint
#   curl            retained for parity with existing tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    tini \
    ca-certificates \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG ENABLE_CORP_CA=false
COPY docker/certs/corporate/ /tmp/corporate-certs/
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        echo "Corporate CA installed for runtime SSL"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi && rm -rf /tmp/corporate-certs

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

COPY docker/perfanalysis-mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-perf-suite/perfanalysis-mcp/ ./perfanalysis-mcp/
COPY docker/perfanalysis-mcp/config/config.yaml ./perfanalysis-mcp/config.yaml
COPY docker/perfanalysis-mcp/config/slas.yaml ./perfanalysis-mcp/slas.yaml
COPY docker/datadog-mcp/config/environments.json /app/datadog-mcp/environments.json
COPY docker/perfanalysis-mcp/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && groupadd --system app \
 && useradd  --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app

USER app

ENV PERFPILOT_DOCKER=true
ENV DEPLOYMENT_MODE=local
ENV MCP_TRANSPORT=http
ENV MCP_HTTP_PREFIX=/perfpilot-mcp-perfanalysis
ENV HTTP_PORT=8113
ENV OTEL_SERVICE_NAME=perfpilot-mcp-perfanalysis

EXPOSE 8113

# Probe the FastMCP /health endpoint (GET, returns 200 + small JSON body).
# `-O /dev/null` forces GET instead of wget's default HEAD, so FastMCP
# does not spawn a Streamable HTTP session per probe and no 405 is logged.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${HTTP_PORT}${MCP_HTTP_PREFIX}/health" || exit 1

WORKDIR /app/perfanalysis-mcp
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
