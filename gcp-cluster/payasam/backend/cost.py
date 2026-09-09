"""Modeled GCP cost calculation -- pure functions, kept separate from
main.py's HTTP/Kubernetes-querying code, same reason impact.py is
separate: the formulas are unit-testable without a live cluster.

This is deliberately NOT a real billing integration. Every number here
is a live Kubernetes fact (replica count, container resource requests)
multiplied through a static public GCP price list -- never a fabricated
usage estimate. There is no CPU/memory *usage* telemetry in this project
(see /status, which only reports up/down health), so this module does
not attempt right-sizing recommendations based on usage -- only the
honestly-supportable "replicas above baseline" signal.

Pricing model: GCP Autopilot's container-optimized compute platform.
Chosen specifically because Autopilot bills per-Pod resource *request*
directly (unlike GKE Standard, which bills per-Node and requires
node-packing assumptions this project has no data to make honestly) --
so `replicas x requests x rate` is a real GCP billing formula, not an
approximation of one.
"""

from __future__ import annotations

# Snapshot of GCP Autopilot container-optimized compute platform public
# pricing, us-central1. Source: Google Cloud published pricing,
# researched 2026-09-09. Pricing changes over time -- this is a labeled
# snapshot, not a live feed (see DECISIONS.md if a live Cloud Billing
# Catalog API integration is ever added as a follow-up).
PRICING_SNAPSHOT = {
    "provider": "google_cloud",
    "product": "GKE Autopilot (container-optimized compute platform)",
    "region": "us-central1",
    "snapshot_date": "2026-09-09",
    "currency": "USD",
    "vcpu_hour": 0.0445,
    "memory_gib_hour": 0.0049225,
}

HOURS_PER_DAY = 24
HOURS_PER_MONTH = 730  # GCP's own standard monthly-hours convention


def compute_service_cost(
    replicas: int,
    cpu_request_cores: float,
    memory_request_gib: float,
    pricing: dict = PRICING_SNAPSHOT,
) -> dict:
    """Modeled cost for one service, given its live replica count and
    per-pod resource requests. Returns hourly/daily/monthly figures plus
    the compute/memory breakdown so the UI can show its work."""
    compute_hourly = replicas * cpu_request_cores * pricing["vcpu_hour"]
    memory_hourly = replicas * memory_request_gib * pricing["memory_gib_hour"]
    hourly = compute_hourly + memory_hourly
    return {
        "replicas": replicas,
        "cpu_request_cores": cpu_request_cores,
        "memory_request_gib": memory_request_gib,
        "compute_hourly_usd": round(compute_hourly, 6),
        "memory_hourly_usd": round(memory_hourly, 6),
        "hourly_usd": round(hourly, 6),
        "daily_usd": round(hourly * HOURS_PER_DAY, 4),
        "monthly_usd": round(hourly * HOURS_PER_MONTH, 2),
    }


def optimization_opportunity(
    current_replicas: int,
    baseline_replicas: int,
    cpu_request_cores: float,
    memory_request_gib: float,
    pricing: dict = PRICING_SNAPSHOT,
) -> dict:
    """Dollar delta between the current modeled cost and the cost at a
    service's healthy baseline replica count. Positive delta_hourly_usd
    means the service is currently costing more than baseline (e.g.
    scaled up in response to a fault) -- this is what powers the
    "incident cost impact" and "replicas above baseline" story. Never
    negative-clamped: a service below baseline (e.g. scaled to 0 during
    an outage) legitimately shows a negative delta."""
    current = compute_service_cost(current_replicas, cpu_request_cores, memory_request_gib, pricing)
    baseline = compute_service_cost(baseline_replicas, cpu_request_cores, memory_request_gib, pricing)
    delta_hourly = current["hourly_usd"] - baseline["hourly_usd"]
    return {
        "current_replicas": current_replicas,
        "baseline_replicas": baseline_replicas,
        "current_hourly_usd": current["hourly_usd"],
        "baseline_hourly_usd": baseline["hourly_usd"],
        "delta_hourly_usd": round(delta_hourly, 6),
        "delta_monthly_usd": round(delta_hourly * HOURS_PER_MONTH, 2),
    }


def cluster_total(service_costs: list[dict]) -> dict:
    """Sums a list of compute_service_cost(...) results into a cluster-wide total."""
    hourly = sum(s["hourly_usd"] for s in service_costs)
    return {
        "hourly_usd": round(hourly, 6),
        "daily_usd": round(hourly * HOURS_PER_DAY, 4),
        "monthly_usd": round(hourly * HOURS_PER_MONTH, 2),
    }
