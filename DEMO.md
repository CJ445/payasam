# DEMO.md — Judge-Facing Walkthrough (structured per UC2's S1–S8 order)

This is the script for presenting Payasam live: what to click, what to
say, and why it matters — organized to match
`hackathon-guidelines/use-case-2-presentation-structure.pdf`'s own S1–S8
PPT/MVP sequence, so the demo runs in the exact order that structure
expects rather than needing to be re-mapped live. `SETUP.md` covers
getting the environment running — do all of that (and the pre-demo
checklist below) *before* judges arrive.

**Current state:** the Payasam UI (Phase 6) is built. It's the primary
interactive surface: fault injection happens by clicking its buttons,
and its Technical/Business/Remediation panels are all visible
simultaneously (no tab-switching) so the audience sees all three update
together. OpenObserve is still where you go for deep technical evidence
(raw logs/traces/alerts) when someone asks "how do you know that" — the
Technical panel's "Investigate further" links jump straight there.
**Click-tested live in a real browser (`DECISIONS.md` D020)** — one
genuine layout bug (a long fault name overflowing its table) was found
and fixed this way; everything else (activity feed, sparklines,
recovery, OpenObserve links) confirmed working exactly as designed. One
thing to know for demo day: redeploying any service kills a `kubectl
port-forward` pointed at it — restart the port-forward afterward (see
`SETUP.md`'s troubleshooting table) or the browser tab will look frozen.

**GCP note:** per the team's decision, there is no live GCP deployment
for this demo. S5 below is the script for presenting the local→GCP
conceptual mapping instead of a console walkthrough — full detail lives
in `GCP_MAPPING.md`. Read it before your run so this doesn't land as "we
didn't do the GCP part."

**The one-sentence story to state once, up front, before any screen:**

> Nothing on screen is a canned incident. We generate real traffic
> against a real running multi-service application, break a real
> dependency on purpose, and everything you see afterward — logs,
> traces, metrics, the incident — is what OpenObserve actually captured
> as a result. Recovery is real too.

This maps directly to the PRD's own non-negotiable principle (§3): no
fake dashboard values, ever.

---

## 0. Pre-demo checklist (do this 10–15 min before judges arrive)

```bash
cd ~/claude-workspace/payasam/gcp-cluster

# 1. Confirm everything is up (see SETUP.md §3.6)
kubectl -n payasam get pods                 # all 7: 1/1 Running
curl -s http://localhost:5080/healthz       # {"status":"ok"}

# 2. Clear any leftover faults from testing/rehearsal
scripts/clear-faults.sh

# 3. Reset application data to a clean seeded state
scripts/reset.sh

# 3b. One-time (if not already done on this environment): create the
#     two demo alerts and the OpenObserve dashboard. Safe/idempotent to re-run.
scripts/configure-alerts.sh
scripts/configure-dashboard.sh
# If either alert still shows "firing" in the OpenObserve UI from
# earlier testing, wait ~5 minutes with no faults active — it clears on
# its own once the ERROR ages out of the lookback window.

# 4. Start port-forwards you'll use live (leave these running)
kubectl -n payasam port-forward svc/api-gateway 18080:8000 &
kubectl -n payasam port-forward svc/database 18081:8000 &
kubectl -n payasam port-forward svc/payasam-frontend 5173:80 &

# 5. Sanity-check the golden path once, so you're not debugging live
curl -s http://127.0.0.1:18080/health
curl -s http://127.0.0.1:5173/api/status   # confirm the UI's own proxy to the backend is up
```

Open browser tabs in advance:
- **Payasam UI:** `http://127.0.0.1:5173` — the primary screen for the demo
- **OpenObserve:** `http://localhost:5080` (log in with the account from
  your `.env` — never type the password where it's visible on a shared
  screen if you can paste it instead) — for deep technical evidence
- **KubeView:** `http://localhost:8000` (optional, infra-topology visual)

**Reminder if you redeploy anything between now and presenting:** a
`kubectl rollout restart` kills any `kubectl port-forward` pointed at
that service — restart the port-forward too, or the browser tab will
look frozen (`DECISIONS.md` D020).

---

## S1 — Problem → developers' monitoring challenge → proposed solution

*(MVP: open the monitoring dashboard, show overall status)*

**Say:** "Development teams running production services can't just
*hope* nothing breaks — they need to see failures as they happen, know
who and what is affected, and act fast. That's the developers'
monitoring challenge this use case names directly. Our proposed
solution is Payasam: a real multi-service application, instrumented
end-to-end, feeding a real observability backend, with a purpose-built
dashboard on top that turns raw telemetry into 'what's broken, who does
it hurt, what do I do about it' — answered from real data, never staged
numbers."

**MVP — open the dashboard and show overall status:**

```bash
kubectl -n payasam get pods -o wide
```

Open `http://127.0.0.1:5173`. **Say:** "Here's Payasam. Green status
pill, all five application services healthy — this is reading real
Kubernetes/application state every 3 seconds, not a static mock."

Optionally flip to the KubeView tab here — point at the pods/services it
shows live, as visual confirmation this is a real cluster, not a
diagram (PRD §26 — KubeView is supporting visual only, not the product).

---

## S2 — Dashboard/UI → log views → filters/visualization

*(MVP: filter/view logs and demonstrate the dashboard)*

**Say:** "Two dashboards work together here, deliberately not
duplicating each other: OpenObserve is the raw log/trace/metric
explorer — we didn't rebuild that. Payasam's own UI is everything on
top of it: business impact, remediation guidance, and the controls to
actually drive a demo."

**Payasam UI tour** (`http://127.0.0.1:5173`) — one page, three panels
always visible at once (no tab-switching, so the audience sees all
three update together):

- **Technical panel** — "what's the problem with the deployment right
  now": per-service reachability and the list of currently active
  faults, straight from `GET /status`, plus "Investigate further" links
  that open OpenObserve's real Logs/Traces/Alerts pages in a new tab.
- **Business panel** — "how does this affect my business, right now":
  affected users, failed transactions, estimated revenue at risk,
  downtime cost, and business criticality, from `GET /impact` — labeled
  `ESTIMATED / SIMULATED BUSINESS IMPACT` on screen, with the underlying
  assumptions always shown (PRD §19/§45) and a sparkline trend on the
  two headline numbers. Hover any stat tile for its exact formula.
- **Remediation panel** — "steps for how to fix it": a deterministic,
  rule-based recommended action per active fault (`GET /remediation`) —
  explicitly labeled as rule-based on screen, not claimed as AI (PRD
  §17).
- **Failure Simulator panel** — fault buttons, "Recover System," and
  "Generate Test Traffic" — used throughout S3/S7/S8 below.

**OpenObserve's Logs page — filter and demonstrate the dashboard:**

1. Open `http://localhost:5080`, log in (never type the password where
   the screen is projected; paste it), land in the `default`
   organization.
2. Click **Logs** in the left sidebar, pick the `default` stream.
3. Set the time range (top right) — "Past 15 minutes" is enough right
   after generating traffic.
4. Filter directly (SQL toggle off), e.g. `transaction_id = 'txn-...'`
   (copy a real one once you've generated traffic in S3), or with SQL
   on: `SELECT * FROM "default" WHERE transaction_id = 'txn-...'`.
5. Click **Run query**. **Say:** "Every log line here came from a real
   service handling a real request — this is the filter/visualization
   half of the log-monitoring requirement, working against real data."

---

## S3 — Log generation/application logic → log flow

*(MVP: generate an application event/error and show the resulting log)*

**Say:** "Here's the application generating the logs: API Gateway →
Order Service → Inventory/Payment → Database. Five real services,
running in a local Kubernetes cluster, each one producing structured
logs as a real side effect of doing real work — not a script writing
fake log lines." (Full service-by-service description: `GCP_MAPPING.md` §1.)

**Generate a real application event — click "Generate Test Traffic" in
the Payasam UI**, or from a terminal for a bigger, scripted burst:

```bash
scripts/run-baseline.sh 3 5 0.5 0.1 42
```

While it runs, say what's happening: "3 simulated users, 5 orders each,
hitting the real API Gateway over real HTTP." When it finishes, point at
the printed JSON summary — `success_count`, `latency_ms.p50`, etc. — as
the generator's own measured numbers, not something written for the
demo.

**Show the resulting log** — back in OpenObserve's Logs page (S2),
re-run the query for the `transaction_id` this request just produced.
**Say:** "`order_received`, `inventory_reserved`, `payment_succeeded`,
`order_completed` — one real transaction, four services, one shared
`transaction_id` tying the log flow together end-to-end."

---

## S4 — Log data → structure → processing/analysis

*(MVP: show a log entry being captured, filtered, or categorized)*

> **UC2's own guideline is explicit here:** *"S4 should not artificially
> present an SQLite database if the team's actual solution does not use
> one."* Payasam's `database` service genuinely *is* SQLite — so this
> section is about **log data**, not the application database. If asked
> about the database directly, answer honestly per `GCP_MAPPING.md` §1's
> callout (a deliberate, disclosed stand-in for Cloud SQL) — don't build
> S4 around it.

**Say:** "Every log record has the same structured shape, everywhere —
that consistency is what makes filtering and correlation possible."

1. In OpenObserve Logs, click any row to open **Source Details**. Point
   at the fields: `timestamp`, `service`, `severity`, `message`,
   `event_type`, `request_id`, `transaction_id`, `status_code` — the
   same whitelist every one of the 5 services emits from one shared
   `logging_utils.py` module (kept byte-identical on purpose).
2. Point at `trace_id`/`span_id` on that same record. **Say:** "This is
   the processing/analysis half — every log line carries the
   OpenTelemetry trace it happened inside of, so you can pivot from 'a
   log line says X failed' to 'here's the entire distributed request
   that failed,' not just a stack of disconnected strings."
3. Click **Traces** in the left sidebar, filter by that `trace_id`.
   **Say:** "This one request fanned out across all 5 services — API
   Gateway, Order, Inventory, Payment, Database — this is log data
   structured well enough to reconstruct the actual causal path: reserve
   inventory, charge payment, persist the order." Use the **Timeline**
   view (color-coded horizontal bars) for the clearest visual; point at
   individual span durations as the real per-hop latency breakdown (see
   `docs/telemetry-verification/example-trace.txt` for the exact shape
   to expect).
4. To jump back to correlated logs from any span: hover its **Operation
   Name** and click the magnifier icon, or open the span details panel
   and click **View Logs** next to the Span ID — "categorized" in the
   MVP demo's own words, both directions.

---

## S5 — Cloud/GCP architecture → cloud logging implementation

*(MVP: show logs arriving in the cloud service and dashboard)*

Full detail, the complete mapping table, and what actually changes to
move this to real GCP all live in **`GCP_MAPPING.md`** — open it
alongside this section. The script:

**Say, plainly, not apologetically:**

> "This entire environment intentionally runs on a local, GCP-shaped
> stack instead of live GCP, to keep the project's cloud spend at zero
> during development on a data-heavy simulation — every local component
> maps 1:1 to a specific GCP service."

Walk the headline mappings (full table in `GCP_MAPPING.md` §2):

| What you're looking at right now | Real GCP equivalent |
|---|---|
| 5 FastAPI services as Kubernetes Deployments in `kind` | Cloud Run (or GKE workloads) |
| OpenObserve (logs/metrics/traces/alerts/dashboards) | Cloud Logging + Cloud Monitoring + Cloud Trace |
| The `kind` cluster itself | GKE (Autopilot or Standard) |
| Kubernetes Secret (`openobserve-credentials`) | Secret Manager |
| The traffic-generator Kubernetes Job | Cloud Run Jobs / Cloud Scheduler |

**"Show logs arriving in the cloud service and dashboard"** — this is
literally S2/S3's demo replayed with the GCP framing on top: point back
at the Logs page from S2/S4 and say "this is standing in for Cloud
Logging; the dashboard on top of it — both OpenObserve's and Payasam's
own — is standing in for Cloud Monitoring."

**Close with the honest trade-off**, stated as a deliberate engineering
decision, not a limitation you're hoping nobody notices:

> "Every one of these local components was chosen specifically because
> it's the direct architectural equivalent of its GCP counterpart —
> moving this to real GCP is a redeploy of the same container images
> and manifests, not a redesign." (`GCP_MAPPING.md` §3 has the exact,
> short list of what would actually change.)

---

## S6 — Networking/deployment → application-to-cloud log flow

*(MVP: demonstrate the deployed application/resource generating logs)*

**Say:** "Every service runs as a real Kubernetes Deployment, and every
one of them exports its own telemetry directly — there's no
intermediate collector to point at and claim is 'doing the work.'"

```bash
kubectl -n payasam get deployments
kubectl -n payasam exec deployment/order-service -- env | grep OTEL_EXPORTER
# OTEL_EXPORTER_OTLP_ENDPOINT=http://<resolved-gateway-ip>:5080/api/default
```

**Say:** "That `OTEL_EXPORTER_OTLP_ENDPOINT` value is resolved
dynamically, never hardcoded — the local `kind` cluster and the
OpenObserve container run as two separate Docker networks on this
laptop, so reaching one from the other needed a real networking
solution: `scripts/configure-telemetry.sh` resolves the Docker bridge
gateway IP fresh every time, verifies OpenObserve is reachable *from
inside the cluster* before applying it, and re-resolves it any time the
cluster is recreated, since that address isn't guaranteed stable
(`DECISIONS.md` D011 — this was caught and fixed during Phase 0: a naive
version of this script grabbed the wrong network's gateway entirely)."

**Demonstrate the deployed resource actually generating logs live:**

```bash
kubectl -n payasam logs deployment/order-service --tail=20 -f
```

Trigger one request from another terminal or the UI's "Generate Test
Traffic," and point at the log line appearing in real time in this
terminal *and* in OpenObserve simultaneously — the same event, two
views, because it's one real deployed process producing it, not two
separate demo tracks.

---

## S7 — Alerts/optimization/advanced visualization

*(MVP: trigger an error/threshold and demonstrate an alert)*

**Say:** "Alert thresholds and dashboards both exist as real,
queryable OpenObserve objects — not because we clicked a demo toggle,
but because they're real SQL conditions evaluated on a schedule against
the same telemetry stream."

**OpenObserve Alerts** — two real alerts exist
(`scripts/configure-alerts.sh`): `payasam-any-service-error` (any
service, `severity = 'ERROR'`) and `payasam-payment-degraded`
(payment-service specifically). Both check every minute against a
5-minute lookback window.

1. Click **Alerts** in the left sidebar. **Say (before injecting
   anything):** "These aren't decorative — each one is a real SQL query
   against the same telemetry we just looked at. Watch what happens
   when we break something." Point out both show a healthy/not-firing
   state.
2. **Trigger the threshold — in the Payasam UI, click "Inject Payment
   Errors,"** then **"Generate Test Traffic."**
3. Come back to the Alerts page within about a minute —
   `payasam-payment-degraded` (and shortly after, the general watchdog)
   flips to a **firing** state, driven entirely by the real ERROR log
   the fault produced. **Say:** "This directly answers 'set alert
   thresholds' — the threshold is `severity = 'ERROR'` count ≥ 1 in 5
   minutes, evaluated for real, not toggled by a demo button."
4. Back in the Payasam UI, the **Business panel** updates the same way
   at the same time — point at both updating together as two views of
   one real event.

**Advanced visualization — the native OpenObserve dashboard**
(`scripts/configure-dashboard.sh`, "Payasam - Live Error Overview"):

1. Click **Dashboards** in the left sidebar, open it.
2. **Say:** "Three real-time panels, backed by the exact same
   definitions Payasam's own alerts and business-impact numbers use —
   order-service failures, all-service error count, and how many
   services are currently affected. This satisfies 'visualize logs in a
   dashboard' using OpenObserve's own native feature, not custom chart
   code — we don't rebuild what the platform already does well."

---

## S8 — Testing → error scenarios → system health/impact

*(MVP: generate an intentional error and show detection/alert)*

This is the main event — the PRD's primary demo scenario (§25), driven
entirely from the Payasam UI.

**Baseline reminder (say this out loud):** "Payment currently responds
in well under a second, no errors." Point at the UI header's status
pill (green, "HEALTHY").

**Inject an intentional error — click "Inject Payment Errors" in the
Failure Simulator panel.**

**Say while it rolls out (a few seconds):** "This isn't writing a fake
incident anywhere — clicking that button called a real API, which used
a scoped Kubernetes permission to set a real environment variable,
causing the real payment-service process to actually reject requests,
then it restarts. Watch the status pill."

**Click "Generate Test Traffic"** — sends 5 real orders through
api-gateway from the backend itself, no terminal needed.

**Show detection, live, across every surface at once:**
- **Payasam Technical panel:** "Active Faults" lists
  `PAYASAM_FAULT_PAYMENT_ERROR_RATE` against `payment-service`; the
  activity feed logs `System status: healthy → degraded` — detected by
  the system polling, not caused by your click directly.
- **Payasam Business panel:** `Failed Transactions`, `Estimated Revenue
  at Risk`, `Estimated Downtime Cost`, and `Affected Users` all update to
  real, non-zero numbers within seconds. **Say:** "These aren't
  hardcoded — hover any tile for the exact formula, or click Remediation
  and I'll show you why."
- **Payasam Remediation panel:** shows "Payment Service is rejecting
  requests" and the recommended action. **Say:** "Rule-based, derived
  directly from which fault is active — not a generic AI claim."
- **OpenObserve Alerts (S7):** `payasam-payment-degraded` fires from the
  same real ERROR logs.

**Say:** "Note inventory is not left stuck reserved — the order service
compensates and releases it on every one of these failure paths. That's
real state consistency, not just a real error message."

**Optional — a harder error scenario: click "Take Payment Service
Down."** The Technical panel will show `payment-service: unreachable` —
and interestingly, `order-service` too, since its own readiness probe
depends on payment-service being reachable and Kubernetes removes a
NotReady pod from its Service's routable endpoints entirely. **Say:**
"That's a real, if slightly surprising, cascading effect of how strict
this readiness check is — not a bug we're hiding, an accurate reading of
a genuine emergent property of the system."

**Recover and show system health returning — click "Recover System."**

Wait a few seconds for the status pill to turn green again (the
unavailability scenario takes longer — the scaled-down pod has to come
back and pass its own health checks), then click "Generate Test
Traffic" once more and point at the Business panel returning to zero.
**Say:** "Same button, same system — this is a real recovery, not a
reset dashboard."

**Testing philosophy, briefly, if asked "how do you know this all
actually works":** every one of these behaviors is covered by an
automated test that hits the real, deployed system — not a mock. As of
the last full run: **114 backend tests + 10 frontend tests, all
passing**, including live tests that inject a real fault, generate real
traffic, and assert on the real resulting numbers. Several real bugs
(a Kubernetes API quirk, a 409 concurrency conflict, a display-rounding
inconsistency, a CSS overflow bug) were caught exactly this way, not by
code review alone — full account in `DECISIONS.md` D013–D020.

---

## Wrap-up talking points

- Everything shown was generated by a real 5-service app under real
  (if small-scale) load — 10–50 simulated users, deliberately, per the
  PRD's scale philosophy (§4): the point is proving the causal chain
  works, not raw throughput.
- The one deliberately synthetic piece is the `database` service itself
  — see `GCP_MAPPING.md` §1's callout. Say this proactively, don't wait
  to be asked — it's a disclosed simulation assumption, not something to
  be caught hiding (and directly addresses UC2's own S4 warning).
- Every business-impact number is `actual observed failures × configured
  assumption` — computed live from real OpenObserve data on every
  request, never a hardcoded headline number (PRD §45).
- No live GCP deployment, by deliberate cost-conscious choice, with a
  documented 1:1 mapping (`GCP_MAPPING.md`) instead — say this plainly
  in S5, don't wait for it to be raised as a gap.
