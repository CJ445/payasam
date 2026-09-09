"""Live integration test for payasam-backend's POST /traffic/generate --
the endpoint the UI's "Generate Test Traffic" button calls, added
specifically so the Failure Simulator is self-contained (inject a fault,
then make it actually manifest) without a separate terminal running
scripts/run-baseline.sh.

Proves the same causal chain test_business_impact_live.py proves, but
through this new code path specifically: real requests sent by
payasam-backend itself -> real order-service failures -> reflected in
/impact.
"""

import subprocess
import time

import httpx
import pytest

NAMESPACE = "payasam"


def _wait_until(check, timeout=30.0, interval=2.0):
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(interval)
    return result


@pytest.fixture
def backend_client(payasam_backend_url):
    with httpx.Client(base_url=payasam_backend_url, timeout=20) as client:
        yield client


@pytest.fixture
def ensure_recovered(backend_client):
    yield
    backend_client.post("/faults/recover")
    _wait_until(lambda: backend_client.get("/status").json()["overall_status"] == "healthy", timeout=30.0)
    # See the identical comment in test_faults_control_live.py's
    # ensure_recovered -- /status healthy doesn't guarantee the old pod
    # is fully gone yet.
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "rollout", "status", "deployment/payment-service", "--timeout=60s"],
        check=True,
    )


def test_generate_traffic_all_succeeds_on_healthy_system(backend_client):
    resp = backend_client.post("/traffic/generate", params={"count": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 5
    assert body["success"] == 5
    assert body["failed"] == 0


def test_generate_traffic_rejects_out_of_range_count(backend_client):
    for bad_count in (0, -1, 51, 1000):
        resp = backend_client.post("/traffic/generate", params={"count": bad_count})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "invalid_count"


@pytest.mark.usefixtures("ensure_recovered")
def test_generated_traffic_under_a_fault_is_reflected_in_impact(backend_client):
    inject_resp = backend_client.post("/faults/inject", params={"scenario": "payment_errors"})
    assert inject_resp.status_code == 200

    # /faults reflects the Deployment's *desired* spec the instant it's
    # patched -- not whether the rolling restart has actually replaced
    # the running pod. Traffic sent before the old, not-yet-faulted pod
    # is gone can land there and "succeed" for real, which is exactly
    # the readiness-race already documented in
    # docs/phase4-failure-experiments.md Sec 7. Wait for the actual
    # rollout, not just the spec, before generating traffic.
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "rollout", "status", "deployment/payment-service", "--timeout=60s"],
        check=True,
    )

    traffic_resp = backend_client.post("/traffic/generate", params={"count": 5})
    assert traffic_resp.status_code == 200
    traffic = traffic_resp.json()
    assert traffic["failed"] > 0, "expected at least one real failure while the fault is active"

    impact = _wait_until(
        lambda: backend_client.get("/impact", params={"window_minutes": 5}).json()
        if backend_client.get("/impact", params={"window_minutes": 5}).json()["failed_transactions"] >= traffic["failed"]
        else None,
        timeout=30.0,
    )
    assert impact is not None, "impact did not reflect the generated failures within the timeout"
    assert impact["failure_breakdown"].get("payment_declined", 0) >= traffic["failed"]
