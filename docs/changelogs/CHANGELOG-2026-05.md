# MCP Performance Suite - Changelog (May 2026)

This document summarizes the enhancements and new features added to the MCP Performance Suite during May 2026.

---

## Table of Contents

- [1. PerfMemory Taxonomy System](#1-perfmemory-taxonomy-system)
- [2. JMeter MCP — EntraID Correlation Engine](#2-jmeter-mcp--entraid-correlation-engine)
- [3. EntraID Debugging Skill](#3-entraid-debugging-skill)
- [4. SharePoint MCP Server](#4-sharepoint-mcp-server)
- [5. FastMCP v3 Migration](#5-fastmcp-v3-migration)
- [6. PerfPilot Hub — Super MCP Gateway](#6-perfpilot-hub--super-mcp-gateway)
- [Previous Changelogs](#previous-changelogs)

---

## 1. PerfMemory Taxonomy System

### 1.1 Overview

Added a YAML-driven taxonomy layer to the PerfMemory MCP server that standardizes application and service naming before data enters the vector database and knowledge graph. This solves the problem of inconsistent `system_under_test` naming across teams and sessions (e.g., "OCP", "ocp-app", "Online Cart Application" all mapping to the same system), which previously degraded vector search quality and fragmented graph relationships.

### 1.2 Taxonomy Configuration

A new `taxonomy.yaml` file defines the canonical mapping of applications, services, and aliases:

- Each application entry has a canonical `system_under_test` name, optional aliases, and service hierarchy
- Aliases enable `system_alias` lookups — agents can pass any known alias and the taxonomy resolves it to the canonical name
- Services within an application inherit the parent's project context in the knowledge graph

### 1.3 Database Schema Updates

Extended both the relational and graph schemas to support taxonomy-driven fields:

| Layer | Changes |
|-------|---------|
| Relational (`debug_sessions`, `debug_attempts`) | New taxonomy columns for standardized system and service identification |
| Graph (`perf_knowledge`) | Updated vertex labels and edge properties for service-level tracking |

### 1.4 Tool Enhancements

Updated existing MCP tools to leverage the taxonomy:

| Tool | Enhancement |
|------|-------------|
| `store_debug_session` | Resolves `system_alias` to canonical `system_under_test` via taxonomy |
| `store_debug_attempt` | Applies taxonomy normalization to system and service fields |
| `find_similar_attempts` | Accepts `system_alias` input parameter for taxonomy-aware search |
| `find_cross_project_patterns` | Resolves error category and project aliases via taxonomy |
| `list_sessions` / `get_session_detail` | Enhanced filtering options using taxonomy fields |

### 1.5 Taxonomy Normalization Tool

A new `normalize_taxonomy` MCP tool that validates and normalizes existing data against the taxonomy:

- Scans existing sessions and attempts for non-compliant naming
- Backfills alias mappings for historical data
- Reports compliance status and suggested corrections

### 1.6 Multi-Alias Support for Applications (E-009)

Added an optional `aliases` list field to application entries in the taxonomy YAML, enabling multiple alternative names to resolve to the same canonical application. This mirrors the existing `aliases` list design used by `error_categories`, `environment_types`, and `auth_flow_types`.

The singular `alias` field (stored as `system_alias` in the DB) remains unchanged. The new `aliases` list is used for input matching only — entries are not stored in the database.

| Layer | Change |
|-------|--------|
| `taxonomy.example.yaml` | Added `aliases` list field to application entries with documentation |
| `TaxonomyResolver._build_lookups()` | Registers each `aliases` entry in the app lookup table alongside `name` and `alias` |
| `TaxonomyResolver.resolve_application()` | Updated docstring to document `aliases` list matching |
| `TaxonomyMatcher.match_system()` | Tier 1 (exact) and Tier 2 (contains) now check `aliases` list entries |

### 1.7 Files Created / Modified

| File | Purpose |
|------|---------|
| `perfmemory-mcp/taxonomy.example.yaml` | Example taxonomy configuration with application/service/alias definitions, multi-alias support |
| `perfmemory-mcp/perfmemory.py` | Added `normalize_taxonomy` tool, taxonomy resolution in existing tools |
| `perfmemory-mcp/services/taxonomy.py` | Multi-alias application resolution in `_build_lookups()` and `resolve_application()` |
| `perfmemory-mcp/services/session_manager.py` | Taxonomy column support in CRUD operations and search |
| `perfmemory-mcp/services/graph_manager.py` | Graph schema updates for taxonomy-driven service tracking |
| `perfmemory-mcp/tools/normalize_taxonomy.py` | Multi-alias matching in `TaxonomyMatcher.match_system()` Tier 1 and Tier 2 |
| `.cursor/skills/perfmemory/SKILL.md` | Updated with taxonomy usage guidance and `system_alias` parameter docs |

---

## 2. JMeter MCP — EntraID Correlation Engine

### 2.1 Overview

Extended the JMeter MCP correlation analysis engine to automatically detect and generate extractors and pre-processors for Microsoft Entra ID (formerly Azure AD) authentication flows. Previously, EntraID login flows required 10+ manual debugging iterations to identify missing correlations. This enhancement automates the detection of EntraID-specific dynamic values during script generation from HAR or Playwright network captures.

### 2.2 EntraID-Specific Constants

New constant sets in `constants.py` categorizing EntraID fields and patterns:

| Constant Set | Purpose |
|--------------|---------|
| `ENTRA_CONFIG_FIELDS` | Fields from `$Config` JavaScript objects (`sFT`, `sCtx`, `canary`, `sessionId`, etc.) |
| `ENTRA_CREDENTIAL_TYPE_FIELDS` | Fields from the GetCredentialType API response (`FederationRedirectUrl`, etc.) |
| `WSFED_FORM_FIELDS` | WS-Federation hidden form field names (`wresult`, `wctx`, `wa`, `wtrealm`) |
| `OPENAM_TOKEN_FIELDS` | OpenAM/ForgeRock token fields (`authId`, `nonce`, `cdssoToken`) |

### 2.3 New Extraction Functions

Four new extraction functions in `extractors.py` targeting EntraID response patterns:

| Function | What It Extracts |
|----------|-----------------|
| `extract_from_entra_config()` | `$Config` JS object fields and GetCredentialType JSON responses, including nested URL parsing for `estsrequest` from `FederationRedirectUrl` |
| `extract_from_body_redirect_urls()` | OAuth parameters from `href`, `<meta refresh>`, and `window.location` assignments in HTML |
| `extract_from_html_form_post()` (extended) | WS-Federation form fields (`wresult`, `wctx`) and tenant GUIDs from form action URLs |
| `extract_from_json_body()` (extended) | OpenAM token fields (`authId`, `nonce`, `cdssoToken`) via top-level JSON scan |

### 2.4 EntraID Flow Detection

A new `detect_entra_flow()` function in `extractors.py` that identifies EntraID authentication flows based on URL patterns, response content, and headers. When detected, the script generator attaches EntraID-specific pre-processors to the appropriate samplers.

### 2.5 New Pre-Processors

Two new JMeter pre-processor builders in `pre_processor.py`:

| Pre-Processor | Type | Purpose |
|---------------|------|---------|
| `entra_state_preprocessor` | JSR223 (Groovy) | Generates MSAL-format `oauth_state` as base64url-encoded JSON `{id:UUID, meta:{interactionType:"redirect"}}` |
| `entra_wsfed_cookie_preprocessor` | BeanShell | Injects `ESTSWCTXFLOWTOKEN` and `AADSSO` cookies into the CookieManager before WS-Fed form submission |

Both are registered in the component registry and can be added via `add_jmeter_component`.

### 2.6 Boundary Extractor Support

Added `boundary_extractor` support across the pipeline for extracting large values (e.g., WS-Federation `wresult` SAML tokens) that are too large for regex extraction:

- `naming.py` — Generates `left_boundary`/`right_boundary` configuration with WS-Fed-specific templates
- `extractor_helpers.py` — Creates `BoundaryExtractor` JMeter elements from the naming config
- `correlation_config.yaml` — New `response_wsfed_form: "boundary_extractor"` extractor type mapping

### 2.7 Script Generator Integration

Updated `script_generator.py` with EntraID-aware logic:

- Calls `detect_entra_flow()` during script generation
- Inserts `entra_state_preprocessor` on authorize samplers
- Inserts `entra_wsfed_cookie_preprocessor` on WS-Fed form submission samplers
- Works in both structured (controller-based) and flat generation modes

### 2.8 Files Modified

| File | Changes |
|------|---------|
| `jmeter-mcp/services/correlations/constants.py` | Added 4 EntraID constant sets |
| `jmeter-mcp/services/correlations/extractors.py` | Added 2 new extraction functions, extended 2 existing ones, added `detect_entra_flow()` |
| `jmeter-mcp/services/correlations/utils.py` | Extended `walk_json` to match `OAUTH_TOKEN_FIELDS` |
| `jmeter-mcp/services/correlations/naming.py` | Added boundary extractor config generation and WS-Fed boundary templates |
| `jmeter-mcp/correlation_config.example.yaml` | Added EntraID extractor type mappings and regex templates |
| `jmeter-mcp/services/helpers/extractor_helpers.py` | Added `boundary_extractor` case in extractor element creation |
| `jmeter-mcp/services/jmx/pre_processor.py` | Added 2 new pre-processor builder functions |
| `jmeter-mcp/services/jmx/component_registry.py` | Registered `entra_state_preprocessor` and `entra_wsfed_cookie_preprocessor` |
| `jmeter-mcp/services/script_generator.py` | Added EntraID flow detection and pre-processor insertion logic |

---

## 3. EntraID Debugging Skill

### 3.1 Overview

A new dedicated Cursor Skill (`.cursor/skills/jmeter-entraid-debugging/SKILL.md`) providing domain-specific knowledge for debugging JMeter scripts that interact with Microsoft Entra ID authentication flows. This skill is a companion to the general `jmeter-debugging` skill — the debugging skill defines the iterative workflow, while this skill provides the EntraID-specific diagnosis patterns, component references, and sampler sequences.

### 3.2 Why a Separate Skill

- The general debugging skill handles any JMeter failure type; EntraID knowledge is only relevant during auth/login flow debugging
- Skills should stay focused and under 500 lines (this skill is 217 lines)
- Separation of concerns: agents use `jmeter-debugging` for *how* to debug and `jmeter-entraid-debugging` for *what* to look for in EntraID flows

### 3.3 Skill Content

| Section | Purpose |
|---------|---------|
| Flow Recognition | Table of indicators that identify an EntraID flow (URL patterns, response markers) |
| Diagnosis Patterns | 8 patterns ordered by flow sequence: `response_mode=fragment`, BssoInterrupt, `$Config` extraction, GetCredentialType, OpenAM chain, WS-Fed wresult, cookie injection, MSAL state |
| Available Components | 7 JMeter MCP components relevant to EntraID with their `add_jmeter_component` types |
| Typical Sampler Sequence | 13-step reference table from authorize to application token |
| Extractor Quick Reference | 20 extractors with exact variable names, types, and expressions |
| Network Capture Limitation | Documents the known HAR/Playwright empty-response limitation for EntraID flows |

### 3.4 Cross-Reference

A one-line cross-reference was added to the `jmeter-debugging` skill's Common Diagnosis Patterns section, directing agents to the EntraID skill when they encounter EntraID patterns during triage.

### 3.5 Files Created / Modified

| File | Purpose |
|------|---------|
| `.cursor/skills/jmeter-entraid-debugging/SKILL.md` | New EntraID debugging skill with diagnosis patterns and component references |
| `.cursor/skills/jmeter-debugging/SKILL.md` | Added cross-reference to the EntraID skill |

---

## 4. SharePoint MCP Server

### 4.1 Overview

A new MCP server (`sharepoint-mcp`) that enables performance test engineers to upload artifacts (files, folders, reports) to SharePoint document libraries directly from Cursor agent conversations. Built with FastMCP 2.0, it reuses the browser-based authentication pattern from `msteams-mcp` to work in environments without Azure AD app registration access.

### 4.2 Authentication: Dual Auth Strategy

The initial implementation used Bearer token interception from Playwright network requests. Smoke testing on a personal small business tenant revealed that some SharePoint tenants use **cookie-only authentication** (FedAuth/rtFa) for `_api/` calls and never emit SharePoint-scoped Bearer tokens during browser sessions.

The fix introduced a **Dual Auth Strategy** that transparently supports both authentication modes:

| Auth Mode | Mechanism | When Used |
|-----------|-----------|-----------|
| **Bearer** | `Authorization: Bearer` header with JWT audience validated to `*.sharepoint.com` | Tenants that issue SP-scoped tokens in browser requests |
| **Cookie** | `Cookie: FedAuth=...; rtFa=...` header extracted from Playwright session state | Tenants that rely on session cookies for `_api/` calls |

Key improvements:

- **JWT audience validation** — rejects Graph-scoped or other wrong-audience tokens that were previously cached as valid
- **Probe-based verification** — validates cached tokens with a lightweight API call before reporting authentication success
- **Automatic 401 retry** — if Bearer auth returns 401, retries once with cookie auth before failing
- **Heartbeat logging** — logs progress every 30 seconds during the 5-minute manual login wait

### 4.3 MCP Tools (10 Tools)

| Tool | Category | Description |
|------|----------|-------------|
| `sharepoint_login` | Auth | Authenticate via SSO, headless, or visible browser. Reports active `authMode`. |
| `sharepoint_status` | Auth | Diagnostic snapshot of session, token, cookie, and auth mode state |
| `sharepoint_upload_file` | File Ops | Upload a single file (auto-chunked above 250 MB) |
| `sharepoint_upload_folder` | File Ops | Upload an entire local folder recursively, preserving directory structure |
| `sharepoint_download_file` | File Ops | Download a file from SharePoint to local disk |
| `sharepoint_create_folder` | Folder Ops | Create a folder (and parents) in a document library |
| `sharepoint_list_folder` | Folder Ops | List files and subfolders with metadata |
| `sharepoint_list_libraries` | Discovery | List all document libraries in a SharePoint site |
| `sharepoint_search` | Discovery | Search content using KQL (Keyword Query Language) |
| `sharepoint_get_me` | Discovery | View authenticated user's profile from JWT claims |

### 4.4 Chunked Upload

Files exceeding the configurable `max_upload_size_mb` threshold (default 250 MB) are automatically uploaded using SharePoint's chunked upload API (`StartUpload` / `ContinueUpload` / `FinishUpload`). Chunk size is configurable (default 10 MB) with progress logging per chunk.

### 4.5 Cursor Skill: SharePoint Upload

A new Cursor Skill (`.cursor/skills/sharepoint-upload/SKILL.md`) orchestrates the artifact upload workflow:

1. Authenticate via `sharepoint_login`
2. Validate the destination folder exists (or create it)
3. Upload file(s) via `sharepoint_upload_file` or `sharepoint_upload_folder`
4. Optionally send an MS Teams notification via the `msteams-mcp` server

A companion Teams notification template (`msteams-mcp/templates/default-notification-sharepoint-upload.md`) was added for upload-complete notifications.

### 4.6 Security Model

- AES-256-GCM encryption at rest for session state and token cache
- Machine-bound key derivation via scrypt (hostname + username)
- Encrypted files are useless on another machine or user account
- No credentials stored in plaintext; no `.env` files required

### 4.7 Files Created

| File | Purpose |
|------|---------|
| `sharepoint-mcp/sharepoint.py` | FastMCP server entry point with 10 tool definitions |
| `sharepoint-mcp/services/auth_manager.py` | Three-layer auth orchestration with dual auth strategy |
| `sharepoint-mcp/services/browser_auth.py` | Playwright navigation, audience-aware token interception, login detection |
| `sharepoint-mcp/services/browser_context.py` | Persistent Playwright browser context management |
| `sharepoint-mcp/services/token_extractor.py` | JWT extraction, audience validation, user profile parsing |
| `sharepoint-mcp/services/session_store.py` | Encrypted session persistence with cookie extraction |
| `sharepoint-mcp/services/sharepoint_api.py` | SharePoint `_api/` REST client with chunked upload support |
| `sharepoint-mcp/services/crypto.py` | AES-256-GCM encryption primitives |
| `sharepoint-mcp/services/errors.py` | Error codes, Result monad, HTTP error classification |
| `sharepoint-mcp/utils/config.py` | Platform-aware YAML config loader |
| `sharepoint-mcp/config.example.yaml` | Configuration template |
| `sharepoint-mcp/pyproject.toml` | Python project metadata and dependencies |
| `sharepoint-mcp/README.md` | Full documentation |
| `.cursor/skills/sharepoint-upload/SKILL.md` | Cursor skill for artifact upload workflow |
| `msteams-mcp/templates/default-notification-sharepoint-upload.md` | Teams notification template for upload completion |
| `docs/bugs/sharepoint-mcp-bearer-token-interception-failure.md` | Bug report documenting the cookie-auth discovery |

---

## 5. FastMCP v3 Migration

### 5.1 Overview

Upgraded all 9 Python MCP servers from FastMCP v2.x to **FastMCP v3.4.2**. This is a major framework upgrade that enables server composition, proxy mounting, and HTTP transport — laying the foundation for PerfPilot Hub and future Docker deployment.

### 5.2 What Changed

| Aspect | Before (v2) | After (v3.4.2) |
|--------|-------------|-----------------|
| Framework version | FastMCP 2.x | FastMCP 3.4.2 |
| Server launch | `uv run <server>.py` | Direct venv Python: `.venv\Scripts\python.exe <server>.py` |
| Cursor `mcp.json` | `uv` command with `--directory` args | Direct path to venv Python with `cwd` |
| Composition support | Not available | `mount()` + `create_proxy()` for gateway composition |
| Transport | stdio only | stdio + HTTP (streamable) |
| Tool visibility | All tools always visible | Tag-based visibility (`mcp.disable(tags={"deprecated"})`) |

### 5.3 Servers Upgraded

All 9 Python MCP servers were migrated:

| Server | Package Version |
|--------|----------------|
| JMeter MCP | FastMCP 3.4.2 |
| BlazeMeter MCP | FastMCP 3.4.2 |
| Datadog MCP | FastMCP 3.4.2 |
| PerfAnalysis MCP | FastMCP 3.4.2 |
| PerfReport MCP | FastMCP 3.4.2 |
| Confluence MCP | FastMCP 3.4.2 |
| PerfMemory MCP | FastMCP 3.4.2 |
| MS Teams MCP | FastMCP 3.4.2 |
| SharePoint MCP | FastMCP 3.4.2 |

### 5.4 Breaking Changes

- **Cursor `mcp.json`** — All entries updated from `uv run` to direct venv Python paths. Users must recreate virtual environments and install dependencies fresh.
- **`uv.lock` files** — Regenerated for all servers to reflect updated dependency trees.
- **Deprecated tools** — Servers now use `mcp.disable(tags={"deprecated"})` to hide legacy tools from tool listings while preserving backward compatibility.

### 5.5 Migration Steps for Users

1. Delete all existing `.venv` and `__pycache__` folders
2. Recreate venvs: `python -m venv .venv` in each server directory
3. Install dependencies: `.venv\Scripts\pip.exe install -r requirements.txt`
4. Update `mcp.json` to use direct venv Python paths (see each server's README)

---

## 6. PerfPilot Hub — Super MCP Gateway

### 6.1 Overview

Introduced **PerfPilot Hub** (`gateway-mcp/`) — a single MCP gateway that composes all 9 performance testing MCP servers behind one endpoint. Built on FastMCP v3's `create_proxy()` composition, it spawns each server as an isolated subprocess while presenting a unified tool interface to AI agents.

> "Connect your AI agent to **PerfPilot Hub** and get the full performance testing toolchain through one MCP endpoint."

### 6.2 Architecture

- **One endpoint** — 99 tools from 9 servers accessible through a single MCP connection
- **Full process isolation** — each server runs as its own subprocess with its own venv via `create_proxy()`
- **No code changes** — existing servers are completely untouched; PerfPilot Hub is purely additive
- **Configurable** — enable/disable individual servers via `config.yaml`
- **Transport-agnostic** — stdio for local Cursor, HTTP for future Docker/A2A deployment

### 6.3 Namespace Mapping

All tools are prefixed with their server namespace:

| Server | Namespace | Example Tool |
|--------|-----------|--------------|
| JMeter | `jmeter` | `jmeter_generate_jmeter_script` |
| BlazeMeter | `blazemeter` | `blazemeter_get_workspaces` |
| Datadog | `datadog` | `datadog_collect_host_metrics` |
| PerfAnalysis | `perfanalysis` | `perfanalysis_analyze_test_results` |
| PerfReport | `perfreport` | `perfreport_create_performance_test_report` |
| Confluence | `confluence` | `confluence_publish_page` |
| PerfMemory | `perfmemory` | `perfmemory_store_debug_session` |
| MS Teams | `msteams` | `msteams_teams_send_message` |
| SharePoint | `sharepoint` | `sharepoint_sharepoint_upload_file` |

### 6.4 Configuration

Platform-specific config support following the same pattern as all other MCPs:

- `config.yaml` — user's local config (gitignored)
- `config.windows.yaml` / `config.mac.yaml` — OS-specific overrides (gitignored)
- `config.example.yaml` — public reference template (committed)

Optional `ssl_cert_file` setting for environments with HTTPS-intercepting proxies (Norton 360, Zscaler, corporate proxies).

### 6.5 Future Ecosystem

| Component | Purpose | Status |
|-----------|---------|--------|
| 🛩️ **PerfPilot Hub** | MCP Gateway — single endpoint to all perf tools | ✅ Complete |
| 🤖 **PerfPilot Orchestrator** | A2A Server — external AI Agent communication | 📋 Planned |
| 🧠 **PerfMemory DB** | PostgreSQL + pgvector + Apache AGE | ✅ Exists |
| 🐳 **Docker Deployment** | Containerized hub + database | 📋 Planned |

### 6.6 Files Created

| File | Purpose |
|------|---------|
| `gateway-mcp/gateway.py` | FastMCP gateway composing all servers via `create_proxy()` |
| `gateway-mcp/utils/__init__.py` | Package marker |
| `gateway-mcp/utils/config.py` | Platform-aware YAML config loader |
| `gateway-mcp/config.yaml` | Local gateway config (gitignored) |
| `gateway-mcp/config.example.yaml` | Public config template |
| `gateway-mcp/requirements.txt` | Single dependency: `fastmcp>=3.4.2,<4` |
| `gateway-mcp/README.md` | Full setup and usage documentation |
| `docs/plans/super-mcp-gateway-implementation.md` | Implementation plan |

---

## Previous Changelogs

| Month | File | Highlights |
|-------|------|------------|
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills Migration, Cursor Subagents, PerfMemory MCP + AGE Graph, MS Teams MCP, KPI Analysis, JMeter Script Validator, Structure Export & HAR-JMX Comparison |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL Editing Tools, Correlation Analysis v0.6/v0.7, AI-Assisted Debugging, Artifact Path Alignment, BlazeMeter Shared Folders |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI Adapter, HAR Adapter, Centralized SLA Config, JMeter Log Analysis, Bottleneck Analyzer v0.2, Multi-Session Artifacts |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-Assisted Report Revision, Datadog Dynamic Limits, Report Enhancements, New Charts |

---

*Last Updated: May 31, 2026*
