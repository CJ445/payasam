"""Unit tests for faults_control._update_deployment_with_retry's
Kubernetes-conflict retry logic -- the fix for a real bug caught during
live UI testing: calling recover() with two faults active on the same
Deployment hit a genuine 409 Conflict (the Deployment controller updates
.status continuously in the background, independent of these env-var
changes, so a read can go stale between GET and PUT purely from that
unrelated churn).

Mocked at the Kubernetes API boundary -- this is the one piece of
faults_control.py that's meaningfully unit-testable without a real
cluster; everything else needs live RBAC/API behavior and is covered
instead by tests/integration/test_faults_control_live.py.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "payasam" / "backend"))
import faults_control as fc  # noqa: E402
from kubernetes.client.exceptions import ApiException  # noqa: E402


def _fake_deployment():
    """Minimal stand-in for a V1Deployment -- only the attribute path
    _get_container() actually walks, plus a mutable .env list."""
    container = types.SimpleNamespace(env=[])
    return types.SimpleNamespace(spec=types.SimpleNamespace(template=types.SimpleNamespace(spec=types.SimpleNamespace(containers=[container]))))


def _mock_apps_api(read_side_effect, replace_side_effect):
    api = MagicMock()
    api.read_namespaced_deployment.side_effect = read_side_effect
    api.replace_namespaced_deployment.side_effect = replace_side_effect
    return api


def test_retries_and_succeeds_after_a_409_conflict():
    conflict = ApiException(status=409, reason="Conflict")
    api = _mock_apps_api(
        read_side_effect=[_fake_deployment(), _fake_deployment()],
        replace_side_effect=[conflict, None],
    )
    with patch.object(fc, "_apps_api", return_value=api):
        fc._update_deployment_with_retry("payment-service", lambda c: None)

    assert api.read_namespaced_deployment.call_count == 2, "must re-read (fresh resourceVersion) before retrying"
    assert api.replace_namespaced_deployment.call_count == 2


def test_reraises_immediately_on_a_non_conflict_error():
    server_error = ApiException(status=500, reason="Internal Server Error")
    api = _mock_apps_api(
        read_side_effect=[_fake_deployment()],
        replace_side_effect=[server_error],
    )
    with patch.object(fc, "_apps_api", return_value=api):
        with pytest.raises(ApiException) as exc_info:
            fc._update_deployment_with_retry("payment-service", lambda c: None)

    assert exc_info.value.status == 500
    assert api.replace_namespaced_deployment.call_count == 1, "must not retry a non-409 error"


def test_gives_up_after_max_attempts_of_persistent_conflict():
    conflict = ApiException(status=409, reason="Conflict")
    api = _mock_apps_api(
        read_side_effect=[_fake_deployment() for _ in range(5)],
        replace_side_effect=[conflict, conflict, conflict, conflict, conflict],
    )
    with patch.object(fc, "_apps_api", return_value=api):
        with pytest.raises(ApiException) as exc_info:
            fc._update_deployment_with_retry("payment-service", lambda c: None, max_attempts=5)

    assert exc_info.value.status == 409
    assert api.replace_namespaced_deployment.call_count == 5


def test_mutation_is_reapplied_on_each_retry_attempt():
    """The exact bug scenario: a fresh read on retry must have the
    mutation reapplied to it, not silently reuse the stale (already
    conflicting) object."""
    conflict = ApiException(status=409, reason="Conflict")
    deployments = [_fake_deployment(), _fake_deployment()]
    api = _mock_apps_api(
        read_side_effect=deployments,
        replace_side_effect=[conflict, None],
    )
    calls = []

    def mutate(container):
        calls.append(container)
        container.env = ["mutated"]

    with patch.object(fc, "_apps_api", return_value=api):
        fc._update_deployment_with_retry("payment-service", mutate)

    assert len(calls) == 2, "mutation must be reapplied to the freshly-read object on retry"
    for dep in deployments:
        assert dep.spec.template.spec.containers[0].env == ["mutated"]
