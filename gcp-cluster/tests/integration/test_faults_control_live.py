"""Live integration test for the UI's fault-control API
(payasam-backend's /faults/inject, /faults/recover, /status,
/remediation) -- real HTTP, over the network, against the actually
deployed backend and its real RBAC permission to patch Deployments.

This exercises the exact same path the Payasam UI uses to drive Phase 4
fault injection, instead of a person running scripts/set-fault.sh in a
terminal. During development this test's manual equivalent caught two
real bugs: an inverted "faults_readable" flag that always reported
overall_status as "unknown", and the Kubernetes Scale subresource
silently reporting replicas=0 as None (fixed in
payasam/backend/faults_control.py's get_replicas -- see its comment).
"""

import subprocess
import time

import httpx
import pytest

NAMESPACE = "payasam"


def _wait_until(check, timeout=30.0, interval=1.5):
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
    with httpx.Client(base_url=payasam_backend_url, timeout=15) as client:
        yield client


@pytest.fixture
def ensure_recovered(backend_client):
    yield
    backend_client.post("/faults/recover")
    _wait_until(lambda: backend_client.get("/status").json()["overall_status"] == "healthy", timeout=30.0)
    # /status turning "healthy" only means the new pod answered one real
    # HTTP health check -- it does not guarantee the old pod/ReplicaSet
    # has fully terminated. Waiting for the actual rollout too (as
    # scripts/set-fault.sh and clear-faults.sh already do) avoids leaving
    # a residual old pod that could serve one stray request to whichever
    # test file pytest happens to run next -- exactly what caused 3
    # unrelated tests to flake when the full suite was run back-to-back
    # during development of this file.
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "rollout", "status", "deployment/payment-service", "--timeout=60s"],
        check=True,
    )


@pytest.mark.usefixtures("ensure_recovered")
def test_baseline_is_healthy_with_no_active_faults(backend_client):
    result = _wait_until(lambda: backend_client.get("/status").json())
    assert result["overall_status"] == "healthy"
    assert result["active_faults"] == {}
    for service, state in result["services"].items():
        assert state == "healthy", f"{service} was not healthy at test start"


@pytest.mark.usefixtures("ensure_recovered")
def test_inject_payment_errors_reflected_in_status_and_remediation(backend_client):
    resp = backend_client.post("/faults/inject", params={"scenario": "payment_errors"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    status = _wait_until(
        lambda: backend_client.get("/status").json()
        if "PAYASAM_FAULT_PAYMENT_ERROR_RATE" in backend_client.get("/status").json()["active_faults"]
        else None
    )
    assert status is not None, "fault did not appear in /status within the timeout"
    assert status["overall_status"] == "degraded"

    remediation = backend_client.get("/remediation").json()
    assert remediation["status"] == "degraded"
    assert any("Payment Service is rejecting requests" in issue for issue in remediation["issues"])

    recover_resp = backend_client.post("/faults/recover")
    assert recover_resp.status_code == 200

    healthy = _wait_until(lambda: backend_client.get("/status").json()["overall_status"] == "healthy", timeout=30.0)
    assert healthy


@pytest.mark.usefixtures("ensure_recovered")
def test_unavailable_scenario_scales_to_zero_and_recovers(backend_client):
    resp = backend_client.post("/faults/inject", params={"scenario": "payment_unavailable"})
    assert resp.status_code == 200

    status = _wait_until(
        lambda: backend_client.get("/status").json()
        if "PAYASAM_SERVICE_UNAVAILABLE" in backend_client.get("/status").json()["active_faults"]
        else None,
        timeout=30.0,
    )
    assert status is not None, "PAYASAM_SERVICE_UNAVAILABLE did not appear in /status within the timeout"
    assert status["services"]["payment-service"] == "unreachable"

    backend_client.post("/faults/recover")
    recovered = _wait_until(
        lambda: backend_client.get("/status").json()
        if backend_client.get("/status").json()["overall_status"] == "healthy"
        else None,
        timeout=45.0,  # scaling back up + order-service's readiness re-check takes longer than an env-var restart
    )
    assert recovered is not None, "system did not return to healthy after recovering from the unavailable scenario"


def test_inject_rejects_unknown_scenario(backend_client):
    resp = backend_client.post("/faults/inject", params={"scenario": "not-a-real-scenario"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_scenario"
