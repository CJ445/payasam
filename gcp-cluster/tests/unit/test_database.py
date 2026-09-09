"""Unit tests for the `database` service: seeding, inventory reservation
(including insufficient stock), and order/payment persistence."""


def test_seeded_products_available(database_client):
    resp = database_client.get("/products/product-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product_id"] == "product-001"
    assert body["quantity"] == 10
    assert body["price_cents"] == 500


def test_unknown_product_returns_404(database_client):
    resp = database_client.get("/products/does-not-exist")
    assert resp.status_code == 404


def test_reserve_inventory_decrements_quantity(database_client):
    resp = database_client.post("/inventory/reserve", json={"product_id": "product-001", "quantity": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reserved"] is True
    assert body["remaining_quantity"] == 7
    assert body["unit_price_cents"] == 500

    # Quantity change is durable across requests.
    resp2 = database_client.get("/products/product-001")
    assert resp2.json()["quantity"] == 7


def test_reserve_insufficient_inventory_rejected(database_client):
    resp = database_client.post("/inventory/reserve", json={"product_id": "product-002", "quantity": 999})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "insufficient_inventory"
    assert body["available_quantity"] == 5

    # Rejected reservation must not have changed the stored quantity.
    resp2 = database_client.get("/products/product-002")
    assert resp2.json()["quantity"] == 5


def test_release_inventory_restores_quantity(database_client):
    database_client.post("/inventory/reserve", json={"product_id": "product-003", "quantity": 5})
    resp = database_client.post("/inventory/release", json={"product_id": "product-003", "quantity": 5})
    assert resp.status_code == 200
    assert resp.json()["remaining_quantity"] == 20

    resp2 = database_client.get("/products/product-003")
    assert resp2.json()["quantity"] == 20


def test_create_and_fetch_payment(database_client):
    resp = database_client.post(
        "/payments",
        json={"payment_id": "payment-1", "transaction_id": "txn-1", "amount_cents": 1000, "status": "succeeded"},
    )
    assert resp.status_code == 201

    resp2 = database_client.get("/payments/payment-1")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["amount_cents"] == 1000
    assert body["status"] == "succeeded"
    assert body["transaction_id"] == "txn-1"


def test_create_and_fetch_order(database_client):
    resp = database_client.post(
        "/orders",
        json={"order_id": "order-1", "transaction_id": "txn-1", "user_id": "user-001", "total_cents": 500, "status": "completed"},
    )
    assert resp.status_code == 201

    resp2 = database_client.get("/orders/order-1")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["user_id"] == "user-001"
    assert body["status"] == "completed"
    assert body["total_cents"] == 500
