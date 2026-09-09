"""Payasam `inventory-service` (Phase 1).

Performs a real database-backed inventory operation on every request by
calling the `database` service over HTTP — no local/faked state.
"""

import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from pydantic import BaseModel

from logging_utils import get_logger, log_event
from telemetry import RequestMetrics, attach_otlp_logging_handler, setup_telemetry

SERVICE_NAME = "payasam-inventory-service"
logger = get_logger(SERVICE_NAME)
telemetry = setup_telemetry(SERVICE_NAME)
attach_otlp_logging_handler(logger, telemetry["logger_provider"])
metrics = RequestMetrics(telemetry["meter"])
HTTPXClientInstrumentor().instrument()

DATABASE_URL = os.environ.get("DATABASE_URL", "http://database:8000")

app = FastAPI(title="Payasam Inventory Service")
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


class ReserveRequest(BaseModel):
    product_id: str
    quantity: int
    transaction_id: str | None = None


@app.post("/reserve")
async def reserve(req: ReserveRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_quantity", "message": "quantity must be positive"})

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{DATABASE_URL}/inventory/reserve",
                json={"product_id": req.product_id, "quantity": req.quantity, "transaction_id": req.transaction_id},
            )
    except httpx.HTTPError as exc:
        log_event(logger, logging.ERROR, SERVICE_NAME, "database_unavailable", "database unreachable during reserve",
                   transaction_id=req.transaction_id)
        raise HTTPException(status_code=502, detail={"error": "database_unavailable", "message": str(exc)})

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=resp.json().get("detail", resp.json()))
    if resp.status_code == 409:
        log_event(logger, logging.WARNING, SERVICE_NAME, "insufficient_inventory", "insufficient inventory",
                   transaction_id=req.transaction_id, product_id=req.product_id)
        return JSONResponse(status_code=409, content=resp.json())
    if resp.status_code != 200:
        # Any other status (e.g. database returning 5xx under degraded
        # conditions) must become a structured, logged failure here --
        # never an uncaught exception that skips the caller's ability to
        # compensate and reaches the client as a bare "Internal Server Error".
        log_event(logger, logging.ERROR, SERVICE_NAME, "database_error", "database returned unexpected status",
                   transaction_id=req.transaction_id, status_code=resp.status_code)
        raise HTTPException(status_code=502, detail={"error": "database_unavailable", "message": f"database returned status {resp.status_code}"})

    log_event(logger, logging.INFO, SERVICE_NAME, "reserved", "inventory reserved",
              transaction_id=req.transaction_id)
    return resp.json()


class ReleaseRequest(BaseModel):
    product_id: str
    quantity: int
    transaction_id: str | None = None


@app.post("/release")
async def release(req: ReleaseRequest):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{DATABASE_URL}/inventory/release",
                json={"product_id": req.product_id, "quantity": req.quantity, "transaction_id": req.transaction_id},
            )
    except httpx.HTTPError as exc:
        log_event(logger, logging.ERROR, SERVICE_NAME, "database_unavailable", "database unreachable during release",
                   transaction_id=req.transaction_id)
        raise HTTPException(status_code=502, detail={"error": "database_unavailable", "message": str(exc)})

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=resp.json().get("detail", resp.json()))
    if resp.status_code != 200:
        log_event(logger, logging.ERROR, SERVICE_NAME, "database_error", "database returned unexpected status",
                   transaction_id=req.transaction_id, status_code=resp.status_code)
        raise HTTPException(status_code=502, detail={"error": "database_unavailable", "message": f"database returned status {resp.status_code}"})

    log_event(logger, logging.INFO, SERVICE_NAME, "released", "inventory released",
              transaction_id=req.transaction_id)
    return resp.json()
