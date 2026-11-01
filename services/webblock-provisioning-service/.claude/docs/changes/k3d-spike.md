# Change note: k3d-spike

Change: spike step 1. A local k3d cluster replaces Kind for the live suites; the live suites follow the 3-try
go-live (plan `go-live-tries` Task 10) and the one-label preview host.
IDs: TR-44, TR-45 (spike, design `.claude/docs/designs/k3s-site-cluster-spike.md`); FR-19 to FR-22 (Task 10);
FR-20, FR-52 (live preview cases).
Agreed: the owner, in the lead's session (constitution version 5 names Kind and k3d as the local cluster tools).

What changes:
- `deploy/kind/versions.sh`: k3d v5.9.0 with its SHA-256; `make kind-tools` installs it into `bin/` (G2).
- `scripts/prototype/` proto-up and proto-lib: one engine switch, context `k3d-webblock-proto` on
  `https://127.0.0.1:<port>`, kubeconfig and caches under `bin/kind-state/` (G2, G5).
- The local kubeconfig check accepts `kind-webblock-*` or `k3d-webblock-*`, loopback only, refused in production (G3).
- `deploy/prototype/gateway`: the Traefik Gateway API variant (G6); certificates (G7); tenant policy (G8).
- `tests/e2e/` and the Kind-only suites: the 3-try go-live (Task 10) and the preview host cases.
- The guard rule for the k3d context (G4) goes on branch `guard-doks`, so the owner installs one guard.

Tests first: the G3 context rows; the Task 10 cases.
Risks: Traefik and kube-router differ from Cilium and Istio (design section 4); the cluster run needs an owner slot
(`free -m`, `docker ps`, one heavy suite at a time); no cluster command runs in this change.
Out of scope: step 2 on EC2 (owner-run).
Docs touched: the prototype runbooks, the spike design's result section after the run.
Constitution: II.1 and II.3 pass (version 5); VI.1 pass (local only, never EC2 or DOKS).
