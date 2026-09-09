#!/usr/bin/env bash
# Build, load, deploy, and wait for the application to become
# ready in the existing kind cluster.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"${ROOT_DIR}/scripts/build.sh"

echo "== Applying Kubernetes manifests =="
kubectl apply -k "${ROOT_DIR}/infrastructure/kubernetes"

echo "== Waiting for deployments to become available =="
for dep in database inventory-service payment-service order-service api-gateway payasam-backend payasam-frontend; do
  kubectl -n payasam rollout status "deployment/${dep}" --timeout=120s
done

echo "== Pods =="
kubectl -n payasam get pods -o wide

echo "application is up."
