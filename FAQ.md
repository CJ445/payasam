# FAQ.md — Q&A Prep for the Technical Cross-Questioning Session

Every answer below is grounded in what was actually built, decided, or
measured — cited against `PRD.md`, `DECISIONS.md` (D001–D020),
`IMPLEMENTATION_PLAN.md`, `SETUP.md`, `DEMO.md`, and `GCP_MAPPING.md`.
Where the honest answer is a limitation or a scope cut, it's stated
plainly — the evaluation guidelines list "limits" as an expected part of
a strong presentation ("Roadmap — what's built now vs. what's next"),
not something to hide. Four tiers, easy to complex; the closing table
flags the specific traps most likely to come up.

---

## Easy

**Q: What is Payasam, in one sentence?**
Payasam is a Cloud Operations Intelligence Platform that sits on top of
a real, small, multi-service application and a real observability
backend (OpenObserve), turning raw logs/metrics/traces into
plain-language answers to "what's broken, who does it hurt, and what
should I do about it" (PRD §2.2–2.3). Nothing it shows is precomputed —
every number is derived live from what the running system actually did.

**Q: What problem does this solve, and who is it for?**
It targets development/operations teams who need centralized log
monitoring, real-time visibility into errors, and cost-aware alerting —
exactly the hackathon's own UC2 problem statement (centralized logging,
real-time log viewing, error alerts, a cost-saving dashboard). Payasam's
specific contribution on top of that baseline is translating technical
incidents into business terms (revenue at risk, downtime cost, affected
users) that a non-technical stakeholder can act on without reading a
stack trace.

**Q: What are the main layers of the system?**
Four, per PRD §7/§16/§26–27: (1) a local, GCP-like Kubernetes production
environment running five real FastAPI services; (2) OpenObserve as the
observability substrate (logs/metrics/traces/alerts/dashboards); (3) the
Payasam Intelligence layer (currently: the business-impact engine) that
reads OpenObserve's data and adds business meaning; (4) the Payasam UI,
the single interactive product surface. KubeView is a supporting
infra-topology visual, explicitly not part of this stack (PRD §26).

**Q: What actually happens in the live demo, start to finish?**
From the Payasam UI: the system starts healthy (green status pill,
polling real Kubernetes/application state every 3 seconds); clicking
"Inject Payment Errors" calls a real API that sets a real environment
variable via the Kubernetes API, causing `payment-service` to actually
reject requests; clicking "Generate Test Traffic" sends real orders
through the real gateway; the Technical, Business, and Remediation
panels update together within seconds from real OpenObserve data; and
"Recover System" removes the fault and the numbers return to zero. This
is `DEMO.md`'s S8 walkthrough, which is also the PRD's own primary demo
scenario (§25).

**Q: Is OpenObserve something you built, or a dependency?**
It's a pre-existing dependency, not something Payasam built — it was
already running as a local Docker container before this project started
(PRD §5). Payasam deliberately does not rebuild log storage, search,
generic dashboards, or generic alerting (PRD §13, D002); it consumes
OpenObserve's real ingestion/search/alerting APIs and adds business
intelligence on top, which is the actual product differentiator (PRD
§16).

**Q: Is the `database` service a real production database?**
No, and this is disclosed on purpose, not hidden: it's a small FastAPI
service backed by SQLite, chosen specifically because it makes
deterministic, safely-reversible failure injection possible on a
hackathon timeline (D005). Every other service reaches it only over
HTTP, never a shared file, so nothing else in the architecture depends
on it being SQLite specifically — see `GCP_MAPPING.md` §1's callout,
written to directly answer UC2's own explicit warning about this.

**Q: Is any of this actually deployed on real GCP?**
No — this is a deliberate, budget-conscious team decision made explicitly
without live GCP credits or billing, not an oversight or a time-crunch
cut. Every local component was chosen because it maps 1:1 to a specific
GCP service (`kind`→GKE, OpenObserve→Cloud Logging/Monitoring/Trace,
etc. — full table in `GCP_MAPPING.md` §2), so the migration path is a
redeploy of the same containers and manifests, not a redesign.

**Q: What did you build the application services in?**
Python + FastAPI + uvicorn, for all five application services
(`api-gateway`, `order-service`, `inventory-service`, `payment-service`,
`database`) — chosen because the machine already had FastAPI, uvicorn,
and the full OpenTelemetry SDK/exporter family pre-installed, the
strongest environmental signal for the stack (D004). The frontend is
React + Vite (D008); `payasam-backend` is also FastAPI.

**Q: What is KubeView, and how is it different from Payasam?**
KubeView is a generic, third-party Kubernetes topology visualizer used
only to show judges "this is a real running cluster, not a diagram" (PRD
§26). It is explicitly not the product — Payasam's own UI is the primary
interactive surface for the actual demo narrative.

---

## Medium

**Q: Why `kind` instead of `minikube`?**
Both were already installed and viable; `kind` was chosen because it
runs its control plane as a single Docker container with no extra
VM/driver layer, comes up faster, and uses less memory — a real
constraint on this machine (D001, ~4–5Gi realistic budget for the whole
stack per `IMPLEMENTATION_PLAN.md` §1.5). A stale `kind-payasam`
kubeconfig context found during discovery also suggested `kind` was the
originally intended choice.

**Q: Why is the `database` service SQLite instead of Postgres?**
PRD §1.3 explicitly excludes Postgres "unless later proven necessary,"
and the primary failure scenario needed the "safest and most
deterministic" saturation mechanism (PRD §14). A small controllable
SQLite-backed service lets `payment`/`order` genuinely call a real
dependency and genuinely experience injected latency/errors, while
staying 100% reproducible and safely reversible — materially harder to
guarantee against real Postgres connection-pool exhaustion on a
hackathon timeline (D005). Rejected alternative: writing a fake
`database_error=true` incident record directly — explicitly prohibited
by PRD §3.

**Q: Why no Kafka/Redis/message broker anywhere in the design?**
PRD §1.3 explicitly excludes them unless proven necessary, and nothing
in the actual transaction flow (`api-gateway → order-service →
{inventory-service, payment-service} → database`) needed asynchronous
messaging — every call is a direct, synchronous HTTP request whose
failure needs to be handled and compensated immediately (D012), which a
broker would only complicate. No decision entry proposes one because the
need never arose during any of the seven build phases.

**Q: Why a custom traffic generator instead of Locust or k6?**
Neither was installed, and both would add a UI/dependency footprint not
needed at this project's traffic scale — PRD §43 requires justifying new
dependencies before adding them (D006). A ~150-line `asyncio`/`httpx`
script fully satisfies PRD §11 (configurable users/RPS, real `user_id`/
`request_id`/`transaction_id`, trace-context propagation) using
libraries that were already installed.

**Q: Why React + Vite for the frontend, and why so few libraries?**
The UI needs several distinct, live-updating, shared-state views
(Technical/Business/Remediation panels polling together), which is
exactly the kind of thing that makes a component framework the simpler
option rather than a complexity to avoid (D008) — hand-rolled
server-rendered HTML+polling would have meant reinventing client-side
state management. Vite was picked over Create React App because CRA is
effectively deprecated; no Redux, GraphQL, or UI kit was added, in
keeping with PRD §1.3's "no unnecessary frontend frameworks" in spirit.

**Q: Why plain Kubernetes YAML + Kustomize instead of Helm?**
`helm` was not installed on the machine, and Kustomize already ships
built into `kubectl` (v5.6.0) — sufficient for the ~9 small,
structurally similar manifests this project needed, with no templating
complexity Helm would add but this scale doesn't require (D009).

**Q: How are the Business-panel numbers (revenue at risk, downtime cost,
affected users) actually computed?**
`payasam-backend`'s `GET /impact` queries OpenObserve directly for real
`order-service` failure-event logs (never trusts a caller-supplied
count) in a specific event-type set (`inventory_unavailable`,
`inventory_error`, `payment_unavailable`, `payment_declined`,
`order_persist_failed` — deliberately excluding the business condition
`insufficient_inventory`). `revenue_at_risk = failed_transactions ×
avg_transaction_value_inr`, `downtime_cost = incident_duration_minutes ×
downtime_cost_per_minute_inr`, both from `business_metadata.yaml`'s
explicit, disclosed assumptions (D014, PRD §19). `affected_users` is a
real count of distinct `user_id` values among those same failure
records, computed by `impact.distinct_affected_users` (D019) — never an
estimate like "assume one user per failed transaction."

**Q: What does the traffic generator actually do, end to end?**
Each simulated user is its own `asyncio` coroutine sending real
`POST /orders` requests to `api-gateway` over real HTTP, spaced by a
jittered interval, picking a product via a seeded RNG weighted by seeded
stock (D006, `simulator/traffic/generator.py`). It runs either as a CLI
tool (`scripts/run-baseline.sh`) or as a one-shot Kubernetes `Job`, and
computes its own metrics (success/failure counts, latency percentiles,
RPS) locally — nothing about those metrics is sent to OpenObserve; that
comes from the application services' own telemetry instead.

**Q: How do the OpenObserve alerts actually work — what triggers them?**
Two real alerts exist (`scripts/configure-alerts.sh`):
`payasam-any-service-error` (any service, `severity = 'ERROR'`) and
`payasam-payment-degraded` (payment-service specifically), each a real
SQL query evaluated every minute against a 5-minute lookback window —
not a UI toggle. Their notification destination is a public echo
endpoint (`https://httpbin.org/post`) because OpenObserve's own SSRF
guard rejected a local address (D013) — the alert's own evaluation and
firing state in the OpenObserve UI are unaffected by that.

**Q: What are the five application services, and how do they interact
in one transaction?**
`api-gateway` (external entry point, thin proxy), `order-service`
(orchestrator), `inventory-service` and `payment-service` (called by
order-service), and `database` (the single writer of state, reached
over HTTP by every other service). A single order reserves inventory
first, then charges payment; if payment fails for any reason,
`order-service` releases the inventory it already reserved rather than
leaving stock silently stuck decremented (D012) — full service-role
table in `GCP_MAPPING.md` §1.

---

## Hard

**Q: Isn't SQLite as your "database" unrealistic — doesn't that
undercut the whole demo?**
It's a real, documented trade-off, not a hidden shortcut: the mechanism
being synthetic (SQLite instead of Postgres) is disclosed explicitly,
while the *causal chain* around it is fully real — a real HTTP call, a
real injected delay, real resulting telemetry (D005). The alternative,
directly injecting a fake `database_error=true` record, is exactly what
PRD §3 prohibits and would have been strictly less honest than what was
actually built. If pushed further to production, this is an isolated,
contained swap to Cloud SQL (`GCP_MAPPING.md` §1) — nothing else in the
architecture depends on the mechanism.

**Q: How do you know the business-impact numbers aren't just
fabricated?**
Because they're computed from the same failure-event log records
`/impact` queries live from OpenObserve, using formulas unit-tested
independently of any cluster (`tests/unit/test_business_impact.py`) and
live-verified against real injected faults
(`tests/integration/test_business_impact_live.py`) — a real payment
fault producing exactly the number of failed transactions the test
itself caused, with revenue/downtime figures matching the formula by
hand calculation (D014). A concrete example of this scrutiny actually
catching something: a real display-rounding inconsistency
(`0.0324 × 800` showing `25.93` instead of `25.92`) was found by the
user reading the live dashboard and fixed at the formula level, not
papered over with a display tweak (D018).

**Q: What happens if two people click fault buttons on the same service
at the same time?**
This was hit for real during development, not hypothesized: two rapid
fault changes on the same Deployment produced a genuine Kubernetes `409
Conflict`, because the Deployment controller updates `.status`
continuously in the background independent of these env-var changes,
invalidating a stale read (D017). The fix,
`_update_deployment_with_retry`, re-reads the Deployment (fresh
`resourceVersion`) and reapplies the same mutation, up to 5 attempts —
confirmed by reproducing the exact stacked-fault scenario three times in
a row post-fix.

**Q: Why didn't you build the full incident-correlation store and
optimization/FinOps engine the PRD originally described (§16–20)?**
This was a deliberate, explicit scope decision made directly with the
user as part of a hackathon-feasibility plan, not a silent gap — neither
was needed for the immediate demo narrative (inject → real impact →
recover), and building them anyway would have been exactly the kind of
speculative, unrequested complexity the PRD's own §1.3/§43 and
engineering principle (§52) warn against (D014). What was built instead
— the business-impact engine — reuses the PRD's own §19 formulas
faithfully; the correlation store and FinOps engine remain legitimate,
explicitly-scoped future work.

**Q: What's your actual test coverage, and what has it caught?**
As of the last full run: 114 backend tests + 10 frontend tests, all
passing, including live integration tests that inject a real fault,
generate real traffic, and assert on the real resulting numbers
(`DEMO.md` S8, D020). This is not a paper claim — real bugs were found
exactly this way, not by code review alone: an inverted `faults_readable`
flag that made a healthy system report `unknown` status; the Kubernetes
`/scale` subresource returning `replicas=0` as `None` instead of `0`;
the D017 `409 Conflict` above; the D018 display-rounding bug; and a CSS
table-overflow bug found only once real browser click-testing happened
(D020) — that last one is explicitly a class of defect no unit test or
bundle inspection could have caught.

**Q: Why does `affected_users` sometimes come back `null`?**
It was initially deferred entirely (D014) because `user_id` wasn't
present in exported telemetry — a real Phase 1 gap, honestly reported as
`null` with a note rather than faked. It was later implemented for real
(D019) by adding `user_id` to the shared logging whitelist, but it still
returns `null` with an explicit note in one narrow, real case: querying
a freshly-initialized OpenObserve instance that has never ingested a
`user_id` field returns a genuine schema-not-yet-known `400`, caught
live and handled with a fallback retry rather than surfacing a raw error
to the UI.

**Q: Your alert destination is a public echo endpoint — doesn't that
contradict "no internet required for the demo"?**
Only the notification-*delivery* hop touches the network, and that was
forced: OpenObserve's own SSRF guard rejected every local/loopback
address tried, with `POST /api/default/alerts/destinations` returning an
explicit `400 Destination URL blocked by SSRF guard` (D013) — disabling
that guard to route around it was never on the table. The alert's own
evaluation (a real SQL query) and its Triggered/History state in the
OpenObserve UI are entirely unaffected by network availability, so
everything the demo actually needs to show still works offline (PRD
§47); only delivery-confirmation to `httpbin.org` would fail without
internet, and nothing in the demo script depends on that succeeding.

**Q: The fault-control admin endpoints are unauthenticated — isn't that
a security hole?**
Yes, and it's accepted for exactly one reason stated explicitly (D007):
the entire system is local-only and never internet-exposed. This is
scoped as a hackathon-simulation trade-off, not a production pattern —
it must not be carried into any non-local deployment, and the GCP
mapping (`GCP_MAPPING.md` §2) would replace it with a proper IAM Service
Account grant, not an unauthenticated endpoint.

**Q: What's the actual measured timing of the demo loop — is
"real-time" a fair claim?**
Measured, not claimed: fault injected → visible in `/status` ≈0.3s;
fault active → real failure → visible in `/impact` ≈12.5s (dominated by
OTLP batch export plus OpenObserve ingestion, not application logic);
recover triggered → `/status` reports healthy ≈0.5s
(`IMPLEMENTATION_PLAN.md` Phase 7). PRD §39 explicitly forbids claiming
"real-time" with an arbitrary number — these are the actual measured
values from one scripted, automated timing pass, not an estimate.

---

## Complex

**Q: How would this look under real production traffic instead of
10–50 simulated users?**
The single-process SQLite lock in `database` is the first thing that
would need to change — Phase 2/3's own load experiments already showed
p50 latency degrading from 83ms at 1 user to 2231ms at 20 users, with
`db.lock_wait_ms` telemetry directly confirming lock contention (not
just correlation) rising from ~0% to ~88–92% of DB-operation time across
that same range (`IMPLEMENTATION_PLAN.md` Phases 2–3). At real
production scale this service would need to move off SQLite to Cloud
SQL with real connection pooling — the exact promotion path D005 already
names as a contained, isolated change, since nothing else in the
architecture depends on the mechanism.

**Q: What breaks first if you pushed this at real GCP scale?**
Beyond the SQLite bottleneck above: the direct-OTLP-per-service export
path (D003) would need revisiting once telemetry volume or destination
count grows, since there's currently no centralized point for
batching/retry/fan-out — that trade-off was named explicitly as a
consequence when the no-Collector decision was made. The unauthenticated
fault-control endpoints (D007) would also need real IAM-scoped
authentication before touching anything beyond a local-only environment.

**Q: Why no OpenTelemetry Collector — wouldn't a real system need one?**
At this project's actual scale (10–50 simulated users, low RPS),
OpenObserve's native OTLP/HTTP ingestion made a standalone Collector a
process, memory footprint, and operational layer with no capability
benefit — direct SDK export already satisfied PRD §12–13 fully (D003).
The named, honest trade-off is no centralized batching/retry/fan-out if
volume or destinations grow — at that point, introducing a Collector
`DaemonSet` (already identified as the concrete next step in
`GCP_MAPPING.md` §2) is a contained addition, not a redesign, since it
sits transparently between the existing SDKs and their existing OTLP
target.

**Q: If given another month, how would you extend this to the full
original PRD scope?**
In priority order, following what's already explicitly scoped as
deferred rather than abandoned: (1) the incident-correlation store and
optimization/FinOps engine (PRD §16–20, D014) — the business-impact
formulas already exist and generalize directly; (2) the PRD's original
4-screen UI (role selection + separate Technical/Business/Investigation
views, PRD §21–24) if a future demo context specifically needs
audience-gated views rather than the current single simultaneous
dashboard (D015); (3) idempotency-key handling for order submission,
flagged as a known HIGH-severity gap since Phase 1 and never
implemented; (4) promoting `database` to a real Postgres/Cloud SQL
backend once the SQLite lock-contention ceiling above is actually hit in
a demo.

**Q: What's the actual GCP migration path, and what changes first?**
Per `GCP_MAPPING.md` §3, in order: (1) point
`OTEL_EXPORTER_OTLP_ENDPOINT` at Cloud Trace/Logging's OTLP endpoint
instead of the locally-resolved gateway IP — one environment variable,
no code change; (2) move `openobserve-credentials`'s values into Secret
Manager, or drop Basic Auth entirely in favor of the Cloud Run service
identity; (3) apply the existing `infrastructure/kubernetes/*.yaml`
manifests to a GKE cluster, or convert each Deployment/Service pair to a
Cloud Run service — a mechanical 1:1 conversion given the mapping table;
(4) swap the RBAC Role/RoleBinding for an equivalent IAM role grant.
Nothing about the application logic, failure-injection mechanism, or
business-impact calculations changes, since none of them were ever
GCP-specific or `kind`-specific to begin with.

**Q: Why does taking Payment Service down also make Order Service show
"unreachable" — is that a bug?**
No — it's a real, if slightly surprising, emergent property of strict
Kubernetes readiness checking: `order-service`'s own `/ready` handler
checks its actual downstream dependencies (Phase 1), and when
`payment-service` is scaled to zero, Kubernetes removes any pod that
fails readiness from its Service's routable endpoints entirely (`DEMO.md`
S8). This is disclosed and demonstrated live in the demo script
precisely because it's a genuine cascading-failure behavior worth
showing, not a defect worth hiding.

**Q: How would you handle idempotency for duplicate order submissions
in a real production version?**
This is an explicitly named, unresolved gap since Phase 1 — a retried
`POST /orders` today is indistinguishable from a new order and would
double-reserve/double-charge (`IMPLEMENTATION_PLAN.md` Phase 1
post-build review). It was reported but deliberately not fixed because
it requires a new architectural decision (an idempotency-key design) that
was never made; the credible next step is a client-supplied idempotency
key on `order-service`, deduplicated against a short-lived store keyed
by that value before reservation begins.

**Q: What's the single biggest engineering risk in this system as it
stands today?**
Two, both named explicitly in the project's own risk log
(`IMPLEMENTATION_PLAN.md` §7): the tight local memory budget (~4–5Gi for
the whole stack, the reason behind single-replica services and no
Collector), and the `kind`-to-host gateway IP not being guaranteed
stable across cluster recreation (D011) — both are documented,
mitigated (small explicit resource limits; the IP is re-resolved fresh
every time, never hardcoded), and neither has caused an actual failure
in this build, but both would need re-evaluating before scaling this
beyond a single-laptop demo.

---

## Quick reference: if they ask about X, don't say Y

| If asked about... | Don't say | Say instead |
|---|---|---|
| The `database` service | "We built a real production database" | It's a small, disclosed FastAPI+SQLite service, chosen specifically for deterministic/reversible fault injection (D005); the honest GCP target is Cloud SQL (`GCP_MAPPING.md` §1) |
| No live GCP deployment | "We ran out of time for the GCP part" | It's a deliberate, budget-conscious decision made explicitly with no GCP credits; every local component maps 1:1 to a GCP service, so migration is a redeploy, not a redesign (`GCP_MAPPING.md`) |
| Phase 5/6 scope vs. the original PRD | "We implemented the full Intelligence layer and 4-view UI" | We implemented the business-impact engine only, and a single 3-panel dashboard instead of role-selection + 3 separate views — both deliberate, user-directed scope decisions (D014, D015), with the rest named as explicit future work |
| `affected_users` | "It's always an accurate live number" | It's a real distinct-user count from log data (D019), with an honest `null` + note fallback for the one real case (a freshly-initialized OpenObserve schema) where it can't be computed yet |
| "Real-time" dashboard updates | An unmeasured word like "instant" | The actual measured numbers: ~0.3s fault→status, ~12.5s fault→visible business impact, ~0.5s recover→status healthy (`IMPLEMENTATION_PLAN.md` Phase 7) |
| The alert notification destination | "This demo needs zero internet access, full stop" | Alert evaluation and firing state work fully offline; only the notification-delivery hop uses a public echo endpoint because OpenObserve's own SSRF guard blocked every local address (D013) |
| Test coverage | A vague "we tested it thoroughly" | The specific number (114 backend + 10 frontend tests) and at least one specific bug it caught (the 409 Conflict, the `/scale` `None` quirk, the display-rounding bug, or the CSS overflow bug) |
| Browser verification of the UI | "Extensively tested across browsers/devices" | One real click-through verification pass happened (D020) and found/fixed one genuine layout bug; that is the actual extent of manual browser testing performed |
| Idempotency / duplicate orders | "That's handled" | It is an explicitly open, known gap since Phase 1 — a retried request today can double-charge; not implemented, named as the next architectural decision needed |
