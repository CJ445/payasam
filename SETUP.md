# SETUP.md — Payasam Local Environment

Developer-facing runbook for bringing the Payasam local environment up,
checking it's healthy, and tearing it down. This is *not* the demo script
(see `DEMO.md` for that) — this is "how do I get the thing running."

All commands assume you're in `~/claude-workspace/payasam/gcp-cluster`
unless stated otherwise.

```bash
cd ~/claude-workspace/payasam/gcp-cluster
```

---

## 1. Prerequisites

Verified available on this machine:

| Tool | Purpose |
|---|---|
| `docker` | runs OpenObserve, KubeView, and the `kind` cluster's node container |
| `kind` | local Kubernetes cluster (control-plane node named `payasam`) |
| `kubectl` | talk to the cluster |
| `python3` + `pip` | run tests, ad-hoc scripts |
| `jq` | used by scripts to parse `docker network inspect` output |

Check them:

```bash
docker version
kind version
kubectl version --client
python3 -m pytest --version
```

## 2. One-time: local `.env`

Copy the template and fill in your real local OpenObserve credentials
(never commit `.env` — it's git-ignored):

```bash
cp .env.example .env
# edit .env: set OPENOBSERVE_PASSWORD to your actual local OpenObserve
# root password (the one you set when you created/recreated the
# openobserve container). Never put this value in any other tracked file.
```

Several commands below need these values in your shell environment. Load
them when needed with:

```bash
set -a; source .env; set +a
```

---

## 3. Bringing everything up (fresh machine boot)

Do these in order. Each step includes how to confirm it worked.

### 3.1 Start OpenObserve

If the container already exists (it should, after the first time):

```bash
docker start openobserve
sleep 3
curl -s http://localhost:5080/healthz
# expect: {"status":"ok"}
```

If it doesn't exist yet (brand new machine), create it — **never** hardcode
the real password in a shell history-visible form if you can avoid it;
prefer sourcing it from your local `.env`:

```bash
set -a; source .env; set +a
docker run -d \
  --name openobserve \
  -p 5080:5080 \
  -e ZO_ROOT_USER_EMAIL="${OPENOBSERVE_USERNAME}" \
  -e ZO_ROOT_USER_PASSWORD="${OPENOBSERVE_PASSWORD}" \
  openobserve/openobserve:latest
```

Do **not** recreate/replace the container if it already exists — per
`DECISIONS.md` D010, Payasam only ever verifies OpenObserve, it never
auto-creates or replaces it.

### 3.2 Start (or verify) the `kind` cluster

```bash
kind get clusters
# expect: payasam
```

If it's missing entirely (rare — only after `kind delete cluster`):

```bash
kind create cluster --config infrastructure/kind/kind-config.yaml
kubectl wait --for=condition=Ready node/payasam-control-plane --timeout=120s
```

If `kind get clusters` already shows `payasam`, the control-plane
container just needs to be running (it's a normal Docker container under
the hood):

```bash
docker start payasam-control-plane
kubectl get nodes
# expect: payasam-control-plane   Ready
```

### 3.3 Build and deploy the application

```bash
scripts/start.sh
```

This builds the 5 application service images + `payasam-backend` +
`payasam-frontend` (7 images total) + the traffic-generator image, loads
them into `kind` (no registry needed), applies the Kubernetes manifests,
and waits for every Deployment to become `Ready`. It ends by printing
`kubectl get pods -o wide` — confirm all 7 application/backend/frontend
pods show `1/1 Running` (plus the traffic-generator, which runs to
completion as a `Job` rather than staying `Running`).

### 3.4 Wire up telemetry (OpenObserve connectivity)

The cluster reaches OpenObserve via the `kind` Docker bridge network's
gateway IP, which is **re-resolved every time**, never hardcoded (D011).
Safe to run this even if nothing changed:

```bash
scripts/configure-telemetry.sh
```

This resolves the gateway IP, verifies OpenObserve is reachable *from
inside the cluster* at that address, and applies the `telemetry-config`
ConfigMap. Then create/refresh the credentials Secret (values are piped
straight into `kubectl apply`, never written to disk or echoed):

```bash
set -a; source .env; set +a
scripts/create-otel-secret.sh
```

> Only needed again if the `kind` cluster was deleted/recreated, or the
> credentials changed. Both are safe/idempotent to re-run at any time.

### 3.5 (Optional) Start KubeView

KubeView is a read-only Kubernetes topology viewer used to visually show
"this is a real cluster" during the demo (PRD §26) — it is **not** part
of the Payasam product itself.

```bash
docker start kubeview
# first time only:
# docker run -d --name kubeview --network host \
#   -e PORT=8000 -v ~/.kube:/root/.kube:ro ghcr.io/benc-uk/kubeview:latest
```

Open `http://localhost:8000` in a browser.

### 3.6 Confirm everything is healthy

```bash
kubectl -n payasam get pods
# all 7 (5 application services + payasam-backend + payasam-frontend)
# should show 1/1 Running

kubectl -n payasam get configmap,secret
# expect: configmap/telemetry-config, secret/openobserve-credentials

curl -s http://localhost:5080/healthz
docker ps --format "table {{.Names}}\t{{.Status}}"
```

---

## 4. Everyday commands

### Pod status / logs

```bash
kubectl -n payasam get pods -o wide
kubectl -n payasam logs deployment/<service-name> --tail=50 -f
# e.g. kubectl -n payasam logs deployment/order-service -f
```

### Reach a service from your machine

```bash
kubectl -n payasam port-forward svc/api-gateway 18080:8000 &
curl http://127.0.0.1:18080/health
```

Do the same for `svc/database` on a different local port if you need
direct DB-service access (`/products/{id}`, `/orders/{id}`, etc.).

### Restart a single service (e.g. after an env var change)

```bash
kubectl -n payasam rollout restart deployment/<service-name>
kubectl -n payasam rollout status deployment/<service-name> --timeout=90s
```

### Reset application data to the seeded state

Restores product stock (`product-001`/`002`/`003`) and clears
orders/payments. Does **not** touch faults, the cluster, or OpenObserve.

```bash
scripts/reset.sh
```

---

## 5. Running the test suite

```bash
python3 -m pytest tests/unit          # in-process, no cluster needed
python3 -m pytest tests/integration   # needs the live cluster from §3
python3 -m pytest tests/              # everything
```

Notes:
- `tests/integration` uses `kubectl port-forward` itself — don't have a
  conflicting port-forward already running on 18080/18081.
- One integration test (`test_telemetry_live.py`) needs real OpenObserve
  credentials in your shell (`OPENOBSERVE_USERNAME`/`OPENOBSERVE_PASSWORD`
  — `set -a; source .env; set +a` first) and **skips cleanly** without
  them.
- `tests/integration/test_payment_failure.py` temporarily scales
  `payment-service` to 0 replicas; a fixture guarantees it's restored to
  1 afterward even if the test fails.

---

## 6. Generating traffic

Run a one-shot experiment as a Kubernetes Job (resets data first, waits
for completion, saves JSON to `docs/baseline-results/`):

```bash
scripts/run-baseline.sh <users> [requests_per_user] [interval] [jitter] [seed]
# e.g. a quick 3-user burst:
scripts/run-baseline.sh 3 5 0.5 0.1 42
```

Or run the generator directly against a port-forwarded gateway (useful
while iterating):

```bash
kubectl -n payasam port-forward svc/api-gateway 18080:8000 &
python3 simulator/traffic/generator.py \
  --gateway-url http://127.0.0.1:18080 \
  --users 3 --requests-per-user 5 --interval 0.5 --jitter 0.1 --seed 42
```

Keep volumes small — this is a 10–50 simulated user demo, not a load
test (PRD §4). There are only 35 total seeded product units across all 3
products; higher volumes will legitimately hit `409 insufficient_inventory`.

---

## 7. Fault injection (Phase 4)

| Command | Effect |
|---|---|
| `scripts/set-fault.sh payment-service PAYASAM_FAULT_PAYMENT_LATENCY_MS 500` | payment-service adds 500ms latency to every `/payments` call |
| `scripts/set-fault.sh payment-service PAYASAM_FAULT_PAYMENT_ERROR_RATE 1.0` | payment-service rejects every `/payments` call with `503` |
| `scripts/set-fault.sh database PAYASAM_FAULT_DB_LATENCY_MS 200` | database adds 200ms latency inside its lock on every reserve/create call |
| `kubectl -n payasam scale deployment/payment-service --replicas=0` | payment-service becomes fully unreachable (Scenario D) |
| `scripts/clear-faults.sh` | disables every fault on `payment-service` and `database`, restarts both |

Each `set-fault.sh` call triggers a normal rolling restart of just that
one Deployment and confirms the fault is active via the pod's own
startup log line. After clearing a fault (or scaling `payment-service`
back to 1), **wait for `/ready` to actually pass** before sending
traffic again — see `docs/phase4-failure-experiments.md` §7 for the
readiness-race gotcha:

```bash
kubectl -n payasam rollout status deployment/payment-service --timeout=90s
kubectl -n payasam port-forward svc/payment-service 18082:8000 &
until curl -sf http://127.0.0.1:18082/ready >/dev/null; do sleep 1; done
```

---

## 8. Alerts (Phase: log monitoring requirement)

Creates two real OpenObserve alerts evaluated against actual telemetry —
directly satisfies the "set alert thresholds" / "error alerts" hackathon
requirement, which nothing else in this repo touches:

```bash
scripts/configure-alerts.sh
```

| Alert | Fires when |
|---|---|
| `payasam-any-service-error` | any service logs `severity = 'ERROR'` in the last 5 minutes |
| `payasam-payment-degraded` | `service = 'payasam-payment-service'` logs `severity = 'ERROR'` in the last 5 minutes |

Both check every 1 minute, so they fire visibly fast during a live fault
demo (`scripts/set-fault.sh payment-service PAYASAM_FAULT_PAYMENT_ERROR_RATE 1.0`).
Idempotent — safe to re-run; it skips anything that already exists.

**Note on the destination:** the alert *evaluation* is 100% local (real
telemetry, real SQL query, real "Triggered" state in OpenObserve's UI) —
but OpenObserve's own SSRF guard refuses any destination URL that
resolves to a private/loopback IP, so the notification-delivery target
had to be a public echo endpoint (`https://httpbin.org/post`). This only
affects whether a demo notification payload is visibly delivered
somewhere; it's not required for the alert to fire or to show as
triggered in the UI (see `DECISIONS.md` D013).

---

## 9. Business impact endpoint (scoped-down Phase 5)

`payasam-backend` is deployed alongside the 5 application services by
`scripts/start.sh` (no separate step needed). It exposes real,
formula-based business-impact numbers computed from actual OpenObserve
data — see `DECISIONS.md` D014 for exactly what's in and out of scope.

```bash
kubectl -n payasam port-forward svc/payasam-backend 18083:8000 &
curl "http://127.0.0.1:18083/impact?window_minutes=5"
# optional: &business_function=payment (default: order)
```

Healthy system → all zeros. After injecting a fault (§7) and sending
traffic, `failed_transactions`, `estimated_revenue_at_risk_inr`, and
`estimated_downtime_cost_inr` reflect what actually happened in the last
`window_minutes`. `affected_users` is intentionally `null` — `user_id`
isn't currently present in exported telemetry (D014), not fabricated.

---

## 9b. Payasam UI

The custom Payasam frontend — Failure Simulator buttons plus a
Technical/Business/Remediation toggle — is deployed alongside everything
else by `scripts/start.sh`. One port-forward is enough; the frontend's
own nginx reverse-proxies `/api/*` to `payasam-backend` internally, so
the browser never needs a second one:

```bash
kubectl -n payasam port-forward svc/payasam-frontend 5173:80 &
```

Open `http://127.0.0.1:5173` in a browser. See `DECISIONS.md` D015 for
why fault injection from the UI needed a Kubernetes RBAC grant, and
`DEMO.md` for how to actually present it.

**Click-tested live in a real browser (`DECISIONS.md` D020):** the full
loop (inject → generate traffic → watch all three panels update →
recover) confirmed working exactly as designed. One genuine layout bug
was found this way (a long fault name overflowing its table, invisible
to `npm run build`/bundle inspection/unit tests) and is already fixed.
Also worth knowing: redeploying any service kills a `kubectl
port-forward` pointed at it (see §12's troubleshooting table) — restart
the port-forward afterward or the browser tab will look frozen.

---

## 10. Querying OpenObserve directly (CLI, no browser)

Useful for scripting/checking without opening the UI. Runs from inside a
pod that already has credentials injected, so you never type your
password on the command line:

```bash
scripts/otel-query.sh logs   "SELECT * FROM \"default\" WHERE transaction_id = 'txn-...' ORDER BY _timestamp DESC LIMIT 20"
scripts/otel-query.sh traces "SELECT service_name, operation_name FROM \"default\" WHERE trace_id = '...' ORDER BY start_time ASC LIMIT 200"
```

---

## 11. Tearing down

Stop things in reverse order. All of this is safe to redo via §3 later.

```bash
# stop any background port-forwards you started
jobs -p | xargs -r kill

# stop the application (data/cluster untouched)
kubectl -n payasam scale deployment --all --replicas=0

# fully remove the app (keeps the cluster + telemetry config)
kubectl delete -k infrastructure/kubernetes

# stop the cluster's node container (keeps the cluster definition)
docker stop payasam-control-plane

# stop OpenObserve / KubeView
docker stop openobserve kubeview
```

**Destructive — only if you actually want to start over:**

```bash
kind delete cluster --name payasam   # deletes the cluster entirely;
                                      # re-run all of §3 afterward
```

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| OpenObserve container `Exited` on start, logs show password-policy error | password doesn't meet OpenObserve's strength policy (upper+lower+digit+special) | pick a compliant password, recreate the container (see §3.1) |
| Pods crash-loop right after a fresh image build | missing `setuptools`/`pkg_resources` in `python:3.13-slim` (seen in Phase 3) | already fixed in `requirements.txt` for all 5 services; if you see this again after adding a new dependency, add `setuptools` to that service's `requirements.txt` |
| Telemetry not showing up in OpenObserve | stale gateway IP after cluster recreation (D011) | re-run `scripts/configure-telemetry.sh` |
| Spurious timeouts right after clearing a fault / scaling a service back up | readiness-probe cache lag (see §7) | poll `/ready` before sending traffic, don't just check `kubectl rollout status` |
| Integration tests fail to start port-forward | a leftover port-forward is already bound to 18080/18081 | `jobs -p \| xargs -r kill`, retry |
| `test_telemetry_live.py` skipped | `OPENOBSERVE_USERNAME`/`_PASSWORD` not in your shell | `set -a; source .env; set +a` before running pytest |
| `configure-alerts.sh` fails with `Destination URL blocked by SSRF guard` | you pointed the destination at a private/loopback IP | use a public URL (script already does this — see D013) |
| Alerts page shows `payasam-*` alerts already "firing" right when you open OpenObserve | a real ERROR was logged recently (e.g. leftover from testing/rehearsal) and hasn't aged out of the 5-minute lookback yet | wait ~5 minutes with no faults active — it clears on its own; not a bug |
| Browser tab showing the Payasam UI freezes / stops updating after you redeploy a service | `kubectl port-forward` pins to a specific pod and dies when a `kubectl rollout restart` replaces it — confirmed live (D020) | restart the port-forward (`kubectl -n payasam port-forward svc/payasam-frontend 5173:80 &`) after any rollout touching a service you have port-forwarded |
