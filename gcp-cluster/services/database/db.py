"""SQLite-backed persistence for Payasam (Phase 1).

Per DECISIONS.md D005, this is a small, real, controllable dependency that
`order-service`, `inventory-service`, and `payment-service` call over HTTP —
not an embedded library each service links directly. That's what lets
Phase 4 later introduce controlled latency/error injection at a single,
well-defined point without an architectural rewrite. Phase 1 does not add
that injection logic yet; it only needs the shape to support it later.
"""

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from opentelemetry import trace

import faults

_tracer = trace.get_tracer("payasam.database.db")


def _db_path() -> Path:
    # Read lazily (not once at import time) so tests can point each run at
    # an isolated temp file via the DATABASE_FILE env var.
    return Path(os.environ.get("DATABASE_FILE", str(Path(__file__).parent / "data" / "payasam.db")))


# A single global lock serializes all access. This is a demo-scale system
# (10-50 simulated users, low RPS) where correctness and simplicity matter
# more than write concurrency; SQLite plus uvicorn's threadpool would
# otherwise risk "database is locked" errors under concurrent writes.
#
# Phase 2's baseline experiment observed latency growing substantially
# with concurrency and hypothesized this lock as the likely cause, but
# noted that hypothesis wasn't confirmed via instrumentation. `_traced_op`
# times lock acquisition separately from the rest of the operation and
# records it as a span attribute specifically so that claim can be
# checked against real telemetry instead of staying a guess.
_lock = threading.Lock()


@contextmanager
def _traced_op(operation: str, apply_fault: bool = True, **attributes):
    """Wraps a database operation in a span, with lock-wait time broken
    out as its own attribute (see note above _lock).

    Phase 4: if PAYASAM_FAULT_DB_LATENCY_MS is set, the delay is applied
    *while holding the lock* (apply_fault=True operations only --
    init_db/reset_db opt out, since they're administrative, not part of
    the transaction path Scenario C studies). This makes the fault
    show up two ways in telemetry: as this operation's own span duration,
    and as db.lock_wait_ms on any other concurrent operation genuinely
    blocked behind it -- see faults.py's module docstring.
    """
    with _tracer.start_as_current_span(f"db.{operation}") as span:
        span.set_attribute("db.system", "sqlite")
        span.set_attribute("order.operation", operation)
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)

        lock_wait_start = time.monotonic()
        with _lock:
            lock_wait_ms = (time.monotonic() - lock_wait_start) * 1000.0
            span.set_attribute("db.lock_wait_ms", lock_wait_ms)
            if apply_fault and faults.DB_LATENCY_MS > 0:
                time.sleep(faults.DB_LATENCY_MS / 1000.0)
            yield span

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    quantity INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

SEED_PRODUCTS = [
    ("product-001", "Widget", 500, 10),
    ("product-002", "Gadget", 1200, 5),
    ("product-003", "Gizmo", 750, 20),
]


def get_connection() -> sqlite3.Connection:
    db_file = _db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _traced_op("init_db", apply_fault=False):
        conn = get_connection()
        try:
            conn.executescript(SCHEMA)
            existing = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
            if existing == 0:
                conn.executemany(
                    "INSERT INTO products (product_id, name, price_cents, quantity) VALUES (?, ?, ?, ?)",
                    SEED_PRODUCTS,
                )
        finally:
            conn.close()


def get_product(product_id: str) -> sqlite3.Row | None:
    with _traced_op("get_product", **{"db.product_id": product_id}):
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT product_id, name, price_cents, quantity FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        finally:
            conn.close()


def reserve_inventory(product_id: str, quantity: int) -> dict:
    """Atomically check-and-decrement. Returns a result dict; never raises
    for ordinary business outcomes (product missing / insufficient stock) so
    callers can translate it into the right HTTP status."""
    with _traced_op("reserve_inventory", **{"db.product_id": product_id, "db.quantity": quantity}) as span:
        conn = get_connection()
        try:
            product = conn.execute(
                "SELECT product_id, price_cents, quantity FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if product is None:
                span.set_attribute("db.outcome", "not_found")
                return {"outcome": "not_found"}
            if product["quantity"] < quantity:
                span.set_attribute("db.outcome", "insufficient_stock")
                return {"outcome": "insufficient_stock", "available_quantity": product["quantity"]}
            conn.execute(
                "UPDATE products SET quantity = quantity - ? WHERE product_id = ?",
                (quantity, product_id),
            )
            remaining = product["quantity"] - quantity
            span.set_attribute("db.outcome", "reserved")
            return {
                "outcome": "reserved",
                "unit_price_cents": product["price_cents"],
                "remaining_quantity": remaining,
            }
        finally:
            conn.close()


def release_inventory(product_id: str, quantity: int) -> dict:
    with _traced_op("release_inventory", **{"db.product_id": product_id, "db.quantity": quantity}) as span:
        conn = get_connection()
        try:
            product = conn.execute(
                "SELECT quantity FROM products WHERE product_id = ?", (product_id,)
            ).fetchone()
            if product is None:
                span.set_attribute("db.outcome", "not_found")
                return {"outcome": "not_found"}
            conn.execute(
                "UPDATE products SET quantity = quantity + ? WHERE product_id = ?",
                (quantity, product_id),
            )
            span.set_attribute("db.outcome", "released")
            return {"outcome": "released", "remaining_quantity": product["quantity"] + quantity}
        finally:
            conn.close()


def create_payment(payment_id: str, transaction_id: str, amount_cents: int, status: str, created_at: str) -> None:
    with _traced_op("create_payment", transaction_id=transaction_id, **{"db.payment_id": payment_id}):
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO payments (payment_id, transaction_id, amount_cents, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (payment_id, transaction_id, amount_cents, status, created_at),
            )
        finally:
            conn.close()


def get_payment(payment_id: str) -> sqlite3.Row | None:
    with _traced_op("get_payment", **{"db.payment_id": payment_id}):
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT payment_id, transaction_id, amount_cents, status, created_at "
                "FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()
        finally:
            conn.close()


def create_order(
    order_id: str, transaction_id: str, user_id: str, total_cents: int, status: str, created_at: str
) -> None:
    with _traced_op("create_order", transaction_id=transaction_id, **{"db.order_id": order_id}):
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO orders (order_id, transaction_id, user_id, total_cents, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, transaction_id, user_id, total_cents, status, created_at),
            )
        finally:
            conn.close()


def reset_db() -> None:
    """Restore products to their seeded quantities and clear orders/payments.

    A test-and-demo utility, not a saturation/failure-injection mechanism
    (it does not touch latency/error behavior). Needed because this is a
    single shared piece of state with finite seeded stock: without a reset
    path, repeated test runs (or repeated manual demo runs) permanently
    consume inventory until orders start failing for the wrong reason --
    stock exhaustion, not the thing actually being tested.
    """
    with _traced_op("reset_db", apply_fault=False):
        conn = get_connection()
        try:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM payments")
            conn.execute("DELETE FROM products")
            conn.executemany(
                "INSERT INTO products (product_id, name, price_cents, quantity) VALUES (?, ?, ?, ?)",
                SEED_PRODUCTS,
            )
        finally:
            conn.close()


def get_order(order_id: str) -> sqlite3.Row | None:
    with _traced_op("get_order", **{"db.order_id": order_id}):
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT order_id, transaction_id, user_id, total_cents, status, created_at "
                "FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        finally:
            conn.close()
