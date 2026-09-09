#!/usr/bin/env bash
# start.sh — quick daily convenience script: get the Payasam UI open in a
# browser with minimal fuss.
#
# What it does:
#   1. Checks whether the `openobserve` Docker container is up. If it
#      exists but is stopped, starts it. Never creates it from scratch
#      (matches DECISIONS.md D010 — Payasam only ever verifies/starts the
#      existing OpenObserve container, never auto-creates or replaces it).
#   2. Sanity-checks the `payasam-frontend` Deployment is reachable via
#      kubectl (it must already be deployed — this script does not build
#      or apply anything).
#   3. Starts a `kubectl port-forward` to payasam-frontend, but only if
#      one isn't already running (idempotent — re-running this script when
#      the UI is already up does nothing).
#
# This is NOT the full environment bring-up. To build and deploy all 5
# application services + payasam-backend + payasam-frontend from scratch,
# use gcp-cluster/scripts/start.sh instead.

set -euo pipefail

FRONTEND_PORT=5173
FRONTEND_SVC=payasam-frontend
NAMESPACE=payasam
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${SCRIPT_DIR}/.frontend-port-forward.pid"
LOG_FILE="/tmp/payasam-frontend-port-forward.log"

echo "== Payasam quick start =="

# --- 1. OpenObserve ---------------------------------------------------
if docker ps --filter "name=^openobserve$" --filter "status=running" --format '{{.Names}}' | grep -q .; then
  echo "[openobserve] already running"
elif docker ps -a --filter "name=^openobserve$" --format '{{.Names}}' | grep -q .; then
  echo "[openobserve] container exists but is stopped — starting it"
  docker start openobserve >/dev/null
  sleep 2
  if curl -sf -m 5 http://localhost:5080/healthz >/dev/null 2>&1; then
    echo "[openobserve] up: http://localhost:5080"
  else
    echo "[openobserve] started, but health check hasn't responded yet (may still be booting — give it a few seconds)"
  fi
else
  echo "[openobserve] no container named 'openobserve' found — this script never creates it (see DECISIONS.md D010)."
  echo "              create it manually first (see SETUP.md section 3.1), then re-run this script."
  exit 1
fi

# --- 2. Cluster / deployment reachability ------------------------------
if ! kubectl -n "$NAMESPACE" get deployment "$FRONTEND_SVC" >/dev/null 2>&1; then
  echo "[frontend] cannot reach the '${FRONTEND_SVC}' deployment via kubectl."
  echo "           is the kind cluster running? try: docker start payasam-control-plane"
  echo "           or run the full gcp-cluster/scripts/start.sh first to deploy everything."
  exit 1
fi

# --- 3. Frontend port-forward (idempotent) ------------------------------
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[frontend] already running (pid $(cat "$PID_FILE")) at http://127.0.0.1:${FRONTEND_PORT}"
  exit 0
fi
rm -f "$PID_FILE"

if curl -sf -m 2 "http://127.0.0.1:${FRONTEND_PORT}" >/dev/null 2>&1; then
  echo "[frontend] something is already listening on port ${FRONTEND_PORT} (not started by this script) — leaving it alone"
  exit 0
fi

echo "[frontend] waiting for the deployment to be ready..."
kubectl -n "$NAMESPACE" rollout status deployment/"$FRONTEND_SVC" --timeout=60s

echo "[frontend] starting port-forward on ${FRONTEND_PORT}..."
nohup kubectl -n "$NAMESPACE" port-forward "svc/${FRONTEND_SVC}" "${FRONTEND_PORT}:80" \
  > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "[frontend] up: http://127.0.0.1:${FRONTEND_PORT}"
else
  echo "[frontend] port-forward failed to start — check ${LOG_FILE}"
  rm -f "$PID_FILE"
  exit 1
fi
