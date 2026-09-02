"""GitHub REST API client for the github-mcp server.

Thin async wrapper over the GitHub Contents / Git-Refs API using
``httpx``. Follows the same style as ``confluence-mcp/services/`` and
``blazemeter-mcp/services/`` (module-level dotenv + config load, per-call
``AsyncClient(verify=..., timeout=...)``, no shared client state).

Token resolution
----------------

Tokens are resolved per call in this order:

  1. Explicit ``token`` argument on the tool call.
     Sources: Web UI encrypted browser store, or user-attributed A2A
     (Scenario 1) forwarding the upstream user's token.
  2. ``GITHUB_PERSONAL_ACCESS_TOKEN`` env var.
     Source: user-agnostic A2A (Scenario 2) workaround; also the local
     dev fallback (same var Cursor's official GitHub MCP uses).
  3. ``GITHUB_TOKEN`` env var (backup for the same Scenario 2 case).
  4. Raise ``GitHubAuthError``.

The response of every write operation includes ``token_source`` so the
caller can annotate "no user attribution" when Scenario 2 fires.

Nothing here writes tokens to disk. Tokens are never logged. Error
messages redact any URL userinfo via ``_redact_url``.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Union
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from utils.config import load_config

load_dotenv()

log = logging.getLogger("github-mcp.api")

_config = load_config()
_github_cfg = _config.get("github", {}) or {}

GITHUB_API_BASE = _github_cfg.get("api_base_url", "https://api.github.com").rstrip("/")
HTTP_TIMEOUT_SECONDS = float(_github_cfg.get("http_timeout_seconds", 30))
DEFAULT_COMMIT_MESSAGE = _github_cfg.get(
    "default_commit_message",
    "chore(perf): publish JMX from perfpilot",
)

CA_BUNDLE = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GitHubError(Exception):
    """Base error raised by the github-mcp client."""


class GitHubAuthError(GitHubError):
    """Raised when no token is available or GitHub rejects the credentials."""


class GitHubURLError(GitHubError):
    """Raised when the caller-supplied URL cannot be parsed as a GitHub repo."""


# ---------------------------------------------------------------------------
# SSL / auth helpers
# ---------------------------------------------------------------------------


def get_ssl_verify_setting() -> Union[str, bool]:
    """Resolve the SSL verify setting from github-mcp config.

    Matches the ``ssl_verification`` knob used by ``confluence-mcp``:

      - ``"ca_bundle"`` (default): use ``REQUESTS_CA_BUNDLE`` /
        ``SSL_CERT_FILE`` if set, else default trust store.
      - ``"disabled"``: skip TLS verification (dev only).
    """
    setting = str(_github_cfg.get("ssl_verification", "ca_bundle")).lower()
    if setting == "disabled":
        return False
    if setting == "ca_bundle":
        return CA_BUNDLE or True
    return True


@dataclass(frozen=True)
class ResolvedToken:
    """Outcome of ``resolve_token()``.

    Attributes:
        value: The token string. Never empty on success.
        source: One of ``"caller"``, ``"env:GITHUB_PERSONAL_ACCESS_TOKEN"``,
            or ``"env:GITHUB_TOKEN"``. Used by the tool layer to flag
            Scenario 2 fallback (``no_user_attribution=True``).
    """

    value: str
    source: str


def resolve_token(caller_token: Optional[str] = None) -> ResolvedToken:
    """Resolve a GitHub token from the caller argument or env vars.

    Raises:
        GitHubAuthError: When no token is available. GitHub rejects
            unauthenticated writes on private repos with a 404, which is
            misleading; failing fast here is friendlier.
    """
    if caller_token and isinstance(caller_token, str) and caller_token.strip():
        return ResolvedToken(value=caller_token.strip(), source="caller")

    for env_key in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN"):
        env_val = os.environ.get(env_key)
        if env_val and env_val.strip():
            return ResolvedToken(value=env_val.strip(), source=f"env:{env_key}")

    raise GitHubAuthError(
        "No GitHub token available. Pass a `token` argument or set "
        "GITHUB_PERSONAL_ACCESS_TOKEN in the environment."
    )


def _auth_headers(token: str) -> dict:
    """Standard GitHub v3 REST headers with a bearer token."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "perfpilot-github-mcp/0.1.0",
    }


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


_ALLOWED_HOSTS = {"github.com", "www.github.com"}
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str


def parse_repo_url(url: str) -> RepoRef:
    """Parse ``https://github.com/{owner}/{repo}[...]`` into a ``RepoRef``.

    Raises:
        GitHubURLError: When the URL does not look like a GitHub repo.
    """
    if not url or not isinstance(url, str):
        raise GitHubURLError("Empty or non-string GitHub URL")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise GitHubURLError(f"Unsupported scheme in URL: {_redact_url(url)}")
    if (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
        raise GitHubURLError(
            f"Non-GitHub host in URL: {_redact_url(url)}"
        )

    parts = [seg for seg in parsed.path.split("/") if seg]
    if len(parts) < 2:
        raise GitHubURLError(f"Missing owner/repo in URL: {_redact_url(url)}")

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not _valid_name(owner) or not _valid_name(repo):
        raise GitHubURLError(f"Malformed owner/repo in URL: {_redact_url(url)}")

    return RepoRef(owner=owner, repo=repo)


def _valid_name(name: str) -> bool:
    if not name or name.startswith("."):
        return False
    return bool(_NAME_PATTERN.match(name))


def _redact_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
        return url
    except Exception:
        return "<unparseable-url>"


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


async def get_repo_default_branch(
    owner: str,
    repo: str,
    *,
    token: ResolvedToken,
) -> str:
    """GET /repos/{owner}/{repo} -> default_branch string."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    async with httpx.AsyncClient(
        verify=get_ssl_verify_setting(),
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as client:
        resp = await client.get(url, headers=_auth_headers(token.value))
        _raise_for_status(resp, context=f"get repo {owner}/{repo}")
        data = resp.json()
    branch = data.get("default_branch")
    if not branch:
        raise GitHubError(
            f"Repo {owner}/{repo} has no default_branch in the response"
        )
    return branch


async def get_ref_sha(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: ResolvedToken,
) -> Optional[str]:
    """GET /repos/{owner}/{repo}/git/ref/heads/{ref}. Returns None on 404."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{ref}"
    async with httpx.AsyncClient(
        verify=get_ssl_verify_setting(),
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as client:
        resp = await client.get(url, headers=_auth_headers(token.value))
        if resp.status_code == 404:
            return None
        _raise_for_status(resp, context=f"get ref heads/{ref}")
        data = resp.json()
    return (data.get("object") or {}).get("sha")


async def create_ref(
    owner: str,
    repo: str,
    ref: str,
    sha: str,
    *,
    token: ResolvedToken,
) -> None:
    """POST /repos/{owner}/{repo}/git/refs to create a new branch ref."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs"
    payload = {"ref": f"refs/heads/{ref}", "sha": sha}
    async with httpx.AsyncClient(
        verify=get_ssl_verify_setting(),
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as client:
        resp = await client.post(
            url,
            headers=_auth_headers(token.value),
            json=payload,
        )
        _raise_for_status(resp, context=f"create ref heads/{ref}")


async def ensure_branch(
    owner: str,
    repo: str,
    branch: str,
    *,
    base: Optional[str] = None,
    token: ResolvedToken,
) -> dict:
    """Idempotent branch create.

    Returns a status dict::

        {
            "created": bool,
            "branch": str,
            "base": str | None,   # base branch used, or None when branch existed
            "sha": str,           # commit sha the branch now points at
        }
    """
    existing_sha = await get_ref_sha(owner, repo, branch, token=token)
    if existing_sha:
        return {
            "created": False,
            "branch": branch,
            "base": None,
            "sha": existing_sha,
        }

    if base:
        base_branch = base
    else:
        base_branch = await get_repo_default_branch(owner, repo, token=token)

    base_sha = await get_ref_sha(owner, repo, base_branch, token=token)
    if not base_sha:
        raise GitHubError(
            f"Base branch '{base_branch}' not found in {owner}/{repo}; "
            "cannot create '{branch}'"
        )

    await create_ref(owner, repo, branch, base_sha, token=token)
    return {
        "created": True,
        "branch": branch,
        "base": base_branch,
        "sha": base_sha,
    }


async def get_content_sha(
    owner: str,
    repo: str,
    path: str,
    branch: str,
    *,
    token: ResolvedToken,
) -> Optional[str]:
    """GET /repos/{owner}/{repo}/contents/{path}?ref=... . Returns None on 404."""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
    params = {"ref": branch}
    async with httpx.AsyncClient(
        verify=get_ssl_verify_setting(),
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as client:
        resp = await client.get(
            url,
            headers=_auth_headers(token.value),
            params=params,
        )
        if resp.status_code == 404:
            return None
        _raise_for_status(resp, context=f"get contents {path}")
        data = resp.json()
    if isinstance(data, list):
        raise GitHubError(
            f"Path {path} is a directory in {owner}/{repo}@{branch}, not a file"
        )
    return data.get("sha")


async def put_file(
    owner: str,
    repo: str,
    path: str,
    branch: str,
    content_bytes: bytes,
    message: str,
    *,
    token: ResolvedToken,
) -> dict:
    """PUT /repos/{owner}/{repo}/contents/{path} — create or update a file.

    Returns the parsed JSON response body verbatim (contains ``content``
    and ``commit`` sub-dicts).
    """
    existing_sha = await get_content_sha(
        owner, repo, path, branch, token=token,
    )
    payload: dict[str, Any] = {
        "message": message,
        "branch": branch,
        "content": base64.b64encode(content_bytes).decode("ascii"),
    }
    if existing_sha:
        payload["sha"] = existing_sha

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
    async with httpx.AsyncClient(
        verify=get_ssl_verify_setting(),
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as client:
        resp = await client.put(
            url,
            headers=_auth_headers(token.value),
            json=payload,
        )
        _raise_for_status(resp, context=f"put contents {path}")
        return resp.json()


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _raise_for_status(resp: httpx.Response, *, context: str) -> None:
    """Turn HTTP errors into typed exceptions with a short body preview."""
    if resp.is_success:
        return

    body_preview = resp.text[:500] if resp.text else ""
    if resp.status_code in (401, 403):
        raise GitHubAuthError(
            f"GitHub auth error while {context} (HTTP {resp.status_code}): "
            f"{body_preview}"
        )
    raise GitHubError(
        f"GitHub API error while {context} (HTTP {resp.status_code}): "
        f"{body_preview}"
    )
