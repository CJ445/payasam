"""Fault-injection control via the Kubernetes API -- the HTTP-triggerable
equivalent of scripts/set-fault.sh / scripts/clear-faults.sh, so the
Payasam UI can drive the same real failures those scripts already
exercise (Phase 4), without a person running kubectl in a terminal.

Uses a read-modify-write on each Deployment's container env list (not a
raw strategic-merge PATCH) so behavior is unambiguous and easy to reason
about -- exactly what `kubectl set env` does under the hood. This is
still just "cause a real Deployment rollout with a different env var,"
the same mechanism DECISIONS.md D007 already established; nothing here
introduces a new failure-injection *mechanism*, only a new way to
trigger the existing one.

Requires the in-cluster ServiceAccount `payasam-backend` (see
infrastructure/kubernetes/payasam-backend-rbac.yaml) to have get/patch
on deployments and deployments/scale in the `payasam` namespace --
scoped to exactly that, nothing else.
"""

from __future__ import annotations

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

NAMESPACE = "payasam"

# (deployment, env var) pairs that constitute "a fault is active" --
# kept as the single source of truth so /faults, /status, and /remediation
# all agree on what "active" means.
ALL_FAULT_ENV_VARS = [
    ("payment-service", "PAYASAM_FAULT_PAYMENT_LATENCY_MS"),
    ("payment-service", "PAYASAM_FAULT_PAYMENT_ERROR_RATE"),
    ("database", "PAYASAM_FAULT_DB_LATENCY_MS"),
]

# Preset scenarios the UI can trigger -- deliberately a fixed named set,
# not free-form var/value input, so the UI never needs to know internal
# env var names and can't set an arbitrary env var on a Deployment.
FAULT_PRESETS = {
    "payment_latency": ("payment-service", "PAYASAM_FAULT_PAYMENT_LATENCY_MS", "500"),
    "payment_errors": ("payment-service", "PAYASAM_FAULT_PAYMENT_ERROR_RATE", "1.0"),
    "database_latency": ("database", "PAYASAM_FAULT_DB_LATENCY_MS", "200"),
}
UNAVAILABLE_SCENARIO = "payment_unavailable"  # special-cased: scale to 0, not an env var
SCENARIOS = frozenset(set(FAULT_PRESETS) | {UNAVAILABLE_SCENARIO})


def _apps_api() -> client.AppsV1Api:
    config.load_incluster_config()
    return client.AppsV1Api()


def _get_container(dep) -> client.V1Container:
    # Every Payasam service Deployment has exactly one container, named
    # after the service (see infrastructure/kubernetes/*.yaml).
    return dep.spec.template.spec.containers[0]


def _update_deployment_with_retry(deployment_name: str, mutate_container, max_attempts: int = 5) -> None:
    """Read-modify-write with retry on Kubernetes' optimistic-concurrency
    409 Conflict. The Deployment controller updates .status continuously
    in the background -- entirely independent of these env-var changes --
    so a read can go stale between our GET and PUT purely from that
    unrelated churn (confirmed experimentally: recover() calling this
    twice back-to-back for the same Deployment hit exactly this 409).
    Each retry re-reads (a fresh resourceVersion) before reapplying the
    same mutation, rather than reusing a possibly-stale object.
    """
    apps = _apps_api()
    last_exc: ApiException | None = None
    for _ in range(max_attempts):
        dep = apps.read_namespaced_deployment(deployment_name, NAMESPACE)
        mutate_container(_get_container(dep))
        try:
            apps.replace_namespaced_deployment(deployment_name, NAMESPACE, dep)
            return
        except ApiException as exc:
            if exc.status == 409:
                last_exc = exc
                continue
            raise
    raise last_exc


def set_env_var(deployment_name: str, var: str, value: str) -> None:
    def mutate(container: client.V1Container) -> None:
        env_list = list(container.env or [])
        for e in env_list:
            if e.name == var:
                e.value = str(value)
                break
        else:
            env_list.append(client.V1EnvVar(name=var, value=str(value)))
        container.env = env_list

    _update_deployment_with_retry(deployment_name, mutate)


def unset_env_var(deployment_name: str, var: str) -> None:
    def mutate(container: client.V1Container) -> None:
        if container.env:
            container.env = [e for e in container.env if e.name != var]

    _update_deployment_with_retry(deployment_name, mutate)


def get_env_vars(deployment_name: str) -> dict[str, str]:
    apps = _apps_api()
    dep = apps.read_namespaced_deployment(deployment_name, NAMESPACE)
    container = _get_container(dep)
    return {e.name: e.value for e in (container.env or []) if e.value is not None}


def scale(deployment_name: str, replicas: int) -> None:
    apps = _apps_api()
    apps.patch_namespaced_deployment_scale(deployment_name, NAMESPACE, {"spec": {"replicas": replicas}})


def get_replicas(deployment_name: str) -> int:
    # Deliberately reads the Deployment object itself, not the /scale
    # subresource: read_namespaced_deployment_scale(...).spec.replicas
    # comes back as None when replicas==0 (confirmed experimentally --
    # the API server omits the field at that value), which would make a
    # scaled-to-zero Deployment indistinguishable from an API error.
    # spec.replicas on the Deployment object itself does not have this
    # problem.
    apps = _apps_api()
    dep = apps.read_namespaced_deployment(deployment_name, NAMESPACE)
    return dep.spec.replicas


def get_resource_requests(deployment_name: str) -> dict[str, float | None]:
    """CPU (cores) and memory (GiB) requests on the Deployment's single
    container, for cost.py's modeled-cost calculation. Returns None for
    a resource with no request set on the container spec -- never
    fabricates a value, since an unset request is a real fact about the
    deployment, not missing data to guess at.

    Kubernetes CPU quantities are strings like "250m" (millicores) or
    "1" (whole cores); memory quantities are strings like "512Mi" or
    "1Gi". Parsed here rather than via a generic quantity library, since
    this project's own Deployment manifests only use these two suffix
    families (see infrastructure/kubernetes/*.yaml).
    """
    apps = _apps_api()
    dep = apps.read_namespaced_deployment(deployment_name, NAMESPACE)
    container = _get_container(dep)
    requests = (container.resources and container.resources.requests) or {}

    cpu_raw = requests.get("cpu")
    cpu_cores = _parse_cpu_to_cores(cpu_raw) if cpu_raw else None

    mem_raw = requests.get("memory")
    mem_gib = _parse_memory_to_gib(mem_raw) if mem_raw else None

    return {"cpu_cores": cpu_cores, "memory_gib": mem_gib}


def _parse_cpu_to_cores(value: str) -> float:
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)


def _parse_memory_to_gib(value: str) -> float:
    # Binary (Ki/Mi/Gi) and decimal (K/M/G) suffixes -- only the ones
    # actually used across this project's manifests.
    units = {
        "Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1,
        "K": 1e3 / (1024 ** 3), "M": 1e6 / (1024 ** 3), "G": 1e9 / (1024 ** 3),
    }
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor
    return float(value) / (1024 ** 3)  # bare bytes


def inject(scenario: str) -> None:
    if scenario == UNAVAILABLE_SCENARIO:
        scale("payment-service", 0)
        return
    if scenario not in FAULT_PRESETS:
        raise ValueError(f"unknown scenario: {scenario}")
    deployment, var, value = FAULT_PRESETS[scenario]
    set_env_var(deployment, var, value)


def recover() -> None:
    for deployment, var in ALL_FAULT_ENV_VARS:
        unset_env_var(deployment, var)
    if get_replicas("payment-service") == 0:
        scale("payment-service", 1)


def status() -> dict[str, dict[str, str]]:
    """Currently active faults, keyed by env var name (or the special
    PAYASAM_SERVICE_UNAVAILABLE key for the scale-to-zero scenario), each
    naming which deployment it's on and its current value. Empty dict
    means healthy."""
    active: dict[str, dict[str, str]] = {}
    for deployment, var in ALL_FAULT_ENV_VARS:
        val = get_env_vars(deployment).get(var)
        try:
            is_active = val is not None and float(val) > 0
        except ValueError:
            is_active = False
        if is_active:
            active[var] = {"deployment": deployment, "value": val}

    if get_replicas("payment-service") == 0:
        active["PAYASAM_SERVICE_UNAVAILABLE"] = {"deployment": "payment-service", "value": "0 replicas"}

    return active
