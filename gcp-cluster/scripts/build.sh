#!/usr/bin/env bash
# Build all application service images and load them into the existing
# kind cluster (no registry needed). Does not touch the cluster's running
# state. Tag bumped to phase4 with Phase 4 controlled fault injection
# (disabled by default -- see services/*/faults.py).
set -euo pipefail

CLUSTER_NAME="payasam"
TAG="phase4"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERVICES=(database inventory-service payment-service order-service api-gateway)

for svc in "${SERVICES[@]}"; do
  echo "== Building ${svc} =="
  docker build -t "payasam/${svc}:${TAG}" "${ROOT_DIR}/services/${svc}"
done

for svc in "${SERVICES[@]}"; do
  echo "== Loading ${svc} into kind cluster ${CLUSTER_NAME} =="
  kind load docker-image "payasam/${svc}:${TAG}" --name "${CLUSTER_NAME}"
done

echo "All application images built and loaded."

echo "== Building traffic-generator (Phase 2) =="
docker build -t "payasam/traffic-generator:phase2" "${ROOT_DIR}/simulator/traffic"
echo "== Loading traffic-generator into kind cluster ${CLUSTER_NAME} =="
kind load docker-image "payasam/traffic-generator:phase2" --name "${CLUSTER_NAME}"

echo "== Building payasam-backend (Phase 5) =="
docker build -t "payasam/payasam-backend:phase5" "${ROOT_DIR}/payasam/backend"
echo "== Loading payasam-backend into kind cluster ${CLUSTER_NAME} =="
kind load docker-image "payasam/payasam-backend:phase5" --name "${CLUSTER_NAME}"

echo "== Building payasam-frontend (Phase 6) =="
docker build -t "payasam/payasam-frontend:phase6" "${ROOT_DIR}/payasam/frontend"
echo "== Loading payasam-frontend into kind cluster ${CLUSTER_NAME} =="
kind load docker-image "payasam/payasam-frontend:phase6" --name "${CLUSTER_NAME}"

echo "All images built and loaded."
