# Spike: generated sites on a k3s cluster

Stage 2 design of a spike: a short experiment with recorded results, not a feature. It answers one question:
can Provisioning deploy, probe, promote, roll back, suspend and resume sites on a small k3s cluster (the owner's
EC2 instance) while the rest of Provisioning stays on DOKS? The owner approves it at gate 2. Nothing here changes
the BRD, PRD, FRD or the Notion database design; a rule the spike proves becomes a decision record only after the
results (section 9).

## 1. Context

- Today all four processes (`api`, `worker`, `build_worker`, `bootstrap`) and the sites target one cluster. The
  local proof is the Kind stack of decision `0014` (`make proto-up`: Cilium 1.20.2 as CNI, kube-proxy replacement
  and Gateway API controller; cert-manager with the local CA; admission policies; a Cilium tenant policy).
- Goal: the generated websites live on a separate, cheap k3s cluster on one EC2 instance (8 GB RAM, 2 vCPU),
  because the budget is tight and the investors want to see the MVP. Jenkins runs on another EC2 machine
  (`jenkins-edge`). PostgreSQL, RabbitMQ, Gitea and the API stay on DOKS.
- Step 1 (local, agents run it): a k3d cluster (k3s in Docker) replaces Kind and the existing live suites run
  against it. Step 2 (EC2, the owner runs every cluster step): the worker and the bootstrap run inside k3s in the
  existing `in-cluster` mode; the API and the build worker stay on DOKS.
- Base: branch `cqrs-layout`, commit `3ddbdc3`.
- Pinned facts used below: k3s `v1.36.5+k3s1` (Kubernetes 1.36, the same minor as the Kind node
  `kindest/node:v1.36.4`) ships Traefik `v3.7.13` (chart v40). Traefik's Kubernetes Gateway provider implements
  Gateway API Standard `v1.6.3`. k3d `v5.9.0` is the newest k3d release.

## 2. IDs the spike touches

FRs exercised (no FR changes): FR-6 (production refuses changes), FR-17 (deploy), FR-19 (go-live), FR-20 (preview
host), FR-21 and FR-22 (restore after a failed promote), FR-23 (`uncertain`), FR-25 (fences), FR-33 and FR-34 (admin
suspend and resume), FR-35 (admin redeploy), FR-50 (admin rollback), FR-52 (preview token), FR-53 (pull Secret,
not started).

TRs touched:

- TR-1: same processes; the worker and the bootstrap move to another cluster.
- TR-6: server-side apply and fences unchanged; measured on k3s.
- TR-11: Provisioning's database stays in `platform-db` on DOKS, reached over the link of G11.
- TR-22, TR-23: RabbitMQ stays on DOKS, reached over the link of G11.
- TR-31: no Istio on k3s, so the worker cannot call Payment over the mesh; the MVP uses the entitlement stand-in.
- TR-34: worker and bootstrap keep projected tokens checked by SelfSubjectReview; the kubeconfig check accepts a
  local k3d cluster (G3).
- TR-35: the admission policies run unchanged on k3s (ValidatingAdmissionPolicy is GA since Kubernetes 1.30).
- TR-42: the chart needs per-process switches and a Kubernetes NetworkPolicy variant (G9).
- TR-43: egress rules rewritten from CiliumNetworkPolicy to NetworkPolicy (G9).
- TR-44: sites on Traefik's Gateway API instead of Istio's; still behind Cloudflare Tunnel (G6, G13).
- TR-45: wildcard certificate from cert-manager; the issuer differs on EC2 (G13).
- TR-46, TR-52: Gitea registry kept; a node pull credential is the stop-gap until the pull Secret exists (G12).
- TR-47: the MVP runs the `prototype` profile because production refuses changes (G15).

Not covered: tenant network isolation has no FR (section 11, Q1). FR-47 to FR-49 (automatic flow) are not built,
so deploy and go-live are owner requests in both steps.

## 3. Decisions

Each decision is labelled `lead` (designer's call under the owner's delegation of routine questions) or `owner`
(the owner decided, or must).

### G1. Spike shape (lead)

The spike runs on a throwaway branch `k3s-spike` in a worktree under `.claude/worktrees/`, from the head of
`cqrs-layout`. Only G3 and G4 (kubeconfig acceptance, guard hook) are written to be kept; every other change is
evidence. Anything adopted afterwards goes through stages 1 to 3 like any change. Test: the spike's final report
lists each changed file as "keep" or "evidence".

### G2. Step 1 cluster: k3d on the developer machine (lead)

- k3d `v5.9.0` (binary pinned with its SHA-256 in `deploy/kind/versions.sh`, installed by `make kind-tools` into
  `./bin`), node image `rancher/k3s:v1.36.5-k3s1` pinned by digest.
- Cluster name `webblock-proto`, so the context is `k3d-webblock-proto`. One server and one agent, like Kind's
  control plane and worker.
- `--api-port 127.0.0.1:<port>`: k3d's default binds the API on `0.0.0.0` and writes `https://0.0.0.0:<port>`
  into the kubeconfig, which every check refuses on purpose.
- `--network kind`: the cluster joins the existing Docker network `kind` (created with `docker network create kind`
  when Kind is not running). The registry alias `webblock-proto-registry.test:5000`, Jenkins, BuildKit, the Git
  server, the catalog database and Provisioning's own containers keep their names and addresses, and every script
  that names network `kind` works unchanged.
- `--kubeconfig-update-default=false --kubeconfig-switch-context=false`, with `HOME` and `KUBECONFIG` pointed into
  `bin/kind-state/proto/`. The kubeconfig is written with `k3d kubeconfig merge ... --output
  bin/kind-state/proto/kubeconfig` (the implementer checks the exact flags against the pinned k3d's `--help`).
- k3s defaults kept: flannel, kube-proxy, kube-router network policy, ServiceLB, Traefik. `metrics-server` is
  disabled (unused). Registry trust uses `--registry-config` (a `registries.yaml` naming the project-local registry
  CA, mounted with `--volume`).
- One cluster at a time, as today: `proto-up` refuses when a Kind cluster or another k3d cluster runs.

Test: `bin/kubectl --kubeconfig bin/kind-state/proto/kubeconfig config view --minify` names `k3d-webblock-proto`
and `https://127.0.0.1:<port>`.

### G3. Accept a local k3d kubeconfig, still loopback only, still refused in production (lead; code to keep)

What refuses a non-Kind kubeconfig today:

1. `src/provisioning/infrastructure/kubernetes/_client.py`, `_checked_kubeconfig`: the current context must match
   `_KIND_CONTEXT = r"kind-webblock-[a-z0-9]([a-z0-9-]*[a-z0-9])?"`, the server must match
   `_LOCAL_SERVER = r"https://127\.0\.0\.1:([0-9]{1,5})"`, there must be no `proxy-url`, and the user must not use
   `exec` or `auth-provider`.
2. `src/provisioning/infrastructure/config/common.py`, `read_cluster`: `KUBE_CONNECTION=kubeconfig` is refused in
   the production profile. This is already engine-neutral and does not change; only the `KubeconfigConnection`
   docstring ("a local Kind cluster") gets the words "or k3d".
3. `deploy/kind/proto-lib.sh`, `require_local_kubeconfig`: `case "$cur" in kind-webblock-*)` and
   `[ "$cur" = "$CONTEXT" ]`, with `CONTEXT="kind-$CLUSTER"` from `deploy/kind/lib.sh`.
4. The live test harness compares the context to the literal `kind-webblock-proto`:
   `tests/integration/policy/_kube.py`, `tests/integration/stack/_stack.py`,
   `tests/integration/kubernetes/conftest.py`, `tests/e2e/faults/_recovery_support.py`,
   `tests/e2e/faults/test_bootstrap_namespace_rebuilt.py`, `tests/e2e/faults/test_hosting_retry.py`.
5. `.claude/hooks/guard.py` (G4).

The change, the smallest that is still safe:

```python
_LOCAL_CONTEXT = re.compile(r"(?:kind|k3d)-webblock-[a-z0-9]([a-z0-9-]*[a-z0-9])?")
...
if not isinstance(current, str) or not _LOCAL_CONTEXT.fullmatch(current):
    raise KubeconfigRefused("the current context is not a kind-webblock-* or k3d-webblock-* context")
```

Everything else stays: the server is still exactly `https://127.0.0.1:<port>`; no proxy; no `exec` or
`auth-provider` user (k3d's admin user authenticates with a client certificate, so it passes);
`persist_config=False`; the loaded host checked again; `$KUBECONFIG` and `~/.kube/config` never read; production
still refuses `KUBE_CONNECTION=kubeconfig`. In step 2 the processes use `in-cluster`, which needs no change.

Scripts and harness: `proto-up` writes the engine (`kind` or `k3d`) to `bin/kind-state/proto/engine`.
`proto-lib.sh` sets `CONTEXT="$engine-$CLUSTER"` and accepts `kind-webblock-*|k3d-webblock-*`, still requiring the
loopback server and mode 0600. One test helper reads the same file and returns the expected context; the six harness
files call it instead of the literal.

Tests (unit, written first, each naming TR-34 and FR-17):

- `k3d-webblock-proto` on `https://127.0.0.1:<port>` connects.
- Refused: `k3d-webblock-`, `k3d-other`, `k3d-webblock-UPPER`, `k3s-webblock-proto`, `K3D-webblock-proto`, and a
  `k3d-webblock-proto` context whose server is `https://0.0.0.0:<port>` (k3d's default), `https://localhost:<port>`
  or a public address. No request reaches the fake server in any refused case.
- `read_cluster` in the production profile with `KUBE_CONNECTION=kubeconfig` is still refused (existing test kept).

### G4. Guard hook: allow the local k3d cluster, nothing else (lead; the owner approves hook edits)

Today `local_kind` returns True only for a kubeconfig under `bin/kind-state/` whose every server is loopback, with
no `exec`, `proxy-url` or `auth-provider`, and whose context starts with `kind-`. There is no rule for the `k3d`
binary at all, so `k3d cluster create` (which writes `~/.kube/config` by default) and `k3d kubeconfig get` (which
prints the admin credentials) are not blocked.

Changes:

1. `local_kind`: the context check becomes `context.startswith(("kind-", "k3d-webblock-"))`. The path, loopback and
   risky-entry checks stay.
2. A new `check_k3d`, called for the program name `k3d`:
   - deny `k3d kubeconfig get` (prints credentials);
   - deny `k3d kubeconfig merge` and `k3d kubeconfig write` unless `--output` (`-o`) resolves under
     `bin/kind-state/`; deny `--kubeconfig-merge-default` and `--kubeconfig-switch-context` there;
   - `k3d cluster create`: deny unless `--kubeconfig-update-default=false` and `--kubeconfig-switch-context=false`
     are given, `--api-port` starts with `127.0.0.1:`, and `KUBECONFIG` (from the command's environment) resolves
     under `bin/kind-state/`; then ask (it changes a local cluster; prefer `make proto-up`);
   - `k3d cluster delete`: deny `--all`; deny unless `KUBECONFIG` resolves under `bin/kind-state/` (k3d removes the
     cluster from the default kubeconfig on delete); otherwise ask;
   - allow `version`, `help`, `completion`, `cluster list`, `node list`, `registry list`, and `image import` into a
     `webblock-*` cluster; everything else asks.
3. `k3s` and `k3s kubectl` on the developer machine: deny (there is no local k3s outside Docker; on EC2 the owner
   runs them).

Tests in `.claude/hooks/test_guard.py`: one allow or deny case per rule above, plus
`bin/kubectl --kubeconfig <k3d kubeconfig> --context k3d-webblock-proto get pods` allowed, and the same with a
`k3d-other` context or a `0.0.0.0` server treated as not local.

### G5. Stack scripts: one engine switch (lead; evidence)

`make proto-up PROTO_ENGINE=k3d` runs the same steps with the replacements below. Every other step (registry mirror,
cert-manager, RBAC, offline page, tokens, Git server, BuildKit, Jenkins, catalog, admission policies, Loki) is
shared.

- Step 2, `kind create cluster` with `deploy/kind/cluster.yaml` (no CNI, no kube-proxy, containerd `hosts.toml`):
  becomes `k3d cluster create` as G2, with registry trust through `registries.yaml`.
- Step 3, Gateway API v1.6.1 standard CRDs: the same pinned bundle, unless k3s's `traefik-crd` chart already ships
  Gateway API CRDs. Then the run records the bundle version k3s installs and uses it if it is v1.6.1 or newer; one
  CRD never has two owners.
- Step 4, Cilium 1.20.2 chart and its checks: becomes Traefik from k3s, configured by a `HelmChartConfig` (G6);
  wait for `deployment/traefik` and for `gatewayclass/traefik` Accepted.
- Step 5, LB-IPAM pool and L2 announcements: becomes k3s ServiceLB. The Traefik Service's
  `status.loadBalancer.ingress[0].ip` is a node address on network `kind`, reachable from the host.
- Step 8c, `CiliumClusterwideNetworkPolicy` and its `Valid` wait: no cluster-wide policy; the tenant NetworkPolicy
  of G8 is applied by the test harness to test tenants only.

`scripts/prototype/run-provisioning.sh` reads the Gateway address from the Traefik Service instead of
`ciliumloadbalancerippool webblock-kind-pool`, drops the `lbipam.cilium.io/ips` annotation, and deletes a
NetworkPolicy instead of `ciliumnetworkpolicy provisioning-migrate`. The memory floor `KIND_MIN_AVAILABLE_MB` stays;
the result records k3d's real use.

### G6. Gateway API on Traefik (lead; evidence)

- Traefik is configured by `HelmChartConfig` `kube-system/traefik` (on k3d mounted into
  `/var/lib/rancher/k3s/server/manifests/`; on EC2 written there by the owner):
  `providers.kubernetesGateway.enabled: true`, `providers.kubernetesIngress.enabled: false`,
  `providers.kubernetesCRD.enabled: false` (only HTTPRoutes route traffic, and the admission policies cover
  HTTPRoutes only), the chart's own Gateway off, its GatewayClass `traefik` on, entry points `web` 8000 and
  `websecure` 8443 exposed as 80 and 443. The value names are checked against chart v40's `values.yaml` in step 1.
- `deploy/prototype/gateway/20-gateway.yaml` gets a k3s variant: `gatewayClassName: traefik` and listener ports
  8000 and 8443, because Traefik's rule is "Gateway listener ports must match the configured EntryPoint ports".
  The name `webblock-gateway/webblock-public`, the hostnames, the TLS Secret `sites-wildcard-tls` and
  `allowedRoutes` (tenant label selector) do not change.
- No worker code changes. The worker writes HTTPRoutes naming `webblock-gateway/webblock-public`, reads only the
  route's `Accepted` and `ResolvedRefs` conditions at the current generation, and probes over HTTPS with
  `SITE_GATEWAY_HTTPS_URL` and `SITE_GATEWAY_CA_FILE`. It never reads the GatewayClass or the controller name (the
  string `io.cilium/gateway-controller` exists only in a unit test fixture).

Checks the spike must answer, each a line in the result (section 8):

1. Header match `type: Exact` on `X-Webblock-Preview`. FR-52.1: no token, no candidate. Code: `render.py`
   `preview_match`, `site_cluster._matches_token`.
2. `RequestHeaderModifier` with `remove: [X-Webblock-Preview]`. FR-52.2: the site never sees the token.
3. `ResponseHeaderModifier` `set: X-Webblock-Release` (an Extended feature). The probe requires it on every answer
   (`probe_site`).
4. Route status carries our `parentRef` with its namespace, and `observedGeneration` on each condition.
   `site_cluster._accepted` matches both.
5. A cross-namespace `backendRef` to `webblock-offline/offline-page` through the bootstrap's ReferenceGrant (FR-22,
   offline page).
6. Listener hostname `*.sites.webblock.test` matches `<subdomain>-preview.sites.webblock.test` (FR-20).
7. A backend with no ready endpoint: does Traefik's 503 carry the route's `X-Webblock-Release`? Envoy's does, and
   `SiteDependencyUnavailable` and the failure codes of task H5a rely on it (`probe_site`,
   `tests/e2e/faults/test_failure_codes_live.py`).
8. Route switch gap: how many non-2xx answers while a route moves (`tests/e2e/test_flows.py` counts Cilium's few
   503s).

### G7. Certificates on k3d (lead)

Unchanged: cert-manager v1.21.2 from the local registry, ClusterIssuer `webblock-local-ca` (name-constrained to
`.test`), Certificate `sites-wildcard` for `*.sites.webblock.test`, which covers the preview hosts too. The worker's
probe and `curl --cacert bin/kind-state/proto/local-ca.crt` verify Traefik's HTTPS listener against it.

### G8. Tenant network policy on kube-router (lead; evidence; adoption needs an FR)

kube-router enforces only Kubernetes NetworkPolicy, which is namespaced: there is no cluster-wide policy and no
"Gateway entity". The Kind policy `webblock-tenant-network` (one `CiliumClusterwideNetworkPolicy` for every tenant)
becomes one NetworkPolicy per `wb-<projectId>` namespace:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: webblock-tenant-network
  namespace: wb-<projectId>
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
  ingress:
    - from:  # only Traefik
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
          podSelector: {matchLabels: {app.kubernetes.io/name: traefik}}
  egress:
    - to: [{ipBlock: {cidr: <catalog address>/32}}]  # the catalog database
      ports: [{protocol: TCP, port: 5432}]
    - to:  # cluster DNS
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
          podSelector: {matchLabels: {k8s-app: kube-dns}}
      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}]
    - to:  # through the Gateway to any site, as any client could
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
          podSelector: {matchLabels: {app.kubernetes.io/name: traefik}}
      ports: [{protocol: TCP, port: 8000}, {protocol: TCP, port: 8443}]
```

Differences from Cilium: no `egressDeny` (the metadata address is refused because nothing allows it; on EC2, G18
adds IMDSv2 with hop limit 1); no "Envoy checks the client's egress" rule (Traefik is an ordinary pod, so the third
egress rule is plain); kubelet probes rely on kube-router allowing traffic from the local node (a check).

Who writes it is the open part. The natural writer is the bootstrap, beside the ResourceQuota and LimitRange it
already writes (III.6 allows it; `webblock-bootstrap-scope` and the bootstrap RBAC would need `networkpolicies`). No FR
asks for tenant network isolation today, so the spike does not change the bootstrap. The test harness applies the
policy to its test tenants, and `test_tenant_network_live.py` gets a k3s variant that proves refusals against
destinations that exist and answer when allowed (another tenant's pod, the API server, the registry), because there
is no `cilium-dbg monitor` to show a policy drop. Q1 asks the owner for the FR.

### G9. The Provisioning chart on k3s (lead; evidence; adoption is a TR-42 task)

`deploy/helm/provisioning/templates/networkpolicies.yaml` renders `CiliumNetworkPolicy` (it names the entities
`kube-apiserver` and `ingress`), which fails on k3s (no such CRD). The bootstrap has no replica switch, so the chart
cannot run "API only" or "worker and bootstrap only". On the spike branch:

- `networkPolicy.engine: cilium | kubernetes` (default `cilium`). `kubernetes` renders NetworkPolicies with the same
  destinations: the API server becomes an `ipBlock` of the k3s server address on TCP 6443, the Gateway becomes the
  Traefik pod selector, outside addresses come from the existing `networkPolicy.*` values.
- `<process>.enabled` (default true) for `api`, `worker`, `buildWorker`, `bootstrap`; the schema keeps refusing an
  unknown key.
- Chart tests: both engines render; `enabled: false` renders no Deployment, ConfigMap or policy for that process.

### G10. Step 2 topology (lead)

- DOKS: `api` (prototype profile, G15), `build_worker`, `platform-db` (database `provisioning`), RabbitMQ, Gitea and
  its registry, the existing `doks` Cloudflare tunnel, and a `catalog-link` client (G11).
- EC2 `jenkins-edge`: Jenkins, unchanged (the build worker calls it; it pushes site images to Gitea).
- EC2 k3s: `worker` and `bootstrap` (in-cluster mode, chart with `api` and `buildWorker` disabled), Traefik,
  cert-manager, the offline page, the catalog database (G14), the sites `wb-<projectId>`, a `sites` tunnel and two
  link clients (G11).

The worker and the bootstrap are disabled on DOKS at the same time (two workers would apply to two clusters). The
platform layer on k3s is applied by the owner: namespaces, ServiceAccounts and RBAC (`deploy/prototype/rbac`), the
admission policies (`deploy/prototype/policy`, `site-params` with the Gitea image prefix), the Gateway (G6), the
offline page.

### G11. Network path between EC2 and DOKS: Cloudflare Tunnel with Access service tokens (lead)

Choice: TCP over Cloudflare Tunnel, with one Access application per destination that admits only a service token,
and `cloudflared access tcp` client pods on the calling side. Each client Service carries the same in-cluster name as
the real destination on the other side, so host names, settings and TLS server names are identical in both clusters.

- Worker and bootstrap (k3s) to `platform-db-rw.webblock-data.svc.cluster.local:5432` (DOKS): a k3s Service of that
  name in namespace `webblock-data`, backed by a `cloudflared access tcp` pod; an Access application on a published
  TCP hostname of the existing `doks` tunnel.
- Worker (k3s) to RabbitMQ on 5672 (DOKS): the same pattern, one client Deployment and one Access application.
- Project service (DOKS) to `catalog-db.webblock-data.svc.cluster.local:5432` (k3s): a DOKS Service `catalog-db` in
  `webblock-data` backed by a `cloudflared access tcp` pod; an Access application on a published TCP hostname of
  the new `sites` tunnel on k3s.

Why this one for the MVP:

- web-block-config already runs a Cloudflare tunnel and Access: no new vendor, nothing paid on the Zero Trust free
  plan, no inbound port opened on DOKS or EC2.
- Everything is a Kubernetes object on both sides (GitOps on DOKS) with no host routing, so it does not interact
  with flannel, kube-router or the WARP split-tunnel set-up the team uses for people.
- Each destination has its own Access application and service token, so a leaked EC2 token reaches only the
  Provisioning database and RabbitMQ, never Keycloak, Gitea or the other databases.
- Unlike containerd (G12), `cloudflared access tcp` passes Access with a service token.

Options not chosen:

- WARP Connector on the EC2 host: host routing beside flannel and kube-router, and the reverse direction would need
  WARP on DOKS nodes, which managed nodes do not allow cleanly.
- A private link such as a WireGuard or Tailscale gateway: a new privileged component and a new dependency on DOKS.
- Public PostgreSQL and RabbitMQ behind a DigitalOcean load balancer with a source-address allow list: opens data
  ports to the internet and costs a load balancer.

Rules: PostgreSQL runs `verify-full` against the DOKS database CA (the client Service keeps the server's name).
RabbitMQ uses `amqps` if the DOKS listener offers TLS; otherwise the tunnel is the only encryption, and the result
says so. Each client Deployment runs 2 replicas. Service token IDs and secrets live in Kubernetes Secrets the owner
creates, never in a file of this repository.

Cost if wrong: every worker transaction pays the round trip through Cloudflare (G17 keeps it small); a Cloudflare
outage stops deploys and suspends on EC2, while sites keep serving (they need only the local catalog).

### G12. Site images from the Gitea registry (lead)

Facts: the Gitea registry is behind Cloudflare Access; containerd cannot send Access headers; the per-site pull
Secret (FR-53, TR-52) is not built.

MVP choice:

1. An Access policy "Bypass" for the EC2 instance's Elastic IP on the registry host only. Gitea still requires its
   own authentication, so the bypass removes only the Access layer, for that one address.
2. k3s `registries.yaml` on the EC2 node holds a read-only Gitea account for the registry host (file mode 0600,
   root only). containerd pulls with it; no pull Secret is in any tenant namespace.
3. Site images stay `<gitea registry>/webblock/sites/<projectId>@sha256:<digest>`; `site-params` holds that prefix,
   so `webblock-site-image` still pins each Deployment to its own project's image.

When FR-53 is built, the node credential is removed and the bootstrap's pull Secret takes over. Options not chosen:
a registry on the EC2 instance (Jenkins on `jenkins-edge` would push twice, and TR-46 names Gitea); a mirror through
a local `cloudflared access tcp` (Gitea's token realm points at the public host, so containerd still meets Access).

### G13. Public path and site certificate on EC2 (lead; host names are the owner's)

- A new tunnel `sites` (cloudflared, 2 replicas in k3s) routes the wildcard public hostname `*.<site domain>` to
  `https://traefik.kube-system.svc.cluster.local:443` with `matchSNItoHost: true` and the Gateway's CA as `caPool`.
  Traefik needs no public address; ServiceLB is disabled on EC2.
- The Gateway's wildcard certificate comes from cert-manager with a private CA ClusterIssuer on k3s (as on Kind,
  name-constrained to the site domain). Only cloudflared and the worker's probe trust it; no DNS credential is on
  the EC2 instance. Visitors see Cloudflare's edge certificate.
- Cloudflare's free Universal SSL covers the zone apex and one level of wildcard (`*.<zone>`). So the site domain
  should be a zone of its own (`<subdomain>.<zone>`), or the owner pays for Advanced Certificate Manager for
  `<subdomain>.sites.<zone>`. Preview hosts are not published through Cloudflare in the MVP: today only the worker's
  probe uses them (FR-52), and it talks to Traefik directly.
- The worker's `SITE_GATEWAY_HTTPS_URL` is `https://traefik.kube-system.svc.cluster.local:443` and
  `SITE_GATEWAY_CA_FILE` is the private CA (ConfigMap `provisioning-gateway-ca`).

### G14. The catalog database lives on the k3s EC2 next to the sites (owner considering; lead recommends)

Sites read the catalog on every request; the platform writes it rarely (Project's catalog writes and migrations,
Provisioning's per-site logins). So the database sits beside its readers. It is not placed on DOKS.

- PostgreSQL 18 (image pinned by digest) as one StatefulSet in namespace `webblock-data` on k3s, on a `local-path`
  volume, with TLS from a cert-manager Certificate whose names include `catalog-db.webblock-data.svc.cluster.local`.
  No operator such as CloudNativePG for the MVP (memory, G16).
- Schema: the Project service's Flyway migrations, as decision `0017` section 1 says. For the MVP the owner runs
  them as a one-off Job on k3s; later Project's own pipeline runs them over the link.
- The bootstrap (on k3s) reaches it in-cluster with `CATALOG_HOST`, `CATALOG_ADMIN_USER` and `CATALOG_ADMIN_PASS`;
  `CATALOG_RUNTIME_HOST` and `CATALOG_RUNTIME_PORT` name the same in-cluster Service for the sites.
- The Project service (DOKS) reaches it through the `catalog-link` Service of G11, which has the same name, so its
  `verify-full` check passes. Provisioning's DOKS processes (`api`, `build_worker`) never connect to the catalog.
- Backups: a nightly `pg_dump` CronJob to object storage outside the instance (Q4).

How a site gets its connection, and whether generated code can set hosts or passwords. This is already true in the
code:

1. The bootstrap's `access_setup` (`src/provisioning/infrastructure/catalog/access_setup.py`) creates the site's own
   login role and writes Secret `catalog-runtime` in `wb-<projectId>` with exactly `PGHOST`, `PGPORT`, `PGDATABASE`,
   `PGUSER`, `PGPASSWORD` and `PGSSLMODE` (`render.py`: the six keys, each non-empty).
2. The worker renders the site Deployment with `envFrom: [{secretRef: {name: catalog-runtime}}]` and no other
   environment. Values set by Kubernetes override any `ENV` baked into the image.
3. Only the bootstrap identity may write that Secret (`webblock-bootstrap-scope`), only the worker writes site objects
   (`webblock-tenant-writer`), and `webblock-site-image` pins the image. The demo site reads only those six
   variables (`deploy/prototype/demo-sites/clothing-shop/src/db/config.ts`).

So no host or password comes from generated code; a site receives only its own role. Not yet enforced on k3s: a site
whose code connects somewhere else is stopped only by a tenant network policy (G8, Q1). Whether Project's generator
always reads the six variables is Project's contract (Q6), not invented here.

### G15. Profile for the MVP: prototype (lead; owner confirms)

FR-6 and TR-47: the production profile refuses every change until Project's and Payment's gRPC calls pass on Kind.
The MVP test therefore runs every process in the `prototype` profile: the worker uses the entitlement stand-in file
(no mesh on k3s, TR-31), and the API keeps its access mode. To limit the risk of a prototype API on DOKS, the API is
reachable only through the private tunnel and Access (no public route) during the MVP. Production keeps refusing;
lifting FR-6 is the gRPC contract work, not this spike.

### G16. Capacity of the 8 GB, 2 vCPU instance (lead; measured in step 2)

Estimates; step 2 replaces them with measured numbers.

| Part | Memory | CPU request |
| --- | --- | --- |
| OS and k3s (`system-reserved`, `kube-reserved`), eviction margin | 1.1 GiB | 400m |
| CoreDNS, local-path provisioner (metrics-server disabled) | 0.1 GiB | 100m |
| Traefik | 0.1 GiB | 100m |
| cert-manager (3 pods) | 0.15 GiB | 30m |
| cloudflared: `sites` tunnel x2, two link clients x2 | 0.25 GiB | 60m |
| Offline page | 0.03 GiB | 10m |
| worker and bootstrap (chart values 384 and 256 MiB) | 0.63 GiB | 200m |
| Catalog PostgreSQL (`shared_buffers` 256 MB) | 1.0 GiB | 150m |
| Safety margin (k3s often uses more than reserved) | 0.5 GiB | 0 |
| **Platform total** | **about 3.9 GiB** | **about 1050m** |

Left for sites: about 4.1 GiB and about 950m. A site pod is 100m and 256 MiB, with limits equal to requests
(`render.py`). Memory fits about 16 pods; CPU fits 9, so CPU binds. A deploy runs the candidate beside the live pod,
so one slot stays free per concurrent deploy (`WORKER_CONCURRENCY=1`). Result: **about 8 live sites for the MVP**,
10 if the platform requests are trimmed after measuring. More sites need a 4 vCPU instance or a lower
`SITE_CPU_MILLICORES` (a hard 50m limit slows server rendering; measure first).

If the instance is a burstable type (t3 or t4g), its sustained CPU is well below 2 vCPU: prefer a
fixed-performance type, or record the credit balance during step 2. Site images must match the instance's CPU
architecture (Jenkins builds amd64 today; a Graviton instance is arm64).

### G17. Region (lead; owner picks)

Put the EC2 instance (and `jenkins-edge`) in the AWS region of the same metro as the DOKS region, for example DO
`sgp1` with AWS `ap-southeast-1`, DO `fra1` with AWS `eu-central-1`, DO `lon1` with AWS `eu-west-2`. Both tunnel ends
then use the same Cloudflare location, and a worker round trip stays at a few milliseconds instead of hundreds. That
keeps the worker's leases, heartbeats and database deadlines inside their current settings.

### G18. EC2 hardening for the MVP (lead)

IMDSv2 required with hop limit 1 (pods cannot get instance metadata tokens) and no instance role; a security group
with no inbound rule except SSH (or SSM) from the owner's address; the k3s API (6443) not public; k3s Secrets
encryption on; k3s installed from the checksum-verified binary and air-gap image archive of the pinned release.

## 4. Cilium-specific assumptions and their k3s or Traefik replacement

- `deploy/kind/cluster.yaml`: no CNI and no kube-proxy, Cilium replaces both. Replacement: k3s defaults (flannel,
  kube-proxy, kube-router).
- `deploy/kind/values/cilium.yaml`, `deploy/kind/values/cilium-local-images.yaml`, the Cilium pins in
  `deploy/prototype/versions.sh` and the Cilium notes in `deploy/kind/versions.sh`. Replacement: Traefik from k3s
  with a `HelmChartConfig` (G6); k3s image pins.
- `deploy/kind/manifests/lb-ipam.yaml.tmpl`: Cilium LB-IPAM and L2. Replacement: ServiceLB on k3d; the tunnel and no
  load balancer on EC2.
- `deploy/kind/proto-up.sh` steps 3 to 5: Cilium rollout, kube-proxy replacement check, `gatewayclass/cilium`.
  Replacement: Traefik rollout and `gatewayclass/traefik`.
- `deploy/kind/proto-certs.sh`: runs after Cilium (network up). Replacement: runs after k3s is Ready.
- `deploy/prototype/gateway/20-gateway.yaml`: `gatewayClassName: cilium`, ports 80 and 443. Replacement: `traefik`,
  ports 8000 and 8443.
- `deploy/prototype/policy/tenant-network.yaml.tmpl` and `deploy/kind/proto-policy.sh`:
  `CiliumClusterwideNetworkPolicy`, entities `ingress` and `host`, `egressDeny`, the `Valid` status. Replacement: a
  per-namespace NetworkPolicy (G8) with no status to wait on.
- `deploy/helm/provisioning/templates/networkpolicies.yaml`: `CiliumNetworkPolicy` with entities `kube-apiserver`
  and `ingress`. Replacement: `networkPolicy.engine: kubernetes` (G9).
- `scripts/prototype/run-provisioning.sh`: `ciliumloadbalancerippool`, the `lbipam.cilium.io/ips` annotation,
  `ciliumnetworkpolicy provisioning-migrate`. Replacement: the Traefik Service address, no annotation, a
  NetworkPolicy.
- `src/provisioning/infrastructure/kubernetes/render.py`: a docstring line only ("no CiliumNetworkPolicy").
  Replacement: nothing.
- `tests/integration/stack/test_tenant_network_live.py`: drops proven with `cilium-dbg monitor`. Replacement: a k3s
  variant with live destinations (G8).
- `tests/e2e/test_flows.py`, `tests/integration/kubernetes/test_kubernetes_live.py`,
  `tests/e2e/faults/test_failure_codes_live.py`: Envoy's 503 carries the route header; a few 503s while a route
  moves. Replacement: measured on Traefik (G6 checks 7 and 8).
- `tests/e2e/incluster/test_identities.py`, `tests/unit/scripts/test_cluster_mode.py`, `tests/chart/test_chart.py`,
  `tests/chart/test_cluster_values.py`, `tests/chart/values-kind.json`: the chart's CiliumNetworkPolicy. Kept for
  Kind; k3s variants only if adopted.
- `tests/unit/infrastructure/kubernetes/test_site_cluster.py`: fixture `io.cilium/gateway-controller`. Replacement:
  nothing (the code ignores it).

## 5. What success means

Step 1 (k3d), all on the spike branch, every count recorded:

1. `make proto-up PROTO_ENGINE=k3d` ends healthy; context `k3d-webblock-proto` on `https://127.0.0.1:<port>`.
2. `make proto-test-kind`, `proto-run` and `proto-e2e` run to the end. Every failure is listed and is either a
   Cilium-specific test from section 4 or a failed G6 check (each with its reason), nothing else.
3. One sample site is built by the stack's Jenkins, deployed (FR-17), answers HTTPS through Traefik with the local
   CA, is promoted (FR-19), rolled back by an admin (FR-50, through the existing fault test that makes routing
   `uncertain`), and suspended and resumed by an admin (FR-33, FR-34).
4. Preview: no token, or another project's token, does not reach the candidate (FR-52.1); the site never sees
   `X-Webblock-Preview` (FR-52.2).
5. Every admission policy is enforced (the policy live suite passes), and the G8 NetworkPolicy refuses the
   destinations Cilium refuses on Kind.
6. The kept changes (G3, G4) pass `make py-fmt-check py-lint py-typecheck py-layers py-test` and
   `python3 -I .claude/hooks/test_guard.py`.

Step 2 (EC2), the owner runs the cluster steps:

1. Worker and bootstrap on k3s pass SelfSubjectReview and connect to PostgreSQL and RabbitMQ on DOKS through G11.
2. One sample site goes from a published source to live on `https://<subdomain>.<site domain>` through Cloudflare,
   with its catalog content from the EC2 catalog database.
3. A second release is deployed and promoted; the first becomes `superseded`. An admin suspends it (offline page)
   and resumes it. An admin redeploy keeps the same digest.
4. Measured: k3s memory and CPU at idle and with the site; promote time; the worker's round trip to PostgreSQL; time
   to first byte through Cloudflare.

The spike succeeds if both lists hold, or if every gap has a named, costed fix. It fails if the worker cannot confirm
routes on Traefik (G6) or cannot hold its leases over the link (G11).

## 6. Owner step list for step 2 (commands for the owner; agents never run them)

Placeholders are in angle brackets. No secret value is written here; each comes from the owner's secret store.

1. Instance (AWS, same metro as DOKS, G17): amd64, 8 GB, 2 vCPU, Ubuntu LTS, 40 GB gp3, an Elastic IP, a security
   group with SSH from the owner's address only. Then:

   ```sh
   aws ec2 modify-instance-metadata-options --instance-id <id> \
     --http-tokens required --http-put-response-hop-limit 1
   ```

2. k3s files, checksum-verified (on the instance):

   ```sh
   V=v1.36.5%2Bk3s1
   B="https://github.com/k3s-io/k3s/releases/download/$V"
   curl -fsSLO "$B/k3s"
   curl -fsSLO "$B/k3s-airgap-images-amd64.tar.zst"
   curl -fsSLO "$B/sha256sum-amd64.txt"
   sha256sum --check --ignore-missing sha256sum-amd64.txt
   sudo install -m 0755 k3s /usr/local/bin/k3s
   sudo mkdir -p /var/lib/rancher/k3s/agent/images /var/lib/rancher/k3s/server/manifests /etc/rancher/k3s
   sudo cp k3s-airgap-images-amd64.tar.zst /var/lib/rancher/k3s/agent/images/
   ```

3. Before the first start, the owner writes (contents from the spike branch; only item c holds a credential):
   a. `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml`, the `HelmChartConfig` of G6;
   b. `/etc/rancher/k3s/config.yaml` with `disable: [servicelb, metrics-server]`, `secrets-encryption: true`,
      `write-kubeconfig-mode: "0600"` and the kubelet arguments `system-reserved=cpu=200m,memory=512Mi` and
      `kube-reserved=cpu=200m,memory=512Mi`;
   c. `/etc/rancher/k3s/registries.yaml` (mode 0600) with the read-only Gitea account for the registry host (G12).
4. Install and start: download `https://get.k3s.io` to `install.sh`, read it, then:

   ```sh
   sudo INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_VERSION=v1.36.5+k3s1 sh ./install.sh
   sudo k3s kubectl get nodes
   sudo k3s kubectl -n kube-system rollout status deployment/traefik
   ```

5. Cloudflare (dashboard): the Access "Bypass" policy for the Elastic IP on the Gitea registry host (G12); on the
   `doks` tunnel, published TCP hostnames for the Provisioning database and RabbitMQ, each with an Access application
   that allows one service token; a new tunnel `sites` with the wildcard public hostname of G13 and a published TCP
   hostname for the catalog, with its own Access application and service token.
6. On k3s, in this order (`sudo k3s kubectl apply` of the spike branch's files): Gateway API CRDs if k3s lacks them;
   cert-manager v1.21.2 and the private CA ClusterIssuer; the Gateway variant (G6); RBAC; admission policies; the
   offline page; the catalog StatefulSet and its Certificate; the Flyway Job; the link clients and the `sites`
   tunnel. Their Secrets are created by the owner with `kubectl create secret generic ... --from-file`.
7. Provisioning on k3s, with the spike values (worker and bootstrap only, `networkPolicy.engine: kubernetes`,
   prototype profile, stand-ins):

   ```sh
   helm upgrade --install provisioning deploy/helm/provisioning -n webblock -f <spike values file>
   ```

8. DOKS (web-block-config): scale the worker and the bootstrap to zero, add the `catalog-link` client and its
   Service, keep the API without a public route.
9. Run the step 2 checks of section 5; the agent reads the results the owner pastes.

Undo the spike: `sudo /usr/local/bin/k3s-uninstall.sh` on the instance, revert step 8 on DOKS, and delete the
Cloudflare hostnames, applications and tokens of step 5.

## 7. Constitution check

- I.2 kept: every kept change cites TR-34 and FR-17; chart and tenant policy changes stay evidence until an FR or TR
  task exists (Q1).
- III.3 kept: no table, column, role or grant changes; the catalog schema stays Project's Flyway migrations.
- III.4 kept: PostgreSQL stays the authority; RabbitMQ crosses the link only to notify.
- III.7 kept: no token is forwarded; the link's service tokens are Cloudflare credentials, never in logs or messages.
- III.8 kept: no API change.
- III.6 kept: worker and bootstrap keep their identities, RBAC and admission policies; the API and the build worker
  hold no k3s token.
- VI.1 kept: agents run only the local k3d cluster on 127.0.0.1 (G3, G4); every EC2 and DOKS change is a command in
  section 6.
- V.1 kept: G3 and G4 tests come first; the spike suites report real counts; a suite not run is "not run".
- VIII.1 kept: the owner approves this design, the hook edit and any adoption.
- VII.1 kept: no secret value here; another service's contract is a question (Q6), not invented.

A TR deviation, not a constitution rule: k3s's bundled images (Traefik, CoreDNS) come by tag inside the
checksum-verified release archive, not pinned by digest one by one (TR-9). Accepted for the spike and recorded in
the result.

## 8. Result template

Fill one line per item; write "not run" when not run. The spike's final report (`ai/reviews/k3s-spike.md`) holds it.

```
Spike: k3s site cluster   Branch: k3s-spike <sha>
Engine: k3d <version>, k3s <version>, Traefik <version>
Step 1
  proto-up                     pass | fail   time:   memory used by k3d:
  proto-test-kind              passed / failed / skipped:   failures (test, reason):
  proto-run (host, cluster)    pass | fail
  proto-e2e                    passed / failed:   failures (test, reason):
  G6 checks 1 to 8             one line each: pass | fail, reason
  G8 network policy            refused: other tenant, API server, registry, metadata
                               allowed: catalog, DNS, Gateway
  Sample site                  deployed | probed | promoted | admin rollback | suspended | resumed
  Kept changes gates           fmt | lint | mypy | layers | unit | test_guard
Step 2
  Link                         PostgreSQL RTT p50 / p95:   RabbitMQ connected:   lease losses:
  Sample site                  live URL | second release promoted | suspend | resume | redeploy
  Capacity                     idle memory / CPU:   per site:   sites that fit:
  Timings                      promote:   first byte through Cloudflare:
Decision proposed              adopt | adopt with fixes (list) | do not adopt (why)
```

## 9. What changes, and cost if wrong

- Code to keep: `_client.py` (G3), the `config/common.py` docstring, unit tests; `.claude/hooks/guard.py` and
  `test_guard.py` (G4; the owner approves the hook edit).
- Evidence only: the `deploy/` k3d and k3s variants, chart switches, scripts, the harness context helper, the k3s
  network test.
- Docs: none until adoption. If adopted: a decision record "sites on a k3s cluster" (TR-44, TR-45, TR-46 and TR-52
  wording, the link of G11), the runbook `prototype-stack.md` and a new EC2 runbook, and the pins in `TECH_STACK.md`.
- Other services: web-block-config (link client, Access applications, worker and bootstrap off on DOKS) and the
  Project service (catalog host and its Flyway run over the link). This repository changes nothing of theirs.
- Cost if wrong: step 1 costs a few days on a branch that can be deleted, and the kept change only widens a local,
  loopback-only check. Step 2 costs one small instance and Cloudflare settings that the undo list removes. The real
  risk is believing k3d proves EC2: it proves neither Cloudflare, nor the link latency, nor the instance's capacity,
  which is why step 2 exists.

## 10. Risks

- Traefik misses one G6 feature (most likely check 7 or 8). Effect: probe codes differ and some e2e tests fail.
  Mitigation: record it; change how the probe reads a 503 under a TR, or keep Cilium on k3s (Q2).
- No tenant network policy on EC2 until an FR exists. Effect: a site's code could reach other sites' pods directly,
  or the k3s API address. Mitigation: G18 hardening; only sample sites in the MVP; Q1.
- Link latency or a Cloudflare outage. Effect: worker leases lapse and deploys stall. Mitigation: same metro (G17),
  2 client replicas; sites keep serving.
- Prototype profile on DOKS. Effect: the API accepts stand-ins. Mitigation: the API only through the private tunnel
  and Access (G15).
- Node registry credential. Effect: any pod on the node can pull any site image. Mitigation: images hold no secret;
  `webblock-site-image` pins images; FR-53 replaces it.
- One instance with a local volume. Effect: losing the instance loses the catalog and all sites. Mitigation: nightly
  `pg_dump` off the instance (Q4); sites are rebuilt from their releases.
- CPU on 2 vCPU. Effect: about 8 sites, slow cold starts at 100m. Mitigation: measure in step 2; size up before
  paying customers.

## 11. Open questions for the owner

- Q1. Tenant network isolation has no FR. Should the spec ask for it? Options: a) a new FR: site pods take traffic
  only from the Gateway and reach only the catalog, DNS and the Gateway, and the bootstrap writes one NetworkPolicy
  per tenant; b) keep Cilium-only isolation (Kind and DOKS). Recommendation: a), before any real customer site on
  EC2.
- Q2. If Traefik fails a G6 check, which way? Options: a) fix it in our code under a TR; b) Cilium on k3s
  (`--flannel-backend=none --disable-network-policy --disable=traefik`, about 0.4 GiB more memory). Recommendation:
  a) when the gap is one header or a timing; b) if route status or the header filters fail.
- Q3. The site domain for the MVP. Answered: a), the zone `roaa15.xyz`, previews public at
  `<subdomain>-preview.roaa15.xyz` with the project's token (decision `0016` section 10).
- Q4. Where do catalog backups go? Options: a) S3 in the same region; b) DigitalOcean Spaces. Recommendation: a),
  since egress from EC2 to S3 in the region is cheapest.
- Q5. Does the catalog database live on the k3s EC2 (G14)? Options: a) yes, beside the sites; b) on DOKS (not
  designed: every page view would cross clouds). Recommendation: a).
- Q6. Project's contract: does the generator always read the six `PG*` variables and never set a host? The Project
  service answers. Recommendation: ask Project; the platform already supplies only the Secret.
- Q7. The DOKS region, to pick the EC2 region (G17). Recommendation: the same metro.
- Q8. Keep k3d as a second local engine after the spike? Options: a) keep G3, G4 and the engine switch; b) keep only
  Kind. Recommendation: a) if step 1 passes, since k3d needs less memory than Kind.
