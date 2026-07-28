# 📂🏢 SharePoint MCP Server

A Python-based MCP server built with FastMCP 2.0 to upload performance test artifacts to SharePoint. Enables performance test engineers to persist test results, reports, and analysis files to SharePoint document libraries — all driven by AI agents.

> **Architecture Note**: Authentication architecture adapted from the [msteams-mcp](../msteams-mcp/) server in this suite, using the same browser-based token capture pattern for environments without Azure AD app registration access.

## ✨ Features

- 🔐 **Browser-Based Authentication**: No Azure AD app registration required — authenticates via the SharePoint web client directly
- 🍪 **Dual Auth Strategy**: Supports both Bearer token and cookie-based (FedAuth/rtFa) authentication — works on tenants that don't emit SharePoint-scoped Bearer tokens
- 🔄 **Three-Layer Token Resolution**: In-memory cache, encrypted session file, browser login (fast path avoids browser launches)
- 🔍 **Probe-Based Verification**: Validates cached tokens with a lightweight API probe before reporting success
- 🛡️ **Encrypted Session Storage**: AES-256-GCM encryption with machine-bound scrypt key derivation
- 📡 **SSO Support**: Automatic session reuse across server restarts — login once, reuse silently until tokens expire
- 🏢 **Tenant Auto-Detection**: Extracts tenant name from browser URL after login (no manual configuration needed)
- 📤 **Single File Upload**: Upload individual artifacts to a specified SharePoint location
- 📦 **Folder Upload**: Upload an entire artifact folder (recursive) in one operation
- 📥 **File Download**: Download files from SharePoint to local disk
- 📁 **Folder Management**: Create folders and list document library contents
- 📚 **Library Discovery**: List all document libraries in a SharePoint site
- 🔎 **KQL Search**: Search SharePoint content using Keyword Query Language
- 👤 **User Profile**: View the authenticated user's identity from JWT claims
- 💬 **Optional Teams Notification**: Notify your team via MS Teams MCP after upload completes (config-driven)
- 🧩 **FastMCP 2.0**: Consistent architecture with the rest of the mcp-perf-suite ecosystem

## 🛠️ Prerequisites

- 🐍 **Python 3.12+**
- 🚀 **FastMCP 2.0**
- 🌐 **Microsoft Edge or Google Chrome** installed (used for Playwright browser automation)
- 🏢 **SharePoint Online Account** (business or enterprise)
- 📦 **Python Packages**: `fastmcp`, `httpx`, `playwright`, `cryptography`, `python-dotenv`, `pyyaml`

## 🚀 Getting Started

### 1. Clone the Repository
```
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd mcp-perf-suite/sharepoint-mcp
```

### 2. Register in Cursor MCP Config

Add this entry to your `~/.cursor/mcp.json` inside the `"mcpServers"` block:

```json
"sharepoint": {
  "command": "uv",
  "args": [
    "--directory",
    "C:\\Users\\<your-user>\\Repos\\_GitHub\\mcp-perf-suite\\sharepoint-mcp",
    "run",
    "sharepoint.py"
  ]
}
```

### 3. Restart Cursor

The `sharepoint` MCP server will appear in your available tools.

### 4. First-Time Login

Call `sharepoint_login` from any Cursor agent conversation. On first run:
1. A visible Edge/Chrome window opens and navigates to your SharePoint site
2. You log in manually (SSO, password, MFA — whatever your tenant requires)
3. The session state (cookies, tokens) is captured, encrypted, and saved locally
4. The server auto-detects whether your tenant uses Bearer tokens or cookie-based auth and reports the active `authMode`
5. All subsequent calls reuse the cached session — **no browser opens again** until tokens expire

## 🔧 Available MCP Tools (10 Tools)

### Authentication (2 tools)

| 🛠️ Tool Name | 📃 Description |
|-----------|-------------|
| `sharepoint_login` | Authenticate to SharePoint (SSO, headless, visible browser fallback). Returns `authMode` (bearer or cookie). |
| `sharepoint_status` | Check session health, token validity, auth mode, cookie state, tenant info (no network calls) |

### File Operations (3 tools)

| 🛠️ Tool Name | 📃 Description |
|-----------|-------------|
| `sharepoint_upload_file` | Upload a single file to a specified SharePoint folder. Files > 250 MB use chunked upload automatically. |
| `sharepoint_upload_folder` | Upload an entire local folder (recursive) to a SharePoint folder, preserving directory structure |
| `sharepoint_download_file` | Download a file from SharePoint to a local path |

### Folder Operations (2 tools)

| 🛠️ Tool Name | 📃 Description |
|-----------|-------------|
| `sharepoint_create_folder` | Create a folder (and parent folders) in a SharePoint document library |
| `sharepoint_list_folder` | List contents of a SharePoint folder (files and subfolders with metadata) |

### Discovery & Search (3 tools)

| 🛠️ Tool Name | 📃 Description |
|-----------|-------------|
| `sharepoint_list_libraries` | List all document libraries in a SharePoint site (title, URL, item count) |
| `sharepoint_search` | Search SharePoint content using KQL (Keyword Query Language) with filtering support |
| `sharepoint_get_me` | Get the authenticated user's profile (display name, email, Azure AD object ID, tenant ID) |

### 📤 Upload Design

The upload interface is deliberately simple — two modes only:

- 📄 **Single file**: Specify one local file path and a SharePoint destination
- 📦 **Full folder**: Specify a local folder path and a SharePoint destination — everything inside gets uploaded

Both modes **require** `site_url` and `destination_folder`. There is no default upload location — the user must always specify where artifacts go.

Files larger than 250 MB are automatically uploaded using SharePoint's chunked upload API (`StartUpload` / `ContinueUpload` / `FinishUpload`), with configurable chunk size (default 10 MB).

## 🔐 Authentication Architecture

### Dual Auth Strategy

Some SharePoint tenants issue SharePoint-scoped Bearer tokens in the browser's network requests, while others rely exclusively on cookie-based authentication (FedAuth/rtFa). The MCP server supports both modes transparently:

```
_request() called (sharepoint_api.py)
       |
       v
+--------------------------------+
| Valid Bearer token?            |
| (correct aud: *.sharepoint.com)|
+--------+----------+------------+
     yes |          | no
         v          v
+----------------+  +-----------------------------+
| Authorization: |  | FedAuth/rtFa in session?    |
| Bearer header  |  +--------+--------------------+
+-------+--------+       yes |          | no
        |                    v          v
        |  +-------------------+  +-----------------+
        |  | Cookie: FedAuth=  |  | AUTH_REQUIRED   |
        |  | rtFa= header      |  | → login needed  |
        |  +--------+----------+  +-----------------+
        |           |
        v           v
+----------------------------+
| Execute API call           |
| (401 with Bearer? → retry  |
|  once with cookie fallback)|
+----------------------------+
```

The active auth mode is reported in `sharepoint_login()` and `sharepoint_status()` responses via the `authMode` field (`"bearer"` or `"cookie"`).

### Three-Layer Token Resolution

```
sharepoint_login() called
       |
       v
+----------------------------+
| Layer 1: In-Memory Cache   |  <-- Fastest (no I/O)
| Valid Bearer or cookie?    |
+----------+-----------------+
           | miss
           v
+----------------------------+
| Layer 2: Encrypted File    |  <-- Fast (local file decrypt)
| session-state.json         |
| Bearer → probe verify      |
| Cookies → probe verify     |
+----------+-----------------+
           | miss, expired, or probe failed
           v
+----------------------------+
| Layer 3: Browser Login     |  <-- Slow (launches browser)
| Headless SSO first         |
| Visible login fallback     |
| Auto-detect auth mode      |
+----------------------------+
```

Layer 2 now includes a **probe verification step** — after restoring cached tokens, a lightweight `GET _api/web?$select=Title` call validates that the auth actually works before reporting success. If the probe fails, the cached token is invalidated and the flow continues to Layer 3.

### Token Types

| Token | Source | Used For | Typical TTL |
|-------|--------|----------|-------------|
| **SharePoint Bearer** | Network request intercept (audience-validated) | All `_api/` REST calls (when available) | ~1 hour |
| **FedAuth Cookie** | Browser session cookies | `_api/` REST calls (primary on cookie-auth tenants) | Session-based |
| **rtFa Cookie** | Browser session cookies | Root federation auth (paired with FedAuth) | Session-based |
| **Form Digest** | `_api/contextinfo` | Write operations (CSRF) | ~30 minutes |

> **JWT Audience Validation**: Bearer tokens intercepted during browser login are validated against the `aud` claim before caching. Only tokens scoped to `*.sharepoint.com` are accepted. Tokens scoped to `graph.microsoft.com` or other services are silently rejected to prevent wrong-audience tokens from masking authentication failures.

### Why Browser-Based Auth?

Traditional approaches require registering an Azure AD app with Graph API permissions, admin consent, client secrets, and OAuth2 flows. This is often inaccessible for performance testing teams.

Instead, we authenticate the same way a human does — through the SharePoint web client. Playwright captures the resulting session state (Bearer tokens and/or cookies), which is then reused for `_api/` REST calls via `httpx`. No app registration, no client secrets, no admin consent needed.

## 🛡️ Security Model

### What Is Stored

| File | Location | Contents |
|------|----------|----------|
| `session-state.json` | `%APPDATA%\sharepoint-mcp-server\` | Encrypted cookies + localStorage from SharePoint session |
| `token-cache.json` | `%APPDATA%\sharepoint-mcp-server\` | Encrypted Bearer token + expiry |
| `browser-profile\` | `%APPDATA%\sharepoint-mcp-server\` | Chromium persistent profile |

### Encryption Details

- **Algorithm**: AES-256-GCM (authenticated encryption)
- **Key Derivation**: scrypt (N=16384, r=8, p=1) from `hostname:username`
- **Key Material**: Machine-bound — derived from `socket.gethostname():os.getlogin()`
- **IV**: 16 bytes, randomly generated per encryption
- **Implication**: Encrypted files are useless on another machine or user account

## ⚙️ Configuration

### config.yaml

```yaml
server:
  name: "sharepoint-mcp"
  version: "0.1.0"

general:
  enable_debug: false
  enable_logging: true

sharepoint:
  tenant: ""                    # Auto-detected from browser URL; set manually only if needed
  browser_channel: "chrome"     # "chrome", "msedge", or "chromium"
  session_expiry_hours: 12
  token_refresh_threshold_sec: 600
  http_request_timeout_sec: 60
  retry_max_attempts: 3
  retry_base_delay_sec: 1
  retry_max_delay_sec: 10
  max_upload_size_mb: 250       # Chunked upload threshold
  chunk_size_mb: 10             # Chunk size for large files

  # Optional Teams notification after upload
  notification_on_upload:
    enabled: false
    target: ""                  # Named target from msteams-mcp config
    template: ""                # Falls back to "default-notification-sharepoint-upload.md"
```

### OS-Specific Overrides

Create `config.windows.yaml` or `config.mac.yaml` for platform-specific settings. Platform-specific files take priority over `config.yaml`.

## 📦 Project Structure

```
sharepoint-mcp/
├── sharepoint.py                  # FastMCP server entry point
├── services/
│   ├── auth_manager.py            # Three-layer auth orchestration
│   ├── browser_auth.py            # SharePoint navigation + token intercept
│   ├── browser_context.py         # Playwright persistent browser context
│   ├── token_extractor.py         # Bearer JWT extraction + user profile
│   ├── session_store.py           # Encrypted session persistence
│   ├── crypto.py                  # AES-256-GCM encryption primitives
│   ├── errors.py                  # ErrorCode enum, McpError, Result type
│   └── sharepoint_api.py          # SharePoint _api REST client
├── utils/
│   └── config.py                  # Platform-aware YAML config loader
├── config.yaml                    # Application configuration
├── config.example.yaml            # Configuration template
├── pyproject.toml                 # Python project metadata + dependencies
└── README.md
```

## 🧠 High-Level Workflow

```text
AI Agent
    ↓
SharePoint MCP
    ↓
Browser Authentication
    ↓
SharePoint REST API
    ↓
Upload Artifacts / Reports / Logs
    ↓
(Optional) MS Teams Notification
```

## 🐛 Troubleshooting

### Server Won't Start
- Ensure `mcp.json` entry points to the correct `sharepoint-mcp` directory
- Verify Python 3.12+ is available: `python --version`
- Check `uv` is installed: `uv --version`

### Browser Doesn't Open
- Ensure Microsoft Edge or Google Chrome is installed
- Check for stale browser profile locks: delete `%APPDATA%\sharepoint-mcp-server\browser-profile\SingletonLock` if it exists

### Tenant Not Detected
- If auto-detection fails, set `sharepoint.tenant` in your `config.yaml`
- The tenant is the subdomain in your SharePoint URL: `https://<tenant>.sharepoint.com`

### Upload Fails with 401
- Call `sharepoint_status()` to check the active `authMode` and token/cookie state
- If `authMode` is `"none"`, call `sharepoint_login()` to re-authenticate
- If `authMode` is `"bearer"`, the token may have expired (~1 hour lifetime) — call `sharepoint_login()` to refresh
- If `authMode` is `"cookie"`, session cookies may have expired — call `sharepoint_login(force=True)` for a fresh browser login

### Session Expired
- Call `sharepoint_login(force=True)` to force a fresh browser login
- Sessions expire after 12 hours by default (configurable)
- Quick fix: delete `%APPDATA%\sharepoint-mcp-server\*`, restart Cursor, then call `sharepoint_login()`

### Login Appears to Hang
- During interactive login, the server logs a heartbeat every 30 seconds (`"Waiting for login... Xs / 300s"`)
- The 5-minute timeout is normal for tenants with complex MFA flows
- If login consistently times out, check that your browser channel (`chrome` or `msedge`) is correctly set in `config.yaml`

## License

This project is part of the MCP Performance Suite. See repository root for license information.

Authentication architecture adapted from [msteams-mcp](../msteams-mcp/) (browser-based token capture pattern).

---

**Built with ❤️ using FastMCP 2.0**
