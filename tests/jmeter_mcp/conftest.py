"""Put the JMeter MCP root on ``sys.path`` for this subtree only.

``agent-framework/backend`` is already first from the repo conftest.
This file inserts the JMeter MCP root ahead of it while these tests
run, then restores ``sys.path`` and the ``services`` / ``utils``
modules so a full ``pytest tests/`` session cannot leak JMeter
imports into agent-framework tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_JMETER_MCP = _REPO_ROOT / "mcp-perf-suite" / "jmeter-mcp"


@pytest.fixture
def playwright_adapter(monkeypatch: pytest.MonkeyPatch):
    """Import JMeter ``services.playwright_adapter`` in isolation."""
    saved_path = sys.path[:]
    saved_mods = {
        key: module
        for key, module in sys.modules.items()
        if key == "services"
        or key.startswith("services.")
        or key == "utils"
        or key.startswith("utils.")
    }
    sys.path.insert(0, str(_JMETER_MCP))
    for key in list(sys.modules):
        if (
            key == "services"
            or key.startswith("services.")
            or key == "utils"
            or key.startswith("utils.")
        ):
            del sys.modules[key]

    import importlib

    adapter = importlib.import_module("services.playwright_adapter")
    try:
        yield adapter
    finally:
        for key in list(sys.modules):
            if (
                key == "services"
                or key.startswith("services.")
                or key == "utils"
                or key.startswith("utils.")
            ):
                del sys.modules[key]
        sys.path[:] = saved_path
        sys.modules.update(saved_mods)
