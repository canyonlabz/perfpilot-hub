"""Repo-root pytest conftest.

Handles cross-cutting concerns for the entire test suite:

1. Inserts each in-repo Python source root into ``sys.path`` so tests
   can ``import services``, ``import stores``, ``import utils``, etc.
   without requiring a ``pip install -e .`` first. This mirrors how
   the framework itself imports these packages at runtime — the
   ``agent-framework/backend/`` folder is the effective PYTHONPATH.
2. Reserves this file as the future home for repo-wide shared fixtures
   (e.g., temp dirs, environment variable scrubbing) that apply to
   more than one subproject.

Subproject-specific fixtures live in ``tests/<subproject>/conftest.py``
(e.g., ``tests/agent_framework/conftest.py`` for agent-framework
fixtures). Tests in ``tests/<subproject>/`` inherit fixtures from BOTH
this file and their subproject conftest.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root = parent of this file's parent (tests/conftest.py -> repo/).
REPO_ROOT = Path(__file__).resolve().parent.parent

# Make agent-framework/backend importable as top-level packages
# (services, stores, utils, agents, core, a2a, workflows). The
# folder name uses a hyphen ("agent-framework"), which is not a valid
# Python identifier — hence the sys.path insertion of the "backend"
# subfolder rather than importing the folder itself as a package.
AGENT_FRAMEWORK_BACKEND = REPO_ROOT / "agent-framework" / "backend"
if AGENT_FRAMEWORK_BACKEND.exists() and str(AGENT_FRAMEWORK_BACKEND) not in sys.path:
    sys.path.insert(0, str(AGENT_FRAMEWORK_BACKEND))
