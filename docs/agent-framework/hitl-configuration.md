# HITL Configuration Guide

Reference for operators and developers who need to configure, extend, or
troubleshoot the PerfPilot Agents framework's Human-in-the-Loop (HITL)
approval gates.

HITL policy is **framework-wide** — the same rules apply to every
specialist agent, whether the caller is the Web UI, another A2A agent
framework, an SDK client, or an internal orchestrator delegation. There
is one source of truth (`agent-framework/backend/config/hitl.yaml`)
and one enforcer (`agent-framework/backend/services/hitl_gates.py`).

---

## 1. What HITL is and when it fires

A HITL gate pauses a specialist task at a well-defined checkpoint and
waits for a human to explicitly approve or reject the action. Until the
human decides (or the timeout fires), the specialist task sits in a
`waiting-for-human` state and no further work happens on that task.

The framework fires HITL gates at two points:

- **Task-start gates** — when a task begins and the payload declares
  which tool the agent will run (typical structured caller path), the
  framework can pause **before the tool runs** based on the
  `(agent_name, tool)` combination.
- **Per-tool gates** — when a task is free-form (e.g., a Web UI chat
  message with no pre-declared tool), the framework runs a per-tool
  hook that intercepts each individual MCP tool call inside the agent's
  multi-turn loop, pausing on the specific tool that matches a rule.

Both paths use the same rule list and the same YAML config, so operators
never need to think about the two paths separately — the framework picks
the right enforcement point automatically.

**Uniform treatment of A2A callers:** requests from upstream A2A agent
frameworks are treated the same as human requests. HITL gates apply
uniformly regardless of caller surface. There is no "trust A2A callers"
bypass.

---

## 2. File location and loader order

### The config file

```
agent-framework/backend/config/hitl.yaml          (gitignored, operator override)
agent-framework/backend/config/hitl.example.yaml  (committed template & fallback)
```

Copy `hitl.example.yaml` to `hitl.yaml` and edit `hitl.yaml`. The
`.yaml` variant is gitignored, so operator-specific tuning never leaks
into the repository. If `hitl.yaml` is absent, the framework falls back
to `hitl.example.yaml` — but with a caveat below.

### Loader lookup order

`utils/config_loader.py::load_hitl_config()` walks these candidates in
order and returns the first one that parses:

1. `agent-framework/backend/config/hitl.yaml`
2. `agent-framework/backend/config/hitl.example.yaml`

The result is cached process-wide. Restart the backend after editing.

### Encoding notes

- The loader accepts UTF-8 with an optional BOM (`utf-8-sig`).
- Malformed YAML at either location produces a WARNING and the loader
  returns `{}` (empty dict). Every resolver in `hitl_gates.py` handles
  an empty config gracefully by falling back to safe defaults **plus**
  a WARNING (see [§7 Troubleshooting](#7-troubleshooting)).

---

## 3. YAML schema reference

The full schema, section by section. Every key is optional at the YAML
level — missing keys fall back to safe defaults with a WARNING. Explicit
values are **strongly preferred** for reproducible behavior across
environments.

### 3.1 `gates:` — approval toggles

```yaml
gates:
  require_approval_before_test_start: false
  require_approval_before_test_provision: false
  require_approval_before_publish: true
```

| Key | Type | Fallback if missing | What it gates |
|---|---|---|---|
| `require_approval_before_test_start` | bool | `false` + WARNING | The `start_performance_test` tool on the execution-agent. Blocks starting a load run against a provisioned BlazeMeter test until a human approves. |
| `require_approval_before_test_provision` | bool | `false` + WARNING | The `provision_performance_test` tool on the execution-agent. Blocks creating a NEW BlazeMeter test from a fresh JMX until a human approves — the recommended gate for reviewing a `smoke_status="FAIL"` warning. |
| `require_approval_before_publish` | bool | `false` + WARNING | Every tool listed in `publish_tools:` (see §3.2) on the reporting-agent. Blocks publishing report content to Confluence until a human approves. |

**Legacy flat schema also accepted.** For backward compatibility with
older operator configs, the resolver also accepts these keys at the top
level (i.e., without a `gates:` parent). This lets a raw copy-paste of
the old `hitl:` block from `orchestrator/config.yaml` continue to work.
The nested `gates:` form is the documented preferred shape.

```yaml
# Legacy — works but deprecated. Prefer the gates: form above.
require_approval_before_publish: true
```

### 3.2 `publish_tools:` — publish-gate scope

```yaml
publish_tools:
  - confluence_create_page
  - confluence_update_page
```

Defines **which** tools are treated as "publish tools" and therefore
trigger the publish gate when `gates.require_approval_before_publish` is
`true`. Entries are **fully-qualified MCP tool names** in the form
`{namespace}_{tool}`.

Semantics:

- Listing a tool here makes it **more restricted** (a HITL card fires
  before that tool runs), not less.
- The publish gate only fires on tools in this list. Tools NOT in this
  list are exempt from the gate (regardless of whether they modify data).
- Read-only Confluence queries (`confluence_search`, `confluence_get_page`,
  etc.) are exempt because they are not listed.
- The list is **the sole source of truth** for the publish-tool scope.
  There is no code-side default list.

| Fallback if missing/malformed | Behavior |
|---|---|
| `frozenset()` + WARNING | The publish gate becomes a no-op (nothing is gated) even if the toggle in §3.1 is `true`. A WARNING fires once per process so operators notice. |

**Special values:**

- **Empty list (`publish_tools: []`)** — deliberate no-op. The publish
  gate has no tools to gate, so it never fires even when the toggle is
  `true`. Useful for local dev / smoke tests where you want the toggle
  documented but not enforced. **Emits no WARNING** because the empty
  list is a valid explicit choice.
- **Add `confluence_attach_images`** — stricter policy: require a second
  HITL card before uploading images. Default behavior omits this because
  the human already approved the `confluence_create_page` /
  `confluence_update_page` that this attach follows.

### 3.3 Polling and timeouts

```yaml
poll_interval_seconds: 2
timeout_seconds: 300
```

| Key | Type | Fallback if missing/malformed | Semantics |
|---|---|---|---|
| `poll_interval_seconds` | number | `2.0` + WARNING | How often the enforcer polls the `hitl_approvals` DB row for a decision. Lower = snappier UI; higher = less DB load. Values below `0.1` are clamped up (in `wait_for_decision`) to prevent tight-loop hammering. |
| `timeout_seconds` | number | `300.0` + WARNING | Maximum seconds a HITL gate blocks before treating the outcome as `timeout` (functionally a rejection for the specialist, but distinguished in the log). 5 minutes is generous for interactive workflows; production deployments running long unattended review cycles should raise this. |

Both values apply framework-wide to:

- Task-start enforcers (`enforce_at_task_start`)
- Per-tool enforcers (`enforce_at_tool_call`)
- The orchestrator's `request_human_approval` tool (unless the caller
  passes explicit per-call overrides)

---

## 4. Managing the publish-tool set (operator guide)

### 4.1 Add a tool to the publish gate

To require HITL approval before another MCP tool runs, add its
fully-qualified name to `publish_tools:` in `hitl.yaml`:

```yaml
publish_tools:
  - confluence_create_page
  - confluence_update_page
  - confluence_attach_images        # ← newly gated
```

Restart the backend. From that point on, any reporting-agent task that
invokes `confluence_attach_images` will pause at a HITL card until
approved.

### 4.2 Remove a tool from the publish gate

Delete the line for the tool from `publish_tools:` in `hitl.yaml`.
Restart the backend. Tasks that call the removed tool will now proceed
without a HITL pause.

### 4.3 Disable the publish gate entirely

Two equivalent ways:

**Option A — flip the toggle** (keeps the tool list for later):

```yaml
gates:
  require_approval_before_publish: false
```

**Option B — empty the tool set** (keeps the toggle enabled but empties
its scope):

```yaml
publish_tools: []
```

Both make the publish gate a no-op. Option A is preferred for temporary
disables; Option B is preferred when documenting a "no tools currently
require gating in this environment" stance.

### 4.4 Namespace convention for tool names

Fully-qualified MCP tool names follow `{namespace}_{tool}`. The
namespace is the MCP server that hosts the tool. For the built-in MCPs:

| MCP server | Namespace prefix | Example gated tools |
|---|---|---|
| Confluence | `confluence_` | `confluence_create_page`, `confluence_update_page`, `confluence_attach_images` |
| BlazeMeter | `blazemeter_` | `blazemeter_start_test`, `blazemeter_stop_test` (not gated by default — gated by the two `test_start` / `test_provision` toggles at the agent level instead) |
| JMeter | `jmeter_` | (no write actions gated by default) |
| GitHub | `github_` | `github_push_file`, `github_create_branch` (not gated by default; add if your workflow requires review) |

Consult the target MCP server's `README.md` for its full tool list.

---

## 5. Adding a new HITL gate (developer guide)

Extending the framework with a new gate takes two files: one to define
the rule, one to opt operators in.

### 5.1 Add the rule to `hitl_gates.py`

Rules are declarative — a frozen dataclass with match criteria and a
prompt template. Open
`agent-framework/backend/services/hitl_gates.py` and add a new
`HitlGateRule` to the list returned by `_build_rules()`:

```python
def _build_rules() -> list[HitlGateRule]:
    return [
        # ... existing rules ...
        HitlGateRule(
            config_key="require_approval_before_data_export",
            agent_names=("analysis-agent",),
            tools=frozenset({"export_analysis_data"}),
            title="Approve Analysis Data Export",
            summary_template=(
                "The {agent_name} wants to export analysis data for "
                "test_run_id={test_run_id} to {destination}. "
                "Approve to proceed or reject to cancel."
            ),
        ),
    ]
```

Notes on the `HitlGateRule` fields:

| Field | Type | Notes |
|---|---|---|
| `config_key` | `str` | Key operators add under `gates:` in `hitl.yaml`. Convention: `require_approval_before_<action>`. |
| `agent_names` | `tuple[str, ...]` | Specialist agent folder names the rule applies to. Use a tuple even for a single agent. |
| `tools` | `Optional[frozenset[str]]` | Fully-qualified MCP tool names, OR `None` to match any tool for the listed agents. Use `None` sparingly — it fires on every task for those agents. |
| `title` | `str` | Short human-readable title on the HITL approval card. |
| `summary_template` | `str` | `str.format_map`-style template. Placeholders `{agent_name}`, `{tool}`, and any key from the task payload's `args` dict are interpolated. Unknown placeholders render as literal `{key}` (via `_SafeFormatMap`), never raise. |

### 5.2 Add the toggle to `hitl.example.yaml`

So operators can find it, add the new `config_key` to the `gates:`
block in `agent-framework/backend/config/hitl.example.yaml` with a
sensible default and a comment describing what it gates:

```yaml
gates:
  # ... existing gates ...
  require_approval_before_data_export: false   # Set true to require HITL before exporting analysis data
```

### 5.3 That's it — no other edits needed

The framework wires up the new rule automatically:

- `find_matching_rule()` will match the new `(agent_name, tool)` pair
  when the toggle is `true` and pause the specialist at the right point.
- Both enforcement paths (task-start and per-tool) pick it up without
  any change to `task_executor.py`.
- Operator overrides in `hitl.yaml` are respected on first load
  (restart the backend after editing).

### 5.4 Add a test

Whenever you add a rule, add a matching unit test to
`tests/agent_framework/services/test_hitl_gates.py`:

- One test that asserts `find_matching_rule(<agent>, <tool>)` returns
  the new rule when the toggle is `true`.
- One test that asserts it returns `None` when the toggle is `false` or
  absent.
- One test that asserts `build_prompt()` interpolates the template
  correctly given a representative payload.

---

## 6. Fallback behavior at a glance

Quick reference showing what happens when each YAML key is missing or
malformed. Every fallback also emits a WARNING (see §7).

| Missing/malformed key | Fallback | Effect on framework |
|---|---|---|
| `gates.<config_key>` | `false` | The gate is **open** — no HITL pause for that action. |
| `publish_tools` | `frozenset()` (empty) | The publish gate is a **no-op** — nothing is gated even if the toggle is on. |
| `poll_interval_seconds` | `2.0` | Polls the DB every 2 seconds. |
| `timeout_seconds` | `300.0` | Times out after 5 minutes with `decision="timeout"`. |

**Design rationale:** every fallback biases toward **not blocking the
framework** — the Web UI still starts, specialists still run, tasks still
complete. But every fallback also emits a WARNING so the degraded state
surfaces on the first affected operation. This is a deliberate fail-safe
policy: the framework prefers a noisy log over a hard startup failure
when config is incomplete, so operators can diagnose and fix without
losing service.

---

## 7. Troubleshooting

Every fallback path in `hitl_gates.py` emits a WARNING to the process
log. The same warning fires **once per process per key** (deduped via
`_warn_once()`) so a degraded config surfaces on first use without
flooding the logs on every subsequent task.

Look for these strings in your backend log. Each row lists the WARNING
text and the fix.

### 7.1 Publish gate silently doing nothing

**Symptom:** `gates.require_approval_before_publish` is `true` in
`hitl.yaml` but no HITL card appears when the reporting-agent calls
`confluence_create_page` or `confluence_update_page`.

**WARNING to look for:**

```
hitl.yaml is missing or malformed 'publish_tools:' — the publish gate
is now a no-op (nothing will be gated). Restore the key in
agent-framework/backend/config/hitl.yaml.
```

**Fix:** Restore the `publish_tools:` key with the tools you want to
gate. See [§4.1](#41-add-a-tool-to-the-publish-gate).

### 7.2 A gate toggle is silently open

**Symptom:** You added a new rule (or expect an existing one to fire)
but no HITL card appears for the matching `(agent, tool)`.

**WARNING to look for:**

```
hitl.yaml is missing gate 'gates.<key>' — defaulting to false (gate
open, no HITL). Add it under 'gates:' in
agent-framework/backend/config/hitl.yaml.
```

**Fix:** Add the missing key under `gates:` in `hitl.yaml`:

```yaml
gates:
  <key>: true
```

Restart the backend.

### 7.3 Poll interval fell back to 2.0 seconds

**Symptom:** HITL cards feel snappier than usual, or you set a custom
poll interval but it seems ignored.

**WARNING variants:**

Missing key:

```
hitl.yaml is missing 'poll_interval_seconds:' — defaulting to 2.0.
Set it in agent-framework/backend/config/hitl.yaml.
```

Malformed value (e.g. a string that isn't a number):

```
hitl.yaml has malformed 'poll_interval_seconds' (value=<x>);
falling back to 2.0. Fix the value in
agent-framework/backend/config/hitl.yaml.
```

**Fix:** Add or correct the key:

```yaml
poll_interval_seconds: 2
```

### 7.4 Timeout fell back to 300.0 seconds

**Symptom:** HITL cards time out after 5 minutes when you expected a
longer window.

**WARNING variants:**

Missing key:

```
hitl.yaml is missing 'timeout_seconds:' — defaulting to 300.0. Set it
in agent-framework/backend/config/hitl.yaml.
```

Malformed value:

```
hitl.yaml has malformed 'timeout_seconds' (value=<x>); falling back to
300.0. Fix the value in agent-framework/backend/config/hitl.yaml.
```

**Fix:** Add or correct the key:

```yaml
timeout_seconds: 300
```

### 7.5 A HITL gate timed out (per-event log, not deduped)

**Symptom:** A specialist task moved to a rejected/failed state after
about `timeout_seconds` without the human responding.

**WARNING variants** (both are per-event, not deduped — they fire every
time the timeout hits):

Task-start gate timeout:

```
HITL gate timed out for task <task_id> after <seconds>s
```

Per-tool gate timeout:

```
HITL gate timed out for task <task_id> tool=<fn_name> after <seconds>s
```

**Fix options:**
- **Extend `timeout_seconds`** — raise the framework-wide default in
  `hitl.yaml` if 300 seconds is too tight for your review workflow.
- **Per-call override** — if you're using the orchestrator's
  `request_human_approval` tool directly, pass a larger
  `timeout_seconds` argument on that specific call. Framework-enforced
  gates always use the YAML value.

### 7.6 Warning-dedup: I want the same WARNING to fire again

The `_warn_once()` dedup is process-scoped. To see a WARNING re-fire:

- **In production:** restart the backend process.
- **In tests:** call `hitl_gates._reset_warning_dedup()` — a test-only
  helper that clears the dedup set.

---

## 8. Related documents

- [a2a_new_jmx_pipeline_guide.md](./a2a_new_jmx_pipeline_guide.md) — A2A
  integration guide referencing the two `test_provision` and `test_start`
  gates.
- [blazemeter-mcp README](../../mcp-perf-suite/blazemeter-mcp/README.md)
  — Documents the `smoke_failed` warning path that the test-provision
  gate exists to catch.
- [`hitl.example.yaml`](../../agent-framework/backend/config/hitl.example.yaml)
  — The committed template file. Copy to `hitl.yaml` to customize.
- [`hitl_gates.py`](../../agent-framework/backend/services/hitl_gates.py)
  — The enforcer module. All matcher logic, rule definitions, and
  fallback resolvers live here.
