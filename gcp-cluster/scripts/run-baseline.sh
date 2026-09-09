#!/usr/bin/env bash
# Run one traffic-generator experiment as a Kubernetes Job, reset the
# database first (known starting state, not failure injection), wait for
# completion, save the JSON results, and clean up the Job.
#
# Usage: scripts/run-baseline.sh <users> [requests_per_user] [interval] [jitter] [seed]
set -euo pipefail

USERS="${1:?usage: run-baseline.sh <users> [requests_per_user] [interval] [jitter] [seed]}"
REQUESTS_PER_USER="${2:-3}"
INTERVAL="${3:-1.0}"
JITTER="${4:-0.2}"
SEED="${5:-42}"

NAMESPACE="payasam"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="${ROOT_DIR}/docs/baseline-results"
mkdir -p "${RESULTS_DIR}"

JOB_NAME="traffic-gen-${USERS}u-$(date +%s)"

echo "== Resetting database to known seeded state =="
"${ROOT_DIR}/scripts/reset.sh"

echo "== Rendering Job manifest for ${USERS} users =="
export JOB_NAME USERS REQUESTS_PER_USER INTERVAL JITTER SEED
RENDERED="$(mktemp)"
envsubst < "${ROOT_DIR}/infrastructure/kubernetes/jobs/traffic-generator-job.yaml.template" > "${RENDERED}"

echo "== Applying Job ${JOB_NAME} =="
kubectl apply -f "${RENDERED}"
rm -f "${RENDERED}"

echo "== Waiting for Job to complete =="
kubectl -n "${NAMESPACE}" wait --for=condition=complete "job/${JOB_NAME}" --timeout=180s || {
  echo "Job did not complete in time; fetching logs for diagnosis:"
  kubectl -n "${NAMESPACE}" logs "job/${JOB_NAME}" || true
  kubectl -n "${NAMESPACE}" delete job "${JOB_NAME}" --ignore-not-found=true
  exit 1
}

OUTPUT_FILE="${RESULTS_DIR}/${JOB_NAME}.json"
kubectl -n "${NAMESPACE}" logs "job/${JOB_NAME}" > "${OUTPUT_FILE}"
echo "== Results saved to ${OUTPUT_FILE} =="

kubectl -n "${NAMESPACE}" delete job "${JOB_NAME}"

cat "${OUTPUT_FILE}"
