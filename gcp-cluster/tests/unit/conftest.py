"""Shared test helpers for Phase 1 unit tests.

These tests exercise the real service code in-process (no Docker, no
Kubernetes) by loading each service's `main.py` under a unique module
name and, where a service calls another service over HTTP, wiring httpx
to route those calls to the *actual* downstream FastAPI app via
`httpx.ASGITransport` instead of the network. This tests real business
logic and real cross-service contracts, not mocks of them. The separate
`tests/integration` suite proves the same behavior over a real network
against the deployed Kubernetes services.
"""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

SERVICES_DIR = Path(__file__).resolve().parents[2] / "services"


def load_service_module(service_dir: str, alias: str):
    """Import services/<service_dir>/main.py as a module named `alias`,
    with the service's own directory on sys.path so its same-directory
    imports (e.g. `import db`, `from logging_utils import ...`) resolve."""
    service_path = SERVICES_DIR / service_dir
    if str(service_path) not in sys.path:
        sys.path.insert(0, str(service_path))

    spec = importlib.util.spec_from_file_location(alias, service_path / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def install_mounts(monkeypatch: pytest.MonkeyPatch, mounts: dict[str, httpx.ASGITransport]) -> None:
    """Make every httpx.AsyncClient() constructed from now on route the
    given base URLs to in-process ASGI apps instead of the network,
    without touching the services' own source code."""
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("mounts", mounts)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


@pytest.fixture
def database_app(tmp_path, monkeypatch):
    """A fresh database service (its own temp SQLite file) per test."""
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "test.db"))
    module = load_service_module("database", f"database_main_{id(tmp_path)}")
    return module.app


@pytest.fixture
def database_client(database_app):
    """TestClient for the database app with lifespan (startup/init_db) run."""
    from fastapi.testclient import TestClient

    with TestClient(database_app) as client:
        yield client


@pytest.fixture
def full_stack(tmp_path, monkeypatch):
    """Loads all five Phase 1 services in-process and wires their internal
    httpx calls (which default to the same Kubernetes Service DNS names
    used in production, e.g. http://database:8000) to route to each
    other's real ASGI app instead of the network. Returns a dict of apps.

    This lets unit tests exercise the *real* cross-service request chain
    (order-service -> inventory-service -> payment-service -> database)
    without Docker or a cluster.
    """
    monkeypatch.setenv("DATABASE_FILE", str(tmp_path / "full_stack.db"))

    tag = id(tmp_path)
    database_module = load_service_module("database", f"database_main_{tag}")
    database_module.db.init_db()

    inventory_module = load_service_module("inventory-service", f"inventory_main_{tag}")
    payment_module = load_service_module("payment-service", f"payment_main_{tag}")
    order_module = load_service_module("order-service", f"order_main_{tag}")
    gateway_module = load_service_module("api-gateway", f"gateway_main_{tag}")

    mounts = {
        "http://database:8000": httpx.ASGITransport(app=database_module.app),
        "http://inventory-service:8000": httpx.ASGITransport(app=inventory_module.app),
        "http://payment-service:8000": httpx.ASGITransport(app=payment_module.app),
        "http://order-service:8000": httpx.ASGITransport(app=order_module.app),
    }
    install_mounts(monkeypatch, mounts)

    return {
        "database": database_module.app,
        "inventory": inventory_module.app,
        "payment": payment_module.app,
        "order": order_module.app,
        "gateway": gateway_module.app,
        "mounts": mounts,
    }
