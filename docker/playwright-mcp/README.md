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
| 🖼️ Base image | `mcr.microsoft.com/playwright:v1.63.0-noble` |
| 📦 MCP package | `@playwright/mcp@0.0.80` (installed via `npm ci`) |
| 🌐 Browser | Chromium only (headless) — Firefox and WebKit stripped from `/ms-playwright/` at build time |
| 📂 Source | Microsoft [playwright-mcp](https://github.com/microsoft/playwright-mcp) via npm |

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

The image builds directly from Microsoft's official Playwright base image on
Docker Hub / GHCR — no separate base-image build is required.

Run this MCP standalone in three steps:

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
| `PLAYWRIGHT_CERT_FILE` | *(unset)* | Basename (e.g. `testuser.p12`) or absolute path of a client-certificate file to load from `/certs/`. Skipped silently when unset. |
| `PLAYWRIGHT_CERT_PASSPHRASE` | *(unset)* | Passphrase for `.p12` client-certificate files (paired with `PLAYWRIGHT_CERT_FILE`) |
| `PLAYWRIGHT_CERT_AUTO_SELECT_PATTERN` | `*` (wildcard) | Chromium `AutoSelectCertificateForUrls` policy pattern. Defaults to wildcard so the loaded cert is offered to every host that requests mTLS. Set a narrower pattern to restrict scope. |

**Browser configuration** is baked into `/app/config.json` inside the image
(transport, browser mode, output paths). Cert-related runtime overrides are
provided via the environment variables above and applied by
`docker/playwright-mcp/entrypoint.sh` at container start. To adjust the
baked-in browser config, edit `docker/playwright-mcp/config/config.json` and
rebuild.

---

## 📂 Bind mounts

| Host path | Container path | Purpose |
|---|---|---|
| `../../.playwright-mcp` | `/home/node/output` | HAR / trace files produced by browser sessions. The JMeter MCP reads from this same folder to convert traces into JMX scripts. |

---

## 🔐 Client-certificate authentication (mTLS)

If your target application requires TLS client certificates (`.p12` or `.pem`
format), Playwright can authenticate via a certificate loaded from
`docker/certs/playwright/`:

1. Place your certificate file (`.p12` or `.pem`) in `docker/certs/playwright/`
2. Set the two variables in `docker/playwright-mcp/.env`:

    ```env
    PLAYWRIGHT_CERT_FILE=testuser.p12
    PLAYWRIGHT_CERT_PASSPHRASE=your_p12_passphrase
    ```

    `PLAYWRIGHT_CERT_FILE` accepts either a basename (looked up in `/certs/`
    inside the container) or an absolute path.

3. Optionally narrow the auto-select scope with
   `PLAYWRIGHT_CERT_AUTO_SELECT_PATTERN` (default is wildcard `*`).

4. Rebuild the image: `docker compose up --build -d`

At container start, `entrypoint.sh` copies the certificate into Chromium's
NSS database (both `~/.pki/nssdb` and `~/.local/share/pki/nssdb`) and writes
an `AutoSelectCertificateForUrls` Chromium policy pointing at the imported
cert. Chromium presents the cert automatically when a target site issues an
mTLS challenge — no per-navigation code required.

> 🔒 **Playwright client certificates are sensitive credentials.** They are
> gitignored by default (`docker/certs/playwright/*`). Never commit them to
> the repository.

---

## 🩺 Health check

The image ships a Docker `HEALTHCHECK` directive that probes `/mcp` every
30 seconds via `wget --spider`. MCP is a stateful protocol, so a bare GET
returns HTTP 406 — the healthcheck accepts both 200 and 406 as healthy.

```bash
# Overall status:
docker compose ps
#            NAME                    STATUS
# perfpilot-mcp-playwright    Up 30s (healthy)

# Detailed probe history:
docker inspect --format='{{json .State.Health}}' perfpilot-mcp-playwright | jq
```

For interactive validation with a real MCP client (Cursor, Claude Desktop,
etc.), point the client at `http://localhost:8117/mcp`.

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
| Chromium fails to load HTTPS pages with `NET::ERR_CERT_AUTHORITY_INVALID` | Corporate CA not imported into Chromium NSS database | Rebuild with `ENABLE_CORP_CA=true` — the Dockerfile handles the `certutil` import automatically |
| mTLS-protected sites reject the browser | `PLAYWRIGHT_CERT_FILE` not set or the referenced file is missing from `docker/certs/playwright/` | Verify the file exists and the basename matches `PLAYWRIGHT_CERT_FILE`; verify `PLAYWRIGHT_CERT_PASSPHRASE` unlocks the `.p12` |
| HAR / trace files not appearing on the host | `../../.playwright-mcp` folder missing at the repo root | Create the folder: `mkdir -p ../../.playwright-mcp` |
| Endpoint returns `404` on `/perfpilot-mcp-playwright/mcp` | Wrong URL path | Playwright uses `/mcp` (no prefix) — see [§🔀 Endpoint routing exception](#-endpoint-routing-exception) |
| Container starts as root instead of `node` | Compose sets `user: "0:0"` for cross-OS bind-mount write access to the shared `.playwright-mcp/` output folder | This is expected on Windows and macOS Docker Desktop — the container process still runs Playwright normally |

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
