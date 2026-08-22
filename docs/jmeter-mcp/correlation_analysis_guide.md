# Correlation Analysis Guide

This document explains how the JMeter MCP Server's correlation analysis engine detects, classifies, and parameterizes dynamic values in captured HTTP traffic to produce production-ready JMeter test scripts.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Detection Pipeline](#detection-pipeline)
   - [Phase 1a: Response-Side Source Extraction](#phase-1a-response-side-source-extraction)
   - [Phase 2: Forward Usage Detection](#phase-2-forward-usage-detection)
   - [Phase 1b: Request-Side OAuth/PKCE Extraction](#phase-1b-request-side-oauthpkce-extraction)
   - [Phase 1c: Token Chain Analysis](#phase-1c-token-chain-analysis)
   - [Phase 1d: Static API Key Header Detection](#phase-1d-static-api-key-header-detection)
   - [Phase 3: Orphan ID Detection](#phase-3-orphan-id-detection)
4. [Value Classification](#value-classification)
5. [Parameterization Strategies](#parameterization-strategies)
6. [Correlation Naming](#correlation-naming)
7. [JMX Script Generation](#jmx-script-generation)
8. [Helper Module Reference](#helper-module-reference)
9. [End-to-End Workflow Example](#end-to-end-workflow-example)

---

## Overview

Performance test scripts need to handle dynamic values -- session tokens, business IDs, CSRF tokens, OAuth codes, and API keys that change between runs. The correlation analysis engine automatically detects these values by analyzing captured HTTP traffic (network capture JSON) and classifying each value by type, source location, and parameterization strategy.

The engine uses a multi-phase forward-only scanning algorithm:

1. **Extract** candidate values from responses and requests
2. **Match** those values against subsequent requests (forward-only)
3. **Classify** each match with a parameterization strategy
4. **Output** a structured `correlation_spec.json` for downstream JMX generation

The design is **vendor-agnostic** -- no infrastructure-specific references, variable names, or filtering logic appear in the codebase.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Network Capture JSON                  │
│         (Step-grouped HTTP request/response data)       │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    analyzer.py                          │
│              (Orchestrator — _find_correlations)        │
│                                                         │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 1a: extract_sources() ← extractors.py     │  │
│   │  Responses → headers, redirects, JSON, cookies,  │  │
│   │             HTML form_post                       │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 2: find_usages() ← matchers.py            │  │
│   │  Forward-only scan: URL, headers, POST body      │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 1b: Request-side OAuth/PKCE               │  │
│   │  extract_oauth_params_from_request_urls()        │  │
│   │  extract_oauth_params_from_request_body()        │  │
│   │  extract_oauth_from_request_headers()            │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 1c: detect_token_exchanges()              │  │
│   │  Sequential /oauth/token chain linking           │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 1d: detect_static_api_key_headers()       │  │
│   │  Generic -key$ header pattern matching           │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 3: detect_orphan_ids() ← matchers.py      │  │
│   │  Values in requests with no identifiable source  │  │
│   │  (SKIP_VALUES sentinel filter applied)           │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  classify_parameterization_strategy()            │  │
│   │  ← classifiers.py                                │  │
│   └───────────────────────┬──────────────────────────┘  │
│                           ▼                             │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Phase 4: generate_naming() ← naming.py          │  │
│   │  Algorithmic variable naming + de-duplication    │  │
│   └──────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
    correlation_spec.json + correlation_naming.json
                         │
                    (optional)
                         ▼
         ┌───────────────────────────────┐
         │  Human review: adjust names,  │
         │  remove false positives       │
         └───────────────┬───────────────┘
                         │
                         ▼
              correlation_naming.json
                         │
                         ▼
         ┌───────────────────────────────┐
         │  script_generator.py          │
         │  + helper modules             │
         │  → parameterized JMX          │
         └───────────────────────────────┘
```

### Module Responsibilities

| Module | Location | Role |
|--------|----------|------|
| `analyzer.py` | `services/correlations/` | Orchestrator: loads data, runs all phases, builds `correlation_spec.json` |
| `extractors.py` | `services/correlations/` | Phase 1a/1b/1c/1d: source value extraction from responses and requests |
| `matchers.py` | `services/correlations/` | Phase 2 & 3: forward usage detection, orphan ID detection |
| `classifiers.py` | `services/correlations/` | Value type classification, parameterization strategy rules |
| `naming.py` | `services/correlations/` | Algorithmic naming engine: config loading, camelCase→snake_case, variable generation |
| `constants.py` | `services/correlations/` | Regex patterns, header lists, OAuth parameter sets, `SKIP_VALUES` sentinel filter |
| `utils.py` | `services/correlations/` | URL normalization, value matching, JSON walking, domain exclusion |
| `correlation_config.yaml` | `jmeter-mcp/` | Custom naming configuration: OAuth params, token fields, timestamp patterns, extractor templates |
| `script_generator.py` | `services/` | Consumes spec + naming files to produce parameterized JMX |
| `extractor_helpers.py` | `services/helpers/` | Correlation extractor element creation |
| `substitution_helpers.py` | `services/helpers/` | Variable substitution in URLs, bodies, headers |
| `orphan_helpers.py` | `services/helpers/` | Orphan UDV extraction, static header config |
| `hostname_helpers.py` | `services/helpers/` | Hostname parameterization |

---

## Detection Pipeline

### Input: Network Capture JSON

The input is a step-grouped JSON file produced by the Playwright-based browser capture tool. Each top-level key is a step label (e.g., `"Step 1 - Login"`), and its value is an array of HTTP entries:

```json
{
  "Step 1 - Login": [
    {
      "url": "https://app.example.com/api/login",
      "method": "POST",
      "headers": { "Content-Type": "application/json" },
      "post_data": "{\"username\":\"user1\"}",
      "status": 200,
      "response_headers": { "x-request-id": "abc-123-def" },
      "response": "{\"userId\": 42, \"sessionToken\": \"eyJ...\"}"
    }
  ]
}
```

Before analysis, entries are flattened into a sequential list ordered by `(step_number, original_order)` and filtered against a configurable domain exclusion list (removes APM, analytics, and CDN noise).

---

### Phase 1a: Response-Side Source Extraction

**Module:** `extractors.py` → `extract_sources()`

This phase scans every HTTP response to find values that might be reused in later requests. It checks five source locations in order:

#### 1. Response Headers

Scans response headers for names ending in correlation-related suffixes (`-id`, `-uuid`, `transactionid`, `correlationid`, `requestid`, `traceid`, `spanid`). Skips known non-ID headers (content-type, cache-control, etc.) defined in `SKIP_HEADERS_SOURCE`.

**Example:** A header `x-request-id: abc-123-def` is extracted as a `correlation_id` candidate.

#### 2. Redirect URLs (Location Header)

Parses the `Location` response header to extract query parameters. OAuth parameters (`code`, `state`, `nonce`, `id_token`, etc.) are flagged by name. Other parameters are evaluated by value format using `is_id_like_value()`.

**Example:** `Location: https://app.example.com/callback?code=AUTH_CODE_123&state=XYZ` extracts both `code` and `state`.

#### 3. JSON Response Bodies

Walks the JSON response tree (up to `MAX_JSON_DEPTH=5`) looking for:
- **OAuth token fields** first (`access_token`, `id_token`, `refresh_token`, etc.) -- matched by field name from `OAUTH_TOKEN_FIELDS`
- **ID-like fields** second -- matched by key pattern (`*id`, `*_id`, `*Id`, `uuid`, `guid`) via `ID_KEY_PATTERNS` regex, then validated by value format (`is_id_like_value`)

Deduplication ensures OAuth token fields extracted by name are not re-extracted by the generic ID walker.

#### 4. Set-Cookie Headers

Extracts nonce values from cookies whose names contain keywords like `nonce` or `csrftoken` (case-insensitive). These are flagged as `oauth_nonce` candidates. JMeter's Cookie Manager handles standard cookies automatically; this targets only nonce/CSRF values used in custom request headers.

Cookie names are sanitized to vendor-agnostic variable names (e.g., `CompanyNonce` becomes `nonce`, environment suffixes like `_uat` are stripped).

#### 5. HTML Form Post Responses

Detects the OAuth 2.0 `response_mode=form_post` pattern where tokens are returned in hidden HTML form fields:

```html
<input type="hidden" name="id_token" value="eyJ0eXAi..."/>
<input type="hidden" name="code" value="AUTH_CODE"/>
```

Extracts `id_token`, `code`, `state`, `access_token`, and `token_type` from hidden inputs using regex patterns that handle varying HTML attribute orders.

#### Deduplication

After all five extraction passes, candidates are deduplicated by value. When the same value appears in multiple locations (e.g., in both a redirect URL and a JSON body), only the **first occurrence** (earliest source) is kept.

---

### Phase 2: Forward Usage Detection

**Module:** `matchers.py` → `find_usages()`

For each candidate extracted in Phase 1a, the engine scans all **subsequent** entries (entries with a higher index than the source) to find where the value is reused. This enforces a forward-only constraint: a value can only flow from an earlier response to a later request.

Usage is searched in three locations per entry:

| Location | Method | Notes |
|----------|--------|-------|
| Request URL | Path segments + query parameters | URL-decoded comparison via `value_matches()` |
| Request Headers | All headers except HTTP plumbing | Skips headers in `SKIP_HEADERS_USAGE` |
| Request Body | JSON values + plain text | JSON bodies are walked for exact matches; text bodies use substring matching |

#### Value Matching Algorithm

The `value_matches()` function (`utils.py`) handles URL encoding transparently:

1. Normalizes both needle and haystack to include URL-decoded forms
2. For **short values** (≤4 chars): uses word-boundary regex to prevent false positives (e.g., `"11"` matching inside a UUID)
3. For **longer values**: uses substring matching (safe due to length)

#### Correlation Emission Rules

A correlation is emitted when:
- The candidate has **at least one usage** in a subsequent request, OR
- The candidate is an **OAuth form_post token** (`id_token`, `code`, `access_token` from HTML hidden fields) even without detected usages -- these tokens are typically used as `Authorization: Bearer` headers which may not appear in the capture

---

### Phase 1b: Request-Side OAuth/PKCE Extraction

**Module:** `extractors.py` → `extract_oauth_params_from_request_urls()`, `extract_oauth_params_from_request_body()`, `extract_oauth_from_request_headers()`

Browser-captured traffic often has empty response bodies (the browser consumes them internally). This phase detects OAuth parameters directly from **request** data when no response-side source was captured.

#### URL Parameter Extraction

Scans request URL query strings for parameters in `OAUTH_URL_PARAMS`:
- Standard OAuth 2.0: `client_id`, `redirect_uri`, `response_type`, `scope`, `state`, `response_mode`, `nonce`
- PKCE (RFC 7636): `code_challenge`, `code_challenge_method`
- Tokens: `code`, `id_token`, `access_token`, `cdssotoken`

Handles **nested URL-encoded parameters** (up to 3 levels deep) by recursively parsing values of `goto=`, `redirect_uri=`, `return_url=`, etc. This is critical for SSO redirect chains where the authorization URL is encoded inside a login redirect.

#### POST Body Extraction

Parses form-urlencoded POST bodies for OAuth token exchange parameters. Requires `grant_type` to be present (this is the signature of a token endpoint request). Extracts:

| grant_type | Extracted Parameters |
|------------|---------------------|
| `authorization_code` | `code`, `code_verifier`, `client_id`, `redirect_uri` |
| `urn:ietf:params:oauth:grant-type:token-exchange` | `subject_token`, `client_id`, `scope` |
| `refresh_token` | `refresh_token`, `client_id` |

The `grant_type` value itself is not emitted as a candidate (it's static metadata), but is attached to each candidate as `detected_grant_type` for flow classification.

#### Request Header Extraction

Scans for dynamic values in custom OAuth/SSO headers defined in `OAUTH_INTEREST_HEADERS`:
- `x-cdsso-nonce` → classified as `sso_nonce`
- `x-csrf-token`, `x-xsrf-token` → classified as `csrf_token`

#### Deduplication Against Phase 1a

Values already detected from response-side extraction are skipped. Each detected value is also added to `known_source_values` to prevent Phase 3 from re-flagging it as an orphan.

#### Parameterization Strategy Assignment

Each request-side candidate is assigned a strategy from `_REQUEST_SIDE_STRATEGIES` based on its `value_type`:

| Value Type | Strategy | Reason |
|------------|----------|--------|
| `pkce_code_challenge` | `pkce_preprocessor` | Generate via JSR223 PreProcessor |
| `pkce_code_verifier` | `pkce_preprocessor` | Generate via JSR223 PreProcessor |
| `oauth_code` | `infer_from_prior_response` | Extract from redirect Location header |
| `oauth_subject_token` | `infer_from_prior_response` | Extract `$.access_token` from prior token response |
| `oauth_client_id` | `user_defined_variable` | Static per environment |
| `oauth_redirect_uri` | `user_defined_variable` | Static per environment |
| `oauth_scope` | `user_defined_variable` | Static per environment |
| `oauth_response_type` | `user_defined_variable` | Static per flow |

---

### Phase 1c: Token Chain Analysis

**Module:** `extractors.py` → `detect_token_exchanges()`

Detects sequential `/oauth/token` exchanges where each request's `subject_token` should be extracted from the prior exchange's `$.access_token` response.

**Algorithm:**

1. Scan all POST requests for form-urlencoded bodies containing `grant_type`
2. Classify each by flow type (`pkce_or_auth_code`, `token_exchange`, `refresh_token`, `client_credentials`)
3. Assign a `sequence_position` (0-based) to each exchange
4. For each exchange at position N > 0 that contains a `subject_token`:
   - Link it to exchange at position N-1
   - Emit a `token_chain` correlation with `inferred_json_path: $.access_token`

This handles the common pattern where an authorization code is exchanged for an access token, which is then exchanged for a different access token (token-exchange grant), and potentially refreshed later.

**Example chain:**

```
Exchange 0: grant_type=authorization_code  → access_token_1
Exchange 1: grant_type=token-exchange, subject_token=access_token_1  → access_token_2
Exchange 2: grant_type=refresh_token, refresh_token=...  → access_token_3
```

---

### Phase 1d: Static API Key Header Detection

**Module:** `extractors.py` → `detect_static_api_key_headers()`

Detects request headers carrying static API keys that should be parameterized via User Defined Variables.

**Algorithm:**

1. Scan all request headers for names matching the regex `-key$` (case-insensitive) -- defined as `API_KEY_HEADER_RE` in `constants.py`
2. For each matching header name, collect all distinct values observed across all requests
3. If a header has **exactly one consistent value**, flag it as a static API key

This catches headers like:
- `x-api-key`
- `x-functions-key`
- `my-service-key`

The pattern is intentionally generic: any header ending in `-key` is a candidate. Headers with multiple different values are not flagged (they may be dynamic and need a different parameterization approach).

Each detected static key is added to `known_source_values` to prevent Phase 3 orphan detection from re-flagging the value.

---

### Phase 3: Orphan ID Detection

**Module:** `matchers.py` → `detect_orphan_ids()`

After all other phases have run, this phase catches ID-like values in request URLs that have **no identifiable source** -- they did not appear in any prior response, OAuth flow, or static header.

**Algorithm:**

1. Scan all request URLs for ID-like values:
   - **Path segments**: numeric IDs or GUIDs
   - **Query parameters**: values that are numeric/GUID format, OR parameters whose name matches ID patterns (e.g., `appGuid`, `userId`) with non-trivial values (length ≥ 2)
2. **Sentinel value filter (`SKIP_VALUES`)**: skip values that represent null/empty/boolean sentinels rather than real dynamic IDs. The following values are excluded (case-insensitive): `"null"`, `"true"`, `"false"`, `""`, `"none"`, `"undefined"`, `"0"`, `"-1"`. This filter is also applied defense-in-depth in the JSON walkers (`utils.py`), source extractors (`extractors.py`), and usage matchers (`matchers.py`).
3. Skip values already in `known_source_values` (detected by earlier phases)
4. Deduplicate by value (keep first occurrence)

Orphan IDs are classified as `low` confidence since no source was found.

#### Timestamp Detection

Orphan numeric values that are exactly 13 digits and fall within the epoch millisecond range (2000-01-01 to 2050-01-01) are reclassified as `timestamp` type. These are commonly SignalR cache-busting parameters. Their parameterization strategy is set to `timestamp_preprocessor` (JMeter's `${__time()}` function or a JSR223 PreProcessor).

---

## Value Classification

**Module:** `classifiers.py` → `classify_value_type()`

Every extracted value is classified into a type category:

| Type | Detection Criteria |
|------|-------------------|
| `timestamp` | 13-digit integer in epoch ms range (946684800000–2524608000000) |
| `business_id_numeric` | Numeric string or integer (not in timestamp range) |
| `business_id_guid` | Matches UUID format (`8-4-4-4-12` hex) |
| `oauth_token` | Matches JWT format (`base64url.base64url.base64url`) |
| `opaque_id` | Alphanumeric string > 20 chars |
| `string_id` | Other string identifiers |
| `oauth_state`, `oauth_nonce`, etc. | Classified by parameter name (Phase 1b) |
| `api_key` | Classified by header pattern (Phase 1d) |

### ID-Like Value Filter

`is_id_like_value()` determines whether a value is worth tracking as a correlation candidate:

- **Integers**: must be ≥ 10 (filters out trivial small numbers)
- **Numeric strings**: must meet `MIN_NUMERIC_ID_LENGTH` (2)
- **GUIDs**: always accepted
- **Alphanumeric strings**: must be 8–128 chars matching `[A-Za-z0-9_-]+`

---

## Parameterization Strategies

**Module:** `classifiers.py` → `classify_parameterization_strategy()`

Each correlation is assigned a parameterization strategy that tells the JMX generator how to handle the value:

| Strategy | When Applied | JMeter Element |
|----------|-------------|----------------|
| `extract_and_reuse` | Value found in prior response | JSON Extractor or Regex Extractor |
| `user_defined_variable` | Static value or 1-2 occurrences with no source | User Defined Variables element |
| `csv_dataset` | ≥ 3 occurrences with no source | CSV Data Set Config |
| `pkce_preprocessor` | PKCE `code_challenge` / `code_verifier` | JSR223 PreProcessor (SHA-256 + Base64URL) |
| `timestamp_preprocessor` | 13-digit epoch ms timestamp | JSR223 PreProcessor or `${__time()}` |
| `infer_from_prior_response` | Token chain `subject_token`, OAuth `code` | JSON Extractor on inferred JSONPath |
| `extract_for_bearer` | OAuth form_post token with no detected usage | Regex Extractor (for Bearer header) |

---

## Correlation Naming

The correlation analysis engine produces raw correlation data with auto-generated IDs (`corr_1`, `corr_2`, ...). Before JMX generation, each correlation is mapped to a meaningful JMeter variable name.

### Algorithmic Naming (Default)

The `analyze_network_traffic` tool **automatically generates** `correlation_naming.json` as Phase 4 of the pipeline. The naming engine (`services/correlations/naming.py`) applies deterministic rules:

1. **Custom mappings** from `correlation_config.yaml` (highest priority, user-local)
2. **OAuth parameter lookups** (e.g., `state` → `oauth_state`)
3. **OAuth token field lookups** (e.g., `cdssotoken` → `cdsso_token`)
4. **Timestamp URL-pattern matching** (e.g., SignalR URLs → `signalr_timestamp_N`)
5. **Algorithmic camelCase → snake_case conversion** (e.g., `entityGuid` → `entity_guid`)
6. **De-duplication suffixes** when the same base name is assigned to multiple correlations

### Configuration: `correlation_config.yaml`

The naming engine reads customizable mappings from `jmeter-mcp/correlation_config.yaml`. If that file does not exist, it falls back to `jmeter-mcp/correlation_config.example.yaml`. The config supports:

- `naming.custom_mappings` — application-specific field-to-variable overrides
- `naming.oauth_params` — OAuth parameter → variable name table
- `naming.oauth_token_fields` — token response field → variable name table
- `naming.timestamp_patterns` — URL substring → timestamp variable prefix
- `naming.extractor_types` — source location → JMeter extractor type
- `naming.regex_templates` — regex expression templates per source location

### Optional Human Review

After auto-generation, the user can review and adjust `correlation_naming.json`:
- Rename variables for domain clarity
- Remove false positives
- Add entries to `correlation_config.yaml` for recurring patterns

The Cursor Skill at `.cursor/skills/jmeter-correlation-naming/` provides guidance for reviewing and adjusting the auto-generated output.

### Workflow

1. **`analyze_network_traffic`** (MCP tool) → produces `correlation_spec.json` + `correlation_naming.json`
2. **Human reviews** (optional): adjusts variable names or removes false positives
3. **`generate_jmeter_script`** (MCP tool) → consumes both files to produce the JMX

### `correlation_spec.json` Structure

```json
{
  "capture_file": "network_capture_20260303_211835.json",
  "application": "example-app",
  "spec_version": "2.0",
  "analyzer_version": "0.2.0",
  "analysis_timestamp": "2026-03-03T21:19:53.000000",
  "total_steps": 5,
  "total_entries": 47,
  "correlations": [
    {
      "correlation_id": "corr_1",
      "type": "business_id",
      "value_type": "business_id_guid",
      "confidence": "high",
      "correlation_found": true,
      "source": {
        "step_number": 1,
        "entry_index": 3,
        "source_location": "response_json",
        "source_key": "userId",
        "source_json_path": "$.userId",
        "response_example_value": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
      },
      "usages": [
        {
          "usage_number": 1,
          "entry_index": 7,
          "location_type": "request_url_path",
          "request_url": "https://api.example.com/users/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        }
      ],
      "parameterization_hint": {
        "strategy": "extract_and_reuse",
        "extractor_type": "json"
      }
    }
  ],
  "summary": {
    "total_correlations": 12,
    "business_ids": 3,
    "oauth_params": 5,
    "pkce_params": 2,
    "token_chains": 1,
    "static_headers": 1,
    "orphan_ids": 0
  }
}
```

### `correlation_naming.json` Structure

```json
{
  "test_run_id": "example-run",
  "variables": [
    {
      "correlation_id": "corr_1",
      "variable_name": "user_id",
      "extractor_type": "json",
      "extractor_expression": "$.userId"
    },
    {
      "correlation_id": "corr_5",
      "variable_name": "oauth_state",
      "extractor_type": "regex",
      "extractor_expression": "state=([^&]+)"
    }
  ]
}
```

---

## JMX Script Generation

The `script_generator.py` consumes both the `correlation_spec.json` and `correlation_naming.json` to produce a parameterized JMX test plan. The generation pipeline integrates with the helper modules:

### Extractor Placement

For each `extract_and_reuse` correlation with a matching naming entry:
- **JSON Extractor** when `extractor_type: "json"` -- uses JSONPath expression
- **Regex Extractor** when `extractor_type: "regex"` -- uses regex expression

Extractors are attached as child elements of the HTTP Sampler that produces the source response.

### PKCE PreProcessor

When PKCE parameters (`code_challenge`, `code_verifier`) are detected:
1. A JSR223 PreProcessor is created with Groovy code that generates a random `code_verifier`, computes `code_challenge` using SHA-256 + Base64URL
2. The preprocessor is attached to the authorize request
3. Hardcoded PKCE values are replaced with `${code_challenge}` and `${code_verifier}` in URLs, POST bodies, and headers

### Static Header Substitution

For `static_header` correlations:
1. Values are added to the Test Plan's User Defined Variables element
2. In every HTTP Sampler's Header Manager, hardcoded header values are replaced with `${variable_name}` (case-insensitive header name matching)

### Orphan Variable Handling

For `orphan_id` correlations:
1. Values are added to User Defined Variables with their recorded value
2. Hardcoded values are substituted in URLs and POST bodies (both raw and URL-encoded forms)
3. SignalR timestamps (source_key `"_"`) get `${__time()}` instead of the recorded value

### Hostname Parameterization

All hostnames are extracted from request URLs, categorized, and replaced with JMeter variables. This enables environment-specific test execution by changing hostname variables.

---

## Helper Module Reference

### `extractor_helpers.py`

| Function | Purpose |
|----------|---------|
| `_load_correlation_naming()` | Loads and validates `correlation_naming.json` |
| `_build_extractor_map()` | Builds `source_entry_index → extractor_info` lookup |
| `_create_extractor_element()` | Creates JMeter JSON/Regex Extractor XML element |

### `substitution_helpers.py`

| Function | Purpose |
|----------|---------|
| `_build_variable_name_map()` | Maps `correlation_id` → `variable_name` from naming file |
| `_build_substitution_map()` | Maps `recorded_value` → `${variable_name}` for all correlations with usages |
| `_substitute_entry()` | Replaces hardcoded values in URLs and POST bodies |
| `_substitute_pkce_in_entry()` | Replaces PKCE challenge/verifier values |
| `_substitute_static_headers_in_entry()` | Replaces static header values with JMeter variable references |

### `orphan_helpers.py`

| Function | Purpose |
|----------|---------|
| `_extract_orphan_udv_vars()` | Extracts orphan IDs for User Defined Variables |
| `_build_orphan_substitution_map()` | Builds value → variable mapping for orphan substitution |
| `_substitute_orphan_in_entry()` | Replaces orphan values in URLs and POST bodies |
| `_extract_static_header_config()` | Extracts static header correlations for UDV and header substitution |

### `hostname_helpers.py`

| Function | Purpose |
|----------|---------|
| `_extract_hostnames()` | Extracts unique hostnames from all entries |
| `_categorize_hostname()` | Classifies hostname (app, api, auth, cdn, etc.) |
| `_build_hostname_map()` | Maps hostname → JMeter variable name |
| `_substitute_hostname_in_entry()` | Replaces hardcoded hostnames with `${variable}` |

---

## End-to-End Workflow Example

This example walks through a typical OAuth + API key scenario:

### 1. Capture Traffic

Using the Playwright browser capture tool, record a user session that:
- Navigates to a login page
- Authenticates via OAuth 2.0 with PKCE
- Accesses API endpoints with an API key header

### 2. Analyze Correlations

```
MCP Tool: analyze_network_traffic
Input:    test_run_id = "my-test-01"
Output:   correlation_spec.json
```

The analyzer detects:
- `corr_1`: `business_id_guid` from JSON response `$.userId`, used in 3 subsequent requests → `extract_and_reuse`
- `corr_2`: `oauth_client_id` from request URL → `user_defined_variable`
- `corr_3`: `oauth_redirect_uri` from request URL → `user_defined_variable`
- `corr_4`: `pkce_code_challenge` from request URL → `pkce_preprocessor`
- `corr_5`: `oauth_state` from redirect Location header → `extract_and_reuse`
- `corr_6`: `api_key` from static header `x-api-key` → `user_defined_variable`
- `corr_7`: `timestamp` orphan from SignalR `_` parameter → `timestamp_preprocessor`

### 3. Algorithmic Naming (Phase 4)

The naming engine auto-generates `correlation_naming.json` mapping each correlation to a JMeter variable:

| Correlation | Variable Name | Extractor |
|-------------|---------------|-----------|
| `corr_1` | `user_id` | JSON `$.userId` |
| `corr_2` | `oauth_client_id` | UDV (no extractor) |
| `corr_3` | `oauth_redirect_uri` | UDV (no extractor) |
| `corr_4` | `pkce_code_challenge` | PreProcessor (no extractor) |
| `corr_5` | `oauth_state` | Regex `state=([^&]+)` |
| `corr_6` | `x_api_key` | UDV (no extractor) |
| `corr_7` | `signalr_ts` | PreProcessor `${__time()}` |

### 4. Generate JMX

```
MCP Tool: generate_jmeter_script
Input:    test_run_id = "my-test-01"
Output:   ai-generated_script_YYYYMMDD_HHMMSS.jmx
```

The generated JMX includes:
- **User Defined Variables** with `oauth_client_id`, `oauth_redirect_uri`, `x_api_key`, and `signalr_ts`
- **JSON Extractor** on the login response extracting `$.userId` into `${user_id}`
- **Regex Extractor** on the redirect response extracting `state` into `${oauth_state}`
- **JSR223 PreProcessor** generating PKCE `code_verifier` and `code_challenge`
- All subsequent requests use `${user_id}`, `${oauth_state}`, `${x_api_key}`, etc. instead of hardcoded values
- All hostnames parameterized as `${host_app}`, `${host_api}`, `${host_auth}`, etc.

---

*Last Updated: March 18, 2026*
