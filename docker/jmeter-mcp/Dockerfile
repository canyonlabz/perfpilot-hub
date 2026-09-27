# syntax=docker/dockerfile:1.7
# =============================================================================
# perfpilot-mcp-jmeter
#
# FastMCP Streamable HTTP + JMeter 5.6.3 + OpenJDK 21 + plugins.
# Two-stage build: (1) jmeter-layer downloads JMeter and plugins, (2) final
# image copies the JMeter tree onto a smaller Python runtime.
#
# Build from repo root:
#   docker build -f docker/jmeter-mcp/Dockerfile -t perfpilot-mcp-jmeter:local .
#
# Custom plugin set:
#   docker build -f docker/jmeter-mcp/Dockerfile \
#     --build-arg JMETER_PLUGINS="bzm-parallel,bzm-http2,jpgc-casutg" \
#     -t perfpilot-mcp-jmeter:local .
# =============================================================================

FROM python:3.12-slim AS base

ENV DEBIAN_FRONTEND=noninteractive

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

# =============================================================================
# Stage: JMeter + Java + Plugins download layer
# =============================================================================
FROM base AS jmeter-layer

ARG ENABLE_CORP_CA=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY docker/certs/corporate/ /tmp/corporate-certs/
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        JAVA_CACERTS=$(readlink -f "$(find /usr/lib/jvm /etc/ssl/certs/java -name cacerts | head -1)") && \
        echo "Java keystore: ${JAVA_CACERTS}" && \
        awk 'BEGIN {c=0} /-----BEGIN CERTIFICATE-----/{c++} {print > "/tmp/cert-" c ".pem"}' \
            /usr/local/share/ca-certificates/corporate-ca.crt && \
        for cert in /tmp/cert-*.pem; do \
            keytool -importcert -trustcacerts -keystore "${JAVA_CACERTS}" \
                -storepass changeit -noprompt -alias "corporate-$(basename "$cert" .pem)" \
                -file "$cert" || true; \
        done && \
        rm -f /tmp/cert-*.pem && \
        echo "Corporate CA installed into OS + Java trust stores"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi && rm -rf /tmp/corporate-certs

ENV JMETER_VERSION=5.6.3
ENV JMETER_HOME=/opt/jmeter/apache-jmeter-${JMETER_VERSION}
ENV PATH="${JMETER_HOME}/bin:${PATH}"

RUN mkdir -p /opt/jmeter && \
    wget -q https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz \
         -O /tmp/jmeter.tgz && \
    tar -xzf /tmp/jmeter.tgz -C /opt/jmeter && \
    rm /tmp/jmeter.tgz

RUN wget -q https://jmeter-plugins.org/get/ \
         -O ${JMETER_HOME}/lib/ext/jmeter-plugins-manager.jar && \
    wget -q https://repo1.maven.org/maven2/kg/apc/cmdrunner/2.3/cmdrunner-2.3.jar \
         -O ${JMETER_HOME}/lib/cmdrunner-2.3.jar && \
    java -cp ${JMETER_HOME}/lib/ext/jmeter-plugins-manager.jar \
         org.jmeterplugins.repository.PluginManagerCMDInstaller

ARG JMETER_PLUGINS="jpgc-casutg,bzm-parallel,bzm-http2,jpgc-functions,jpgc-json,jpgc-tst,websocket-samplers"
RUN ${JMETER_HOME}/bin/PluginsManagerCMD.sh install ${JMETER_PLUGINS}

# =============================================================================
# Final image
# =============================================================================
FROM base AS final

ARG TARGETARCH
ARG ENABLE_CORP_CA=false

COPY docker/certs/corporate/ /tmp/corporate-certs/
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        echo "Corporate CA installed for runtime SSL"; \
    else \
        echo "Corporate CA: skipped (ENABLE_CORP_CA=$ENABLE_CORP_CA)"; \
    fi && rm -rf /tmp/corporate-certs

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# JRE is installed separately in this final stage — test runs against
# TLS-intercepted endpoints need the corporate CAs in Java's own keystore too.
RUN if [ -f /usr/local/share/ca-certificates/corporate-ca.crt ]; then \
        JAVA_CACERTS=$(readlink -f "$(find /usr/lib/jvm /etc/ssl/certs/java -name cacerts | head -1)") && \
        awk 'BEGIN {c=0} /-----BEGIN CERTIFICATE-----/{c++} {print > "/tmp/cert-" c ".pem"}' \
            /usr/local/share/ca-certificates/corporate-ca.crt && \
        for cert in /tmp/cert-*.pem; do \
            keytool -importcert -trustcacerts -keystore "${JAVA_CACERTS}" \
                -storepass changeit -noprompt -alias "corporate-$(basename "$cert" .pem)" \
                -file "$cert" || true; \
        done && \
        rm -f /tmp/cert-*.pem; \
    fi

COPY --from=jmeter-layer /opt/jmeter /opt/jmeter

ENV JMETER_HOME=/opt/jmeter/apache-jmeter-5.6.3
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-${TARGETARCH}
ENV PATH="${JMETER_HOME}/bin:${JAVA_HOME}/bin:${PATH}"

COPY docker/jmeter-mcp/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp-perf-suite/jmeter-mcp/ ./jmeter-mcp/
COPY docker/jmeter-mcp/config/config.yaml ./jmeter-mcp/config.yaml
COPY docker/jmeter-mcp/config/jmeter_config.yaml ./jmeter-mcp/jmeter_config.yaml
COPY docker/jmeter-mcp/config/correlation_config.yaml ./jmeter-mcp/correlation_config.yaml
COPY docker/jmeter-mcp/test-specs/ ./jmeter-mcp/test-specs/
COPY docker/jmeter-mcp/config/jmeter-overrides.properties /app/jmeter-config/jmeter-overrides.properties
COPY docker/certs/jmeter/ /app/jmeter-certs/

COPY docker/jmeter-mcp/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /app/artifacts \
 && groupadd --system app \
 && useradd  --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && chown -R app:app /app \
 && chown app:app ${JMETER_HOME}/bin/system.properties ${JMETER_HOME}/bin/jmeter.properties

USER app

ENV PERFPILOT_DOCKER=true
ENV DEPLOYMENT_MODE=local
ENV MCP_TRANSPORT=http
ENV MCP_HTTP_PREFIX=/perfpilot-mcp-jmeter
ENV HTTP_PORT=8112
ENV OTEL_SERVICE_NAME=perfpilot-mcp-jmeter
ENV JMETER_COOKIE_SAVE=true

EXPOSE 8112

# Probe the FastMCP /health endpoint (GET, returns 200 + small JSON body).
# `-O /dev/null` forces GET instead of wget's default HEAD, so FastMCP
# does not spawn a Streamable HTTP session per probe and no 405 is logged.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD wget -q -O /dev/null "http://127.0.0.1:${HTTP_PORT}${MCP_HTTP_PREFIX}/health" || exit 1

WORKDIR /app/jmeter-mcp
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
