# 🤝 PerfPilot A2A Server — Data Contracts

Reference for upstream agent frameworks that want to drive PerfPilot
through the Agent-to-Agent (A2A) v1 protocol. Covers the request
envelope, the three-part `message.parts[]` layout, `test_run_id`
behavior, multi-turn resume with `TASK_STATE_INPUT_REQUIRED`, and the
machine-readable `reasonCode` taxonomy clients can switch UX on.

PerfPilot implements A2A **v1.0.0**. Spec sections referenced inline
below (§3.4.3 context matching, §4.1.3 `TASK_STATE_INPUT_REQUIRED`,
§4.1.6 message parts, §6.3 multi-turn interaction, §11 message RPCs)
point at the public A2A specification; the server's semantics are
derived directly from the spec plus the few extensions documented here.

All examples use sanitized placeholder values (`example.com`,
`example-org`, synthetic UUIDs, UTC timestamps). Nothing in this doc
depends on any private hostname, workspace, or API key.

---

## 📬 1. Protocol envelope

| Binding | Endpoint | Method | Response |
|---------|----------|--------|----------|
| **REST — send** | `POST /message:send` | HTTP | `202 Accepted` with an A2A v1 `Task` object |
| **REST — stream** | `POST /message:stream` | HTTP (`Accept: text/event-stream`) | Server-Sent Events stream of `StreamResponse` frames |
| **JSON-RPC — send** | `POST /a2a/v1` | `"method": "SendMessage"` | JSON-RPC success envelope wrapping a `Task` object |
| **JSON-RPC — stream** | `POST /a2a/v1` | `"method": "SendStreamingMessage"` | JSON-RPC success envelope wrapping an SSE stream |
| **Discovery** | `GET /.well-known/agent-card.json` | HTTP | Public agent card (A2A §8) |
| **Task fetch** | `GET /tasks/{task_id}` | HTTP | `Task` object |
| **Task cancel** | `POST /tasks/{task_id}:cancel` | HTTP | Updated `Task` object |

The same server accepts the same `message` body on all four of the send
paths. Clients pick the binding that matches their transport
preference; the data contract is identical.

### 1.1 Minimum request envelope

```jsonc
{
  "message": {
    "messageId": "msg-<uuid>",
    "role": "ROLE_USER",
    "contextId": "ctx-<thread-id>",
    "parts": [
      { "text": "Create a JMeter script from this Playwright test." }
    ]
  },
  "metadata": { }
}
```

- `message.role` is always `ROLE_USER` on inbound (the server replies
  with `ROLE_AGENT` on streamed `TaskStatusUpdateEvent.status.message`).
- `message.contextId` is required for every request that participates
  in a multi-turn conversation (see §2). First-turn clients that have
  no `contextId` yet should mint one (any opaque string) and reuse it
  for subsequent turns.
- `message.parts[]` is the primary data channel; see §3.
- `metadata` is a free-form dict merged with `message.metadata` on
  ingress; see §3.4.

The server also accepts a **legacy flat payload** for backwards
compatibility (top-level `message` string, `text`, or `prompt`). New
clients should always use the A2A v1 shape described above.

---

## 🧵 2. Context, thread, session, and task hierarchy

The server tracks four correlation IDs. Clients only need to supply
`contextId`; everything else is minted and persisted server-side.

| ID | Who sets it | Scope | Purpose |
|----|-------------|-------|---------|
| `contextId` | **Client** (on `message.contextId`) | Conversation / thread | Links related turns across a multi-turn exchange; required for A2A §6.3 resume |
| `X-External-Thread-Id` | Client (optional HTTP header) | Conversation / thread | External thread correlation when the upstream framework has its own thread ID |
| `session_id` | Server | Caller session | Internal session row; derived from the authenticated caller |
| `taskId` | Server | One request | Issued on `202 Accepted`; the client echoes it in the `Task.id` field and on resume (§6) |

### 2.1 `contextId` matching rule (A2A §3.4.3)

When a client resumes a paused task (§6), the inbound
`message.contextId` **MUST** match the `contextId` on the original
task. Mismatches are rejected with a `UnsupportedOperation` error
(JSON-RPC code `-32004` / REST `400`). The server stores the original
`contextId` on the task row at ingress and compares byte-for-byte on
resume.

### 2.2 Discovery

```
GET /.well-known/agent-card.json
```

Returns the orchestrator's public agent card (A2A §8) describing the
agent's name, version, and capabilities. Clients can cache this.

---

## 🧩 3. The three-part message layout

PerfPilot's data contract requires **exactly three parts** on every
inbound request. The orchestrator and specialist agents need all
three to have the full context they need to decide what to do, where
the inputs come from, and where the outputs should go. Clients that
send fewer parts will either be rejected (`TASK_STATE_INPUT_REQUIRED`
with a `reasonCode`) or fall back to the resolver's prose heuristics,
which cannot cover every case.

Every part uses the same two-field shape — a `text` field (freeform
markdown) and a `metadata` field (typed key/value dict) — plus a
`mediaType` of `text/markdown`. The `data` field is **not** part of
this contract; clients MUST use `metadata` for structured values.

> Media type policy: today the server accepts `text/markdown` for all
> three parts. JSON support may be added in a future release;
> clients should default to `text/markdown`.

### 3.1 `parts[0]` — The user prompt

The user's natural-language request plus an optional task classifier.

```jsonc
{
  "text": "Create a JMeter script from the attached Playwright test case.",
  "metadata": {
    "task": "generate_jmx_from_playwright"
  },
  "mediaType": "text/markdown"
}
```

- `text` carries the free-form prompt the orchestrator LLM reads.
- `metadata.task` (optional) is a short tag the server surfaces to the
  orchestrator as `ParsedRequest.task_classifier`. The taxonomy is
  **open** today — any lowercase snake_case string is accepted. Future
  reserved values are called out in §10.

### 3.2 `parts[1]` — Required task context

Structured context for the task. The `text` field carries the primary
content (for Playwright: the actual step-based test spec; for HAR and
OpenAPI: a human-readable description of what the referenced source
file contains). The `metadata` field carries the typed classifier +
locator fields the server dispatches on.

For a Playwright script-creation request, the step content is sent
**inline** in the `text` field — the user supplies the steps; the
server does not fetch them from a URL today (future releases may
support remote step fetches, at which point `source_location_url`
will apply to Playwright too):

```jsonc
{
  "text": "## Steps\n1. Open https://example.com\n2. Click 'Sign in'\n3. Fill the email field\n...",
  "metadata": {
    "source_type":               "playwright",
    "github_destination_url":    "https://github.com/example-org/perf-suite",
    "github_destination_branch": "perf/2026-10-08-14-30-00"
  },
  "mediaType": "text/markdown"
}
```

For HAR or OpenAPI, the actual source file lives at
`metadata.source_location_url`; the `text` field carries human context
about what that file represents so the orchestrator LLM can reason
about scope and intent:

```jsonc
{
  "text": "## Summary: HAR capture of an end-to-end workflow for purchasing a laptop in the online store. ## PBI Description: covers login, product search, add-to-cart, and checkout.",
  "metadata": {
    "source_type":               "har",
    "source_location_url":       "https://dev.azure.com/example-org/PerformanceTesting/_apis/wit/attachments/550e8400-e29b-41d4-a716-446655440000?fileName=shopping-cart.har",
    "github_destination_url":    "https://github.com/example-org/perf-suite",
    "github_destination_branch": "perf/2026-10-08-14-30-00"
  },
  "mediaType": "text/markdown"
}
```

For a comparison-report request, `metadata` carries the task-type
classifier and the list of runs to compare; `text` carries a short
narrative description:

```jsonc
{
  "text": "## Summary: Compare the two latest nightly runs for the Checkout API to see if the perf regression introduced yesterday has been resolved.",
  "metadata": {
    "task_type":    "comparison_report",
    "test_run_ids": ["2026-10-07-14-00-00", "2026-10-06-10-00-00"]
  },
  "mediaType": "text/markdown"
}
```

Field reference:

- **`metadata.source_type`** — one of `"playwright"`, `"har"`,
  `"openapi"`. Signals a **script-creation** request. The server
  reads this from `parts[1].metadata.source_type` first, then falls
  back to top-level `payload.source_type` for Web-UI callers that
  bypass the parts envelope.
- **`metadata.source_location_url`** — URL the script agent fetches
  the source spec from. Required for `har` and `openapi`; not
  consumed for `playwright` today (steps are supplied inline in
  `text`).
- **`metadata.github_destination_url`** / **`metadata.github_destination_branch`** —
  destination for the generated JMX commit. Omit `github_destination_branch`
  to use the server's default branch convention (`perf/{test_run_id}`).
- **`metadata.task_type: "comparison_report"`** + **`metadata.test_run_ids: [...]`** —
  comparison-report request; see §6.

### 3.3 `parts[2]` — Additional context and prerequisites

Everything `parts[1]` does not cover: environment hints, placeholder
inventories, and work-item references (ADO, JIRA) the orchestrator or
specialists may need to reason about the request. Same two-field
shape — `text` for the narrative, `metadata` for the typed fields:

```jsonc
{
  "text": "## Known Placeholders: {{baseUrl}}, {{environment}}, {{userEmail}}; ## Unique Placeholders: {{checkoutDate}}, {{creditCardNumber}}",
  "metadata": {
    "environment":          "QA",
    "ado_work_item":        "PBI 12345: Performance Test the Shopping Cart",
    "ado_work_item_link":   "https://dev.azure.com/example-org/PerformanceTesting/_workitems/edit/12345",
    "jira_work_item":       "PBI 67890: Performance Test the Store Checkout",
    "jira_work_item_link":  "https://example-org.atlassian.net/browse/PERF-1234"
  },
  "mediaType": "text/markdown"
}
```

Field reference:

- **`text`** — a short markdown description of anything the specialist
  needs to know up front. The placeholder inventory above is one
  common pattern (lists which variables appear in the request so the
  script agent can wire them to CSV data files or runtime defaults).
- **`metadata.environment`** — environment tag (e.g. `QA`, `UAT`,
  `PERF`). Must match a key under the server's environment config so
  the resolver can look up the hostname, cert profile, and default
  BlazeMeter workspace/project.
- **`metadata.ado_work_item`** / **`metadata.ado_work_item_link`** —
  display label and URL for the Azure DevOps work item the request
  originated from. The orchestrator surfaces this in its reply so the
  user can navigate back to the ticket.
- **`metadata.jira_work_item`** / **`metadata.jira_work_item_link`** —
  same as above for JIRA-based teams.
- Additional typed keys may be added here by convention; unknown keys
  are passed through to the orchestrator as part of the merged
  metadata dict (see §3.4).

### 3.4 `metadata` precedence

The server merges `metadata` from two sources on ingress:

```
message.metadata  (overrides)
envelope.metadata (base)
```

Keys present in both — `message.metadata` wins. The merged dict is
exposed to the orchestrator via `ParsedRequest.metadata`.

---

## 🆔 4. `test_run_id` behavior

Every PerfPilot artifact (JMX, BlazeMeter run, metrics capture, report)
is organized under a `test_run_id` folder: `artifacts/{test_run_id}/…`.
The server treats `test_run_id` as **framework-authoritative** — the
LLM never mints one. The ingress resolver classifies every inbound
request into one of four outcomes:

| Outcome | Trigger | Server behavior |
|---------|---------|-----------------|
| **reuse** | Request carries a valid `test_run_id` on `test_run_id`, `metadata.test_run_id`, or inline in the user prose (`test_run_id: YYYY-MM-DD-HH-MM-SS`) | Use the value verbatim; never override |
| **skip_mint** | `parts[1].metadata.task_type == "comparison_report"` with a non-empty `test_run_ids` list | Defer to the PerfReport MCP's own `comparison_id`; no mint |
| **mint** | `parts[1].metadata.source_type` is in `{playwright, har, openapi}`, **or** the Web-UI prose heuristic matches a script-creation intent | Mint a fresh UTC timestamp `YYYY-MM-DD-HH-MM-SS`; write it back to the payload so downstream code sees it |
| **reject** | None of the above matches | Task transitions to `TASK_STATE_INPUT_REQUIRED` with `reasonCode: "missing_test_run_id"`; client resumes per §6 |

### 4.1 `test_run_id` format

- Canonical form: `YYYY-MM-DD-HH-MM-SS` UTC (e.g. `2026-10-08-14-30-00`).
- Clients MAY also pass BlazeMeter test IDs or other opaque strings
  when **reusing** an existing run — the resolver accepts any
  non-empty trimmed string on the reuse branch. The mint branch
  always emits the canonical UTC form.

### 4.2 Reuse via inline prose

For resume messages (§6), the user's reply text is scanned for
`test_run_id:` / `run_id:` labeled values before falling back to any
bare timestamp-shaped token:

```
test_run_id: 2026-10-07-14-00-00
```

Labeled values are preferred so an unrelated timestamp pasted into the
message cannot accidentally be picked up as the ID.

---

## 🛠️ 5. Script-creation use cases

### 5.1 Playwright

```jsonc
{
  "message": {
    "messageId": "msg-001",
    "role":      "ROLE_USER",
    "contextId": "ctx-001",
    "parts": [
      {
        "text": "Convert this Playwright test into a JMeter script.",
        "metadata": { "task": "generate_jmx_from_playwright" },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Steps\n1. Open https://example.com\n2. Click 'Sign in'\n3. Fill the email field\n4. Fill the password field\n5. Click 'Submit'\n...",
        "metadata": {
          "source_type":               "playwright",
          "github_destination_url":    "https://github.com/example-org/perf-suite",
          "github_destination_branch": "perf/<server-fills-with-minted-test_run_id>"
        },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Known Placeholders: {{baseUrl}}, {{userEmail}}, {{userPassword}}",
        "metadata": {
          "environment":        "QA",
          "ado_work_item":      "PBI 12345: Performance Test the Login Flow",
          "ado_work_item_link": "https://dev.azure.com/example-org/PerformanceTesting/_workitems/edit/12345"
        },
        "mediaType": "text/markdown"
      }
    ]
  }
}
```

Server behavior:

1. Resolver sees `parts[1].metadata.source_type == "playwright"` →
   mints a fresh `test_run_id`, e.g. `2026-10-08-14-30-00`.
2. The server's **strict Playwright gate** validates that at least
   one recognizable step pattern is present in the `parts[1].text`
   markdown (lines starting with `Step 1:`, `TC01:`, `Test Case 1:`,
   etc.). Missing content triggers `TASK_STATE_INPUT_REQUIRED` with
   `reasonCode: "malformed_playwright_test_spec"` (§7).
3. Spec persisted to
   `artifacts/{test_run_id}/test-specs/{test_run_id}_test_spec.md`.
4. Script agent delegated with the minted `test_run_id` and the
   full three-part context on its payload.

### 5.2 HAR

```jsonc
{
  "message": {
    "messageId": "msg-002",
    "role":      "ROLE_USER",
    "contextId": "ctx-002",
    "parts": [
      {
        "text": "Generate a JMeter script from this HAR capture.",
        "metadata": { "task": "generate_jmx_from_har" },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Summary: HAR capture of an end-to-end workflow for purchasing a laptop in the online store. ## PBI Description: covers login, product search, add-to-cart, and checkout.",
        "metadata": {
          "source_type":               "har",
          "source_location_url":       "https://dev.azure.com/example-org/PerformanceTesting/_apis/wit/attachments/550e8400-e29b-41d4-a716-446655440000?fileName=shopping-cart.har",
          "github_destination_url":    "https://github.com/example-org/perf-suite",
          "github_destination_branch": "perf/<server-fills-with-minted-test_run_id>"
        },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Known Placeholders: {{baseUrl}}, {{userEmail}}; ## Unique Placeholders: {{creditCardNumber}}, {{shippingAddress}}",
        "metadata": {
          "environment":        "QA",
          "ado_work_item":      "PBI 23456: Performance Test the Shopping Cart Checkout",
          "ado_work_item_link": "https://dev.azure.com/example-org/PerformanceTesting/_workitems/edit/23456"
        },
        "mediaType": "text/markdown"
      }
    ]
  }
}
```

Server behavior:

1. Resolver sees `parts[1].metadata.source_type == "har"` → mints a
   fresh `test_run_id`.
2. Script agent calls the JMeter MCP's HAR-to-JMX converter, pointing
   at `parts[1].metadata.source_location_url`. No step validation is
   performed — the HAR capture is the source of truth for request
   content.

### 5.3 OpenAPI / Swagger

```jsonc
{
  "message": {
    "messageId": "msg-003",
    "role":      "ROLE_USER",
    "contextId": "ctx-003",
    "parts": [
      {
        "text": "Generate a JMeter script from this OpenAPI spec.",
        "metadata": { "task": "generate_jmx_from_openapi" },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Summary: OpenAPI spec covering the shopping cart checkout API — endpoints for cart management, address validation, payment, and order confirmation.",
        "metadata": {
          "source_type":               "openapi",
          "source_location_url":       "https://dev.azure.com/example-org/PerformanceTesting/_apis/wit/attachments/660e8400-e29b-41d4-a716-446655440001?fileName=checkout-api.yaml",
          "github_destination_url":    "https://github.com/example-org/perf-suite",
          "github_destination_branch": "perf/<server-fills-with-minted-test_run_id>"
        },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Known Placeholders: {{baseUrl}}, {{apiToken}}; ## Unique Placeholders: {{orderId}}, {{idempotencyKey}}",
        "metadata": {
          "environment":        "QA",
          "ado_work_item":      "PBI 34567: Performance Test the Checkout API",
          "ado_work_item_link": "https://dev.azure.com/example-org/PerformanceTesting/_workitems/edit/34567"
        },
        "mediaType": "text/markdown"
      }
    ]
  }
}
```

Server behavior: same mint-and-delegate flow as HAR; the JMeter MCP's
Swagger/OpenAPI converter fetches the spec at
`parts[1].metadata.source_location_url` and produces the JMX.

---

## 📊 6. Comparison-report use case

Comparison reports aggregate two or more existing test runs into a
single bundle. The PerfReport MCP mints its own `comparison_id`;
the server captures it from the reporting agent's tool-result stream
and persists it onto the task row's `test_run_id` column so the
`agent_tasks` row's foreign-key links back to the comparison bundle.

```jsonc
{
  "message": {
    "messageId": "msg-004",
    "role":      "ROLE_USER",
    "contextId": "ctx-004",
    "parts": [
      {
        "text": "Compare these two runs side-by-side.",
        "metadata": { "task": "comparison_report" },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Summary: Compare the two latest nightly runs for the Checkout API to see if the perf regression introduced yesterday has been resolved.",
        "metadata": {
          "task_type":    "comparison_report",
          "test_run_ids": [
            "2026-10-07-14-00-00",
            "2026-10-06-10-00-00"
          ]
        },
        "mediaType": "text/markdown"
      },
      {
        "text": "## Context: Nightly perf runs for the Checkout API; the regression affected the add-to-cart endpoint under 500-user load.",
        "metadata": {
          "environment":        "QA",
          "ado_work_item":      "PBI 45678: Investigate Checkout API Regression",
          "ado_work_item_link": "https://dev.azure.com/example-org/PerformanceTesting/_workitems/edit/45678"
        },
        "mediaType": "text/markdown"
      }
    ]
  }
}
```

Server behavior:

1. Resolver sees `parts[1].metadata.task_type == "comparison_report"`
   with a non-empty `parts[1].metadata.test_run_ids` list →
   **skip_mint** outcome. No `test_run_id` is minted at ingress; the
   task row's `test_run_id` column stays `NULL` initially.
2. Orchestrator delegates to the reporting agent with
   `payload.comparison.test_run_ids = [...]`.
3. The reporting agent calls PerfReport MCP tools; the first
   successful tool result that returns `comparison_id` is captured
   via a tolerant scan over the tool-rounds stream.
4. Server persists `comparison_id` onto the task row via
   `task_store.set_test_run_id(task_id, comparison_id)`. Downstream
   consumers (status fetches, Confluence publishing) see the
   comparison ID in the same column where single-run IDs live.

Web-UI callers that bypass the parts envelope MAY send
`comparison.test_run_ids` at the top level of the payload; A2A
clients should always use the three-part shape above.

---

## 🔄 7. Multi-turn interaction (A2A §6.3)

When the server needs more input to proceed, it transitions the task
to `TASK_STATE_INPUT_REQUIRED` and emits a `TaskStatusUpdateEvent`
whose `status.message` carries both a human-readable question and a
machine-readable `reasonCode`.

### 7.1 Server → client: the pause event

Streamed as part of the `/message:stream` SSE flow:

```jsonc
{
  "statusUpdate": {
    "taskId":    "task-<uuid>",
    "contextId": "ctx-001",
    "status": {
      "state":     "TASK_STATE_INPUT_REQUIRED",
      "timestamp": "2026-10-08T14:30:00.000Z",
      "message": {
        "messageId": "task-<uuid>-input-required",
        "role":      "ROLE_AGENT",
        "taskId":    "task-<uuid>",
        "contextId": "ctx-001",
        "parts": [
          {
            "text": "I need a test_run_id to work with before I can continue. Please share the test_run_id in your next message, for example: 'test_run_id: 2026-10-06-10-00-00'.\n\nI see these test_run_ids on this conversation thread that you may want to continue with: `2026-10-07-14-00-00`, `2026-10-06-10-00-00`.",
            "metadata": {
              "reasonCode":             "missing_test_run_id",
              "candidate_test_run_ids": [
                "2026-10-07-14-00-00",
                "2026-10-06-10-00-00"
              ]
            },
            "mediaType": "text/markdown"
          }
        ]
      }
    }
  }
}
```

- `status.message.role` is `ROLE_AGENT` (server speaking to client).
- `status.message.parts[0].text` carries the human-readable question.
  Clients render this to the user.
- `status.message.parts[0].metadata.reasonCode` carries the
  machine-readable code the client switches UX on. Supplementary
  structured values (`candidate_test_run_ids` here) live beside the
  code in the same `metadata` dict.

The `taskId` and `contextId` are also present on the outer
`TaskStatusUpdateEvent` for clients that only care about the
discriminator; the inner `message` repeats them for convenience.

### 7.2 Client → server: resuming

To answer the question, the client sends a fresh `message:send` or
`message:stream` request with the same `taskId` **and** the same
`contextId`. Resume messages carry just the answer — the original
request already supplied the full three-part context, so the
follow-up may be a single-part reply:

```jsonc
{
  "message": {
    "messageId": "msg-005",
    "role":      "ROLE_USER",
    "taskId":    "task-<uuid>",           // ← same task
    "contextId": "ctx-001",               // ← same context (A2A §3.4.3)
    "parts": [
      {
        "text": "test_run_id: 2026-10-07-14-00-00",
        "mediaType": "text/markdown"
      }
    ]
  }
}
```

Server behavior:

1. Ingress lifts `message.taskId` into
   `_a2a_v1_resume_task_id`; the resume classifier (`v1_resume.py`)
   runs before any new-task creation.
2. The server validates:
   - Task exists → otherwise `TaskNotFound` (`-32001` JSON-RPC / 404 REST).
   - Task is in `TASK_STATE_INPUT_REQUIRED` → otherwise
     `UnsupportedOperation` (`-32004` / 400).
   - `message.contextId` matches the stored `contextId` → otherwise
     `UnsupportedOperation`.
3. On success, the server appends the resume message to the task's
   payload (`task_store.append_resume_message`), transitions the task
   back to `TASK_STATE_WORKING`, and re-enqueues execution.
4. The resumed task is returned as the response body (202 for REST,
   JSON-RPC success for the RPC binding). SSE subscribers receive
   subsequent events on the SAME task — no new `taskId` is minted.

### 7.3 What NOT to send on resume

- **Do not** mint a new `taskId` — the server treats that as a brand
  new task and will not resume anything.
- **Do not** change the `contextId` — the mismatch rule fires and the
  resume is rejected.
- **Do not** repeat the full `parts[]` from the original turn — the
  server only needs the answer text in `parts[0].text`.

---

## 🏷️ 8. `reasonCode` taxonomy

Current `reasonCode` values emitted on
`TASK_STATE_INPUT_REQUIRED` (always on
`status.message.parts[0].metadata.reasonCode`):

| `reasonCode` | Trigger | Supplementary `metadata` fields |
|--------------|---------|---------------------------------|
| `missing_test_run_id` | Resolver returned the `reject` outcome (§4) | `candidate_test_run_ids`: array of known IDs from the same thread (newest-first, max 5 rendered in the prompt text) |
| `malformed_playwright_test_spec` | `parts[1].metadata.source_type == "playwright"` but no recognizable step pattern was found in `parts[1].text` | `parts_count`: integer count of `parts[]` entries received |

Clients SHOULD switch on `reasonCode` for UX decisions and treat the
text message as display-only. Unknown `reasonCode` values should fall
back to rendering the text message verbatim.

### 8.1 Reserved for future releases

The following codes are reserved; servers will emit them in a future
release but are not emitted today. Clients that encode them now will
be forward-compatible:

- `awaiting_confirmation` — HITL approval required before a
  destructive action (e.g. BlazeMeter provisioning).
- `awaiting_credentials` — a required credential is missing; the
  client should prompt the user for it.

---

## ✅ 9. Successful response shape

On terminal success (`TASK_STATE_COMPLETED`), the final
`TaskStatusUpdateEvent.status.message` carries the agent's final
reply as a text part. For streamed workflows the server also emits
`TaskArtifactUpdateEvent` frames before the terminal status, carrying
artifact metadata:

```jsonc
{
  "artifactUpdate": {
    "taskId":    "task-<uuid>",
    "contextId": "ctx-001",
    "artifact": {
      "artifactId": "jmx-<uuid>",
      "name":       "checkout.jmx",
      "parts": [
        {
          "text": "## Generated JMX\nCheckout flow JMeter script committed to `perf/2026-10-08-14-30-00`.",
          "metadata": {
            "url":            "https://github.com/example-org/perf-suite/blob/perf/2026-10-08-14-30-00/performance/checkout.jmx",
            "test_run_id":    "2026-10-08-14-30-00",
            "git_commit_sha": "<40-hex-chars>",
            "smoke_status":   "PASS"
          },
          "mediaType": "text/markdown"
        }
      ]
    }
  }
}
```

For `TASK_STATE_FAILED`, the final `status.message` contains a human
summary of the failure plus any error context the specialist agent
provided. The task row's result column is also populated for later
retrieval via `GET /tasks/{task_id}`.

---

## 🚧 10. Out-of-scope / future work

- **`parts[0].metadata.task` reserved values.** The taxonomy is open
  today. A future release will reserve specific tags
  (e.g. `comparison_report`, `generate_jmx_from_playwright`) so
  clients can set them deterministically; untagged requests will keep
  working via the resolver's inferred classification.
- **Additional `reasonCode`s.** `awaiting_confirmation` and
  `awaiting_credentials` are planned (§8.1). Code the client to a
  switch with a fallback branch so new codes do not break existing
  UX.
- **GitHub-push wiring for OpenAPI / Playwright paths.** HAR today has
  full end-to-end GitHub push; the OpenAPI and Playwright paths will
  reach parity in a subsequent release. Clients can send
  `github_destination_url` on all three today — it is honored where
  supported and ignored elsewhere.
- **Push notifications.** A2A §9 push-notification configuration is
  not implemented; the server returns
  `PushNotificationNotSupportedError` (`-32003`) for the related
  JSON-RPC methods.

---

## 📖 11. Spec cross-reference

| A2A §      | What it covers                               | Where in PerfPilot                                      |
|------------|----------------------------------------------|---------------------------------------------------------|
| §3.4.3     | `contextId` matching rule                    | `v1_resume.py` validation step                          |
| §4.1.3     | `TASK_STATE_INPUT_REQUIRED` lifecycle        | `task_store.mark_input_required`; SSE emit in `v1_helpers._task_event_to_a2a_v1_sse` |
| §4.1.6     | Message `parts[]` constraints                | `a2a.shared.parts_parser.parse_request_body`             |
| §6.3       | Multi-turn interaction (resume)              | `v1_resume.try_handle_resume` + ingress hook            |
| §8         | Agent discovery                              | `GET /.well-known/agent-card.json`                      |
| §11        | `SendMessage` / `SendStreamingMessage` RPCs  | `/message:send`, `/message:stream`, `/a2a/v1`           |

Standard A2A error codes used by PerfPilot:

| Code    | Name                            | When emitted                                       |
|---------|---------------------------------|----------------------------------------------------|
| `-32001`| `TaskNotFoundError`             | Resume targeted a `taskId` that does not exist     |
| `-32004`| `UnsupportedOperationError`     | Resume targeted a non-paused task, or `contextId` mismatch |
| `-32602`| `InvalidParams` (JSON-RPC std.) | Request body is missing required fields            |

---

## 🚫 12. Removed API

The following custom PerfPilot task routes are **gone**. They are not
aliased, rewritten, or dual-maintained. Callers must use the A2A v1
bindings in §1.

| Removed | Replacement |
|---------|-------------|
| `POST /agents/{name}/tasks/send` | JSON-RPC `POST /a2a/v1` (`SendMessage`) or REST `POST /message:send` |
| `POST /agents/{name}/tasks/sendSubscribe` | JSON-RPC `POST /a2a/v1` (`SendStreamingMessage`) or REST `POST /message:stream` |
| `GET /agents/{name}/tasks/{task_id}` | JSON-RPC `GetTask` or REST `GET /tasks/{task_id}` |
| `POST /agents/{name}/tasks/{task_id}/cancel` | JSON-RPC `CancelTask` or REST `POST /tasks/{task_id}:cancel` |
| `GET /agents/{name}/tasks/get?task_id=...` | REST `GET /tasks/{task_id}` |
| Catalog field `tasks_send_url` on `GET /agents` | Dropped. Catalog still returns `name` and `agent_card_url` |

Public A2A traffic is orchestrator-centric. Specialist work is created
in-process by the orchestrator's `delegate_to_specialist` tool; it is
not submitted over `/agents/{name}/tasks/*`.

Kept (not A2A task routes):

- `GET /health`
- `GET /agents` (Web UI catalog)
- `GET /agents/{name}/.well-known/agent.json` (Web UI catalog)
- AG-UI port 8102 (`/api/delegate`, `/api/tasks`, `/api/events`, `/copilotkit/`) — unchanged

Snake-case flat task bodies on the retired paths are not accepted on
the v1 bindings. New clients send the A2A Message envelope in §1.1.
