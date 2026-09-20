# =============================================================================
# perfpilot-mcp-jmeter
#
# JMeter 5.6.3 + OpenJDK 21 + plugins, then Python MCP source.
# Each stage FROM python:3.12-slim (no shared base image).
#
# Build from repo root:
#   docker build -f docker/jmeter-mcp/Dockerfile -t perfpilot-mcp-jmeter:local .
#
# Custom plugins:
#   docker build -f docker/jmeter-mcp/Dockerfile \
#     --build-arg JMETER_PLUGINS="bzm-parallel,bzm-http2,jpgc-casutg" \
#     -t perfpilot-mcp-jmeter:local .
# =============================================================================

FROM python:3.12-slim AS jmeter-layer

ARG ENABLE_CORP_CA=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY docker/certs/corporate/ /tmp/corporate-certs/
RUN if [ "$ENABLE_CORP_CA" = "true" ] && ls /tmp/corporate-certs/*.pem 1>/dev/null 2>&1; then \
        cat /tmp/corporate-certs/*.pem > /usr/local/share/ca-certificates/corporate-ca.crt && \
        update-ca-certificates && \
        JAVA_CACERTS=$(find /usr/lib/jvm -name cacerts -path "*/security/*" | head -1) && \
        awk 'BEGIN {c=0} /-----BEGIN CERTIFICATE-----/{c++} {print > "/tmp/cert-" c ".pem"}' \
            /usr/local/share/ca-certificates/corporate-ca.crt && \
        for cert in /tmp/cert-*.pem; do \
            keytool -importcert -trustcacerts -keystore "$JAVA_CACERTS" \
                -storepass changeit -noprompt -alias "corporate-$(basename $cert .pem)" \
                -file "$cert" 2>/dev/null || true; \
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

FROM python:3.12-slim

ARG TARGETARCH
ARG ENABLE_CORP_CA=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

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

COPY --from=jmeter-layer /opt/jmeter /opt/jmeter
COPY --from=jmeter-layer /usr/lib/jvm /usr/lib/jvm
COPY --from=jmeter-layer /etc/java-21-openjdk /etc/java-21-openjdk

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
COPY docker/jmeter-mcp/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENV PERFPILOT_DOCKER=true
ENV DEPLOYMENT_MODE=local
ENV MCP_TRANSPORT=http
ENV MCP_HTTP_PREFIX=/perfpilot-mcp-jmeter
ENV HTTP_PORT=8112
ENV OTEL_SERVICE_NAME=perfpilot-mcp-jmeter

EXPOSE 8112
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${HTTP_PORT}${MCP_HTTP_PREFIX}/health || exit 1

WORKDIR /app/jmeter-mcp
ENTRYPOINT ["/app/entrypoint.sh"]
