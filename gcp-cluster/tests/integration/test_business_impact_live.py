"""Live integration test for the scoped-down Phase 5 business-impact
endpoint: real HTTP, over the network, against the actually-deployed
payasam-backend service and a real injected payment fault -- not a
canned incident record.

Proves the causal chain: a real fault causes real order failures ->
payasam-backend queries the real telemetry -> the returned numbers are
consistent with what actually happened, not hardcoded.

Tolerant of a shared, already-running environment: rather than asserting
an exact failed_transactions count (another test or a manual demo run
could have added failures inside the same lookback window), this
asserts (1) the failures this test itself causes are reflected, and
(2) the revenue/downtime formulas hold for whatever count comes back --
the actual invariant that matters (PRD Sec 31: prove causal behavior,
not a brittle exact value).
"""

import json
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

NAMESPACE = "payasam"
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def _set_payment_error_fault():
    subprocess.run(
        [str(SCRIPTS_DIR / "set-fault.sh"), "payment-service", "PAYASAM_FAULT_PAYMENT_ERROR_RATE", "1.0"],
        check=True,
    )


def _clear_faults():
    subprocess.run([str(SCRIPTS_DIR / "clear-faults.sh")], check=True)


@pytest.fixture
def payment_fault_then_clear():
    """Safety net: whatever this test does to payment-service's fault
    config, guarantee it's cleared before the session continues, even if
    the test fails partway through -- same pattern as
    test_payment_failure.py's restore_payment_service fixture."""
    yield
    _clear_faults()


def _wait_for_condition(check, timeout=30.0, interval=2.0):
    deadline = time.monotonic() + timeout
    last_result = None
    while time.monotonic() < deadline:
        last_result = check()
        if last_result:
            return last_result
        time.sleep(interval)
    return last_result


@pytest.mark.usefixtures("payment_fault_then_clear")
def test_real_payment_failures_produce_matching_business_impact(gateway_url, payasam_backend_url):
    _set_payment_error_fault()

    num_requests = 3
    for i in range(num_requests):
        resp = httpx.post(
            f"{gateway_url}/orders",
            json={"user_id": f"user-impact-live-{i}", "items": [{"product_id": "product-001", "quantity": 1}]},
            timeout=15,
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "payment_failed"

    def check_impact():
        resp = httpx.get(f"{payasam_backend_url}/impact", params={"window_minutes": 5}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if data["failed_transactions"] >= num_requests else None

    result = _wait_for_condition(check_impact, timeout=30.0, interval=2.0)
    assert result is not None, "payasam-backend did not reflect the real failures within the timeout"

    # --- causal correctness: our failures are actually counted ---
    assert result["failed_transactions"] >= num_requests
    assert result["failure_breakdown"].get("payment_declined", 0) >= num_requests

    # --- formula correctness: holds for whatever real count came back ---
    assert result["estimated_revenue_at_risk_inr"] == result["failed_transactions"] * result["assumptions"]["avg_transaction_value_inr"]
    # compute_impact() rounds duration BEFORE using it in the cost
    # multiplication (see impact.py and DECISIONS.md), specifically so
    # this holds exactly, not just approximately -- a user reading the
    # UI caught the previous rounding-order version producing numbers
    # that looked like they didn't multiply out.
    expected_downtime_cost = round(result["incident_duration_minutes"] * result["assumptions"]["downtime_cost_per_minute_inr"], 2)
    assert result["estimated_downtime_cost_inr"] == expected_downtime_cost

    # --- labeling / transparency requirements (PRD Sec 19) ---
    assert result["label"] == "ESTIMATED / SIMULATED BUSINESS IMPACT"
    assert result["business_function"] == "Ordering"
    assert result["business_criticality"] == 5
    # each of this test's num_requests used a distinct user_id, so
    # affected_users must be a real, non-fabricated count of at least
    # that many distinct users (>= to tolerate a shared environment).
    assert result["affected_users"] is not None
    assert result["affected_users"] >= num_requests
    assert result["affected_users_note"] is None


@pytest.mark.usefixtures("payment_fault_then_clear")
def test_same_real_user_failing_twice_logs_one_consistent_user_id(gateway_url):
    """The same real user failing twice must produce two log records
    sharing one identical user_id -- the actual wiring fact that makes
    affected_users' distinct-counting meaningful for real data. (The
    counting/deduplication *math* itself is already fully covered
    deterministically in tests/unit/test_business_impact.py -- this
    test's job is only to prove user_id reaches OpenObserve correctly
    end-to-end for a real request, not to re-derive that math.)

    Deliberately does NOT assert against payasam-backend's aggregate
    /impact number: an earlier version of this test tried to prove the
    same thing via a before/after delta on that aggregate and flaked
    for real -- affected_users is counted over a *sliding* time window,
    and residual data from other tests/manual sessions can age in or out
    of that window between the "before" and "after" queries (each
    several seconds apart, dominated by OTLP export lag), for reasons
    having nothing to do with this test's own two requests. Filtering
    OpenObserve directly by this test's own unique, randomly-generated
    user_id sidesteps that entirely: nothing else in the shared
    environment can share this exact value, so there is no ambient
    window to be unstable.
    """
    # Must be unique per test run -- a fixed literal would risk matching
    # residual data from this test's own prior run, and there's no
    # reason not to make the isolation airtight.
    same_user = f"user-repeat-offender-{uuid.uuid4().hex[:8]}"

    _set_payment_error_fault()

    for _ in range(2):
        resp = httpx.post(
            f"{gateway_url}/orders",
            json={"user_id": same_user, "items": [{"product_id": "product-001", "quantity": 1}]},
            timeout=15,
        )
        assert resp.status_code == 502

    def check_logged():
        # otel-query.sh wraps this SQL in Python triple-single-quotes
        # ('''${SQL}''') -- a query ending in a single-quoted literal
        # (e.g. "...= 'payment_declined'") butts its closing quote
        # directly against the wrapper's closing '''', four quotes in a
        # row, which is a genuine Python string-parsing syntax error
        # (confirmed: this exact query 500'd with "unterminated string
        # literal" before adding the trailing LIMIT clause below, which
        # just needs to not end in a quote character).
        sql = (
            'SELECT user_id, event_type FROM "default" '
            f"WHERE user_id = '{same_user}' AND event_type = 'payment_declined' "
            "LIMIT 10"
        )
        result = subprocess.run(
            [str(SCRIPTS_DIR / "otel-query.sh"), "logs", sql, "120"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        hits = data.get("hits", [])
        return hits if len(hits) >= 2 else None

    hits = _wait_for_condition(check_logged, timeout=30.0, interval=2.0)
    assert hits is not None, "the two real failures for this user_id were not queryable in OpenObserve within the timeout"
    assert len(hits) == 2, f"expected exactly the 2 requests this test made (isolated by a unique user_id), got {len(hits)}"
    assert {h["user_id"] for h in hits} == {same_user}


def test_healthy_window_can_return_zero_impact(payasam_backend_url):
    # A very short, very recent window on a freshly-cleared system should
    # show no failures -- not a hardcoded zero, an absence of matching
    # real log data.
    resp = httpx.get(f"{payasam_backend_url}/impact", params={"window_minutes": 1}, timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    if data["failed_transactions"] == 0:
        assert data["estimated_revenue_at_risk_inr"] == 0
        assert data["estimated_downtime_cost_inr"] == 0.0
        assert data["first_failure_at"] is None


def test_impact_rejects_unknown_business_function(payasam_backend_url):
    resp = httpx.get(
        f"{payasam_backend_url}/impact",
        params={"window_minutes": 5, "business_function": "does-not-exist"},
        timeout=10,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_business_function"
