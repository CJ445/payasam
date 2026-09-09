"""Integration tests: real HTTP, over the network, against the actually
deployed Phase 1 Kubernetes services. Proves service discovery, real
service-to-service communication, and real database state changes -- not
just that pods report Running.
"""

import httpx


def test_health_endpoints_reachable(gateway_url):
    resp = httpx.get(f"{gateway_url}/health", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_successful_order_transaction_end_to_end(gateway_url, database_url):
    before = httpx.get(f"{database_url}/products/product-001", timeout=5).json()

    resp = httpx.post(
        f"{gateway_url}/orders",
        json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 1}]},
        timeout=15,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert body["total_cents"] == before["price_cents"]
    assert "order_id" in body
    assert "payment_id" in body
    assert "request_id" in body

    # Inventory really changed.
    after = httpx.get(f"{database_url}/products/product-001", timeout=5).json()
    assert after["quantity"] == before["quantity"] - 1

    # Order really persisted.
    order_resp = httpx.get(f"{database_url}/orders/{body['order_id']}", timeout=5)
    assert order_resp.status_code == 200
    assert order_resp.json()["status"] == "completed"

    # Payment really persisted.
    payment_resp = httpx.get(f"{database_url}/payments/{body['payment_id']}", timeout=5)
    assert payment_resp.status_code == 200
    assert payment_resp.json()["status"] == "succeeded"


def test_state_persists_across_multiple_requests(gateway_url, database_url):
    before = httpx.get(f"{database_url}/products/product-003", timeout=5).json()["quantity"]

    for _ in range(3):
        resp = httpx.post(
            f"{gateway_url}/orders",
            json={"user_id": "user-003", "items": [{"product_id": "product-003", "quantity": 1}]},
            timeout=15,
        )
        assert resp.status_code == 201

    after = httpx.get(f"{database_url}/products/product-003", timeout=5).json()["quantity"]
    assert after == before - 3


def test_insufficient_inventory_returns_real_failure(gateway_url, database_url):
    before = httpx.get(f"{database_url}/products/product-002", timeout=5).json()["quantity"]

    resp = httpx.post(
        f"{gateway_url}/orders",
        json={"user_id": "user-001", "items": [{"product_id": "product-002", "quantity": before + 1000}]},
        timeout=15,
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "insufficient_inventory"

    after = httpx.get(f"{database_url}/products/product-002", timeout=5).json()["quantity"]
    assert after == before
