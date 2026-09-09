# Phase 4 — Controlled Failure Injection & Observability Validation

This document records the actual, measured Phase 4 experiments. Raw
per-scenario trace/span evidence lives in
`docs/telemetry-verification/scenario-*.txt`; this document summarizes
and interprets it. See `IMPLEMENTATION_PLAN.md` for how this fits the
overall plan and `DECISIONS.md` for the architectural decisions this
phase implements without changing (D002, D003, D011).

## 1. Fault injection architecture

`faults.py` — one file, byte-identical across `database` and
`payment-service` (same duplication pattern as `logging_utils.py`/
`telemetry.py`; kept identical deliberately, since the shared in-process
unit-test harness caches modules by name and divergent copies caused a
real test failure during development — see §15). Reads `PAYASAM_FAULT_*`
env vars once at process startup; every fault defaults to `0`/off. With
none set, application behavior is provably unchanged (verified: 78/79
tests green with faults disabled, byte-identical Docker image content
for the three untouched services).

Faults are toggled via `kubectl set env deployment/<svc> <VAR>=<value>`
(`scripts/set-fault.sh`), which triggers a normal rolling restart of
just that one Deployment — no new infrastructure, no architecture
change, fully reversible (`scripts/clear-faults.sh`).

## 2. Fault controls

| Env var | Service | Effect | Default |
|---|---|---|---|
| `PAYASAM_FAULT_PAYMENT_LATENCY_MS` | payment-service | `await asyncio.sleep()` before processing `/payments` | `0` |
| `PAYASAM_FAULT_PAYMENT_ERROR_RATE` | payment-service | fraction of `/payments` requests rejected with `503` (1.0 = deterministic, every request) | `0` |
| `PAYASAM_FAULT_DB_LATENCY_MS` | database | `time.sleep()` *inside* the held lock, for every fault-eligible operation (`reserve_inventory`, `create_payment`, `create_order`; `init_db`/`reset_db` explicitly excluded) | `0` |
| N/A (Scenario D) | payment-service | `kubectl scale deployment/payment-service --replicas=0/1` — a pure Kubernetes mechanism, no code | N/A |

Enable: `scripts/set-fault.sh <deployment> <VAR> <value>`. Disable all:
`scripts/clear-faults.sh`. Both confirm the fault's active/inactive state
via the service's own startup log line
(`"FAULT INJECTION ACTIVE: ..."`), not just by checking the env var was
set.

## 3. Methodology

Every scenario: reset DB → baseline traffic (3 concurrent users × 5
requests, `interval=0.5s`, `seed=42` — the same configuration throughout,
for direct comparability) → enable exactly one fault → reset DB → same
traffic pattern → collect OpenObserve evidence → disable fault → reset DB
→ same traffic pattern → confirm recovery. Workload parameters held
constant across scenarios per the phase's explicit instruction.

## 4. Scenario A — Service Latency (payment-service +500ms)

**Fault:** `PAYASAM_FAULT_PAYMENT_LATENCY_MS=500`

| | Success | avg | p50 | p95 | p99 |
|---|---|---|---|---|---|
| Baseline | 15/15 | 229.4ms | 158.0ms | 544.6ms | 556.1ms |
| Faulted | 15/15 | 717.3ms | 659.3ms | 1007.5ms | 1015.7ms |
| Recovered | 15/15 | 202.9ms | 134.4ms | 425.0ms | 425.7ms |

p50 delta: **+501.3ms** (injected: 500ms) — extremely close match.
Success rate unaffected — pure latency, no failures, exactly as designed.

**Root-cause localization** (full trace in
`docs/telemetry-verification/scenario-a-latency.txt`): computed each
span's *self-time* (duration minus the sum of its children's durations)
from the raw trace topology alone — no reference to which service the
fault was actually injected into. Result: `payment-service`'s span had
~513ms of self-time (its only child, the call to `database`, accounts
for just 13ms of its 527ms total) — roughly **40x** higher than
`order-service`'s (~13ms) or `api-gateway`'s (~8ms) self-time. Both of
those services' elevated *total* duration is fully explained by their
children — they are propagated symptoms, not independent contributors.
The telemetry-only conclusion (payment-service is the root cause)
matches the known fault.

**Span status:** `UNSET` throughout (correct — a slow-but-successful
request is not an error).

## 5. Scenario B — Service Error (payment-service, deterministic)

**Fault:** `PAYASAM_FAULT_PAYMENT_ERROR_RATE=1.0`

| | Success | Status | avg |
|---|---|---|---|
| Baseline | 15/15 | 201 | 202.9ms |
| Faulted | 0/15 | 502 (100%, deterministic) | 283.8ms |
| Recovered | 15/15 | 201 | 193.3ms |

**Root-cause localization** (full trace in
`docs/telemetry-verification/scenario-b-error.txt`): `payment-service`'s
`POST /payments` span is the only span in the trace that is both marked
`ERROR` **and** a dead-end leaf (1.89ms, no further children to explain
even that small duration) — a distinct "fails fast" signature.
`inventory-service`'s entire reservation subtree stays `UNSET`
throughout — objective evidence it was never involved.
`order-service`/`api-gateway` inherit `ERROR` purely from HTTP-status
propagation (FastAPI/httpx auto-instrumentation correctly marks spans
`ERROR` on 5xx, confirmed working with zero manual code for those two
services); their elevated *duration* here comes from real compensating
work (releasing the reservation, persisting a `failed` order — each of
those child calls is itself `UNSET`/healthy), not from being broken
themselves.

**Regression check:** Phase 1's D012 compensating-rollback logic
(release reserved inventory on payment failure) confirmed still correct
under this new failure path — inventory fully restored after the run.

**Log correlation:** the human-readable log line `"payment rejected by
injected fault"` from `payasam-payment-service` is directly queryable by
the same `trace_id` as every other service's log line for that
transaction.

## 6. Scenario C — Database Latency / Contention

**Fault:** `PAYASAM_FAULT_DB_LATENCY_MS=200`, applied *inside* the held
lock in `db.py`'s `_traced_op` (not before acquiring it, not after
releasing it) — specifically so the resulting telemetry can distinguish
"this operation is slow" from "I'm waiting on someone else's slow
operation."

An existing, safe, already-instrumented mechanism was used —
`_traced_op`'s existing `db.lock_wait_ms` attribute (built in Phase 3)
was the natural injection point; no new architecture, no risk of
corrupting data or wedging the process (the fault only ever adds a
bounded sleep inside a lock this process already owns and will release
normally).

| Concurrency | Generator avg | `db.*` avg span duration | `db.*` avg `lock_wait_ms` | lock_wait as % of span |
|---|---|---|---|---|
| 1 user (no contention) | 794.1ms | 209.1ms | **0.00ms** | 0% |
| 5 users (contention induced) | 2720.7ms | 833.0ms | **624.2ms** | ~75% |

**This is the requested distinction, directly measured, not asserted:**
at 1 concurrent user, 100% of the extra time is the database operation
itself being slow (`lock_wait_ms=0.00` exactly) — this isolates "database
operation delay." At 5 concurrent users, `lock_wait_ms` **exceeds** the
~200ms base fault (505–694ms average) — because every lock-holder now
occupies the lock 20–40x longer than its normal ~5–10ms, so the queue
behind it grows much worse than linearly. This isolates "SQLite lock
waiting" as a distinct, larger, concurrency-driven amplification of the
base fault. Full numbers: `docs/telemetry-verification/scenario-c-database.txt`.

**Database safety:** no corruption, no permanent lock, no crash — the
fault only ever adds a bounded, released sleep; `reset_db`/`init_db`
were explicitly excluded from the fault so administrative operations
stay fast; recovery confirmed clean.

## 7. Scenario D — Service Unavailable

**Fault:** `kubectl scale deployment/payment-service --replicas=0` (no
code, matches Phase 1's already-proven pattern from
`test_payment_failure.py`).

| | Success | Status | avg |
|---|---|---|---|
| Baseline | 15/15 | 201 | (established in prior scenarios' recovery checks) |
| Faulted | 0/15 | 502 (100%) | 1077.7ms (high variance: 9.6–5152ms) |
| Recovered | 15/15 | 201 | 243.9ms |

**Root-cause localization, and how it differs from Scenario B** (full
trace in `docs/telemetry-verification/scenario-d-unavailable.txt`): in
Scenario B a `payasam-payment-service` span exists (fast, `ERROR`). In
Scenario D, **no `payasam-payment-service` span exists anywhere in the
trace** — the process isn't running to create one. The failure is
visible entirely on `order-service`'s own client span, whose
`status_message` literally reads `"ConnectError: All connection attempts
fa[iled]"` — unambiguous, self-explanatory evidence that the dependency
is *unreachable*, categorically distinct from "reachable but rejecting."

**A real operational lesson learned during this scenario:** immediately
re-running traffic right after `kubectl scale ... --replicas=1` (before
waiting for `order-service`/`api-gateway`'s own readiness probes to
re-poll and catch up) produced a batch of spurious timeouts — not a
regression, but a genuine race between "pod Running" and "dependent
services' readiness cache updated." Fixed operationally by explicitly
polling `/ready` before considering a service recovered, not just
`kubectl rollout status`. Documented here since a later phase's demo
script should account for this.

## 8. Optional — Cascading Failure

Reused Scenario A's fault at 5 concurrent users (`docs/telemetry-verification/scenario-cascading.txt`):
`payment-service` span avg 586.2ms (base 500ms fault + ~17% concurrency
overhead) → `api-gateway` root span avg 799.8ms → generator-observed
805.4ms. One root fault, cleanly propagated through two more hops, no
failures (10s client timeout never approached).

Notably **much milder concurrency amplification** than Scenario C's
database fault (500ms→586ms, +17%, vs. 200ms→~600ms, +200%+): this
fault is an `asyncio.sleep()` with no shared lock, so it doesn't block
other concurrent requests the way the database's serializing lock does.
A real, evidence-based distinction between "a slow independent
operation" and "a slow operation holding a shared bottleneck."

## 9. Root-cause vs. propagated symptoms — summary

| Scenario | First abnormal span | Self-time / status signature | Propagated symptoms |
|---|---|---|---|
| A (latency) | `payment-service POST /payments` | self-time ~513ms, ~40x any other span | `order-service`, `api-gateway` (fully explained by children) |
| B (error) | `payment-service POST /payments` | `ERROR`, 1.89ms, no children | `order-service`, `api-gateway` (`ERROR` via propagation + real compensating work) |
| C (DB latency) | `db.*` operations | span duration inflated even with zero lock_wait (1 user) | `lock_wait_ms` on other concurrent `db.*` ops (5 users) |
| D (unavailable) | *(no span — the absence itself is the signal)* | `order-service`'s client span: `ConnectError` message | `order-service`, `api-gateway` (`ERROR` via propagation) |

Every scenario's conclusion was derived from trace topology, span
self-time, span status, and status messages — not from prior knowledge
of which fault was injected where.

## 10. Tests executed

`pytest tests/unit/test_faults.py` — 12/12 passed: defaults-disabled,
invalid-config-falls-back-safely, deterministic error injection at rate
0.0 and 1.0, real behavioral latency injection (measured `time.monotonic`
delta ≥ configured delay), real behavioral error injection (structured
503, no payment persisted), database latency fault (measured delay,
`init_db`/`reset_db` correctly excluded), and fault restoration (a fresh
env-cleared load behaves exactly like the never-faulted baseline).

Full suite: `pytest tests/` → **78 passed, 1 skipped** (the pre-existing
live-OpenObserve-credentials test, unrelated to Phase 4, skips without
credentials in the runner's own shell — unchanged from Phase 3).

## 11. Defect discovered/fixed during this phase

A genuine test-infrastructure bug, not a production one: `faults.py` was
initially written as *different* content for `database` vs
`payment-service`. Because both are imported under the same bare name
(`import faults`) and the in-process unit-test harness (`tests/unit/
conftest.py`) loads multiple services' `main.py` in one Python process,
Python's module cache made whichever copy loaded first "win" for every
other service too — `payment-service`'s code was intermittently getting
`database`'s `faults` module (missing `PAYMENT_LATENCY_MS` entirely),
causing an `AttributeError`. A second, related issue: `db.py` itself is
also imported under a bare name and gets cached the same way, so once
loaded it holds a stale reference to whatever `faults` module was live
at *its own* first import — clearing only `faults` from the module cache
between tests wasn't sufficient; `db` had to be cleared too. Fixed by
(a) making the two `faults.py` files byte-identical (matching the
established `logging_utils.py`/`telemetry.py` pattern) and (b) adding an
autouse pytest fixture that clears both `faults` and `db` from
`sys.modules` before each fault-related test. Caught immediately by the
first real test run, before any live deployment — exactly the kind of
thing this project's layered testing strategy is meant to catch early.

## 12. Limitations

- Span-error semantics were verified for HTTP-level failures (503, 5xx
  propagation) and for a genuine connection failure; not separately
  verified for e.g. an explicit application exception mid-handler (not
  a scenario this phase required).
- The readiness-race lesson in §7 is documented but not yet encoded as
  an automated wait-for-`/ready` step in the experiment scripts
  themselves — a reasonable follow-up for a later phase's demo tooling,
  not required by this phase's own quality gate.
- Fault configuration changes require a pod restart (env vars are read
  once at process startup) — deliberate, not a limitation of this
  phase's design (see `faults.py`'s docstring), but worth noting for
  anyone expecting a live-toggle API.

## 13. Architectural decisions

None changed. `DECISIONS.md` was not modified — D002 (OpenObserve), D003
(no Collector), D011 (kind host connectivity) all remain exactly as
established; this phase's fault injection is a small, config-driven
addition within the existing per-service structure, not a new
architectural element.
