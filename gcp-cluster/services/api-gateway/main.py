"""Payasam `api-gateway` (Phase 1).

The only externally reachable entry point for the demo application.
Assigns a request_id if the caller didn't supply one, forwards to
order-service, and returns its response transparently. Deliberately not a
full API management platform.
"""

import logging
import os
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from logging_utils import get_logger, log_event
from telemetry import RequestMetrics, attach_otlp_logging_handler, setup_telemetry

SERVICE_NAME = "payasam-api-gateway"
logger = get_logger(SERVICE_NAME)
telemetry = setup_telemetry(SERVICE_NAME)
attach_otlp_logging_handler(logger, telemetry["logger_provider"])
metrics = RequestMetrics(telemetry["meter"])
HTTPXClientInstrumentor().instrument()

ORDER_SERVICE_URL = os.environ.get("ORDER_SERVICE_URL", "http://order-service:8000")

app = FastAPI(title="Payasam API Gateway")
FastAPIInstrumentor.instrument_app(app)


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000.0
    route = request.scope.get("route")
    route_path = route.path if route else request.url.path
    metrics.record(route_path, response.status_code, duration_ms)
    return response


@app.get("/health")
def health():
    return {"status": "healthy", "service": SERVICE_NAME}


@app.get("/ready")
async def ready():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ORDER_SERVICE_URL}/health")
        if resp.status_code == 200:
            return {"status": "ready", "service": SERVICE_NAME}
    except httpx.HTTPError:
        pass
    return JSONResponse(status_code=503, content={"status": "not_ready", "service": SERVICE_NAME, "reason": "order_service_unreachable"})


@app.post("/orders")
async def create_order(request: Request):
    request_id = request.headers.get("X-Request-Id") or f"req-{uuid.uuid4()}"
    body = await request.body()

    log_event(logger, logging.INFO, SERVICE_NAME, "request_received", "order request received",
              request_id=request_id, endpoint="/orders")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{ORDER_SERVICE_URL}/orders",
                content=body,
                headers={"Content-Type": "application/json", "X-Request-Id": request_id},
            )
    except httpx.HTTPError as exc:
        log_event(logger, logging.ERROR, SERVICE_NAME, "order_service_unavailable", "order service unreachable",
                   request_id=request_id)
        return JSONResponse(
            status_code=502,
            content={"error": "order_service_unavailable", "message": str(exc), "request_id": request_id},
        )

    log_event(logger, logging.INFO, SERVICE_NAME, "request_completed", "order request completed",
              request_id=request_id, endpoint="/orders", status_code=resp.status_code)

    return JSONResponse(status_code=resp.status_code, content=resp.json())
