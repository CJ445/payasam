#!/usr/bin/env bash
# Reset the Phase 1 application's data to its seeded state (products back
# to their starting quantities, orders/payments cleared) via the
# database service's own /admin/reset endpoint. Does not touch the
# cluster, deployments, or OpenObserve -- data only.
set -euo pipefail

NAMESPACE="payasam"

echo "== Resetting database state via /admin/reset =="
kubectl -n "${NAMESPACE}" exec deployment/database -- python3 -c "
import urllib.request
req = urllib.request.Request('http://localhost:8000/admin/reset', method='POST')
with urllib.request.urlopen(req, timeout=5) as resp:
    print(resp.status, resp.read().decode())
"
