#!/usr/bin/env bash
# Creates/updates the Kubernetes Secret holding OpenObserve credentials
# for OTLP export, from environment variables you provide -- never from a
# tracked file. Typical usage:
#
#   set -a; source .env; set +a; scripts/create-otel-secret.sh
#
# where your local, git-ignored .env sets OPENOBSERVE_USERNAME and
# OPENOBSERVE_PASSWORD (see .env.example for the placeholder shape). This
# script never writes those values to disk -- it pipes a rendered Secret
# manifest directly into `kubectl apply`, in memory, and the values are
# never echoed.
set -euo pipefail

NAMESPACE="payasam"

: "${OPENOBSERVE_USERNAME:?Set OPENOBSERVE_USERNAME in your environment (e.g. via a sourced local .env) before running this script}"
: "${OPENOBSERVE_PASSWORD:?Set OPENOBSERVE_PASSWORD in your environment (e.g. via a sourced local .env) before running this script}"

kubectl -n "${NAMESPACE}" create secret generic openobserve-credentials \
  --from-literal=OPENOBSERVE_USERNAME="${OPENOBSERVE_USERNAME}" \
  --from-literal=OPENOBSERVE_PASSWORD="${OPENOBSERVE_PASSWORD}" \
  --dry-run=client -o yaml \
  | kubectl apply -f -

echo "openobserve-credentials Secret created/updated in namespace ${NAMESPACE} (values not echoed)."
