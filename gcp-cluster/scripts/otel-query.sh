#!/usr/bin/env bash
# Query OpenObserve from inside a pod that already has the credentials
# injected as env vars (the openobserve-credentials Secret) -- so this
# script, and whoever runs it, never needs to type the real password.
#
# Usage: scripts/otel-query.sh <stream_type: traces|logs|metrics> <sql> [window_seconds]
set -euo pipefail

NAMESPACE="payasam"
STREAM_TYPE="${1:?usage: otel-query.sh <traces|logs|metrics> <sql> [window_seconds]}"
SQL="${2:?usage: otel-query.sh <traces|logs|metrics> <sql> [window_seconds]}"
WINDOW_SECONDS="${3:-600}"

kubectl -n "${NAMESPACE}" exec deployment/database -- python3 -c "
import os, base64, json, time, urllib.request

user = os.environ['OPENOBSERVE_USERNAME']
pw = os.environ['OPENOBSERVE_PASSWORD']
token = base64.b64encode(f'{user}:{pw}'.encode()).decode()
headers = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}

# Reuse the already-resolved endpoint this pod was actually configured
# with (OTEL_EXPORTER_OTLP_ENDPOINT, e.g. http://<gateway-ip>:5080/api/default)
# rather than hardcoding the gateway IP here -- per D011, that address is
# dynamically resolved and not guaranteed stable across cluster recreation.
otlp_endpoint = os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']
base_url = otlp_endpoint.rsplit('/api/', 1)[0]
org = otlp_endpoint.rsplit('/api/', 1)[1]

now_us = int(time.time() * 1_000_000)
start_us = now_us - (${WINDOW_SECONDS} * 1_000_000)

body = json.dumps({'query': {'sql': '''${SQL}''', 'start_time': start_us, 'end_time': now_us}}).encode()
req = urllib.request.Request(f'{base_url}/api/{org}/_search?type=${STREAM_TYPE}', data=body, headers=headers, method='POST')
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
        print(json.dumps(data, indent=2))
except urllib.error.HTTPError as e:
    print('HTTP ERROR', e.code, e.read().decode()[:1500])
"
