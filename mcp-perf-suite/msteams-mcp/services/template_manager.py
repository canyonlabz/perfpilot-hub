"""
Template manager for MS Teams notification templates.

Handles loading, rendering, listing, and notification logging.
Supports default/custom template fallback and {{VARIABLE}} interpolation.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.config import load_config

logger = logging.getLogger("msteams-mcp.template-manager")

_config = load_config()
_teams_cfg = _config.get("teams", {})
_artifacts_cfg = _config.get("artifacts", {})

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
_DEFAULT_PREFIX = "default-"

_MCP_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = Path(_teams_cfg.get("templates_path", str(_MCP_ROOT / "templates")))
_ARTIFACTS_ROOT = Path(_artifacts_cfg.get("artifacts_path", str(_MCP_ROOT.parent / "artifacts")))


def _resolve_templates_dir() -> Path:
    """Return absolute path to the templates directory."""
    if _TEMPLATES_DIR.is_absolute():
        return _TEMPLATES_DIR
    return _MCP_ROOT / _TEMPLATES_DIR


def _resolve_artifacts_dir() -> Path:
    """Return absolute path to the artifacts root."""
    if _ARTIFACTS_ROOT.is_absolute():
        return _ARTIFACTS_ROOT
    return _MCP_ROOT / _ARTIFACTS_ROOT


# ---------------------------------------------------------------------------
# Template loading with default/custom fallback
# ---------------------------------------------------------------------------

def load_template(
    template_name: str,
    *,
    channel_template: str | None = None,
) -> tuple[str, str]:
    """
    Load a notification template with layered fallback.

    Resolution order:
        1. template_name (caller-specified — highest priority)
        2. channel_template (per-channel default from config)
        3. default-{template_name} (built-in fallback)

    Returns:
        Tuple of (template_content, resolved_template_name).

    Raises:
        FileNotFoundError: If no template found in the chain.
    """
    templates_dir = _resolve_templates_dir()
    candidates: list[str] = []

    candidates.append(template_name)

    if channel_template and channel_template != template_name:
        candidates.append(channel_template)

    if not template_name.startswith(_DEFAULT_PREFIX):
        candidates.append(f"{_DEFAULT_PREFIX}{template_name}")

    for candidate in candidates:
        path = templates_dir / candidate
        if path.exists():
            content = path.read_text(encoding="utf-8")
            logger.debug("Loaded template '%s' from %s", candidate, path)
            return content, candidate

    searched = ", ".join(candidates)
    raise FileNotFoundError(
        f"No template found. Searched: {searched} in {templates_dir}"
    )


def extract_placeholders(template_content: str) -> list[str]:
    """Return sorted unique placeholder names from template content."""
    return sorted(set(_PLACEHOLDER_RE.findall(template_content)))


def render_template(
    template_content: str,
    variables: dict[str, str],
) -> str:
    """
    Replace {{VARIABLE}} placeholders with provided values.

    Unmatched placeholders are replaced with an empty string so they do not
    appear verbatim in delivered messages.
    """
    def _replacer(match: re.Match) -> str:
        key = match.group(1)
        return variables.get(key, "")

    return _PLACEHOLDER_RE.sub(_replacer, template_content)


# ---------------------------------------------------------------------------
# Template listing / details
# ---------------------------------------------------------------------------

def list_templates() -> dict[str, Any]:
    """List all available notification templates."""
    templates_dir = _resolve_templates_dir()

    if not templates_dir.exists():
        return {"templates": [], "count": 0, "path": str(templates_dir)}

    templates: list[dict[str, Any]] = []
    for f in sorted(templates_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        placeholders = extract_placeholders(content)
        templates.append({
            "name": f.name,
            "isDefault": f.name.startswith(_DEFAULT_PREFIX),
            "placeholders": placeholders,
            "size": f.stat().st_size,
        })

    return {
        "templates": templates,
        "count": len(templates),
        "path": str(templates_dir),
    }


def get_template_details(template_name: str) -> dict[str, Any]:
    """Get details and preview of a specific template."""
    templates_dir = _resolve_templates_dir()
    path = templates_dir / template_name

    if not path.exists():
        return {"error": f"Template not found: {template_name}"}

    content = path.read_text(encoding="utf-8")
    placeholders = extract_placeholders(content)

    return {
        "name": template_name,
        "path": str(path),
        "size": path.stat().st_size,
        "placeholders": placeholders,
        "preview": content[:500] + "..." if len(content) > 500 else content,
    }


# ---------------------------------------------------------------------------
# Test run context: auto-populate variables from artifacts
# ---------------------------------------------------------------------------

def load_context_variables(test_run_id: str) -> dict[str, str]:
    """
    Auto-populate template variables from artifacts/<test_run_id>/ files.

    Scans for:
      - blazemeter/public_report.json → BLAZEMETER_REPORT_LINK
      - confluence/report_metadata.json → CONFLUENCE_REPORT_LINK
      - notifications/notification_log.json → last start notification context
    """
    artifacts_dir = _resolve_artifacts_dir() / test_run_id
    variables: dict[str, str] = {"TEST_RUN_ID": test_run_id}

    bm_report = artifacts_dir / "blazemeter" / "public_report.json"
    if bm_report.exists():
        try:
            data = json.loads(bm_report.read_text(encoding="utf-8"))
            url = data.get("reportUrl") or data.get("url") or data.get("public_url", "")
            if url:
                variables["BLAZEMETER_REPORT_LINK"] = url
                variables.setdefault("REPORT_LINK", url)
        except (json.JSONDecodeError, KeyError):
            logger.warning("Could not parse BlazeMeter public report: %s", bm_report)

    confluence_meta = artifacts_dir / "confluence" / "report_metadata.json"
    if confluence_meta.exists():
        try:
            data = json.loads(confluence_meta.read_text(encoding="utf-8"))
            url = data.get("url") or data.get("page_url", "")
            if url:
                variables["CONFLUENCE_REPORT_LINK"] = url
                variables.setdefault("REPORT_LINK", url)
        except (json.JSONDecodeError, KeyError):
            logger.warning("Could not parse Confluence metadata: %s", confluence_meta)

    log_path = artifacts_dir / "notifications" / "notification_log.json"
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(entries, list):
                start_entries = [
                    e for e in entries
                    if isinstance(e, dict) and "start" in e.get("template", "").lower()
                ]
                if start_entries:
                    last_start = start_entries[-1]
                    start_vars = last_start.get("variables", {})
                    for key in ("TEST_NAME", "ENVIRONMENT", "DURATION", "START_TIME"):
                        if key in start_vars:
                            variables.setdefault(key, start_vars[key])
        except (json.JSONDecodeError, KeyError):
            logger.warning("Could not parse notification log: %s", log_path)

    return variables


# ---------------------------------------------------------------------------
# Notification logging
# ---------------------------------------------------------------------------

def log_notification(
    test_run_id: str,
    *,
    template: str,
    target: str,
    variables: dict[str, str],
    rendered_content: str | None = None,
    message_id: str | None = None,
) -> Path:
    """
    Append a notification entry to artifacts/<test_run_id>/notifications/notification_log.json.

    Returns the path to the log file.
    """
    artifacts_dir = _resolve_artifacts_dir() / test_run_id / "notifications"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    log_path = artifacts_dir / "notification_log.json"

    entries: list[dict[str, Any]] = []
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = []
        except json.JSONDecodeError:
            entries = []

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "template": template,
        "target": target,
        "variables": variables,
    }
    if rendered_content:
        entry["rendered_preview"] = rendered_content[:300]
    if message_id:
        entry["messageId"] = message_id

    entries.append(entry)
    log_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    logger.info("Logged notification to %s (entry #%d)", log_path, len(entries))
    return log_path


def read_notification_log(test_run_id: str) -> list[dict[str, Any]]:
    """Read the notification log for a test run. Returns empty list if none."""
    log_path = _resolve_artifacts_dir() / test_run_id / "notifications" / "notification_log.json"

    if not log_path.exists():
        return []

    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
