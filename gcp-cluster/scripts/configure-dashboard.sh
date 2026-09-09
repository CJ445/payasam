#!/usr/bin/env bash
# Creates a native OpenObserve dashboard with 3 real metric panels --
# satisfies the hackathon problem statement's literal "visualize logs in
# dashboard" requirement using OpenObserve's own dashboarding feature
# (D002: don't rebuild what OpenObserve already provides), rather than
# custom chart code inside Payasam.
#
# Idempotent: if a dashboard with this title already exists, does
# nothing. Uses the v0.92.2 Dashboards API, discovered from this
# instance's own /api-doc/openapi.json (same technique as
# configure-alerts.sh) plus a real example panel structure from
# https://github.com/openobserve/dashboards, since OpenObserve's own
# docs don't show a complete panel JSON example.
#
# Panel SQL queries deliberately mirror definitions already used
# elsewhere in this project (payasam-backend's /impact FAILURE_EVENT_TYPES,
# the payasam-any-service-error alert's severity='ERROR' condition) --
# not new, unverified metrics.
#
# Runs from inside a pod that already has OpenObserve credentials
# injected, so the real password is never typed on the host.
set -euo pipefail

NAMESPACE="payasam"
DASHBOARD_TITLE="Payasam - Live Error Overview"

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

title = '${DASHBOARD_TITLE}'

status, body = request('GET', f'/api/{org}/dashboards')
existing = (body or {}).get('dashboards', []) if isinstance(body, dict) else []
if any(d.get('title') == title for d in existing):
    print(f'dashboard {title!r}: already exists, skipping')
    raise SystemExit(0)

def metric_panel(panel_id, title, description, sql, x):
    # Structure verified against a real, working example dashboard
    # (github.com/openobserve/dashboards/Github/Github.dashboard.json) --
    # OpenObserve's own docs don't show a complete panel JSON, and the
    # first attempt at this script (missing 'x'/'filter'/'customQuery')
    # was rejected by a real 422 from this exact API before this was
    # corrected against that real example.
    return {
        'id': panel_id,
        'type': 'metric',
        'title': title,
        'description': description,
        'config': {'show_legends': True, 'decimals': 0},
        'queryType': 'sql',
        'queries': [{
            'query': sql,
            'customQuery': False,
            'fields': {
                'stream': 'default',
                'stream_type': 'logs',
                'x': [],
                'y': [{'label': title, 'alias': 'y_axis_1', 'column': 'y_axis_1', 'aggregationFunction': 'count'}],
                'z': [],
                'breakdown': [],
                'filter': {'filterType': 'group', 'logicalOperator': 'AND', 'conditions': []},
            },
            'config': {'promql_legend': ''},
        }],
        'layout': {'x': x, 'y': 0, 'w': 4, 'h': 6, 'i': (x // 4) + 1},
    }

# Same FAILURE_EVENT_TYPES definition payasam-backend's /impact endpoint
# uses -- see payasam/backend/impact.py.
order_failure_sql = (
    'SELECT count(*) as \"y_axis_1\" FROM \"default\" '
    \"WHERE service = 'payasam-order-service' \"
    \"AND event_type IN ('inventory_unavailable','inventory_error','payment_unavailable','payment_declined','order_persist_failed')\"
)
any_error_sql = 'SELECT count(*) as \"y_axis_1\" FROM \"default\" WHERE severity = \'ERROR\''
services_affected_sql = 'SELECT count(distinct service) as \"y_axis_1\" FROM \"default\" WHERE severity = \'ERROR\''

dashboard = {
    'title': title,
    'description': 'Real-time error counts backing Payasam business-impact numbers -- not a replacement for OpenObserve Logs/Traces, a quick-glance summary using the same definitions payasam-backend and the payasam-* alerts already use.',
    'tabs': [{
        'tabId': 'default',
        'name': 'Overview',
        'panels': [
            metric_panel('panel_order_failures', 'Order failures (selected window)', 'Same definition as payasam-backend /impact\'s failed_transactions.', order_failure_sql, 0),
            metric_panel('panel_any_error', 'Error logs, all services (selected window)', 'Same condition as the payasam-any-service-error alert.', any_error_sql, 4),
            metric_panel('panel_services_affected', 'Services currently logging errors', 'Distinct services with at least one ERROR-severity log in the selected window.', services_affected_sql, 8),
        ],
    }],
}

status, body = request('POST', f'/api/{org}/dashboards', dashboard)
print(f'dashboard {title!r}: create -> {status}')
if status not in (200, 201):
    raise SystemExit(f'failed to create dashboard: {body}')

print('Done. View under OpenObserve -> Dashboards (left sidebar).')
"
