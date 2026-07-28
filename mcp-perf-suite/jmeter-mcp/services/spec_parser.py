# Specification Parser Service
# Parse Markdown -> structured steps
# This module is part of the Browser Automation Framework (BAF).
#
import os
import re
from typing import List, Dict, Optional, Any
from datetime import datetime

from utils.config import load_config

# === Global configuration ===
CONFIG = load_config()
BROWSER_CONFIG = CONFIG.get("browser", {})
ARTIFACTS_PATH = CONFIG["artifacts"]["artifacts_path"]
THINK_TIME = BROWSER_CONFIG.get("think_time", 5000)  # milliseconds

# ===================================================================================
# Shared helper: resolve test-specs root directories from config
# ===================================================================================

def _get_test_specs_roots(test_run_id: Optional[str] = None) -> List[str]:
    """Return ordered list of test-specs root directories from config.

    Search priority (first match wins):
      1. ``artifacts/{test_run_id}/test-specs/`` — run-scoped specs
         (e.g. A2A-provided test cases normalized and saved per run)
      2. ``test-specs/azure-devops/`` — ADO-originated test cases
      3. ``test-specs/web-flows/`` — browser automation specs
      4. ``test-specs/api-flows/`` — API test specs
      5. ``test-specs/examples/`` — example/demo specs
    """
    roots = []
    test_specs_cfg = CONFIG.get("test_specs", {})

    # Run-scoped specs take highest priority
    if test_run_id:
        run_root = os.path.join(ARTIFACTS_PATH, str(test_run_id), "test-specs")
        if os.path.isdir(run_root):
            roots.append(run_root)

    for key in ("azure_devops_path", "web_flows_path", "api_flows_path", "examples_path"):
        path = test_specs_cfg.get(key)
        if path:
            roots.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", path)))

    return roots


# ===================================================================================
# MCP Tool (get_test_specs): Discover available test spec files
# ===================================================================================

def list_test_specs(test_run_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Discover human-readable spec files (Markdown) under the configured
    test-specs roots. If test_run_id is provided, also look under a
    run-specific test-specs directory inside artifacts/<test_run_id>/.
    """
    roots = _get_test_specs_roots(test_run_id)

    seen = {}
    specs = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                if not fname.lower().endswith(".md"):
                    continue
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root)
                key = os.path.normpath(os.path.join(os.path.basename(root), rel_path))
                if key in seen:
                    continue
                seen[key] = True
                stat = os.stat(full_path)
                specs.append(
                    {
                        "relative_path": key,
                        "absolute_path": os.path.abspath(full_path),
                        "size_bytes": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )

    status = "OK" if specs else "EMPTY"
    return {
        "status": status,
        "count": len(specs),
        "specs": sorted(specs, key=lambda s: s["relative_path"].lower()),
        "roots_scanned": roots,
    }

# ===================================================================================
# MCP Tool (get_browser_steps): Load and parse a browser automation spec into steps
# ===================================================================================

def load_browser_steps(test_run_id: str, spec_path: str, ctx: Optional[Any] = None) -> List[str]:
    """
    Load the Markdown spec / Task file and return a list of step text blocks.
    Accepts absolute paths or relative paths (resolved against test-specs roots).
    """
    resolved = _resolve_spec_path(spec_path, test_run_id)
    task_text = generate_task(resolved)
    steps = parse_task_to_steps(task_text)
    return steps


def _resolve_spec_path(spec_path: str, test_run_id: Optional[str] = None) -> str:
    """
    Resolve a spec file path. Handles:
    1. Absolute path that exists → return as-is
    2. Relative path from CWD → return as-is
    3. Relative path → try against each test-specs root
    4. Relative path → try against parent of each root (handles relative_path from list_test_specs)
    5. Bare filename → search across all roots
    Returns the resolved absolute path, or raises FileNotFoundError.
    """
    if os.path.isabs(spec_path) and os.path.isfile(spec_path):
        return spec_path

    if os.path.isfile(spec_path):
        return os.path.abspath(spec_path)

    roots = _get_test_specs_roots(test_run_id)

    for root in roots:
        candidate = os.path.join(root, spec_path)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    # Also try parent of each root (handles "web-flows/file.md" when root IS web-flows)
    seen_parents = set()
    for root in roots:
        parent = os.path.dirname(root)
        if parent not in seen_parents:
            seen_parents.add(parent)
            candidate = os.path.join(parent, spec_path)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

    # Fallback: search by basename across all roots
    basename = os.path.basename(spec_path)
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            if basename in filenames:
                return os.path.abspath(os.path.join(dirpath, basename))

    raise FileNotFoundError(
        f"Task file not found: {spec_path} "
        f"(searched roots: {roots})"
    )

def generate_task(task_filepath: str) -> str:
    """
    Read the task definition from the given full file path and return
    the full browser automation task as a static string.

    This is used both by Cursor (via @file) and JMeter MCP tooling
    (e.g., to extract steps, derive capture_domain, etc.).
    """
    if not os.path.isfile(task_filepath):
        raise FileNotFoundError(f"Task file not found: {task_filepath}")

    with open(task_filepath, "r", encoding="utf-8") as f:
        return f.read()

def parse_task_to_steps(task_text: str) -> List[str]:
    """
    Parse the raw task/spec text into a list of *step blocks*.

    Supported step labels (case-insensitive, anchored at line start):
      - "Step", "STEP"
      - "TC", "Test Case"
      - "TS", "Test Step"

    Example lines:
      "Step 1: Open https://demoblaze.com/."
      "STEP 2: Click on 'Laptops' under the 'Categories' menu."
      "TC01: User login happy path"
      "TS02: Submit valid credentials"

    Terminal keywords (case-insensitive):
      - "END TASK"
      - "TERMINATE"
      - "END FLOW"
      - "END"

    Returns:
      A list of strings, each representing one step block
      (starting with the step label line and including any
       following lines until the next step or terminal keyword).
    """
    # Normalize newlines
    lines = task_text.strip().splitlines()

    # Step start pattern (case-insensitive)
    step_start_pattern = re.compile(
        r"^\s*(step|tc|ts|test case|test step)\b", re.IGNORECASE
    )

    terminate_keywords = {"end task", "terminate", "end flow", "end"}

    steps: List[str] = []
    current_step_lines: List[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            # Keep blank lines inside a step, but ignore them before the first step
            if current_step_lines:
                current_step_lines.append(line)
            continue

        # Check terminal keywords
        if stripped.lower() in terminate_keywords:
            if current_step_lines:
                steps.append("\n".join(current_step_lines).strip())
                current_step_lines = []
            # Stop parsing entirely
            break

        # Check if this line starts a new step (Step, TC, TS, Test Case, Test Step)
        if step_start_pattern.match(stripped):
            # If we were accumulating a previous step, finalize it
            if current_step_lines:
                steps.append("\n".join(current_step_lines).strip())
                current_step_lines = []
            # Start new step
            current_step_lines.append(line)
        else:
            # Continuation of the current step (if any)
            if current_step_lines:
                current_step_lines.append(line)
            else:
                # Pre-step text (e.g., intro). We ignore it for JMeter steps.
                # If you want to treat an intro as Step 0 later, we can add that.
                continue

    # Flush final step (if not terminated by a keyword)
    if current_step_lines:
        steps.append("\n".join(current_step_lines).strip())

    return steps

# === Build a structured prompt for a single browser automation step ===
def build_structured_step_prompt(step_number, step_text):
    return f"""
You are a web automation agent.

Your goal is to interact with a browser to complete the following step details and ensure you follow the instructions accurately. 
Do NOT make any assumptions or take any actions outside of the provided step details.

Step {step_number} Details:
```
{step_text}
```

Instructions:
- You will receive a step instruction to follow.
- Follow the instruction as closely as possible.
- Interpret the step as literally and accurately as possible.
- Do not perform extra actions outside of this step.
- Wait for the page to fully load before taking any action.
- If the step cannot be completed (e.g., element not found), fail gracefully.
- Once the step is complete, finalize with a short confirmation.
- If the step is not applicable, return a message indicating that the step was skipped.
- If the step is completed successfully, return a message indicating success.
"""
