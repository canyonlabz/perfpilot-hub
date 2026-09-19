# ⚡ perfpilot-mcp-jmeter

JMeter MCP server — generate, execute, and analyze Apache JMeter load-test
scripts (`.jmx`) from a variety of sources (HAR captures, OpenAPI specs,
Playwright browser recordings, Azure DevOps test cases).

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-jmeter:latest` |
| 📦 Container | `perfpilot-mcp-jmeter` |
| 🌐 Port | `8112` |
| 🔗 Endpoint | `http://localhost:8112/perfpilot-mcp-jmeter/mcp` |
| 🐍 Base image | `python:3.12-slim` + OpenJDK 21 + Apache JMeter 5.6.3 (multi-stage) |
| 🧩 Default plugins | `jpgc-casutg`, `bzm-parallel`, `bzm-http2`, `jpgc-functions`, `jpgc-json`, `jpgc-tst`, `websocket-samplers` |
| 📂 Source code | [`mcp-perf-suite/jmeter-mcp/`](../../mcp-perf-suite/jmeter-mcp/) |

---

## 🎯 Capabilities

The JMeter MCP exposes a broad set of tools that let an AI agent (or any MCP
client such as Cursor or Claude Desktop) drive the JMeter script lifecycle
end-to-end. Core capabilities include:

- 🌐 **HAR conversion** — turn a Chrome DevTools / Fiddler / mitmproxy HAR capture into a working JMX
- 📘 **OpenAPI / Swagger conversion** — generate a JMX from a Swagger 2.x or OpenAPI 3.x specification
- 🎬 **Playwright recording integration** — run a browser test spec via the Playwright MCP and convert the resulting network trace into a JMX
- 🧪 **Azure DevOps test case conversion** — turn ADO functional test cases into browser-automation Markdown specs
- 🔗 **Correlation extraction** — detect dynamic values across responses and generate JMeter variable extractors + references
- ▶️ **Local execution** — run JMX scripts headlessly inside the container (smoke tests, small-scale local runs)
- 🔧 **Component editing** — add, edit, or remove JMeter components (samplers, controllers, assertions, listeners) in an existing JMX
- 🔐 **mTLS support** — client-certificate authentication via JKS keystores (see §🔐)

For the complete tool inventory, see the
[`jmeter-mcp` source folder](../../mcp-perf-suite/jmeter-mcp/).

---

## 🚀 Quick start (standalone)

Run this MCP standalone (without the full stack) in three steps:

```bash
# 1. Copy the environment template (optional — all env vars have safe defaults)
cd docker/jmeter-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env only if you need JKS mTLS or want to customize plugins

# 3. Build and start the container
docker compose up --build -d
```

The first build takes ~5–10 minutes — it downloads OpenJDK 21, Apache JMeter
5.6.3, and installs the plugin set. Subsequent builds are cached.

Verify it's healthy:

```bash
docker compose ps
#            NAME                    STATUS
# perfpilot-mcp-jmeter        Up 30s (healthy)
```

Test the endpoint:

```bash
curl http://localhost:8112/perfpilot-mcp-jmeter/mcp
# A 200 / 406 / streaming response indicates the server is alive. MCP is a
# stateful protocol, so a bare curl does not perform a full handshake — use
# an MCP client (Cursor, Claude Desktop, etc.) for functional validation.
```

Shut down the container when finished:

```bash
docker compose down
```

---

## 🔑 Required credentials

**None.** The JMeter MCP has no external API dependencies — it runs Apache
JMeter locally inside the container. All `.env` variables are optional
(see §⚙️).

---

## ⚙️ Environment variables

All environment variables for this MCP are optional.

| Variable | Default | Purpose |
|---|---|---|
| `DEPLOYMENT_MODE` | `local` | `local` → human-readable console logs. `cloud` → OTel-shaped JSON to stdout (used by Aspire / Azure) |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `JMETER_PLUGINS` | See Quick facts | Comma-separated plugin list, applied at build time via `--build-arg` |
| `JMETER_JKS_FILE` | *(unset)* | Filename (not path) of a JKS keystore in `docker/certs/jmeter/` — see §🔐 |
| `JMETER_JKS_PWD` | *(unset)* | Password for the JKS keystore above |
| `JMETER_COOKIE_SAVE` | `false` | Set to `true` to enable `CookieManager.save.cookies=true` in `jmeter.properties` |

The `MCP_TRANSPORT`, `MCP_HTTP_PREFIX`, and `HTTP_PORT` values are baked into
the Dockerfile and are not intended to be overridden at runtime.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../mcp-perf-suite/artifacts` | `/app/artifacts` | JMX scripts, JTL result files, correlation specs, generated reports |
| `../../.playwright-mcp` | `/app/.playwright-mcp` | HAR / trace files produced by the Playwright MCP (input for HAR-to-JMX conversion) |

The `artifacts/` folder is shared across every PerfPilot MCP, so JMeter's
output is immediately readable by PerfAnalysis and PerfReport downstream.

---

## 🔐 Client-certificate authentication (mTLS)

If your target application requires TLS client certificates, JMeter can
authenticate using a Java KeyStore (JKS) file:

1. Place your `.jks` keystore in `docker/certs/jmeter/`
2. Set two variables in `docker/jmeter-mcp/.env`:

    ```env
    JMETER_JKS_FILE=your-keystore.jks    # filename only, not full path
    JMETER_JKS_PWD=your_keystore_password
    ```

3. Rebuild the image: `docker compose up --build -d`

The Dockerfile bakes the contents of `docker/certs/jmeter/` into the image at
`/app/jmeter-certs/`, and the entrypoint wires the specified file into
`jmeter.properties` at container startup. Both variables must be set together —
setting only one has no effect.

> 🔒 **JKS keystores are sensitive credentials.** They are gitignored by
> default (`docker/certs/jmeter/*.jks`). Never commit them to the repository.

---

## 🗂️ Baked-in configuration

The JMeter MCP ships four configuration files and one test-spec folder baked
into the image at build time (no runtime bind mounts):

| Source (in repo) | Destination (in image) | Purpose |
|---|---|---|
| `docker/jmeter-mcp/config/config.yaml` | `/app/jmeter-mcp/config.yaml` | JMeter paths, browser settings, think time |
| `docker/jmeter-mcp/config/jmeter_config.yaml` | `/app/jmeter-mcp/jmeter_config.yaml` | JMX generation settings, verbose logging |
| `docker/jmeter-mcp/config/correlation_config.yaml` | `/app/jmeter-mcp/correlation_config.yaml` | Correlation variable naming mappings |
| `docker/jmeter-mcp/config/jmeter-overrides.properties` | `/app/jmeter-config/jmeter-overrides.properties` | Overrides for the base `jmeter.properties` file |
| `docker/jmeter-mcp/test-specs/` | `/app/jmeter-mcp/test-specs/` | Sample browser-automation test specs (Markdown format) |

Edit these files in the repo and rebuild the image to take effect.

---

## 🏗️ Build architecture

This is the only PerfPilot MCP image with a **multi-stage build**:

- **Stage 1 (`jmeter-layer`)** — Debian slim + OpenJDK 21 + Apache JMeter
  5.6.3 + Plugin Manager + the plugin set specified by `JMETER_PLUGINS`.
  Corporate CA (if enabled) is imported into both the OS trust store **and**
  Java's `cacerts` keystore via `keytool`.
- **Stage 2 (final)** — Fresh `python:3.12-slim`, then `COPY --from=jmeter-layer`
  brings in `/opt/jmeter`, `/usr/lib/jvm`, and `/etc/java-21-openjdk`. Python
  MCP dependencies and source are installed on top.

`ARG TARGETARCH` is used to set `JAVA_HOME` dynamically (`amd64` on Intel/AMD,
`arm64` on Apple Silicon). No Dockerfile changes are needed to build for
either architecture.

---

## 🩺 Health check

The container ships a built-in Docker `HEALTHCHECK` that curls its own endpoint
every 30 seconds. The first probe fires after a **30-second** grace period
(longer than other MCPs because of the JVM + Python cold start).

```bash
# Overall status:
docker compose ps

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-jmeter | jq
```

---

## 🏢 Corporate proxy / HTTPS interception

If your network re-signs TLS with a corporate CA (Zscaler, Norton 360,
BlueCoat, etc.), install your CA bundle into the image:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/jmeter-mcp/.env`
3. Rebuild: `docker compose up --build -d`

Because this image bundles a JDK, the corporate CA is imported into **two**
trust stores automatically:

- The OS trust store (`update-ca-certificates`) — used by Python, `curl`, `wget`
- Java's `cacerts` keystore (via `keytool`) — used by JMeter Plugin Manager and JMX runs targeting HTTPS endpoints

See the [top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Build fails at the JMeter plugin download step with `wget: SSL verification failure` | Corporate proxy without CA installed | Follow the [Corporate proxy section](#-corporate-proxy--https-interception) above |
| JMX runs fail with `PKIX path building failed` | Corporate CA not imported into Java's `cacerts` | Rebuild with `ENABLE_CORP_CA=true` (the Dockerfile handles the `keytool` import automatically) |
| Entrypoint log shows a malformed JKS path (e.g. `/app/jmeter-certs//Users/...`) | `JMETER_JKS_FILE` contains a full host path | Set `JMETER_JKS_FILE` to the filename only (not the full path) |
| JMeter fails to start with `/usr/lib/jvm/java-21-openjdk-amd64/bin/java: not found` on Apple Silicon | `TARGETARCH` not detected by BuildKit | Ensure Docker BuildKit is enabled (default in Docker Desktop / Rancher Desktop) |
| Endpoint returns `404` | Wrong URL path | The endpoint is `/perfpilot-mcp-jmeter/mcp`, **not** just `/mcp` |
| Endpoint returns errors for the first ~30–60s after `up` | Normal — health check grace period + JVM cold start | Wait ~30–60s and try again; `docker compose ps` will show `(healthy)` when ready |
| HAR-to-JMX conversion returns "file not found" | Playwright bind mount empty or missing | Verify `../../.playwright-mcp` exists at the repo root and contains the expected HAR/trace file |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-jmeter
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, cert drop points)
- 📂 [`mcp-perf-suite/jmeter-mcp/`](../../mcp-perf-suite/jmeter-mcp/) — MCP source code + tool inventory
- 🌐 [Apache JMeter User Manual](https://jmeter.apache.org/usermanual/index.html) — official JMeter documentation
- 🧩 [JMeter Plugins Manager](https://jmeter-plugins.org/wiki/PluginsManager/) — plugin catalog and installation reference
- 📄 [`docs/troubleshooting/docker-gateway-macos-build-and-runtime.md`](../../docs/troubleshooting/docker-gateway-macos-build-and-runtime.md) — historical macOS troubleshooting (JDK, JKS, TARGETARCH root causes still apply)
