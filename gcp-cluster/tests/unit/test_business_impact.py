"""Unit tests for the scoped-down Phase 5 business-impact calculation
logic (payasam/backend/impact.py). Pure functions -- no cluster, no
OpenObserve, no FastAPI app needed; the HTTP-facing part
(payasam/backend/main.py) is exercised instead by
tests/integration/test_business_impact_live.py against the real
deployed service.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "payasam" / "backend"))
import impact as bi  # noqa: E402


# ---- distinct_affected_users ----

def test_affected_users_is_zero_with_no_failures():
    users, note = bi.distinct_affected_users([], failed_transactions=0)
    assert users == 0
    assert note is None


def test_affected_users_counts_distinct_user_ids_among_failures():
    hits = [
        {"event_type": "payment_declined", "user_id": "user-001"},
        {"event_type": "payment_declined", "user_id": "user-002"},
        {"event_type": "payment_declined", "user_id": "user-001"},  # same user failed twice
    ]
    users, note = bi.distinct_affected_users(hits, failed_transactions=3)
    assert users == 2  # not 3 -- one user had two failed transactions
    assert note is None


def test_affected_users_none_with_explanatory_note_when_user_id_missing():
    """The honesty case: failures happened (failed_transactions > 0) but
    none of the matching log records carry a user_id -- must not report
    0 (which would misleadingly look like 'zero users affected during a
    real incident') and must not guess -- report None with why."""
    hits = [{"event_type": "payment_declined"}, {"event_type": "payment_declined"}]
    users, note = bi.distinct_affected_users(hits, failed_transactions=2)
    assert users is None
    assert note is not None and "user_id" in note


def test_affected_users_ignores_hits_with_no_user_id_but_still_counts_the_rest():
    hits = [
        {"event_type": "payment_declined", "user_id": "user-001"},
        {"event_type": "payment_declined"},  # missing user_id -- a partial rollout, say
    ]
    users, note = bi.distinct_affected_users(hits, failed_transactions=2)
    assert users == 1
    assert note is None


# ---- incident_duration_minutes ----

def test_duration_zero_with_no_events():
    assert bi.incident_duration_minutes(None, None) == 0.0


def test_duration_zero_with_a_single_event():
    assert bi.incident_duration_minutes(1_000_000, None) == 0.0
    assert bi.incident_duration_minutes(None, 1_000_000) == 0.0


def test_duration_zero_when_last_not_after_first():
    assert bi.incident_duration_minutes(2_000_000, 2_000_000) == 0.0
    assert bi.incident_duration_minutes(2_000_000, 1_000_000) == 0.0


def test_duration_computed_from_real_span():
    # 90 seconds apart, in microseconds
    first = 1_000_000_000
    last = first + 90 * 1_000_000
    assert bi.incident_duration_minutes(first, last) == 1.5


# ---- compute_impact ----

ASSUMPTIONS = {
    "business_function": "Ordering",
    "criticality": 5,
    "avg_transaction_value_inr": 500,
    "transactions_per_minute": 8,
    "downtime_cost_per_minute_inr": 800,
}


def test_compute_impact_zero_failures_is_zero_impact():
    result = bi.compute_impact(0, None, None, ASSUMPTIONS)
    assert result["incident_duration_minutes"] == 0.0
    assert result["estimated_revenue_at_risk_inr"] == 0
    assert result["estimated_downtime_cost_inr"] == 0.0


def test_compute_impact_revenue_is_failed_transactions_times_avg_value():
    result = bi.compute_impact(3, None, None, ASSUMPTIONS)
    assert result["estimated_revenue_at_risk_inr"] == 3 * 500


def test_compute_impact_downtime_cost_is_duration_times_rate():
    first = 0
    last = 2 * 60 * 1_000_000  # exactly 2 minutes later
    result = bi.compute_impact(5, first, last, ASSUMPTIONS)
    assert result["incident_duration_minutes"] == 2.0
    assert result["estimated_downtime_cost_inr"] == 2.0 * 800


def test_downtime_cost_is_always_self_consistent_with_the_displayed_duration():
    """A user reading the UI caught this exact discrepancy: with a raw
    (unrounded) duration used for the cost calculation but a rounded
    duration shown on screen, `displayed_duration * rate` didn't equal
    `displayed_cost` by a rounding-order artifact (0.0324 * 800 = 25.92,
    but the shown cost was 25.93). compute_impact must round the
    duration BEFORE using it for the cost multiplication, so the two
    displayed numbers always multiply out exactly -- not just "close
    enough to explain away."""
    first = 1_000_000
    last = first + 1_945_678  # an arbitrary, not-round span in microseconds
    result = bi.compute_impact(1, first, last, ASSUMPTIONS)
    expected_cost = round(result["incident_duration_minutes"] * ASSUMPTIONS["downtime_cost_per_minute_inr"], 2)
    assert result["estimated_downtime_cost_inr"] == expected_cost


def test_compute_impact_single_failure_has_zero_duration_but_nonzero_revenue():
    # A real, important edge case: exactly one failure gives no time span
    # to measure a duration from, but must NOT suppress the revenue
    # figure, which only depends on the count.
    result = bi.compute_impact(1, 5_000_000, None, ASSUMPTIONS)
    assert result["incident_duration_minutes"] == 0.0
    assert result["estimated_revenue_at_risk_inr"] == 500
    assert result["estimated_downtime_cost_inr"] == 0.0


# ---- FAILURE_EVENT_TYPES ----

def test_failure_event_types_excludes_business_stock_condition():
    # insufficient_inventory is a business/stock condition, not a
    # technical incident -- must never be counted as a failed
    # transaction for business-impact purposes (see impact.py docstring
    # and DECISIONS.md D014).
    assert "insufficient_inventory" not in bi.FAILURE_EVENT_TYPES


def test_failure_event_types_includes_known_order_service_failure_paths():
    expected = {
        "inventory_unavailable",
        "inventory_error",
        "payment_unavailable",
        "payment_declined",
        "order_persist_failed",
    }
    assert bi.FAILURE_EVENT_TYPES == expected


# ---- load_business_metadata ----

def test_load_business_metadata_reads_real_config_file():
    metadata = bi.load_business_metadata()
    assert "order" in metadata
    assert "payment" in metadata
    assert metadata["order"]["business_function"] == "Ordering"
    assert metadata["order"]["avg_transaction_value_inr"] > 0
    assert metadata["order"]["downtime_cost_per_minute_inr"] > 0


def test_load_business_metadata_from_explicit_path(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "test_function:\n"
        "  business_function: Test\n"
        "  criticality: 1\n"
        "  avg_transaction_value_inr: 100\n"
        "  transactions_per_minute: 1\n"
        "  downtime_cost_per_minute_inr: 10\n"
    )
    metadata = bi.load_business_metadata(custom)
    assert metadata["test_function"]["avg_transaction_value_inr"] == 100
