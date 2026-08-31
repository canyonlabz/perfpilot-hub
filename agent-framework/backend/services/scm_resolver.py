"""Source control (SCM) target resolver for the agent framework.

Parses the GitHub repo URL supplied by an A2A caller or the Web UI and
fills in sensible defaults for branch and repo path so downstream code
(the GitHub MCP push tool, the Script Agent, etc.) always has a
complete ``{owner, repo, branch, path}`` tuple to work with.

Only HTTPS GitHub URLs are recognized in this slice. Azure DevOps Git,
JIRA, and SSH remotes are explicitly out of scope; they yield a
``None`` return so the caller can fail the request with a clear error.

Public API::

    parse_github_url(url)               -> Optional[GitHubRepoRef]
    resolve_scm_target(scm, ...)        -> Optional[ResolvedScmTarget]
    default_branch_name(test_run_id, environment)
    default_repo_path(jmx_basename)

Design notes
------------
The default repo path is tool-agnostic: ``performance/<basename>``, never
``jmeter/<basename>``. The user has an explicit ask that the framework
not lock into JMeter or BlazeMeter naming so the same layout keeps
working if the load-generation tool changes later.

Overrides supplied by the caller (branch, path) always win. When both
branch and path are absent, ``createBranch`` is forced to ``True`` so
the GitHub MCP knows to mint the branch before writing the file.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitHubRepoRef:
    """Parsed reference to a GitHub repository.

    Attributes:
        owner: Owner org or user (e.g. ``"canyonlabz"``).
        repo: Repository name without a ``.git`` suffix.
        host: Host portion of the URL, always ``"github.com"`` for this
            slice. Retained for future GitHub Enterprise support.
    """

    owner: str
    repo: str
    host: str = "github.com"


@dataclass(frozen=True)
class ResolvedScmTarget:
    """Fully resolved SCM push target.

    Attributes:
        provider: Only ``"github"`` in this slice; other providers are
            rejected upstream.
        owner: Repo owner.
        repo: Repo name.
        branch: Target branch. Populated with a minted default when the
            caller did not supply one.
        path: Path inside the repo where the artifact will be written,
            e.g. ``"performance/CheckoutFlow.jmx"``.
        create_branch: When ``True``, the GitHub MCP is expected to
            create the branch (from the repo default) before writing.
            When ``False``, the branch must already exist.
        url: The original URL as passed in, retained for logging and
            summary display. Never used to reconstruct owner / repo -
            those come from ``parse_github_url``.
    """

    provider: str
    owner: str
    repo: str
    branch: str
    path: str
    create_branch: bool
    url: str


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


_ALLOWED_HOSTS = {"github.com", "www.github.com"}


def parse_github_url(url: str) -> Optional[GitHubRepoRef]:
    """Parse an HTTPS GitHub URL into a ``GitHubRepoRef``.

    Accepted forms::

        https://github.com/{owner}/{repo}
        https://github.com/{owner}/{repo}.git
        https://github.com/{owner}/{repo}/
        https://github.com/{owner}/{repo}/tree/{ref}[/{path}...]
        https://github.com/{owner}/{repo}/blob/{ref}/{path}...

    The path suffix (``/tree/...`` or ``/blob/...``) is ignored for
    ownership resolution but callers can still supply an explicit
    branch / path via ``resolve_scm_target(..., branch=..., path=...)``.

    Returns:
        ``GitHubRepoRef`` on success, ``None`` on any parse failure.
        Never raises.
    """
    if not url or not isinstance(url, str):
        return None

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return None
    if (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
        return None

    parts = [seg for seg in parsed.path.split("/") if seg]
    if len(parts) < 2:
        return None

    owner = parts[0]
    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not _is_valid_github_name(owner) or not _is_valid_github_name(repo):
        return None

    return GitHubRepoRef(owner=owner, repo=repo, host="github.com")


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _is_valid_github_name(name: str) -> bool:
    """Loose validator for GitHub owner / repo segments.

    GitHub itself allows ``[A-Za-z0-9._-]`` in both fields and forbids a
    leading dot. We match that surface without hitting the API.
    """
    if not name:
        return False
    if name.startswith("."):
        return False
    return bool(_NAME_PATTERN.match(name))


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


_DEFAULT_PATH_PREFIX = "performance"


def default_branch_name(
    *,
    test_run_id: Optional[str] = None,
    environment: Optional[str] = None,
) -> str:
    """Build the default branch name for a script push.

    Precedence:
      1. ``test_run_id`` when supplied -> ``perf/{test_run_id}``.
      2. ``environment`` when supplied -> ``perf/{env-lower}``.
      3. Fallback -> ``perf/auto``.

    The ``perf/`` prefix is tool-agnostic and matches the ``performance/``
    repo path convention.
    """
    if test_run_id and isinstance(test_run_id, str) and test_run_id.strip():
        return f"perf/{test_run_id.strip()}"
    if environment and isinstance(environment, str) and environment.strip():
        return f"perf/{environment.strip().lower()}"
    return "perf/auto"


def default_repo_path(jmx_basename: str) -> str:
    """Build the default in-repo path for an artifact.

    Always ``performance/<basename>``. The prefix is deliberately not
    ``jmeter/`` so downstream artifacts (jmx, csv, jks, har, spec) can
    coexist under the same folder without a tool-specific rename when
    the load-generation tool changes.
    """
    basename = (jmx_basename or "script.jmx").strip().lstrip("/\\")
    return f"{_DEFAULT_PATH_PREFIX}/{basename}"


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_scm_target(
    scm: Optional[dict],
    *,
    jmx_basename: Optional[str] = None,
    test_run_id: Optional[str] = None,
    environment: Optional[str] = None,
) -> Optional[ResolvedScmTarget]:
    """Resolve an A2A / Web UI ``scm`` metadata block to a full target.

    Args:
        scm: The ``scm`` sub-dict from A2A / Web UI metadata. Recognized
            keys: ``provider``, ``url``, ``branch``, ``path``,
            ``createBranch`` (or ``create_branch``). All other keys are
            ignored so the block can carry future extensions.
        jmx_basename: File name of the artifact to be pushed (with
            extension). Used to build the default path when ``path`` is
            missing.
        test_run_id: The run id from A2A metadata; used to mint a branch
            name when ``branch`` is missing.
        environment: The environment name (e.g. ``"QA"``); used as a
            fallback for branch minting when ``test_run_id`` is missing.

    Returns:
        ``ResolvedScmTarget`` when the block is complete enough to push,
        else ``None``. Returning ``None`` means the caller should skip
        the push (no repo url) or fail (unsupported provider / malformed
        url) with a clear error - the resolver never guesses ownership.
    """
    if not isinstance(scm, dict):
        return None

    provider = (scm.get("provider") or "github").strip().lower()
    if provider != "github":
        log.warning(
            "resolve_scm_target: unsupported provider '%s' (only "
            "'github' is supported in this slice)",
            provider,
        )
        return None

    url = scm.get("url")
    if not url or not isinstance(url, str):
        return None

    repo_ref = parse_github_url(url)
    if repo_ref is None:
        log.warning(
            "resolve_scm_target: could not parse GitHub URL '%s'",
            _redact_url(url),
        )
        return None

    branch_raw = scm.get("branch")
    path_raw = scm.get("path")

    if isinstance(branch_raw, str) and branch_raw.strip():
        branch = branch_raw.strip()
        branch_defaulted = False
    else:
        branch = default_branch_name(
            test_run_id=test_run_id,
            environment=environment,
        )
        branch_defaulted = True

    if isinstance(path_raw, str) and path_raw.strip():
        path = path_raw.strip().lstrip("/\\")
    else:
        path = default_repo_path(jmx_basename or "script.jmx")

    create_branch_raw: Any = scm.get("createBranch")
    if create_branch_raw is None:
        create_branch_raw = scm.get("create_branch")

    if isinstance(create_branch_raw, bool):
        create_branch = create_branch_raw
    else:
        create_branch = branch_defaulted

    return ResolvedScmTarget(
        provider="github",
        owner=repo_ref.owner,
        repo=repo_ref.repo,
        branch=branch,
        path=path,
        create_branch=create_branch,
        url=url.strip(),
    )


def _redact_url(url: str) -> str:
    """Strip any embedded userinfo from a URL for safe logging."""
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
