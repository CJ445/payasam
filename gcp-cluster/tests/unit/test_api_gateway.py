"""Unit tests for api-gateway: forwards to order-service and passes
through its response, exercised against the real chain in-process."""

from fastapi.testclient import TestClient


def test_gateway_forwards_successful_order(full_stack):
    with TestClient(full_stack["gateway"]) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-003", "quantity": 1}]},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert "request_id" in body


def test_gateway_forwards_failure_status(full_stack):
    with TestClient(full_stack["gateway"]) as client:
        resp = client.post(
            "/orders",
            json={"user_id": "user-001", "items": [{"product_id": "product-002", "quantity": 999}]},
        )
    assert resp.status_code == 409
    assert resp.json()["error"] == "insufficient_inventory"


def test_gateway_health(full_stack):
    with TestClient(full_stack["gateway"]) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
