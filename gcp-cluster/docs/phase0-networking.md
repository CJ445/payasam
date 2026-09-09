# Phase 0 — Local Kubernetes Foundation & Networking

This document records what Phase 0 actually verified: that a workload
running inside the local `kind` cluster can reach the host-run OpenObserve
container. It is deliberately narrow — see `IMPLEMENTATION_PLAN.md` for the
full phase plan and `DECISIONS.md` (D011) for the architectural decision
this verification confirmed.

## Cluster

- Runtime: `kind` v0.29.0 (Kubernetes v1.33.1)
- Cluster name: `payasam`
- Config: `infrastructure/kind/kind-config.yaml` — single control-plane
  node, no workers (sufficient for the memory budget in
  `IMPLEMENTATION_PLAN.md` §1.5; add workers later only if a measured need
  arises).
- Namespace `payasam` created for future Payasam workloads (Phase 1+).

Start it with:

```bash
kind create cluster --config infrastructure/kind/kind-config.yaml
kubectl wait --for=condition=Ready node/payasam-control-plane --timeout=120s
```

Tear it down with:

```bash
kind delete cluster --name payasam
```

## The networking problem

OpenObserve runs as a plain host Docker container (`docker run -p
5080:5080 ...`), not inside the `kind` cluster's Docker network. This
machine runs native Docker Engine on Linux (not Docker Desktop), so
`host.docker.internal` does not resolve automatically inside containers —
this was confirmed experimentally, not assumed (see below).

## Verified mechanism

Pods reach OpenObserve via the **IPv4 gateway address of the `kind` Docker
bridge network**, which is an address on the host itself:

```
kubectl exec <pod> -- curl http://<kind-network-ipv4-gateway>:5080/healthz
```

On this machine that gateway is `172.18.0.1` (resolved dynamically — do not
hardcode it; it can differ across machines/recreations). Resolve it with:

```bash
docker network inspect kind --format '{{json .IPAM.Config}}' \
  | jq -r '.[] | select(.Subnet | contains(":") | not) | .Gateway'
```

**Why the naive one-line version is wrong:** `kind`'s Docker network is
dual-stack (IPv4 + IPv6). `docker network inspect kind --format
'{{(index .IPAM.Config 0).Gateway}}'` (the mechanism as originally proposed
in `IMPLEMENTATION_PLAN.md` §2.5) grabs whichever config happens to be at
index 0, which was found to be the **IPv6** gateway on this machine, not the
IPv4 one used here. The fix is to explicitly filter for the non-IPv6
subnet, as shown above. This was caught only because the mechanism was
tested experimentally rather than assumed — see `scripts/verify-openobserve-connectivity.sh`
for the corrected, working version.

Why this works: Docker's published-port DNAT (`-p 5080:5080`) binds
OpenObserve's port on all of the host's interfaces, including the `kind`
bridge's gateway interface — even though the `openobserve` container itself
is on the unrelated default `bridge` network (172.17.0.0/16), not the
`kind` network (172.18.0.0/16). A pod's egress traffic is routed out
through its node container, which is a peer on the `kind` bridge network
and can reach the gateway address directly.

## Experimental verification performed

Run via `scripts/verify-openobserve-connectivity.sh`, which:

1. Resolves the `kind` network's IPv4 gateway.
2. Creates a temporary pod (`curlimages/curl`) in the `payasam` namespace.
3. From inside that pod, calls `http://<gateway-ip>:5080/healthz`.
4. Confirms `HTTP 200` and body `{"status":"ok"}`.
5. Deletes the temporary pod (self-cleaning).

Results observed:

| Check | Result |
|---|---|
| `host.docker.internal` from inside a pod | fails to resolve (`curl: (6) Could not resolve host`) — confirms the assumption in D011, not just theoretical |
| `http://172.18.0.1:5080/healthz` from inside a pod (1st call) | `HTTP 200`, `{"status":"ok"}` |
| `http://172.18.0.1:5080/healthz` from inside a pod (2nd, independent call) | `HTTP 200`, `{"status":"ok"}` |
| Cluster health after test | node `Ready`, all `kube-system`/`local-path-storage` pods `Running`, API `/healthz` → `ok` |
| Leftover test resources | none — temporary pod deleted after each run |

No OpenObserve credentials were used or required — only the unauthenticated
`/healthz` endpoint, per Phase 0 scope.

## What later phases should do with this

`IMPLEMENTATION_PLAN.md`'s Phase 3 task list already calls for wiring the
resolved gateway IP into a ConfigMap consumed by application services
(`OPENOBSERVE_URL`). That ConfigMap is intentionally **not** created in
Phase 0 — Phase 0's job was only to prove the mechanism works, not to start
wiring application configuration. The corrected resolution logic in
`scripts/verify-openobserve-connectivity.sh` is the reference
implementation for that later step.
