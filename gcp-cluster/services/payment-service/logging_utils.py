"""Minimal structured JSON logging, shared (by copy, not by package) across
Payasam services. Established in Phase 1 for a consistent log *shape*;
Phase 3 adds trace_id/span_id correlation (when a span is active) so a
log line found in OpenObserve (or in `kubectl logs`) can be pivoted to
the distributed trace it happened inside of, and vice versa.
"""

import json
import logging
import sys
import time

from opentelemetry import trace


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "service": getattr(record, "service", "unknown"),
            "severity": record.levelname,
            "message": record.getMessage(),
        }
        for field in ("event_type", "request_id", "transaction_id", "endpoint", "status_code", "user_id"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")

        return json.dumps(payload)


def get_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, service: str, event_type: str, message: str, **fields):
    extra = {"service": service, "event_type": event_type, **fields}
    logger.log(level, message, extra=extra)
