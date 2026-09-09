#!/usr/bin/env bash
# Disable every Phase 4 fault on every service, restoring normal
# behavior. Safe to run even if no faults are currently set (kubectl
# just no-ops on the unset for a var that isn't present).
set -euo pipefail

NAMESPACE="payasam"

for dep in payment-service database; do
  echo "== Clearing faults on deployment/${dep} =="
  kubectl -n "${NAMESPACE}" set env "deployment/${dep}" \
    PAYASAM_FAULT_PAYMENT_LATENCY_MS- \
    PAYASAM_FAULT_PAYMENT_ERROR_RATE- \
    PAYASAM_FAULT_DB_LATENCY_MS- \
    2>&1 | grep -v "^deployment.apps.*not found in" || true
done

for dep in payment-service database; do
  kubectl -n "${NAMESPACE}" rollout status "deployment/${dep}" --timeout=90s
done

echo "== Confirming no fault-active log lines remain =="
sleep 2
for dep in payment-service database; do
  echo "-- ${dep} --"
  kubectl -n "${NAMESPACE}" logs "deployment/${dep}" --tail=20 | grep -i "fault_injection_active\|FAULT INJECTION ACTIVE" \
    && echo "WARNING: fault still appears active on ${dep}" \
    || echo "OK: no active fault"
done
