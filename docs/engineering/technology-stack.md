# 🧰 PerfPilot Technology Stack

Last updated: October 25, 2026

## 🎯 Purpose

This document records PerfPilot's technology baseline and the official references
to consult when changing an integration. See [Development workflow](development-workflow.md)
for coding, testing, logging, and documentation conventions.

Installed versions below come from maintainer-supplied local command output on
October 25, 2026. They describe the inspected local environment, not verified
container versions. Dependency constraints describe allowed versions; they are
not exact installation pins.

## 🧩 Components and locations

| Component | Repository location | Purpose |
| --- | --- | --- |
| Python backend | `agent-framework/backend/` | AG2 orchestration, specialist agents, A2A, and AG-UI services |
| Frontend application | `agent-framework/frontend/ui/` | Next.js application using React, CopilotKit, and the AG-UI client |
| MCP suite | `mcp-perf-suite/` | MCP services and their supporting implementation |
| Deployment files | `docker/` and component-specific deployment files | Container configuration; inspect the affected files for exact service definitions |
| Automated tests | `tests/` | Maintained unit/regression coverage, following the existing component layout |
| Public documentation | `docs/` | Architecture, integration, and development documentation |
| Cursor rules | `.cursor/rules/` | Coding-agent instructions and approval restrictions |

PerfPilot uses an AG2 orchestrator and specialist agents, with MCP servers as tools.
A2A supports upstream agent integrations; AG-UI connects the backend with the Web UI.
Each approved component is containerized. Preserve the established service boundaries
when changing deployment configuration.

## ⚙️ Runtime baseline

| Runtime | Version | Evidence |
| --- | --- | --- |
| Python | `3.12.4` | Local `python --version` output |
| Node.js | `25.2.1` | Maintainer-supplied runtime target; not independently checked in the supplied output |

The frontend manifest supplied for this baseline has no Node.js `engines` constraint.
Do not infer the Node.js runtime from `@types/node`.

## 🐍 Backend dependencies

| Package | Declared requirement | Installed version |
| --- | --- | --- |
| `ag2[ag-ui]` | `>=0.13.3,<0.14` | `ag2==0.13.3` |
| `ag-ui-protocol` | No separate direct requirement supplied | `0.1.19` |
| `fastmcp` | `>=3.4.2,<4` | `3.4.2` |
| `fastapi` | `>=0.115.0` | `0.136.3` |
| `starlette` | `>=1.2.1` | `1.2.1` |
| `structlog` | `>=24.0.0` | `26.1.0` |

Other supplied backend requirements:

| Package | Declared requirement |
| --- | --- |
| `uvicorn[standard]` | `>=0.32.0` |
| `sse-starlette` | `>=2.1.3` |
| `asyncpg` | `>=0.30.0` |
| `pydantic` | `>=2.10.0` |
| `httpx` | `>=0.28.1` |
| `openai` | `>=2.30.0` |
| `pyyaml` | `>=6.0.2` |
| `python-dotenv` | `>=1.2.1` |
| `tiktoken` | `>=0.12.0` |

No A2A SDK package is installed according to the maintainer. Follow the existing
server implementation; do not assume SDK-specific classes or install an SDK as
an incidental change.

These version observations are not an environment-health certification. Resolve
unrelated local package issues separately from documentation changes.

## 🖥️ Frontend dependencies

Application: `perfpilot-ui`, manifest version `0.1.0`, private package.
Manifest location: `agent-framework/frontend/ui/package.json`.

| Package | Manifest constraint | Installed version |
| --- | --- | --- |
| `@ag-ui/client` | `^0.0.57` | `0.0.57` |
| `@copilotkit/react-core` | `^1.60.1` | `1.60.1` |
| `@copilotkit/react-ui` | `^1.60.1` | `1.60.1` |
| `@copilotkit/runtime` | `^1.60.1` | `1.60.1` |
| `next` | `^15.0.0` | `15.5.19` |
| `react` | `^19.0.0` | `19.2.7` |
| `react-dom` | `^19.0.0` | `19.2.7` |
| `tailwindcss` | `^3.4.0` | `3.4.19` |
| `typescript` | `^5.6.0` | `5.9.3` |

Additional manifest constraints:

| Package | Constraint |
| --- | --- |
| `@tailwindcss/typography` | `^0.5.20` |
| `class-variance-authority` | `^0.7.0` |
| `clsx` | `^2.1.0` |
| `lucide-react` | `^0.400.0` |
| `react-markdown` | `^10.1.0` |
| `remark-gfm` | `^4.0.1` |
| `tailwind-merge` | `^2.2.0` |
| `@types/node` | `^22.0.0` |
| `@types/react` | `^19.0.0` |
| `@types/react-dom` | `^19.0.0` |
| `autoprefixer` | `^10.4.0` |
| `null-loader` | `^4.0.1` |
| `postcss` | `^8.4.0` |

### Declared frontend scripts

Run frontend commands from `agent-framework/frontend/ui/`.

| Script | Declared command |
| --- | --- |
| `dev` | `next dev` |
| `build` | `next build` |
| `start` | `next start` |
| `lint` | `next lint` |

These are manifest declarations, not verified execution results. No test script
or explicit frontend test runner is declared in the supplied manifest. Follow
existing lockfiles and package-manager conventions; do not introduce another
lockfile or test framework without approval.

## 🔗 Integration requirements

### A2A

Target A2A v1.0.0. Follow the existing server implementation and the public
[A2A data contract](../a2a/a2a-server-data-contracts.md). The October 2026 changelog
documents JSON-RPC as the primary binding and REST as supported. Verify affected
behavior against the normative specification; documentation alone is not proof
of compliance.

Keep protocol-wide requirements separate from PerfPilot-specific input rules.
Check applicable message/task schemas, method names, errors, streaming, and Agent
Cards before changing the contract. Do not use legacy requirements comments as
an authoritative source for v1 method names.

### AG2 and AG-UI

Match AG2 APIs to installed `0.13.3` behavior and preserve the existing
orchestrator/specialist design. The Python `ag-ui-protocol` package and JavaScript
`@ag-ui/client` are distinct dependencies: verify their event contract rather
than assuming their version numbers should match.

### Next.js, React, and CopilotKit

Match guidance to the installed versions above. Inspect routing, server/client
boundaries, runtime endpoints, hooks, and components before editing integration
code. Preserve the AG-UI event contract and keep credentials out of browser code.

### FastMCP

Target the approved version-3 implementation and `>=3.4.2,<4` requirement. Use
version-3 documentation or matching release source, not examples from another
major release. Preserve existing gateway, namespace, tool, and transport contracts.
Future readiness does not authorize a v4 migration or speculative compatibility code.

### Logging and containers

Agent Framework logging is implemented in
`agent-framework/backend/utils/logging_config.py`. It is OTEL-shaped structured
logging using Python logging and structlog, not an official OpenTelemetry SDK
integration. See the development workflow before adding events or integrations.

Inspect actual Dockerfiles and Compose definitions for service names, images,
ports, health checks, and runtime versions. Local package output does not verify
container contents.

## 📚 Official technical references

Use version-specific documentation or matching source tags where available.
A moving `latest` page is a discovery link, not permission to change the approved
project version. Record the release and relevant sections actually consulted in
non-trivial implementation plans.

| Technology | Reference | Version context |
| --- | --- | --- |
| A2A | https://a2a-protocol.org/latest/specification/ | Verify against approved v1.0.0 |
| AG2 | https://docs.ag2.ai/ | Match installed 0.13.3 |
| AG2 AG-UI integration | https://docs.ag2.ai/docs/user-guide/ag-ui/ | Verify examples against installed AG2 |
| AG-UI | https://docs.ag-ui.com/introduction | Check relevant Python/JavaScript release contracts |
| CopilotKit | https://docs.copilotkit.ai/ | Match installed 1.60.1 packages |
| Next.js | https://nextjs.org/docs | Match Next.js 15.5.19; select v15 guidance |
| React | https://react.dev/ | Match React 19 behavior |
| FastMCP | https://gofastmcp.com/ and https://github.com/PrefectHQ/fastmcp | Select v3 documentation or matching release source |
| Python | https://docs.python.org/3.12/ | Python 3.12 |
| Node.js | https://nodejs.org/docs/latest-v25.x/api/ | Node.js v25; verify patch-specific behavior if relevant |
| Docker | https://docs.docker.com/ | Match the actual Engine/Compose versions |

Keep these references current when approved versions or integration contracts
change. Do not update observed-version entries unless new evidence is available.
