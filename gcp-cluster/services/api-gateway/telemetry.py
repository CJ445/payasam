"""OpenTelemetry bootstrap for Payasam services (Phase 3).

Direct OTLP/HTTP export straight to OpenObserve -- no standalone
Collector (DECISIONS.md D003). Every setting comes from environment
variables (populated by a Kubernetes ConfigMap for non-secret values and
a Secret for credentials -- see infrastructure/kubernetes/telemetry/) --
nothing here is hardcoded, per D011's requirement that the OpenObserve
endpoint (which depends on a dynamically-resolved host gateway address)
must never be baked into source.

Graceful degradation is the point of this module: if
OTEL_EXPORTER_OTLP_ENDPOINT is unset, or OpenObserve is unreachable at
export time, the application must keep working normally. The OTel SDK's
BatchSpanProcessor/BatchLogRecordProcessor already export asynchronously
in a background thread and swallow/log export failures rather than
raising into request handling -- so the only thing this module needs to
guard directly is exporter *construction* (a malformed endpoint, for
example), not steady-state unavailability.
"""

import base64
import logging
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_bootstrap_logger = logging.getLogger("payasam.telemetry")


def _otlp_headers() -> dict:
    """HTTP Basic Auth against OpenObserve, built from env vars sourced
    from a Kubernetes Secret -- never hardcoded, never logged."""
    username = os.environ.get("OPENOBSERVE_USERNAME")
    password = os.environ.get("OPENOBSERVE_PASSWORD")
    if not username or not password:
        return {}
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def setup_telemetry(service_name: str) -> dict:
    """Call once at service startup. Returns {'tracer', 'meter',
    'logger_provider'} -- 'meter'/'logger_provider' are None if OTLP
    export isn't configured (OTEL_EXPORTER_OTLP_ENDPOINT unset), in which
    case tracing still works locally (in-process spans, e.g. for tests)
    but nothing is exported anywhere."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    environment = os.environ.get("DEPLOYMENT_ENVIRONMENT", "local")
    service_version = os.environ.get("SERVICE_VERSION", "phase3")

    resource = Resource.create({
        "service.name": service_name,
        "service.version": service_version,
        "deployment.environment": environment,
    })

    tracer_provider = TracerProvider(resource=resource)
    meter_provider = None
    logger_provider = None

    if endpoint:
        headers = _otlp_headers()
        try:
            span_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers)
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        except Exception:
            _bootstrap_logger.warning("failed to configure OTLP span exporter for %s", service_name, exc_info=True)

        try:
            logger_provider = LoggerProvider(resource=resource)
            log_exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers)
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        except Exception:
            _bootstrap_logger.warning("failed to configure OTLP log exporter for %s", service_name, exc_info=True)
            logger_provider = None

        try:
            metric_exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers)
            reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        except Exception:
            _bootstrap_logger.warning("failed to configure OTLP metric exporter for %s", service_name, exc_info=True)
            meter_provider = None

    trace.set_tracer_provider(tracer_provider)
    if meter_provider:
        metrics.set_meter_provider(meter_provider)

    return {
        "tracer": trace.get_tracer(service_name),
        "meter": metrics.get_meter(service_name) if meter_provider else None,
        "logger_provider": logger_provider,
    }


def attach_otlp_logging_handler(app_logger: logging.Logger, logger_provider) -> None:
    """Ship every log the application already emits (via logging_utils.py's
    JSON logger) through OTLP too, in addition to stdout -- additive, not
    a replacement. The OTel SDK automatically attaches the active span's
    trace_id/span_id to each exported log record."""
    if logger_provider is None:
        return
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    app_logger.addHandler(handler)


class RequestMetrics:
    """Minimal, deliberately small set of service-level metrics: request
    count and request duration, both with only low-cardinality
    dimensions (http.route, a coarse status class). Tracing is the
    priority for this phase (per the phase's own instructions) -- this
    exists to answer simple aggregate questions without competing with
    that priority or creating metric-cardinality problems.
    """

    def __init__(self, meter):
        self._enabled = meter is not None
        if self._enabled:
            self._request_counter = meter.create_counter(
                "http.server.request.count", unit="1", description="Total HTTP requests handled"
            )
            self._duration_histogram = meter.create_histogram(
                "http.server.request.duration", unit="ms", description="HTTP request duration"
            )

    def record(self, route: str, status_code: int, duration_ms: float) -> None:
        if not self._enabled:
            return
        status_class = f"{status_code // 100}xx"
        attributes = {"http.route": route, "http.status_class": status_class}
        self._request_counter.add(1, attributes)
        self._duration_histogram.record(duration_ms, attributes)
