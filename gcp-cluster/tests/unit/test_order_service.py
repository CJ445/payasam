"""Unit tests for order-service: the full orchestration chain
(order-service -> inventory-service -> payment-service -> database),
exercised in-process via the real ASGI apps (see conftest.py `full_stack`).

These prove business/orchestration logic quickly, without a cluster. The
`tests/integration` suite separately proves the same behavior over a real
network against the actually-deployed Kubernetes services, including a
genuine Payment Service outage.
"""

import httpx
from fastapi.testclient import TestClient

from conftest import install_mounts


def _order_client(full_stack):
    return TestClient(full_stack["order"])


def test_successful_order_completes_and_persists(full_stack):
    with _order_client(full_stack) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 2}]},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert body["total_cents"] == 1000  # 2 x 500
    assert "order_id" in body and "payment_id" in body

    with TestClient(full_stack["database"]) as db_client:
        order_resp = db_client.get(f"/orders/{body['order_id']}")
        assert order_resp.status_code == 200
        assert order_resp.json()["status"] == "completed"

        product_resp = db_client.get("/products/product-001")
        assert product_resp.json()["quantity"] == 8  # 10 - 2


def test_order_rejects_empty_items(full_stack):
    with _order_client(full_stack) as client:
        resp = client.post("/orders", json={"user_id": "user-001", "items": []})
    assert resp.status_code == 422


def test_order_rejects_non_positive_quantity(full_stack):
    with _order_client(full_stack) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 0}]},
        )
    assert resp.status_code == 422


def test_insufficient_inventory_fails_order_and_leaves_stock_untouched(full_stack):
    with _order_client(full_stack) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-002", "quantity": 999}]},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "insufficient_inventory"
    assert "request_id" in body and "transaction_id" in body

    with TestClient(full_stack["database"]) as db_client:
        product_resp = db_client.get("/products/product-002")
        assert product_resp.json()["quantity"] == 5  # unchanged


def test_payment_failure_releases_reserved_inventory_and_fails_order(full_stack, monkeypatch):
    """The key causal-correctness test: if Payment Service is unreachable
    after inventory was already reserved, the order must fail (not
    silently succeed), and the reservation must be compensated (released)
    rather than left stuck decremented."""

    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused (simulated payment outage)", request=request)

    broken_mounts = dict(full_stack["mounts"])
    broken_mounts["http://payment-service:8000"] = httpx.MockTransport(failing_handler)
    install_mounts(monkeypatch, broken_mounts)

    with TestClient(full_stack["database"]) as db_client:
        before = db_client.get("/products/product-001").json()["quantity"]

    with _order_client(full_stack) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 2}]},
        )

    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "payment_failed"

    with TestClient(full_stack["database"]) as db_client:
        after = db_client.get("/products/product-001").json()["quantity"]

    assert after == before, "inventory reservation must be released when payment fails"
