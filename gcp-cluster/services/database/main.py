"""Payasam `database` service (Phase 1).

The single writer of application state (products/inventory, orders,
payments). Other services reach it over HTTP using its Kubernetes Service
name, never a shared file or embedded library — see db.py's module
docstring and DECISIONS.md D005.
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

import db
import faults
from logging_utils import get_logger, log_event
from telemetry import RequestMetrics, attach_otlp_logging_handler, setup_telemetry

SERVICE_NAME = "payasam-database"
logger = get_logger(SERVICE_NAME)
telemetry = setup_telemetry(SERVICE_NAME)
attach_otlp_logging_handler(logger, telemetry["logger_provider"])
metrics = RequestMetrics(telemetry["meter"])

if faults.active_faults():
    log_event(logger, logging.WARNING, SERVICE_NAME, "fault_injection_active",
              f"FAULT INJECTION ACTIVE: {', '.join(faults.active_faults())}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    log_event(logger, logging.INFO, SERVICE_NAME, "startup", "database initialized")
    yield


app = FastAPI(title="Payasam Database Service", lifespan=lifespan)
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


@app.post("/admin/reset")
def reset():
    """Restore seeded product quantities and clear orders/payments. A
    test/demo reset utility (PRD's documented `reset` script requirement),
    not a failure-injection mechanism -- it does not touch service
    behavior, latency, or error rates."""
    db.reset_db()
    log_event(logger, logging.WARNING, SERVICE_NAME, "reset", "database reset to seeded state")
    return {"status": "reset"}


@app.get("/products/{product_id}")
def get_product(product_id: str):
    product = db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail={"error": "product_not_found", "message": f"No such product: {product_id}"})
    return dict(product)


class ReserveRequest(BaseModel):
    product_id: str
    quantity: int
    transaction_id: str | None = None


@app.post("/inventory/reserve")
def reserve_inventory(req: ReserveRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_quantity", "message": "quantity must be positive"})
    result = db.reserve_inventory(req.product_id, req.quantity)
    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail={"error": "product_not_found", "message": f"No such product: {req.product_id}"})
    if result["outcome"] == "insufficient_stock":
        return JSONResponse(
            status_code=409,
            content={
                "error": "insufficient_inventory",
                "message": f"Insufficient stock for {req.product_id}",
                "available_quantity": result["available_quantity"],
            },
        )
    log_event(logger, logging.INFO, SERVICE_NAME, "inventory_reserved", "inventory reserved",
              product_id=req.product_id, transaction_id=req.transaction_id)
    return {
        "reserved": True,
        "unit_price_cents": result["unit_price_cents"],
        "remaining_quantity": result["remaining_quantity"],
    }


class ReleaseRequest(BaseModel):
    product_id: str
    quantity: int
    transaction_id: str | None = None


@app.post("/inventory/release")
def release_inventory(req: ReleaseRequest):
    result = db.release_inventory(req.product_id, req.quantity)
    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail={"error": "product_not_found", "message": f"No such product: {req.product_id}"})
    log_event(logger, logging.INFO, SERVICE_NAME, "inventory_released", "inventory released",
              product_id=req.product_id, transaction_id=req.transaction_id)
    return {"released": True, "remaining_quantity": result["remaining_quantity"]}


class PaymentRecord(BaseModel):
    payment_id: str
    transaction_id: str
    amount_cents: int
    status: str


@app.post("/payments", status_code=201)
def create_payment(record: PaymentRecord):
    created_at = datetime.now(timezone.utc).isoformat()
    db.create_payment(record.payment_id, record.transaction_id, record.amount_cents, record.status, created_at)
    log_event(logger, logging.INFO, SERVICE_NAME, "payment_persisted", "payment persisted",
              transaction_id=record.transaction_id)
    return {"payment_id": record.payment_id, "status": record.status, "created_at": created_at}


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    payment = db.get_payment(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail={"error": "payment_not_found", "message": f"No such payment: {payment_id}"})
    return dict(payment)


class OrderRecord(BaseModel):
    order_id: str
    transaction_id: str
    user_id: str
    total_cents: int
    status: str


@app.post("/orders", status_code=201)
def create_order(record: OrderRecord):
    created_at = datetime.now(timezone.utc).isoformat()
    db.create_order(record.order_id, record.transaction_id, record.user_id, record.total_cents, record.status, created_at)
    log_event(logger, logging.INFO, SERVICE_NAME, "order_persisted", "order persisted",
              transaction_id=record.transaction_id)
    return {"order_id": record.order_id, "status": record.status, "created_at": created_at}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    order = db.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail={"error": "order_not_found", "message": f"No such order: {order_id}"})
    return dict(order)
