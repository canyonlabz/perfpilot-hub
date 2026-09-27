"""Configure stdlib logging and structlog from DEPLOYMENT_MODE."""
import logging
import os
import sys

import structlog

_SEVERITY_NUMBER = {
    "DEBUG": 5,
    "INFO": 9,
    "WARNING": 13,
    "ERROR": 17,
    "CRITICAL": 21,
}


def _otel_shape(_, __, event_dict):
    level = str(event_dict.pop("level", "info")).upper()
    event_dict["severity_text"] = level
    event_dict["severity_number"] = _SEVERITY_NUMBER.get(level, 9)
    event_dict["body"] = event_dict.pop("event", "")
    event_dict.setdefault("trace_id", "")
    event_dict.setdefault("span_id", "")
    event_dict.setdefault("attributes", {})
    event_dict.setdefault(
        "resource.service.name",
        os.environ.get("OTEL_SERVICE_NAME", "mcp"),
    )
    return event_dict


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
