# 🛩️ PerfPilot Hub — Docker Deployment

Run the complete PerfPilot Hub MCP gateway as a single Docker container, exposing all
7 performance testing MCP servers over HTTP transport.

## 📦 What's Included

The Docker image bundles:

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.12 | MCP server runtime |
| FastMCP | v3.4.1 | MCP framework (subprocess proxy mode) |
| JMeter | 5.6.3 | Performance test execution |
| OpenJDK | 21 | JMeter runtime |
| JMeter Plugins | 7 plugins | Custom Thread Groups, Parallel Controller, HTTP/2, WebSocket, etc. |

### MCP Servers (7 Docker-eligible)

| Server | Tools |
|--------|-------|
| ⚡ BlazeMeter MCP | Test run management, artifact downloads |
| 📈 Datadog MCP | Metrics, logs, APM traces |
| 🧪 JMeter MCP | Script generation, correlation, smoke testing |
| 🔍 PerfAnalysis MCP | Bottleneck analysis, SLA validation |
| 📊 PerfReport MCP | Report generation, charts |
| 📚 Confluence MCP | Page publishing |
| 🧠 PerfMemory MCP | Lessons-learned RAG (requires PostgreSQL) |

> **Not included:** MS Teams MCP and SharePoint MCP are excluded — they require Microsoft
> Graph API OAuth flows that are incompatible with headless container operation for this version.

---

## ✅ Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS)
  or [Rancher Desktop](https://rancherdesktop.io/)
- Docker Compose v2+ (included with Docker Desktop)
- API credentials for the services you plan to use (BlazeMeter, Datadog, etc.)

---

## 🚀 Quick Start

### 1. ⚙️ Configure Environment Variables

```bash
# From the docker/ directory:
cp .env.gateway.example .env.gateway
```

Edit `.env.gateway` and fill in your API credentials. At minimum you'll want:
- BlazeMeter API key/secret (for test run data)
- Datadog API/App keys (for infrastructure metrics)
- OpenAI API key (for PerfMemory embeddings)

### 2. 🧩 Configure MCP Server Settings

Each MCP server has configuration files in `docker/config/<server>/`. Copy the
example files and customize:

```bash
# Example: configure all servers (Linux/macOS)
cd docker/config
for dir in */; do
  cd "$dir"
  for f in *.example.*; do
    cp "$f" "${f/.example/}"
  done
  cd ..
done
```

```powershell
# Example: configure all servers (Windows PowerShell)
cd docker\config
Get-ChildItem -Recurse -Filter "*.example.*" | ForEach-Object {
    $newName = $_.Name -replace '\.example', ''
    Copy-Item $_.FullName (Join-Path $_.DirectoryName $newName)
}
```

The real config files (without `.example`) are gitignored — customize freely.

### 3. 🐳 Build and Run

**Gateway standalone** (no database):

```bash
# Windows
docker compose -f docker-compose-gateway-windows.yaml up --build

# macOS
docker compose -f docker-compose-gateway-mac.yaml up --build
```

**Full stack** (gateway + PerfMemory PostgreSQL database):

```bash
# Windows
docker compose -f docker-compose-full-windows.yaml up --build

# macOS
docker compose -f docker-compose-full-mac.yaml up --build
```

### 4. 🖱️ Connect Cursor/Claude

Update your Cursor `mcp.json` to use the Docker gateway:

```jsonc
{
  "perfpilot-hub": {
    "url": "http://localhost:8000/mcp"
  }
}
```

This single entry replaces the individual stdio MCP server configurations.
You get 7 of the 9 servers (MS Teams and SharePoint are excluded from Docker).

### 5. 🩺 Verify

```bash
# Health check
curl http://localhost:8000/health
# Expected: {"status": "healthy", "server": "perfpilot-hub"}
```

---

## 🧭 Compose File Reference

| File | Use Case |
|------|----------|
| 🪟 `docker-compose-gateway-windows.yaml` | Gateway only (Windows) — connect to external DB |
| 🍎 `docker-compose-gateway-mac.yaml` | Gateway only (macOS) — connect to external DB |
| 🪟 `docker-compose-full-windows.yaml` | Gateway + PostgreSQL (Windows) — self-contained |
| 🍎 `docker-compose-full-mac.yaml` | Gateway + PostgreSQL (macOS) — self-contained |
| 🐘 `docker-compose-windows.yaml` | PostgreSQL only (existing, for local PerfMemory dev) |
| 🍎 `docker-compose-mac.yaml` | PostgreSQL only (existing, for local PerfMemory dev) |

### 🪟 Windows vs 🍎 macOS Differences

The gateway container itself is identical on both platforms. The macOS variants add
`user: "999:999"` and explicit `PGDATA` to the PostgreSQL service to avoid file
permission issues with Docker Desktop on macOS.

---

## 🗂️ Directory Structure

```
docker/
├── Dockerfile.gateway                   # Gateway image (multi-stage build)
├── entrypoint.sh                        # Container startup (JKS + JMeter props)
├── requirements.gateway.txt             # Consolidated Python dependencies
├── .env.gateway.example                 # Environment variable template
├── docker-compose-gateway-windows.yaml  # Gateway standalone (Windows)
├── docker-compose-gateway-mac.yaml      # Gateway standalone (macOS)
├── docker-compose-full-windows.yaml     # Gateway + DB (Windows)
├── docker-compose-full-mac.yaml         # Gateway + DB (macOS)
├── config/                              # MCP server config templates
│   ├── gateway/                         # Gateway config (transport, disabled servers)
│   ├── blazemeter/                      # BlazeMeter API settings
│   ├── datadog/                         # Environments, custom queries
│   ├── perfanalysis/                    # SLAs, analysis thresholds
│   ├── perfreport/                      # Report sections, chart config
│   ├── confluence/                      # Confluence connection settings
│   ├── jmeter/                          # JMeter paths, correlation config
│   └── perfmemory/                      # Embedding settings, taxonomy
├── certs/                               # Client certificates (optional)
│   ├── corporate/                       # Place CA PEM bundles here (gitignored)
│   └── jmeter/                          # Place .jks files here (gitignored)
├── data/                                # PostgreSQL data volume (auto-created)
├── Dockerfile.pgvector-age              # PostgreSQL image (existing)
├── docker-compose-windows.yaml          # DB standalone (existing)
└── docker-compose-mac.yaml              # DB standalone (existing)
```

---

## 🧪 JMeter Configuration

### 🔐 TLS Client Certificates (JKS)

For corporate environments that require client certificate authentication:

1. Place your `.jks` keystore file in `docker/certs/jmeter/`
2. Set in `.env.gateway`:
   ```
   JMETER_JKS_FILE=your-keystore.jks
   JMETER_JKS_PWD=your_keystore_password
   ```

Both variables must be set together. The entrypoint script configures JMeter's
`system.properties` with the keystore path and password at container startup.

### 🛠️ JMeter Properties

To override `jmeter.properties` settings (e.g., `CookieManager.save.cookies=true`):

**Option A — Environment variable (single property):**
```
JMETER_COOKIE_SAVE=true
```

**Option B — Properties file (multiple overrides):**
Edit `docker/config/jmeter/jmeter-overrides.properties` with any properties you need.
These are appended to `jmeter.properties` at container startup.

### 🔌 Bundled JMeter Plugins

The image includes these plugins (installed via JMeter Plugin Manager):

| Plugin ID | Description |
|-----------|-------------|
| `jpgc-casutg` | Custom Thread Groups (Ultimate, Stepping, Concurrency) |
| `bzm-parallel` | Parallel Controller |
| `bzm-http2` | HTTP/2 Sampler |
| `jpgc-functions` | Custom JMeter Functions |
| `jpgc-json` | JSON Plugins |
| `jpgc-tst` | Throughput Shaping Timer |
| `websocket-samplers` | WebSocket Samplers (Peter Doornbosch) |

To customize the plugin list at build time:

```bash
docker build -f docker/Dockerfile.gateway \
  --build-arg JMETER_PLUGINS="jpgc-casutg,bzm-parallel,bzm-http2,websocket-samplers" \
  -t perf-gateway .
```

---

## 🔐 Corporate CA / HTTPS-Intercepting Proxy

If your environment uses an HTTPS-intercepting proxy (common in corporate networks)
or security tools that re-sign TLS certificates (Norton 360, Zscaler, etc.), the
Docker build will fail when downloading JMeter, plugins, or Python packages because
the container cannot verify the proxy's certificates.

The `Dockerfile.gateway` supports an optional `ENABLE_CORP_CA` build arg that
installs your CA certificate bundle into the image at build time.

### Setup

1. **Place your CA PEM bundle** in `docker/certs/corporate/`:

   ```bash
   # Copy your corporate CA bundle (PEM format)
   cp /path/to/your/ca-bundle.pem docker/certs/corporate/ca-bundle.pem
   ```

   The `docker/certs/corporate/` directory is gitignored — your certificates
   will not be committed to the repository.

2. **Set `ENABLE_CORP_CA=true` in your `.env.gateway`:**

   ```env
   ENABLE_CORP_CA=true
   ```

3. **Rebuild:**

   ```bash
   docker compose -f docker-compose-full-mac.yaml up --build    # or your compose variant
   ```

   The docker-compose files automatically pass `ENABLE_CORP_CA` as a build arg
   to the Dockerfile — no compose file edits needed.

### What it does

When `ENABLE_CORP_CA=true` and PEM files exist in `docker/certs/corporate/`:

| Stage | Action |
|-------|--------|
| `jmeter-layer` (build) | Installs CA into the OS trust store (`update-ca-certificates`) so `wget` can download JMeter and plugins. Also imports into Java's `cacerts` keystore so the JMeter Plugin Manager CLI works. |
| `final` (runtime) | Copies the CA bundle to `/etc/ssl/certs/corporate-ca.pem` and sets `SSL_CERT_FILE` so Python/httpx can make HTTPS calls through the proxy at runtime. |

When `ENABLE_CORP_CA` is not set or `false` (the default), no CA processing occurs
and the build behaves identically to a standard environment.

### Troubleshooting

For detailed solutions to corporate proxy issues (wget SSL failures, Java PKIX
errors, Python `SSL_CERT_FILE` not found, architecture mismatches), see:

[`docs/troubleshooting/docker-gateway-macos-build-and-runtime.md`](../docs/troubleshooting/docker-gateway-macos-build-and-runtime.md)

---

## 🧯 Troubleshooting

### 🚫 Container fails to start

Check the logs:
```bash
docker compose -f docker-compose-gateway-windows.yaml logs perf-gateway
```

Common issues:
- **Missing config files** — ensure you copied all `.example` files (Step 2 above)
- **Port 8000 already in use** — stop other services on that port or change `GATEWAY_PORT`
- **Health check failing** — the gateway needs ~15 seconds to start all subprocess proxies

### 🧠 PerfMemory can't connect to database

If using the full-stack compose:
- The gateway waits for PostgreSQL to be healthy before starting (`depends_on` with health check)
- Ensure `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` are set in `.env.gateway`
- Check if the `data/pgvectordb` directory has correct permissions (macOS users: use the mac compose variant)

### 🔌 Cursor can't connect

- Verify the container is running: `docker ps | grep perf-gateway`
- Test the health endpoint: `curl http://localhost:8000/health`
- Check that your `mcp.json` uses `http://localhost:8000/mcp` (not `/health`)

### ♻️ Rebuilding after code changes

```bash
docker compose -f docker-compose-gateway-windows.yaml up --build
```

The `--build` flag forces a fresh image build, picking up any source code changes.

---

## 🔒 Security Notes

- **Starlette >= 1.2.1** is pinned in `requirements.gateway.txt` to mitigate
  [CVE-2026-48710](https://arstechnica.com/information-technology/2026/05/millions-of-ai-agents-imperiled-by-critical-vulnerability-in-open-source-package/)
  (host header injection / authentication bypass)
- API keys are passed via `.env.gateway` which is gitignored — never commit credentials
- JKS keystore passwords are passed via environment variables, not baked into the image
- Config and cert volumes are mounted read-only (`:ro`) into the container;
  the artifacts volume is read-write so MCP servers can write test results
