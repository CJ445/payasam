#!/usr/bin/env bash
# Phase 0 connectivity verification.
#
# Proves that a workload running inside the `kind` cluster can reach the
# host-run OpenObserve container's /healthz endpoint, using the Docker
# bridge gateway IP mechanism documented in DECISIONS.md (D011).
#
# Does NOT touch OpenObserve credentials — only the unauthenticated
# /healthz endpoint is used, per Phase 0 scope.
#
# Usage: scripts/verify-openobserve-connectivity.sh
set -euo pipefail

NAMESPACE="payasam"
POD_NAME="connectivity-test"
CLUSTER_CONTEXT="kind-payasam"

cleanup() {
  kubectl --context "$CLUSTER_CONTEXT" delete pod "$POD_NAME" -n "$NAMESPACE" --ignore-not-found=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Resolving the kind network's IPv4 gateway address (this is the address"
echo "the Docker host is reachable at from inside kind nodes on native Linux"
echo "Docker Engine, since host.docker.internal does not resolve here)..."

GATEWAY_IP=$(docker network inspect kind --format '{{json .IPAM.Config}}' \
  | jq -r '.[] | select(.Subnet | contains(":") | not) | .Gateway')

if [[ -z "${GATEWAY_IP}" ]]; then
  echo "ERROR: could not resolve the kind network's IPv4 gateway. Is the kind cluster running?" >&2
  exit 1
fi

echo "Resolved gateway IP: ${GATEWAY_IP}"
echo

kubectl --context "$CLUSTER_CONTEXT" get namespace "$NAMESPACE" >/dev/null 2>&1 \
  || kubectl --context "$CLUSTER_CONTEXT" create namespace "$NAMESPACE"

cleanup

echo "Launching a temporary in-cluster pod to call http://${GATEWAY_IP}:5080/healthz ..."
kubectl --context "$CLUSTER_CONTEXT" run "$POD_NAME" \
  -n "$NAMESPACE" \
  --image=curlimages/curl:8.10.1 \
  --restart=Never \
  --command -- sh -c "sleep 300"

kubectl --context "$CLUSTER_CONTEXT" wait --for=condition=Ready "pod/${POD_NAME}" -n "$NAMESPACE" --timeout=90s

RESPONSE=$(kubectl --context "$CLUSTER_CONTEXT" exec "$POD_NAME" -n "$NAMESPACE" -- \
  curl -sS -m 5 -w '\nHTTP_STATUS:%{http_code}' "http://${GATEWAY_IP}:5080/healthz")

echo "$RESPONSE"
echo

STATUS=$(echo "$RESPONSE" | grep -o 'HTTP_STATUS:[0-9]*' | cut -d: -f2)

if [[ "$STATUS" == "200" ]]; then
  echo "OK: OpenObserve reachable from inside the kind cluster at ${GATEWAY_IP}:5080 (HTTP 200)."
  exit 0
else
  echo "FAIL: expected HTTP 200 from inside the cluster, got '${STATUS}'." >&2
  exit 1
fi
