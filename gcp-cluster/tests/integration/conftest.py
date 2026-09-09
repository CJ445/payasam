"""Integration test fixtures: real HTTP calls, over a real network, to the
actual services deployed in the `payasam` kind cluster (Phase 0/1
infrastructure). No in-process shortcuts here — that's what
tests/unit is for.

Access is via `kubectl port-forward`, since the kind cluster (per Phase 0's
kind-config.yaml) has no extraPortMappings exposing NodePorts to the host.
This does not modify the Phase 0 networking mechanism or the cluster.
"""

import socket
import subprocess
import time

import pytest

NAMESPACE = "payasam"


def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    raise TimeoutError(f"port {port} did not become reachable within {timeout}s: {last_error}")


def _port_forward(service: str, local_port: int, remote_port: int = 8000):
    proc = subprocess.Popen(
        ["kubectl", "-n", NAMESPACE, "port-forward", f"svc/{service}", f"{local_port}:{remote_port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port(local_port)
    except TimeoutError:
        proc.terminate()
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"port-forward to {service} failed to come up: {stderr}")
    return proc


@pytest.fixture(scope="session", autouse=True)
def reset_database_before_suite():
    """Reset seeded product quantities before the integration suite runs,
    so repeated runs don't silently deplete finite seeded stock and start
    failing for the wrong reason (exhaustion, not a real regression) --
    see the Phase 1 adversarial review finding this fixes."""
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "exec", "deployment/database", "--",
         "python3", "-c",
         "import urllib.request; "
         "req = urllib.request.Request('http://localhost:8000/admin/reset', method='POST'); "
         "urllib.request.urlopen(req, timeout=5)"],
        check=True,
    )
    yield


@pytest.fixture(scope="module")
def gateway_url():
    proc = _port_forward("api-gateway", 18080)
    try:
        yield "http://127.0.0.1:18080"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def database_url():
    proc = _port_forward("database", 18081)
    try:
        yield "http://127.0.0.1:18081"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def payasam_backend_url():
    proc = _port_forward("payasam-backend", 18083)
    try:
        yield "http://127.0.0.1:18083"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def restore_payment_service():
    """Safety net: whatever a test does to payment-service's replica
    count, guarantee it's back to 1 and Ready before the test session
    continues, so the cluster is never left broken."""
    yield
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "scale", "deployment/payment-service", "--replicas=1"],
        check=True,
    )
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "rollout", "status", "deployment/payment-service", "--timeout=60s"],
        check=True,
    )
