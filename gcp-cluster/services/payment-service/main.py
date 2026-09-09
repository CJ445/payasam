"""Payasam `payment-service` (Phase 1).

Simulates payment processing (no external payment provider is called) but
performs real work: it validates the request, creates a payment
transaction, and persists it via the `database` service.
"""

import asyncio
import logging
import os
import time
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel

import faults
from logging_utils import get_logger, log_event
from telemetry import RequestMetrics, attach_otlp_logging_handler, setup_telemetry

SERVICE_NAME = "payasam-payment-service"
logger = get_logger(SERVICE_NAME)
telemetry = setup_telemetry(SERVICE_NAME)
attach_otlp_logging_handler(logger, telemetry["logger_provider"])
metrics = RequestMetrics(telemetry["meter"])
HTTPXClientInstrumentor().instrument()

if faults.active_faults():
    log_event(logger, logging.WARNING, SERVICE_NAME, "fault_injection_active",
              f"FAULT INJECTION ACTIVE: {', '.join(faults.active_faults())}")

DATABASE_URL = os.environ.get("DATABASE_URL", "http://database:8000")

app = FastAPI(title="Payasam Payment Service")
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
            resp = await client.get(f"{DATABASE_URL}/health")
        if resp.status_code == 200:
            return {"status": "ready", "service": SERVICE_NAME}
    except httpx.HTTPError:
        pass
    return JSONResponse(status_code=503, content={"status": "not_ready", "service": SERVICE_NAME, "reason": "database_unreachable"})


class PaymentRequest(BaseModel):
    transaction_id: str
    amount_cents: int


@app.post("/payments", status_code=201)
async def create_payment(req: PaymentRequest):
    if req.amount_cents <= 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_amount", "message": "amount_cents must be positive"})

    # Phase 4 controlled fault injection -- disabled unless a
    # PAYASAM_FAULT_* env var is explicitly set (see faults.py). The
    # delay happens inside the actual request path, before any real
    # work, so it shows up as real latency on this service's own span.
    if faults.PAYMENT_LATENCY_MS > 0:
        await asyncio.sleep(faults.PAYMENT_LATENCY_MS / 1000.0)

    if faults.should_inject_payment_error():
        span = trace.get_current_span()
        span.set_status(Status(StatusCode.ERROR, description="injected payment fault"))
        span.set_attribute("error.type", "injected_fault")
        log_event(logger, logging.ERROR, SERVICE_NAME, "payment_fault_injected",
                  "payment rejected by injected fault", transaction_id=req.transaction_id)
        raise HTTPException(status_code=503, detail={"error": "payment_unavailable", "message": "Payment processing is temporarily unavailable"})

    payment_id = f"payment-{uuid.uuid4()}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{DATABASE_URL}/payments",
                json={
                    "payment_id": payment_id,
                    "transaction_id": req.transaction_id,
                    "amount_cents": req.amount_cents,
                    "status": "succeeded",
                },
            )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log_event(logger, logging.ERROR, SERVICE_NAME, "payment_persist_failed", "failed to persist payment",
                   transaction_id=req.transaction_id)
        raise HTTPException(status_code=502, detail={"error": "database_unavailable", "message": str(exc)})

    log_event(logger, logging.INFO, SERVICE_NAME, "payment_succeeded", "payment processed",
              transaction_id=req.transaction_id)
    return {"payment_id": payment_id, "status": "succeeded", "amount_cents": req.amount_cents}
