# DECISIONS.md

Architectural decision log for Payasam. Only significant, already-established
architectural/implementation decisions are recorded here — not every
implementation detail. Decisions are numbered in the order they were
established, not by phase.

---

## D001 — Local Kubernetes (kind) instead of Docker Compose

**Decision:** Use a local Kubernetes cluster, specifically `kind`, as the
runtime for the simulated production environment.

**Context/problem:** The project needs a real local, GCP-like production
environment with multiple dependent workloads, and KubeView will be used as
a supporting infrastructure-topology visualization during the hackathon
demo. `kubectl`, `kind`, and `minikube` are all installed on the machine;
`k3d`, `k3s`, and `helm` are not. No cluster was running at discovery time.

**Reason:** Kubernetes provides the production-like workload environment the
PRD requires (§7–§8) and gives KubeView something real to visualize. Between
the two available Kubernetes options, `kind` runs its control plane as a
single Docker container with no additional VM/driver layer, comes up faster,
and is lighter on memory than `minikube` — a real constraint on this machine
(see D012-adjacent memory note in IMPLEMENTATION_PLAN.md §1.5). A stale
`kind-payasam` kubeconfig context found during discovery also suggests `kind`
was the prior intended choice.

**Alternatives considered:**
- Docker Compose — rejected: PRD explicitly wants a real Kubernetes
  environment and KubeView integration, which Compose doesn't provide.
- `minikube` — rejected: heavier memory/driver overhead than `kind` for no
  additional benefit in this use case.
- `k3d`/`k3s` — not installed; would add a new dependency where `kind` is
  already available and sufficient.

**Consequences/trade-offs:**
- More setup complexity than Docker Compose.
- First `kind create cluster` requires pulling a node image over the
  network (confirmed available) and takes several minutes.
- Cluster-to-host networking on native Linux Docker requires an explicit
  connectivity approach (see D011) rather than "just working."

**Status:** Accepted — established during planning; directly follows from
PRD §7, §8, §26 (Kubernetes environment + KubeView requirement) and confirmed
available tooling on this machine.

---

## D002 — OpenObserve as the observability backend

**Decision:** Use the existing local OpenObserve container as the sole
observability backend (logs, metrics, traces, search, alerting).

**Context/problem:** The project must provide centralized logs, metrics,
traces, visualization, and alerting without GCP credits or a rebuilt
observability platform. OpenObserve is explicitly named in the PRD as an
existing dependency running on host port 5080.

**Reason:** OpenObserve already provides everything PRD §13 requires
(ingestion, log/metric/trace visualization, alerting) and is already part of
the project environment. The PRD explicitly forbids rebuilding this
capability (§1.3, §13).

**Alternatives considered:**
- SigNoz — rejected: not part of the existing environment, would duplicate
  a capability OpenObserve already provides.
- Grafana + Loki + Prometheus — rejected: PRD §1.3 explicitly excludes
  Grafana as another visualization platform.
- Managed GCP observability services — rejected: PRD explicitly forbids any
  GCP dependency (§1.3, §49).

**Consequences/trade-offs:**
- Adds a local infrastructure dependency that must be running before the
  rest of the system is useful.
- Avoids GCP costs entirely.
- Payasam must build intelligence *on top of* OpenObserve (correlation,
  business impact, optimization) rather than reimplementing generic
  observability functionality (PRD §16).

**Status:** Accepted — explicitly specified by the PRD (§5, §13).

---

## D003 — Direct OTLP export to OpenObserve, no standalone OTel Collector

**Decision:** Each application service's OpenTelemetry SDK exports logs,
metrics, and traces directly via OTLP/HTTP to OpenObserve's native OTLP
ingestion endpoint. No standalone OpenTelemetry Collector deployment is
introduced.

**Context/problem:** PRD §12 requires OTel-instrumented logs/metrics/traces;
§1.3 explicitly warns against "unnecessary orchestration layers" and
duplicate observability pipelines; the machine has a tight memory budget
(~4–5Gi realistically available for the whole stack).

**Reason:** OpenObserve accepts OTLP/HTTP directly, so a Collector adds a
process, a memory footprint, and an operational layer without adding
capability at this scale (10–50 simulated users, low RPS). This is the
simplest option that satisfies PRD §12–13.

**Alternatives considered:**
- Standalone OpenTelemetry Collector (DaemonSet or sidecar) — rejected for
  now: adds memory overhead and complexity not justified at this traffic
  volume; PRD §43 requires justifying added components.

**Consequences/trade-offs:**
- Fewer moving parts, lower memory usage.
- No centralized point for batching/retry/fan-out if telemetry volume or
  destinations grow later — would need to be introduced if direct export
  proves unreliable under load.

**Status:** Accepted — established during planning as the simplest option
consistent with PRD §1.3 and the documented memory constraint. Not yet
exercised against a live cluster; will be confirmed empirically in Phase 3.

---

## D004 — Application services in Python + FastAPI

**Decision:** All application services (`api-gateway`, `auth`, `users`,
`orders`, `payments`, `inventory`, `notifications`, `database`) are built as
small FastAPI + uvicorn services in Python.

**Context/problem:** The PRD leaves language/framework choice unspecified.
Discovery found the machine's Python installation already has FastAPI,
uvicorn, `httpx`, `aiohttp`, and the full `opentelemetry-*` SDK/exporter
family pre-installed. Node.js and an outdated Go (1.18.1, from 2022) are
also present.

**Reason:** The pre-installed OpenTelemetry SDK/exporter stack is the
strongest environmental signal for a Python-based service stack, and FastAPI
needs no compilation step, which matters for iterating on ~8 structurally
similar small services. Keeping every service on one stack also directly
serves PRD §1.1's "understandable and maintainable by one developer" goal.

**Alternatives considered:**
- Node.js/Express — viable, also installed, but no pre-installed OTel SDK;
  would require adding a new dependency chain where Python already has one.
- Go — rejected: toolchain on this machine is outdated (1.18.1) and nothing
  in the PRD requires Go specifically.
- Mixed-language services — rejected: adds cross-language operational
  complexity (multiple base images, multiple test tooling) with no PRD
  requirement driving it.

**Consequences/trade-offs:**
- Single-language stack keeps the system simple to reason about and test.
- FastAPI/uvicorn processes have a higher baseline memory footprint than a
  compiled Go binary — relevant given the tight memory budget; mitigated by
  explicit small per-pod resource limits (Phase 1).

**Status:** Accepted — this PRD requirement is unspecified in the document
itself; the choice was made per PRD §1.2 ("choose the simplest reasonable
option and document the decision") based on concrete environment signals.

---

## D005 — "Database" is a controllable FastAPI+SQLite service, not real Postgres

**Decision:** The `database` component is a small FastAPI service backed by
SQLite, with an admin-only endpoint that can inject deterministic latency,
error rate, and connection-slot contention, and remove it on recovery. It is
not a real PostgreSQL instance.

**Context/problem:** PRD §1.3 excludes PostgreSQL "unless later proven
necessary." PRD §14 (Scenario 1) requires the "safest and most deterministic
method" of causing database saturation, and the failure must cause genuine
downstream degradation (§3) — not a faked incident record.

**Reason:** A small controllable service lets `payment` genuinely call a
real dependency and genuinely experience the injected slowness/errors (real
request path, real telemetry) while keeping the failure mechanism
deterministic and safely reversible — which is materially harder to
guarantee against real Postgres connection-pool exhaustion under a
hackathon timeline. This satisfies PRD §3's "no fake incidents" rule at the
telemetry/causality level; the synthetic nature of the dependency itself is
documented explicitly rather than hidden.

**Alternatives considered:**
- Real PostgreSQL with actual connection-pool exhaustion — rejected for the
  initial implementation: harder to make deterministic/reversible quickly;
  explicitly de-prioritized by PRD §1.3 unless proven necessary.
- Simulating the incident directly (writing a fake "database_error=true"
  record) — explicitly prohibited by PRD §3.

**Consequences/trade-offs:**
- The failure mechanism is a controlled synthetic dependency rather than
  genuine database internals; this is a documented simulation assumption,
  not a hidden shortcut.
- If this proves insufficiently convincing for the demo, promoting
  `database` to real Postgres or real SQLite-file lock contention is an
  isolated, contained change (nothing else depends on the mechanism).

**Status:** Accepted — established during planning as the simplest option
satisfying PRD §1.3, §3, and §14 simultaneously. Implementation and
real-world validation of the "deterministic and reversible" claim happens in
Phase 4.

---

## D006 — Traffic generator: custom asyncio script, not Locust

**Decision:** Build a small custom asyncio-based traffic generator
(`httpx.AsyncClient`), controllable via a minimal admin API (start/stop/set
rate), instead of adopting Locust.

**Context/problem:** PRD §11 requires a configurable synthetic traffic
generator producing real HTTP requests with identifiable context
(`user_id`, `request_id`, `transaction_id`, etc.). Locust is not installed on
the machine.

**Reason:** PRD §43 requires justifying new dependencies; Locust would add a
web UI and dependency footprint not needed here, when a ~150-line script
using already-installed `httpx` fully satisfies the requirement (start,
stop, change volume, per-request identifiers, trace propagation).

**Alternatives considered:**
- Locust — rejected: not installed, adds unneeded UI/dependency surface for
  this scale of traffic (10–50 users).
- k6 — rejected: not installed, introduces a new language/runtime (Go/JS)
  when Python is already the service-stack language.

**Consequences/trade-offs:**
- No built-in web UI or distributed load-testing features — acceptable,
  since the PRD explicitly caps traffic at a small, controlled scale (§4).
- One more piece of custom code to maintain, but small and directly
  testable.

**Status:** Accepted — established during planning per PRD §1.2/§43.

---

## D007 — Failure injection via per-service admin HTTP endpoints

**Decision:** Each application service exposes a local-only admin endpoint
(e.g. `POST /admin/config`) accepting latency/error-rate/CPU-burn
parameters. A single `failure-controller` component calls these endpoints
(and, for the deployment-regression scenario, `kubectl`) to change actual
running service behavior. No component ever writes an incident record
directly.

**Context/problem:** PRD §3 and §14 require failure scenarios to modify real
running workload behavior, propagate through real dependencies, and be
safely reversible, without any fake incident injection.

**Reason:** Admin endpoints on each real service are the most direct way to
make a real running process behave differently (real latency added to a
real code path, real CPU actually consumed) while remaining deterministic
and reversible, matching PRD §14's explicit "choose the safest and most
deterministic method" instruction.

**Alternatives considered:**
- OS/cgroup-level manipulation (e.g. `tc` for network latency, cgroup CPU
  quotas via `kubectl` patches only) — viable for CPU saturation (Scenario
  2) but less practical for deterministic database-specific latency;
  admin-endpoint approach chosen as the uniform mechanism across scenarios
  where practical.
- Chaos-engineering tooling (e.g. Chaos Mesh, Litmus) — rejected: PRD §1.3
  discourages unnecessary orchestration layers; a dedicated chaos platform
  is disproportionate to a 5-scenario hackathon demo.

**Consequences/trade-offs:**
- Admin endpoints are unauthenticated, acceptable only because the whole
  system is local-only and not internet-exposed; must not be carried into
  any non-local deployment.
- Keeps the failure controller simple and testable via HTTP calls alone.

**Status:** Accepted — established during planning, directly implements PRD
§3 and §14's required causal chain.

---

## D008 — Frontend: React + Vite

**Decision:** Build the Payasam UI as a React application using Vite (no
Create React App, no additional state-management/UI-kit libraries).

**Context/problem:** PRD §21–§24, §46 require several distinct,
live-updating, role-specific views (role selection, Technical Operations,
Business/Management, Incident Investigation) with a clear narrative
progression, while §1.3 warns against "unnecessary frontend frameworks."

**Reason:** The amount of real interactivity and shared live state across
multiple views makes a component framework the simplest option that avoids
fighting the requirement, rather than a complexity to avoid. React + Vite
(vs. the already-globally-installed but effectively deprecated
Create-React-App) is the lighter, faster modern default, used minimally:
plain `fetch` polling against the Payasam backend, no Redux/GraphQL/UI kit.

**Alternatives considered:**
- Server-rendered HTML + `fetch` polling (no framework) — considered and
  rejected: would need hand-rolled client-side state management across
  three interlinked views to hit the "live updates" and "role-specific"
  requirements, effectively reinventing what a minimal component framework
  already gives for free.
- Create React App — rejected: globally installed but upstream-deprecated;
  Vite is the lighter, currently-recommended equivalent.

**Consequences/trade-offs:**
- Introduces a Node/npm build step for the frontend.
- Kept deliberately minimal (no auxiliary libraries beyond React itself and
  routing if needed) to honor PRD §1.3's "no unnecessary frontend
  frameworks" instruction in spirit.

**Status:** Accepted — established during planning per PRD §1.2, as the
simplest reasonable option for an otherwise-unspecified requirement.

---

## D009 — Kubernetes manifests: plain YAML + built-in Kustomize, no Helm

**Decision:** Use plain Kubernetes YAML manifests (Deployment + Service per
component) organized under `infrastructure/kubernetes/`, composed with
`kubectl`'s built-in Kustomize support. No Helm.

**Context/problem:** `helm` is not installed on the machine; the project has
~9 small, structurally similar services to deploy.

**Reason:** Kustomize is already bundled with the installed `kubectl`
(v1.33.2 includes Kustomize v5.6.0), so it requires no new dependency and is
sufficient for this scale. PRD §43 requires justifying new dependencies
before adding them.

**Alternatives considered:**
- Helm — rejected: not installed; templating power it offers isn't needed
  for ~9 near-identical manifests.

**Consequences/trade-offs:**
- Less templating power than Helm for per-environment value substitution;
  not needed since this is a single-environment (local) deployment.

**Status:** Accepted — established during planning per PRD §43.

---

## D010 — OpenObserve credentials/configuration handling

**Decision:** OpenObserve connection details (`OPENOBSERVE_URL`,
`OPENOBSERVE_ORG`, `OPENOBSERVE_USERNAME`, `OPENOBSERVE_PASSWORD`) are
supplied to all Payasam/service code exclusively via environment
variables/a local, git-ignored `.env` file. A `.env.example` with
placeholder (non-functional) values is committed; the real password is
never written into source, manifests, scripts, or documentation. The
running OpenObserve container is treated as pre-existing local
infrastructure that the implementation verifies but does not automatically
create, recreate, or replace.

**Context/problem:** PRD §5 and §29 explicitly require externalized
configuration and forbid hardcoded credentials. During discovery, the
original container (created with a weak password, `password123`) was found
crash-looping because OpenObserve's password-strength policy rejected it.
That container has since been recreated by the user directly, outside of
this planning process, with a policy-compliant local password — the
implementation must not embed or reference that literal value anywhere.

**Reason:** This is a direct, non-negotiable PRD requirement (§5, §29), and
the credential-handling approach doesn't change based on which password is
currently in use — it must never be written into tracked files regardless.

**Alternatives considered:**
- Hardcoding credentials for demo convenience — explicitly forbidden by PRD
  §5.

**Consequences/trade-offs:**
- Requires every developer/demo run to have a correctly populated local
  `.env` before the system will connect to OpenObserve.
- Slightly more setup friction than hardcoding, traded for correctness and
  PRD compliance.

**Status:** Accepted — explicitly specified by the PRD (§5, §29); container
lifecycle ownership (Payasam verifies, never auto-recreates) reconfirmed
directly by the user in this task.

---

## D011 — Host connectivity from `kind` on native Linux Docker

**Decision:** Workloads inside the `kind` cluster reach the host-run
OpenObserve container via the **IPv4 gateway address of the `kind` Docker
bridge network**, resolved dynamically (never hardcoded) with:

```bash
docker network inspect kind --format '{{json .IPAM.Config}}' \
  | jq -r '.[] | select(.Subnet | contains(":") | not) | .Gateway'
```

On the machine this was verified on, that address is `172.18.0.1`; it is
resolved fresh each time rather than assumed, since it can differ across
machines or cluster recreations. `host.docker.internal` is not used.

**Context/problem:** OpenObserve runs as a plain host Docker container
(`-p 5080:5080`), not inside the `kind` network (it's on the unrelated
default `bridge` network, 172.17.0.0/16, while `kind` is 172.18.0.0/16).
This machine runs native Docker Engine on Linux (confirmed — not Docker
Desktop), where `host.docker.internal` does not resolve automatically
inside containers, including `kind` nodes.

**Reason:** Docker's published-port DNAT (`-p 5080:5080`) binds the port on
all of the host's interfaces, including the `kind` bridge's own gateway
interface, regardless of which Docker network the published container
itself sits on. A pod's egress is routed out through its node container,
which is a peer on the `kind` bridge network and can reach that gateway
address directly. This requires no new dependency and no changes to how
OpenObserve is run.

**Alternatives considered:**
- `host.docker.internal` — rejected: **experimentally confirmed** not to
  resolve inside a pod on this setup (`curl: (6) Could not resolve host`),
  not just assumed to fail.
- Attaching OpenObserve's container to the `kind` network and referencing
  it by container name — rejected: Kubernetes pods use the CNI overlay
  (kindnet), not Docker's embedded DNS, so container-name resolution isn't
  reliably available to pods even if the container joins the network.

**Consequences/trade-offs:**
- The gateway IP must be re-resolved whenever the cluster is recreated (it
  is not guaranteed stable across `kind delete/create`); consuming code
  (from Phase 3 onward) must treat it as dynamic configuration, not a
  constant.
- **A bug was caught during verification, not before it:** the `kind`
  network is dual-stack (IPv4 + IPv6). The mechanism as originally proposed
  in `IMPLEMENTATION_PLAN.md` §2.5 (`docker network inspect kind --format
  '{{(index .IPAM.Config 0).Gateway}}'`) took whatever config was at index
  0, which on this machine is the **IPv6** gateway, not IPv4. The corrected
  version explicitly filters for the non-IPv6 subnet (shown above). This is
  exactly why the mechanism was required to be proven experimentally rather
  than accepted on paper.
- If this approach proves unreliable in a later phase, PRD-compliant
  fallbacks (e.g. `extraPortMappings` in the kind cluster config) remain
  available without changing anything else in the architecture.

**Experimental verification (Phase 0, this session):** performed via
`gcp-cluster/scripts/verify-openobserve-connectivity.sh` — a temporary pod
in the `payasam` namespace, running `curlimages/curl`, called
`http://172.18.0.1:5080/healthz` from inside the cluster twice
independently; both calls returned `HTTP 200` with body `{"status":"ok"}`.
The temporary pod was deleted after each run; the cluster's node and all
system pods were confirmed `Ready`/`Running` afterward. Full detail in
`gcp-cluster/docs/phase0-networking.md`.

**Status:** Accepted — experimentally verified in Phase 0 (see evidence
above), superseding the "Proposed" status this decision carried during
planning.

---

## D012 — Order transaction consistency: compensating inventory release on downstream failure

**Decision:** `order-service` reserves inventory for every item *before*
charging payment (matching the PRD's stated order of operations). If
payment then fails for any reason — declined, or Payment Service
unreachable/erroring — `order-service` calls `inventory-service` to
release every item it already reserved, before returning the failure to
the caller. A `failed`-status order row is still persisted for audit
visibility (see D005/database schema), but reserved stock is never left
silently stuck decremented.

**Context/problem:** discovered while implementing Phase 1's transaction
flow (PRD §10's example transaction reserves inventory, then charges
payment). With no compensating step, a payment failure after a successful
inventory reservation would leave `product.quantity` permanently
decremented for an order that never completed — a real, observable
inconsistency, not a cosmetic one, and exactly the kind of thing PRD §3
("no fake dashboard values", real causal behavior) implicitly rules out in
spirit: state must reflect what actually happened, including on failure
paths.

**Reason:** this is the simplest correct fix — no distributed transaction
coordinator, no saga framework, just one additional HTTP call
(`inventory-service`'s existing `POST /release`, already present because
Phase 4's saturation scenario will need release/recovery semantics
anyway) in the failure branch. It keeps `order-service`'s error handling
honest: "the order failed" and "the system's state is consistent with
that" are both true simultaneously.

**Alternatives considered:**
- No compensation (leave inventory decremented on payment failure) —
  rejected: produces a real, silent data inconsistency; also would have
  made the Phase 1 failure test (`tests/integration/test_payment_failure.py`)
  unable to assert `after_failure == before`, which is exactly the
  property that proves the failure is handled honestly rather than
  half-applied.
- Reserve inventory only *after* payment succeeds (reordering the steps to
  avoid needing compensation at all) — rejected: contradicts the PRD's
  explicit example ordering (§10: reserve/check inventory, then process
  payment) and would just move the same problem elsewhere (a
  payment-succeeds-but-inventory-then-fails race).

**Consequences/trade-offs:**
- One more network call in the failure path, only when needed.
- Compensation is fire-and-forget best-effort in Phase 1: if the release
  call itself fails, that failure is logged (`event_type: release_failed`)
  but doesn't change the response already being returned to the caller
  (the order still correctly fails either way). A stuck reservation from a
  double failure is a known, narrow edge case, acceptable at this scale
  and not silently hidden — it would surface as a logged error.
- This pattern is what Phase 4's Database Saturation scenario will
  exercise for real (payment/database calls failing under injected
  latency/errors) — Phase 1 already proves the compensating path works
  under a genuine outage, not just a mocked one.

**Status:** Accepted — implemented and verified in Phase 1
(`tests/unit/test_order_service.py::test_payment_failure_releases_reserved_inventory_and_fails_order`
and `tests/integration/test_payment_failure.py`, both passing against real
service code / a real cluster respectively).

---

## D013 — Alert destination: a public echo endpoint, not a local no-op

**Decision:** The two OpenObserve alerts created by
`gcp-cluster/scripts/configure-alerts.sh` (`payasam-any-service-error`,
`payasam-payment-degraded`) use a single HTTP destination
(`payasam-local-sink`) pointed at `https://httpbin.org/post` — a public,
well-known echo endpoint requiring no account/signup — rather than a
local address.

**Context/problem:** The hackathon problem statement explicitly requires
"set alert thresholds" / "error alerts," which nothing built through
Phase 4 touched. OpenObserve is already the designated alerting surface
(D002), so this is config, not new architecture. The first attempt
pointed the destination at OpenObserve's own `/healthz` via the D011
gateway address (a genuinely local, zero-dependency no-op) — this was
**experimentally rejected** by OpenObserve itself: `POST
/api/default/alerts/destinations` returned `400 Destination URL blocked
by SSRF guard: Access to private IP address 172.18.0.1 is not allowed`.
The same guard blocks `127.0.0.1`/`localhost` by construction (that's
the point of an SSRF guard) — no config toggle to disable it exists in
this version (v0.92.2), and disabling a security guard to route around
it was not an option worth taking.

**Reason:** OpenObserve's destination types (`http`/`email`/`sns`) all
require a real, externally-resolvable target — there is no "no-op"
destination type. Given that constraint, a stable, well-known public
echo endpoint is the simplest option that lets the destination (and
therefore the alert, since a destination is a required field) actually
get created.

**Alternatives considered:**
- Local loopback/gateway URL — rejected: blocked by OpenObserve's own
  SSRF guard, confirmed experimentally, not theoretically.
- `email` destination type — rejected: requires SMTP server
  configuration (`ZO_SMTP_*`) that doesn't exist in this environment;
  would be new infrastructure for no additional demo value.
- A session-specific `webhook.site` URL — rejected: not reproducible
  across sessions/machines the way a fixed, well-known URL is.

**Consequences/trade-offs:**
- This is the *only* part of the alerting setup that touches the
  network, and only for notification delivery — the alert's own
  evaluation (a real SQL query against real OTLP-exported logs) and its
  Triggered/History state in OpenObserve's UI are unaffected by network
  availability, so PRD §47's "no internet required for the demo" still
  holds for everything the demo actually needs to show. If delivery
  fails in an offline environment, that failure is confined to
  OpenObserve's own destination-delivery log.
- Alert SQL uses `severity`/`service` fields, verified against a real
  ERROR-level log produced by a live `PAYASAM_FAULT_PAYMENT_ERROR_RATE`
  injection in this session (not assumed from `logging_utils.py`'s
  stdout JSON shape, which is a separate export path from the OTLP logs
  OpenObserve actually stores).

**Status:** Accepted — implemented and verified live: both alerts
correctly showed `"last_outcome": "firing"` immediately after the
verification fault, using OpenObserve's own v0.92.2 API (discovered via
this instance's `/api-doc/openapi.json`, since no public curl example
existed in OpenObserve's own docs at the time).

---

## D014 — Business-impact endpoint: scoped down from the full Intelligence layer

**Decision:** Implement a single `payasam-backend` FastAPI service
(`gcp-cluster/payasam/backend/`) exposing `GET /impact`, computing
PRD §19's revenue-at-risk/downtime-cost formulas from real OpenObserve
data plus `business_metadata.yaml` — deliberately **not** the PRD's full
Intelligence layer (§16–20: incident correlation store, optimization/
FinOps engine, `affected_users`). This is a scope decision made with the
user, not an unspecified-detail default.

**Context/problem:** The user asked for the "scoped-down Phase 5
business-impact endpoint" specifically, as the second step of a
hackathon-feasibility plan (after OpenObserve alerts) prioritizing what's
actually judged over the PRD's full original ambition, given limited
time before the hackathon.

**Reason / scope cuts and why each is safe:**
- **No `affected_users`.** Verified by inspecting real OpenObserve log
  records (`kubectl exec`-based query, same credential pattern as
  `scripts/otel-query.sh`): `user_id` is not present on any exported log
  record. `logging_utils.py`'s `JsonFormatter` only surfaces a fixed
  field whitelist (`event_type`, `request_id`, `transaction_id`,
  `endpoint`, `status_code`) that never included `user_id`, even though
  PRD §11/§12 name it as a minimum field to collect. Rather than
  fabricate this number (explicitly forbidden by PRD §45) or silently
  patch all 5 already-verified Phase 1–4 services to add it — a much
  larger, riskier touch than "add a business-impact endpoint" — `/impact`
  returns `affected_users: null` with an explicit note. Adding `user_id`
  to the shared logging field whitelist plus one extra kwarg in
  `order-service`'s existing log calls is a small, contained follow-up
  if this number is wanted later.
- **No incident-correlation store or optimization/FinOps engine.** Not
  needed for the immediate demo narrative (inject fault → show real
  business impact → recover); adding them now would be exactly the kind
  of speculative, unrequested complexity PRD §1.3/§43 and the project's
  own engineering principle (§52) warn against.
- **Failed-transaction definition reuses prior art, not a new judgment
  call:** counts `order-service` log events in
  `{inventory_unavailable, inventory_error, payment_unavailable,
  payment_declined, order_persist_failed}` — deliberately excluding
  `insufficient_inventory` (a business/stock condition). This is the
  same infra-vs-business-condition distinction
  `simulator/traffic/generator.py`'s `classify_status`/`aggregate`
  already established in Phase 2 (`infra_error_rate` vs
  `insufficient_inventory_count`), reused rather than reinvented.
- **`incident_duration_minutes` is derived, not caller-supplied:**
  computed from the real first/last matching failure-event timestamps
  within the query window, not the caller's chosen `window_minutes` —
  an honest measured span, not an assumed one.
- **No OTel self-instrumentation for this service.** It *reads*
  telemetry rather than producing the application's business traces;
  duplicating `telemetry.py`/`logging_utils.py` into it for the current
  scope would be unused complexity. Uses plain Python `logging` instead.
- **Reuses existing config/networking exactly:** `envFrom` on
  `telemetry-config` + `openobserve-credentials` (same as all 5
  application services), deriving the OpenObserve base URL from
  `OTEL_EXPORTER_OTLP_ENDPOINT` the same way `otel-query.sh` does — zero
  new networking or credential-handling decisions (D010, D011
  unchanged).

**Alternatives considered:**
- Building the full PRD §16–20 Intelligence layer now — rejected: far
  more than the hackathon-feasibility plan called for at this step: not
  simplicity first violating.
- Silently estimating `affected_users` as `failed_transactions` (1
  request ≈ 1 user, since the traffic generator does assign one
  synthetic user per request) — rejected: still a proxy being presented
  as a distinct metric it isn't; more honest to omit and label than to
  quietly redefine what "affected users" means.

**Consequences/trade-offs:**
- `/impact` is genuinely useful for the demo (real fault → real,
  formula-correct business numbers) but is not the full "incident
  intelligence" product PRD §16–17 describes; that remains future work
  if scoped in explicitly.
- `affected_users` will read `null` in every demo run until the
  `user_id`-logging follow-up above is done.

**Status:** Accepted — implemented in `gcp-cluster/payasam/backend/`,
unit-tested (`tests/unit/test_business_impact.py`, pure formulas, no
cluster needed) and live-verified against a real injected payment fault
(`tests/integration/test_business_impact_live.py`): 4 real failed
requests produced `failed_transactions: 4`, a measured ~9.6s span, and
revenue/downtime figures matching the formulas exactly by hand
calculation before the automated test was written.

---

## D015 — Custom Payasam UI: fault control via Kubernetes RBAC, not `kubectl`; three toggleable views, not four

**Decision:** Build `payasam-frontend` (React + Vite, per D008) as a
single page with a toggle across exactly three views — **Technical**
("what's the problem right now"), **Business** ("how does this affect
my business"), **Remediation** ("what should I do about it") — plus a
Failure Simulator panel that triggers real faults directly from the UI.
To make that last part possible, `payasam-backend` gained a Kubernetes
RBAC `ServiceAccount`/`Role`/`RoleBinding` (`infrastructure/kubernetes/
payasam-backend-rbac.yaml`) scoped to `get/patch` on Deployments and
Deployments/scale in the `payasam` namespace only, and a
`faults_control.py` module using the Kubernetes Python client to do
exactly what `scripts/set-fault.sh`/`clear-faults.sh` already do, callable
over HTTP (`POST /faults/inject`, `POST /faults/recover`, `GET /faults`,
`GET /status`, `GET /remediation`).

**Context/problem:** The user explicitly asked for failure simulation
and its consequences to be visible *inside* the Payasam UI itself — not
narrated over a terminal running the Phase 4 scripts — and for the
output to be presented as a small number of role-based perspectives
(SRE/technical, business, "how do I fix it") rather than the PRD's
original four-view split (role selection + Technical + Business +
Investigation).

**Reason:**
- **RBAC over shelling out to `kubectl`:** a pod can't easily run
  `kubectl` (needs the binary added to the image, plus manual
  `--server`/`--token`/`--certificate-authority` flags since `kubectl`,
  unlike client libraries, doesn't auto-detect in-cluster config). The
  official Kubernetes Python client's `load_incluster_config()` handles
  all of that automatically from the ServiceAccount token already
  mounted into every pod — simpler and more idiomatic inside a pod, once
  the RBAC exists. The RBAC grant is deliberately as narrow as possible
  (two resource types, one namespace) — no cluster-wide access, no
  access to Secrets/Pods beyond what the existing `openobserve-credentials`
  envFrom already provides.
- **Read-modify-write over a raw PATCH:** `set_env_var`/`unset_env_var`
  read the full Deployment, mutate the one container's env list in
  Python, and call `replace_namespaced_deployment` — avoiding reliance on
  strategic-merge-patch semantics for a two-level nested list
  (`containers[].env[]`) that weren't worth the risk to get exactly
  right blind. This is exactly what `kubectl set env` does internally.
- **Three views, not four:** "role selection" as a separate screen adds
  a click with no information value when the UI is this small; a toggle
  across the three *content* perspectives the user actually asked for is
  simpler and matches their spec directly.
- **Fixed named scenarios, not free-form var/value input:** the UI never
  needs to know internal `PAYASAM_FAULT_*` variable names, and the
  backend never accepts an arbitrary env var to set on a Deployment —
  only four hardcoded presets plus recovery.
- **nginx reverse-proxying `/api/` to `payasam-backend`, not a second
  port-forward:** the frontend's own JS runs in the browser, which
  cannot resolve in-cluster Service DNS names — but nginx, running
  server-side inside the frontend's own pod, can. This means the demo
  needs one port-forward (to `payasam-frontend`) for the whole UI to
  work, not two.

**Alternatives considered:**
- Shelling out to `kubectl` from inside the pod — rejected: needs the
  binary added to the image plus manual auth flags; the Python client
  needs neither once RBAC exists.
- A raw strategic-merge PATCH on `containers[].env[]` — rejected: correct
  merge-by-key behavior for a list nested inside another list merged by
  key is genuinely easy to get subtly wrong; read-modify-write is
  unambiguous.
- The PRD's original 4-view UI (role selection + 3 content views) —
  rejected per explicit user direction; 3 toggleable views is what was
  asked for and is simpler.

**Consequences/trade-offs:**
- `payasam-backend` can now mutate cluster state, not just read
  telemetry — a real (if narrowly scoped and entirely local-only)
  increase in what that pod can do, accepted because it's exactly what
  the UI's core interaction requires.
- A real bug was caught and fixed during live verification: the
  Kubernetes `/scale` subresource's `spec.replicas` comes back as `None`
  (not `0`) via the Python client when a Deployment is actually scaled to
  zero — apparently an `omitempty`-style omission by the API server at
  that specific value. `get_replicas()` now reads the Deployment object
  directly instead, which does not have this problem. Caught by live
  testing, not by static reasoning about the client library.
- A second real bug was caught the same way: `/status`'s
  `faults_readable` flag was computed as `active_faults != {}` (should
  have been "did the call succeed," not "is the result non-empty"),
  which made a perfectly healthy system report `overall_status: unknown`.
  Both are covered by regression tests now
  (`tests/integration/test_faults_control_live.py`).
- No visual/interactive browser verification was performed on the
  frontend in this session (no browser tooling connected) — verified
  instead via the built JS bundle's contents, the deployed nginx proxy
  correctly round-tripping real GET/POST calls to the real backend, and
  code review. A real click-through pass is still worth doing before
  presenting.

**Status:** Accepted — implemented in `gcp-cluster/payasam/backend/
faults_control.py` + new endpoints in `main.py`, RBAC in
`infrastructure/kubernetes/payasam-backend-rbac.yaml`, and
`gcp-cluster/payasam/frontend/`. Backend fault-control path live-verified
end-to-end for all four scenarios via HTTP alone (no `kubectl` calls),
including the two bugs above being found and fixed before landing.

---

## D016 — Self-contained traffic generation from the Payasam UI

**Decision:** Add `POST /traffic/generate` to `payasam-backend` (sends
`count` real `POST /orders` requests to `api-gateway`, one at a time) and
a "Generate Test Traffic" button in the UI, so the Failure Simulator
panel is fully self-contained — inject a fault and make it manifest
without a second terminal running `scripts/run-baseline.sh`.

**Context/problem:** Live testing surfaced that the Business tab
correctly showed all zeros because fault injection alone produces zero
requests — nothing was measuring anything because nothing had happened.
OpenObserve confirmed zero `order-service` log entries in the prior 30
minutes. A compounding, separately-discovered issue (the cluster stuck
in an unrecovered cascading outage from earlier testing) was fixed
operationally first, not as a code change.

**Reason:** the minimal fix that keeps the demo in the UI: reuse
`httpx`, `api-gateway`'s existing DNS name, and the exact request shape
`simulator/traffic/generator.py` already uses (one order at a time,
`quantity: 1`, small delay between requests) — no new traffic-generation
mechanism, just a new caller of the existing one.

**Consequences/trade-offs, and two real bugs caught during live
verification, not merely written and assumed correct:**
- **Timing:** a request sent immediately after injecting a fault can
  land on the old, not-yet-replaced pod during its rolling restart and
  succeed for real — not a bug, but worth knowing for demo pacing (a
  couple of seconds' gap, or just clicking "Generate Test Traffic" twice,
  is enough).
- **Test-suite flakiness, found and fixed:** the new tests'
  `ensure_recovered` fixture originally waited only for `GET /status` to
  report `healthy`, which only proves the new pod answered one health
  check — not that the old pod/ReplicaSet had fully terminated. Running
  the full suite back-to-back let a residual old pod serve one stray
  request to an unrelated, later test file, failing it. Fixed by also
  waiting for the actual `kubectl rollout status` in the fixture (the
  same mechanism `scripts/set-fault.sh`/`clear-faults.sh` already rely
  on) — confirmed fixed by running the full suite twice in a row clean
  afterward, not just once.

**Status:** Accepted — implemented, unit/integration-tested
(`tests/integration/test_traffic_generation_live.py`), and live-verified
through the exact browser-facing path (the deployed nginx proxy, not a
direct backend call): on a healthy system 5/5 generated orders
succeeded; with `payment_errors` injected, 3/5 failed and `/impact`
correctly reported `failed_transactions: 3` with matching revenue.

---

## D017 — UI process visibility: unified dashboard, activity feed, honest indeterminate progress

**Decision:** Replace the three-tab toggle with a single simultaneous
dashboard (Technical/Business/Remediation panels all visible at once),
add a live, newest-first activity feed narrating both user-initiated
actions and system-detected state changes in plain English, and add
brief pulse-flash highlighting on values that just changed. Progress
during fault injection/traffic generation is an explicitly
**indeterminate** animated bar, not a fabricated percentage.

**Context/problem:** the user found the demo experience "feels horrible"
— clicking a button and waiting in silence for a background poll to
maybe show something different, with three tabs hiding two-thirds of the
story at any moment, gives no sense that anything real is happening.

**Reason:**
- Simultaneous panels over tabs: judges watching a live demo should see
  all three perspectives update together when a fault is injected, not
  be told "now imagine the Business tab also changed."
- Client-synthesized activity feed over a new backend stream: every
  event logged is derived from data the frontend already receives from
  existing endpoints (the result of an action it just took, or a value
  that changed between two polls) — no new backend endpoint, no new
  protocol, kept in `src/activityLog.js` as pure functions specifically
  so this logic is unit-testable without a rendering environment.
- Indeterminate progress bar, not a fake percentage: neither fault
  injection nor traffic generation currently exposes a real, granular
  progress fraction (that would need per-request streaming, deliberately
  deferred — see the "streaming" option discussed and not chosen this
  pass). Fabricating a percentage that doesn't correspond to anything
  measured would violate the same "no fake dashboard values" principle
  (PRD Sec 3) applied to a smaller, cosmetic case; an indeterminate bar
  honestly says "something real is in progress, duration unknown."
- Pulse as a discrete flash, not an animated counter: the underlying
  value changed at one discrete moment (the last poll) — a flash matches
  that; a smoothly-animating number would imply continuous measurement
  that isn't happening.

**A real, unrelated bug found and fixed during this pass's live
verification, not merely written and assumed correct:** reproducing a
UI-driven demo sequence left two faults active on `payment-service`
simultaneously, and calling Recover then failed with a genuine
Kubernetes `409 Conflict` ("the object has been modified"). Root cause:
`faults_control.py`'s read-modify-write functions did a single
GET-then-PUT with no retry, and the Deployment controller updates
`.status` continuously in the background, independent of these env-var
changes — two `unset_env_var` calls back-to-back on the same Deployment
gave that background churn enough of a window to invalidate the second
call's read. Fixed with `_update_deployment_with_retry`: on a 409,
re-read (fresh resourceVersion) and reapply the same mutation, up to 5
attempts. Confirmed fixed by reproducing the exact stacked-fault
scenario 3 times in a row post-fix, not just once.

**Testing scope, stated explicitly rather than left implicit:** the pure
activity-log logic (`activityLog.js`) and the new retry logic
(`faults_control.py`) are both unit-tested (6 + 4 tests, a new `vitest`
setup for the frontend — the project's first JS test tooling, kept
minimal: no jsdom/component-rendering tests were added, since none of
the new logic needs a DOM to verify). The `usePulse`/`ProgressBar`
timing behavior itself was **not** unit-tested (would need
`@testing-library/react` + jsdom, a heavier addition not justified for a
cosmetic flash/spinner) — verified instead by code review and confirming
the built bundle contains the expected classNames/behavior.

**Status:** Accepted — implemented, unit-tested, and live-verified
end-to-end through the deployed nginx proxy (not a shortcut): a fault
correctly appears in Technical, drives real numbers in Business, and a
matching recommendation in Remediation, simultaneously visible, with the
409 fix confirmed reliable across repeated runs. Full suite: 108 passed,
1 skipped (pre-existing), run clean twice in a row.

---

## D018 — Business-panel design pass: real status palette, self-consistent math, measured-vs-assumption labeling

**Decision:** Two changes, prompted by the user actually using the
deployed UI and asking "are these numbers real": (1) fixed a genuine
display inconsistency in the business-impact math, and (2) applied the
project's dataviz design method (reserved status palette, status-dot
convention, stat-tile contract, sparkline trend spec) to the dashboard,
replacing ad hoc colors chosen without that reference.

**Context/problem:** The user, live-testing the deployed dashboard,
noticed `0.0324 min × ₹800/min` displaying as `₹25.93` instead of
`₹25.92` and asked whether the Business panel's numbers were real at
all. They were real, but two independent things needed fixing: the
display inconsistency itself, and the fact that nothing in the UI
distinguished a *measured* number (failed transactions) from a
*configured assumption* (business criticality) -- both looked identical.

**Reason / what changed:**
- `impact.py`'s `compute_impact` now rounds `incident_duration_minutes`
  **before** using it in the downtime-cost multiplication, not after.
  This is marginally less numerically precise than multiplying the raw
  duration, but guarantees the two numbers a viewer can see on screen
  always multiply out exactly -- the correct tradeoff for a dashboard
  whose entire purpose is being auditable by eye. Locked in by a new
  unit test using the exact discrepancy the user found, plus tightening
  an existing integration-test tolerance from `abs=0.1` to an exact
  match now that there's nothing left to tolerate.
- Adopted the project's documented dataviz method for the whole
  dashboard rather than continuing with colors/labels chosen without
  it: the four reserved status colors (good/warning/serious/critical)
  now apply only to state indicators (a `StatusDot`, always beside a
  text label, per that system's "never color alone" rule) -- never to
  buttons or plain body text, which stay in normal ink ("text wears
  text tokens, never the status color"). Action buttons instead use the
  categorical accent hue, kept deliberately distinct from status
  colors so the two systems (state vs. action) never visually collide.
- Added a sparkline trend (12-point rolling history, de-emphasis line +
  accent-colored current point, per the stat-tile contract) under the
  two headline live numbers -- built from polling data already being
  fetched, no new backend endpoint.
- Added a `stat-tag` marking Business Criticality as "assumption, not
  measured," and a distinct dashed-border "Assumptions used" box, so a
  viewer can tell at a glance which numbers are live readings and which
  are configuration inputs.
- Added native `title` tooltips on every Business stat card spelling out
  its exact formula -- a permanent, on-demand answer to "is this real"
  for anyone looking at the dashboard later, not just this conversation.

**Alternatives considered:**
- Leaving the raw-duration multiplication and just displaying more
  decimal places -- rejected: still looks inconsistent to a viewer doing
  the obvious mental multiplication with the number actually shown.
  Rounding-before-multiplying removes the discrepancy at its source.
- A custom tooltip component instead of the native `title` attribute --
  deferred: native tooltips are functional and zero-dependency; a
  styled version is a proportionate future upgrade, not required now.

**Status:** Accepted -- implemented, unit-tested (backend: 1 new test
locking the exact discrepancy scenario, plus the existing 12 re-verified
green; frontend: `vitest` still 10/10 across both pure modules), and
live-verified: reproduced the exact rounding scenario end-to-end through
the deployed proxy and confirmed `duration × rate` now equals the
displayed cost exactly (0.4272 × 800 = 341.76, matched). Full suite: 109
passed, 1 skipped (pre-existing), run clean twice in a row.

---

## D019 — Real `affected_users`: closing the D014 gap, with a defensive fallback for a fresh OpenObserve schema

**Decision:** Implement `affected_users` for real, closing the gap D014
deliberately left open. Added `user_id` to the shared `logging_utils.py`
field whitelist (all 5 services, kept byte-identical) and to
`order-service`'s log calls wherever `order.user_id` is in scope; added
`impact.distinct_affected_users(hits, failed_transactions)` — a real
count of distinct `user_id` values among the same failure-event log
records `/impact`'s other numbers already come from, never a guess (e.g.
"assume 1 user per failed transaction").

**Context/problem:** D014 explicitly deferred this because `user_id`
wasn't in exported telemetry. Implementing it meant touching the
already-stable Phase 1–4 services — a bigger, riskier change than adding
a new endpoint, so it was scoped as its own deliberate step rather than
folded silently into D014.

**Reason / how it stays honest under all circumstances, not just the
happy path:**
- `distinct_affected_users` returns `(0, None)` when
  `failed_transactions == 0` (trivially correct), `(count, None)` when at
  least one matching log has a `user_id`, and `(None, <note>)` — never a
  fabricated `0` — when failures exist but none of the matching records
  carry a `user_id` (e.g. a partial rollout still running old pods).
- **A real, narrow failure mode was found live, not assumed:** querying
  `SELECT ..., user_id FROM "default"` against a fresh OpenObserve
  instance that has never ingested a log with a `user_id` field returns
  a genuine `400` (the column doesn't exist yet in OpenObserve's dynamic
  schema). Confirmed by reproducing it, not by reasoning about it in
  advance. `/impact` now catches exactly that `400`, retries the same
  query without `user_id`, and reports `affected_users: null` with an
  explicit "schema not yet initialized" note instead of surfacing a raw
  502 to the UI — the endpoint stays fully functional either way.

**Alternatives considered:**
- Leaving `affected_users` as the permanent `null` D014 shipped with —
  rejected once the user asked for it directly; the underlying gap
  (`user_id` missing from telemetry) was always a contained, disclosed
  follow-up, not a hard limitation.
- Letting the fresh-schema `400` propagate as a generic `502
  openobserve_unavailable` — rejected: that would look like a broken
  dependency when it's actually a one-time, self-resolving schema
  initialization, and would suppress `failed_transactions`/revenue too
  even though those don't depend on `user_id` at all.

**Consequences/trade-offs:**
- One extra HTTP round-trip to OpenObserve on the (rare, one-time-per-
  environment) occasion the fallback path triggers.
- `affected_users` is now real everywhere this environment has been
  used, since the schema learned the `user_id` field the first time a
  real order was placed after this change shipped.

**Status:** Accepted — unit-tested (`tests/unit/test_business_impact.py`:
zero-failure, distinct-counting including "same user twice counts once,"
and missing-`user_id` cases) and live-verified twice: once showing a
real `affected_users: 3` matching 3 real distinct failures, and
separately via a dedicated integration test
(`tests/integration/test_business_impact_live.py::
test_same_real_user_failing_twice_logs_one_consistent_user_id`) that
queries OpenObserve directly, filtered by a unique per-run `user_id`, to
sidestep a sliding-time-window flake an earlier (since-replaced) version
of this test hit for real when run after heavy manual testing — full
account of that flake and its fix is in the test file's own docstring.

---

## D020 — First real browser verification: one genuine bug found and fixed, everything else confirmed working as designed

**Decision/finding:** Once Chrome browser tooling connected for the
first time this build, the Payasam UI was click-tested live for the
first time (every prior "verification" was via curl/bundle-inspection,
explicitly disclosed as such throughout D015/D017/D018). This is that
verification's result, recorded because it closes a limitation stated
repeatedly in prior decisions, not because a new architectural choice
was made.

**What was confirmed working exactly as designed, live, in a real
browser:** the unified 3-panel dashboard rendering; the Failure
Simulator's indeterminate progress bar during a real fault
rollout; the activity feed narrating both user actions and
system-detected transitions (`System status: healthy → degraded`
appeared without any click causing it directly); real business-impact
numbers appearing within seconds of real traffic under a real fault;
sparklines correctly showing both a rise (impact appearing) and a
decline (recovery) shape; the `StatusDot` status-palette convention;
`Recover System` fully closing the loop; and the "Investigate further"
OpenObserve links genuinely opening `http://localhost:5080` (landing on
its real login page, exactly as expected for an unauthenticated new
tab).

**One genuine bug found and fixed, that no prior verification method
could have caught:** the Active Faults table's `PAYASAM_FAULT_*`
variable names (e.g. `PAYASAM_FAULT_PAYMENT_ERROR_RATE`, a single
33-character unbroken token) forced the table wider than its panel,
visibly bleeding the Deployment/Value columns into the adjacent
Business panel. Fixed with `table-layout: fixed` and
`overflow-wrap: anywhere` on `.data-table` cells so long tokens wrap
within their column instead of forcing table width. This class of bug
(layout overflow from real, long real-world content) is invisible to
`npm run build`, to unit tests, and to bundle-content inspection --
finding it required an actual rendered viewport, exactly the gap
disclosed as open in D015/D017/D018.

**A separate operational finding, not a product bug:** `kubectl
port-forward svc/X` pins to the specific pod it first resolved to and
dies when that pod is replaced by a rollout -- observed directly when
redeploying the frontend for the CSS fix silently killed the
port-forward the browser was using, presenting as a frozen/unresponsive
tab. Worth knowing for demo day: after any `kubectl rollout restart`
touching a service you have port-forwarded, the port-forward must be
restarted too, not just the pod.

**Status:** Accepted -- the CSS fix is deployed and re-verified live
(confirmed the table now wraps correctly instead of overflowing).
Full suite re-confirmed green afterward (114 passed, 1 skipped
pre-existing; frontend 10/10) -- this fix touched only CSS, no test
changes were needed or made.

---

## Decisions carried directly from the PRD (not independently established, listed for traceability)

These are not "decisions" made during planning — they are explicit PRD
mandates restated here only so the log is a complete picture of what
constrains the architecture. They are not open for reconsideration during
implementation.

- Deployment target is local-only; no GCP infrastructure, credentials, or
  billing (PRD §1.3, §49).
- No BigQuery, Kafka, Redis, or PostgreSQL unless later proven necessary
  (PRD §1.3).
- No Grafana as an additional visualization platform; OpenObserve is the
  single observability UI (PRD §1.3).
- KubeView is a supporting infrastructure-topology visualization only, not
  the Payasam product UI (PRD §26–27).
- Single primary implementation agent by default; no multi-agent workflows
  without concrete technical justification (PRD §1.1).
- Traffic scale stays small and configurable (10, up to ~50 simulated
  users) — not a production-scale load test (PRD §4).

**Status:** Accepted (by PRD mandate, not by independent architectural
choice).
