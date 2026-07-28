# MS Teams MCP Server 💬🚀

A Python-based MCP server built with FastMCP 2.0 to automate Microsoft Teams notifications for performance testing workflows. Enables pre-test alerts, post-test result sharing, channel discovery, and team communication — all driven by AI agents.

> **Attribution**: Authentication architecture informed by [m0nkmaster/msteams-mcp](https://github.com/nicholasgriffintn/msteams-mcp) (MIT License, v0.23.1). Reimplemented in Python with improvements for the mcp-perf-suite ecosystem.

## 🎯 Features

- 🔐 **Browser-Based Authentication**: No Azure AD app registration required — authenticates via the Teams web client directly
- 🔄 **Three-Layer Token Resolution**: In-memory cache → encrypted session file → browser login (fast path avoids browser launches)
- 🛡️ **Encrypted Session Storage**: AES-256-GCM encryption with machine-bound scrypt key derivation
- 📡 **SSO Support**: Automatic session reuse across server restarts — login once, reuse silently until tokens expire
- 💬 **Chat & Group Chat**: Create 1:1 chats and group chats with multiple members for stakeholder communication
- 📋 **Templated Notifications**: Pre-defined markdown templates with `{{PLACEHOLDER}}` interpolation, auto-converted to Teams HTML
- 🎯 **Config-Driven Targets**: Named notification targets (channels, chats) in YAML config with auto-resolution
- 📁 **Test Run Context**: Auto-populates template variables from `artifacts/<test_run_id>/` (BlazeMeter, Confluence links) and logs notifications for context tracking
- 🧩 **FastMCP 2.0**: Consistent architecture with the rest of the mcp-perf-suite ecosystem

## 🛠️ Prerequisites

- 🐍 **Python 3.12+**
- 🚀 **FastMCP 2.0**
- 🌐 **Microsoft Edge or Google Chrome** installed (used for Playwright browser automation)
- 🏢 **Microsoft Teams Account** (personal, business, or enterprise)
- 📦 **Python Packages**: `fastmcp`, `httpx`, `playwright`, `cryptography`, `python-dotenv`, `pyyaml`

## 🚀 Getting Started

### 1. Clone the Repository
```
git clone https://github.com/canyonlabz/mcp-perf-suite.git
cd mcp-perf-suite/msteams-mcp
```

### 2. Register in Cursor MCP Config

Add this entry to your `~/.cursor/mcp.json` inside the `"mcpServers"` block:

```json
"msteams": {
  "command": "uv",
  "args": [
    "--directory",
    "C:\\Users\\<your-user>\\Repos\\_GitHub\\mcp-perf-suite\\msteams-mcp",
    "run",
    "msteams.py"
  ]
}
```

### 3. Restart Cursor

The `msteams` MCP server will appear in your available tools.

### 4. First-Time Login

Call `teams_login` from any Cursor agent conversation. On first run:
1. A visible Edge/Chrome window opens and navigates to `teams.microsoft.com`
2. You log in manually (SSO, password, MFA — whatever your Tenant requires)
3. The session is captured, encrypted, and saved locally
4. All subsequent calls reuse the cached session — **no browser opens again** until tokens expire

## 🔧 Usage

### 📝 Available MCP Tools

#### Authentication

| 🛠️ Tool Name | 📃 Description |
|--------------|----------------|
| `teams_login` | Authenticate to MS Teams (SSO → headless → visible browser fallback) |
| `teams_status` | Check session health, token validity, and user info (no network calls) |

#### Messaging & Channels

| 🛠️ Tool Name | 📃 Description |
|--------------|----------------|
| `teams_send_message` | Send a message to a channel, chat, or group — supports templates, named targets, and `test_run_id` context |
| `teams_list_channels` | List all teams and channels the authenticated user has access to |
| `teams_find_channel` | Search for channels by name (org-wide discovery + membership status) |

#### Chats

| 🛠️ Tool Name | 📃 Description |
|--------------|----------------|
| `teams_get_chat` | Get a 1:1 chat conversation ID for another user (deterministic, no API call) |
| `teams_create_group_chat` | Create a new group chat with 2+ members and optional topic |

#### Search & People

| 🛠️ Tool Name | 📃 Description |
|--------------|----------------|
| `teams_search` | Full-text search across Teams messages with operator support |
| `teams_search_people` | Search for people by name or email (returns job title, department, MRI) |
| `teams_get_me` | Get the current user's profile (display name, email, tenant ID, MRI) |

### Example: Login Flow
```
# First call — opens browser for manual login
teams_login()
# → {"status": "authenticated", "method": "manual", "user": "you@company.com"}

# Subsequent calls — instant, from encrypted cache
teams_login()
# → {"status": "authenticated", "method": "sso", "user": "you@company.com"}

# Force fresh login (wipes and rebuilds session)
teams_login(force=True)
```

### Example: Status Check
```
teams_status()
# → {
#   "hasSession": true,
#   "sessionAgeHours": 2.3,
#   "isSessionExpired": false,
#   "substrateToken": {"hasToken": true, "remainingMinutes": 45.2},
#   "messageAuth": {"hasSkypeToken": true, "hasAuthToken": true, "skypeTokenRemainingMinutes": 110.5},
#   "user": "you@company.com"
# }
```

### Example: Send a Plain Message
```
# List available channels first
teams_list_channels()
# → [{"displayName": "Performance Engineering", "channels": [{"id": "19:abc@thread.tacv2", ...}]}]

# Send a plain message (markdown auto-converted to Teams HTML)
teams_send_message(conversation_id="19:abc@thread.tacv2", message="**Load test** starting in 5 minutes")
# → {"status": "sent", "details": {"messageId": "...", "target": "19:abc@thread.tacv2"}}
```

### Example: Send a Templated Notification
```
# Send a start-test notification using a template + named target from config
teams_send_message(
    target="perf-channel",
    template="notification-start-test.md",
    variables='{"TEST_NAME": "50-User Load Test", "ENVIRONMENT": "staging", "DURATION": "30 min", "VIRTUAL_USERS": "50"}',
    message="Heads up — starting load test shortly."
)
# → Template loaded, placeholders interpolated, markdown→HTML converted, sent to perf-channel

# Send a test-results notification with auto-populated context from artifacts
teams_send_message(
    target="perf-channel",
    template="notification-test-results.md",
    test_run_id="abc-123-run",
    message="Test completed successfully. See report for details."
)
# → Auto-reads BlazeMeter/Confluence links from artifacts/abc-123-run/, fills template, sends
```

### Example: 1:1 and Group Chats
```
# Get a 1:1 chat with a colleague (deterministic ID, no API call)
teams_get_chat(user_identifier="8:orgid:abc-123-456")
# → {"conversationId": "19:abc_def@unq.gbl.spaces", "otherUserId": "abc-123-456"}

# Create a group chat for triage
teams_create_group_chat(
    member_identifiers='["8:orgid:abc-123", "8:orgid:def-456"]',
    topic="Load Test Triage"
)
# → {"status": "created", "details": {"conversationId": "19:xyz@thread.v2", "members": [...]}}
```

### Example: Search Messages
```
# Search for recent test notifications
teams_search(query="load test results sent:>=2026-04-01")
# → {"resultCount": 5, "pagination": {"total": 12, "hasMore": true}, "results": [...]}

# Search with operators
teams_search(query="from:homer.simpson@company.com is:Channels hasattachment:true")
```

### Example: Find a Channel
```
# Search when you know part of the name
teams_find_channel(query="performance")
# → {"count": 3, "channels": [
#     {"channelName": "Performance Engineering", "teamName": "QA", "isMember": true},
#     {"channelName": "Performance Results", "teamName": "DevOps", "isMember": false}
# ]}
```

### Example: People Search
```
# Find someone for @mentions
teams_search_people(query="Homer")
# → {"returned": 2, "results": [
#     {"displayName": "Homer Simpson", "email": "homer.simpson@company.com", "jobTitle": "Power Plant Operator", ...}
# ]}

# Get your own profile
teams_get_me()
# → {"displayName": "Your Name", "email": "you@company.com", "mri": "8:orgid:abc-123-..."}
```

## 🔐 Authentication Architecture

### Three-Layer Token Resolution

```
teams_login() called
       │
       ▼
┌──────────────────────────┐
│ Layer 1: In-Memory Cache │  ← Fastest (no I/O)
│ Valid token in memory?   │
└────────┬─────────────────┘
         │ miss
         ▼
┌──────────────────────────┐
│ Layer 2: Encrypted File  │  ← Fast (local file decrypt)
│ session-state.json       │
│ Valid tokens extractable?│
└────────┬─────────────────┘
         │ miss or expired
         ▼
┌──────────────────────────┐
│ Layer 3: Browser Login   │  ← Slow (launches browser)
│ Headless SSO first       │
│ Visible login fallback   │
└──────────────────────────┘
```

### Why Browser-Based Auth?

Traditional approaches require registering an Azure AD app with Graph API permissions, admin consent, client secrets, and OAuth2 flows. This is complex for performance testing teams who just need to post results to a channel.

Instead, we authenticate the same way a human does — through the Teams web client. Playwright captures the resulting session cookies and MSAL tokens, which are then reused for API calls.

### Token Types

| Token | Source | Used For | Typical TTL |
|-------|--------|----------|-------------|
| **Skype Token** | `skypetoken_asm` cookie | Messaging APIs (chatsvc), CSA channel listing | ~24 hours |
| **Auth Token** | `authtoken` cookie | General Teams API auth | ~24 hours |
| **Substrate Token** | MSAL localStorage | Search, People, and Channel discovery APIs | ~1 hour |
| **CSA Token** | `chatsvcagg` localStorage | Teams/channels list API (CSA v3) | ~24 hours |

### Login Detection

After the user logs in, the system detects success by checking for **tokens in localStorage** (primary) and known UI selectors (fallback). Token-based detection is preferred because the Teams UI changes across versions, but localStorage token patterns are stable.

## 🛡️ Security Model

### What Is Stored

| File | Location | Contents |
|------|----------|----------|
| `session-state.json` | `%APPDATA%\teams-mcp-server\` | Encrypted cookies + localStorage from Teams web session |
| `token-cache.json` | `%APPDATA%\teams-mcp-server\` | Encrypted extracted tokens (Substrate, Skype) |
| `browser-profile\` | `%APPDATA%\teams-mcp-server\` | Chromium persistent profile (same as normal Edge usage) |

### Encryption Details

- **Algorithm**: AES-256-GCM (authenticated encryption)
- **Key Derivation**: scrypt (N=16384, r=8, p=1) from `hostname:username`
- **Key Material**: Machine-bound — derived from `socket.gethostname():os.getlogin()`
- **IV**: 16 bytes, randomly generated per encryption
- **Implication**: Encrypted files are **useless on another machine or user account**. If you copy `session-state.json` to another computer, decryption will fail.

### Session Lifecycle

- Sessions expire after **12 hours** (configurable in `config.yaml`)
- Token refresh threshold: **10 minutes** before expiry, tokens are proactively refreshed
- `teams_login(force=True)` wipes all cached state and starts fresh
- On first read, plaintext files are automatically migrated to encrypted format

## 📦 Project Structure

```
msteams-mcp/
├── msteams.py                      # FastMCP server entry point (10 tools)
├── services/
│   ├── auth_manager.py             # Three-layer auth orchestration
│   ├── browser_auth.py             # Teams navigation + login detection
│   ├── browser_context.py          # Playwright persistent browser context
│   ├── token_extractor.py          # JWT decode + token extraction from localStorage
│   ├── session_store.py            # Encrypted session persistence
│   ├── crypto.py                   # AES-256-GCM encryption primitives
│   ├── errors.py                   # ErrorCode enum, McpError, Result monad
│   ├── teams_api.py                # Chatsvc + CSA API client (messaging, chats, channels)
│   ├── substrate_api.py            # Substrate API client (search, people, channel discovery)
│   ├── parsers.py                  # Parsing functions + markdown→Teams HTML converter
│   ├── template_manager.py         # Template loading, rendering, and notification logging
│   └── target_resolver.py          # Named target resolution from config
├── templates/
│   ├── default-notification-start-test.md    # Start-test notification template
│   ├── default-notification-stop-test.md     # Stop-test notification template
│   └── default-notification-test-results.md  # Results summary notification template
├── utils/
│   └── config.py                   # Platform-aware YAML config loader
├── config.yaml                     # Application configuration
├── config.example.yaml             # Configuration template
├── pyproject.toml                  # Python project metadata + dependencies
└── README.md
```

## ⚙️ Configuration

### config.yaml

```yaml
server:
  name: "msteams-mcp"
  version: "0.1.0"

general:
  enable_debug: false       # Set true for verbose logging
  enable_logging: true

teams:
  browser_channel: "chrome"         # Browser for login: "chrome", "msedge", or "chromium"
  session_expiry_hours: 12          # Max session age before re-login
  token_refresh_threshold_sec: 600  # Refresh tokens 10 min before expiry
  http_request_timeout_sec: 30      # HTTP request timeout
  retry_max_attempts: 3             # Max retry attempts
  retry_base_delay_sec: 1           # Initial retry backoff
  retry_max_delay_sec: 10           # Max retry backoff
  default_page_size: 25             # Default search results page size
  max_page_size: 100                # Max search results page size
  default_people_limit: 10          # Default people search results
  default_channel_limit: 10         # Default channel search results
  templates_path: "./templates"     # Path to notification templates

  # Named notification targets — auto-resolved by teams_send_message
  notification_targets:
    channels:
      perf-channel:
        conversation_id: "19:abc@thread.tacv2"
        description: "Performance testing notifications"
        # template: "custom-start-test.md"   # Optional per-channel template override
    default_chat:
      conversation_id: ""
      description: "Default chat for notifications"
```

### OS-Specific Overrides

Create `config.windows.yaml` or `config.mac.yaml` for platform-specific settings. Platform-specific files take priority over `config.yaml`.

## 🗺️ Roadmap

### Phase 1 — Authentication ✅
- `teams_login` — Browser-based auth with three-layer token resolution
- `teams_status` — Diagnostic session health check

### Phase 2 — Channel Discovery & Messaging ✅
- `teams_list_channels` — List all joined teams and channels
- `teams_send_message` — Send messages/notifications to channels and chats
- `teams_find_channel` — Search for channels by name (org-wide + membership)

### Phase 3 — Search & People ✅
- `teams_search` — Full-text message search with operator support
- `teams_search_people` — People search by name or email
- `teams_get_me` — Current user profile extraction

### Phase 4 — Chats & Templated Notifications ✅
- `teams_get_chat` — 1:1 chat conversation ID resolution
- `teams_create_group_chat` — Group chat creation with member management
- Markdown → Teams HTML auto-conversion
- `{{PLACEHOLDER}}` notification templates (start, stop, results) with default/custom fallback
- Config-driven named targets with per-channel template overrides
- `test_run_id` context auto-population from BlazeMeter/Confluence artifacts
- Notification logging to `artifacts/<test_run_id>/notifications/`

### Phase 5 — Future Enhancements (Planned)
- Adaptive Card formatting for rich structured messages
- `teams_logout` tool for explicit session cleanup
- Integration with `performance-testing-workflow` skill for end-to-end notifications

## 🐛 Troubleshooting

> **Full troubleshooting guide:** [`docs/troubleshooting/msteams-mcp-troubleshooting.md`](../docs/troubleshooting/msteams-mcp-troubleshooting.md)

### Server Won't Start
- Ensure `mcp.json` entry points to the correct `msteams-mcp` directory
- Verify Python 3.12+ is available: `python --version`
- Check `uv` is installed: `uv --version`

### Browser Doesn't Open
- Ensure Microsoft Edge or Google Chrome is installed
- Check for stale browser profile locks: delete `%APPDATA%\teams-mcp-server\browser-profile\SingletonLock` if it exists

### Login Detected But Tokens Missing
- Some Tenant configurations may not populate all token types (e.g., personal accounts may lack Substrate search tokens)
- Call `teams_status` to see which tokens are available
- Core messaging uses Skype tokens (from cookies), not Substrate tokens

### Search/People Tools Return AUTH_EXPIRED
- Substrate tokens have a short TTL (~1 hour). The server proactively refreshes via headless browser when within 10 minutes of expiry.
- If refresh fails, call `teams_login()` to re-authenticate
- Check `teams_status()` → `substrateToken.remainingMinutes` to see current token health

### Session Expired
- Call `teams_login(force=True)` to force a fresh browser login
- Check `teams_status` for `sessionAgeHours` — sessions expire after 12 hours by default

### Substrate Token Missing / `force=True` Not Opening Browser
- See [full troubleshooting guide](../docs/troubleshooting/msteams-mcp-troubleshooting.md) for token recovery steps
- Quick fix: delete `~/.teams-mcp-server/*`, restart Cursor, then call `teams_login()`

## 📋 Notification Templates

### How Templates Work

Templates are markdown files in the `templates/` directory with `{{PLACEHOLDER}}` variables. When `teams_send_message` is called with a `template` parameter:

1. **Load**: Template is loaded using layered fallback (channel-specific → caller-specified → default)
2. **Populate**: Variables are interpolated from three sources (merged in order):
   - `test_run_id` context (auto-read from `artifacts/<test_run_id>/`)
   - Explicit `variables` parameter
   - `message` parameter (fills `{{MESSAGE}}`)
3. **Convert**: Rendered markdown is converted to Teams-compatible HTML
4. **Send**: HTML content is sent to the resolved target
5. **Log**: Notification is logged to `artifacts/<test_run_id>/notifications/notification_log.json`

### Default Templates

| Template | Purpose | Key Placeholders |
|----------|---------|------------------|
| `default-notification-start-test.md` | Pre-test alert | `TEST_NAME`, `ENVIRONMENT`, `DURATION`, `VIRTUAL_USERS` |
| `default-notification-stop-test.md` | Post-test completion | Same as start + `END_TIME`, `STATUS` |
| `default-notification-test-results.md` | Results summary | Same as stop + `AVG_RESPONSE_TIME`, `ERROR_RATE`, `BLAZEMETER_REPORT_LINK`, `CONFLUENCE_REPORT_LINK` |

### Custom Templates

Create your own templates in the `templates/` directory. Name them without the `default-` prefix to override the defaults:

- `notification-start-test.md` overrides `default-notification-start-test.md`
- Or use a completely custom name like `my-team-notification.md` and pass it directly via the `template` parameter

### Auto-Populated Variables from `test_run_id`

When `test_run_id` is provided, these variables are auto-populated from artifacts:

| Source File | Variable |
|-------------|----------|
| `blazemeter/public_report.json` | `{{BLAZEMETER_REPORT_LINK}}`, `{{REPORT_LINK}}` |
| `confluence/report_metadata.json` | `{{CONFLUENCE_REPORT_LINK}}`, `{{REPORT_LINK}}` |
| `notifications/notification_log.json` | `{{TEST_NAME}}`, `{{ENVIRONMENT}}`, `{{DURATION}}`, `{{START_TIME}}` (from last start notification) |

## 📄 License

This project is part of the MCP Performance Suite. See repository root for license information.

Authentication architecture informed by [m0nkmaster/msteams-mcp](https://github.com/nicholasgriffintn/msteams-mcp) (MIT License).

---

**Built with ❤️ using FastMCP 2.0**
