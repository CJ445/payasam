"""Unit tests for payment-service, exercised against the real database
service via in-process ASGI transport (see conftest.py `full_stack`)."""

from fastapi.testclient import TestClient


def test_create_payment_success(full_stack):
    with TestClient(full_stack["payment"]) as client:
        resp = client.post("/payments", json={"transaction_id": "txn-1", "amount_cents": 1500})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["amount_cents"] == 1500
        assert body["payment_id"].startswith("payment-")

    # Persisted for real, verifiable via the database service directly.
    with TestClient(full_stack["database"]) as db_client:
        resp2 = db_client.get(f"/payments/{body['payment_id']}")
        assert resp2.status_code == 200
        assert resp2.json()["transaction_id"] == "txn-1"


def test_create_payment_rejects_non_positive_amount(full_stack):
    with TestClient(full_stack["payment"]) as client:
        resp = client.post("/payments", json={"transaction_id": "txn-1", "amount_cents": 0})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_amount"
