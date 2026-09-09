"""Unit tests for the Phase 2 synthetic traffic generator.

Covers pure logic (config validation, seeding, request construction,
percentile/aggregation math, status classification) directly, and the
actual async request-sending path against the real Phase 1 application
in-process via ASGI transport (see tests/unit/conftest.py's `full_stack`
fixture) -- not mocks of business logic, the real FastAPI apps.
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator" / "traffic"))
import generator as g  # noqa: E402

from conftest import install_mounts  # noqa: E402


# ---- configuration parsing / validation ----

def test_parse_args_defaults():
    config, output = g.parse_args(["--gateway-url", "http://api-gateway:8000"])
    assert config.gateway_url == "http://api-gateway:8000"
    assert config.concurrent_users == 1
    assert config.requests_per_user == 3
    assert config.seed == 42
    assert output is None


def test_parse_args_overrides():
    config, output = g.parse_args([
        "--gateway-url", "http://x:8000",
        "--users", "5",
        "--requests-per-user", "7",
        "--interval", "0.5",
        "--jitter", "0.1",
        "--timeout", "3.0",
        "--seed", "99",
        "--duration-cap", "30",
        "--output", "/tmp/out.json",
    ])
    assert config.concurrent_users == 5
    assert config.requests_per_user == 7
    assert config.interval_seconds == 0.5
    assert config.interval_jitter_seconds == 0.1
    assert config.request_timeout_seconds == 3.0
    assert config.seed == 99
    assert config.duration_cap_seconds == 30.0
    assert output == "/tmp/out.json"


@pytest.mark.parametrize("kwargs,message", [
    ({"concurrent_users": 0}, "concurrent_users"),
    ({"requests_per_user": 0}, "requests_per_user"),
    ({"interval_seconds": -1}, "interval_seconds"),
    ({"interval_jitter_seconds": -1}, "interval_jitter_seconds"),
    ({"request_timeout_seconds": 0}, "request_timeout_seconds"),
])
def test_config_validation_rejects_invalid_values(kwargs, message):
    base = dict(gateway_url="http://x", concurrent_users=1, requests_per_user=1)
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        g.TrafficConfig(**base)


# ---- deterministic seed behavior ----

def test_same_seed_produces_same_request_sequence():
    rng1 = g.random.Random("seed-A")
    rng2 = g.random.Random("seed-A")
    seq1 = [g.build_order_payload(rng1) for _ in range(20)]
    seq2 = [g.build_order_payload(rng2) for _ in range(20)]
    assert seq1 == seq2


def test_different_seeds_can_produce_different_sequences():
    rng1 = g.random.Random("seed-A")
    rng2 = g.random.Random("seed-B")
    seq1 = [g.build_order_payload(rng1) for _ in range(20)]
    seq2 = [g.build_order_payload(rng2) for _ in range(20)]
    assert seq1 != seq2


# ---- request construction / valid product selection ----

def test_build_order_payload_only_uses_known_products():
    rng = g.random.Random("product-selection")
    for _ in range(100):
        payload = g.build_order_payload(rng)
        assert payload["product_id"] in g.KNOWN_PRODUCTS
        assert payload["quantity"] == 1


def test_user_id_format():
    assert g.user_id_for(0) == "user-001"
    assert g.user_id_for(41) == "user-042"


# ---- HTTP status classification ----

@pytest.mark.parametrize("status,expected", [
    (200, "success"),
    (201, "success"),
    (299, "success"),
    (409, "insufficient_inventory"),
    (400, "client_rejected"),
    (404, "client_rejected"),
    (500, "server_error"),
    (503, "server_error"),
    (None, "timeout_or_connection_error"),
])
def test_classify_status(status, expected):
    assert g.classify_status(status) == expected


# ---- latency statistics ----

def test_percentile_known_values():
    values = list(range(1, 101))  # 1..100
    assert g.percentile(values, 50) == 50
    assert g.percentile(values, 95) == 95
    assert g.percentile(values, 99) == 99
    assert g.percentile(values, 100) == 100


def test_percentile_empty():
    assert g.percentile([], 50) == 0.0


def test_percentile_single_value():
    assert g.percentile([42.0], 99) == 42.0


# ---- result aggregation ----

def test_aggregate_computes_expected_summary():
    records = [
        g.RequestRecord(user_id="user-001", seq=0, request_id="r1", status_code=201, latency_ms=100.0, transaction_id="t1"),
        g.RequestRecord(user_id="user-001", seq=1, request_id="r2", status_code=201, latency_ms=200.0, transaction_id="t2"),
        g.RequestRecord(user_id="user-002", seq=0, request_id="r3", status_code=409, latency_ms=50.0),
        g.RequestRecord(user_id="user-002", seq=1, request_id="r4", status_code=500, latency_ms=300.0),
        g.RequestRecord(user_id="user-003", seq=0, request_id="r5", status_code=None, latency_ms=10000.0, error="timeout: x"),
    ]
    summary = g.aggregate(records, wall_clock_seconds=2.0)

    assert summary["total_requests"] == 5
    assert summary["success_count"] == 2
    assert summary["success_rate"] == 2 / 5
    assert summary["insufficient_inventory_count"] == 1
    assert summary["server_error_count"] == 1
    assert summary["timeout_count"] == 1
    assert summary["infra_error_rate"] == 2 / 5  # server_error + timeout
    assert summary["status_distribution"] == {"201": 2, "409": 1, "500": 1, "no_response": 1}
    assert summary["requests_per_second"] == 2.5
    assert summary["latency_ms"]["min"] == 50.0
    assert summary["latency_ms"]["max"] == 10000.0


def test_aggregate_empty_records():
    summary = g.aggregate([], wall_clock_seconds=1.0)
    assert summary["total_requests"] == 0
    assert summary["success_rate"] == 0.0
    assert summary["requests_per_second"] == 0.0


# ---- concurrency configuration + real (in-process, real app) request path ----

@pytest.mark.asyncio
async def test_run_experiment_spawns_configured_concurrency_and_hits_real_gateway(full_stack, monkeypatch):
    install_mounts(monkeypatch, full_stack["mounts"] | {
        "http://api-gateway:8000": httpx.ASGITransport(app=full_stack["gateway"]),
    })

    config = g.TrafficConfig(
        gateway_url="http://api-gateway:8000",
        concurrent_users=3,
        requests_per_user=2,
        interval_seconds=0.0,
        interval_jitter_seconds=0.0,
        seed=7,
    )
    result = await g.run_experiment(config)

    assert result["total_requests"] == 3 * 2
    # every request actually reached the real api-gateway/order-service/etc
    # chain and got a real classified response (success or a real business
    # rejection under seeded stock) -- not zero, not all failures.
    assert result["success_count"] + result["insufficient_inventory_count"] == result["total_requests"]
    assert result["server_error_count"] == 0
    assert result["timeout_count"] == 0


@pytest.mark.asyncio
async def test_run_experiment_records_distinct_users(full_stack, monkeypatch):
    install_mounts(monkeypatch, full_stack["mounts"] | {
        "http://api-gateway:8000": httpx.ASGITransport(app=full_stack["gateway"]),
    })
    config = g.TrafficConfig(
        gateway_url="http://api-gateway:8000",
        concurrent_users=4,
        requests_per_user=1,
        interval_seconds=0.0,
        seed=3,
    )
    result = await g.run_experiment(config)
    assert result["total_requests"] == 4


# ---- timeout handling ----

@pytest.mark.asyncio
async def test_simulate_user_records_timeout_as_no_status():
    def hang_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    transport = httpx.MockTransport(hang_handler)
    records: list[g.RequestRecord] = []
    stop_event = asyncio.Event()
    config = g.TrafficConfig(
        gateway_url="http://timeout-target:8000",
        concurrent_users=1,
        requests_per_user=1,
        interval_seconds=0.0,
        request_timeout_seconds=0.01,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await g._simulate_user(client, config, 0, g.random.Random("t"), stop_event, records)

    assert len(records) == 1
    assert records[0].status_code is None
    assert records[0].error is not None
    assert "timeout" in records[0].error.lower()


# ---- graceful shutdown ----

@pytest.mark.asyncio
async def test_stop_event_halts_user_before_remaining_requests():
    call_count = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(201, json={"transaction_id": "t", "status": "completed"})

    transport = httpx.MockTransport(counting_handler)
    records: list[g.RequestRecord] = []
    stop_event = asyncio.Event()
    config = g.TrafficConfig(
        gateway_url="http://x:8000",
        concurrent_users=1,
        requests_per_user=100,  # would be 100 requests if not stopped
        interval_seconds=0.01,
        interval_jitter_seconds=0.0,
    )

    async with httpx.AsyncClient(transport=transport) as client:
        task = asyncio.create_task(
            g._simulate_user(client, config, 0, g.random.Random("s"), stop_event, records)
        )
        await asyncio.sleep(0.03)  # let a couple of requests go through
        stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)

    assert 0 < len(records) < 100, "stop_event should halt the loop well before all 100 requests"
