"""Pure business-impact calculation logic (PRD Sec 18-19), kept separate
from main.py's HTTP/OpenObserve-querying code so the formulas are
unit-testable without a live cluster or OpenObserve credentials.

Every number this module returns is either directly observed (a failed
transaction count, a first/last failure timestamp) or a deterministic
function of that observation plus an explicit business assumption from
business_metadata.yaml -- never a hardcoded headline number (PRD Sec 45).
"""

from __future__ import annotations

from pathlib import Path

import yaml

# order-service event_types that represent a genuine dependency/infra
# failure of the transaction. Deliberately excludes
# "insufficient_inventory", which is a business/stock condition, not a
# technical incident -- the same distinction
# simulator/traffic/generator.py already draws between infra_error_rate
# and insufficient_inventory_count.
FAILURE_EVENT_TYPES = frozenset({
    "inventory_unavailable",
    "inventory_error",
    "payment_unavailable",
    "payment_declined",
    "order_persist_failed",
})

_METADATA_PATH = Path(__file__).parent / "business_metadata.yaml"


def load_business_metadata(path: Path | None = None) -> dict:
    with open(path or _METADATA_PATH) as f:
        return yaml.safe_load(f)


def incident_duration_minutes(first_ts_us: int | None, last_ts_us: int | None) -> float:
    """Duration between the first and last observed failure event, in
    minutes. Zero (not fabricated) when there are fewer than two data
    points to measure a span from."""
    if first_ts_us is None or last_ts_us is None or last_ts_us <= first_ts_us:
        return 0.0
    return (last_ts_us - first_ts_us) / 1_000_000 / 60


def distinct_affected_users(hits: list[dict], failed_transactions: int) -> tuple[int | None, str | None]:
    """How many distinct real users had a failed transaction, computed
    from `user_id` on the same failure-event log records
    compute_impact's other numbers already come from -- never a guess
    (e.g. "assume 1 user per failed transaction"), and never silently
    wrong when the data isn't there to support an answer.

    Returns (affected_users, note). `note` is only non-None when
    affected_users could not be honestly computed -- e.g. older log
    records from before user_id was added to the shared logging
    whitelist, or a pod that hasn't rolled out that change yet.
    """
    if failed_transactions == 0:
        return 0, None

    user_ids = {h["user_id"] for h in hits if h.get("user_id")}
    if user_ids:
        return len(user_ids), None

    return None, "not computed: user_id missing on these log records (older data, or a pod predating this field)"


def compute_impact(
    failed_transactions: int,
    first_ts_us: int | None,
    last_ts_us: int | None,
    assumptions: dict,
) -> dict:
    """Given an actual observed failure count/time span and a business
    function's configured assumptions, compute the PRD Sec 19 formulas:

        revenue_at_risk = failed_transactions x avg_transaction_value
        downtime_cost = incident_duration_minutes x downtime_cost_per_minute
    """
    # Rounded BEFORE being used in the downtime-cost multiplication, not
    # after -- so the duration and cost this function returns visibly
    # multiply out to each other for anyone checking the math by eye
    # (exactly what this dashboard exists to make possible). Rounding
    # after multiplying would be marginally more numerically precise but
    # would make the two displayed numbers look like they don't agree
    # with the formula they're supposed to demonstrate -- a real
    # discrepancy caught by a user auditing the UI's own numbers.
    duration = round(incident_duration_minutes(first_ts_us, last_ts_us), 4)
    avg_value = assumptions["avg_transaction_value_inr"]
    cost_per_minute = assumptions["downtime_cost_per_minute_inr"]
    return {
        "incident_duration_minutes": duration,
        "estimated_revenue_at_risk_inr": failed_transactions * avg_value,
        "estimated_downtime_cost_inr": round(duration * cost_per_minute, 2),
    }
