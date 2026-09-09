"""Unit tests for inventory-service, exercised against the real database
service via in-process ASGI transport (see conftest.py `full_stack`)."""

from fastapi.testclient import TestClient


def test_reserve_success(full_stack):
    with TestClient(full_stack["inventory"]) as client:
        resp = client.post("/reserve", json={"product_id": "product-001", "quantity": 2, "transaction_id": "txn-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reserved"] is True
        assert body["remaining_quantity"] == 8


def test_reserve_insufficient_inventory(full_stack):
    with TestClient(full_stack["inventory"]) as client:
        resp = client.post("/reserve", json={"product_id": "product-002", "quantity": 999, "transaction_id": "txn-1"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "insufficient_inventory"


def test_reserve_unknown_product(full_stack):
    with TestClient(full_stack["inventory"]) as client:
        resp = client.post("/reserve", json={"product_id": "no-such-product", "quantity": 1, "transaction_id": "txn-1"})
        assert resp.status_code == 404


def test_reserve_rejects_non_positive_quantity(full_stack):
    with TestClient(full_stack["inventory"]) as client:
        resp = client.post("/reserve", json={"product_id": "product-001", "quantity": 0, "transaction_id": "txn-1"})
        assert resp.status_code == 400


def test_release_restores_quantity(full_stack):
    with TestClient(full_stack["inventory"]) as client:
        client.post("/reserve", json={"product_id": "product-001", "quantity": 4, "transaction_id": "txn-1"})
        resp = client.post("/release", json={"product_id": "product-001", "quantity": 4, "transaction_id": "txn-1"})
        assert resp.status_code == 200
        assert resp.json()["remaining_quantity"] == 10
