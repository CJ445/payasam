#!/usr/bin/env bash
# Enable exactly one Phase 4 controlled fault on a target Deployment.
# Triggers a normal rolling restart of that Deployment only -- nothing
# else in the cluster is touched.
#
# Usage: scripts/set-fault.sh <deployment> <PAYASAM_FAULT_VAR> <value>
# Examples:
#   scripts/set-fault.sh payment-service PAYASAM_FAULT_PAYMENT_LATENCY_MS 500
#   scripts/set-fault.sh payment-service PAYASAM_FAULT_PAYMENT_ERROR_RATE 1.0
#   scripts/set-fault.sh database PAYASAM_FAULT_DB_LATENCY_MS 200
set -euo pipefail

NAMESPACE="payasam"
DEPLOYMENT="${1:?usage: set-fault.sh <deployment> <PAYASAM_FAULT_VAR> <value>}"
VAR="${2:?usage: set-fault.sh <deployment> <PAYASAM_FAULT_VAR> <value>}"
VALUE="${3:?usage: set-fault.sh <deployment> <PAYASAM_FAULT_VAR> <value>}"

case "$VAR" in
  PAYASAM_FAULT_*) ;;
  *) echo "ERROR: refusing to set non-fault env var '${VAR}' via this script." >&2; exit 1 ;;
esac

echo "== Setting ${VAR}=${VALUE} on deployment/${DEPLOYMENT} =="
kubectl -n "${NAMESPACE}" set env "deployment/${DEPLOYMENT}" "${VAR}=${VALUE}"

echo "== Waiting for rollout =="
kubectl -n "${NAMESPACE}" rollout status "deployment/${DEPLOYMENT}" --timeout=90s

echo "== Confirming fault is active in the running pod's logs =="
sleep 2
kubectl -n "${NAMESPACE}" logs "deployment/${DEPLOYMENT}" --tail=20 | grep -i "fault_injection_active\|FAULT INJECTION ACTIVE" || \
  echo "WARNING: no fault-active log line found yet -- check manually if this is unexpected."
