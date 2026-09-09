# Phase 2 — Synthetic Traffic Generator & Baseline

This document covers the traffic generator itself and the actual,
measured baseline experiment results. See `IMPLEMENTATION_PLAN.md` for
how this fits into the overall phase plan.

## What the generator does

`simulator/traffic/generator.py` sends real HTTP `POST /orders` requests
against `api-gateway` — never against internal services directly, never
mocked. Per DECISIONS.md D006, it's a small custom `asyncio`/`httpx`
script rather than Locust (not installed, unneeded UI/dependency
footprint at this traffic scale).

Each simulated user (`user-001`, `user-002`, ...) runs as its own asyncio
coroutine, sending `requests_per_user` requests spaced by
`interval_seconds` (± jitter), choosing a product each time via a
seeded RNG weighted by that product's seeded stock (so sustained traffic
doesn't disproportionately slam the scarcest item). Every request carries
an `X-Request-Id` header the generator itself assigns; the
`transaction_id` `order-service` returns in its response is captured and
recorded per-request, so both correlation identifiers are preserved
end-to-end for every generated request.

**No automatic retries.** The Phase 1 adversarial review found `POST
/orders` has no idempotency mechanism — a retried request that actually
succeeded server-side but whose response was lost would silently
double-reserve/double-charge. This generator therefore treats every
request as fire-once: failures (including timeouts) are recorded and
reported, never retried. This was a deliberate deferral, not an oversight
— see "Idempotency" below.

## Running it

**Locally** (e.g. against a port-forwarded gateway, useful for
development):

```bash
kubectl -n payasam port-forward svc/api-gateway 18080:8000 &
python3 simulator/traffic/generator.py \
  --gateway-url http://127.0.0.1:18080 \
  --users 3 --requests-per-user 5 --interval 0.5 --jitter 0.1 --seed 42
```

**As a Kubernetes Job** (the way the baseline experiment below was
actually run) — reaches `api-gateway` via its stable in-cluster Service
DNS name, no port-forward needed:

```bash
scripts/run-baseline.sh <users> [requests_per_user] [interval] [jitter] [seed]
# e.g.
scripts/run-baseline.sh 10 5 0.5 0.1 42
```

`run-baseline.sh` resets the database to its seeded state first (via
Phase 1's existing `scripts/reset.sh` / `POST /admin/reset` — not a
second reset mechanism, not failure injection), renders
`infrastructure/kubernetes/jobs/traffic-generator-job.yaml.template` with
a unique Job name, applies it, waits for completion, saves the JSON
result to `docs/baseline-results/`, and deletes the Job
(`ttlSecondsAfterFinished` also cleans it up automatically as a backstop).

## Configuration options

| Flag | Meaning | Default |
|---|---|---|
| `--gateway-url` | target api-gateway base URL | `http://api-gateway:8000` |
| `--users` | concurrent simulated users | 1 |
| `--requests-per-user` | requests each user sends | 3 |
| `--interval` | base pause between a user's requests (s) | 1.0 |
| `--jitter` | ± random jitter on that pause (s) | 0.2 |
| `--timeout` | per-request client timeout (s) | 10.0 |
| `--seed` | RNG seed — same seed reproduces the same product/quantity sequence per user | 42 |
| `--duration-cap` | optional hard wall-clock stop, independent of requests-per-user | none |
| `--output` | optional file path to also write the JSON result to | none |

## Baseline methodology

Per level: reset the database to seeded state, run the generator as a
Job against the live cluster, record the generator's own computed
metrics (never sent to OpenObserve — that's Phase 3). Levels were
increased gradually (1 → 3 → 5 → 10), with host memory and pod health
checked between each level before proceeding, per the resource-safety
requirement. `requests_per_user=5`, `interval=0.5s`, `jitter=0.1s`,
`seed=42` held constant across all four required levels for direct
comparability.

## Measured results (actual, this session — `docs/baseline-results/*.json`)

| Users | Total reqs | Success | 409 (inventory) | Infra errors | p50 | p95 | p99 | RPS |
|---|---|---|---|---|---|---|---|---|
| 1  | 5  | 5 (100%)  | 0  | 0 | 83ms   | 150ms  | 150ms  | 1.9 |
| 3  | 15 | 15 (100%) | 0  | 0 | 160ms  | 574ms  | 652ms  | 4.4 |
| 5  | 25 | 25 (100%) | 0  | 0 | 335ms  | 818ms  | 824ms  | 6.0 |
| 10 | 50 | 35 (70%)  | 15 | 0 | 638ms  | 1441ms | 1456ms | 8.0 |

Exploratory (beyond the required minimum, `requests_per_user=3` to
isolate concurrency pressure from sheer duration):

| Users | Total reqs | Success | 409 (inventory) | Infra errors | p50 | p95 | p99 | RPS |
|---|---|---|---|---|---|---|---|---|
| 20 | 60 | 35 (58%) | 25 | 0 | 2231ms | 2799ms | 2869ms | 8.2 |

All raw JSON output is preserved in `docs/baseline-results/`.

## Observed degradation threshold

**No infrastructure-level error-rate degradation was observed within the
tested range (1–20 concurrent users).** `server_error_count` and
`timeout_count` were `0` at every level tested, including the exploratory
20-user run. This is reported as measured, not assumed — the task
explicitly warned against fabricating a threshold, and none was found in
this range.

At 10 users, `total_requests` (50) exceeded the combined seeded stock
across all three products (35 units), producing exactly 15 `409
insufficient_inventory` responses — 35 successes, matching total stock
exactly. This is a **business condition** (correctly, separately
classified from infrastructure failure by the generator's
`infra_error_rate` metric, which stayed at `0.0`), not a system fault —
per the explicit instruction not to conflate the two.

**Latency does degrade progressively and substantially with concurrency**
— this is the real, measured characteristic of this implementation:

- p50: 83ms → 160ms → 335ms → 638ms → 2231ms (1/3/5/10/20 users)
- p99: 150ms → 652ms → 824ms → 1456ms → 2869ms

The most likely cause, based on the existing application's known design
(not newly instrumented in this phase): `services/database/db.py` fully
serializes **every** database operation behind a single process-wide
`threading.Lock()` (a deliberate Phase 1 decision, see D005/D012's
context) — every reservation, release, and persistence call across every
concurrent request queues behind that one lock. This is a plausible,
architecturally-grounded explanation for the observed near-linear latency
growth with concurrency, offered as the most likely explanation given the
existing code, not confirmed via new instrumentation (that's Phase 3's
job).

Escalation beyond 20 users was deliberately not attempted: host free
memory dipped to ~500Mi during the 20-user run (from a baseline around
580–620Mi), and per the explicit resource-safety instructions not to
intentionally exhaust host memory or destabilize the cluster, this was
treated as the point to stop increasing load rather than push further on
this laptop. The cluster fully recovered afterward (all 5 pods `Running`,
`0` restarts, full 57-test suite still green).

## Idempotency: deliberately deferred, not solved

The Phase 1 review flagged missing idempotency as HIGH severity. For
Phase 2: **no retry behavior was implemented, and none was needed** — the
baseline experiment's purpose (characterize latency/throughput/error
behavior at increasing concurrency) doesn't require retries, and the
generator's `infra_error_rate` came back `0.0` at every tested level, so
there was never a case where retry behavior would even have been
exercised. No new architectural decision was made or silently invented;
`DECISIONS.md` was not modified. If a later phase's traffic model
requires retries (e.g., a longer-running continuous background traffic
generator that needs to tolerate transient network blips), that must be
proposed and decided explicitly first, per the same instruction that
applied here.

## Limitations

- Only 3 seeded products exist (35 total units); realistic sustained
  traffic at higher concurrency will hit inventory limits quickly. This
  is expected and correctly classified, not a generator or application
  defect.
- No `metrics-server` in this kind cluster, so host-level CPU/memory
  attribution to specific pods during the runs is inferred from overall
  `free -h` and pod-restart-count observations, not per-pod cgroup
  metrics.
- The "most likely cause" of latency growth (the global SQLite lock) is
  an architecturally-grounded inference from reading the existing code,
  not a conclusion drawn from new profiling/instrumentation — Phase 3's
  telemetry is what will let this be confirmed empirically (e.g., a
  per-service latency breakdown would show whether time is actually spent
  waiting on that lock).
- The generator's product-selection weighting reduces but does not
  eliminate inventory-driven 409s under sustained load — by design, per
  the explicit instruction that exhaustion is a legitimate outcome to
  observe, not something to engineer away entirely.
