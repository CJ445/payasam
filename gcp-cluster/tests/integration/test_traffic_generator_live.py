"""Live integration test: the actual traffic generator, making real HTTP
requests over the network against the actually-deployed api-gateway (via
kubectl port-forward, same pattern as the rest of tests/integration).

Not an ASGI/in-process shortcut -- this is the one test that proves the
generator itself (not just the application) works end-to-end against a
real running cluster.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator" / "traffic"))
import generator as g  # noqa: E402

import asyncio


def test_generator_runs_real_small_experiment_against_live_gateway(gateway_url):
    config = g.TrafficConfig(
        gateway_url=gateway_url,
        concurrent_users=2,
        requests_per_user=2,
        interval_seconds=0.1,
        interval_jitter_seconds=0.05,
        seed=123,
    )
    result = asyncio.run(g.run_experiment(config))

    assert result["total_requests"] == 4
    # every request got a real response from the real deployed chain:
    # either a real success or a real (business-level) inventory
    # rejection -- never zero real traffic, never an infra failure on a
    # healthy cluster.
    assert result["success_count"] + result["insufficient_inventory_count"] == 4
    assert result["server_error_count"] == 0
    assert result["timeout_count"] == 0
    assert result["latency_ms"]["avg"] > 0
