# Docker Gateway — macOS Build & Runtime Troubleshooting

Guide for resolving Docker build and runtime issues when building the PerfPilot Hub
gateway image on a corporate-managed macOS machine (Apple Silicon) with HTTPS-intercepting
proxy.

---

## Environment

- **Host OS:** macOS (Apple Silicon / ARM64) — enterprise-managed
- **Docker:** Docker Desktop for Mac
- **Network:** Corporate HTTPS-intercepting proxy (re-signs TLS with internal CA)
- **Image:** `perfpilot-hub-full:latest` (multi-stage build from `Dockerfile.gateway`)

---

## Issue 1: `wget` SSL Verification Failure (Exit Code 5)

### Symptom

Build fails at the JMeter plugin download step:

```
=> ERROR [perf-gateway jmeter-layer 3/4] RUN wget -q https://jmeter-plugins.org/get/ ...
target perf-gateway: failed to solve: exit code: 5
```

### Root Cause

The Docker build environment has no knowledge of the corporate CA certificate.
`wget` cannot verify HTTPS connections through the intercepting proxy.

### Solution

1. Place your CA PEM bundle in `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in your `.env.gateway`
3. Rebuild:

```bash
docker compose -f docker-compose-full-mac.yaml up --build
```

The docker-compose files pass `ENABLE_CORP_CA` as a build arg to the Dockerfile,
which conditionally copies PEM files from `docker/certs/corporate/` and installs
them into both the OS trust store and Java's cacerts keystore.

---

## Issue 2: Java PKIX Path Building Failed

### Symptom

Build fails when JMeter Plugin Manager CLI runs (Java-based tool):

```
ERROR: java.lang.RuntimeException: Failed to perform cmdline operation:
(certificate_unknown) PKIX path building failed:
sun.security.provider.certpath.SunCertPathBuilderException:
unable to find valid certification path to requested target
```

### Root Cause

Java uses its own trust store (`cacerts` keystore), separate from the OS-level CA store.
`update-ca-certificates` only updates the OS store used by `wget`/`curl` — Java is unaffected.

### Solution

Import the corporate CA bundle into Java's `cacerts` keystore after `update-ca-certificates`:

```dockerfile
RUN JAVA_CACERTS=$(find /usr/lib/jvm -name cacerts -path "*/security/*" | head -1) && \
    awk 'BEGIN {c=0} /-----BEGIN CERTIFICATE-----/{c++} {print > "/tmp/cert-" c ".pem"}' \
        /usr/local/share/ca-certificates/corporate-ca.crt && \
    for cert in /tmp/cert-*.pem; do \
        keytool -importcert -trustcacerts -keystore "$JAVA_CACERTS" \
            -storepass changeit -noprompt -alias "corporate-$(basename $cert .pem)" \
            -file "$cert" 2>/dev/null || true; \
    done && \
    rm -f /tmp/cert-*.pem
```

This splits the PEM bundle into individual certificates and imports each into Java's keystore.

---

## Issue 3: Runtime `FileNotFoundError` — `SSL_CERT_FILE`

### Symptom

Container starts then immediately crashes in a restart loop:

```
File "/usr/local/lib/python3.12/site-packages/httpx/_config.py", line 35, in create_ssl_context
    ctx = ssl.create_default_context(cafile=os.environ["SSL_CERT_FILE"])
FileNotFoundError: [Errno 2] No such file or directory
```

### Root Cause

The `SSL_CERT_FILE` environment variable in `.env.gateway` points to a **host** filesystem
path (e.g., `/Users/username/.ssl/ca-bundle.pem`) that doesn't exist inside the container.

FastMCP's startup version check uses `httpx`, which reads `SSL_CERT_FILE` to locate the
CA trust store.

### Solution

**Option A (recommended):** Set `ENABLE_CORP_CA=true` in `.env.gateway` and rebuild.
The Dockerfile automatically copies PEM files from `docker/certs/corporate/` into
the final image at `/etc/ssl/certs/corporate-ca.pem` and sets `SSL_CERT_FILE`
accordingly.

**Option B:** If you don't need the corporate CA at runtime (container bypasses proxy),
remove `SSL_CERT_FILE` from `.env.gateway` and leave `ENABLE_CORP_CA` unset or `false`.

---

## Issue 4: JKS Keystore Path Concatenation

### Symptom

Entrypoint log shows a malformed path:

```
[entrypoint] JKS keystore configured: /app/jmeter-certs//Users/username/.../keystore.jks
```

### Root Cause

`JMETER_JKS_FILE` in `.env.gateway` contains the full host path. The entrypoint script
prepends `/app/jmeter-certs/`, creating a double path.

### Solution

Set `JMETER_JKS_FILE` to just the filename (not the full path):

```env
# Wrong:
JMETER_JKS_FILE=/Users/username/Repos/.../certs/jmeter/my-keystore.jks

# Correct:
JMETER_JKS_FILE=my-keystore.jks
```

The entrypoint script automatically prepends `/app/jmeter-certs/` which maps to the
mounted volume `./certs/jmeter/` in docker-compose.

---

## Issue 5: Java Not Found — Architecture Mismatch

### Symptom

JMeter fails to start inside the container:

```json
{"status": "FAILED_TO_START", "stderr": "/usr/lib/jvm/java-21-openjdk-amd64/bin/java: not found"}
```

### Root Cause

`JAVA_HOME` was hardcoded to `java-21-openjdk-amd64` in the Dockerfile. On Apple Silicon
Macs, Docker runs ARM64 containers, and the JDK installs at `java-21-openjdk-arm64`.

### Solution

Use Docker BuildKit's `TARGETARCH` variable to set the correct architecture dynamically:

```dockerfile
# In the 'final' stage:
ARG TARGETARCH
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-${TARGETARCH}
```

`TARGETARCH` is automatically set by Docker BuildKit:
- `amd64` on x86_64 (Windows/Intel Mac)
- `arm64` on Apple Silicon

---

## Complete Fix Summary

| Issue | Layer | Fix |
|-------|-------|-----|
| wget SSL failure | `jmeter-layer` (build) | Copy CA bundle + `update-ca-certificates` |
| Java PKIX failure | `jmeter-layer` (build) | Import CA into Java `cacerts` via `keytool` |
| Python SSL_CERT_FILE | `final` (runtime) | Copy CA bundle + set `ENV SSL_CERT_FILE` in Dockerfile |
| JKS path double-concat | `.env.gateway` (config) | Use filename only, not full path |
| Java not found (arch) | `final` (runtime) | Use `ARG TARGETARCH` for dynamic `JAVA_HOME` |

---

## Verification Checklist

After applying fixes, verify the Docker deployment end-to-end:

```bash
# 1. Build
docker compose -f docker-compose-full-mac.yaml up --build

# 2. Health check
curl http://localhost:8000/health
# Expected: {"status":"healthy","server":"perfpilot-hub"}

# 3. Connect Cursor (mcp.json)
# { "perfpilot-hub-http": { "url": "http://localhost:8000/mcp" } }

# 4. Test tools (via Cursor)
# - blazemeter_get_workspaces        → External API (cloud)
# - confluence_list_spaces (cloud)   → External API with corporate SSL
# - perfmemory_list_sessions         → Internal container-to-container DB
# - jmeter_start_jmeter_test         → JMeter execution inside container
# - datadog_get_host_metrics         → External API (cloud)
```

---

## Platform Differences

These issues are **macOS-specific** (corporate-managed with intercepting proxy). The same
Dockerfile builds and runs without issues on a personal Windows 11 Pro laptop where:
- No HTTPS-intercepting proxy is present
- `SSL_CERT_FILE` is not needed
- `JAVA_HOME` with `amd64` matches the host architecture
