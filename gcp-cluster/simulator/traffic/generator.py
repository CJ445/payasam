"""Payasam synthetic traffic generator (Phase 2).

Generates real HTTP traffic against the API Gateway -- never against
internal services directly, never mocked, never fabricated telemetry.
Per DECISIONS.md D006: a small custom asyncio script using httpx, not
Locust (not installed, adds an unneeded UI/dependency footprint at this
traffic scale).

IMPORTANT -- no automatic retries (see Phase 1 adversarial review):
`order-service` has no idempotency mechanism, so a client-side retry of a
POST /orders that actually succeeded server-side but whose response was
lost would silently double-charge/double-reserve. This generator
therefore treats every request as fire-once: a failure (including a
timeout) is recorded and reported, never retried. If future traffic
behavior seems to need retries, that requires a deliberate idempotency
design decision first -- this module must not grow retry logic without
one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import signal
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field

import httpx

# Must match services/database/db.py's SEED_PRODUCTS product_ids. The
# generator only ever talks to api-gateway (POST /orders, GET /health), so
# it cannot discover the catalog dynamically -- api-gateway doesn't expose
# a products endpoint, by design (it's a thin proxy to order-service, not
# a general-purpose API). Weights are proportional to each product's
# seeded stock (10 / 5 / 20) so sustained traffic doesn't slam the
# scarcest item (product-002) disproportionately -- this spreads load
# realistically across the catalog, it does not eliminate inventory
# pressure (nor should it: exhaustion is a legitimate, expected business
# outcome to observe, not a bug to hide).
KNOWN_PRODUCTS = ["product-001", "product-002", "product-003"]
PRODUCT_WEIGHTS = [10, 5, 20]


@dataclass
class TrafficConfig:
    gateway_url: str
    concurrent_users: int
    requests_per_user: int
    interval_seconds: float = 1.0
    interval_jitter_seconds: float = 0.2
    request_timeout_seconds: float = 10.0
    seed: int = 42
    duration_cap_seconds: float | None = None

    def __post_init__(self):
        if self.concurrent_users < 1:
            raise ValueError("concurrent_users must be >= 1")
        if self.requests_per_user < 1:
            raise ValueError("requests_per_user must be >= 1")
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be >= 0")
        if self.interval_jitter_seconds < 0:
            raise ValueError("interval_jitter_seconds must be >= 0")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")


@dataclass
class RequestRecord:
    user_id: str
    seq: int
    request_id: str
    status_code: int | None  # None => no response at all (timeout/connection error)
    latency_ms: float
    transaction_id: str | None = None
    error: str | None = None


def user_id_for(index: int) -> str:
    return f"user-{index + 1:03d}"


def build_order_payload(rng: random.Random) -> dict:
    """Deterministic (given a seeded rng) but varied request body: a
    weighted-random product, quantity 1 (keeps requests small relative to
    finite seeded stock so more of them can be served per experiment)."""
    product_id = rng.choices(KNOWN_PRODUCTS, weights=PRODUCT_WEIGHTS, k=1)[0]
    return {"product_id": product_id, "quantity": 1}


def classify_status(status_code: int | None) -> str:
    if status_code is None:
        return "timeout_or_connection_error"
    if 200 <= status_code < 300:
        return "success"
    if status_code == 409:
        return "insufficient_inventory"
    if 400 <= status_code < 500:
        return "client_rejected"
    if 500 <= status_code < 600:
        return "server_error"
    return "other"


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list. pct in [0,100]."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = max(0, min(len(sorted_values) - 1, int((pct / 100.0) * len(sorted_values) + 0.5) - 1))
    return sorted_values[k]


async def _simulate_user(
    client: httpx.AsyncClient,
    config: TrafficConfig,
    user_index: int,
    rng: random.Random,
    stop_event: asyncio.Event,
    records: list[RequestRecord],
) -> None:
    user_id = user_id_for(user_index)
    for seq in range(config.requests_per_user):
        if stop_event.is_set():
            break

        request_id = f"req-gen-{user_id}-{seq}-{uuid.uuid4().hex[:8]}"
        body = build_order_payload(rng)
        payload = {"user_id": user_id, "items": [body]}

        start = time.monotonic()
        status_code: int | None = None
        transaction_id: str | None = None
        error: str | None = None
        try:
            resp = await client.post(
                f"{config.gateway_url}/orders",
                json=payload,
                headers={"X-Request-Id": request_id},
                timeout=config.request_timeout_seconds,
            )
            status_code = resp.status_code
            try:
                body_json = resp.json()
                transaction_id = body_json.get("transaction_id")
            except ValueError:
                pass
        except httpx.TimeoutException as exc:
            error = f"timeout: {exc}"
        except httpx.HTTPError as exc:
            error = f"http_error: {exc}"

        latency_ms = (time.monotonic() - start) * 1000.0
        records.append(
            RequestRecord(
                user_id=user_id,
                seq=seq,
                request_id=request_id,
                status_code=status_code,
                latency_ms=latency_ms,
                transaction_id=transaction_id,
                error=error,
            )
        )

        if stop_event.is_set():
            break
        if seq < config.requests_per_user - 1:
            jitter = rng.uniform(-config.interval_jitter_seconds, config.interval_jitter_seconds)
            wait = max(0.0, config.interval_seconds + jitter)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass  # normal case: interval elapsed without a stop signal


def aggregate(records: list[RequestRecord], wall_clock_seconds: float) -> dict:
    total = len(records)
    by_category: dict[str, int] = {}
    status_distribution: dict[str, int] = {}
    latencies = []

    for r in records:
        category = classify_status(r.status_code)
        by_category[category] = by_category.get(category, 0) + 1
        key = str(r.status_code) if r.status_code is not None else "no_response"
        status_distribution[key] = status_distribution.get(key, 0) + 1
        latencies.append(r.latency_ms)

    latencies_sorted = sorted(latencies)
    success_count = by_category.get("success", 0)
    timeout_count = by_category.get("timeout_or_connection_error", 0)
    server_error_count = by_category.get("server_error", 0)

    return {
        "total_requests": total,
        "success_count": success_count,
        "success_rate": (success_count / total) if total else 0.0,
        "insufficient_inventory_count": by_category.get("insufficient_inventory", 0),
        "client_rejected_count": by_category.get("client_rejected", 0),
        "server_error_count": server_error_count,
        "timeout_count": timeout_count,
        # The metric that actually indicates system-level degradation,
        # as distinct from ordinary business-level stock scarcity (409s).
        "infra_error_rate": ((server_error_count + timeout_count) / total) if total else 0.0,
        "status_distribution": status_distribution,
        "latency_ms": {
            "avg": statistics.fmean(latencies) if latencies else 0.0,
            "p50": percentile(latencies_sorted, 50),
            "p95": percentile(latencies_sorted, 95),
            "p99": percentile(latencies_sorted, 99),
            "max": max(latencies) if latencies else 0.0,
            "min": min(latencies) if latencies else 0.0,
        },
        "wall_clock_seconds": wall_clock_seconds,
        "requests_per_second": (total / wall_clock_seconds) if wall_clock_seconds > 0 else 0.0,
    }


async def run_experiment(config: TrafficConfig) -> dict:
    records: list[RequestRecord] = []
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    handlers_installed = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            handlers_installed.append(sig)
        except (NotImplementedError, RuntimeError):
            pass  # e.g. not running in the main thread / unsupported platform

    limits = httpx.Limits(max_connections=config.concurrent_users, max_keepalive_connections=config.concurrent_users)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(limits=limits) as client:
            user_tasks = []
            for user_index in range(config.concurrent_users):
                rng = random.Random(f"{config.seed}-{user_index}")
                user_tasks.append(
                    _simulate_user(client, config, user_index, rng, stop_event, records)
                )

            if config.duration_cap_seconds is not None:
                async def _duration_watchdog():
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=config.duration_cap_seconds)
                    except asyncio.TimeoutError:
                        stop_event.set()

                await asyncio.gather(*user_tasks, _duration_watchdog())
            else:
                await asyncio.gather(*user_tasks)
    finally:
        for sig in handlers_installed:
            loop.remove_signal_handler(sig)

    wall_clock = time.monotonic() - start
    result = aggregate(records, wall_clock)
    result["config"] = {
        "gateway_url": config.gateway_url,
        "concurrent_users": config.concurrent_users,
        "requests_per_user": config.requests_per_user,
        "interval_seconds": config.interval_seconds,
        "interval_jitter_seconds": config.interval_jitter_seconds,
        "request_timeout_seconds": config.request_timeout_seconds,
        "seed": config.seed,
        "duration_cap_seconds": config.duration_cap_seconds,
    }
    return result


def parse_args(argv: list[str] | None = None) -> TrafficConfig:
    parser = argparse.ArgumentParser(description="Payasam synthetic traffic generator")
    parser.add_argument("--gateway-url", default="http://api-gateway:8000")
    parser.add_argument("--users", type=int, default=1, dest="concurrent_users")
    parser.add_argument("--requests-per-user", type=int, default=3)
    parser.add_argument("--interval", type=float, default=1.0, dest="interval_seconds")
    parser.add_argument("--jitter", type=float, default=0.2, dest="interval_jitter_seconds")
    parser.add_argument("--timeout", type=float, default=10.0, dest="request_timeout_seconds")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration-cap", type=float, default=None, dest="duration_cap_seconds")
    parser.add_argument("--output", type=str, default=None, help="optional file path to also write JSON results to")
    args = parser.parse_args(argv)

    config = TrafficConfig(
        gateway_url=args.gateway_url,
        concurrent_users=args.concurrent_users,
        requests_per_user=args.requests_per_user,
        interval_seconds=args.interval_seconds,
        interval_jitter_seconds=args.interval_jitter_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
        seed=args.seed,
        duration_cap_seconds=args.duration_cap_seconds,
    )
    return config, args.output


def main(argv: list[str] | None = None) -> int:
    config, output_path = parse_args(argv)
    result = asyncio.run(run_experiment(config))
    text = json.dumps(result, indent=2)
    print(text)
    if output_path:
        with open(output_path, "w") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
