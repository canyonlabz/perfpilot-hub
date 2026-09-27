#!/bin/bash
set -e

export DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-local}"

SYSTEM_PROPS="${JMETER_HOME}/bin/system.properties"
JMETER_PROPS="${JMETER_HOME}/bin/jmeter.properties"

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

if [ "${JMETER_COOKIE_SAVE}" = "true" ]; then
    echo "" >> "${JMETER_PROPS}"
    echo "# Auto-configured by PerfPilot Hub entrypoint" >> "${JMETER_PROPS}"
    echo "CookieManager.save.cookies=true" >> "${JMETER_PROPS}"
    echo "[entrypoint] JMeter property set: CookieManager.save.cookies=true"
fi

if [ -f "/app/jmeter-config/jmeter-overrides.properties" ]; then
    echo "" >> "${JMETER_PROPS}"
    echo "# Appended from /app/jmeter-config/jmeter-overrides.properties" >> "${JMETER_PROPS}"
    cat "/app/jmeter-config/jmeter-overrides.properties" >> "${JMETER_PROPS}"
    echo "[entrypoint] Applied jmeter-overrides.properties"
fi

if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
  export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"
fi

exec python jmeter.py
