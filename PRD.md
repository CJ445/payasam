# Payasam — Cloud Operations Intelligence Platform

## Product Requirements & Implementation Specification

**Project root:** `~/claude-workspace/payasam`
**Primary implementation directory:** `~/claude-workspace/payasam/gcp-cluster`
**Existing external dependency:** OpenObserve running in Docker on host port `5080`
**Development environment:** Ubuntu Linux
**Primary coding agent:** Claude Code CLI
**Deployment target:** Local machine only
**Cloud requirement:** No GCP credits, accounts, APIs, or live GCP infrastructure are required for the working product.

---

# 1. Development Instruction to Claude Code

You are the primary implementation agent for this project.

## 1.1 Operating mode

Work as a **single primary agent by default**.

Do **not** use multi-agent/subagent workflows unless there is a concrete technical reason that cannot reasonably be handled by the primary agent. Parallel agents are not the default.

Do not create unnecessary agents merely to review, research, or implement independent files.

The project should remain understandable and maintainable by one developer.

## 1.2 Do not blindly implement

Before modifying code:

1. Inspect the current repository.
2. Inspect the existing Docker/OpenObserve environment.
3. Identify what already exists.
4. Determine the smallest implementation satisfying the current phase.
5. State the implementation plan internally.
6. Implement only the current phase.
7. Run the phase's tests and verification commands.
8. Do not proceed to the next phase if the current quality gate fails.

Do not repeatedly ask the user for decisions that are already defined by this PRD.

If an implementation detail is genuinely unspecified, choose the simplest reasonable option and document the decision.

## 1.3 No unnecessary complexity

Do NOT introduce:

* GCP infrastructure
* cloud credentials
* cloud billing
* BigQuery
* Kafka
* Redis
* PostgreSQL unless later proven necessary
* multiple LLMs
* unnecessary microservices
* unnecessary frontend frameworks
* unnecessary message brokers
* unnecessary orchestration layers
* redundant observability systems
* Grafana as another visualization platform

Do not rebuild capabilities already provided by OpenObserve unless required for Payasam-specific functionality.

Prefer simple local components.

---

# 2. Product Definition

## 2.1 Product name

**Payasam**

## 2.2 Product category

**Cloud Operations Intelligence Platform**

## 2.3 Core concept

Payasam runs on top of a locally hosted **GCP-like production environment**.

The local environment contains real running application workloads and infrastructure components.

The system generates **synthetic but real application traffic**.

Failure scenarios modify the behavior of those running workloads rather than injecting fake incident records.

The resulting:

* logs
* metrics
* traces
* request failures
* latency changes
* resource behavior

are collected by OpenTelemetry/OpenObserve.

Payasam then interprets the resulting observability information and produces:

* incidents
* root-cause evidence
* business impact
* affected users
* failed transactions
* estimated revenue impact
* resource optimization recommendations

The central pipeline is:

```text
Real running application
        ↓
Real simulated traffic
        ↓
Failure / abnormal behavior
        ↓
Real telemetry generated
        ↓
OpenTelemetry
        ↓
OpenObserve
        ↓
Observability / incident evidence
        ↓
Payasam Intelligence Layer
        ↓
Business impact + optimization
        ↓
Payasam UI
```

---

# 3. Critical Product Principle

The demo must NOT rely on fake dashboard values.

For example, this is prohibited:

```text
User clicks "Database Saturation"
        ↓
Application inserts:
"database_error=true"
        ↓
UI displays fake incident
```

Instead:

```text
User clicks "Database Saturation"
        ↓
Failure controller modifies the actual running database/service behavior
        ↓
Real application requests experience degradation
        ↓
Actual requests become slower/fail
        ↓
Actual logs/metrics/traces change
        ↓
OpenObserve observes those changes
        ↓
Payasam derives the incident and impact
```

Synthetic users and synthetic transactions are acceptable.

**Synthetic data must represent actual behavior produced by running software, not prerecorded or manually injected incident results.**

---

# 4. Scale Philosophy

This is a controlled hackathon simulation.

Do NOT attempt production-scale traffic.

Use a small number of simulated users so that failures become visible quickly.

Initial target:

* 10 simulated users
* configurable up to approximately 50 users
* low request rate
* short demonstration windows
* deterministic behavior
* repeatable failures

The purpose is not to prove millions of requests per second.

The purpose is to demonstrate:

> real failure → real telemetry → real detection → real Payasam analysis.

The system must allow traffic volume to be configured.

Example:

```yaml
traffic:
  simulated_users: 10
  requests_per_second: 2
```

The exact configuration may be adjusted during implementation based on observed laptop performance.

---

# 5. Existing Environment

The project currently exists at:

```text
~/claude-workspace/payasam
```

The current project contains:

```text
~/claude-workspace/payasam/
└── gcp-cluster/
```

OpenObserve is already running independently as a Docker container:


```bash
docker run -d \
  --name openobserve \
  -p 5080:5080 \
  -e ZO_ROOT_USER_EMAIL=admin@example.com \
  -e ZO_ROOT_USER_PASSWORD=<your-local-password> \
  openobserve/openobserve:latest
```

The actual password must be supplied through a local environment variable
or `.env` file and must never be committed to source control.

OpenObserve is therefore expected to be reachable locally at:

```text
http://localhost:5080
```

Do NOT recreate or replace the existing OpenObserve container unless necessary.

Do NOT hardcode credentials into source code.

Use environment variables/configuration for OpenObserve connection details.

The implementation must first verify the existing OpenObserve container and connectivity.

---

# 6. Repository Responsibility

All Payasam implementation work should live under:

```text
~/claude-workspace/payasam/gcp-cluster
```

Claude Code must inspect the directory before creating files.

Do not blindly assume the directory is empty.

The final structure should be logical and may evolve during implementation, but should broadly separate:

```text
gcp-cluster/
├── services/
├── infrastructure/
├── simulator/
├── telemetry/
├── payasam/
├── tests/
├── docs/
└── scripts/
```

Do not create directories simply to match this example if the implementation does not require them.

---

# 7. System Architecture

The system consists of four major layers.

## Layer 1 — Local GCP-like Production Environment

This is the actual running environment.

It should contain real workloads representing cloud/application services.

Initial application topology:

```text
                    API Gateway
                    /    |    \
                   /     |     \
                Auth    Order    User
                         |
                    ┌────┴────┐
                    │         │
                 Payment   Inventory
                    |
                    ▼
                 Database
                    |
                    ▼
              Notification
```

The exact topology can be simplified if required, but it must demonstrate service dependencies and cascading failures.

The workloads should run locally using Kubernetes.

---

# 8. Kubernetes Environment

Use a local Kubernetes cluster.

Preferred local Kubernetes technology should be determined from what is already installed on the machine.

Before choosing a cluster implementation, inspect:

```bash
kubectl version --client
docker version
kind version
minikube version
```

Use an already available local Kubernetes solution if possible.

Do not install a heavyweight cluster unnecessarily.

The cluster should contain:

* application services
* database
* telemetry components where required
* failure-control components where appropriate

The cluster exists to provide a real running environment, not merely a visual diagram.

---

# 9. GCP-like Service Mapping

The local environment should conceptually represent common GCP concepts without attempting to recreate the entire GCP platform.

Required conceptual mappings:

| GCP concept      | Local implementation                                                                      |
| ---------------- | ----------------------------------------------------------------------------------------- |
| Cloud Run        | Kubernetes application workloads                                                          |
| Cloud Logging    | OpenTelemetry/OpenObserve logging pipeline                                                |
| Cloud Monitoring | OpenObserve metrics/monitoring                                                            |
| Pub/Sub          | Lightweight local event mechanism only if application functionality genuinely requires it |
| Cloud Storage    | Only if required by an implemented feature                                                |
| Cloud Functions  | Only if required by an implemented feature                                                |

Do not build fake implementations of every GCP product.

The objective is a **GCP-like production environment**, not a GCP emulator.

Documentation should explain which local components represent which GCP concepts.

---

# 10. Demo Application

The demo application is a small production-like transactional application.

It must generate real traffic and real telemetry.

Recommended services:

```text
api-gateway
auth-service
user-service
order-service
payment-service
inventory-service
notification-service
database
```

Keep services lightweight.

Each service must expose enough behavior to participate in the request flow.

The application should support a simple transaction such as:

```text
User
 ↓
API Gateway
 ↓
Authentication
 ↓
Order
 ├── Inventory
 └── Payment
       ↓
     Database
 ↓
Notification
```

The implementation does not need to resemble a real commercial application in complexity.

It needs to provide realistic dependencies for observability demonstrations.

---

# 11. Synthetic User Traffic

Implement a traffic generator.

The traffic generator must create actual HTTP requests against the running application.

Each request should have identifiable synthetic context.

At minimum:

```text
user_id
request_id
transaction_id
timestamp
endpoint
service
```

Where appropriate, propagate:

```text
trace_id
span_id
```

The generator should support:

```text
start normal traffic
stop traffic
change traffic volume
```

The initial default should be intentionally small.

Example:

```text
10 simulated users
2 requests/sec
```

The values must be configurable.

---

# 12. OpenTelemetry

Instrument the application with OpenTelemetry.

At minimum collect:

## Logs

```text
timestamp
service
severity
message
request_id
user_id
transaction_id
endpoint
status_code
deployment_version
```

## Metrics

```text
request_count
error_count
error_rate
request_latency
CPU usage where available
memory usage where available
```

Prefer standard OpenTelemetry instrumentation where practical.

## Traces

Create traces across service boundaries where practical.

A request should ideally be traceable:

```text
API Gateway
    ↓
Order
    ↓
Payment
    ↓
Database
```

The implementation must prioritize working telemetry over excessive instrumentation complexity.

---

# 13. OpenObserve Integration

OpenObserve is the observability substrate.

Do not rebuild:

* log storage
* log search
* generic dashboards
* generic alerting
* generic observability functionality

Use OpenObserve for:

* telemetry ingestion
* log visualization
* metrics visualization
* traces where configured
* alerts
* observability investigation

Payasam consumes the resulting observability information.

The implementation must verify actual telemetry reaches OpenObserve.

A successful health check alone is NOT sufficient.

At least one automated integration test must prove:

```text
application request
        ↓
telemetry generated
        ↓
OpenObserve receives telemetry
```

---

# 14. Failure Injection System

Implement a dedicated failure controller.

The controller must modify actual running application behavior.

It must NOT directly create fake incidents.

Initial failure scenarios:

## Scenario 1 — Database Saturation

This is the primary demo scenario.

Normal:

```text
database latency ≈ low
database connections available
payment requests succeed
```

Injected:

```text
database becomes constrained
queries slow down
connection contention increases
dependent requests become slow/fail
```

The exact implementation can use controlled:

* connection limits
* query delays
* CPU contention
* artificial database latency

Choose the safest and most deterministic method.

Expected propagation:

```text
Database degradation
        ↓
Payment latency/errors
        ↓
Order failures
        ↓
API Gateway errors
```

The actual application must generate the resulting telemetry.

---

## Scenario 2 — Service CPU Saturation

Cause an actual running service to consume controlled CPU.

Expected:

```text
CPU ↑
latency ↑
error rate potentially ↑
```

---

## Scenario 3 — Traffic Spike

Increase actual request generation.

Expected:

```text
request rate ↑
resource utilization ↑
latency potentially ↑
```

---

## Scenario 4 — Deployment Regression

Introduce a controlled application version/configuration that produces elevated errors.

The failure should be reversible.

---

## Scenario 5 — Recovery

Every failure scenario must support recovery.

Example:

```text
Inject failure
    ↓
Observe degradation
    ↓
Recover
    ↓
Metrics return toward baseline
    ↓
Incident closes/resolves
```

Recovery is required because the final demo should demonstrate both failure and restoration.

---

# 15. Failure Injection UI

The Payasam UI must be able to trigger the failure scenarios.

Example:

```text
FAILURE SIMULATOR

System status: HEALTHY

[ Database Saturation ]
[ CPU Saturation ]
[ Traffic Spike ]
[ Deployment Regression ]
[ Recover System ]
```

Clicking a scenario must invoke the actual failure controller.

The UI must show the action being executed.

Example:

```text
Injecting Database Saturation...

Target:
database

Status:
ACTIVE

Expected propagation:
Database → Payment → Order → API Gateway
```

The UI should then transition to showing actual telemetry changes.

---

# 16. Payasam Intelligence Layer

This is the core product differentiator.

Payasam must sit above OpenObserve.

Conceptually:

```text
OpenObserve
    ↓
observability evidence
    ↓
Payasam Intelligence
    ├── Incident correlation
    ├── Business impact
    └── Resource optimization
```

Payasam must not duplicate OpenObserve's entire observability platform.

---

# 17. Incident Intelligence

Payasam should consume observable incident information and correlate it with application/service context.

For an incident, Payasam should determine:

```text
incident_id
service
severity
start_time
duration
affected_services
root_cause_evidence
confidence where available
```

Where OpenObserve provides root-cause evidence, Payasam should preserve the evidence rather than inventing a separate unsupported root cause.

If a sophisticated native RCA capability is unavailable, implement a transparent, limited correlation mechanism based on measurable telemetry.

Do not falsely claim advanced RCA.

---

# 18. Business Impact Engine

Payasam must translate technical degradation into business impact.

Create a configurable business metadata model.

Example:

```yaml
payment:
  business_function: Payments
  criticality: 5
  avg_transaction_value_inr: 500
  transactions_per_minute: 10
  downtime_cost_per_minute_inr: 1000

order:
  business_function: Ordering
  criticality: 5
  avg_transaction_value_inr: 500
  transactions_per_minute: 8
  downtime_cost_per_minute_inr: 800
```

Values are explicitly simulation assumptions.

They are not real accounting data.

---

# 19. Business Impact Calculation

The calculation must be deterministic and explainable.

Potential outputs:

```text
affected_users
failed_transactions
estimated_revenue_at_risk
estimated_downtime_cost
business_criticality
```

For example:

```text
failed_transactions = actual observed failed transactions

revenue_at_risk =
failed_transactions × avg_transaction_value
```

Downtime cost:

```text
downtime_cost =
incident_duration_minutes × downtime_cost_per_minute
```

Do not fabricate precise financial claims.

The UI must clearly label these values as:

**Estimated / simulated business impact**

and expose the assumptions used.

---

# 20. Resource Optimization / FinOps Engine

Payasam must identify inefficient resource behavior using actual observed local resource metrics.

Examples:

```text
low utilization
high utilization
resource saturation
traffic/resource mismatch
```

Example recommendation:

```text
Notification Service

Observed CPU utilization: 8%

Observed traffic: low

Recommendation:
Reduce provisioned capacity.

Reason:
Resource utilization remains consistently low.

Estimated savings:
₹X/month

Basis:
Simulation configuration
```

The savings calculation must be transparent.

Do not pretend that local resource consumption is an actual GCP bill.

Label it as:

**Estimated simulated savings**

---

# 21. Payasam User Experience

Payasam must support different audiences.

## Entry screen

Users should be able to choose:

```text
Technical Operations
```

or

```text
Business / Management
```

---

# 22. Technical Operations View

Primary question:

> What broke, where, and why?

Show:

```text
System Health
Active Incidents
Service Health
Error Rate
Latency
CPU
Memory
Request Rate
Incident Timeline
Service Dependencies
Root Cause Evidence
Logs / Metrics / Traces
```

This view should provide links or navigation into OpenObserve for detailed observability investigation.

---

# 23. Business / Management View

Primary question:

> What does the current system state mean for the business?

Show:

```text
System Health

Active Incidents

Users Affected

Failed Transactions

Revenue at Risk

Downtime Cost

Critical Services

Optimization Opportunities

Estimated Savings
```

Avoid unnecessary technical terminology.

For example, instead of:

```text
Payment p95 latency = 913ms
```

show:

```text
Payment processing is degraded
```

with technical details available as secondary information.

---

# 24. Incident Investigation View

Accessible from either audience.

Show:

```text
Incident
    ↓
Timeline
    ↓
Affected services
    ↓
Root cause evidence
    ↓
Telemetry evidence
    ↓
Affected users
    ↓
Failed transactions
    ↓
Business impact
    ↓
Optimization/recommended action
```

Every business number must be traceable back to the underlying data/assumption.

---

# 25. Main Demonstration Scenario

The primary demo must be:

## Database Saturation

### Initial state

```text
System Health: HEALTHY

10 simulated users
normal traffic

Database:
healthy

Payment:
healthy

Order:
healthy

API Gateway:
healthy
```

### Action

User clicks:

```text
Inject Database Saturation
```

### Actual system behavior

The failure controller changes the database behavior.

Actual requests begin experiencing:

```text
database latency
        ↓
payment latency
        ↓
payment errors
        ↓
order failures
        ↓
API errors
```

### OpenObserve

OpenObserve receives the resulting telemetry.

### Payasam

Payasam identifies the incident and displays:

```text
DATABASE DEGRADATION

Affected services:
Database
Payment
Order
API Gateway
```

Then:

```text
Affected simulated users: X / 10
Failed transactions: X
Estimated revenue at risk: ₹X
Estimated downtime cost: ₹X
```

### Recovery

User clicks:

```text
Recover System
```

The actual failure condition is removed.

Telemetry should return toward baseline.

The UI should reflect recovery.

---

# 26. KubeView

KubeView is a supporting visualization.

It is NOT the Payasam product UI.

Use KubeView to demonstrate:

```text
running Kubernetes workloads
service topology
pods
dependencies
```

It may be used during the hackathon presentation/demo to visually establish the local cloud environment.

Payasam remains the primary user-facing product.

---

# 27. Dashboard Relationship

The conceptual relationship is:

```text
KubeView
    ↓
Infrastructure topology

OpenObserve
    ↓
Observability

Payasam
    ↓
Operational + business intelligence
```

Do not duplicate KubeView's topology functionality inside Payasam unless required for a specific Payasam feature.

---

# 28. Recommended Project Structure

Claude Code should adapt this structure based on actual implementation needs:

```text
gcp-cluster/
│
├── services/
│   ├── api-gateway/
│   ├── auth/
│   ├── users/
│   ├── orders/
│   ├── payments/
│   ├── inventory/
│   ├── notifications/
│   └── database/
│
├── infrastructure/
│   ├── kubernetes/
│   ├── configs/
│   └── scripts/
│
├── telemetry/
│   ├── otel/
│   └── openobserve/
│
├── simulator/
│   ├── traffic/
│   └── failures/
│
├── payasam/
│   ├── backend/
│   ├── intelligence/
│   └── frontend/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── end-to-end/
│
├── docs/
│
├── scripts/
│
└── README.md
```

Do not create unnecessary files merely to satisfy this structure.

---

# 29. Configuration

All environment-specific configuration must be externalized.

At minimum:

```text
OPENOBSERVE_URL
OPENOBSERVE_ORG
OPENOBSERVE_USERNAME
OPENOBSERVE_PASSWORD
TRAFFIC_USERS
TRAFFIC_RATE
```

Use a `.env.example`.

Never commit real secrets.

---

# 30. Testing Philosophy

Testing is mandatory.

Do not declare a phase complete because the application starts.

Use multiple levels of testing.

## Unit tests

Test:

* business-impact formulas
* cost/optimization formulas
* configuration validation
* incident transformation
* failure-controller logic
* input validation

## Integration tests

Test:

```text
application
    ↓
OpenTelemetry
    ↓
OpenObserve
```

Test actual service-to-service behavior.

## End-to-end tests

At minimum test:

```text
Start environment
    ↓
Start traffic
    ↓
Inject database saturation
    ↓
Actual service degradation occurs
    ↓
Telemetry changes
    ↓
OpenObserve receives telemetry
    ↓
Payasam detects/processes incident
    ↓
Business impact is calculated
    ↓
UI reflects incident
    ↓
Recover
    ↓
System returns toward baseline
```

---

# 31. Test Cases Must Prove Real Behavior

A test that only asserts:

```text
incident.status == "critical"
```

is insufficient if the incident was manually created.

Tests should verify causal behavior.

For the primary scenario:

```text
Given healthy database behavior

When database saturation is injected

Then database latency increases

And dependent payment requests degrade

And order requests show failures

And API error rate increases

And telemetry is generated

And OpenObserve receives the telemetry

And Payasam can associate the incident with the affected service chain
```

The exact thresholds must be based on measured behavior rather than arbitrary brittle values.

---

# 32. Quality Gates

Development must occur in phases.

Never implement the entire system in one pass.

## Phase 0 — Repository and environment discovery

Tasks:

* inspect repository
* inspect Docker
* inspect Kubernetes availability
* inspect OpenObserve
* verify OpenObserve connectivity
* determine available local Kubernetes runtime
* document baseline

### Gate

Must prove:

```text
OpenObserve container running
OpenObserve reachable
Kubernetes runtime available
repository understood
```

---

# 33. Phase 1 — Running application

Build the smallest working application topology.

Implement:

* core services
* service communication
* database
* basic transaction
* health endpoints

### Gate

Must prove:

```text
Application starts
Services communicate
Transaction succeeds
Transaction failure is observable
Automated tests pass
```

Do not proceed if this is unstable.

---

# 34. Phase 2 — Real synthetic traffic

Implement:

* simulated users
* configurable request rate
* request IDs
* user IDs
* transaction IDs

### Gate

Must prove:

```text
Traffic generator creates actual HTTP requests
Requests reach application
Transactions execute
Traffic produces logs
Traffic is configurable
```

---

# 35. Phase 3 — OpenTelemetry + OpenObserve

Implement:

* logs
* metrics
* traces where practical
* OpenObserve ingestion

### Gate

Must prove with actual tests:

```text
Request
 ↓
Telemetry
 ↓
OpenObserve
```

Do not proceed if telemetry is unreliable.

---

# 36. Phase 4 — Failure Injection

Implement:

* database saturation
* CPU saturation
* traffic spike
* deployment regression
* recovery

Start with **Database Saturation only**.

Do not implement all failure scenarios before the first one works end-to-end.

### Gate

The database saturation scenario must demonstrate:

```text
failure injection
 ↓
actual database degradation
 ↓
actual dependent-service degradation
 ↓
actual telemetry change
 ↓
OpenObserve visibility
```

---

# 37. Phase 5 — Payasam Intelligence

Implement:

* incident processing
* business metadata
* business impact formulas
* resource optimization
* recommendation generation

### Gate

Given an actual observed incident, Payasam must produce:

```text
incident
affected services
affected users
failed transactions
estimated business impact
optimization recommendation where applicable
```

All calculations must be reproducible.

---

# 38. Phase 6 — Payasam UI

Implement:

* role selection
* Technical Operations view
* Business/Management view
* Investigation view
* failure controls
* live status updates

### Gate

A human must be able to perform the full demo without using the command line:

```text
Open Payasam
 ↓
Start/observe traffic
 ↓
Inject Database Saturation
 ↓
Watch actual system degrade
 ↓
See incident
 ↓
See business impact
 ↓
Inspect evidence
 ↓
Recover system
 ↓
See recovery
```

---

# 39. Phase 7 — Full System Verification

Run the complete test suite.

Perform at least three complete manual demo runs.

Record actual measurements:

```text
time to inject failure
time until telemetry changes
time until incident appears
time until business impact appears
time to recovery
```

Do not claim "real-time" using an arbitrary number.

Report measured behavior.

---

# 40. Quality Gate Rules

At the end of every phase:

1. Run automated tests.
2. Run relevant integration tests.
3. Run lint/type/static checks where applicable.
4. Start the actual system.
5. Perform a smoke test.
6. Compare implementation against this PRD.
7. Fix failures before continuing.

If a quality gate fails:

**STOP.**

Do not continue building new features on top of a broken foundation.

---

# 41. Verification Before Completion

Never say:

```text
implemented
```

unless the implementation has been executed and verified.

For every completed feature, report:

```text
What changed
Files changed
Tests run
Test results
Manual verification performed
Known limitations
```

If something could not be verified, explicitly state that.

Never fabricate successful test results.

---

# 42. Debugging Rules

When something fails:

Do not immediately patch symptoms.

Follow:

```text
Observe
 ↓
Reproduce
 ↓
Identify root cause
 ↓
Fix root cause
 ↓
Add/adjust regression test
 ↓
Re-run verification
```

Avoid arbitrary sleeps in tests.

Prefer condition-based waiting with explicit timeouts.

Avoid increasing timeouts merely to hide race conditions.

---

# 43. Dependency Rules

Before adding a dependency:

1. Determine whether the existing stack already provides the capability.
2. Determine whether the dependency materially simplifies implementation.
3. Check maintenance/compatibility concerns.
4. Add it only if justified.

Do not add libraries for trivial functionality.

---

# 44. Architecture Rules

Every component must have a clear responsibility.

Avoid:

```text
frontend doing business calculations
service directly manipulating OpenObserve internals everywhere
duplicate observability pipelines
duplicate databases
global mutable state
hardcoded environment values
```

Prefer explicit interfaces.

The Payasam intelligence layer should not become tightly coupled to every application service.

---

# 45. Data Integrity

Business-impact values must be derived from:

```text
actual observed application behavior
+
explicit business assumptions
```

Do not directly inject:

```text
users_affected = 5000
revenue_at_risk = 500000
```

unless those values are explicitly configuration inputs.

The preferred model is:

```text
actual failed transactions
        ×
configured transaction value
        =
estimated revenue impact
```

---

# 46. UI Requirements

The UI should prioritize:

* clear hierarchy
* readable status
* visible live changes
* role-specific information
* incident timelines
* actionable recommendations
* technical evidence when requested

Avoid building a generic admin dashboard.

The UI should tell a story.

The primary incident screen should make the sequence visually obvious:

```text
WHAT HAPPENED
      ↓
WHY
      ↓
WHO/WHAT IS AFFECTED
      ↓
BUSINESS IMPACT
      ↓
WHAT SHOULD WE DO
```

---

# 47. Demo Reliability

The entire demo must run locally.

The primary demo must not require:

* internet connectivity
* GCP
* cloud credentials
* external APIs
* paid AI APIs

The system must be restartable.

Provide scripts for:

```bash
start
stop
reset
test
demo
```

Exact commands should be chosen during implementation.

The `demo` workflow should make it easy to reproduce the primary database-saturation scenario.

---

# 48. Documentation Requirements

Create/update:

```text
README.md
docs/architecture.md
docs/development.md
docs/demo.md
```

README must explain:

* what Payasam is
* architecture
* prerequisites
* startup instructions
* OpenObserve setup
* Kubernetes setup
* how to run traffic
* how to inject failures
* how to access Payasam
* how to run tests

Demo documentation must explain the exact primary demonstration sequence.

Architecture documentation must distinguish:

```text
Local GCP-like environment
OpenObserve
Payasam Intelligence
Payasam UI
KubeView
```

---

# 49. Scope Boundaries

The following are explicitly OUT OF SCOPE for this implementation:

* real GCP deployment
* GCP billing
* BigQuery
* real Cloud Run deployment
* Slack integration
* one-click production remediation
* autonomous remediation
* Gemini dependency
* multi-agent Payasam architecture
* production-scale traffic
* production-grade financial accounting
* full GCP emulation
* replacing OpenObserve
* building another observability platform

Do not implement these unless this PRD is explicitly revised.

---

# 50. Primary Success Criteria

The project is successful when all of the following are true:

### Environment

A real local Kubernetes-based GCP-like production environment runs.

### Application

Multiple dependent services execute actual transactions.

### Traffic

Synthetic users generate actual requests.

### Observability

Those requests generate real telemetry visible in OpenObserve.

### Failure

Database Saturation changes the behavior of the actual running system.

### Propagation

The failure propagates through dependent services.

### Detection

The resulting telemetry becomes visible as an incident/degradation.

### Intelligence

Payasam correlates the technical incident with:

* affected users
* failed transactions
* business impact
* resource optimization information

### UX

A non-technical user can understand the business impact.

A technical user can investigate the technical evidence.

### Recovery

The system can recover from the injected failure.

### Verification

Automated and end-to-end tests prove the above behavior.

---

# 51. Final Demo Narrative

The finished product should support this sequence:

```text
1. Open Payasam.

2. Select Technical Operations or Business view.

3. Show healthy system.

4. Show simulated users generating actual traffic.

5. Briefly show KubeView as the local production environment.

6. Return to Payasam.

7. Click:
   "Inject Database Saturation"

8. The actual database becomes constrained.

9. Actual requests begin degrading.

10. Payment latency/errors increase.

11. Order requests begin failing.

12. API errors increase.

13. OpenObserve receives the resulting telemetry.

14. Payasam identifies the incident.

15. Technical view shows:
    affected services
    telemetry
    incident evidence

16. Business view shows:
    affected users
    failed transactions
    estimated revenue at risk
    estimated downtime cost

17. Payasam presents the recommended operational action.

18. Click:
    "Recover System"

19. Actual database behavior returns to normal.

20. Metrics/errors return toward baseline.

21. Payasam reflects recovery.

This is the primary proof that Payasam is not displaying mock incident data.
```

---

# 52. Final Engineering Principle

Build the smallest system that can convincingly demonstrate:

> **real application behavior → real failure → real telemetry → real observability → Payasam intelligence → understandable decision → recovery.**

Do not optimize for number of components.

Optimize for:

**causal correctness, observability, repeatability, clarity, testability and demo reliability.**

When forced to choose between another feature and making the existing failure scenario more reliable, choose reliability.

When forced to choose between architectural complexity and a simpler implementation that satisfies the requirement, choose the simpler implementation.

When forced to choose between an impressive claim and a measured result, choose the measured result.
