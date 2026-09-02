# A2A New-JMX Pipeline Guide

Reference for upstream agent frameworks and Web UI clients that need to
drive the PerfPilot **new-JMX pipeline** — generate a JMeter script from
a source spec, version-control the JMX in Git, smoke-test it locally,
and provision a fresh BlazeMeter test from the result.

The pipeline is orchestrated by the PerfPilot Orchestrator and executed
by the Script Agent and Execution Agent. All that upstream callers must
supply is the correct **A2A metadata block** on their initial task.

---

## 1. Which surfaces support the pipeline

| Surface | Endpoint | Metadata delivery |
|---------|----------|-------------------|
| **A2A** (server-to-server) | `POST http://<host>:8001/agents/orchestrator/tasks/send` | JSON `metadata` field inside the task payload |
| **Web UI** (CopilotKit) | Browser → `POST /api/copilotkit` | Populated automatically from the [`GitHubCredsCard`](../../agent-framework/frontend/ui/components/github/github-creds-card.tsx) session state via `useCopilotReadable` |
| **Cursor / MCP** | Existing MCP tool calls | Not applicable — Cursor uses per-tool arguments, not framework metadata |

Both surfaces route to the same orchestrator, so the metadata schema
described below is identical.

---

## 2. Metadata schema

```jsonc
{
  "environment": "QA",                    // Required: environment name defined in agent-framework/backend/config/environments.yaml
  "test_run_id": "2026-08-23-14-30-00",   // Optional: verbatim ID. Minted by orchestrator if omitted.
  "upstream_framework": "acme-perf-hub",  // Optional: string surfaced to the orchestrator for logging.
  "requested_workflow": "new-jmx-pipeline", // Optional: hint. Presence of scm block already implies the pipeline.

  "scm": {                                // Required to trigger the pipeline. Presence of `scm.url` is the signal.
    "url": "https://github.com/your-org/your-perf-repo",
    "branch": "perf/2026-08-23-14-30-00", // Optional. Defaults to perf/{test_run_id}.
    "path": "performance/checkout.jmx",   // Optional. Defaults to performance/{jmx_basename}.
    "createBranch": true,                 // Optional (default true). Set false to require an existing branch.
    "provider": "github"                  // Optional. Only "github" is recognised today.
  },

  "blazemeter": {                         // Optional. Overrides the environments.yaml BlazeMeter mapping.
    "workspaceId": "12345",
    "projectId": "67890",
    "testName": "checkout-a2a-2026-08-23"
  }
}
```

### 2.1 Field notes

- **`environment`** — matches a key under `environments:` in
  `agent-framework/backend/config/environments.yaml`. The resolver
  (`services/env_resolver.py`) uses it to look up the hostname, cert
  profile, and default BlazeMeter workspace/project. See
  [`environments.example.yaml`](../../agent-framework/backend/config/environments.example.yaml).
- **`scm.url`** — must be an `https://github.com/<owner>/<repo>` URL.
  SSH and enterprise hosts are rejected by
  `services/scm_resolver.py::parse_github_url`.
- **`scm.branch` / `scm.path`** — omit to accept the safe defaults.
  Defaults use the resolved `test_run_id` and the JMX basename so
  different runs never overwrite each other.
- **`blazemeter.*`** — every field is optional. When present it
  overrides whatever `environments.yaml` supplied. If neither this
  block nor `environments.yaml` provides a workspace/project, the
  Execution Agent returns an error before touching BlazeMeter.
- **`testName`** — when omitted, the Execution Agent derives it from
  the JMX basename plus the `test_run_id`.

### 2.2 Fields the framework surfaces to the orchestrator LLM

`services/task_executor.py::_format_metadata_context` extracts the
following into the orchestrator's system context so it can reason about
delegation:

- `upstream_framework`, `environment`, `requested_workflow`,
  `test_run_id` (as scalar lines)
- `scm.url`, `scm.branch`, `scm.path`, `scm.provider`
  (as `SCM target: url=..., branch=..., path=..., provider=...`)
- `blazemeter.workspaceId`, `blazemeter.projectId`, `blazemeter.testName`
  (as `BlazeMeter target: workspaceId=..., projectId=..., testName=...`)

Any other keys pass through the task payload untouched but are not
prepended to the LLM system message.

---

## 3. Example A2A request

Minimum request that a `curl` or Python client can send to the A2A
server (port 8001) to kick off a full new-JMX pipeline:

```json
{
  "id": "task-2026-08-23-001",
  "message": {
    "role": "user",
    "parts": [
      {
        "type": "text",
        "text": "Build a JMeter script for the checkout flow from this HAR: /shared/hars/checkout.har. Push it to Git and provision a BlazeMeter test for QA."
      }
    ]
  },
  "metadata": {
    "environment": "QA",
    "test_run_id": "2026-08-23-14-30-00",
    "upstream_framework": "acme-perf-hub",
    "scm": {
      "url": "https://github.com/your-org/your-perf-repo",
      "createBranch": true
    }
  }
}
```

The orchestrator will respond with a `task_id`. Downstream steps
(`script-agent` delegation, `provision_performance_test`, and any HITL
gates) all happen asynchronously; monitor them via
`GET /agents/orchestrator/tasks/get?task_id=...` or the SSE stream.

---

## 4. User-attributed vs user-agnostic token flow

Every call into the GitHub MCP resolves an authentication token in the
following order (see [github-mcp README](../../mcp-perf-suite/github-mcp/README.md#token-resolution)):

1. **Explicit `token` argument on the tool call** — used when the
   upstream framework or the Web UI attaches a per-user PAT via A2A
   metadata (`scm.token`, encrypted in browser session storage on the
   Web UI path). Every response carries `token_source="argument"` and
   `no_user_attribution=false`.
2. **`GITHUB_PERSONAL_ACCESS_TOKEN` env var** — user-agnostic
   fallback used when the caller omits a token. Responses carry
   `token_source="env"` and `no_user_attribution=true` so downstream
   consumers can flag "system-token push" in audit logs.
3. **`GITHUB_TOKEN` env var** — last-resort backup, same
   `no_user_attribution` behaviour as `#2`.

Best-practice guidance for upstream frameworks:

- If you have a real signed-in user, forward their encrypted PAT and
  keep `no_user_attribution=false`.
- If you do not, fall back to the server env var but mark the push
  as system-attributed in your audit trail. This is the only way a
  fully autonomous nightly run can push script updates without a
  human present.

---

## 5. HITL gates

Two gates are relevant to this pipeline and are toggled in
`agent-framework/backend/agents/orchestrator/config.yaml`:

| Config key | When it fires |
|------------|---------------|
| `hitl.require_approval_before_test_provision` | Before `provision_performance_test` creates a new BlazeMeter test. This is the recommended gate for reviewing a `smoke_status="FAIL"` warning. |
| `hitl.require_approval_before_test_start` | Before an actual load run against the provisioned test. |

Both gates apply uniformly to A2A callers and human Web UI users. The
orchestrator does not skip HITL just because the trigger came from
another AI framework.

---

## 6. Related documents

- [github-mcp README](../../mcp-perf-suite/github-mcp/README.md)
- [blazemeter-mcp README §New-JMX Pipeline](../../mcp-perf-suite/blazemeter-mcp/README.md#-new-jmx-pipeline-a2a--web-ui)
- [jmeter-mcp README §New-JMX Pipeline Handoff](../../mcp-perf-suite/jmeter-mcp/README.md#-new-jmx-pipeline-handoff-a2a--web-ui)
- [orchestrator INSTRUCTIONS §9.7](../../agent-framework/backend/agents/orchestrator/INSTRUCTIONS.md)
- [Script Agent INSTRUCTIONS §2.1](../../agent-framework/backend/agents/script-agent/INSTRUCTIONS.md)
- [Execution Agent INSTRUCTIONS §3.4](../../agent-framework/backend/agents/execution-agent/INSTRUCTIONS.md)
- [environments.example.yaml](../../agent-framework/backend/config/environments.example.yaml)
