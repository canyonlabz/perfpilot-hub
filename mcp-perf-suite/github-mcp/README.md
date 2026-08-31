# 📦 GitHub MCP Server

Minimal GitHub Contents API bridge for the PerfPilot performance
testing pipeline. Its job is narrow: publish a locally-generated JMX
(or supporting file) to a `owner/repo/branch/path` chosen by the
caller, creating the branch if needed. This is the version-control
step in the A2A + Web UI JMX pipeline; it does not clone repos, open
PRs, or read history.

## Features

- Push a file to a GitHub repo path on a branch (`push_jmx`).
- Idempotently create a branch off the repo default (`ensure_branch`).
- Discover the repo default branch for URL / branch defaulting
  (`get_repo_default_branch`).
- Per-request token support: pass `token` on any tool call to
  override the server-side env var. When the env var fallback fires,
  every response carries `no_user_attribution=true` so upstream can
  flag "system-token push".

## Prerequisites

- Python 3.10+
- A GitHub Personal Access Token (fine-grained or classic) with
  Contents: Read and write on the target repos, or the classic `repo`
  scope. See [.env.example](./.env.example) for details.

## Getting Started

1. Copy `.env.example` to `.env` and fill in the token, OR set the
   `GITHUB_PERSONAL_ACCESS_TOKEN` env var at the user/machine level.
2. Copy `config.example.yaml` to `config.yaml` (or the platform-specific
   `config.windows.yaml` / `config.mac.yaml`) if you need to override
   `api_base_url`, `ssl_verification`, or the default commit message.
3. Install dependencies (`pip install -r requirements.txt`) or let the
   gateway launch the server from the checked-in `.venv/`.
4. Run standalone for testing: `python github.py`.

## Available MCP Tools

When mounted through the gateway under namespace `github`, tool names
become `github_<tool>` (e.g. `github_push_jmx`).

| Tool | Purpose |
|------|---------|
| `push_jmx` | Upload a local file to `owner/repo/branch/path`. Auto-creates the branch when requested. Defaults path to `performance/<basename>`. |
| `ensure_branch` | Create the branch off a base (default: repo default branch). Idempotent. |
| `get_repo_default_branch` | Look up the default branch name for a repo URL. |

## Configuration

See `config.example.yaml`. The `github.ssl_verification` knob follows
the same convention as `blazemeter-mcp` and `confluence-mcp`:

- `ca_bundle` (default) reads `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`.
- `disabled` skips TLS verification (dev only).

## Token resolution

Every tool call resolves the token in this order:

1. Explicit `token` argument on the tool call (Web UI or user-attributed
   A2A path).
2. `GITHUB_PERSONAL_ACCESS_TOKEN` env var (user-agnostic A2A fallback,
   local dev).
3. `GITHUB_TOKEN` env var (last-resort backup).

If none are present the tool raises `GitHubAuthError` immediately
rather than issuing an unauthenticated request that GitHub would
reject with a misleading 404.

Tokens are never logged or written to disk. Error messages redact any
URL user-info.

## Related Projects

- [confluence-mcp](../confluence-mcp/) - same MCP server style; same
  `ssl_verification` knob.
- [blazemeter-mcp](../blazemeter-mcp/) - `create_test` +
  `upload_test_files` complete the A2A pipeline on the BlazeMeter side.
- [gateway-mcp](../gateway-mcp/) - mounts this server under namespace
  `github`.
