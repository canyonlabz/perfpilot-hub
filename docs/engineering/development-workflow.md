# 🛠️ PerfPilot Development Workflow

Last updated: October 25, 2026

## 🎯 Purpose

This document describes shared PerfPilot development conventions. The
[technology stack](technology-stack.md) records versions and official references.
Cursor-specific activation, command restrictions, and approvals remain in
`.cursor/rules/`. These documents do not grant permission to bypass those rules
or establish that checks are automatically enforced in CI.

## 1. 🌿 Branches, approvals, and scope

- Work on a confirmed feature branch, never `main`.
- Coding agents must obtain approval for all Git commands, including read-only
  commands. User confirmation may establish the current branch without running Git.
- If the branch is unknown, detached, or `main`, stop before editing and ask the
  user to select the intended feature branch.
- Do not autonomously commit, merge, submit PRs, push, or pull. Follow the existing
  project restrictions; a feature-branch requirement is not permission to use Git.
- Do not install dependencies or software. Ask the maintainer to install them.
- Keep commands within the repository boundary and changes within approved scope.
- Obtain separate approval for refactoring, signature changes, dependency/runtime
  changes, or work discovered outside the original task.
- During a documentation refresh, record implementation issues for follow-up
  rather than silently fixing them.

## 2. 🗺️ Plan and verify references

For non-trivial implementation:

1. Confirm scope, acceptance criteria, and branch.
2. Inspect affected code, existing tests, dependency files, and deployment files.
3. Read relevant sections of both engineering reference documents.
4. Consult official documentation matched to the affected installed versions.
5. Create a plan/task list using Plan Mode or `.cursor/docs/plans/`.
6. Ask focused questions about missing requirements and obtain final approval
   before code changes.
7. Revisit the plan if new evidence materially changes the work.

Internal plans are intended to remain gitignored. If that status is uncertain,
ask before writing sensitive material. Keep useful source URLs and release
assumptions in the plan or appropriate engineering documentation, not private
work-item references in source comments.

For documentation-only changes, verify the claims being edited. Do not research
unrelated technologies or introduce unnecessary environment-remediation work.

## 3. 🐍 Python module organization

Before adding a function or class, search for equivalent behavior and identify
its owning component. Reuse existing implementations when the responsibilities
and dependencies fit.

- Keep service modules focused on core service functionality and coordination.
- Place supporting reusable logic in focused, descriptively named helper modules
  near the owning component. Avoid a generic utilities dumping ground.
- Small private helpers may remain local when that preserves cohesion.
- Propose a new module/subpackage when no existing module is an appropriate owner.
- Do not add cross-component coupling merely to reuse a small helper.
- Follow established async patterns, typing, docstrings, and error handling.
- Moving existing code or restructuring modules requires approval.

### Module size

- Target fewer than 1,000 physical lines per hand-maintained Python module.
- Before growing beyond 1,000 lines, propose a responsibility-based split or
  explain why an exception is appropriate.
- Creating or growing a module beyond 1,500 lines requires explicit approval.
- Generated and vendored files are excluded. Flag existing oversized modules
  when relevant; do not automatically refactor them.
- Do not compress code or remove useful documentation to meet a line limit.

## 4. 📊 OTEL-shaped structured logging

Existing Agent Framework module:
`agent-framework/backend/utils/logging_config.py`.

- Inspect this module and existing call sites before adding logging.
- Use `log_event(...)` for new structured application events, following existing
  import conventions and field usage.
- Configure logging through the established startup path, not individual helpers.
- Preserve `DEPLOYMENT_MODE` local/cloud behavior and existing context handling.
- Do not add parallel handlers, logging frameworks, exporters, or synthetic
  trace/span IDs merely to populate fields.

The implementation uses stdlib logging and structlog to produce OTEL-shaped
records. It is not an official OpenTelemetry SDK integration, and empty context
fields do not demonstrate active tracing or telemetry export.

MCP services and frontend code need component-specific review before adding a
logging integration. Do not import Agent Framework internals into them by default.
An official OpenTelemetry migration is separate approval-required work.

Never log secrets or sensitive payloads. Follow existing redaction conventions.

## 5. 🧪 Tests and temporary diagnostics

Maintained unit/regression tests belong in the appropriate version-controlled
`tests/` tree and are intended for CI. Follow the existing layout, including root
`tests/agent_framework/` where applicable; do not create competing test trees.

- Use unit tests for isolated behavior and mock external dependencies.
- Use integration/contract tests when behavior crosses service, database, or
  protocol boundaries. Distinguish infrastructure-dependent checks from fast tests.
- Reuse existing fixtures, helpers, naming, and async-test conventions.
- Keep maintained CI tests out of the gitignored `scripts/` directory.
- Reserve `scripts/` for approved temporary diagnostics or operator utilities.
- Before creating a diagnostic script, consider repeatable regression coverage.
- Test enhancements incrementally and report actual commands, results, and gaps.
- Do not run load tests or tests against external or production-like targets
  without explicit authorization for the target and execution scope.

The supplied frontend manifest has no test script or explicit test runner.
Inspect existing tooling before proposing a minimal setup; do not install one
without approval. Absence of a runner does not establish that a change is tested.

Use existing verified test commands and CI configuration. Do not invent a command
or claim CI enforcement without checking the relevant files.

## 6. 🔌 Frontend, protocol, and deployment checks

Frontend application root: `agent-framework/frontend/ui/`.

Match documentation to installed Next.js, React, CopilotKit, and AG-UI packages.
Preserve server/client boundaries, endpoint configuration, and stream contracts.
Keep credentials out of browser-delivered code. Inspect scripts before running
them; a declared lint/build script is not evidence that it succeeds.

For A2A changes, read the public data contract and verify affected behavior against
the approved A2A v1.0.0 specification. Distinguish PerfPilot-specific input rules
from protocol requirements. No A2A SDK is currently installed; do not assume one.

For Docker changes, inspect actual build contexts, runtime images, service names,
ports, health checks, and secret handling. Do not build, start, stop, or modify
shared services without appropriate approval. Report unverified container behavior
rather than inferring it from local package versions.

## 7. 📚 Documentation and public content

Update affected documentation in the same change when behavior, configuration,
APIs, environment variables, deployment, or developer workflows change.

- Keep the version inventory and official-source registry in `technology-stack.md`.
- Keep detailed procedures here rather than copying them into every guide.
- Make public examples self-contained, sanitized, and accurate.
- Clearly distinguish implemented functionality from planned work.
- Keep credentials, private endpoints, internal plans, and work-item mappings
  out of public documentation and source comments/docstrings.
- Do not send private repository content to external research services without
  explicit approval.
- If documentation is unaffected, state why in the completion summary.

Tool-specific rules should retain critical safety instructions even when the
shared engineering documents describe the same restrictions.

## 8. 📝 Monthly changelog

- Active changelog: repository-root `CHANGELOG.md`.
- Monthly archives: `docs/changelogs/CHANGELOG-YYYY-MM.md`.

Before PR handoff, update the active changelog with completed changes, including
relevant agent-rule and documentation updates. Follow its existing month-based
format, thematic sections, table of contents, previous-month links, and
last-updated convention.

Preserve history. Follow the established rollover procedure when rotation is due.
The exact rollover trigger/date basis is not established in this reference; ask
before moving entries if no procedure is documented. Do not overwrite an archive,
duplicate a month's history, or invent a new rotation policy.

Changelog maintenance does not authorize Git operations or PR submission.

## 9. ✅ Completion checklist

- [ ] Feature branch and required approvals confirmed.
- [ ] Changes remain within the approved scope.
- [ ] Existing code reused or new module placement explained.
- [ ] Module-size and logging conventions followed.
- [ ] Relevant official references checked against the affected versions.
- [ ] Appropriate tests updated and actual results reported.
- [ ] Documentation updated, or non-applicability explained.
- [ ] Active changelog updated before PR handoff.
- [ ] Remaining assumptions and verification gaps disclosed.
- [ ] No unsupported claim of compliance, test success, or build success.
- [ ] No unauthorized Git operations, installations, or external test execution.
