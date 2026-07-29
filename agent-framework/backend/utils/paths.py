"""Shared path resolution for the agent framework.

Provides environment-aware helpers that return correct paths whether
running locally on disk, inside Docker containers (Aspire or
docker-compose), or in a future cloud deployment (Azure Container
Apps with blob-backed mounts).

The Docker bind-mount target ``/app/artifacts`` is shared by both the
agent-backend and gateway containers (configured in ``apphost.cs`` and
``docker-compose-full-*.yaml``).  Absolute paths returned by
``get_artifacts_base()`` are therefore valid across containers.

Public API
----------
    is_docker()            -> bool
    get_framework_dir()    -> Path   (agent-framework/backend/)
    get_artifacts_base()   -> Path   (/app/artifacts or local equivalent)
"""

from __future__ import annotations

import os
from pathlib import Path

_DOCKER_ARTIFACTS_PATH = Path("/app/artifacts")


def is_docker() -> bool:
    """Return True when running inside a Docker container.

    Checks the ``PERFPILOT_DOCKER`` env var set by
    ``Dockerfile.agent-backend`` (``ENV PERFPILOT_DOCKER=true``).
    """
    return os.environ.get("PERFPILOT_DOCKER", "").lower() in ("true", "1")


def get_framework_dir() -> Path:
    """Return the framework root directory (``agent-framework/backend/``).

    ``utils/`` lives directly under ``backend/``, so one ``.parent`` up
    from this file's directory reaches the framework root where
    ``config/``, ``agents/``, ``.env``, and ``sql/`` all live.
    """
    return Path(__file__).resolve().parent.parent


def get_artifacts_base() -> Path:
    """Return the artifacts base directory.

    In Docker (``PERFPILOT_DOCKER=true``), the bind-mount target is
    ``/app/artifacts`` — matching both ``apphost.cs`` and
    ``docker-compose-full-*.yaml``.  Both the agent-backend and
    gateway containers mount the same host directory here, so
    absolute paths are valid across containers.

    Locally, resolves to ``{framework_parent}/artifacts/`` where
    *framework_parent* is the directory above ``backend/`` (typically
    ``agent-framework/``).
    """
    if is_docker():
        return _DOCKER_ARTIFACTS_PATH
    return get_framework_dir().parent / "artifacts"
