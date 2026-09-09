# Payasam — Implementation Plan

**Status:** Planning only. No product code has been written. This document is the output of Phase 0 discovery plus a concrete phase-by-phase plan for Phases 1–7 as defined in `PRD.md` §32–39.

**Derived from:** `PRD.md` (2026-08-26) + live inspection of this machine on 2026-08-26.

---

## 1. Discovery Findings (as of 2026-08-26)

### 1.1 Repository state

```
~/claude-workspace/payasam/
├── PRD.md
└── gcp-cluster/          ← empty, 0 files
```

`gcp-cluster/` is confirmed empty. No prior Payasam code exists. This is a clean slate — Phase 1 is a true greenfield build, not a migration.

### 1.2 OpenObserve — was broken at initial discovery, now resolved and verified

At initial discovery, the existing container was found `Exited (1)`, crash-looping
on boot because `ZO_ROOT_USER_PASSWORD=password123` failed OpenObserve's
server-side password-strength policy (needs upper+lower+digit+special char;
`password123` doesn't qualify). Port mapping (`5080/tcp → host 5080`) was
correctly configured; only the password was the problem.

That container has since been recreated outside of this planning process
with a policy-compliant local password. Re-verification (read-only) confirms:

```
docker ps        → openobserve   Up   0.0.0.0:5080->5080/tcp
curl /healthz     → HTTP 200, {"status":"ok"}
```

OpenObserve is therefore reachable and healthy as of this writing. Per
PRD §5/§29, the actual password value is never written into this plan, into
source, or into any tracked configuration — only referenced via environment
variables / a local, git-ignored `.env` file (see D010 in `DECISIONS.md`).
This issue is closed; it is retained here only as a record of what was
found and fixed, since Phase 0's discovery record should reflect what
actually happened.

### 1.3 Kubernetes tooling

| Tool | Version | Status |
|---|---|---|
| `kubectl` | v1.33.2 | installed |
| `kind` | v0.29.0 | installed, no clusters running (`kind get clusters` → none) |
| `minikube` | v1.36.0 | installed, no active cluster (`minikube status` → no container) |
| `k3d` / `k3s` / `helm` | — | **not installed** |
| `docker compose` | v5.1.4 (plugin) | installed |
| `docker buildx` | v0.30.0 | installed, default builder running |

Stale kubeconfig contexts exist (`kind-payasam`, `kind-ctc`, `minikube`) pointing at clusters that no longer exist as Docker containers — leftovers from earlier, unrelated sessions on this shared dev machine. They are dead references, not running infrastructure; no cleanup needed before we create a fresh `kind` cluster (kind will just add/overwrite its own context).

**Decision: use `kind`, not `minikube`.** Both are viable and already installed. `kind` runs the control plane as a single Docker container with no extra VM/driver layer, comes up faster, and is lighter on memory (relevant — see §1.5). Given the stale `kind-payasam` context already implies this was the prior intended choice, and the PRD explicitly says "use an already available local Kubernetes solution" and "do not install a heavyweight cluster unnecessarily," `kind` is the better fit. No node image is cached yet — first `kind create cluster` will pull the node image over the network (confirmed working internet access) and will take a few minutes.

### 1.4 Language/runtime tooling

| Runtime | Version |
|---|---|
| Python | 3.13.5, pip 26.1.1, `venv` available |
| Node.js | v20.19.4, npm 10.8.2 |
| Go | 1.18.1 (from 2022 — stale toolchain) |
| git | 2.34.1 |
| jq | present |

Critically, **pip already has FastAPI, uvicorn, the full `opentelemetry-*` SDK/exporter family (OTLP over HTTP and gRPC), the official `kubernetes` client, `httpx`, `aiohttp`, and `requests` pre-installed.** This is a strong environmental signal for the application-service stack (see §2.1). Go is present but outdated and nothing about this project needs Go specifically, so it's excluded rather than upgraded.

### 1.5 System resources — real constraint

```
Mem:   14Gi total, 9.6Gi used, 319Mi free, 5.6Gi available (reclaimable cache)
CPU:   16 cores
Disk:  80G available on /
```

The 9.6Gi in use is this being a shared, actively-used dev workstation (many Firefox tab processes, GNOME shell, MySQL, AnyDesk — none related to this project). **Effective budget for the whole Payasam stack (kind control plane + 8 app pods + OpenObserve + traffic generator + Payasam backend/frontend) is realistically ~4–5Gi, not 14Gi.** This directly shapes several decisions below (single replica per service, small explicit resource requests/limits, no separate OTel Collector deployment). This should be re-checked at Phase 0 execution time since it fluctuates.

Docker also has 9.5GB of reclaimable volumes from unrelated projects on this machine (mlflow, ollama, postgres, etc.) — not touched, not relevant, noted only so they aren't mistaken for Payasam state later.

### 1.6 Network

Outbound internet confirmed reachable (npm registry, PyPI, Docker Hub all returned HTTP 200). Needed once, at build/pull time (kind node image, pip/npm installs, base images). The PRD's "no internet connectivity" requirement (§47) applies to running the demo, not to setting it up — this plan treats it that way.

---

## 2. Key Technical Decisions

Per PRD §1.2 ("if genuinely unspecified, choose the simplest reasonable option and document the decision"), the following choices are made now so implementation isn't blocked on avoidable questions later.

### 2.1 Application services: Python + FastAPI + uvicorn

Rationale: already installed system-wide including the full OpenTelemetry SDK/exporter set, which is the single most telemetry-relevant stack signal in the environment. FastAPI is lightweight, has trivial async HTTP client support (`httpx`), and needs no build step (unlike a compiled Go service), which matters for iteration speed on 7 small services. All application services (`api-gateway`, `auth`, `users`, `orders`, `payments`, `inventory`, `notifications`, `database`) will be structurally identical small FastAPI apps to keep the system understandable by one developer (PRD §1.1).

### 2.2 "Database" service: not real Postgres — a small deterministic FastAPI+SQLite service

PRD explicitly excludes Postgres "unless later proven necessary" (§1.3) and Scenario 1 requires the *safest and most deterministic* method of causing saturation (§14). Standing up real connection-pool exhaustion against Postgres is possible but harder to make deterministic and safely reversible under a tight demo timeline. Instead, `database` is a small FastAPI service backed by SQLite, exposing normal read/write endpoints plus an **admin control endpoint** (`POST /admin/inject`, `POST /admin/recover`) that deterministically adds artificial per-query latency / connection-slot contention / error rate. This keeps the causal chain real (payment really does call this service and really does experience the injected slowness — nothing is faked in the response path) while making it 100% reproducible and safely reversible, satisfying PRD §3's "no fake dashboard values" rule at the *telemetry* level even though the failure mechanism itself is a controlled synthetic dependency rather than genuine Postgres internals. Documented explicitly as a simulation-environment decision, not hidden.

If this later proves too artificial for the demo's credibility, promoting `database` to real SQLite-file-backed contention or a real Postgres container is a contained, isolated change — nothing else in the system depends on the mechanism.

### 2.3 Failure injection mechanism: per-service admin HTTP endpoints

Each application service exposes a small, unauthenticated (local-only) `/admin/config` endpoint accepting `{ latency_ms, error_rate, cpu_burn_pct }`. A single `failure-controller` module (under `simulator/failures/`) calls these endpoints and/or `kubectl` (for Scenario 4's deployment regression, via `kubectl set env`/rollout) to change **actual running process behavior**. This is the literal implementation of PRD §3's required flow: controller mutates real service behavior → real requests degrade → real telemetry changes. No incident is ever written directly; Payasam only ever reads what OpenObserve observed.

### 2.4 Telemetry path: OTel SDK in each service → **direct OTLP/HTTP to OpenObserve**, no separate Collector

OpenObserve natively accepts OTLP/HTTP ingestion. PRD §1.3 explicitly warns against "unnecessary orchestration layers" and duplicate observability pipelines. Given the memory budget (§1.5), skipping a standalone OpenTelemetry Collector deployment and having each service's SDK export straight to OpenObserve's OTLP endpoint is the simplest approach that satisfies PRD §12–13. This will be revisited only if direct export proves unreliable under the traffic generator's load (unlikely at 10–50 users / low RPS).

### 2.5 Cluster-to-host connectivity: resolve kind's bridge gateway IP, inject via ConfigMap

This machine runs native Docker Engine on Linux (not Docker Desktop), so `host.docker.internal` does **not** resolve automatically inside kind nodes. OpenObserve runs as a plain host container (`-p 5080:5080`), not inside the `kind` Docker network. The reliable, well-documented approach: after `kind create cluster`, resolve the kind bridge network's IPv4 gateway IP — Docker's published-port DNAT is reachable from containers on that bridge via the gateway address — and inject it as `OPENOBSERVE_URL=http://<gateway-ip>:5080` into a Kubernetes ConfigMap consumed by all services. This will be automated in a setup script run as part of cluster bring-up, not hardcoded, per PRD §29's config externalization requirement.

**Verified in Phase 0** (see `gcp-cluster/docs/phase0-networking.md` and D011 in `DECISIONS.md`) — with one correction to the extraction command originally sketched above: the `kind` network is dual-stack, so a naive `{{(index .IPAM.Config 0).Gateway}}` can grab the IPv6 gateway instead of IPv4 (it did, on this machine). The corrected, tested command lives in `gcp-cluster/scripts/verify-openobserve-connectivity.sh`. The ConfigMap itself is deliberately *not* created yet — that remains Phase 3's task (§5, Phase 3) — Phase 0 only proved the resolution + reachability mechanism.

### 2.6 Traffic generator: custom small asyncio script, not Locust

`locust` is not installed and would add a web UI and dependency footprint the project doesn't need (PRD §43: don't add libraries for trivial functionality). A ~150-line asyncio script using `httpx.AsyncClient`, controllable via a tiny FastAPI admin surface (`start`, `stop`, `set_rate`), satisfies PRD §11 fully (configurable users/RPS, generates `user_id`/`request_id`/`transaction_id`, propagates trace context) with no new dependency beyond what's already installed.

### 2.7 Frontend: React + Vite, minimal

PRD §46 requires several distinct live-updating views (role selection, Technical Ops, Business, Investigation) with a clear narrative flow — enough real interactivity/state that hand-rolled server-rendered HTML+polling would fight the requirement rather than simplify it. React + Vite (no CRA — Vite is the lighter, faster modern default) is used with plain `fetch` polling against the Payasam backend (no Redux/UI-kit/GraphQL). This is the simplest option that still satisfies "no unnecessary frontend frameworks": one framework, no auxiliary libraries beyond routing if needed.

### 2.8 Kubernetes manifests: plain YAML + Kustomize (built into kubectl), no Helm

`helm` isn't installed and isn't needed for ~9 small Deployments/Services. `kubectl` already bundles Kustomize v5.6.0, sufficient for per-service base manifests plus one `kustomization.yaml` under `infrastructure/kubernetes/`.

---

## 3. Directory Structure (created incrementally, per phase — not all at once)

```
gcp-cluster/
├── services/
│   ├── api-gateway/
│   ├── auth/
│   ├── users/
│   ├── orders/
│   ├── payments/
│   ├── inventory/
│   ├── notifications/
│   └── database/            # FastAPI+SQLite, admin-controllable
├── infrastructure/
│   └── kubernetes/          # Deployment/Service YAML + kustomization.yaml
├── simulator/
│   ├── traffic/             # asyncio traffic generator + admin API
│   └── failures/            # failure-controller
├── telemetry/
│   └── shared/               # shared OTel bootstrap helper imported by each service
├── payasam/
│   ├── backend/              # FastAPI: incident correlation, business impact, optimization
│   └── frontend/             # React + Vite
├── tests/
│   ├── unit/
│   ├── integration/
│   └── end-to-end/
├── docs/
│   ├── architecture.md
│   ├── development.md
│   └── demo.md
├── scripts/                  # start/stop/reset/test/demo, gateway-IP resolver
└── README.md
```

Only directories a given phase actually needs are created in that phase (PRD §6, §28).

---

## 4. Configuration (`.env.example`, no real secrets committed)

```
OPENOBSERVE_URL=http://<resolved-at-setup>:5080
OPENOBSERVE_ORG=default
OPENOBSERVE_USERNAME=admin@example.com
OPENOBSERVE_PASSWORD=<policy-compliant, local-only demo value>
TRAFFIC_USERS=10
TRAFFIC_RATE=2
K8S_NAMESPACE=payasam
```

`OPENOBSERVE_PASSWORD` in `.env.example` will be a placeholder, never the real value used to recreate the container.

---

## 5. Phase Plan

Each phase: **Tasks → Dependencies → Deliverables → Quality gate → Test strategy**. Per PRD §32/§40, a failed gate stops progress — no phase N+1 work starts until phase N's gate passes with real command output, not narrated success.

### Phase 0 — Repository and environment discovery *(complete)*

**Tasks:**
1. ~~Recreate the OpenObserve container with a policy-compliant password~~ — **done** (performed outside this planning process; verified read-only: container `Up`, `/healthz` → `HTTP 200`). No further action needed here; the implementation must not automatically recreate this container (see D010).
2. `kind create cluster` (config: `gcp-cluster/infrastructure/kind/kind-config.yaml`, cluster name `payasam`, single control-plane node) — **done.** `kubectl get nodes` shows `payasam-control-plane` `Ready`; all `kube-system`/`local-path-storage` pods `Running`.
3. Resolve and record the kind→host gateway IP approach from §2.5 / D011 — **done, experimentally verified.** A temporary in-cluster pod successfully called OpenObserve's `/healthz` via the resolved gateway IP (`172.18.0.1` on this machine) twice independently, both `HTTP 200`. `host.docker.internal` was confirmed (not assumed) to fail from inside a pod. D011 updated to **Accepted** in `DECISIONS.md`. Full record in `gcp-cluster/docs/phase0-networking.md`; reusable script at `gcp-cluster/scripts/verify-openobserve-connectivity.sh`.
4. Populate a local, git-ignored `.env` from `.env.example` with the current OpenObserve credentials — **deferred to Phase 1.** Phase 0 needed only the unauthenticated `/healthz` endpoint (no credentials required or used); `.env`/`.env.example` will be introduced when Phase 1 services first need `OPENOBSERVE_URL`/credentials, per PRD §29.

**Dependencies:** none — this is the entry point.

**Quality gate (PRD §32):**
- `docker ps` shows OpenObserve `Up`, `curl` to its health endpoint returns 2xx. — **satisfied, verified.**
- `kubectl get nodes` shows the kind node `Ready`. — **satisfied, verified.**
- Kubernetes workload can reach OpenObserve from inside the cluster. — **satisfied, verified.**
- This plan document exists and reflects actual (not assumed) machine state.

**Test strategy:** manual/scripted verification commands only (no app code exists yet to unit-test) — see `gcp-cluster/scripts/verify-openobserve-connectivity.sh` for the repeatable connectivity check.

**Phase 0 gate: PASSED.** Proceeding to Phase 1 requires a separate go-ahead per PRD §1.1/§32 (do not implement past the current phase without instruction).

### Phase 1 — Running application *(complete)*

**Scope note (revises this section's original task list):** the Phase 1 work order explicitly narrowed the topology from this plan's original 8-service sketch to the minimum useful chain — `api-gateway → order-service → {inventory-service, payment-service} → database` — and explicitly excluded `auth`/`users`/`notifications` unless genuinely required by the transaction flow. They weren't required, so they were not built. This is a deliberate, directed scope reduction for Phase 1, not a silent drift from the plan; those three services remain available to add in a later phase if the demo narrative needs them.

**What was actually built:**
- 5 FastAPI services: `database` (SQLite-backed, per D005 — the single writer; other services reach it over HTTP, never a shared file), `inventory-service`, `payment-service`, `order-service` (orchestrator, with compensating inventory release on any downstream failure — see new decision D012), `api-gateway` (external entry point, thin forwarding proxy to `order-service`).
- Every service: `/health` (liveness) plus `/ready` (readiness — checks its actual downstream dependencies, not a hardcoded 200) where it has dependencies.
- Structured JSON logging (stdlib `logging` + a small custom formatter, duplicated per service rather than packaged — no new dependency) with `service`, `severity`, `event_type`, `request_id`, `transaction_id` fields, confirmed correlating across services in real pod logs.
- `request_id` (gateway-assigned or caller-supplied via `X-Request-Id`) and `transaction_id` (order-service-assigned) propagate through the whole chain — confirmed in logs. No trace_id/span_id introduced (correctly deferred to Phase 3/OpenTelemetry).
- Kubernetes manifests (Deployment + Service per service) under `infrastructure/kubernetes/`, single replica each, resource requests/limits as originally planned (`64Mi/50m` requests, `150Mi/250m` limits — slightly higher memory limit than the `128Mi` originally sketched, based on actually observing FastAPI/uvicorn's footprint rather than the earlier estimate).
- `scripts/build.sh` (build all 5 images + `kind load docker-image`) and `scripts/start.sh` (build + apply + wait-for-rollout), both executed successfully against the existing Phase 0 `payasam` kind cluster — no cluster recreation.

**Dependencies:** Phase 0 gate passed — satisfied, reused the existing cluster and networking without modification.

**Deliverables:** `services/*`, `infrastructure/kubernetes/*`, `scripts/build.sh`, `scripts/start.sh`, `tests/unit/*`, `tests/integration/*`.

**Quality gate (PRD §33) — result:**
- All 5 pods `Running`/`Ready` — verified.
- Real end-to-end transaction through `api-gateway` (both from an in-cluster pod and via `kubectl port-forward` from the host) succeeds, decrements the correct product's inventory by the correct amount, and persists real `orders`/`payments` rows — verified, including that state survives across multiple sequential requests.
- A genuine Payment Service outage (scaled to 0 replicas — not the Phase 4 failure controller, which doesn't exist yet) is detected by `order-service`, the client receives a real `502 payment_failed` (never a false `201`), reserved inventory is compensated back, and a subsequent order succeeds again after Payment Service is restored — verified automatically in `tests/integration/test_payment_failure.py`, cluster left healthy afterward (a `restore_payment_service` fixture guarantees this even on test failure).
- `pytest tests/unit` — 22/22 passed. `pytest tests/integration` — 5/5 passed.

**Test strategy — what was actually done:**
- Unit (`tests/unit/`): 22 tests. Rather than mocking cross-service calls, each service's real `main.py` is loaded in-process and httpx calls are routed via `httpx.ASGITransport` to the *actual* downstream FastAPI app (see `tests/unit/conftest.py`) — so "unit" tests here exercise real business logic and real cross-service contracts (validation, insufficient-inventory handling, payment creation, database operations, and the payment-failure compensating-rollback path) without Docker or a cluster.
- Integration (`tests/integration/`): 5 tests, real HTTP over the network against the actually-deployed Kubernetes services (via `kubectl port-forward`, since Phase 0's kind config has no `extraPortMappings`) — proving real service discovery/DNS, a real successful transaction, state persistence across requests, a real insufficient-inventory rejection, and the genuine Payment Service outage/recovery scenario.

**Post-build adversarial review (same session):** a targeted adversarial review of the Phase 1 implementation found and fixed, within the existing architecture (no DECISIONS.md change — none of these were architectural):
- **CRITICAL**, fixed: `inventory-service` and `order-service` called `httpx.Response.raise_for_status()` unguarded outside any `try/except`, so any downstream status code other than the ones explicitly anticipated (200/404/409) — e.g. a `500` from a degraded dependency, exactly what Phase 4's saturation scenario will produce — crashed with an unhandled exception, returned a bare non-JSON "Internal Server Error" to the caller, skipped `order-service`'s compensating inventory release, and logged nothing structured. Reproduced live over real HTTP before fixing; both services now translate any unexpected status into a structured, logged failure with compensation still applied.
- **CRITICAL**, fixed: the integration test suite had no way to reset seeded state, so repeated runs permanently consumed the finite seeded stock (`product-003` was driven to `0` during this review's own testing) — a future run would fail on stock exhaustion, not a real regression. Added `POST /admin/reset` on `database` (reseed products, clear orders/payments — not a failure-injection mechanism), `scripts/reset.sh`, and a session-scoped autouse fixture in `tests/integration/conftest.py` that resets before the suite runs.
- **HIGH**, fixed: none of the five Deployments set `timeoutSeconds` on their probes (Kubernetes default: 1s), while `/ready` handlers do real dependency HTTP checks with 3s internal timeouts — a latent mismatch that would cause readiness flapping specifically when a dependency is slow (not down), which is exactly Phase 4's Database Saturation scenario. Added explicit `timeoutSeconds` to all probes; `order-service`'s `/ready` (which checks 3 dependencies) was also changed from sequential to concurrent checks to bound worst-case latency.
- **HIGH**, fixed: `transaction_id` was never sent from `inventory-service` to `database`, so `database`'s own `inventory_reserved`/`inventory_released` log events couldn't be correlated to a transaction at all. Now threaded through.
- **HIGH**, reported but not fixed (would require a new architectural decision on idempotency-key design, not implemented): no idempotency mechanism anywhere in the order flow — a retried `POST /orders` is indistinguishable from a new order and would double-reserve/double-charge.
- Full findings list (including MEDIUM/LOW/OBSERVATION items not requiring action before Phase 2) reported inline in the review session.

### Phase 2 — Real synthetic traffic *(complete)*

**Scope note (revises this section's original sketch):** the actual Phase 2 work order specified a concurrency-and-baseline experiment tool (configurable concurrent users × requests-per-user × interval, run gradually at 1/3/5/10 users, measuring latency/success-rate/status-distribution locally) rather than the originally-sketched long-running rate-controlled service with a start/stop/set-rate admin API. Built to the actual work order; the admin-API model was not needed and was not built. `TRAFFIC_USERS`/`TRAFFIC_RATE` env vars from `.env.example` (§4) were likewise not introduced — configuration is via CLI flags / Job env instead, since the tool is invoked per-experiment, not run as a persistent background service.

**What was actually built:**
- `simulator/traffic/generator.py` — an asyncio/httpx traffic generator (per D006). Each simulated user (`user-001`, ...) is its own coroutine sending `requests_per_user` `POST /orders` requests to `api-gateway`, spaced by a jittered interval, choosing a product via a seeded RNG weighted by seeded stock. Every request carries a generator-assigned `X-Request-Id`; the `transaction_id` returned by `order-service` is captured per-request. **No automatic retries** — a deliberate, explicit deferral given Phase 1's HIGH idempotency finding (see `docs/phase2-traffic-baseline.md`), not a silent decision; `DECISIONS.md` was not modified since no idempotency design was actually made.
- Runs both locally (CLI, e.g. against a port-forwarded gateway) and as a one-shot Kubernetes Job (`infrastructure/kubernetes/jobs/traffic-generator-job.yaml.template`, rendered per-run by `scripts/run-baseline.sh`) reaching `api-gateway` via its Service DNS name — no hardcoded pod IPs.
- `scripts/run-baseline.sh <users> [requests_per_user] [interval] [jitter] [seed]` — resets the database via Phase 1's existing `/admin/reset` (no second reset mechanism built), runs the Job, waits for completion, saves JSON results to `docs/baseline-results/`, cleans up the Job.
- The generator computes its own metrics locally (total/success/failure counts, HTTP status distribution, avg/p50/p95/p99 latency, RPS, timeout count) — nothing sent to OpenObserve (correctly out of scope until Phase 3).

**Dependencies:** Phase 1 gate passed — satisfied, reused the existing application and cluster without modification.

**Deliverables:** `simulator/traffic/*`, `infrastructure/kubernetes/jobs/traffic-generator-job.yaml.template`, `scripts/run-baseline.sh`, `tests/unit/test_traffic_generator.py`, `tests/integration/test_traffic_generator_live.py`, `docs/phase2-traffic-baseline.md`, `docs/baseline-results/*.json`.

**Quality gate — result:**
- Real HTTP traffic through `api-gateway` only, confirmed via live Job runs against the deployed cluster — verified.
- Baseline run at 1/3/5/10 concurrent users, gradually increased, each level's actual measured results in `docs/phase2-traffic-baseline.md` — verified. **100% success at 1/3/5/10 users on infrastructure grounds** (`server_error_count`/`timeout_count` were `0` at every level); at 10 users, seeded stock (35 units total) was exhausted, correctly classified as `409 insufficient_inventory` (a business condition) separately from infrastructure failure.
- No fabricated threshold: no infra-level error-rate degradation was found in the tested range (1–20 users, the 20-user level run as an explicit exploratory step beyond the required minimum). Latency degrades progressively and substantially with concurrency (p50 83ms→2231ms, p99 150ms→2869ms across 1→20 users) — reported as the real, measured characteristic, with the most likely architectural cause (Phase 1's single process-wide SQLite lock in `database`) stated as a plausible inference from existing code, not confirmed via new instrumentation.
- Escalation stopped at 20 users on resource-safety grounds (host free memory dipped to ~500Mi); cluster fully recovered afterward, 0 pod restarts throughout.
- `pytest tests/unit/test_traffic_generator.py` — 29/29 passed. `pytest tests/integration/test_traffic_generator_live.py` — 1/1 passed (live, real gateway). Full suite (`pytest tests/`) — **57/57 passed**, including all pre-existing Phase 1 tests, confirming Phase 1 remains fully functional.

**Test strategy — what was actually done:**
- Unit (`tests/unit/test_traffic_generator.py`, 29 tests): config validation, seed determinism (same seed → same product/quantity sequence; different seeds → different sequences), product-selection validity, status classification, percentile/aggregation math against hand-computed expected values, timeout handling, graceful shutdown (a `stop_event` correctly halts an in-flight user loop early), and — reusing Phase 1's ASGI-mount test pattern — real concurrent request-sending against the actual Phase 1 application chain in-process.
- Integration (`tests/integration/test_traffic_generator_live.py`, 1 test): the generator itself, live, over the network, against the actually-deployed `api-gateway`.

### Phase 3 — OpenTelemetry + OpenObserve *(complete)*

**What was actually built:**
- `telemetry.py` (duplicated per service, per the established `logging_utils.py` pattern — no shared package) initializes a `TracerProvider`/`LoggerProvider`/`MeterProvider` per service, exporting via OTLP/HTTP directly to OpenObserve (D003 — no Collector). Service identity: `payasam-api-gateway`, `payasam-order-service`, `payasam-inventory-service`, `payasam-payment-service`, `payasam-database` (a single consistent naming scheme — the pre-existing Phase 1 `SERVICE_NAME` constants were renamed to match, confirmed safe: no test asserted the old bare names).
- `opentelemetry-instrumentation-fastapi` (server spans) + `opentelemetry-instrumentation-httpx` (client spans, automatic W3C `traceparent` propagation) on every service that needs them; manual `db.*` spans in `database/db.py` (no maintained sqlite3 auto-instrumentation exists) with a specific `db.lock_wait_ms` attribute added to directly test Phase 2's unconfirmed SQLite-lock latency hypothesis.
- `logging_utils.py`'s `JsonFormatter` now injects `trace_id`/`span_id` into every log line while a span is active (confirmed absent otherwise); an OTel `LoggingHandler` additionally ships the same logs to OpenObserve.
- Minimal request metrics (`http.server.request.count`, `http.server.request.duration`; route + coarse status-class dimensions only) — tracing kept as the stated priority, not diluted by a large metrics surface.
- Kubernetes config: `telemetry-config` ConfigMap (endpoint/environment/version, rendered by `scripts/configure-telemetry.sh` which re-resolves the D011 gateway IP every time — never hardcoded) and `openobserve-credentials` Secret (created imperatively by `scripts/create-otel-secret.sh` from environment variables only — never written to a tracked file; the real password was typed directly by the user in their own shell, never by the agent, after the harness's own safety classifier correctly blocked the agent from doing so itself). Both referenced via `envFrom` with `optional: true` on every Deployment.

**Dependencies:** Phase 2 gate passed — satisfied, reused the existing application/cluster/traffic-generator without modification.

**Deliverables:** `services/*/telemetry.py`, updated `services/*/main.py` and `services/database/db.py`, `infrastructure/kubernetes/telemetry/telemetry-config.yaml.template`, `scripts/configure-telemetry.sh`, `scripts/create-otel-secret.sh`, `.env.example`, `tests/unit/test_telemetry.py`, `tests/integration/test_telemetry_live.py`, `docs/phase3-telemetry.md`, `docs/telemetry-verification/example-trace.txt`.

**Quality gate (PRD §35, plus this phase's explicit live-verification requirement) — result:**
- **Live-verified, not just health-checked** (explicitly required — a 200 from `/healthz` was already proven in Phase 0 and was declared insufficient here): a real order placed through the live cluster produced **one distributed trace of 37 spans across all 5 services**, pulled back from OpenObserve by `trace_id`, in the exact required causal shape (`api-gateway → order-service → {inventory-service → database, payment-service → database, database}`). Full span list in `docs/telemetry-verification/example-trace.txt`. This is a single propagated trace, not separate per-service traces — the quality gate's explicit defect condition does not apply.
- Log correlation live-verified: one `transaction_id` query against OpenObserve's logs stream returned 7 log lines from 4 different services, all sharing one `trace_id`.
- Metrics live-verified present in OpenObserve with the intended low-cardinality dimensions.
- Latency experiment repeated at 1/5/20 users (same methodology as Phase 2): trace-measured root-span latency agreed with generator-observed latency within ~5ms average at every level. `db.lock_wait_ms` data showed lock contention growing from ~0% of DB-operation time at 1 user to ~51–59% at 5 users to ~88–92% at 20 users — confirming Phase 2's SQLite-lock hypothesis as the dominant driver at higher concurrency specifically, while also showing it doesn't explain a ~5–8ms baseline floor present even with zero contention. Full numbers in `docs/phase3-telemetry.md` §7.
- Graceful degradation live-verified twice: all 5 pods started and passed health/readiness checks with `envFrom` pointing at a ConfigMap/Secret that didn't exist yet (before those were created); a real service pointed at an unreachable OTLP endpoint kept serving requests normally while printing the export failure to stderr.
- A real deployment defect was caught and fixed during this phase: `opentelemetry-instrumentation`'s dependency on `pkg_resources`/`setuptools` isn't present by default in the `python:3.13-slim` image (unlike the host, where it was already installed, masking the issue in local testing) — all 5 pods crash-looped identically on first deploy; fixed by adding `setuptools` to each service's `requirements.txt`. A concrete demonstration of why this phase's live-cluster verification step is a real quality gate, not a formality.
- `pytest tests/unit/test_telemetry.py` — 9/9 passed. Full suite (`pytest tests/`) — **66/66 passed** (67 collected, 1 live-OpenObserve-credentials test skips cleanly when credentials aren't in the runner's own environment, by design — see below), confirming Phase 1/2 remain fully functional.

**Idempotency/credentials note:** per this phase's task instructions, the actual OpenObserve password was never typed by the agent — the harness's safety classifier blocked an attempted use of the already-disclosed value in a command, correctly, and the user ran the credential-creating and (optionally) the live-verification-test commands themselves in their own shell instead. This is now the established pattern for any future step needing real OpenObserve credentials.

**Test strategy — what was actually done:**
- Unit (`tests/unit/test_telemetry.py`, 9 tests): tracer initialization, graceful no-op with no endpoint configured, graceful handling of a malformed endpoint, service-identity resource attributes, span creation/attributes, nested-span trace-id sharing, real `database` operation spans (including `db.lock_wait_ms`), full-chain trace-context propagation (verified at the real `traceparent` header level, not just via the global tracer-provider singleton — deliberately, since loading multiple services in one test process makes that singleton unreliable as a test signal), and log/trace correlation.
- Integration (`tests/integration/test_telemetry_live.py`, 1 test, skips cleanly without live credentials): places a real order through the live gateway, then queries OpenObserve's real search API for the resulting logs and trace, asserting cross-service correlation and a single root span. The equivalent live check was additionally performed manually in this session with full evidence (see quality gate results above), independent of whether this specific automated test has been executed in any given environment.

### Phase 4 — Controlled failure injection & observability validation *(complete)*

**Scope note (revises this section's original single-scenario sketch):** the actual Phase 4 work order specified a broader controlled-fault-injection framework (four scenarios: service latency, service error, database latency/contention, service unavailable — labeled A–D) validated against real OpenObserve telemetry with explicit root-cause-vs-propagated-symptom localization, rather than the originally-sketched single Database Saturation scenario with a dedicated `failure-controller`/`inject_db_saturation()` API. Built to the actual work order. `tests/end-to-end/` was not created — the equivalent proof (real fault → real telemetry → real recovery, baseline-relative, not hardcoded thresholds) was performed as four live experiments against the deployed cluster plus 12 automated `tests/unit/test_faults.py` tests, documented in full in `docs/phase4-failure-experiments.md`.

**What was actually built:**
- `faults.py` (byte-identical across `database`/`payment-service`, matching the established `logging_utils.py`/`telemetry.py` duplication pattern): `PAYASAM_FAULT_PAYMENT_LATENCY_MS`, `PAYASAM_FAULT_PAYMENT_ERROR_RATE`, `PAYASAM_FAULT_DB_LATENCY_MS` — all default `0`/off, read once at process startup, toggled via `kubectl set env` (a normal rolling restart, no new infrastructure).
- Scenario D (service unavailable) required no code at all — `kubectl scale deployment/payment-service --replicas=0/1`, the exact mechanism already proven in Phase 1's `test_payment_failure.py`.
- `scripts/set-fault.sh`, `scripts/clear-faults.sh`, `scripts/otel-query.sh` (the last reads the querying pod's own already-resolved `OTEL_EXPORTER_OTLP_ENDPOINT` rather than hardcoding the D011 gateway IP).
- `db.py`'s `_traced_op` (built in Phase 3) was extended with an `apply_fault` parameter and the DB-latency fault is applied *inside* the held lock specifically so the resulting `db.lock_wait_ms` telemetry can separate "database operation delay" from "SQLite lock waiting" — the exact distinction this phase required, not a re-assertion of Phase 3's finding.

**Dependencies:** Phase 3 gate passed — satisfied, reused the existing application/telemetry/traffic-generator without modification (confirmed: three of five service Docker images are byte-identical to their Phase 3 build, since only `database` and `payment-service` needed changes).

**Deliverables:** `services/{database,payment-service}/faults.py`, updated `services/payment-service/main.py` and `services/database/{main.py,db.py}`, `scripts/{set-fault,clear-faults,otel-query}.sh`, `tests/unit/test_faults.py`, `docs/phase4-failure-experiments.md`, `docs/telemetry-verification/scenario-{a-latency,b-error,c-database,d-unavailable,cascading}.txt`.

**Quality gate — result (full detail in `docs/phase4-failure-experiments.md`):**
- All four required scenarios, each with a fresh baseline, one fault, live traffic, OpenObserve evidence, and confirmed recovery: **A (latency)** p50 +501ms vs. 500ms injected; **B (error)** 15/15 → 0/15 deterministic 502s, inventory correctly released; **C (database)** cleanly separated "operation delay" (1 user, `lock_wait_ms=0.00`) from "lock waiting" (5 users, `lock_wait_ms` 505–694ms, *exceeding* the 200ms base fault); **D (unavailable)** no `payasam-payment-service` span exists at all, `order-service`'s client span carries a literal `ConnectError` message.
- Root cause independently distinguished from propagated symptoms in every scenario using only span self-time/status/topology — never using the known fault as evidence (see §9 of the phase doc for the summary table).
- Span error semantics verified working both via explicit manual `set_status(ERROR)` (payment-service's own fault path) and via FastAPI/httpx auto-instrumentation's automatic 5xx→`ERROR` marking (order-service, api-gateway — zero manual code).
- Optional cascading scenario completed (A-D all passed first): one root fault (payment latency) → two further propagated hops, and a real, measured contrast with Scenario C showing *why* the database fault amplifies far more under concurrency (shared lock) than the payment fault does (independent `asyncio.sleep`).
- `pytest tests/unit/test_faults.py` — 12/12 passed. Full suite — **78 passed, 1 skipped** (pre-existing, unrelated to this phase).
- All 5 pods healthy, 0 unexpected restarts, node Ready, OpenObserve untouched and healthy at the end of the phase; every fault fully cleared and confirmed absent via each service's own startup log.

**Defect found and fixed:** a real test-infrastructure bug (not production) — the two `faults.py` files were initially written with *different* content, which the shared in-process test harness's module-name caching silently broke (`payment-service`'s code intermittently got `database`'s `faults` module). Fixed by making them byte-identical and clearing both `faults` and `db` from the test session's module cache before each fault test. Full account in the phase doc §11.

**Operational lesson (not a regression):** re-running traffic immediately after `kubectl scale ... --replicas=1` (before `order-service`/`api-gateway`'s own readiness probes re-poll) produces spurious timeouts — a real readiness-propagation race, not a fault-injection bug. Documented for later demo-script phases to account for.

### Phase 5 — Payasam Intelligence *(complete, deliberately scoped down — see D014)*

**Scope note (revises this section's original task list):** built to a
scoped-down version of this plan, decided directly with the user rather
than the original full-Intelligence-layer sketch: **no** incident-
correlation store and **no** optimization/FinOps engine (PRD §20) —
neither was needed for the immediate demo narrative and both remain
legitimate future work if explicitly requested. What *was* built, and
later extended to full completeness (see below), is the business-impact
engine.

**What was actually built:**
- `payasam/backend/impact.py` — pure functions (`compute_impact`,
  `distinct_affected_users`, `incident_duration_minutes`) taking
  observed OpenObserve query results + `business_metadata.yaml`
  assumptions, independently unit-tested without a live cluster.
- `payasam/backend/main.py`'s `GET /impact` — queries OpenObserve
  directly for real `order-service` failure events (never trusts a
  caller-supplied count), returns `failed_transactions`,
  `estimated_revenue_at_risk_inr`, `estimated_downtime_cost_inr`,
  `business_criticality`, and **`affected_users`** (a real distinct-
  `user_id` count — see below), every number labeled
  `ESTIMATED / SIMULATED BUSINESS IMPACT` with assumptions shown
  alongside (PRD §19/§45).
- **`affected_users` was initially deferred, then implemented for
  real (D019):** `user_id` wasn't present in exported telemetry when
  Phase 5 first shipped (a real Phase 1 gap, not a Phase 5 shortcut) —
  the endpoint honestly reported `null` with a note rather than
  fabricate a number. Later closed by adding `user_id` to the shared
  `logging_utils.py` field whitelist (all 5 services, kept
  byte-identical) and to `order-service`'s log calls, plus a
  `distinct_affected_users` function counting real distinct users
  among the failures — with an explicit defensive fallback for a
  freshly-initialized OpenObserve instance that hasn't seen the field
  yet (caught live: querying an unknown column 400s until the schema
  learns it from the first real record).
- A real display-consistency bug was caught by the user actually
  reading the deployed numbers (`0.0324 × 800` showing `25.93` instead
  of `25.92`) and fixed at the formula level (round duration before
  multiplying, not after) — see D018.

**Dependencies:** Phase 4 gate passed — satisfied.

**Quality gate — result:** unit-tested (formulas + affected_users
distinct-counting, including the zero-failure and missing-user_id edge
cases) and live-verified end-to-end multiple times against real
injected faults, including the specific rounding and distinct-user
scenarios that were found to be wrong and then fixed.

### Phase 6 — Payasam UI *(complete, deliberately scoped down — see D015/D017/D018)*

**Scope note (revises this section's original task list):** built to a
scoped-down version decided directly with the user: **no** role-
selection screen and **no** separate Technical/Business/Incident-
Investigation *pages* — instead, a single dashboard with all three
perspectives (Technical/Business/Remediation) visible simultaneously,
per explicit user direction that switching tabs hid too much of the
story during a live demo.

**What was actually built:**
- `payasam/frontend` — React + Vite (per D008), a single page: a status
  header, a Failure Simulator panel (4 fault buttons + Recover + a
  self-contained "Generate Test Traffic" button — see below), a live
  activity feed narrating both user actions and system-detected state
  changes, and the three-panel dashboard, all polling every 3s.
- `payasam/backend/faults_control.py` + a scoped Kubernetes RBAC grant
  (`infrastructure/kubernetes/payasam-backend-rbac.yaml`) — the UI
  triggers real fault injection/recovery over HTTP, using the same
  mechanism `scripts/set-fault.sh`/`clear-faults.sh` already established
  (D007), not a new failure-injection mechanism.
- `POST /traffic/generate` — added after live testing showed fault
  injection alone produces zero requests, so the Business panel
  correctly showed all zeros with nothing to measure; this closes that
  gap without a second terminal window.
- Design pass (D018): the reserved status palette (good/warning/
  serious/critical) applied only to state indicators via a `StatusDot`
  component, never to buttons or plain text; sparkline trends on the
  two headline Business numbers; native tooltips spelling out every
  formula; explicit "assumption, not measured" labeling on configured
  (non-live) values; "View in OpenObserve" links to verified-real page
  paths for deeper evidence.

**Real bugs found and fixed by live testing, not code review alone:**
an inverted `faults_readable` flag that always reported `unknown`
status; the Kubernetes `/scale` subresource reporting `replicas=0` as
`None`; a genuine `409 Conflict` from two rapid fault changes on the
same Deployment (fixed with re-read-and-retry); the display-rounding
inconsistency above; a test that used a fixed literal `user_id` and
therefore silently passed against stale data from its own prior run.

**Dependencies:** Phase 5 gate passed — satisfied.

**Quality gate — result:** a human can complete the full loop (open →
generate traffic → inject → watch Technical/Business/Remediation update
live → recover → see recovery) with zero CLI use, using only the
"Generate Test Traffic" and fault buttons. **Not yet independently
click-tested in a live browser by the implementing agent** (no browser
tooling connected during this build) — verified instead via the
production build succeeding, the deployed bundle's contents, and the
real nginx proxy round-tripping live GET/POST calls end-to-end. The
user is expected to do one manual click-through before presenting.

### Phase 7 — Full system verification *(partial — see below)*

**What was actually done instead of the full 3-manual-run protocol:**
given time constraints ahead of the actual hackathon, this was narrowed
to (a) the full automated suite kept green throughout every change
(113 tests as of the last documented run — see `DECISIONS.md` D014–D019
for the phases that added to it), and (b) one scripted, automated timing
pass through the real demo loop (inject → real failure → business
impact visible → recover), reported as measured numbers, not estimated
ones:

| Step | Measured |
|---|---|
| Fault injected → visible in `/status` | ~0.3s |
| Fault active → real failure → visible in `/impact` | ~12.5s (dominated by OTLP batch export + OpenObserve ingestion, not application logic) |
| Recover triggered → `/status` reports healthy | ~0.5s (reflects the status check passing quickly, not necessarily the full pod rollover completing — a full rollout was separately observed taking several seconds longer elsewhere in this project) |

A genuine, real operational finding from this same measurement pass:
repeated demo/test runs exhaust the 35 total seeded product units
(`insufficient_inventory`, a business condition, is correctly excluded
from failure counts) — `scripts/reset.sh` before each real demo run is
required, not optional, and is not automatic.

**Not done:** the full ≥3-manual-click-through-run protocol this
section originally specified — deferred to the user, who has direct
browser access this agent does not.

**Dependencies:** Phase 6 gate passed — satisfied for the automated
portion; the manual portion remains the user's to complete.

---

## 6. Cross-Cutting Quality Gate Checklist (applied at the end of every phase, PRD §40)

1. `pytest` (relevant subset for that phase) — green.
2. Relevant integration test — green.
3. Static checks — `ruff`/`mypy` for Python (to be added as a dev-dependency in Phase 1, not yet installed — noted as a small justified addition per PRD §43) and `tsc --noEmit` for the frontend once it exists.
4. System actually started via the phase's start script.
5. Manual smoke test performed and its result stated plainly (including "not verified" if something couldn't be checked — PRD §41).
6. Diff against this plan / the PRD — flag any deviation explicitly rather than silently drifting.

---

## 7. Risks / Open Items to Revisit During Implementation

- **Memory budget (§1.5)** is the single biggest execution risk. If the full stack doesn't fit in ~4–5Gi at Phase 1, the mitigation is reducing per-pod limits further and/or asking the user to free memory before demo runs — not adding infrastructure complexity.
- **kind↔host connectivity (§2.5)** — resolved and experimentally verified in Phase 0 (see D011 in `DECISIONS.md` and `gcp-cluster/docs/phase0-networking.md`). The gateway IP is not guaranteed stable across cluster recreation, so it must always be re-resolved at setup time, never hardcoded; the fallback (`extraPortMappings` in the kind cluster config) remains available if the gateway-IP approach proves flaky under later phases' actual traffic.
- **Static-analysis tooling** (`ruff`, `mypy`, `eslint`) isn't installed yet; will be added in Phase 1/6 respectively as minimal dev dependencies, per PRD §43's justification requirement.
