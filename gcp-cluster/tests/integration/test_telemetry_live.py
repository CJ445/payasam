"""Live integration test: proves telemetry actually reaches OpenObserve --
not just that the endpoint responds (Phase 0 already proved that), but
that a real application request produces a real, queryable trace and
correlated logs in OpenObserve.

Requires real OpenObserve credentials in the environment
(OPENOBSERVE_USERNAME / OPENOBSERVE_PASSWORD), the same ones used to
create the openobserve-credentials Kubernetes Secret (see
scripts/create-otel-secret.sh) -- never hardcoded here. Skips cleanly
(does not fail) if they aren't set, so the rest of the suite stays fully
runnable without live OpenObserve credentials, per this phase's testing
instructions.
"""

import base64
import json
import os
import time

import httpx
import pytest

OPENOBSERVE_URL = os.environ.get("OPENOBSERVE_URL", "http://localhost:5080")
OPENOBSERVE_ORG = os.environ.get("OPENOBSERVE_ORG", "default")
OPENOBSERVE_USERNAME = os.environ.get("OPENOBSERVE_USERNAME")
OPENOBSERVE_PASSWORD = os.environ.get("OPENOBSERVE_PASSWORD")

pytestmark = pytest.mark.skipif(
    not (OPENOBSERVE_USERNAME and OPENOBSERVE_PASSWORD),
    reason="OPENOBSERVE_USERNAME/OPENOBSERVE_PASSWORD not set in the environment -- "
    "live OpenObserve verification skipped (see docs/phase3-telemetry.md)",
)


def _auth_headers() -> dict:
    token = base64.b64encode(f"{OPENOBSERVE_USERNAME}:{OPENOBSERVE_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _search(stream_type: str, sql: str, start_us: int, end_us: int) -> list[dict]:
    body = {"query": {"sql": sql, "start_time": start_us, "end_time": end_us}}
    resp = httpx.post(
        f"{OPENOBSERVE_URL}/api/{OPENOBSERVE_ORG}/_search",
        params={"type": stream_type},
        headers=_auth_headers(),
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("hits", [])


def _wait_for_condition(check, timeout=30.0, interval=1.0):
    deadline = time.monotonic() + timeout
    last_result = None
    while time.monotonic() < deadline:
        last_result = check()
        if last_result:
            return last_result
        time.sleep(interval)
    return last_result


def test_real_order_produces_queryable_trace_and_correlated_logs(gateway_url):
    resp = httpx.post(
        f"{gateway_url}/orders",
        json={"user_id": "user-telemetry-live-test", "items": [{"product_id": "product-003", "quantity": 1}]},
        timeout=15,
    )
    assert resp.status_code == 201
    body = resp.json()
    transaction_id = body["transaction_id"]

    start_us = int((time.time() - 30) * 1_000_000)

    def check_logs():
        end_us = int(time.time() * 1_000_000)
        sql = f"SELECT * FROM \"default\" WHERE transaction_id = '{transaction_id}' ORDER BY _timestamp DESC LIMIT 20"
        return _search("logs", sql, start_us, end_us)

    log_hits = _wait_for_condition(check_logs, timeout=30.0, interval=2.0)
    assert log_hits, f"no logs found in OpenObserve for transaction_id={transaction_id} within timeout"

    trace_ids = {h.get("trace_id") for h in log_hits if h.get("trace_id")}
    assert len(trace_ids) == 1, f"expected exactly one trace_id correlated to this transaction, got {trace_ids}"
    trace_id = trace_ids.pop()

    services_in_logs = {h.get("service_name") for h in log_hits}
    assert "payasam-order-service" in services_in_logs
    assert "payasam-database" in services_in_logs

    end_us = int(time.time() * 1_000_000)
    sql = f"SELECT service_name, operation_name, reference_parent_span_id FROM \"default\" WHERE trace_id = '{trace_id}' ORDER BY start_time ASC LIMIT 200"
    span_hits = _search("traces", sql, start_us, end_us)

    assert len(span_hits) > 10, f"expected a real multi-span distributed trace, got {len(span_hits)} spans"

    services_in_trace = {h.get("service_name") for h in span_hits}
    # the real cross-service chain this transaction should have touched
    assert {"payasam-api-gateway", "payasam-order-service", "payasam-database"}.issubset(services_in_trace)

    root_spans = [h for h in span_hits if not h.get("reference_parent_span_id")]
    assert len(root_spans) == 1, f"expected exactly one root span (one distributed trace, not several), got {len(root_spans)}"
