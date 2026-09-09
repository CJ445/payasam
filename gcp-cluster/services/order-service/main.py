"""Payasam `order-service` (Phase 1).

Orchestrates the transaction: reserves inventory for each item, charges
payment for the total, persists the resulting order, and returns a real
success or a real, structured failure. On a downstream failure, it
compensates by releasing any inventory it already reserved rather than
leaving stock silently stuck — this is real application behavior, not a
simulated one.
"""

import asyncio
import logging
import os
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from pydantic import BaseModel, field_validator

from logging_utils import get_logger, log_event
from telemetry import RequestMetrics, attach_otlp_logging_handler, setup_telemetry

SERVICE_NAME = "payasam-order-service"
logger = get_logger(SERVICE_NAME)
telemetry = setup_telemetry(SERVICE_NAME)
attach_otlp_logging_handler(logger, telemetry["logger_provider"])
metrics = RequestMetrics(telemetry["meter"])
HTTPXClientInstrumentor().instrument()

INVENTORY_SERVICE_URL = os.environ.get("INVENTORY_SERVICE_URL", "http://inventory-service:8000")
PAYMENT_SERVICE_URL = os.environ.get("PAYMENT_SERVICE_URL", "http://payment-service:8000")
DATABASE_URL = os.environ.get("DATABASE_URL", "http://database:8000")

app = FastAPI(title="Payasam Order Service")
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


async def _check_dependency(client: httpx.AsyncClient, url: str) -> bool:
    try:
        resp = await client.get(f"{url}/health")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@app.get("/ready")
async def ready():
    # Checked concurrently, not sequentially -- three dependencies checked
    # one after another could take up to 3x as long in the worst case
    # (all slow at once), which is exactly the scenario later
    # failure-injection phases want to exercise, and would otherwise blow
    # past Kubernetes' readiness probe timeout well before this handler
    # itself gives up.
    names = ["inventory-service", "payment-service", "database"]
    urls = [INVENTORY_SERVICE_URL, PAYMENT_SERVICE_URL, DATABASE_URL]
    async with httpx.AsyncClient(timeout=3.0) as client:
        results = await asyncio.gather(*(_check_dependency(client, url) for url in urls))
    checks = dict(zip(names, results))
    if all(checks.values()):
        return {"status": "ready", "service": SERVICE_NAME, "dependencies": checks}
    return JSONResponse(status_code=503, content={"status": "not_ready", "service": SERVICE_NAME, "dependencies": checks})


class OrderItem(BaseModel):
    product_id: str
    quantity: int

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("quantity must be positive")
        return v


class OrderRequest(BaseModel):
    user_id: str
    items: list[OrderItem]

    @field_validator("items")
    @classmethod
    def items_not_empty(cls, v: list[OrderItem]) -> list[OrderItem]:
        if not v:
            raise ValueError("items must not be empty")
        return v


def error_response(status_code: int, error: str, message: str, request_id: str, transaction_id: str):
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message, "request_id": request_id, "transaction_id": transaction_id},
    )


@app.post("/orders", status_code=201)
async def create_order(order: OrderRequest, request: Request):
    request_id = request.headers.get("X-Request-Id") or f"req-{uuid.uuid4()}"
    transaction_id = f"txn-{uuid.uuid4()}"

    current_span = trace.get_current_span()
    current_span.set_attribute("transaction_id", transaction_id)
    current_span.set_attribute("order.operation", "create_order")
    current_span.set_attribute("order.item_count", len(order.items))

    log_event(logger, logging.INFO, SERVICE_NAME, "order_received", "order received",
              request_id=request_id, transaction_id=transaction_id, user_id=order.user_id)

    reserved_items: list[dict] = []
    total_cents = 0

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Step 1: reserve inventory for every item. Compensate (release
        # already-reserved items) on the first failure of any kind.
        for item in order.items:
            try:
                resp = await client.post(
                    f"{INVENTORY_SERVICE_URL}/reserve",
                    json={"product_id": item.product_id, "quantity": item.quantity, "transaction_id": transaction_id},
                )
            except httpx.HTTPError as exc:
                await _release_all(client, reserved_items, transaction_id)
                await _persist_failed_order(client, transaction_id, order.user_id, total_cents)
                log_event(logger, logging.ERROR, SERVICE_NAME, "inventory_unavailable", "inventory service unreachable",
                          request_id=request_id, transaction_id=transaction_id, user_id=order.user_id)
                return error_response(502, "inventory_unavailable", "Inventory service unavailable", request_id, transaction_id)

            if resp.status_code == 409:
                await _release_all(client, reserved_items, transaction_id)
                await _persist_failed_order(client, transaction_id, order.user_id, total_cents)
                log_event(logger, logging.WARNING, SERVICE_NAME, "insufficient_inventory", "insufficient inventory",
                          request_id=request_id, transaction_id=transaction_id, user_id=order.user_id)
                return error_response(409, "insufficient_inventory", f"Insufficient stock for {item.product_id}", request_id, transaction_id)

            if resp.status_code == 404:
                await _release_all(client, reserved_items, transaction_id)
                await _persist_failed_order(client, transaction_id, order.user_id, total_cents)
                return error_response(404, "product_not_found", f"No such product: {item.product_id}", request_id, transaction_id)

            if resp.status_code != 200:
                # Any other status (e.g. inventory-service returning 5xx
                # because ITS downstream is degraded) must be treated as a
                # real failure with full compensation -- never assumed to
                # be success, and never allowed to throw an uncaught
                # exception that would skip the release below.
                await _release_all(client, reserved_items, transaction_id)
                await _persist_failed_order(client, transaction_id, order.user_id, total_cents)
                log_event(logger, logging.ERROR, SERVICE_NAME, "inventory_error", "inventory service returned unexpected status",
                          request_id=request_id, transaction_id=transaction_id, status_code=resp.status_code, user_id=order.user_id)
                return error_response(502, "inventory_unavailable", "Inventory service unavailable", request_id, transaction_id)

            body = resp.json()
            reserved_items.append({"product_id": item.product_id, "quantity": item.quantity})
            total_cents += body["unit_price_cents"] * item.quantity

        # Step 2: charge payment for the reserved total.
        try:
            payment_resp = await client.post(
                f"{PAYMENT_SERVICE_URL}/payments",
                json={"transaction_id": transaction_id, "amount_cents": total_cents},
            )
        except httpx.HTTPError as exc:
            await _release_all(client, reserved_items, transaction_id)
            await _persist_failed_order(client, transaction_id, order.user_id, total_cents)
            log_event(logger, logging.ERROR, SERVICE_NAME, "payment_unavailable", "payment service unreachable",
                      request_id=request_id, transaction_id=transaction_id, user_id=order.user_id)
            return error_response(502, "payment_failed", "Payment processing failed", request_id, transaction_id)

        if payment_resp.status_code != 201:
            await _release_all(client, reserved_items, transaction_id)
            await _persist_failed_order(client, transaction_id, order.user_id, total_cents)
            log_event(logger, logging.WARNING, SERVICE_NAME, "payment_declined", "payment declined",
                      request_id=request_id, transaction_id=transaction_id, status_code=payment_resp.status_code, user_id=order.user_id)
            return error_response(502, "payment_failed", "Payment processing failed", request_id, transaction_id)

        payment_body = payment_resp.json()

        # Step 3: persist the completed order.
        order_id = f"order-{uuid.uuid4()}"
        try:
            persist_resp = await client.post(
                f"{DATABASE_URL}/orders",
                json={
                    "order_id": order_id,
                    "transaction_id": transaction_id,
                    "user_id": order.user_id,
                    "total_cents": total_cents,
                    "status": "completed",
                },
            )
        except httpx.HTTPError:
            log_event(logger, logging.ERROR, SERVICE_NAME, "order_persist_failed", "failed to persist completed order",
                      request_id=request_id, transaction_id=transaction_id, user_id=order.user_id)
            return error_response(502, "order_persist_failed", "Order completed but failed to persist", request_id, transaction_id)

        if persist_resp.status_code != 201:
            # Payment already succeeded at this point -- we report this
            # distinctly (not a generic "completed") rather than silently
            # returning success for an order that was never actually
            # written to the database.
            log_event(logger, logging.ERROR, SERVICE_NAME, "order_persist_failed", "database rejected completed order",
                      request_id=request_id, transaction_id=transaction_id, status_code=persist_resp.status_code, user_id=order.user_id)
            return error_response(502, "order_persist_failed", "Order completed but failed to persist", request_id, transaction_id)

    log_event(logger, logging.INFO, SERVICE_NAME, "order_completed", "order completed",
              request_id=request_id, transaction_id=transaction_id, status_code=201, user_id=order.user_id)

    return {
        "order_id": order_id,
        "transaction_id": transaction_id,
        "request_id": request_id,
        "status": "completed",
        "total_cents": total_cents,
        "payment_id": payment_body["payment_id"],
    }


async def _release_all(client: httpx.AsyncClient, reserved_items: list[dict], transaction_id: str) -> None:
    for item in reserved_items:
        try:
            await client.post(
                f"{INVENTORY_SERVICE_URL}/release",
                json={"product_id": item["product_id"], "quantity": item["quantity"], "transaction_id": transaction_id},
            )
        except httpx.HTTPError:
            log_event(logger, logging.ERROR, SERVICE_NAME, "release_failed", "failed to release reserved inventory",
                      transaction_id=transaction_id)


async def _persist_failed_order(client: httpx.AsyncClient, transaction_id: str, user_id: str, total_cents: int) -> None:
    order_id = f"order-{uuid.uuid4()}"
    try:
        await client.post(
            f"{DATABASE_URL}/orders",
            json={
                "order_id": order_id,
                "transaction_id": transaction_id,
                "user_id": user_id,
                "total_cents": total_cents,
                "status": "failed",
            },
        )
    except httpx.HTTPError:
        log_event(logger, logging.ERROR, SERVICE_NAME, "order_persist_failed", "failed to persist failed order",
                   transaction_id=transaction_id, user_id=user_id)
