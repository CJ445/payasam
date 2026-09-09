"""Unit tests for the static, pure-logic parts of the UI fault-control
surface: that every scenario the UI can trigger and every fault the
backend can detect has a matching, non-empty remediation rule.

The Kubernetes-mutating parts of payasam/backend/faults_control.py
(reading/patching real Deployments) are intentionally NOT unit tested
here -- they need a real cluster and are covered instead by
tests/integration/test_faults_control_live.py, which caught two real
bugs during development (an inverted "faults_readable" flag, and the
Kubernetes Scale subresource silently reporting replicas=0 as None) that
a mocked unit test would not have caught.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "payasam" / "backend"))
import faults_control as fc  # noqa: E402
import main as backend_main  # noqa: E402


def test_scenarios_cover_every_fault_preset_plus_unavailable():
    assert fc.SCENARIOS == set(fc.FAULT_PRESETS) | {fc.UNAVAILABLE_SCENARIO}


def test_every_fault_preset_targets_a_real_known_deployment():
    known_deployments = {"payment-service", "database"}
    for deployment, var, value in fc.FAULT_PRESETS.values():
        assert deployment in known_deployments
        assert var.startswith("PAYASAM_FAULT_")
        assert float(value) > 0


def test_all_fault_env_vars_matches_fault_presets_targets():
    # ALL_FAULT_ENV_VARS is the source of truth status()/recover() use to
    # know what to check/clear -- it must include every var a preset can
    # actually set, or recover() would leave a fault behind.
    preset_vars = {(d, v) for d, v, _ in fc.FAULT_PRESETS.values()}
    assert preset_vars == set(fc.ALL_FAULT_ENV_VARS)


def test_every_possible_active_fault_key_has_a_remediation_rule():
    possible_keys = {var for _, var in fc.ALL_FAULT_ENV_VARS} | {"PAYASAM_SERVICE_UNAVAILABLE"}
    assert possible_keys <= set(backend_main.REMEDIATION_RULES.keys())
    for rule in backend_main.REMEDIATION_RULES.values():
        assert rule["issue"]
        assert rule["recommended_action"]
