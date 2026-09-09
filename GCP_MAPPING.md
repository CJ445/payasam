# GCP_MAPPING.md — Local Environment ↔ Real GCP

This document exists for S5's slot ("Cloud/GCP architecture") and for
anyone who asks "so where's the GCP part." Per a deliberate,
budget-conscious team decision, this project runs entirely on a local,
GCP-shaped stack instead of live GCP — every local component below maps
1:1 to a specific GCP service, so the honest answer to "is this really
tied to GCP" is: **yes, architecturally — moving it there is a redeploy
of the same containers and manifests, not a redesign.** See
`DECISIONS.md` D002/D003/D011 for the reasoning already recorded when
these choices were first made, and `DEMO.md` §7 for the exact talking
script.

---

## 1. The sample application

Payasam's demo application is a small, real, transactional order-flow
app — not a toy that only produces fake success. It exists to generate
real traffic, real failures, and real telemetry for the observability
layer above it to actually observe. Five services, one topology:

```text
        User / traffic generator
                 │
                 ▼
          ┌─────────────┐
          │ API Gateway │   the only externally-reachable entry point;
          └──────┬──────┘   a thin proxy to Order Service
                 │
                 ▼
          ┌─────────────┐
          │Order Service│   orchestrates the transaction: reserves
          └──┬───────┬──┘   inventory → charges payment → persists the
             │       │      order; compensates (releases inventory) on
             ▼       ▼      any downstream failure
   ┌──────────────┐ ┌──────────────┐
   │  Inventory   │ │   Payment    │   Inventory does a real
   │   Service    │ │   Service    │   database-backed stock check;
   └──────┬───────┘ └──────┬───────┘   Payment simulates payment
          │                │           processing (no real payment
          └───────┬────────┘           provider) but does real work —
                   ▼                   validates, persists via Database
            ┌─────────────┐
            │  Database   │  the single writer of application state
            └─────────────┘  (products/inventory, orders, payments)
```

| Service | Role |
|---|---|
| `api-gateway` | The only externally-reachable service. Assigns a `request_id` if the caller didn't supply one, forwards to Order Service, returns its response transparently. |
| `order-service` | The transaction orchestrator. Reserves inventory for every item, charges payment for the total, persists the completed order — and if anything downstream fails, releases whatever inventory it already reserved rather than leaving stock silently stuck (`DECISIONS.md` D012). |
| `inventory-service` | Performs a real, database-backed inventory reservation/release on every request — no local or faked state. |
| `payment-service` | Simulates payment processing (no external payment provider is called) but does real work: validates the request, creates a payment transaction, persists it. This is also where the Phase 4 controlled fault injection (latency, error rate) lives. |
| `database` | The single writer of application state — a small, real, controllable FastAPI + SQLite service. **Deliberately not a production database** (see the callout below) — every other service reaches it over HTTP, never a shared file or embedded library, specifically so failure injection has one well-defined point to act on without an architectural rewrite (`DECISIONS.md` D005). |

**On top of the application, two more components exist purely as
Payasam's own value-add, not part of the simulated production app:**

| Component | Role |
|---|---|
| `payasam-backend` | Queries OpenObserve directly for real failure events and computes business impact (`failed transactions × configured transaction value`, `duration × downtime cost`) — never a hardcoded number. Also the fault-injection control API the UI drives. |
| `payasam-frontend` | The live dashboard: Technical/Business/Remediation panels, the Failure Simulator, an activity feed — everything Payasam adds on top of raw OpenObserve. |

**A callout worth saying out loud, not waiting to be asked (per the UC2
guideline's own explicit warning against artificially presenting a
SQLite database):** `database` really is SQLite, and that's disclosed
on purpose, not hidden. It's a stand-in for a real production database,
chosen specifically because it makes the failure-injection scenarios
deterministic and safely reversible on a hackathon timeline — real
Postgres connection-pool exhaustion is much harder to make both
realistic and safe to demo repeatedly. If this were pushed to
production, it maps to **Cloud SQL** (see the table below) and nothing
else about the architecture would need to change.

---

## 2. The mapping

| What you're looking at locally | Real GCP equivalent | Why this maps cleanly |
|---|---|---|
| 5 FastAPI services running as Kubernetes Deployments in `kind` | **Cloud Run** (or GKE workloads, if you need more K8s-native control) | Each service is already a stateless, independently-scalable container behind a Service — exactly Cloud Run's model. |
| The `kind` cluster itself | **GKE** (Autopilot or Standard) | `kind` was chosen specifically because it's the lightest real Kubernetes control plane available locally (`DECISIONS.md` D001) — the manifests underneath (`infrastructure/kubernetes/*.yaml`, applied via Kustomize) are standard Kubernetes and apply to GKE unchanged. |
| OpenObserve (logs/metrics/traces/alerts/dashboards) | **Cloud Logging + Cloud Monitoring + Cloud Trace** | OpenObserve was chosen as the observability substrate specifically so this project doesn't rebuild ingestion/search/alerting/dashboarding that already exists (`DECISIONS.md` D002) — the same reasoning is exactly why you'd use Cloud's native operations suite in production rather than self-hosting an observability stack on GKE. |
| Direct OTLP/HTTP export from every service straight to OpenObserve (no collector) | **OTel → Cloud Trace/Cloud Logging via the Cloud Operations OTLP endpoint**, or an OTel Collector `DaemonSet` if fan-out to multiple backends is ever needed | No standalone collector was introduced locally purely because it added memory/process overhead with no capability benefit at this traffic scale (`DECISIONS.md` D003) — the same OTLP-native code ships unchanged either way. |
| The `kind` Docker-bridge-gateway trick for reaching host-run OpenObserve (`DECISIONS.md` D011) | **Not needed at all** | This entire mechanism exists solely to solve "a pod needs to reach a service running outside the cluster, on the same laptop." Inside GKE, Cloud Logging/Monitoring/Trace are reached via their normal public (or Private Service Connect) endpoints — no gateway-IP resolution, no dynamic re-resolution on cluster recreation. |
| Kubernetes Secret (`openobserve-credentials`) | **Secret Manager**, mounted into Cloud Run/GKE via the standard integration | Same "never hardcode credentials, never commit them" rule (`DECISIONS.md` D010) — just backed by GCP's managed secret store instead of a cluster-local Secret. |
| `payasam-backend`'s scoped Kubernetes RBAC (`get`/`patch` on Deployments, `payasam` namespace only) | **A GCP IAM Service Account** with `roles/run.developer` (or a narrower custom role) scoped to just the relevant Cloud Run services | Same principle — the narrowest permission that lets the backend flip a fault and nothing else (`DECISIONS.md` D015). |
| The traffic-generator Kubernetes `Job` (`infrastructure/kubernetes/jobs/traffic-generator-job.yaml.template`) | **Cloud Run Jobs**, optionally triggered by **Cloud Scheduler** for a recurring baseline | A `Job` was already the right K8s primitive for "run once to completion" — Cloud Run Jobs is its direct, serverless equivalent. |
| `database` (FastAPI + SQLite) | **Cloud SQL** (Postgres/MySQL), if promoted past the demo's deliberately-synthetic version | See the callout in §1 above — this is the one component explicitly *not* production-shaped by design, and the mapping table says so rather than pretending otherwise. |
| Plain Kubernetes YAML + built-in Kustomize (no Helm) | **Same** — Kustomize ships with `kubectl`/GKE unchanged | Helm wasn't installed locally and wasn't needed for ~9 near-identical manifests (`DECISIONS.md` D009); nothing about this changes on GKE. |

---

## 3. What actually changes if this moves to real GCP

Everything in the left column above is already built the way its GCP
equivalent expects to receive it — the honest list of what changes is
short:

1. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at Cloud Trace/Logging's OTLP
   endpoint instead of the local `kind`-gateway-resolved OpenObserve
   address (one env var, no code change).
2. Move `openobserve-credentials`'s values into Secret Manager (or drop
   them entirely — Cloud Logging/Trace auth via the Cloud Run service
   identity, no Basic Auth needed).
3. Apply the same `infrastructure/kubernetes/*.yaml` manifests to a GKE
   cluster, or translate each Deployment/Service pair to a Cloud Run
   service (a mechanical, 1:1 conversion given the mapping above).
4. Swap the RBAC Role/RoleBinding for the equivalent IAM role grant.

Nothing about the application logic, the failure-injection mechanism, or
the business-impact calculations changes at all — they were never
GCP-specific or `kind`-specific to begin with.
