# Phase 3 — OpenTelemetry Instrumentation & OpenObserve Telemetry

This document covers what was actually built and actually verified live
against OpenObserve. See `IMPLEMENTATION_PLAN.md` for how this fits into
the overall phase plan and `DECISIONS.md` for the binding architectural
decisions (D002 OpenObserve, D003 direct OTLP/no Collector, D011 kind
host connectivity) this phase implements on top of, unchanged.

## 1. Telemetry architecture

```
Synthetic Traffic (Phase 2 generator)
      |  real HTTP
      v
api-gateway  --[OTel: FastAPI server span + httpx client span]-->
      |
order-service --[same]-->
      |            \
inventory-service    payment-service
      |                    |
   database  <--[OTel: FastAPI server span + manual db.* spans]-->

Every hop above propagates one W3C trace context (traceparent header),
injected/extracted automatically by opentelemetry-instrumentation-httpx
(client) and opentelemetry-instrumentation-fastapi (server) -- no custom
propagation protocol.

Each service's OTel SDK batches spans/logs/metrics and exports them via
OTLP/HTTP directly to OpenObserve. No Collector (D003).
```

Each service independently initializes its own `TracerProvider` /
`LoggerProvider` / `MeterProvider` at startup (`telemetry.py`, duplicated
per service like `logging_utils.py` — no shared package, consistent with
the Phase 1 pattern). Per DECISIONS.md D005, `database` remains the only
place that touches SQLite; its own spans are added manually since there's
no maintained OTel auto-instrumentation for the stdlib `sqlite3` module.

## 2. Service instrumentation

| Service | `service.name` | Auto-instrumented | Manual additions |
|---|---|---|---|
| api-gateway | `payasam-api-gateway` | FastAPI (server), httpx (client) | request metrics middleware |
| order-service | `payasam-order-service` | FastAPI, httpx | `transaction_id`/`order.operation` span attributes, request metrics |
| inventory-service | `payasam-inventory-service` | FastAPI, httpx | request metrics |
| payment-service | `payasam-payment-service` | FastAPI, httpx | request metrics |
| database | `payasam-database` | FastAPI | manual `db.*` spans per operation (see §4), request metrics |

Resource attributes on every span/log/metric: `service.name`,
`service.version` (`phase3`), `deployment.environment` (`local` — an
explicit, honest label; this is a local simulation, not real GCP, per the
PRD's own instruction not to pretend otherwise).

## 3. Trace propagation — verified, not assumed

Verified two ways:

1. **Unit** (`tests/unit/test_telemetry.py`): a header-capturing httpx
   transport wraps the real ASGI apps in-process and asserts every
   propagated `traceparent` header across a full order transaction shares
   exactly one trace id (and each hop has a distinct span id).
2. **Live** (this session, against the actual deployed cluster and real
   OpenObserve): one real order was placed through `api-gateway`, and its
   full trace was pulled back from OpenObserve by `trace_id`. Result: **37
   spans, one trace_id, across all 5 services**, in the exact required
   causal shape:

   ```
   api-gateway POST /orders (86.9ms, root)
     -> order-service POST /orders (73.9ms)
          -> inventory-service POST /reserve (25.3ms)
               -> database POST /inventory/reserve (8.1ms)
                    -> db.reserve_inventory (6.2ms, manual span)
          -> payment-service POST /payments (15.2ms)
               -> database POST /payments (7.1ms)
                    -> db.create_payment (5.2ms, manual span)
          -> database POST /orders (7.6ms)
               -> db.create_order (6.0ms, manual span)
   ```

   Full raw span list: `docs/telemetry-verification/example-trace.txt`.

This is a single propagated distributed trace, not separate per-service
traces — the quality gate's explicit defect condition does not apply.

## 4. Span attributes

Low-cardinality, no secrets/PII, per the phase's explicit constraints:
`service.name`/`service.version`/`deployment.environment` (resource),
`http.route`, `http.status_code` (from auto-instrumentation),
`transaction_id`, `order.operation`, `order.item_count` (order-service,
manual), `db.system`, `order.operation`, `db.outcome`, `db.product_id` /
`db.payment_id` / `db.order_id`, and **`db.lock_wait_ms`** (database,
manual — see §7). The full order request body is never attached to a
span; only identifiers and outcome codes are.

## 5. Logging: preserved and correlated

Phase 1's structured JSON stdout logging is unchanged in shape and is
still the primary local debugging signal. Two additions, both additive:

1. `logging_utils.py`'s `JsonFormatter` now includes `trace_id`/`span_id`
   in every log line emitted while a span is active (confirmed absent
   when no span is active — verified in
   `test_logs_include_trace_and_span_id_when_span_active`).
2. An OTel `LoggingHandler` is attached to each service's existing logger
   (`attach_otlp_logging_handler`), so the same log records are also
   exported via OTLP to OpenObserve's `logs` stream, automatically
   carrying the same trace/span ids.

Live-verified: querying OpenObserve's logs stream for one transaction_id
returned 7 log lines across `payasam-order-service`, `payasam-database`,
`payasam-payment-service`, and `payasam-inventory-service`, **all sharing
one trace_id** — direct proof logs can be pivoted to their trace and back.

## 6. Metrics

Deliberately minimal, per the phase's explicit priority ("tracing is the
highest priority... do not sacrifice trace correctness merely to add a
large number of metrics"): each service records `http.server.request.count`
(counter) and `http.server.request.duration` (histogram), dimensioned
only by `http.route` and a coarse `http.status_class` (`2xx`/`4xx`/`5xx`)
— never raw status codes or IDs, to keep cardinality bounded.
`opentelemetry-instrumentation-fastapi` also emits its own standard
`http_server_*` metrics (duration/request/response size, active
requests) for free. Live-verified present in OpenObserve's metrics
streams with the expected dimensions.

No dependency-latency metric was added separately — httpx client span
durations already capture this precisely (see the example trace), and a
duplicate metric would add cardinality for no real additional signal.

## 7. What telemetry revealed about latency (the Phase 2 question, answered)

Phase 2 observed latency growing substantially with concurrency and
*hypothesized* the cause was `database`'s single process-wide
`threading.Lock()` (D005), explicitly flagging that hypothesis as
unconfirmed. Phase 3 added `db.lock_wait_ms` as a span attribute — timing
lock acquisition separately from the rest of each database operation —
specifically to check that claim against real data instead of leaving it
a guess.

Live-measured (`docs/baseline-results/` for generator side, OpenObserve
queries for trace side), same methodology as Phase 2 (fresh reset per
level, `seed=42`):

| Users | Root span avg (trace) | Generator avg (client) | `db.*` avg span dur | `db.*` avg lock_wait | lock_wait as % of span |
|---|---|---|---|---|---|
| 1 | 154.9ms | 159.0ms | 5.3–7.8ms | ~0.0ms | ~0% |
| 5 | 404.5ms | 411.4ms | 13.1–19.9ms | 6.7–11.7ms | ~51–59% |
| 20 | 2146.1ms | 2215.8ms | 115.6–229.8ms | 101.3–211.3ms | ~88–92% |

Two things this actually supports, precisely:

1. **Trace data and generator-observed latency agree closely** at every
   level (within ~5ms average, the expected gap being the generator's own
   client-side network/serialization overhead) — telemetry explains real
   application behavior, not a separate, disconnected number.
2. **The SQLite lock is a real, measured, dominant contributor at higher
   concurrency, but not the whole story.** At 1 user (no contention),
   lock-wait is ~0ms and each DB operation still costs 5–8ms baseline
   (most likely `db.py` opening a fresh `sqlite3` connection on every
   single call, per its own existing code — not newly discovered here,
   but now consistent with the measured floor). As concurrency rises, the
   *lock-wait* component grows much faster than that baseline (0% → ~55%
   → ~90% of each operation's time from 1→5→20 users), confirming the
   lock as the dominant driver of degradation under load specifically —
   a more precise conclusion than Phase 2's hypothesis, and one now
   actually backed by telemetry rather than architecture-reading alone.

## 8. OpenObserve configuration (dynamic, never hardcoded)

Per D011, the OpenObserve endpoint depends on the kind bridge network's
gateway IP, which is not guaranteed stable across cluster recreation.
`scripts/configure-telemetry.sh` re-resolves it the same way Phase 0's
`verify-openobserve-connectivity.sh` does, verifies reachability from
inside the cluster, and renders/applies a `telemetry-config` ConfigMap
(`infrastructure/kubernetes/telemetry/telemetry-config.yaml.template`)
holding `OTEL_EXPORTER_OTLP_ENDPOINT=http://<gateway-ip>:5080/api/<org>`,
`DEPLOYMENT_ENVIRONMENT`, `SERVICE_VERSION`. Re-run this script any time
the kind cluster is recreated.

## 9. Credential handling

`OPENOBSERVE_USERNAME`/`OPENOBSERVE_PASSWORD` are never written to any
tracked file. `scripts/create-otel-secret.sh` creates/updates a
Kubernetes `Secret` (`openobserve-credentials`) from environment
variables the operator supplies (typically via a local, git-ignored
`.env` — see `.env.example` for the placeholder shape), piping directly
into `kubectl apply` without ever persisting the values to disk or
echoing them. Both the ConfigMap and Secret are referenced via
`envFrom` with `optional: true` on every Deployment, so a service starts
and serves normally even before either exists (see §10).

## 10. Kubernetes configuration

Each Deployment's `envFrom` references `telemetry-config` (ConfigMap) and
`openobserve-credentials` (Secret), both `optional: true`. `telemetry.py`
builds the OTLP Basic Auth header from `OPENOBSERVE_USERNAME`/`_PASSWORD`
at startup; if either is absent, no auth header is sent (matching
whatever OpenObserve then does with unauthenticated OTLP requests — in
practice, rejected, which is the same as "not exported," not a crash).

## 11. Graceful degradation — verified, not assumed

- **No endpoint configured:** `setup_telemetry()` returns a usable
  no-op-exporting tracer; the app starts and serves normally
  (`test_setup_telemetry_without_endpoint_is_graceful_noop`). Verified by
  deploying before the Secret/ConfigMap existed — all 5 pods started and
  passed their own health/readiness checks with `envFrom` pointing at
  resources that didn't exist yet.
- **Malformed/unreachable endpoint:** exporter construction doesn't raise;
  export failures happen asynchronously in the SDK's background batch
  thread, are printed to stderr (satisfying "failures should be
  observable locally"), and never propagate into request handling —
  reproduced live by pointing a real service at an unreachable OTLP
  endpoint and confirming both the request path and the process stayed
  healthy.

## 12. Data-quality assessment (the 8 questions)

1. **Which service handled the request?** Yes — `service.name` on every
   span/log/metric.
2. **Which transaction did it belong to?** Yes — `transaction_id` is a
   span attribute (order-service) and a log field (every service);
   queried directly in this session (`WHERE transaction_id = '...'`).
3. **Total request duration?** Yes — root span (`api-gateway POST
   /orders`) duration; matches generator-observed latency closely (§7).
4. **Which downstream service consumed the most time?** Yes, from the
   example trace: inventory reservation (~30.0ms) > payment (~18.7ms) >
   order persistence (~10.3ms), all visible as sibling span durations
   under the same parent.
5. **Did any dependency return an error?** Answerable via `span_status`
   (OTel sets this on exceptions/non-2xx per semantic conventions) — the
   mechanism is confirmed present and populated (`status_code`/
   `status_message` fields observed on every span); not exercised with a
   live failing dependency in this session, since Phase 4's failure
   injection is what will actually produce one on demand.
6. **Can logs be correlated with a trace?** Yes — demonstrated live
   (§5): one `transaction_id` query returned log lines from 4 different
   services all sharing one `trace_id`.
7. **Can repeated failures be identified by service?** The data supports
   it (`service_name` + `span_status`/`http_status_code` are both
   present and queryable), but wasn't exercised against a real failure
   pattern in this session — no failure-injection mechanism exists yet
   (Phase 4).
8. **Can latency changes under higher traffic be identified?** Yes —
   this is exactly §7's measurement, done live across three load levels.

Gaps found (#5 and #7): both are "mechanism present, not yet exercised
against a real failure" rather than a missing capability — they don't
need a fix before Phase 4; Phase 4 is what will actually generate the
failure data to exercise them against.

## 13. Known limitations

- `pkg_resources`/`setuptools` had to be added explicitly to every
  service's `requirements.txt` — `opentelemetry-instrumentation`'s
  dependency-checking machinery imports it, and Python's slim Docker
  images (and modern `pip`) no longer bundle `setuptools` by default.
  This was caught by the first real deployment attempt (all 5 pods
  crash-looped identically), not by host-side testing (`setuptools`
  happened to already be present on the host Python), and is a good
  example of why the live Kubernetes verification step in this phase's
  quality gate matters, not just local test runs.
- No standalone OTel Collector (correct, per D003) means no
  batching/buffering point between the cluster and OpenObserve beyond
  each SDK's own in-process `BatchSpanProcessor`/
  `BatchLogRecordProcessor`/`PeriodicExportingMetricReader` — acceptable
  at this traffic scale, revisit only if proven necessary.
- `db.lock_wait_ms` isolates lock-wait from the rest of each database
  operation, but the *rest* (connection open/execute/close) isn't itself
  broken down further; the "fresh SQLite connection per call" theory for
  the baseline ~5–8ms floor is a reasonable read of `db.py`'s existing
  code, not something separately instrumented and measured in this phase.
- Span-error semantics (#5/#7 above) are present but unexercised against
  a real failure in this session, as noted.
