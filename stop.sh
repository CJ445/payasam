#!/usr/bin/env bash
# stop.sh — undo start.sh: kill the frontend port-forward it started, and
# stop the openobserve container. Does not touch the kind cluster or any
# Kubernetes Deployments/pods — symmetric with what start.sh actually
# started.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${SCRIPT_DIR}/.frontend-port-forward.pid"

echo "== Payasam quick stop =="

# --- 1. Frontend port-forward ------------------------------------------
if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "[frontend] stopped port-forward (pid ${PID})"
  else
    echo "[frontend] pid file present but process not running (already stopped)"
  fi
  rm -f "$PID_FILE"
else
  echo "[frontend] no port-forward pid file found — nothing to stop"
fi

# --- 2. OpenObserve ------------------------------------------------------
if docker ps --filter "name=^openobserve$" --filter "status=running" --format '{{.Names}}' | grep -q .; then
  docker stop openobserve >/dev/null
  echo "[openobserve] stopped"
else
  echo "[openobserve] already stopped"
fi
