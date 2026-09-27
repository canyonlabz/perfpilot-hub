#!/usr/bin/env bash
# =============================================================================
# perfpilot-mcp-playwright entrypoint
#
# 1. (Optional) Imports a single client certificate into Chromium's NSS
#    databases when PLAYWRIGHT_CERT_FILE + PLAYWRIGHT_CERT_PASSPHRASE
#    are provided. Skips silently if either is unset.
#
# 2. (Optional) Writes an `AutoSelectCertificateForUrls` Chromium policy
#    so the browser picks the right client cert without prompting.
#    Only written when PLAYWRIGHT_CERT_AUTO_SELECT_CN is set.
#    Default pattern is `*` (matches any URL).
#
# 3. Launches @playwright/mcp on HTTP_PORT with Streamable HTTP transport
#    at the bare /mcp path (@playwright/mcp does not accept a path prefix).
#
# All three sections are independent — a plain HTTP scraping workload
# needs none of them; an mTLS-protected corporate app needs all three.
# =============================================================================
set -euo pipefail

HTTP_PORT="${HTTP_PORT:-8117}"
NSS_DB_LEGACY="sql:/home/node/.pki/nssdb"
NSS_DB_XDG="sql:/home/node/.local/share/pki/nssdb"
CERTS_DIR="/certs"

# Playwright 1.57+ "chromium" may be Chrome for Testing (x64) or
# Chromium (historically ARM64). Write the same policy to every known
# Linux managed-policy dir so whichever binary launches picks it up.
POLICY_DIRS=(
    "/etc/chromium/policies/managed"
    "/etc/opt/chrome/policies/managed"
    "/etc/opt/chrome_for_testing/policies/managed"
)

# ---------------------------------------------------------------------------
# Client certificate import (optional).
# ---------------------------------------------------------------------------
# PLAYWRIGHT_CERT_FILE accepts either a bare filename (looked up under
# /certs) or an absolute path. PLAYWRIGHT_CERT_PASSPHRASE is required
# to unwrap PKCS#12 (.pfx/.p12) files.
import_client_cert() {
    local cert_ref="${PLAYWRIGHT_CERT_FILE:-}"
    local passphrase="${PLAYWRIGHT_CERT_PASSPHRASE:-}"

    if [ -z "${cert_ref}" ]; then
        echo "[perfpilot-mcp-playwright] Client cert import: skipped (PLAYWRIGHT_CERT_FILE not set)"
        return 0
    fi

    if [ -z "${passphrase}" ]; then
        echo "[perfpilot-mcp-playwright] Client cert import: skipped (PLAYWRIGHT_CERT_PASSPHRASE not set)"
        return 0
    fi

    # Resolve to an absolute path.
    local cert_file
    case "${cert_ref}" in
        /*) cert_file="${cert_ref}" ;;
        *)  cert_file="${CERTS_DIR}/${cert_ref}" ;;
    esac

    if [ ! -f "${cert_file}" ]; then
        echo "[perfpilot-mcp-playwright] Client cert import: file not found at ${cert_file}"
        return 0
    fi

    local nss_db
    for nss_db in "${NSS_DB_LEGACY}" "${NSS_DB_XDG}"; do
        if pk12util -i "${cert_file}" -d "${nss_db}" -W "${passphrase}" -K "" 2>/dev/null; then
            echo "[perfpilot-mcp-playwright] Imported $(basename "${cert_file}") into ${nss_db}"
        else
            echo "[perfpilot-mcp-playwright] WARNING: failed to import $(basename "${cert_file}") into ${nss_db}"
        fi
    done
}

# ---------------------------------------------------------------------------
# Chromium AutoSelectCertificateForUrls policy (optional).
# ---------------------------------------------------------------------------
write_auto_select_policy() {
    local cert_cn="${PLAYWRIGHT_CERT_AUTO_SELECT_CN:-}"
    local cert_pattern="${PLAYWRIGHT_CERT_AUTO_SELECT_PATTERN:-*}"

    if [ -z "${cert_cn}" ]; then
        echo "[perfpilot-mcp-playwright] Cert auto-select policy: skipped (PLAYWRIGHT_CERT_AUTO_SELECT_CN not set)"
        return 0
    fi

    local policy_body
    policy_body=$(cat <<EOF
{
  "AutoSelectCertificateForUrls": [
    "{\"pattern\":\"${cert_pattern}\",\"filter\":{\"SUBJECT\":{\"CN\":\"${cert_cn}\"}}}"
  ]
}
EOF
)

    echo "[perfpilot-mcp-playwright] Cert auto-select policy: CN=${cert_cn} for ${cert_pattern}"

    local policy_dir
    for policy_dir in "${POLICY_DIRS[@]}"; do
        if [ -d "${policy_dir}" ] && [ -w "${policy_dir}" ]; then
            printf '%s\n' "${policy_body}" > "${policy_dir}/auto_select_certificate.json"
            echo "[perfpilot-mcp-playwright] Wrote policy to ${policy_dir}/auto_select_certificate.json"
        else
            echo "[perfpilot-mcp-playwright] Skipping policy dir ${policy_dir} (missing or not writable)"
        fi
    done
}

import_client_cert
write_auto_select_policy

echo "[perfpilot-mcp-playwright] starting @playwright/mcp on :${HTTP_PORT}"

exec node /app/node_modules/@playwright/mcp/cli.js \
    --headless --browser chromium --no-sandbox \
    --port "${HTTP_PORT}" --host 0.0.0.0 --allowed-hosts "*" \
    --caps devtools --ignore-https-errors \
    --config /app/config.json \
    "$@"
