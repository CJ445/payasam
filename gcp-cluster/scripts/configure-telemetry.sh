#!/usr/bin/env bash
# Renders and applies the telemetry-config ConfigMap: resolves the kind
# bridge network's IPv4 gateway address (the mechanism verified in
# Phase 0 / DECISIONS.md D011 -- never hardcoded, re-resolved every time
# this script runs) and points OTEL_EXPORTER_OTLP_ENDPOINT at OpenObserve
# through it. Does not touch Phase 0's networking mechanism itself.
#
# Usage: scripts/configure-telemetry.sh [openobserve_org]
set -euo pipefail

NAMESPACE="payasam"
OPENOBSERVE_ORG="${1:-${OPENOBSERVE_ORG:-default}}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Resolving kind network IPv4 gateway (same mechanism as Phase 0) =="
GATEWAY_IP=$(docker network inspect kind --format '{{json .IPAM.Config}}' \
  | jq -r '.[] | select(.Subnet | contains(":") | not) | .Gateway')

if [[ -z "${GATEWAY_IP}" ]]; then
  echo "ERROR: could not resolve the kind network's IPv4 gateway. Is the kind cluster running?" >&2
  exit 1
fi
echo "Resolved gateway IP: ${GATEWAY_IP}"

echo "== Verifying OpenObserve is reachable from inside the cluster at this address =="
kubectl -n "${NAMESPACE}" run telemetry-config-check \
  --image=curlimages/curl:8.10.1 --restart=Never --rm -i --pod-running-timeout=60s \
  --command -- curl -sS -m 5 -o /dev/null -w "HTTP_STATUS:%{http_code}" "http://${GATEWAY_IP}:5080/healthz" \
  || { echo "ERROR: OpenObserve not reachable from inside the cluster at ${GATEWAY_IP}:5080" >&2; exit 1; }
echo

export GATEWAY_IP OPENOBSERVE_ORG
RENDERED="$(mktemp)"
envsubst < "${ROOT_DIR}/infrastructure/kubernetes/telemetry/telemetry-config.yaml.template" > "${RENDERED}"

echo "== Applying telemetry-config ConfigMap (org=${OPENOBSERVE_ORG}) =="
kubectl apply -f "${RENDERED}"
rm -f "${RENDERED}"

echo "telemetry-config ConfigMap set: OTEL_EXPORTER_OTLP_ENDPOINT=http://${GATEWAY_IP}:5080/api/${OPENOBSERVE_ORG}"
