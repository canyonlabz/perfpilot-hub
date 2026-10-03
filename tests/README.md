# ✈️ PerfPilot Tests 🧪

Committed unit-test suite for the PerfPilot Agents framework and, over
time, other subprojects in this repository.

This folder is the single canonical location for committed regression
tests. Ad-hoc, experimental, or one-off smoke scripts still live under
`scripts/` (gitignored) per project convention — this `tests/` folder
is for tests that run in CI (once wired up) and that every contributor
is expected to keep passing.

---

## ⚡ Quick start

### Option A — dedicated venv (recommended for regular contributors)

From the repo root:

```powershell
cd tests
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pytest
```

### Option B — one-shot with `uv` (fast, no venv commit)

```powershell
uv run --with-requirements tests/requirements.txt pytest tests/
```

### Running a specific subset

```powershell
# All tests
pytest tests/

# One package
pytest tests/agent_framework/

# One module
pytest tests/agent_framework/services/test_hitl_gates.py

# One test
pytest tests/agent_framework/services/test_hitl_gates.py::test_publish_tools_returns_yaml_list

# With verbose output + short traceback on failure
pytest tests/ -v --tb=short
```

---

## 📁 Folder conventions

Test files mirror the source tree they cover:

| Test path | Covers source |
|---|---|
| `tests/agent_framework/services/test_hitl_gates.py` | `agent-framework/backend/services/hitl_gates.py` |
| `tests/agent_framework/services/test_task_executor_playwright_default.py` | `agent-framework/backend/services/task_executor.py` (specific regression scope) |
| `tests/agent_framework/utils/test_config_loader.py` | `agent-framework/backend/utils/config_loader.py` |
| `tests/agent_framework/agents/orchestrator/test_agent.py` | `agent-framework/backend/agents/orchestrator/agent.py` |

Naming rules:

- **Folders** use underscores, not dashes: `agent_framework/` (Python
  module naming), not `agent-framework/` (the source-tree layout uses
  dashes because it is a project name, not a package name). The root
  `conftest.py` sets up `sys.path` so this mismatch is invisible to
  tests.
- **Files** use `test_<subject>.py` where `<subject>` matches the
  module being tested. When a test file covers a specific concern (not
  the whole module), add a suffix: `test_hitl_gates.py` for the whole
  module; `test_task_executor_playwright_default.py` for a scoped
  regression concern.
- **Test functions** use `test_<what_it_verifies>`. Long descriptive
  names are preferred over short cryptic ones — the test name IS the
  documentation.

---

## 🏗️ Current state (scaffolding)

The pytest infrastructure (this README, `requirements.txt`, `pytest.ini`,
`conftest.py`, and `agent_framework/conftest.py`) ships without test
cases inside the leaf folders. That's intentional — the scaffolding
lands first so the framework fix that requires it can ship, and the
actual test cases are being written in a follow-up branch.

When the follow-up lands, the first two test modules will be:

- `tests/agent_framework/services/test_hitl_gates.py`
- `tests/agent_framework/services/test_task_executor_playwright_default.py`

The fixtures in `tests/agent_framework/conftest.py` (`hitl_store_mock`,
`tmp_hitl_yaml`) are already implemented and ready for those modules to
use.

Running `pytest tests/` today prints "no tests ran" without errors —
that confirms the scaffolding itself is healthy.

---

## 📐 Design principles

1. **No external services.** These are unit tests. They must NOT require
   a running Postgres, Redis, MCP server, or LLM endpoint. Every external
   dependency must be mocked via `monkeypatch` or `pytest-mock`.
2. **Fast.** The full suite should run in under 10 seconds on a
   developer laptop. If a test needs more than 100 ms, it probably needs
   a mock added.
3. **Deterministic.** No `time.sleep(1)`, no reliance on wall-clock
   timing beyond the fixtures provided in `conftest.py`. Async waits
   use short (< 100 ms) timeouts.
4. **Self-contained.** A test file should be readable top-to-bottom
   without cross-referencing another module. Fixtures in `conftest.py`
   are the exception because they're framework infrastructure.

---

## 🔵 Adding a new test

1. Find (or create) the right folder mirroring the source tree.
2. Add or open `test_<subject>.py`.
3. Import from source using the package name from `sys.path`
   (e.g., `from services import hitl_gates` — the root `conftest.py`
   makes this work).
4. Use fixtures from `tests/<subproject>/conftest.py` for anything that
   would otherwise need a real DB / config file / MCP call.
5. Write descriptive `test_<what_it_verifies>` names.
6. Run `pytest <your-new-file> -v` to confirm.
7. Run the full suite `pytest tests/` to confirm you haven't broken
   anything else.

---

## 🔗 Test dependencies

Pinned in `tests/requirements.txt`. Kept intentionally minimal:

- `pytest` — the test runner
- `pytest-asyncio` — for `async def` test functions (asyncio_mode is
  set to `auto` in `pytest.ini`, so no `@pytest.mark.asyncio` decorator
  is needed)
- `pytest-mock` — provides the `mocker` fixture as a thin wrapper over
  `unittest.mock` (used sparingly; `monkeypatch` is preferred for path
  and attribute patching)

Additional dependencies (coverage reporting, benchmark plugins, etc.)
can be added as needed for specific test types. Keep the base
`requirements.txt` minimal so `pip install` stays fast.

## 💾 Legacy tests elsewhere in the repo

Some pre-existing test files live outside this folder (e.g., under
`agent-framework/backend/core/`). Consolidating them into this canonical
layout is planned but not scheduled — for now, this folder holds only
tests written after the folder was created. If you find a scattered
test file that should live here, feel free to open a discussion or move
it as part of the work that touches it.
