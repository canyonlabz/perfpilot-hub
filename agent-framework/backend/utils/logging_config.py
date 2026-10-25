"""Configure stdlib logging and structlog from DEPLOYMENT_MODE.

Callers that need a structured event should use ``log_event``. That
keeps body, severity, attributes, and (later) span context on one
OTEL-shaped path so a future official SDK swap does not rewrite
per-feature log helpers.
"""
from __future__ import annotations

import contextvars
import logging
import os
import sys
from typing import Any, Mapping, Optional

import structlog

_SEVERITY_NUMBER = {
    "DEBUG": 5,
    "INFO": 9,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
}

# Filled by a span processor when OTEL traces are wired. Empty until then.
otel_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "otel_trace_id", default=""
)
otel_span_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "otel_span_id", default=""
)

_OTEL_TOP_LEVEL = frozenset(
    {
        "level",
        "event",
        "timestamp",
        "severity_text",
        "severity_number",
        "body",
        "trace_id",
        "span_id",
        "attributes",
        "resource.service.name",
        "exception",
        "stack",
        "exc_info",
    }
)


def _otel_shape(_, __, event_dict):
    level = str(event_dict.pop("level", "info")).upper()
    event_dict["severity_text"] = level
    event_dict["severity_number"] = _SEVERITY_NUMBER.get(level, 9)
    event_dict["body"] = event_dict.pop("event", "")
    event_dict.setdefault("trace_id", otel_trace_id.get() or "")
    event_dict.setdefault("span_id", otel_span_id.get() or "")
    existing = event_dict.get("attributes")
    attributes = dict(existing) if isinstance(existing, dict) else {}
    for key in list(event_dict):
        if key not in _OTEL_TOP_LEVEL:
            attributes.setdefault(key, event_dict.pop(key))
    event_dict["attributes"] = attributes
    event_dict.setdefault(
        "resource.service.name",
        os.environ.get("OTEL_SERVICE_NAME", "mcp"),
    )
    return event_dict


def log_event(
    name: str,
    level: int,
    body: str,
    attributes: Optional[Mapping[str, Any]] = None,
) -> None:
    """Emit one log through the configured stack (OTEL-shaped extras).

    Cloud JSON already maps this to severity_text/number, body, trace_id,
    span_id, and attributes. When the official OTEL SDK is added, this
    function is the swap point for Logger.emit and current span context.
    """
    extras = {
        key: value
        for key, value in dict(attributes or {}).items()
        if value not in (None, "")
    }
    logging.getLogger(name).log(
        level,
        body,
        extra={
            "attributes": extras,
            "trace_id": otel_trace_id.get() or "",
            "span_id": otel_span_id.get() or "",
        },
    )


def configure_logging() -> None:
    mode = os.environ.get("DEPLOYMENT_MODE", "local").lower()
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if mode == "cloud":
        renderer = structlog.processors.JSONRenderer()
        extra = [
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            _otel_shape,
        ]
    else:
        renderer = structlog.dev.ConsoleRenderer()
        extra = [structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S")]

    structlog.configure(
        processors=shared
        + extra
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
            foreign_pre_chain=shared + extra,
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
