# =============================================================================
# perfpilot-mcp-gateway
#
# Slim Python-only aggregator. Mounts the 8 gateway-mounted MCPs over
# streamable-HTTP transport (fastmcp.server.create_proxy). No JMeter,
# no Java, no other MCP source is copied into this image.
#
# Build from repo root:
#   docker build -f docker/gateway-mcp/Dockerfile -t perfpilot-mcp-gateway:local .
# =============================================================================

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
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

COPY docker/gateway-mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-perf-suite/gateway-mcp/ ./gateway-mcp/
COPY docker/gateway-mcp/config/config.yaml ./gateway-mcp/config.yaml
COPY docker/gateway-mcp/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENV PERFPILOT_DOCKER=true
ENV DEPLOYMENT_MODE=local
ENV MCP_TRANSPORT=http
ENV MCP_HTTP_PREFIX=/perfpilot-mcp-gateway
ENV HTTP_PORT=8125
ENV OTEL_SERVICE_NAME=perfpilot-mcp-gateway

EXPOSE 8125
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${HTTP_PORT}${MCP_HTTP_PREFIX}/health || exit 1

WORKDIR /app/gateway-mcp
ENTRYPOINT ["/app/entrypoint.sh"]
