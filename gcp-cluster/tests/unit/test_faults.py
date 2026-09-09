"""Unit tests for Phase 4 controlled fault injection.

Faults read their configuration from env vars at *import* time (see
faults.py's module docstring), so behavioral tests load a fresh copy of
the relevant service's `main.py` (via conftest.py's `load_service_module`,
same pattern Phase 1 established) after setting the env var, rather than
monkeypatching an already-imported module's constants.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "payment-service"))
import faults as faults_module  # noqa: E402

from conftest import load_service_module  # noqa: E402


@pytest.fixture(autouse=True)
def clear_faults_module_cache():
    """`faults.py`'s constants are computed once at import time from env
    vars. Since it's imported under its plain (unaliased) name by every
    service's main.py, Python's module cache would otherwise make the
    *first* test in a session to import it "stick" for every later test
    too, regardless of what env vars a later test sets. `db.py` is the
    same story one level up: it's also imported under its plain name
    (`import db` inside database/main.py), so once cached it holds
    whichever `faults` module object was live at *its own* first import
    -- clearing only "faults" isn't enough once "db" has already latched
    onto a stale one. Force fresh imports of both every test."""
    sys.modules.pop("faults", None)
    sys.modules.pop("db", None)
    yield
    sys.modules.pop("faults", None)
    sys.modules.pop("db", None)


# ---- configuration parsing / defaults ----

def test_faults_disabled_by_default(monkeypatch):
    for var in ("PAYASAM_FAULT_PAYMENT_LATENCY_MS", "PAYASAM_FAULT_PAYMENT_ERROR_RATE", "PAYASAM_FAULT_DB_LATENCY_MS"):
        monkeypatch.delenv(var, raising=False)
    tag = "faults_default_test"
    module = load_service_module("payment-service", f"faults_defaults_{tag}")
    assert module.faults.PAYMENT_LATENCY_MS == 0.0
    assert module.faults.PAYMENT_ERROR_RATE == 0.0
    assert module.faults.DB_LATENCY_MS == 0.0
    assert module.faults.active_faults() == []


def test_env_float_parsing_rejects_garbage_and_falls_back_to_default():
    assert faults_module._env_float("PAYASAM_TEST_DOES_NOT_EXIST", 0.0) == 0.0


def test_configuration_validation_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_PAYMENT_LATENCY_MS", "not-a-number")
    tag = "faults_invalid_test"
    module = load_service_module("payment-service", f"faults_invalid_{tag}")
    assert module.faults.PAYMENT_LATENCY_MS == 0.0  # invalid value -> disabled, not a crash


def test_active_faults_lists_only_enabled_ones(monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_PAYMENT_LATENCY_MS", "250")
    monkeypatch.delenv("PAYASAM_FAULT_PAYMENT_ERROR_RATE", raising=False)
    monkeypatch.delenv("PAYASAM_FAULT_DB_LATENCY_MS", raising=False)
    tag = "faults_active_test"
    module = load_service_module("payment-service", f"faults_active_{tag}")
    active = module.faults.active_faults()
    assert active == ["payment_latency_ms=250.0"]


# ---- deterministic error injection ----

def test_error_injection_rate_zero_never_triggers(monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_PAYMENT_ERROR_RATE", "0")
    tag = "faults_rate0"
    module = load_service_module("payment-service", f"faults_rate0_{tag}")
    assert all(not module.faults.should_inject_payment_error() for _ in range(50))


def test_error_injection_rate_one_always_triggers(monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_PAYMENT_ERROR_RATE", "1.0")
    tag = "faults_rate1"
    module = load_service_module("payment-service", f"faults_rate1_{tag}")
    assert all(module.faults.should_inject_payment_error() for _ in range(50))


# ---- behavioral: service with faults disabled (regression) ----

def test_payment_service_normal_behavior_when_faults_disabled(tmp_path, monkeypatch):
    for var in ("PAYASAM_FAULT_PAYMENT_LATENCY_MS", "PAYASAM_FAULT_PAYMENT_ERROR_RATE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    tag = f"disabled_{id(tmp_path)}"

    database_module = load_service_module("database", f"database_main_{tag}")
    database_module.db.init_db()
    payment_module = load_service_module("payment-service", f"payment_main_{tag}")

    import httpx
    from conftest import install_mounts
    install_mounts(monkeypatch, {"http://database:8000": httpx.ASGITransport(app=database_module.app)})

    with TestClient(payment_module.app) as client:
        resp = client.post("/payments", json={"transaction_id": "txn-1", "amount_cents": 500})
    assert resp.status_code == 201
    assert resp.json()["status"] == "succeeded"


# ---- behavioral: latency injection ----

def test_payment_service_latency_injection_adds_real_delay(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_PAYMENT_LATENCY_MS", "300")
    monkeypatch.delenv("PAYASAM_FAULT_PAYMENT_ERROR_RATE", raising=False)
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    tag = f"latency_{id(tmp_path)}"

    database_module = load_service_module("database", f"database_main_{tag}")
    database_module.db.init_db()
    payment_module = load_service_module("payment-service", f"payment_main_{tag}")

    import httpx
    from conftest import install_mounts
    install_mounts(monkeypatch, {"http://database:8000": httpx.ASGITransport(app=database_module.app)})

    import time
    with TestClient(payment_module.app) as client:
        start = time.monotonic()
        resp = client.post("/payments", json={"transaction_id": "txn-1", "amount_cents": 500})
        elapsed_ms = (time.monotonic() - start) * 1000.0

    assert resp.status_code == 201  # latency fault alone doesn't fail the request
    assert elapsed_ms >= 300, f"expected the injected 300ms delay to be observable, took {elapsed_ms:.1f}ms"


# ---- behavioral: deterministic error injection at the service level ----

def test_payment_service_error_injection_returns_structured_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_PAYMENT_ERROR_RATE", "1.0")
    monkeypatch.delenv("PAYASAM_FAULT_PAYMENT_LATENCY_MS", raising=False)
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    tag = f"error_{id(tmp_path)}"

    database_module = load_service_module("database", f"database_main_{tag}")
    database_module.db.init_db()
    payment_module = load_service_module("payment-service", f"payment_main_{tag}")

    import httpx
    from conftest import install_mounts
    install_mounts(monkeypatch, {"http://database:8000": httpx.ASGITransport(app=database_module.app)})

    with TestClient(payment_module.app) as client:
        resp = client.post("/payments", json={"transaction_id": "txn-1", "amount_cents": 500})

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "payment_unavailable"

    # and no payment was persisted -- the fault must not silently succeed
    with TestClient(database_module.app) as db_client:
        assert db_client.get("/payments/does-not-matter").status_code == 404


# ---- database latency fault ----

def test_database_latency_fault_extends_span_and_lock_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("PAYASAM_FAULT_DB_LATENCY_MS", "200")
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    tag = f"dblatency_{id(tmp_path)}"

    database_module = load_service_module("database", f"database_main_{tag}")
    database_module.db.init_db()

    import time
    start = time.monotonic()
    result = database_module.db.reserve_inventory("product-001", 1)
    elapsed_ms = (time.monotonic() - start) * 1000.0

    assert result["outcome"] == "reserved"
    assert elapsed_ms >= 200, f"expected the injected 200ms db delay to be observable, took {elapsed_ms:.1f}ms"


def test_database_reset_and_init_are_not_affected_by_db_latency_fault(tmp_path, monkeypatch):
    """apply_fault=False on init_db/reset_db -- administrative operations
    shouldn't be slowed by a fault meant for the transaction path."""
    monkeypatch.setenv("PAYASAM_FAULT_DB_LATENCY_MS", "500")
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    tag = f"dbnofault_{id(tmp_path)}"

    database_module = load_service_module("database", f"database_main_{tag}")

    import time
    start = time.monotonic()
    database_module.db.init_db()
    elapsed_ms = (time.monotonic() - start) * 1000.0
    assert elapsed_ms < 500, f"init_db should not be delayed by the fault, took {elapsed_ms:.1f}ms"


# ---- fault restoration ----

def test_fault_restoration_disabled_after_env_cleared(tmp_path, monkeypatch):
    """Simulates the restore step: a fresh process load with the fault
    env var unset behaves exactly like the never-faulted baseline."""
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    monkeypatch.delenv("PAYASAM_FAULT_PAYMENT_LATENCY_MS", raising=False)
    monkeypatch.delenv("PAYASAM_FAULT_PAYMENT_ERROR_RATE", raising=False)
    tag = f"restored_{id(tmp_path)}"

    database_module = load_service_module("database", f"database_main_{tag}")
    database_module.db.init_db()
    payment_module = load_service_module("payment-service", f"payment_main_{tag}")
    assert payment_module.faults.active_faults() == []

    import httpx
    from conftest import install_mounts
    install_mounts(monkeypatch, {"http://database:8000": httpx.ASGITransport(app=database_module.app)})

    with TestClient(payment_module.app) as client:
        resp = client.post("/payments", json={"transaction_id": "txn-1", "amount_cents": 500})
    assert resp.status_code == 201
