# 📋 PerfPilot-Hub — Changelog (October 2026)

This document summarizes the enhancements added to PerfPilot-Hub during October 2026.

The JMeter script workflow is unchanged. Playwright, HAR, and OpenAPI are three ways the Script Agent receives the source. After that, validation, execution, monitoring, analysis, and reporting follow the same path and share one `test_run_id`.

This changelog also covers the A2A server compliance pass: JSON-RPC is
the primary binding, REST stays supported, custom PerfPilot task routes
are retired, and inbound messages follow A2A v1.0.0.

---

## 📑 Table of Contents

- [🆔 1. Framework-assigned `test_run_id`](#1-framework-assigned-test_run_id)
- [🧪 2. Script creation inputs](#2-script-creation-inputs)
- [🔄 3. Multi-turn pause and resume](#3-multi-turn-pause-and-resume)
- [🖥️ 4. Web UI thread context](#4-web-ui-thread-context)
- [📊 5. Comparison reports](#5-comparison-reports)
- [📄 6. A2A server data contract](#6-a2a-server-data-contract)
- [✨ 7. Files created](#7-files-created)
- [🔧 8. Files modified](#8-files-modified)
- [🚫 9. Removed custom A2A task routes](#9-removed-custom-a2a-task-routes)
- [🔌 10. A2A wire-format compliance](#10-a2a-wire-format-compliance)
- [🧵 11. Required `contextId`](#11-required-contextid)
- [📥 12. Strict A2A ingress](#12-strict-a2a-ingress)
- [🪪 13. A2A Agent Cards](#13-a2a-agent-cards)
- [📘 14. A2A data-contract rewrite](#14-a2a-data-contract-rewrite)
- [🚀 15. A2A post-smoke enhancements](#15-a2a-post-smoke-enhancements)
- [📚 Previous Changelogs](#previous-changelogs)

---

## 🆔 1. Framework-assigned `test_run_id`

The agent framework assigns `test_run_id`. Specialist agents reuse the id they are given. They do not mint one.

Every inbound request is classified into one of four outcomes:

| Outcome | When | What the server does |
|---------|------|----------------------|
| **Reuse** | An id is already on the task, in the payload, or in the user's message (`test_run_id: YYYY-MM-DD-HH-MM-SS`) | Keep that id |
| **Mint** | The request creates a JMeter script (Playwright, HAR, or OpenAPI) and no id exists yet | Mint `YYYY-MM-DD-HH-MM-SS` in UTC and store it on the task |
| **Skip mint** | The request is a comparison report and names two or more existing run ids, **or** a catalog / list-specialists question | Do not mint. Comparison reports later store `comparison_id`. Catalog questions need no id |
| **Ask** | No id exists and the request is not script creation, start-test, or catalog discovery | Pause the task and ask the user which run to use |

Artifacts for a single run stay under `artifacts/{test_run_id}/`.

---

## 🧪 2. Script creation inputs

One script-creation workflow. The Script Agent is the only part that changes with the source:

| Source | What the caller supplies | What the Script Agent uses |
|--------|--------------------------|----------------------------|
| Playwright | Step text in the task context | Those steps, written to `artifacts/{test_run_id}/test-specs/` |
| HAR | A short summary, plus the URL of the capture file | The HAR-to-JMX converter |
| OpenAPI | A short summary, plus the URL of the specification file | The OpenAPI-to-JMX converter |

A Playwright request must say `source_type: playwright` and include recognizable step lines (for example `Step 1:`, `TC01:`, or `Test Case 1:`). A request that declares Playwright and has no steps pauses instead of writing an empty spec.

---

## 🔄 3. Multi-turn pause and resume

When the server needs more input, the task moves to `TASK_STATE_INPUT_REQUIRED`. The status message includes a question for the user and a `reasonCode` a client can switch on.

| `reasonCode` | When |
|--------------|------|
| `missing_test_run_id` | The request needs a run id and none was supplied |
| `malformed_playwright_test_spec` | Playwright was declared and the task context has no recognizable steps |
| `agent_input_required` | A specialist asked the client to choose (for example A/B/C) instead of producing the objective |

The caller continues the same task by sending another message with the same task id and the same `contextId`. The follow-up can be a short answer. The original request stays on the task. A mismatched `contextId`, an unknown task id, or a task that is not paused is rejected.

---

## 🖥️ 4. Web UI thread context

On each Web UI turn, the backend loads `test_run_id` values already stored for that conversation and gives them to the orchestrator.

- One known id is reused.
- Several known ids are listed so the orchestrator can ask which run the user means.
- A first turn with no known id follows the same reuse / mint / ask rules as an A2A request.

---

## 📊 5. Comparison reports

A comparison report names the runs to compare. The framework does not mint a new `test_run_id` for that request. When the reporting agent returns `comparison_id`, that value is stored on the reporting task in the same column a single-run id uses, so later status reads and publishes can find the bundle.

---

## 📄 6. A2A server data contract

Public reference: [`docs/a2a/a2a-server-data-contracts.md`](docs/a2a/a2a-server-data-contracts.md)
(rewritten after the A2A compliance pass; see §14).

It describes what an upstream framework sends on an inbound A2A request:

- Exactly three parts on a new request: the user prompt, the task context, and extra context such as environment and work-item links.
- Each part is `text` plus `metadata`, with `mediaType: text/markdown`.
- Playwright steps travel in the task-context text. HAR and OpenAPI travel as a summary plus a source URL.
- Comparison reports travel as a summary plus `task_type: comparison_report` and `test_run_ids`.
- A resume message can be a short single-part answer.

Examples use placeholder hosts (`example.com`, `example-org`).

---

## ✨ 7. Files created

| File | Purpose |
|------|---------|
| `agent-framework/backend/sql/011_extend_agent_tasks_status.sql` | Allows `input_required`, `rejected`, and `auth_required` on `agent_tasks` |
| `agent-framework/backend/core/task_signals.py` | Signal used to pause a task for missing input |
| `docs/a2a/a2a-server-data-contracts.md` | Public A2A inbound data contract |
| `tests/agent_framework/stores/test_task_store_input_required.py` | Task-store coverage for pause, resume messages, and `test_run_id` persistence |
| `tests/agent_framework/core/test_test_run_id.py` | Resolver outcome coverage |
| `tests/agent_framework/a2a/test_parts_parser_task_split.py` | Prompt and optional task-tag parsing |
| `tests/agent_framework/a2a/test_a2a_sse_input_required.py` | Pause-event coverage |
| `tests/agent_framework/a2a/test_a2a_resume_flow.py` | Resume-path coverage |
| `tests/agent_framework/a2a/test_spec_normalizer_strict_gate.py` | Playwright source gate |
| `tests/agent_framework/a2a/test_retired_task_routes.py` | Retired `/agents/{name}/tasks/*` routes are unregistered |
| `tests/agent_framework/a2a/test_wire_format.py` | JSON-RPC/REST wrappers, `returnImmediately`, push `-32003` |
| `tests/agent_framework/a2a/test_context_enforcement.py` | Required `contextId`; optional matching `X-External-Thread-Id` |
| `tests/agent_framework/a2a/test_ingress_validation.py` | Strict Message validation; metadata-sourced `test_run_id` |
| `tests/agent_framework/a2a/test_agent_cards.py` | Official card capabilities, `provider.url`, metadata catalog fields |
| `tests/agent_framework/agui/test_webui_context_load.py` | Web UI thread-context coverage |
| `tests/agent_framework/services/test_orchestrator_input_required.py` | Orchestrator pause coverage |
| `tests/agent_framework/services/test_specialist_envelope_reuse.py` | Specialist reuse and `comparison_id` capture |
| `tests/agent_framework/services/test_persist_test_spec_strict.py` | Playwright spec persistence |
| `tests/agent_framework/agents/test_delegate_source_type.py` | Orchestrator delegation of `source_type` and comparison payloads |

## 🔧 8. Files modified

| File | Changes |
|------|---------|
| `agent-framework/backend/stores/task_store.py` | Persist `test_run_id`, mark a task paused, append a resume message |
| `agent-framework/backend/core/test_run_id.py` | Reuse, mint, skip-mint, and ask outcomes |
| `agent-framework/backend/a2a/shared/parts_parser.py` | Read an optional task tag from the prompt part |
| `agent-framework/backend/services/task_executor.py` | Pause when an id is missing; specialists reuse the id; capture `comparison_id`; persist Playwright specs from step text |
| `agent-framework/backend/a2a/server/v1_jsonrpc.py` | JSON-RPC binding (`POST /a2a/v1`) |
| `agent-framework/backend/a2a/server/v1_rest.py` | REST binding + official Agent Card discovery |
| `agent-framework/backend/a2a/server/helpers/normalize.py` | Strict SendMessage validation; metadata-centric flatten |
| `agent-framework/backend/a2a/server/helpers/converters.py` | Task / StreamResponse shapes; `reasonCode` on one text part |
| `agent-framework/backend/a2a/server/helpers/resume.py` | Resume an existing paused task when the inbound message names it |
| `agent-framework/backend/a2a/server/helpers/spec_normalizer.py` | Playwright extraction requires `source_type: playwright` |
| `agent-framework/backend/a2a/server/utils/context.py` | Required `contextId`; optional header match; never mint |
| `agent-framework/backend/agui_server.py` | Load prior `test_run_id` values for the Web UI thread |
| `agent-framework/backend/agents/orchestrator/agent.py` | Delegation accepts `source_type` for the Script Agent and passes comparison run ids through |
| `agent-framework/backend/agents/orchestrator/INSTRUCTIONS.md` | The framework assigns `test_run_id`; the model asks when more than one run is in play |
| `agent-framework/backend/agents/script-agent/INSTRUCTIONS.md` | Use the `test_run_id` already on the payload |

---

## 🚫 9. Removed custom A2A task routes

Custom PerfPilot task URLs under `/agents/{name}/tasks/*` are retired.
JSON-RPC (`POST /a2a/v1`) is the primary A2A binding; REST
(`/message:send`, `/message:stream`, `/tasks/{id}`) remains supported.
The Web UI catalog (`GET /agents`, `GET /agents/{name}/.well-known/agent.json`)
and AG-UI (port 8102) are unchanged. Orchestrator specialist
delegation is in-process; it no longer HTTP-POSTs the deleted routes.

See [A2A server data contract §12](docs/a2a/a2a-server-data-contracts.md#12-removed-api)
for the replacement table.

---

## 🔌 10. A2A wire-format compliance

JSON-RPC `GetTask` / `CancelTask` now return a bare `Task` in `result`.
`SendMessage` (both bindings) returns `{ "task": Task }`. REST success
and error bodies use `Content-Type: application/a2a+json`.
`configuration.returnImmediately` (default `false`) is the blocking
switch. `INPUT_REQUIRED` emits a single text part with
`metadata.reasonCode`. Agent cards advertise `pushNotifications: false`;
push methods return `-32003`. REST gained `GET /tasks/{id}:subscribe`
and `GET /tasks` (list is still unimplemented).

---

## 🧵 11. Required `contextId`

Every A2A `SendMessage` / `SendStreamingMessage` must include
`message.contextId`. The server no longer auto-mints a conversation
id. Optional `X-External-Thread-Id` must match `contextId` when
present. Task objects and SSE events echo `contextId` (never an empty
string). Successful Send / Stream / Get / Cancel / Subscribe responses
now also return `X-Thread-Id` (see §15).

---

## 📥 12. Strict A2A ingress

A2A `SendMessage` / `SendStreamingMessage` now require a validated
`message` object (`messageId`, `ROLE_USER`, non-empty `parts`,
`contextId`). Flat bodies (`text`, `prompt`, or a string `message`)
are rejected. PerfPilot fields such as `test_run_id` are read from
`message.metadata` and `parts[].metadata` (`message.metadata` wins
over envelope `metadata`).

---

## 🪪 13. A2A Agent Cards

On-disk Agent Cards and the stub synthesizer now use official A2A
`capabilities` only (`streaming`, `pushNotifications`,
`extendedAgentCard`). PerfPilot flags live under
`metadata.perfpilot_capabilities`. `displayName`, `status`, and `tags`
moved into `metadata`. `provider.url` is the `{host}:{port}`
placeholder (hostname auto-injection stays backlog-only).
`list_available_specialists` reads those metadata fields.

---

## 📘 14. A2A data-contract rewrite

[`docs/a2a/a2a-server-data-contracts.md`](docs/a2a/a2a-server-data-contracts.md)
is the post-compliance inbound contract: required `contextId` (never
minted), optional matching `X-External-Thread-Id`, official envelope
`metadata`, no flat legacy payload, correct JSON-RPC / REST wrappers,
Removed API, and Agent Card metadata. Unit coverage for those rules
lives under `tests/agent_framework/a2a/`.

---

## 🚀 15. A2A post-smoke enhancements

Smoke follow-ups on `feature/update-a2a-server-compliance` after the
compliance pass. Public contract:
[`docs/a2a/a2a-server-data-contracts.md`](docs/a2a/a2a-server-data-contracts.md).

- Parent SSE stays open until the **subscribed** task is terminal.
  Proxied child `COMPLETED` frames do not close the parent stream.
- Playwright specs persist **after** `test_run_id` mint or reuse.
- Specialist A/B/C “please choose” replies settle as
  `TASK_STATE_INPUT_REQUIRED` (`reasonCode: agent_input_required`),
  not `COMPLETED`.
- `X-Thread-Id` is returned on successful JSON-RPC and REST Send / Get /
  Cancel / Subscribe, not only on SSE.
- A2A request logs go through `logging_config.log_event` (warning on
  client rejects, debug on `GetTask` polls).
- A2A orchestrator replies name child `taskId`s and `GetTask` /
  `SubscribeToTask`. They do not mention the Tasks panel.
- Catalog / “list specialists” questions `skip_mint` and do not pause
  for `missing_test_run_id`.
- `archive_playwright_traces` returns `DEGRADED` with `archived_path`
  when source cleanup hits a permission error; a new capture can still run.

Intent classifiers (catalog vs script vs start-test) remain heuristics
in the `test_run_id` resolver and are tracked as later cleanup.

---

## 📚 Previous Changelogs

| Month | Link | Highlights |
|-------|------|------------|
| September 2026 | [CHANGELOG-2026-09.md](docs/changelogs/CHANGELOG-2026-09.md) | GitHub MCP, BlazeMeter test provisioning, A2A new-JMX pipeline, documentation refactor, Docker full-stack refactor |
| August 2026 | [CHANGELOG-2026-08.md](docs/changelogs/CHANGELOG-2026-08.md) | Repository restructuring, agent-framework containers, HITL approval flow, user identity, task management UI |
| July 2026 | [CHANGELOG-2026-07.md](docs/changelogs/CHANGELOG-2026-07.md) | Loop engineering, LLM round-trip reduction, context and token tracking, compaction |
| June 2026 | [CHANGELOG-2026-06.md](docs/changelogs/CHANGELOG-2026-06.md) | PerfPilot Agents framework, orchestrator, Execution Agent, Playwright integration, CopilotKit frontend |
| May 2026 | [CHANGELOG-2026-05.md](docs/changelogs/CHANGELOG-2026-05.md) | PerfMemory taxonomy, correlation engine, SharePoint MCP, FastMCP v3 migration, PerfPilot Hub |
| April 2026 | [CHANGELOG-2026-04.md](docs/changelogs/CHANGELOG-2026-04.md) | Skills migration, Cursor subagents, PerfMemory MCP, MS Teams MCP, KPI analysis, JMeter script validator |
| March 2026 | [CHANGELOG-2026-03.md](docs/changelogs/CHANGELOG-2026-03.md) | HITL editing tools, correlation analysis, AI-assisted debugging, artifact path alignment |
| February 2026 | [CHANGELOG-2026-02.md](docs/changelogs/CHANGELOG-2026-02.md) | Swagger/OpenAPI adapter, HAR adapter, centralized SLA config, JMeter log analysis |
| January 2026 | [CHANGELOG-2026-01.md](docs/changelogs/CHANGELOG-2026-01.md) | AI-assisted report revision, Datadog dynamic limits, report enhancements, new charts |

---

*Last Updated: October 23, 2026* ✨
