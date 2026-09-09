"""Controlled fault injection for Phase 4 experiments.

Disabled by default -- every fault reads from a `PAYASAM_FAULT_*`
environment variable that defaults to zero/off. With none set, this
module is a complete no-op and application behavior is unchanged from
Phase 1-3.

This single file is duplicated byte-for-byte across every service that
needs fault injection (`database`, `payment-service`) -- same pattern as
`logging_utils.py`/`telemetry.py`. Each service only reads the constants
relevant to it (payment-service: PAYMENT_*; database: DB_LATENCY_MS);
unused ones stay at their default (0/off) and are simply never read by
that service's code. Keeping the file identical everywhere, rather than
service-specific, matters here specifically because the shared
in-process unit-test harness (tests/unit/conftest.py) loads multiple
services' `main.py` in one Python process, and `import faults` is cached
process-globally by module name -- if the two copies' contents ever
diverged, whichever service's copy got imported first would silently
"win" for every other service too.

This is deliberately NOT a general chaos-engineering framework: it
implements exactly the four controlled, deterministic faults Phase 4
calls for, nothing else, per PRD's "no unnecessary complexity" guidance
and this phase's explicit instruction to keep injection simple,
reversible, and config-driven rather than baked into business logic.

Fault state is read once at import time (fixed per process, changed only
by restarting the process with different env vars -- e.g. `kubectl set
env deployment/payment-service PAYASAM_FAULT_PAYMENT_LATENCY_MS=500`,
which triggers a normal rolling restart). A given running pod has one
fixed fault configuration for its lifetime, never flips mid-flight.
"""

import os
import random


def _env_float(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


PAYMENT_LATENCY_MS = _env_float("PAYASAM_FAULT_PAYMENT_LATENCY_MS", 0.0)
PAYMENT_ERROR_RATE = _env_float("PAYASAM_FAULT_PAYMENT_ERROR_RATE", 0.0)
DB_LATENCY_MS = _env_float("PAYASAM_FAULT_DB_LATENCY_MS", 0.0)


def active_faults() -> list[str]:
    faults = []
    if PAYMENT_LATENCY_MS > 0:
        faults.append(f"payment_latency_ms={PAYMENT_LATENCY_MS}")
    if PAYMENT_ERROR_RATE > 0:
        faults.append(f"payment_error_rate={PAYMENT_ERROR_RATE}")
    if DB_LATENCY_MS > 0:
        faults.append(f"db_latency_ms={DB_LATENCY_MS}")
    return faults


def should_inject_payment_error() -> bool:
    if PAYMENT_ERROR_RATE <= 0:
        return False
    if PAYMENT_ERROR_RATE >= 1.0:
        return True
    return random.random() < PAYMENT_ERROR_RATE
