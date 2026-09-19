# 🔐 docker/certs/

Certificate drop points for the PerfPilot Hub Docker images. Every folder
here is optional — if you're not running behind a corporate proxy, don't
need mTLS to your test target, and don't need Playwright client certificates,
you can ignore this entire folder.

> 🧩 Part of the **[PerfPilot Hub Docker suite](../README.md)**. See the top-level
> README for the full topology and deployment modes.

---

## 📁 Layout

```
docker/certs/
├── corporate/    (CA PEM bundles for HTTPS-intercepting proxies)
├── jmeter/       (JMeter JKS client keystores for mTLS)
└── playwright/   (Playwright test-user certificates for browser mTLS)
```

Each sub-folder is committed to the repo with a `.gitkeep` so the paths
always exist. The actual cert / keystore files are **gitignored** and never
committed.

---

## 🏢 `corporate/` — HTTPS-intercepting proxy CA

Corporate CA PEM bundles for networks that re-sign TLS traffic (Zscaler,
Norton 360, BlueCoat, Palo Alto, etc.).

| Field | Value |
|---|---|
| Consumed by | **Every** PerfPilot image (when `ENABLE_CORP_CA=true` at build time) |
| File types | `*.pem` (single cert or concatenated bundle) |
| Where installed | OS trust store on every image; JMeter's Java `cacerts` keystore; Chromium's NSS database (Playwright) |
| Default filename | `ca-bundle.pem` (any `*.pem` file in this folder is picked up) |

To enable:

1. Place your CA PEM bundle at `docker/certs/corporate/ca-bundle.pem`
2. Set `ENABLE_CORP_CA=true` in the `.env` file of each image you're building
3. Rebuild: `docker compose up --build -d`

If `ENABLE_CORP_CA=false` (the default), the folder contents are ignored
even if PEM files are present. See the
[top-level Corporate CA guide](../README.md#-10-corporate-ca--https-intercepting-proxy)
for the full three-layer story (OS store, Java `cacerts`, Chromium NSS).

---

## ⚡ `jmeter/` — JMeter JKS client keystores

Java KeyStore (JKS) files used by JMeter for **client-certificate
authentication (mTLS)** to your target application.

| Field | Value |
|---|---|
| Consumed by | `perfpilot-mcp-jmeter` only |
| File types | `*.jks` (Java KeyStore format) |
| Where installed | Baked into the image at `/app/jmeter-certs/` |
| Related env vars | `JMETER_JKS_FILE` (filename only), `JMETER_JKS_PWD` (keystore password) |

To use:

1. Place your `.jks` keystore at `docker/certs/jmeter/your-keystore.jks`
2. Set both variables in `docker/jmeter-mcp/.env`:

    ```env
    JMETER_JKS_FILE=your-keystore.jks   # filename only, not full path
    JMETER_JKS_PWD=your_keystore_password
    ```

3. Rebuild: `docker compose up --build -d` (from `docker/jmeter-mcp/`)

Both variables must be set together — setting only one has no effect. See
the [`jmeter-mcp/README.md`](../jmeter-mcp/README.md#-client-certificate-authentication-mtls)
for the full workflow.

---

## 🎭 `playwright/` — Playwright test-user client certificates

Digital certificates used by the Playwright MCP's Chromium browser for
**test-user impersonation** in mTLS-protected web applications.

| Field | Value |
|---|---|
| Consumed by | `perfpilot-mcp-playwright` only |
| File types | `*.p12`, `*.pem` |
| Where installed | Baked into the image at `/home/node/certs/` |
| Related env vars | `PLAYWRIGHT_CERT_PASSPHRASE` (unlocks `.p12`), `PLAYWRIGHT_CERT_AUTO_SELECT_CN` (Common Name filter) |

To use:

1. Place your certificate files (`.p12` or `.pem`) in `docker/certs/playwright/`
2. Set both variables in `docker/playwright-mcp/.env`:

    ```env
    PLAYWRIGHT_CERT_PASSPHRASE=your_p12_passphrase
    PLAYWRIGHT_CERT_AUTO_SELECT_CN=TestUser1
    ```

3. Rebuild: `docker compose up --build -d` (from `docker/playwright-mcp/`)

The `AUTO_SELECT_CN` filter must match the Common Name in the certificate
subject exactly. See the [`playwright-mcp/README.md`](../playwright-mcp/README.md#-client-certificate-authentication-mtls)
for the full workflow.

---

## 🔒 Security

All files inside `corporate/`, `jmeter/`, and `playwright/` are **gitignored**
except for `.gitkeep` markers. Verify with:

```bash
git check-ignore -v docker/certs/corporate/ca-bundle.pem
docker/.gitignore:2:docker/certs/corporate/*    docker/certs/corporate/ca-bundle.pem
```

**Never commit** any real certificate material to the repository. If a
credential ever ends up committed (either accidentally or via a bad
`git add`), rotate it immediately and force-push the removal.

---

## 🔗 See also

- 📖 [`docker/README.md`](../README.md) — full PerfPilot Hub Docker guide (includes the Corporate CA section §10)
- 📂 [`docker/jmeter-mcp/README.md`](../jmeter-mcp/README.md) — JMeter MCP that consumes JKS keystores
- 📂 [`docker/playwright-mcp/README.md`](../playwright-mcp/README.md) — Playwright MCP that consumes P12 / PEM certs
