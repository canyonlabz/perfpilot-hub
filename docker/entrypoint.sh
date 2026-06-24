#!/bin/bash
set -e

SYSTEM_PROPS="${JMETER_HOME}/bin/system.properties"
JMETER_PROPS="${JMETER_HOME}/bin/jmeter.properties"

# =============================================================================
# JKS Keystore Configuration (corporate TLS client certificates)
# =============================================================================
if [ -n "${JMETER_JKS_FILE}" ] && [ -n "${JMETER_JKS_PWD}" ]; then
    echo "" >> "${SYSTEM_PROPS}"
    echo "# Auto-configured by PerfPilot Hub entrypoint" >> "${SYSTEM_PROPS}"
    echo "javax.net.ssl.keyStore=/app/jmeter-certs/${JMETER_JKS_FILE}" >> "${SYSTEM_PROPS}"
    echo "javax.net.ssl.keyStorePassword=${JMETER_JKS_PWD}" >> "${SYSTEM_PROPS}"
    echo "javax.net.ssl.keyStoreType=JKS" >> "${SYSTEM_PROPS}"
    echo "[entrypoint] JKS keystore configured: /app/jmeter-certs/${JMETER_JKS_FILE}"
elif [ -n "${JMETER_JKS_FILE}" ] || [ -n "${JMETER_JKS_PWD}" ]; then
    echo "[entrypoint] WARNING: Both JMETER_JKS_FILE and JMETER_JKS_PWD must be set. Skipping keystore config."
fi

# =============================================================================
# JMeter Properties Configuration
# =============================================================================
# Apply common overrides from environment variables
if [ "${JMETER_COOKIE_SAVE}" = "true" ]; then
    echo "" >> "${JMETER_PROPS}"
    echo "# Auto-configured by PerfPilot Hub entrypoint" >> "${JMETER_PROPS}"
    echo "CookieManager.save.cookies=true" >> "${JMETER_PROPS}"
    echo "[entrypoint] JMeter property set: CookieManager.save.cookies=true"
fi

# Apply additional properties from mounted override file (if exists)
if [ -f "/app/jmeter-config/jmeter-overrides.properties" ]; then
    echo "" >> "${JMETER_PROPS}"
    echo "# Appended from /app/jmeter-config/jmeter-overrides.properties" >> "${JMETER_PROPS}"
    cat "/app/jmeter-config/jmeter-overrides.properties" >> "${JMETER_PROPS}"
    echo "[entrypoint] Applied jmeter-overrides.properties"
fi

# =============================================================================
# Python/httpx TLS Configuration
# =============================================================================
if [ -f "/etc/ssl/certs/corporate-ca.pem" ]; then
    export SSL_CERT_FILE="/etc/ssl/certs/corporate-ca.pem"
    export REQUESTS_CA_BUNDLE="/etc/ssl/certs/corporate-ca.pem"
    echo "[entrypoint] Corporate CA configured for Python/httpx"
else
    unset SSL_CERT_FILE
    unset REQUESTS_CA_BUNDLE
    echo "[entrypoint] No corporate CA bundle found; using default Python trust store"
fi

# =============================================================================
# Start the Gateway
# =============================================================================
echo "[entrypoint] Starting PerfPilot Hub gateway (transport=${GATEWAY_TRANSPORT:-http})"
exec python gateway.py
