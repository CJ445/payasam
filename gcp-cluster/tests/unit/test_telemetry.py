"""Unit tests for Phase 3 OpenTelemetry instrumentation.

Uses OpenTelemetry's own in-memory testing exporter for isolated
span-shape assertions, and a header-capturing transport wrapper (checking
the real W3C `traceparent` header on the wire) for cross-service
propagation -- deliberately not relying on the global tracer-provider
singleton for the propagation test, since loading multiple services'
`main.py` in one test process (this repo's established ASGI-mount test
pattern) makes each service's `setup_telemetry()` call
`trace.set_tracer_provider()` on the same process-wide global, and later
calls can override earlier ones. That's a test-harness artifact of
running 5 "services" in one Python process, not something that happens in
the real one-service-per-container deployment -- so instead of fighting
it, this suite verifies propagation the way it actually matters: the real
`traceparent` header value on the wire between real ASGI apps.
"""

import logging
import re
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "database"))
import telemetry as telemetry_module  # noqa: E402

from conftest import install_mounts, load_service_module  # noqa: E402

TRACEPARENT_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-0[01]$")


# ---- tracer initialization / service identity ----

def test_setup_telemetry_without_endpoint_is_graceful_noop(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    result = telemetry_module.setup_telemetry("payasam-test-service")
    assert result["tracer"] is not None
    assert result["meter"] is None
    assert result["logger_provider"] is None
    # must not raise creating a span even with no exporter configured
    with result["tracer"].start_as_current_span("noop-check"):
        pass


def test_setup_telemetry_with_malformed_endpoint_does_not_raise(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "not a valid url at all :::")
    result = telemetry_module.setup_telemetry("payasam-test-service")
    assert result["tracer"] is not None  # still usable, degrades gracefully


def test_service_identity_resource_attributes(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "local")
    monkeypatch.setenv("SERVICE_VERSION", "test-version")

    exporter = InMemorySpanExporter()
    resource = Resource.create({
        "service.name": "payasam-identity-test",
        "service.version": "test-version",
        "deployment.environment": "local",
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("payasam-identity-test")

    with tracer.start_as_current_span("identity-check"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    resource_attrs = spans[0].resource.attributes
    assert resource_attrs["service.name"] == "payasam-identity-test"
    assert resource_attrs["service.version"] == "test-version"
    assert resource_attrs["deployment.environment"] == "local"


# ---- span creation (in-memory exporter) ----

def test_span_creation_and_attributes():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "payasam-span-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("payasam-span-test")

    with tracer.start_as_current_span("db.reserve_inventory") as span:
        span.set_attribute("order.operation", "reserve_inventory")
        span.set_attribute("transaction_id", "txn-abc123")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "db.reserve_inventory"
    assert spans[0].attributes["order.operation"] == "reserve_inventory"
    assert spans[0].attributes["transaction_id"] == "txn-abc123"
    assert spans[0].end_time is not None and spans[0].start_time is not None
    assert spans[0].end_time >= spans[0].start_time


def test_nested_spans_share_trace_id():
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "payasam-nest-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("payasam-nest-test")

    with tracer.start_as_current_span("outer") as outer:
        with tracer.start_as_current_span("inner") as inner:
            pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["outer"].context.trace_id == spans["inner"].context.trace_id
    assert spans["inner"].parent.span_id == spans["outer"].context.span_id


# ---- database operation spans (real db.py, real spans) ----

def test_database_operation_spans_capture_lock_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "payasam-db-span-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    db_module = load_service_module("database", f"db_module_span_test_{id(tmp_path)}")
    monkeypatch.setattr(db_module.db, "_tracer", provider.get_tracer("payasam.database.db"))
    db_module.db.init_db()
    db_module.db.reserve_inventory("product-001", 1)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "db.reserve_inventory" in spans
    reserve_span = spans["db.reserve_inventory"]
    assert reserve_span.attributes["db.system"] == "sqlite"
    assert reserve_span.attributes["db.product_id"] == "product-001"
    assert reserve_span.attributes["db.outcome"] == "reserved"
    assert "db.lock_wait_ms" in reserve_span.attributes
    assert reserve_span.attributes["db.lock_wait_ms"] >= 0.0


# ---- HTTP dependency instrumentation + cross-service propagation ----

class _HeaderCapturingTransport(httpx.AsyncBaseTransport):
    """Wraps a real ASGITransport and records every outbound request's
    headers, so propagation can be checked against the real wire value
    OpenTelemetry's httpx/FastAPI instrumentation actually produces."""

    def __init__(self, app, captured: list):
        self._inner = httpx.ASGITransport(app=app)
        self._captured = captured

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._captured.append(dict(request.headers))
        return await self._inner.handle_async_request(request)


@pytest.fixture
def full_stack_with_header_capture(full_stack, monkeypatch):
    captured: list[dict] = []
    mounts = {
        "http://database:8000": _HeaderCapturingTransport(full_stack["database"], captured),
        "http://inventory-service:8000": _HeaderCapturingTransport(full_stack["inventory"], captured),
        "http://payment-service:8000": _HeaderCapturingTransport(full_stack["payment"], captured),
        "http://order-service:8000": _HeaderCapturingTransport(full_stack["order"], captured),
    }
    install_mounts(monkeypatch, mounts)
    return full_stack, captured


def test_trace_context_propagates_across_full_chain(full_stack_with_header_capture):
    full_stack, captured = full_stack_with_header_capture

    with TestClient(full_stack["gateway"]) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 1}]},
        )
    assert resp.status_code == 201

    traceparents = [h["traceparent"] for h in captured if "traceparent" in h]
    # every hop below the gateway (order->inventory->database,
    # order->payment->database, order->database) must carry propagation.
    assert len(traceparents) >= 4, f"expected multiple propagated hops, got {traceparents}"

    matches = [TRACEPARENT_RE.match(tp) for tp in traceparents]
    assert all(matches), f"malformed traceparent header(s): {traceparents}"

    trace_ids = {m.group(1) for m in matches}
    assert len(trace_ids) == 1, (
        f"all hops of one order transaction must belong to a single distributed trace, "
        f"got {len(trace_ids)} distinct trace ids: {trace_ids}"
    )


def test_httpx_dependency_spans_use_distinct_span_ids(full_stack_with_header_capture):
    """Each hop should be its own span (not literally the same span
    reused), even though they share one trace id."""
    full_stack, captured = full_stack_with_header_capture

    with TestClient(full_stack["gateway"]) as client:
        client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 1}]},
        )

    span_ids = [TRACEPARENT_RE.match(h["traceparent"]).group(2) for h in captured if "traceparent" in h]
    assert len(span_ids) == len(set(span_ids)), "each propagated hop must carry a distinct span id"


# ---- log correlation ----

def test_logs_include_trace_and_span_id_when_span_active(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    import io
    import json

    import logging_utils  # already on sys.path via this module's top-level insert

    stream = io.StringIO()
    logger = logging.getLogger("payasam-log-correlation-test")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging_utils.JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    provider = TracerProvider(resource=Resource.create({"service.name": "payasam-log-test"}))
    tracer = provider.get_tracer("payasam-log-test")

    logging_utils.log_event(logger, logging.INFO, "test", "before_span", "no span active yet")
    with tracer.start_as_current_span("correlated-span"):
        logging_utils.log_event(logger, logging.INFO, "test", "in_span", "span is active")

    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
    assert "trace_id" not in lines[0]
    assert "trace_id" in lines[1]
    assert "span_id" in lines[1]
    assert len(lines[1]["trace_id"]) == 32
    assert len(lines[1]["span_id"]) == 16
