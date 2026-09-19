# 🎭 perfpilot-mcp-playwright

Playwright MCP server — browser automation for capturing network traffic
during scripted user journeys. Produces HAR / trace files that the JMeter
MCP can then convert into `.jmx` load-test scripts.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology, port map, and full-stack deployment.

---

## 📌 Quick facts

| Property | Value |
|---|---|
| 🐳 Image | `perfpilot-mcp-playwright:latest` |
| 📦 Container | `perfpilot-mcp-playwright` |
| 🌐 Port | `8117` |
| 🔗 Endpoint | `http://localhost:8117/mcp` *(no path prefix — see §🔀)* |
| 🖼️ Base image | `mcp/playwright:latest` *(Microsoft [playwright-mcp](https://github.com/microsoft/playwright-mcp) v0.0.74)* |
| 🌐 Browser | Chromium (headless) |
| 📂 Source | Vendor image; no local Python source. See [Microsoft's repository](https://github.com/microsoft/playwright-mcp) |

---

## 🎯 Capabilities

The Playwright MCP wraps Microsoft's vendor Playwright-based MCP image and
adds PerfPilot-specific configuration for performance-testing workflows.
Core capabilities include:

- 🌐 **Browser navigation** — drive Chromium through arbitrary user journeys
- 📸 **Screenshots** — capture visual state at any point in the flow
- 🎬 **Trace recording** — full trace files with network requests, DOM snapshots, and console output
- 📄 **HAR export** — HTTP Archive files ready for consumption by the JMeter MCP
- 🔐 **Client certificate support** — auto-select test-user certs from `docker/certs/playwright/` for mTLS scenarios
- 🛡️ **Corporate proxy compatibility** — three-layer CA trust (OS store + Chromium NSS + `--ignore-https-errors`) for HTTPS-intercepting environments

For the complete tool inventory and browser automation reference, see the
[Microsoft playwright-mcp repository](https://github.com/microsoft/playwright-mcp).

---

## 🚀 Quick start (standalone)

⚠️ **Base image prerequisite.** This image layers on top of Microsoft's
`mcp/playwright:latest`, which must exist locally before the PerfPilot
Dockerfile can build. Build the base image first:

```bash
git clone https://github.com/microsoft/playwright-mcp.git
cd playwright-mcp
git checkout v0.0.74
docker build -t mcp/playwright:latest .
```

Then run this MCP standalone in three steps:

```bash
# 1. Copy the environment template (optional — mostly needed for mTLS)
cd docker/playwright-mcp/
cp .env.example .env         # Windows PowerShell: Copy-Item .env.example .env

# 2. Populate .env only if you need client-certificate authentication

# 3. Build and start the container
docker compose up --build -d
```

Verify it's healthy:

```bash
docker compose ps
#            NAME                    STATUS
# perfpilot-mcp-playwright    Up 30s
```

Test the endpoint:

```bash
curl http://localhost:8117/mcp
# A 200 / 406 / streaming response indicates the server is alive. MCP is a
# stateful protocol, so a bare curl does not perform a full handshake — use
# an MCP client (Cursor, Claude Desktop, etc.) for functional validation.
```

Shut down the container when finished:

```bash
docker compose down
```

---

## 🔀 Endpoint routing exception

Unlike every other MCP in PerfPilot Hub, the Playwright MCP endpoint is
`/mcp` — **not** `/perfpilot-mcp-playwright/mcp`. Microsoft's vendor
Playwright MCP image does not accept a URL path prefix, so it cannot be
mounted behind the gateway. Agent code must call it directly at:

```
http://perfpilot-mcp-playwright:8117/mcp      (inside Docker network)
http://localhost:8117/mcp                     (from host / standalone)
```

Because the gateway does not proxy this MCP, its capabilities are exposed
to agents via a separate `PLAYWRIGHT_MCP_URL` env var in the agent backend
configuration.

---

## 🔑 Required credentials

**None** for basic browser automation. Optional client-certificate values
are described in §🔐.

---

## ⚙️ Environment variables

All environment variables for this MCP are optional.

| Variable | Default | Purpose |
|---|---|---|
| `HTTP_PORT` | `8117` | Listen port |
| `ENABLE_CORP_CA` | `false` | Set to `true` at build time if you're behind an HTTPS-intercepting corporate proxy (see §🏢) |
| `PLAYWRIGHT_CERT_PASSPHRASE` | *(unset)* | Passphrase for `.p12` client certificate files in `docker/certs/playwright/` |
| `PLAYWRIGHT_CERT_AUTO_SELECT_CN` | *(unset)* | Common Name (CN) filter — Playwright auto-selects the matching cert when a target site presents an mTLS challenge |

**Browser configuration** is provided via CLI flags in `docker-compose.yml`
(rather than a mounted config file), so `HTTP_PORT` is the only server-level
variable. To adjust browser settings (headless mode, browser type, output
directory, etc.), edit the `command:` block in `docker/playwright-mcp/docker-compose.yml`.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../.playwright-mcp` | `/home/node/output` | HAR / trace files produced by browser sessions. The JMeter MCP reads from this same folder to convert traces into JMX scripts. |

---

## 🔐 Client-certificate authentication (mTLS)

If your target application requires TLS client certificates (`.p12` or `.pem`
format), Playwright can authenticate via test-user certs baked into the image:

1. Place your certificate files (`.p12` or `.pem`) in `docker/certs/playwright/`
2. Set two variables in `docker/playwright-mcp/.env`:

    ```env
    PLAYWRIGHT_CERT_PASSPHRASE=your_p12_passphrase
    PLAYWRIGHT_CERT_AUTO_SELECT_CN=TestUser1
    ```

3. Rebuild the image: `docker compose up --build -d`

The Dockerfile bakes the contents of `docker/certs/playwright/` into
`/home/node/certs/`. When a target site presents a client-cert challenge,
Playwright matches the CN filter against the available certificates and
uses the passphrase to unlock the selected `.p12`.

> 🔒 **Playwright client certificates are sensitive credentials.** They are
> gitignored by default (`docker/certs/playwright/*`). Never commit them to
> the repository.

---

## 🩺 Health check

The vendor image does not ship a Docker `HEALTHCHECK` directive, so
`docker compose ps` will show `Up` without a `(healthy)` marker. The
container is functional as soon as the port is bound (typically within
a few seconds). Confirm liveness with:

```bash
curl http://localhost:8117/mcp
```

A successful response (HTTP 200 / 406 / streaming) means the server is
accepting connections.

---

## 🏢 Corporate proxy / HTTPS interception

Playwright is the most sensitive component to corporate proxy behavior
because Chromium performs its own certificate validation independently of
the OS trust store. When `ENABLE_CORP_CA=true`, this image applies a
**three-layer defense**:

1. **OS trust store** (`update-ca-certificates`) — used by Node.js via `NODE_EXTRA_CA_CERTS`
2. **Chromium NSS database** (`certutil -A -t "C,," -n ...`) — used by Chromium for its native cert validation
3. **`--ignore-https-errors` CLI flag** — belt-and-suspenders fallback for edge cases

Enablement:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in `docker/playwright-mcp/.env`
3. Rebuild: `docker compose up --build -d`

See the [top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full context and additional per-image behavior.

---

## 🐛 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Build fails with `pull access denied for mcp/playwright` | Base image `mcp/playwright:latest` not built locally | Build the base image first (see [Quick start](#-quick-start-standalone)) |
| Chromium fails to load HTTPS pages with `NET::ERR_CERT_AUTHORITY_INVALID` | Corporate CA not imported into Chromium NSS database | Rebuild with `ENABLE_CORP_CA=true` — the Dockerfile handles the `certutil` import automatically |
| mTLS-protected sites reject the browser | Wrong `PLAYWRIGHT_CERT_AUTO_SELECT_CN` or `.p12` not in `docker/certs/playwright/` | Verify the CN in the certificate subject matches `PLAYWRIGHT_CERT_AUTO_SELECT_CN` exactly |
| HAR / trace files not appearing on the host | `../../.playwright-mcp` folder missing at the repo root | Create the folder: `mkdir -p ../../.playwright-mcp` |
| Endpoint returns `404` on `/perfpilot-mcp-playwright/mcp` | Wrong URL path | Playwright uses `/mcp` (no prefix) — see [§🔀 Endpoint routing exception](#-endpoint-routing-exception) |
| Container starts as root instead of `node` | Compose sets `user: "0:0"` for certutil/NSS access at startup | This is expected — Chromium drops privileges internally |

For further diagnostics, inspect the container logs:

```bash
docker compose logs -f perfpilot-mcp-playwright
```

Report bugs, request features, or contribute at the
[PerfPilot Hub repository](https://github.com/canyonlabz/perfpilot-hub/issues).

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (topology, ports, corporate CA, cert drop points)
- 🌐 [Microsoft playwright-mcp](https://github.com/microsoft/playwright-mcp) — vendor base image source
- 📂 [`docker/jmeter-mcp/README.md`](../jmeter-mcp/README.md) — downstream MCP that consumes HAR / trace output
- 📂 [`docker/certs/README.md`](../certs/README.md) — cert drop points reference
- 🌐 [Playwright documentation](https://playwright.dev/) — official Playwright framework docs
