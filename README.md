# Payasam

**Cloud Operations Intelligence Platform** — a locally hosted, GCP-like production environment that generates real application traffic, simulates real failures, and uses the resulting telemetry to derive incidents, root cause, and business impact.

Payasam does not fake incidents. When a failure scenario is triggered, it modifies the actual behavior of running services — real requests slow down or fail, real logs/metrics/traces change, and Payasam's intelligence layer derives the incident from that real telemetry.

```text
Real running application
        ↓
Real simulated traffic
        ↓
Failure / abnormal behavior
        ↓
Real telemetry (OpenTelemetry → OpenObserve)
        ↓
Payasam Intelligence Layer
        ↓
Business impact + optimization
        ↓
Payasam UI
```

## Architecture

- **Microservices** (`gcp-cluster/services`) — `api-gateway`, `order-service`, `payment-service`, `inventory-service`, `database`: a small e-commerce-style stack running in Kubernetes (kind).
- **Traffic simulator** (`gcp-cluster/simulator`) — generates a configurable number of synthetic users making real requests against the stack.
- **Telemetry** — services are instrumented with OpenTelemetry and export to [OpenObserve](https://openobserve.ai/), which runs independently in Docker.
- **Payasam backend/frontend** (`gcp-cluster/payasam`) — reads observability data from OpenObserve, detects incidents, computes business impact, and serves the UI.
- **Fault injection** (`gcp-cluster/scripts/set-fault.sh`, `clear-faults.sh`) — toggles real degraded behavior in the running services (e.g. database saturation, service latency, cascading failures).

## Prerequisites

- Docker
- kind + kubectl
- OpenObserve running locally in Docker on port `5080` (see `SETUP.md`)

## Getting started

```bash
# Full environment bring-up: builds and deploys all services + Payasam
gcp-cluster/scripts/start.sh

# Quick daily start: ensures OpenObserve is up and opens the Payasam UI
./start.sh

# Tear down
./stop.sh
```

See `SETUP.md` for detailed environment setup and `DEMO.md` for a walkthrough of the failure scenarios and expected demo flow.

## Documentation

| File | Purpose |
|---|---|
| `PRD.md` | Product requirements and implementation specification |
| `IMPLEMENTATION_PLAN.md` | Phased build plan |
| `DECISIONS.md` | Log of implementation decisions and rationale |
| `SETUP.md` | Environment and dependency setup |
| `DEMO.md` | Demo script and failure scenarios |
| `FAQ.md` | Frequently asked questions |
| `GCP_MAPPING.md` | How the local environment maps to GCP concepts |
| `gcp-cluster/docs/` | Phase-by-phase implementation notes and baseline results |

## Scope

This is a controlled hackathon simulation — no GCP credits, cloud accounts, or live cloud infrastructure are required. Traffic volume is intentionally small (on the order of 10–50 simulated users) so that induced failures are quickly and clearly observable, not to demonstrate production-scale throughput.
