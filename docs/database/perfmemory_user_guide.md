# PerfMemory and JMeter Debugging — User Guide

## What is This Guide?

This guide shows how **PerfMemory MCP** and **JMeter MCP** work together to create an AI-driven debugging loop for JMeter scripts. PerfMemory stores structured lessons learned from past debugging sessions — symptoms, diagnoses, and fixes — as vector embeddings. When the AI agent encounters a new error, it searches PerfMemory for similar past issues and applies known fixes proactively instead of starting from scratch.

The goal: **generate scripts and fix issues in hours, not days.**

This guide provides example prompts you can use with any AI tool (Cursor, Claude CLI, Codex CLI, etc.) along with the underlying tool calls that the AI agent executes. If your AI tool doesn't auto-detect the right workflow, you can use the tool calls directly.

**Setup References:**

- [PerfMemory MCP README](../perfmemory-mcp/README.md) — server setup, configuration, tool reference
- [JMeter MCP README](../jmeter-mcp/README.md) — JMeter MCP setup and tool reference
- [pgvector Installation Guide](pgvector_installation_guide.md) — database setup with Docker

---

## Prerequisites

Before using PerfMemory with the debugging workflow, make sure:

| Requirement | Details |
|-------------|---------|
| PostgreSQL + pgvector | Running via Docker Compose (`docker/docker-compose.yaml`) |
| Schema applied | Run `schema_openai.sql` or `schema_ollama.sql` against the database |
| PerfMemory MCP configured | `.env` with database credentials and embedding API key, `config.yaml` with search settings |
| JMeter MCP configured | JMeter 5.6+ installed, JMeter MCP server running |
| Both MCP servers in `mcp.json` | Both `user-perfmemory` and `user-jmeter` registered in your IDE |

**Optional — Taxonomy Setup:**

If your team uses standardized application/service naming, copy `perfmemory-mcp/taxonomy.example.yaml` to `perfmemory-mcp/taxonomy.yaml` and customize with your applications, services, and environments. This enables alias resolution during search and ingestion (e.g., searching by "CART" finds results stored under "Shopping Cart").

---

## Skills Reference

The AI agent uses **Skills** to determine what workflow to follow. You don't need to name the skill in your prompt — the agent auto-detects based on your intent. Here are the relevant skills for debugging with memory:

| Skill | Triggers On | What It Does |
|-------|-------------|--------------|
| `jmeter-debugging` | "debug", "troubleshoot", "fix my script", "smoke test failures" | Full iterative debug loop with built-in PerfMemory integration (Steps 0.5, 1.5, 5d, 8e, 10b) |
| `perfmemory` | "search memory", "store lesson", "ingest manifest", "perfmemory stats" | Standalone memory operations: search, store, ingest, maintain |
| `jmeter-hitl-editing` | "analyze script", "add component", "edit sampler" | Manual script editing (no auto-debug loop) |

For the most common use case — debugging a script — just ask the agent to debug. It will automatically search memory, store lessons, and close the session as part of the workflow.

---

## Example Prompts

### A. Full Debug Workflow (Recommended Starting Point)

This is the most common use case. One prompt triggers the full `jmeter-debugging` skill, which integrates PerfMemory automatically.

**Prompt:**

```
Debug my JMeter script for test run {your_test_run_id}.
The system under test is {your_system_name} in the {environment} environment.
```

**Example:**

```
Debug my JMeter script for test run regression-2026-04.
The system under test is OrderPortal in the QA environment.
```

**What the agent does behind the scenes:**

1. Searches PerfMemory for past issues on this system (`find_similar_attempts`)
2. Creates a debug manifest at `artifacts/{test_run_id}/analysis/debug_manifest.md`
3. Opens a PerfMemory session (`store_debug_session`)
4. Runs a 1/1/1 smoke test (`start_jmeter_test`, `get_jmeter_run_status`)
5. Stops early when errors appear (`stop_jmeter_test`)
6. Analyzes the log (`analyze_jmeter_log`)
7. Searches memory for the specific error (`find_similar_attempts`)
8. If a known fix exists: applies it directly. If not: attaches debug post-processor, diagnoses, and fixes.
9. Stores each attempt in PerfMemory (`store_debug_attempt`)
10. Repeats until clean or 5 iterations reached
11. Closes the PerfMemory session (`close_debug_session`)

**Skills used:** `jmeter-debugging` (which internally calls `perfmemory` tools)

---

### B. Search Memory Before Debugging

Use this when you want to check what's in memory before starting a full debug workflow.

**Prompts:**

```
Search perfmemory for OAuth correlation issues on {your_system_name}.
```

```
Before I start debugging, check if we've seen this error before:
Missing correlation for oauth_state on the /authorize endpoint,
java.net.URISyntaxException in the redirect URL.
```

```
What past fixes do we have for authentication failures on {your_system_name}?
```

**Tool call (under the hood):**

```
find_similar_attempts(
  symptom_text      = "Missing Correlations (OAuth Parameters) on /authorize
                       — java.net.URISyntaxException. Unresolved ${oauth_state}
                       in redirect URL query parameter.",
  system_under_test = "{your_system_name}",
  error_category    = "Missing Correlations"
)
```

**Response interpretation:**

| Recommendation | Similarity | What to Do |
|----------------|------------|------------|
| `apply_known_fix` | > 0.85 | Apply the returned fix directly (especially if `confirmed_count > 1`) |
| `review_suggestions` | 0.60 - 0.85 | Review the suggestions and decide which (if any) to apply |
| `no_match` | < 0.60 | No useful matches — proceed with normal debugging |

**Skill used:** `perfmemory` (Workflow A)

---

### C. Store a Debug Session Manually

Use this when you've debugged something outside the automated workflow and want to record the lesson.

**Prompt to create a session:**

```
Create a new perfmemory session for {your_system_name}, test run {your_test_run_id},
script {your_script_name}.jmx in the {environment} environment.
```

**Tool call:**

```
store_debug_session(
  system_under_test = "{your_system_name}",
  test_run_id       = "{your_test_run_id}",
  script_name       = "{your_script_name}.jmx",
  environment       = "{environment}",
  created_by        = "{your_name}",
  system_alias      = "{optional_app_alias}",
  service_name      = "{optional_service_name}"
)
```

**Prompt to store an attempt:**

```
Store this debug attempt for session {session_id}:
- Iteration: 1
- Symptom: Missing correlation for oauth_redirect_uri on GET /authorize,
  caused java.net.URISyntaxException
- Diagnosis: The redirect URL from GET /home contains OAuth parameters
  URL-encoded in the goto query parameter, but no extractors existed
- Fix: Added a Regex Extractor on GET /home to extract oauth_redirect_uri
  from the goto parameter
- Outcome: resolved
```

**Tool call:**

```
store_debug_attempt(
  session_id       = "{session_id}",
  iteration_number = 1,
  symptom_text     = "Missing Correlations (OAuth Parameters) on GET /authorize
                      — java.net.URISyntaxException. Unresolved ${oauth_redirect_uri}
                      in redirect URL. The goto parameter from GET /home contains
                      the OAuth redirect URI URL-encoded but no extractor existed.",
  outcome          = "resolved",
  error_category   = "Missing Correlations",
  severity         = "Critical",
  response_code    = "URISyntaxException",
  sampler_name     = "TC01_S02_GET /authorize",
  diagnosis        = "The redirect URL from GET /home contains OAuth parameters
                      URL-encoded in the goto query parameter but no extractors existed.",
  fix_description  = "Added a Regex Extractor on TC01_S01_GET /home to extract
                      oauth_redirect_uri from the goto query parameter.",
  fix_type         = "add_extractor",
  component_type   = "regex_extractor"
)
```

**Prompt to close the session:**

```
Close perfmemory session {session_id} as resolved. The fix that resolved it
was attempt {attempt_id}.
```

**Tool call:**

```
close_debug_session(
  session_id            = "{session_id}",
  final_outcome         = "resolved",
  resolution_attempt_id = "{attempt_id}",
  notes                 = "OAuth redirect URI correlation was missing from GET /home"
)
```

**Skill used:** `perfmemory` (Workflow B)

---

### D. Ingest a Debug Manifest

Use this to bulk-load lessons from an existing debug manifest file (produced by the `jmeter-debugging` skill) into PerfMemory.

**Prompt:**

```
Ingest the debug manifest from test run {your_test_run_id} into perfmemory.
The system under test is {your_system_name}.
```

**Example:**

```
Ingest the debug manifest from test run regression-2026-04 into perfmemory.
The system under test is OrderPortal.
```

**What the agent does:**

1. Reads `artifacts/{test_run_id}/analysis/debug_manifest.md`
2. Parses the header for session metadata (test run ID, script name, status)
3. Creates a PerfMemory session (`store_debug_session`)
4. For each iteration in the manifest, extracts the symptom, diagnosis, fix, and outcome
5. Builds a structured `symptom_text` and stores each as an attempt (`store_debug_attempt`)
6. Closes the session with the final outcome (`close_debug_session`)

**Skill used:** `perfmemory` (Workflow C, Source 1)

---

### E. Ingest a Lessons-Learned Document

Use this to load curated knowledge (like team-maintained pattern files or Cursor Rules) into PerfMemory.

**Prompt:**

```
Ingest the lessons learned from docs/{your_filename} into perfmemory
for {your_system_name}.
```

**Example:**

```
Ingest the SSO lessons learned from docs/lessons/sso-lessons-learned.md
into perfmemory for OrderPortal.
```

**What the agent does:**

1. Reads the lessons-learned document
2. Creates a PerfMemory session with a note indicating the source document
3. For each numbered lesson, parses the pattern, symptom, and fix
4. Stores each as an attempt with `outcome = "resolved"` (these are known-good patterns)
5. Marks all attempts as human-verified (`verify_attempt`)
6. Closes the session

**Skill used:** `perfmemory` (Workflow C, Source 2)

---

### F. Review and Browse Memory

**Prompt — View stats:**

```
Show me perfmemory stats for {your_system_name}.
```

**Tool call:**

```
get_memory_stats(
  system_under_test = "{your_system_name}"
)
```

---

**Prompt — List sessions:**

```
List all debug sessions for {your_system_name} in the {environment} environment.
```

**Tool call:**

```
list_sessions(
  system_under_test = "{your_system_name}",
  environment       = "{environment}"
)
```

You can also filter by alias or service:

```
list_sessions(
  system_alias = "AUTH",
  service_name = "auth-service"
)
```

---

**Prompt — Session details:**

```
Show me the full details of perfmemory session {session_id}.
```

**Tool call:**

```
get_session_detail(
  session_id = "{session_id}"
)
```

---

**Prompt — List only resolved sessions:**

```
Show me all resolved debug sessions for {your_system_name}.
```

**Tool call:**

```
list_sessions(
  system_under_test = "{your_system_name}",
  final_outcome     = "resolved"
)
```

**Skill used:** `perfmemory` (Maintenance Operations)

---

### G. Maintenance — Verify and Archive

**Prompt — Verify a lesson:**

```
Mark attempt {attempt_id} as verified — I confirmed this fix works correctly.
```

**Tool call:**

```
verify_attempt(
  attempt_id = "{attempt_id}"
)
```

---

**Prompt — Archive an outdated lesson:**

```
Archive attempt {attempt_id} — the API endpoint changed and this fix
no longer applies.
```

**Tool call:**

```
archive_attempt(
  attempt_id = "{attempt_id}",
  reason     = "API endpoint changed; fix no longer applicable"
)
```

Archived attempts remain in the database for audit but are excluded from future search results.

**Skill used:** `perfmemory` (Maintenance Operations)

---

## Integrated Workflow Walkthrough

This section walks through a real-world scenario showing what a PTE types and what the agent does at each stage.

### Scenario

A PTE needs to debug a JMeter script for "OrderPortal" in the QA environment. The script was generated from a Playwright capture and has OAuth/SSO authentication issues.

```text
PTE types:   "Debug my JMeter script for test run sprint-42-qa.
              The system under test is OrderPortal in the QA environment."

Agent:        Activates jmeter-debugging skill
              ↓
Step 0.5:     Searches PerfMemory → finds 3 past issues for OrderPortal
              (OAuth correlations, cookie handling, redirect chains)
              ↓
Step 1:       Creates debug manifest at artifacts/sprint-42-qa/analysis/debug_manifest.md
              ↓
Step 1.5:     Opens PerfMemory session → saves pm_session_id
              ↓
Step 2:       Enforces 1/1/1 thread configuration
              ↓
Step 3:       Runs smoke test → errors at 40% → stops test early
              ↓
Step 4:       Analyzes log → first failure: TC01_S02_GET /authorize (URISyntaxException)
              ↓
Step 5:       Triages → isolated script issue
              ↓
Step 5d:      Searches PerfMemory for this specific symptom
              → MATCH at 0.91 similarity! Past fix: "Add Regex Extractor on
              GET /home to extract oauth_redirect_uri from goto parameter"
              → confirmed_count = 3, is_verified = true
              ↓
              Agent skips Steps 6-7 (no need for verbose debugging)
              ↓
Step 8:       Applies the known fix directly
              ↓
Step 8e:      Stores attempt in PerfMemory with matched_attempt_id
              → confirmed_count on the original fix increments to 4
              ↓
Step 3:       Re-runs smoke test → 0% errors!
              ↓
Step 9:       Cleans up debug post-processors, final validation pass
              ↓
Step 10:      Finalizes debug manifest, closes PerfMemory session
              ↓
              Tells PTE: "Script is clean. 1 iteration, 3 minutes.
              Applied a known fix from memory (confirmed 4 times).
              Ready for load testing."
```

**Without PerfMemory:** The agent would have gone through Steps 6-7 (attach debug post-processor, re-run, read verbose logs, diagnose) adding several minutes and an extra smoke test cycle.

**With PerfMemory:** The agent recognized the issue from memory, skipped straight to the fix, and resolved it in one iteration.

---

## Tips for Writing Good Symptom Text

The `symptom_text` field is the **only field** that gets embedded as a vector. Its quality directly affects how well future searches will match. Always use this structure:

```
[Error Category] on [Sampler Name] — [Response Code].
[Error Message / Symptom Description].
[Brief Diagnosis Context].
```

### Good Examples

```
Missing Correlations (OAuth Parameters) on TC01_S02_GET /authorize
— java.net.URISyntaxException. Illegal character in query caused by unresolved
${oauth_redirect_uri}, ${oauth_client_id}, ${oauth_response_mode}. The redirect
URL from GET /home contains all OAuth parameters URL-encoded in the goto query
parameter but no extractors existed.
```

```
Cookie/Redirect Handling on TC01_S03_POST /sso/authenticate — HTTP 403.
SSO authentication fails silently because auto_redirects=true delegates redirect
handling to Java's HttpURLConnection which does not use JMeter's CookieManager.
SSO cookies are lost during the redirect chain.
```

### Bad Examples

```
OAuth error on login page. Variables not found.
```
*Too vague — no sampler name, no specific variables, no context about where the values come from.*

```
Error 403
```
*Minimal information — could match almost anything, producing false positives in search.*

### Why It Matters

- **Specific symptoms** produce **specific matches** (high similarity to the right fix)
- **Vague symptoms** produce **noisy matches** (moderate similarity to many unrelated fixes)
- Include the **sampler name**, **response code**, and **root cause context** for best results

---

## FAQ

**What if PerfMemory is down or unavailable?**

Debugging continues normally. PerfMemory is advisory only — all memory-related steps are automatically skipped if the database is unreachable. The `jmeter-debugging` skill is designed to work with or without PerfMemory.

---

**Can I use this with Claude CLI, Codex CLI, or other AI tools?**

Yes. The tool calls shown in this guide are the MCP tool signatures. Any AI tool that supports MCP can call them directly. If your tool doesn't auto-detect the workflow, copy the tool calls from this guide and provide them as instructions.

---

**Do I need to name the Skill in my prompt?**

No. In Cursor, the agent auto-detects the relevant skill based on your prompt. Saying "debug my JMeter script" is enough to activate the `jmeter-debugging` skill with PerfMemory integration. However, if you want to be explicit, you can say "use the perfmemory skill to search for past fixes."

---

**How do I reset the database?**

Re-run the schema SQL against your database. This drops and recreates the tables:

```bash
psql -h localhost -U perfadmin -d perfmemory -f perfmemory-mcp/sql/schema/schema_openai.sql
```

**Warning:** This deletes all stored lessons. Consider using `pg_dump` to back up first.

---

**What if my team uses different names for the same application?**

Set up a `taxonomy.yaml` file with your applications and their aliases. For example, if one PTE stores data under "Shopping Cart" and another under "CART", define both as the same application. The taxonomy resolver will map aliases to canonical names during both ingestion and search, ensuring consistency when consolidating databases.

---

**What embedding provider should I use?**

| Provider | Best For | Dimensions | Cost |
|----------|----------|------------|------|
| OpenAI (`text-embedding-3-small`) | Best search quality, recommended default | 1536 | Pay-per-use API |
| Azure OpenAI | Corporate environments with Azure agreements | 1536 | Azure subscription |
| Ollama (`nomic-embed-text`) | Local/offline use, personal experimentation | 768 | Free (local compute) |

All providers produce compatible results within their dimension size. You cannot mix dimensions in the same database — pick one provider and stay consistent.

---

**Can multiple PTEs share the same database?**

Yes — that's the long-term vision. When multiple PTEs connect to the same PostgreSQL instance, every lesson stored by one PTE becomes available to all others. The `created_by` field tracks who stored each session, and the `system_under_test` filter lets you scope searches to specific systems.

For team use, consider hosting the database on a shared server rather than each PTE running their own Docker container.
