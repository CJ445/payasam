"""The Phase 1 failure test required by the task: make Payment Service
genuinely unavailable (scaled to zero replicas -- not the Phase 4 failure
controller, just a temporary, reversible mechanism for this one test),
prove Order Service detects it and the client gets a real failure with no
false success, then restore Payment Service and prove recovery.
"""

import subprocess
import time

import httpx
import pytest

NAMESPACE = "payasam"


def _scale_payment_service(replicas: int) -> None:
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "scale", "deployment/payment-service", f"--replicas={replicas}"],
        check=True,
    )


def _wait_no_payment_pods(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "get", "pods", "-l", "app=payment-service",
             "--field-selector=status.phase=Running", "-o", "name"],
            capture_output=True, text=True, check=True,
        )
        if result.stdout.strip() == "":
            return
        time.sleep(1)
    raise TimeoutError("payment-service pods did not terminate in time")


@pytest.mark.usefixtures("restore_payment_service")
def test_payment_service_outage_causes_real_failure_and_recovers(gateway_url, database_url):
    before = httpx.get(f"{database_url}/products/product-001", timeout=5).json()["quantity"]

    # 1. Make Payment Service genuinely unavailable.
    _scale_payment_service(0)
    _wait_no_payment_pods()

    # 2 & 3. Submit an order; Order Service must detect the failure.
    resp = httpx.post(
        f"{gateway_url}/orders",
        json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 1}]},
        timeout=15,
    )

    # 4. API Gateway must return a real failure, not a false success.
    assert resp.status_code != 201, "order must not falsely succeed while Payment Service is down"
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "payment_failed"

    # 5. No false successful transaction: reserved inventory must have
    # been released (compensated), not left stuck decremented.
    after_failure = httpx.get(f"{database_url}/products/product-001", timeout=5).json()["quantity"]
    assert after_failure == before, "inventory must be released when payment fails, not left decremented"

    # 6. Restore Payment Service.
    _scale_payment_service(1)
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "rollout", "status", "deployment/payment-service", "--timeout=60s"],
        check=True,
    )

    # 7. Subsequent orders must succeed again.
    resp2 = httpx.post(
        f"{gateway_url}/orders",
        json={"user_id": "user-001", "items": [{"product_id": "product-001", "quantity": 1}]},
        timeout=15,
    )
    assert resp2.status_code == 201
    assert resp2.json()["status"] == "completed"

    after_recovery = httpx.get(f"{database_url}/products/product-001", timeout=5).json()["quantity"]
    assert after_recovery == before - 1
