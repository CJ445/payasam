#!/usr/bin/env bash
# Creates the Payasam demo alerts in OpenObserve: one general "any service
# logged an ERROR" watchdog and one specific "payment-service is degraded"
# alert, both evaluated against real telemetry already flowing into the
# `default` stream (Phase 3). Directly satisfies the hackathon problem
# statement's "Set alert thresholds" / "Error alerts" requirement, which
# nothing else in this repo previously touched.
#
# Idempotent: safe to re-run. Uses the v0.92.2 alerts API, discovered from
# this instance's own /api-doc/openapi.json (no public curl example existed
# in OpenObserve's docs at the time this was written).
#
# Field names below (`severity`, `service`) were verified against a real
# ERROR-level log line produced by a live payment-service fault injection
# in this session -- not guessed from documentation.
#
# Like scripts/otel-query.sh, this runs from *inside* a pod that already
# has OpenObserve credentials injected via the openobserve-credentials
# Secret, so the real password is never typed on the host or written to
# any file.
set -euo pipefail

NAMESPACE="payasam"

kubectl -n "${NAMESPACE}" exec deployment/database -- python3 -c "
import os, base64, json, urllib.request, urllib.error

user = os.environ['OPENOBSERVE_USERNAME']
pw = os.environ['OPENOBSERVE_PASSWORD']
token = base64.b64encode(f'{user}:{pw}'.encode()).decode()
headers = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}

otlp_endpoint = os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']
base_url, org = otlp_endpoint.rsplit('/api/', 1)

def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{base_url}{path}', data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

# --- 1. Destination: a public echo endpoint (httpbin.org/post).
# OpenObserve's own SSRF guard rejects any destination URL that resolves
# to a private/loopback IP (confirmed experimentally: it refused
# OpenObserve's own host gateway address with 'Destination URL blocked
# by SSRF guard'), so a purely-local no-op sink isn't possible for an
# http-type destination -- only http/email/sns destination types exist,
# and all require a real, publicly reachable target.
#
# This is the ONLY part of the alerting setup that touches the network,
# and it is not required for the demo to work: the alert's own
# evaluation (the SQL query against real telemetry) and its
# Triggered/History state in the OpenObserve UI are 100% local,
# per PRD Sec.47. If the delivery attempt fails (e.g. offline demo
# environment), that failure is confined to OpenObserve's own delivery
# log and never affects what the alert itself shows. Reuses the
# built-in 'prebuilt_webhook' template so no custom template needs to
# be created.
DEST_NAME = 'payasam-local-sink'
status, _ = request('GET', f'/api/{org}/alerts/destinations/{DEST_NAME}')
if status == 200:
    print(f'destination {DEST_NAME}: already exists, skipping')
else:
    status, body = request('POST', f'/api/{org}/alerts/destinations', {
        'name': DEST_NAME,
        'url': 'https://httpbin.org/post',
        'method': 'post',
        'type': 'http',
        'template': 'prebuilt_webhook',
        'skip_tls_verify': False,
    })
    print(f'destination {DEST_NAME}: create -> {status}')
    if status not in (200, 201):
        raise SystemExit(f'failed to create destination: {body}')

# --- 2. Alerts ---
ALERTS = [
    {
        'name': 'payasam-any-service-error',
        'description': 'Fires when any Payasam service logs an ERROR-severity '
                        'event in the last 5 minutes -- the generic error-rate '
                        'watchdog the hackathon problem statement calls for. '
                        'Evaluated against real OTLP-exported logs, not a '
                        'canned trigger.',
        'sql': 'SELECT count(*) as count FROM \"default\" WHERE severity = \'ERROR\'',
    },
    {
        'name': 'payasam-payment-degraded',
        'description': 'Fires specifically when payment-service logs an ERROR '
                        '-- a targeted alert distinct from the general '
                        'watchdog, tied to the one business-critical '
                        'dependency the Phase 4 fault scenarios exercise.',
        'sql': 'SELECT count(*) as count FROM \"default\" WHERE service = \'payasam-payment-service\' AND severity = \'ERROR\'',
    },
]

for alert in ALERTS:
    status, body = request('GET', f'/api/v2/{org}/alerts?alert_name_substring={alert[\"name\"]}')
    existing = (body or {}).get('list', []) if isinstance(body, dict) else []
    if any(a.get('name') == alert['name'] for a in existing):
        print(f'alert {alert[\"name\"]}: already exists, skipping')
        continue

    payload = {
        'name': alert['name'],
        'description': alert['description'],
        'stream_type': 'logs',
        'stream_name': 'default',
        'is_real_time': False,
        'query_condition': {'type': 'sql', 'sql': alert['sql']},
        'trigger_condition': {
            'period': 5,           # look back 5 minutes
            'operator': '>=',
            'threshold': 1,        # any ERROR at all counts
            'frequency': 1,        # check every 1 minute -- fast enough to see fire live
            'frequency_type': 'minutes',
            'silence': 1,          # low silence so it can re-fire across repeated demo runs
        },
        'destinations': [DEST_NAME],
        'enabled': True,
    }
    status, body = request('POST', f'/api/v2/{org}/alerts', payload)
    print(f'alert {alert[\"name\"]}: create -> {status}')
    if status not in (200, 201):
        raise SystemExit(f'failed to create alert: {body}')

print('Done. View under OpenObserve -> Alerts (left sidebar).')
"
