"""Payasam Business Impact API (scoped-down Phase 5).

Translates real, observed order-service failures into the business
numbers PRD Sec 18-19 describe: failed_transactions, estimated
revenue-at-risk, estimated downtime cost, business criticality, and
affected_users (distinct real user_id values among the failures --
see impact.distinct_affected_users and DECISIONS.md D019).

Deliberately scoped down from the PRD's full Intelligence layer: no
incident-correlation store, no optimization/FinOps engine, and no
self-instrumentation via the OTel pipeline the other 5 services share --
this service *reads* telemetry, it doesn't need to produce distributed
traces of its own for the current scope.

Never receives or trusts a caller-supplied failure count -- every
/impact call queries OpenObserve directly for the real number.
"""

import asyncio
import base64
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import faults_control as fc
from impact import FAILURE_EVENT_TYPES, compute_impact, distinct_affected_users, load_business_metadata

SERVICE_NAME = "payasam-backend"

# Must match services/database/db.py's SEED_PRODUCTS product_ids -- same
# constraint simulator/traffic/generator.py already documents (api-gateway
# is a thin proxy, it doesn't expose a products endpoint to discover the
# catalog from).
API_GATEWAY_URL = os.environ.get("API_GATEWAY_URL", "http://api-gateway:8000")
TRAFFIC_PRODUCTS = ["product-001", "product-002", "product-003"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(SERVICE_NAME)

BUSINESS_METADATA = load_business_metadata()

app = FastAPI(title="Payasam Business Impact API")


def _openobserve_config() -> tuple[str, str, dict]:
    """Reuses the same OTEL_EXPORTER_OTLP_ENDPOINT (from the
    telemetry-config ConfigMap, resolved per D011) and
    OPENOBSERVE_USERNAME/PASSWORD (from the openobserve-credentials
    Secret) every other service already gets via envFrom -- no new
    config plumbing."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        raise RuntimeError("OTEL_EXPORTER_OTLP_ENDPOINT not set")
    base_url, org = endpoint.rsplit("/api/", 1)

    headers = {"Content-Type": "application/json"}
    username = os.environ.get("OPENOBSERVE_USERNAME")
    password = os.environ.get("OPENOBSERVE_PASSWORD")
    if username and password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return base_url, org, headers


@app.get("/health")
def health():
    return {"status": "healthy", "service": SERVICE_NAME}


@app.get("/ready")
async def ready():
    try:
        base_url, _, headers = _openobserve_config()
    except RuntimeError:
        return JSONResponse(status_code=503, content={
            "status": "not_ready", "service": SERVICE_NAME, "reason": "openobserve_not_configured",
        })
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/healthz", headers=headers)
        if resp.status_code == 200:
            return {"status": "ready", "service": SERVICE_NAME}
    except httpx.HTTPError:
        pass
    return JSONResponse(status_code=503, content={
        "status": "not_ready", "service": SERVICE_NAME, "reason": "openobserve_unreachable",
    })


def _iso(ts_us: int | None) -> str | None:
    return datetime.fromtimestamp(ts_us / 1_000_000, tz=timezone.utc).isoformat() if ts_us else None


APP_SERVICES = ["api-gateway", "order-service", "inventory-service", "payment-service", "database"]

# Rule-based, deterministic recommended actions keyed by the same fault
# identifiers faults_control.status() reports. Deliberately transparent
# and simple -- PRD Sec 17 explicitly forbids falsely claiming advanced
# RCA when the underlying mechanism is this kind of direct lookup.
REMEDIATION_RULES = {
    "PAYASAM_FAULT_PAYMENT_LATENCY_MS": {
        "issue": "Payment Service is responding slowly (injected latency fault).",
        "recommended_action": "Inspect payment-service's dependency spans in OpenObserve Traces. If this is the demo fault, click Recover System.",
    },
    "PAYASAM_FAULT_PAYMENT_ERROR_RATE": {
        "issue": "Payment Service is rejecting requests (injected error-rate fault).",
        "recommended_action": "Check payment-service logs for the failure reason in OpenObserve Logs. If this is the demo fault, click Recover System.",
    },
    "PAYASAM_FAULT_DB_LATENCY_MS": {
        "issue": "Database queries are slow, increasing lock contention on dependent requests.",
        "recommended_action": "Inspect db.lock_wait_ms on database spans in OpenObserve Traces. If this is the demo fault, click Recover System.",
    },
    "PAYASAM_SERVICE_UNAVAILABLE": {
        "issue": "Payment Service is completely unreachable (0 replicas).",
        "recommended_action": "Check payment-service pod status. If this is the demo fault, click Recover System.",
    },
}


def _fault_status_or_502() -> dict:
    try:
        return fc.status()
    except Exception as exc:
        logger.error("kubernetes API call failed: %s", exc)
        raise HTTPException(status_code=502, detail={"error": "kubernetes_api_unavailable", "message": str(exc)})


@app.get("/faults")
def get_faults():
    return {"active_faults": _fault_status_or_502()}


@app.post("/faults/inject")
def inject_fault(scenario: str):
    if scenario not in fc.SCENARIOS:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_scenario", "valid_options": sorted(fc.SCENARIOS),
        })
    try:
        fc.inject(scenario)
    except Exception as exc:
        logger.error("fault injection failed: %s", exc)
        raise HTTPException(status_code=502, detail={"error": "kubernetes_api_unavailable", "message": str(exc)})
    logger.info("fault injected: %s", scenario)
    return {"status": "accepted", "scenario": scenario}


@app.post("/faults/recover")
def recover_faults():
    try:
        fc.recover()
    except Exception as exc:
        logger.error("recovery failed: %s", exc)
        raise HTTPException(status_code=502, detail={"error": "kubernetes_api_unavailable", "message": str(exc)})
    logger.info("recovery triggered")
    return {"status": "accepted"}


@app.get("/status")
async def system_status():
    """Consolidated technical state: per-service reachability plus
    currently active faults -- 'what's the problem with the deployment
    right now', for the Technical view."""
    health: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=3.0) as client_:
        for svc in APP_SERVICES:
            try:
                resp = await client_.get(f"http://{svc}:8000/health")
                health[svc] = "healthy" if resp.status_code == 200 else "unhealthy"
            except httpx.HTTPError:
                health[svc] = "unreachable"

    try:
        active_faults = fc.status()
        faults_readable = True
    except Exception as exc:
        logger.error("kubernetes API call failed: %s", exc)
        active_faults = None
        faults_readable = False

    all_services_healthy = all(v == "healthy" for v in health.values())
    if not faults_readable:
        overall = "unknown"
    elif all_services_healthy and not active_faults:
        overall = "healthy"
    else:
        overall = "degraded"

    return {"overall_status": overall, "services": health, "active_faults": active_faults}


@app.get("/remediation")
def remediation():
    """Deterministic, rule-based recommended actions for whatever faults
    are currently active -- 'steps for how to fix it', for the
    Remediation view. Not AI, not claimed to be (PRD Sec 17)."""
    active = _fault_status_or_502()
    if not active:
        return {"status": "healthy", "issues": [], "recommended_actions": ["No action needed -- all services healthy."]}

    issues = []
    actions = []
    for var in active:
        rule = REMEDIATION_RULES.get(var)
        if rule:
            issues.append(rule["issue"])
            actions.append(rule["recommended_action"])
    return {"status": "degraded", "issues": issues, "recommended_actions": actions}


@app.get("/impact")
async def impact(window_minutes: int = 5, business_function: str = "order"):
    if window_minutes <= 0:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_window", "message": "window_minutes must be positive",
        })
    if business_function not in BUSINESS_METADATA:
        raise HTTPException(status_code=400, detail={
            "error": "unknown_business_function",
            "message": f"'{business_function}' not in business_metadata.yaml",
            "valid_options": sorted(BUSINESS_METADATA.keys()),
        })

    try:
        base_url, org, headers = _openobserve_config()
    except RuntimeError:
        raise HTTPException(status_code=503, detail={"error": "openobserve_not_configured"})

    now_us = int(time.time() * 1_000_000)
    start_us = now_us - window_minutes * 60 * 1_000_000

    event_types_sql = ",".join(f"'{e}'" for e in sorted(FAILURE_EVENT_TYPES))
    where_clause = (
        "WHERE service = 'payasam-order-service' "
        f"AND event_type IN ({event_types_sql}) "
        "ORDER BY _timestamp ASC LIMIT 1000"
    )
    body = {"query": {
        "sql": f'SELECT _timestamp, event_type, user_id FROM "default" {where_clause}',
        "start_time": start_us, "end_time": now_us,
    }}

    schema_missing_user_id = False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{base_url}/api/{org}/_search?type=logs", json=body, headers=headers)
        if resp.status_code == 400:
            # A brand-new OpenObserve instance that has never ingested a
            # log with `user_id` on it doesn't have that column in its
            # dynamic schema yet, and querying an unknown column 400s --
            # confirmed experimentally against a real freshly-initialized
            # field, not assumed. Falls back to the same query without
            # user_id rather than surfacing a raw 502 to the UI; the only
            # user-visible effect is affected_users being honestly
            # unavailable via schema_missing_user_id below.
            schema_missing_user_id = True
            body["query"]["sql"] = f'SELECT _timestamp, event_type FROM "default" {where_clause}'
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base_url}/api/{org}/_search?type=logs", json=body, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("openobserve query failed: %s", exc)
        raise HTTPException(status_code=502, detail={"error": "openobserve_unavailable", "message": str(exc)})

    hits = resp.json().get("hits", [])
    failed_transactions = len(hits)

    breakdown: dict[str, int] = {}
    for h in hits:
        et = h.get("event_type", "unknown")
        breakdown[et] = breakdown.get(et, 0) + 1

    first_ts = hits[0]["_timestamp"] if hits else None
    last_ts = hits[-1]["_timestamp"] if hits else None

    assumptions = BUSINESS_METADATA[business_function]
    calc = compute_impact(failed_transactions, first_ts, last_ts, assumptions)
    if schema_missing_user_id:
        affected_users, affected_users_note = None, (
            "not computed: this OpenObserve instance has not yet ingested any log with a user_id field "
            "(will resolve automatically once at least one order has been processed)"
        )
    else:
        affected_users, affected_users_note = distinct_affected_users(hits, failed_transactions)

    return {
        "label": "ESTIMATED / SIMULATED BUSINESS IMPACT",
        "window_minutes": window_minutes,
        "business_function": assumptions["business_function"],
        "business_criticality": assumptions["criticality"],
        "failed_transactions": failed_transactions,
        "failure_breakdown": breakdown,
        "first_failure_at": _iso(first_ts),
        "last_failure_at": _iso(last_ts),
        "assumptions": {
            "avg_transaction_value_inr": assumptions["avg_transaction_value_inr"],
            "downtime_cost_per_minute_inr": assumptions["downtime_cost_per_minute_inr"],
        },
        "affected_users": affected_users,
        "affected_users_note": affected_users_note,
        **calc,
    }


@app.post("/traffic/generate")
async def generate_traffic(count: int = 5):
    """Sends `count` real POST /orders requests to api-gateway, one at a
    time with a short pause between each -- so the UI's Failure Simulator
    can be fully self-contained (inject a fault, then make it actually
    manifest) without a separate terminal running
    scripts/run-baseline.sh. Not a load-testing tool: same small-scale,
    one-request-at-a-time model simulator/traffic/generator.py already
    uses for exactly this reason (PRD Sec 4 -- demonstrate the causal
    chain, not raw throughput).
    """
    if count <= 0 or count > 50:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_count", "message": "count must be between 1 and 50",
        })

    sent = 0
    success = 0
    failed = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for _ in range(count):
            product = random.choice(TRAFFIC_PRODUCTS)
            request_id = f"req-uidemo-{uuid.uuid4().hex[:8]}"
            try:
                resp = await client.post(
                    f"{API_GATEWAY_URL}/orders",
                    json={"user_id": f"user-uidemo-{uuid.uuid4().hex[:8]}", "items": [{"product_id": product, "quantity": 1}]},
                    headers={"X-Request-Id": request_id},
                )
                sent += 1
                if resp.status_code == 201:
                    success += 1
                else:
                    failed += 1
            except httpx.HTTPError as exc:
                logger.warning("traffic request failed to reach api-gateway: %s", exc)
                sent += 1
                failed += 1
            await asyncio.sleep(0.3)

    return {"sent": sent, "success": success, "failed": failed}
