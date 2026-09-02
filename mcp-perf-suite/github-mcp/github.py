"""GitHub MCP Server.

Minimal GitHub Contents API bridge for the PerfPilot performance
testing pipeline. The primary use case is pushing generated JMX scripts
(and their data files) to a Git repo/branch/path chosen by the caller.

Tools
-----

- ``push_jmx`` - upload a local file to a repo path on a branch,
  creating the branch first when requested.
- ``ensure_branch`` - idempotent branch create.
- ``get_repo_default_branch`` - discover the default branch name for
  URL / branch defaulting.

Token resolution matches ``services.github_api.resolve_token``:
explicit ``token`` argument wins over ``GITHUB_PERSONAL_ACCESS_TOKEN``
env var. See ``services/github_api.py`` for details. Every write tool
returns a ``token_source`` field so callers can surface "no user
attribution" when a Scenario 2 server-side token was used.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastmcp import Context, FastMCP

from services.github_api import (
    DEFAULT_COMMIT_MESSAGE,
    GitHubAuthError,
    GitHubError,
    GitHubURLError,
    ensure_branch as api_ensure_branch,
    get_repo_default_branch as api_get_repo_default_branch,
    parse_repo_url,
    put_file,
    resolve_token,
)
from utils.config import load_config

log = logging.getLogger("github-mcp")

config = load_config()
mcp = FastMCP("github")


def _token_source_is_env(source: str) -> bool:
    """True when the token came from the env var (Scenario 2 fallback)."""
    return source.startswith("env:")


@mcp.tool
async def push_jmx(
    local_path: str,
    repo_url: str,
    branch: Optional[str] = None,
    path: Optional[str] = None,
    message: Optional[str] = None,
    token: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> dict:
    """Upload a local file (typically a JMX) to a GitHub repo path.

    Args:
        local_path: Absolute or repo-relative path to the file on disk.
        repo_url: GitHub HTTPS URL (``https://github.com/{owner}/{repo}``,
            optionally with ``.git``, trailing slash, or ``/tree/...``
            suffix). Only ``github.com`` is supported.
        branch: Target branch. Defaults to the repo default branch
            when omitted.
        path: Path inside the repo. Defaults to ``performance/{basename}``
            when omitted (tool-agnostic prefix by design).
        message: Commit message. Defaults to the value configured in
            ``config.yaml`` under ``github.default_commit_message``.
        token: Optional per-request GitHub token. When omitted, falls
            back to ``GITHUB_PERSONAL_ACCESS_TOKEN`` /
            ``GITHUB_TOKEN`` env vars.

    Returns:
        ``{
            "html_url": str,
            "commit_sha": str,
            "branch": str,
            "path": str,
            "owner": str,
            "repo": str,
            "token_source": str,           # "caller" | "env:GITHUB_..."
            "no_user_attribution": bool,   # True when Scenario 2 fired
            "branch_created": bool,
            "size_bytes": int,
        }``
    """
    if not local_path or not isinstance(local_path, str):
        raise ValueError("local_path is required")

    src = Path(local_path)
    if not src.is_file():
        raise FileNotFoundError(f"Local file not found: {local_path}")

    content_bytes = src.read_bytes()
    size_bytes = len(content_bytes)

    resolved_token = resolve_token(token)
    repo_ref = parse_repo_url(repo_url)

    target_branch = (branch or "").strip()
    if not target_branch:
        target_branch = await api_get_repo_default_branch(
            repo_ref.owner, repo_ref.repo, token=resolved_token,
        )
        branch_status = {"created": False, "branch": target_branch, "base": None}
    else:
        branch_status = await api_ensure_branch(
            repo_ref.owner,
            repo_ref.repo,
            target_branch,
            token=resolved_token,
        )

    target_path = (path or "").strip().lstrip("/\\")
    if not target_path:
        target_path = f"performance/{src.name}"

    commit_message = (message or "").strip() or DEFAULT_COMMIT_MESSAGE

    log.info(
        "push_jmx: %s -> %s/%s@%s:%s (bytes=%d, token_source=%s)",
        src.name,
        repo_ref.owner,
        repo_ref.repo,
        target_branch,
        target_path,
        size_bytes,
        resolved_token.source,
    )

    response = await put_file(
        repo_ref.owner,
        repo_ref.repo,
        target_path,
        target_branch,
        content_bytes,
        commit_message,
        token=resolved_token,
    )

    content_info = response.get("content") or {}
    commit_info = response.get("commit") or {}

    return {
        "html_url": content_info.get("html_url"),
        "commit_sha": commit_info.get("sha"),
        "branch": target_branch,
        "path": target_path,
        "owner": repo_ref.owner,
        "repo": repo_ref.repo,
        "token_source": resolved_token.source,
        "no_user_attribution": _token_source_is_env(resolved_token.source),
        "branch_created": bool(branch_status.get("created")),
        "size_bytes": size_bytes,
    }


@mcp.tool
async def ensure_branch(
    repo_url: str,
    branch: str,
    base: Optional[str] = None,
    token: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> dict:
    """Idempotently ensure a branch exists in a GitHub repo.

    Returns::

        {
            "created": bool,
            "branch": str,
            "base": str | None,   # base branch used, None when branch existed
            "sha": str,
            "owner": str,
            "repo": str,
            "token_source": str,
            "no_user_attribution": bool,
        }
    """
    if not branch or not isinstance(branch, str):
        raise ValueError("branch is required")

    resolved_token = resolve_token(token)
    repo_ref = parse_repo_url(repo_url)

    status = await api_ensure_branch(
        repo_ref.owner,
        repo_ref.repo,
        branch.strip(),
        base=(base or None),
        token=resolved_token,
    )
    return {
        **status,
        "owner": repo_ref.owner,
        "repo": repo_ref.repo,
        "token_source": resolved_token.source,
        "no_user_attribution": _token_source_is_env(resolved_token.source),
    }


@mcp.tool
async def get_repo_default_branch(
    repo_url: str,
    token: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> dict:
    """Return the default branch name for a GitHub repo.

    Returns::

        {
            "default_branch": str,
            "owner": str,
            "repo": str,
            "token_source": str,
            "no_user_attribution": bool,
        }
    """
    resolved_token = resolve_token(token)
    repo_ref = parse_repo_url(repo_url)

    default_branch = await api_get_repo_default_branch(
        repo_ref.owner, repo_ref.repo, token=resolved_token,
    )
    return {
        "default_branch": default_branch,
        "owner": repo_ref.owner,
        "repo": repo_ref.repo,
        "token_source": resolved_token.source,
        "no_user_attribution": _token_source_is_env(resolved_token.source),
    }


if __name__ == "__main__":
    transport = os.environ.get("GITHUB_MCP_TRANSPORT", "stdio")
    if transport == "http":
        host = os.environ.get("GITHUB_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("GITHUB_MCP_PORT", 8010))
        mcp.run(transport="http", host=host, port=port)
    else:
        mcp.run(transport="stdio")
