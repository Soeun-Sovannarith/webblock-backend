# Prototype stack runbook (decision 0014)

Scope: the local stack the Python prototype runs against: a Kind cluster without Crossplane, HTTPS on the shared Gateway
with a wildcard certificate from a local CA, the two Provisioning cluster identities, the admission policies and the
tenant network policy, the shared offline page, a local Jenkins that builds with BuildKit, a local Git server for test
and demo sources, and Loki with a log shipper for the Provisioning processes' logs. It is test infrastructure on this
machine only. It stands in for DigitalOcean Kubernetes and for the owner's Jenkins server, and proves nothing about
either.

The real-cluster rule of `.claude/CLAUDE.md` section 0 applies unchanged: every `kind`, `k3d` and `kubectl` call goes
through `deploy/kind/proto-lib.sh`, which uses only the kubeconfigs in `bin/kind-state/proto/` and refuses any file
whose current context is not the recorded engine's `kind-webblock-proto` or `k3d-webblock-proto` on
`https://127.0.0.1:<port>`. Nothing reads `~/.kube` or `.env`. The k3d engine is a spike (see The k3d engine).

## Commands

Run from the module root. `make kind-tools` must have installed `bin/kind`, `bin/k3d`, `bin/kubectl`, `bin/helm` and
`bin/crane` once.

| Command | What it does | Time (measured) |
| --- | --- | --- |
| `make proto-up` | Brings up everything below from nothing, or continues on a kept stack made by the same pins. On failure it removes everything unless `KEEP_CLUSTER=1`. | 91 s with warm caches; 150 s on the first run, which pulls the images |
| `make proto-smoke` | End-to-end check of the stack alone, no Provisioning code (below), including HTTPS through the Gateway with the local CA. | 10 to 18 s |
| `make proto-build-checks` | The Jenkins job's failure reports, concurrent builds and the never-ready variant. | 10 to 13 s |
| `make proto-tokens` | Refreshes the worker and bootstrap tokens (24 hours each). | a few seconds |
| `make proto-policy` | (Re)installs the admission policies and the tenant network policy and waits until they are enforced; `proto-up` runs it too, after the catalog (see Admission policies and Tenant network policy). | 15 to 20 s |
| `make proto-logs Q='<LogQL>' [SINCE=1h] [LIMIT=1000]` | Queries the stack's Loki and prints one JSON log line per line, oldest first (see Logs and Loki). | under a second |
| `make proto-test-kind` | Builds the site and never-ready images through the stack's Jenkins under one fresh project ID, exports `WEBBLOCK_KIND_SITE_IMAGE`, `WEBBLOCK_KIND_NEVER_READY_IMAGE` and `WEBBLOCK_KIND_GATEWAY_URL`, and runs `pytest -m kind` on `tests/integration/kubernetes`, `tests/integration/policy` and `tests/integration/stack`. Fails, never skips, when the stack is down. Never run it while `proto-run` or `proto-e2e` uses the stack's Jenkins heavily. | 5 min |
| `make proto-down` | Removes every container (Loki and the shipper included), the cluster, the network and the stack's credentials; keeps the caches, the local CA and the Provisioning log files (Kept between runs). | 3 to 5 s |

`proto-up` refuses to start when another Kind or k3d cluster exists, and needs `KIND_MIN_AVAILABLE_MB` (default 7000) of
available memory before it creates the cluster. Never run it while a Testcontainers suite or another Kind cluster runs
on the machine.

**After Phase B (decision `0022`) recreate the stack.** The queue and exchange names changed, and a running broker keeps
the old queue arguments. Run `make proto-down; make proto-up`; a kept stack is not migrated. The prototype's run script
then calls the simulator command `declare-producers`, which declares `project.events`, `payment.events` and Payment's
usage queue as Project and Payment would. The worker and the build worker wait until those exchanges exist. Tracing is
off by default (`OTEL_EXPORTER_OTLP_ENDPOINT` is `none` in the generated environment files). To send traces, set
`OTEL_EXPORTER_OTLP_ENDPOINT` to an OTLP/HTTP collector such as `http://127.0.0.1:4318` (the service adds `/v1/traces`)
and restart the processes; `OTEL_SERVICE_NAME` is optional and has the form `provisioning-<process>`. The stack ships no
collector.

To run the Provisioning service itself as pods of the stack instead of host processes, see
[prototype-in-cluster.md](prototype-in-cluster.md) (`make proto-run PROV_MODE=cluster`, task PK). `make proto-down`
stops Provisioning first, in either mode.

`proto-up` needs a checkout of the Project service for the catalog schema: `PROJECT_SERVICE_DIR` (default
`../project-service`) must contain its catalog migrations (see Catalog; decision 0017 section 1). A stack made before
decision 0017 must be recreated: `make proto-down proto-up`.

The first run on a machine downloads the Cilium and cert-manager charts and the Gateway API CRDs (checksum-verified),
the Cilium and cert-manager images (copied by digest into the local registry), the Jenkins, BuildKit, nginx, registry
and Flyway images from Docker Hub, and 32 Jenkins plugins from `updates.jenkins.io` while building the Jenkins image. If
the Phase 4 stack ever ran here, its registry storage seeds the prototype registry. Later runs reuse these caches. A
stack made before task T1 (Gateway API v1.4.1) is refused by `proto-up`: run `make proto-down` first.

## What it builds

| Component | Pin | Where |
| --- | --- | --- |
| Kind cluster `webblock-proto` | `kindest/node:v1.36.4` by digest, one control plane and one worker, no kube-proxy | `deploy/kind/cluster.yaml`, `deploy/kind/versions.sh` (shared with Phase 4, unchanged) |
| Gateway API CRDs | v1.6.1 standard channel, SHA-256 checked. Cilium 1.20 requires v1.6.1 at a minimum. The CRDs include `ListenerSet`, which nothing uses since custom domains were removed (decision 0014 section 23) | `deploy/prototype/versions.sh` |
| Cilium | chart 1.20.2, its images mirrored by digest into the local registry; Gateway API, LB-IPAM, L2 announcements | `deploy/kind/values/` (shared with Phase 4), pins in `deploy/prototype/versions.sh` |
| Shared Gateway | `webblock-gateway/webblock-public`: HTTP on port 80 and HTTPS on port 443 (wildcard certificate `sites-wildcard-tls`) for `*.sites.webblock.test`; routes only from namespaces labelled `websites.webblock.internal/tenant=true`; no ListenerSets (no `allowedListeners`) | `deploy/prototype/gateway/` |
| cert-manager | v1.21.2 (chart and images by digest, from the local registry), Gateway API shim off, no startupapicheck job | `deploy/prototype/cert-manager/`, `deploy/kind/proto-certs.sh` |
| Local CA | `ClusterIssuer webblock-local-ca`, issuer of the platform wildcard certificate, stand-in for the production CA (OD-58); EC P-256, 365 days, name-constrained to `.test` | `deploy/prototype/cert-manager/local-ca-issuer.yaml`, key in `bin/kind-state/proto/local-ca/` |
| Admission policies | `webblock-bootstrap-scope`, `webblock-tenant-writer`, `webblock-site-binding`, `-fence`, `-image`, `-route-shape`, `-routed-release`; parameters in the ConfigMap `webblock/webblock-site-policy` | `deploy/prototype/policy/`, `deploy/kind/proto-policy.sh` |
| Tenant network policy | `CiliumClusterwideNetworkPolicy webblock-tenant-network` | `deploy/prototype/policy/tenant-network.yaml.tmpl` |
| Loki and its log shipper | containers `webblock-loki` (`grafana/loki:3.7.8` by digest, single binary, filesystem storage) and `webblock-log-shipper` (`fluent/fluent-bit:5.1.3` by digest) on the kind network | `deploy/prototype/loki/`, `deploy/kind/proto-loki.sh` |
| Local registry | container `webblock-proto-registry`, `registry:3` by digest, TLS with a project-local CA | `deploy/kind/proto-up.sh` |
| Offline page | `webblock-offline/offline-page`: unprivileged nginx (`nginx-unprivileged:1.31.5-alpine` by digest, from the local registry) that answers 503 "Site offline" with `Cache-Control: no-store` for every path | `deploy/prototype/offline/` |
| Prototype RBAC | namespaces, ServiceAccounts, ClusterRoles below | `deploy/prototype/rbac/` |
| Jenkins | container `webblock-jenkins`, image `webblock-proto-jenkins:local` built from `jenkins/jenkins:2.568.3-lts-jdk21` by digest, 32 pinned plugins, configuration as code, job `webblock-site-build` | `deploy/prototype/jenkins/` |
| BuildKit | container `webblock-buildkit`, `moby/buildkit:v0.33.1` by digest, privileged, mutual TLS on port 1234 | `deploy/prototype/buildkit/`, `deploy/kind/proto-ci.sh` |
| Git server | container `webblock-git`, unprivileged nginx serving bare repositories read-only (Git "dumb" HTTP) | `deploy/prototype/git/` |

All pins are in `deploy/prototype/versions.sh` and `deploy/kind/versions.sh` (the latter holds the tools, node image and
registry image the prototype uses). The Phase 4 stack (Cilium 1.19.8, Gateway API v1.4.1, Crossplane, the composition
function, the XRDs, and the admission policies and RBAC of `deploy/policy` and `deploy/rbac`, written for Crossplane
XRs) was never installed by the prototype (`0014` section 5) and is now only on branch `go-reference` (`0014` section
18), with its scripts (`up.sh`, `down.sh`, `smoke.sh`, `prove-stack.sh`) and `make kind-up`. The prototype's own
policies are in `deploy/prototype/policy` (below). `deploy/kind/lib.sh`, `cluster.yaml`, `values/cilium*.yaml` and
`manifests/lb-ipam.yaml.tmpl` stay, because the prototype's scripts use them.

`proto-up` runs, after the catalog database: `proto-policy.sh` (step 8c; the network policy names the catalog's address)
and `proto-loki.sh` (step 8d), each idempotent and each proven before `proto-up` goes on (probes for the policies, a
line found in Loki).

## Addresses and credentials

Everything lives in `bin/kind-state/proto/` (git-ignored, folder mode 0700). `stack.env` there lists the non-secret
addresses of the running stack.

| What | Where or value | Used by |
| --- | --- | --- |
| Admin kubeconfig | `bin/kind-state/proto/kubeconfig` (Kind's own) | the scripts only; never the service |
| Worker kubeconfig | `bin/kind-state/proto/worker.kubeconfig`, ServiceAccount `webblock/provisioning-worker`, token valid 24 hours | Python `worker` host |
| Bootstrap kubeconfig | `bin/kind-state/proto/bootstrap.kubeconfig`, ServiceAccount `webblock/tenant-bootstrap`, token valid 24 hours | Python `bootstrap` host |
| Gateway address | `PROTO_GATEWAY_ADDRESS` in `stack.env` (an address of the Kind Docker network, reachable from the host); send `Host: <subdomain>.sites.webblock.test` | the worker's site probe, demos |
| Gateway over HTTPS | `PROTO_GATEWAY_HTTPS_URL` in `stack.env` (`https://<PROTO_GATEWAY_ADDRESS>:443`); send the site's host name by SNI, for example `curl --resolve <host>:443:<address> --cacert <PROTO_LOCAL_CA> https://<host>/` | the worker's probe, demos |
| Local CA certificate | `bin/kind-state/proto/local-ca.crt` (`PROTO_LOCAL_CA`); trust it for HTTPS to the stack (it is valid for `.test` names only). Issuer name `PROTO_CLUSTER_ISSUER=webblock-local-ca` | clients, the worker's probe, a browser for the demo |
| Loki | `PROTO_LOKI_URL` in `stack.env` (`http://127.0.0.1:3100`, host only; `webblock-loki:3100` on the kind network); no authentication | `make proto-logs`, the live tests |
| Provisioning log folder | `PROTO_PROVISIONING_LOGS` in `stack.env` (`bin/kind-state/proto/provisioning/logs`): `run-provisioning.sh` writes one run folder per start there, and the shipper tails it | `proto-run`, the shipper |
| Local CA key | `bin/kind-state/proto/local-ca/ca.key` (mode 0600, folder 0700) and the Secret `cert-manager/webblock-local-ca`; never print or copy it | `proto-certs.sh` only |
| Image registry for builds | `webblock-proto-registry.test:5000` (`PROTO_IMAGE_REGISTRY`); a build's image repository is `<registry>/webblock/sites/<projectId>`, and the nodes pull `<repository>@sha256:<digest>` from it | Provisioning configuration, Jenkins |
| Registry from the host | `https://127.0.0.1:5001`, CA `bin/kind-state/proto/registry-tls/ca.crt` | `crane`, checks |
| Jenkins | `http://127.0.0.1:8090` (host only), job `webblock-site-build` | the Python `build_worker` |
| Jenkins API token | `bin/kind-state/proto/jenkins.env` (`JENKINS_URL`, `JENKINS_USER=provisioning`, `JENKINS_API_TOKEN`), mode 0600, created at each new Jenkins container | the Python `build_worker` |
| Jenkins admin login | user `admin`, password in `bin/kind-state/proto/jenkins-secrets/WEBBLOCK_ADMIN_PASSWORD` | a person, in the browser |
| Git repositories | `http://webblock-git:8080/<name>.git`, reachable from Jenkins only (the kind network) | `REPOSITORY_URL` of a build |

Neither the scripts nor Jenkins print a password or token. `jenkins.env` is a plain key-value file; load it into the
build worker's environment, never into a log.

## Identities

- `webblock-tenant-worker` (ClusterRole, bound only per tenant namespace by bootstrap): Deployments and Services
  get/list/watch/create/patch/delete (delete for cleanup of superseded, rolled-back and failed releases, decision 0014
  section 21 ruling 8; the routed release is protected by admission); HTTPRoutes get/list/watch/create/patch, no delete
  (ruling K9); EndpointSlices and Pods get/list. No Secrets, no ListenerSets and no Certificates (custom domains were
  removed, section 23). The worker has no access in a tenant before its RoleBinding exists.
- `webblock-worker-namespaces` (ClusterRole and ClusterRoleBinding to the worker): `list` on namespaces, cluster-wide,
  for the reconciler (ruling 9). RBAC cannot narrow a list by label, so the worker sees every namespace's name and
  labels; it has no get, watch or write on them and nothing else outside tenant namespaces.
- `webblock-tenant-bootstrap` (ClusterRole, bound cluster-wide to `tenant-bootstrap`): Namespaces, ResourceQuotas,
  LimitRanges and RoleBindings create/get/patch; `bind` on `webblock-tenant-worker` only, so it can grant no other role
  it does not itself hold. A Role in `webblock-offline` lets it create/get/patch ReferenceGrants there. It may
  get/create/patch Secrets named `catalog-runtime` (access_setup, see Catalog), and list none. It deletes nothing.
- The ClusterRoleBinding gives bootstrap these verbs in every namespace; `webblock-bootstrap-scope` narrows what it may
  write to the tenant objects. The worker's writes are checked by the per-site policies, and `webblock-tenant-writer`
  keeps every other identity (administrators and the Kubernetes controllers excepted) from writing per-site or tenant
  objects (see Admission policies).

## Certificates

Tasks T1 and W6-K (decision 0014 sections 16 and 23). The platform provides no custom domains: every site is served only
on its platform host `<subdomain>.sites.webblock.test`, the sub-domain from `project_hosting.subdomain` (decision `0023`
G18), and the promotion probe's `<subdomain>-preview.sites.webblock.test` (decision `0023` G18b). There is one
certificate, the Gateway's wildcard for `*.sites.webblock.test`, which covers both, one label deep: the listener
`https-sites` on port 443 terminates TLS with `webblock-gateway/sites-wildcard` (Secret `sites-wildcard-tls`), issued at
`proto-up` by `ClusterIssuer webblock-local-ca`. A route that names the Gateway without a `sectionName` is served on
both `http-sites` and `https-sites`; nothing per project is needed, and Provisioning writes no Certificate.

The Gateway has no `allowedListeners`, so it accepts no `ListenerSet` from any namespace, and the worker has no grant on
ListenerSets or Certificates. Task T1's ListenerSet support and task T2's per-domain certificates can be restored from
commit `c5796ec` if custom domains return.

**The local CA.** `proto-up` makes it once (before the cluster), in `bin/kind-state/proto/local-ca/`: EC P-256, valid
365 days, `CA:TRUE` with path length 0, and a critical name constraint that permits DNS names under `.test` only. The
key never leaves that folder except into the Secret `cert-manager/webblock-local-ca`. `proto-down` keeps the CA, so a
browser that imported `local-ca.crt` for a demo keeps trusting the stack; it is replaced (and `proto-up` refuses to run
on a cluster made with the old one) when it has fewer than 30 days left. To start over: `make proto-down`, then
`rm -rf bin/kind-state/proto/local-ca bin/kind-state/proto/local-ca.crt`.

`deploy/kind/proto-certs.sh` (step 4b of `proto-up`; idempotent) installs cert-manager after rendering its chart and
checking every image against the pins, writes the CA Secret from the files through a pipe, applies the ClusterIssuer
(retrying until the webhook admits it), waits until it is `Ready`, and proves it: a throwaway Certificate is issued and
must verify with `openssl verify -CAfile local-ca.crt` for its host name. cert-manager's Gateway API shim is off;
nothing needs it.

**Moving to the production CA (OD-58).** Decision 0014 section 23 item 2: production gets the wildcard from a real CA
through the cluster setup, not through Provisioning, which holds no issuer setting at all now. The change is
configuration of the cluster only: a `ClusterIssuer` for the production CA (ACME with DNS-01 at infrastructure's DNS
provider, its credentials in cert-manager's namespace), the wildcard Certificate's `issuerRef.name` pointing at it and
its `dnsNames` at the production site domain (`roaa15.xyz` for the MVP, decision 0016 section 10), and the
Gateway's listener hostnames. No Provisioning code, policy or
tenant object changes. If the production CA is not publicly trusted, the worker's probe needs its root, as it needs
`local-ca.crt` here.

By hand, through the project-local kubeconfig only:

```sh
bin/kubectl --kubeconfig bin/kind-state/proto/kubeconfig --context kind-webblock-proto \
  --cache-dir bin/kind-state/proto/kube-cache get clusterissuer,certificate -A
. bin/kind-state/proto/stack.env   # non-secret addresses only
curl --resolve "<subdomain>.sites.webblock.test:443:$PROTO_GATEWAY_ADDRESS" --cacert "$PROTO_LOCAL_CA" \
  "https://<subdomain>.sites.webblock.test/"
```

**Live tests** (`tests/integration/stack/test_gateway_https_live.py`, marker `kind`): both Gateway listeners programmed
and no ListenerSet allowed; a platform subdomain over HTTPS (verified by the local CA alone) and HTTP, through the
renderer's `route-<projectId>` applied by the worker's token, with `X-Webblock-Release` naming the release, then
`offline` when the route moves to the offline page, then the release again; the wildcard certificate refused without the
local CA (curl exit 60).

## Bootstrap admission policy

`deploy/prototype/policy/bootstrap-scope.yaml`: a ValidatingAdmissionPolicy `webblock-bootstrap-scope` and its binding
(`validationActions: [Deny]`, `failurePolicy: Fail`), from decision `0014` section 17 item 3 and review finding M3. Its
one match condition is the username `system:serviceaccount:webblock:tenant-bootstrap`; it matches every resource,
subresource and operation of that user. `deploy/kind/proto-policy.sh` (`make proto-policy`, and step 8c of `proto-up`)
applies it by server-side apply and waits until the API server has type-checked it (no warnings) and two server-side dry
runs as the bootstrap user hold three times in a row: an out-of-scope namespace is refused by the policy, and a tenant
namespace as bootstrap renders it is admitted (a policy whose expression errors would refuse everything, bootstrap
included).

What it allows that identity, and nothing else:

| Object | Allowed |
| --- | --- |
| Namespace | named `wb-<projectId>` (lowercase UUID), labelled `websites.webblock.internal/tenant=true`, `websites.webblock.internal/project-id=<projectId>` (equal to the name's UUID), `websites.webblock.internal/subdomain=<subdomain>` (one DNS label from `project_hosting.subdomain` of at most 55 characters, not ending in `-preview` (FR-55), added once, never changed or removed; decision `0023` G18a) and `pod-security.kubernetes.io/enforce=restricted` |
| ResourceQuota, LimitRange | only inside a `wb-<projectId>` namespace |
| RoleBinding | only inside a `wb-<projectId>` namespace, `roleRef` the ClusterRole `webblock-tenant-worker`, exactly one subject: ServiceAccount `webblock/provisioning-worker` |
| Secret | only inside a `wb-<projectId>` namespace, named `catalog-runtime`, `type: Opaque` |
| ReferenceGrant | only in `webblock-offline`, named `wb-<projectId>`, from HTTPRoutes in that same namespace to the Service `offline-page` |

Refused: any DELETE or CONNECT, any subresource (for example `serviceaccounts/token`), any other resource, and any
create or update outside the table, including updates of existing objects such as `kube-system`'s labels. Each refusal
is HTTP 403 with a fixed message that starts with `webblock-bootstrap-scope:` and contains no value from the request.
Reads are not admission requests; RBAC limits them (bootstrap can list no Secrets).

How to test it (the stack must be up):

```sh
make proto-policy                                    # idempotent; prints "policy: ... enforced"
.venv/bin/python -m pytest -m kind tests/integration/policy   # or make proto-test-kind for both live suites
```

`tests/integration/policy` makes each out-of-scope request as a server-side dry run twice: with the bootstrap token
(`bootstrap.kubeconfig`; refused by RBAC or the policy) and as Kind's admin impersonating the bootstrap user with the
group `system:masters` (RBAC allows everything, so only the policy can refuse, and the test requires its name in the
refusal). It checks that each object the bootstrap renderer writes passes the policy alone, and runs the real
`KubernetesTenantBootstrap` (tenant and `catalog-runtime`) for a fresh project, twice, the second run changing no
resourceVersion. `tests/unit/deploy/test_bootstrap_scope_policy.py` checks the file without a cluster. A one-off check
by hand, always through the project-local kubeconfig:

```sh
bin/kubectl --kubeconfig bin/kind-state/proto/kubeconfig --context kind-webblock-proto \
  --cache-dir bin/kind-state/proto/kube-cache \
  --as=system:serviceaccount:webblock:tenant-bootstrap \
  label namespace kube-system pod-security.kubernetes.io/enforce=privileged --dry-run=server
# Error from server (Forbidden): ... ValidatingAdmissionPolicy 'webblock-bootstrap-scope' ... denied request
```

The reach that remains:

- The policy limits the bootstrap identity only; `webblock-tenant-writer` (Admission policies, below) keeps other
  identities from writing tenant namespaces, RoleBindings and `catalog-runtime`. Cluster administrators, Kind's admin
  kubeconfig and any identity in `system:masters` are not limited; they can also delete the policies or their bindings
  (break-glass, as in Phase 4's `deploy/policy`, now on branch `go-reference`).
- Inside a tenant namespace bootstrap can still change the quota and limits, add or change labels other than the four
  checked (for example lower `enforce-version`, or `audit` and `warn`), and overwrite `catalog-runtime`'s values. It
  cannot run a pod: it holds no workload verbs, and the only role it may bind is the worker's, to the worker only.
- It can create any number of `wb-<uuid>` namespaces for project IDs that do not exist; nothing ties a namespace to a
  known project.
- The worker's own reach (`webblock-tenant-worker` in each tenant namespace) is limited by RBAC, Pod Security
  `restricted` and the per-site policies below, not by this policy.

The tenant objects bootstrap creates, and the release and route objects the worker applies, are written out in
`deploy/prototype/manifests/` (`tenant.yaml`: namespace with its labels, quota, limits, ReferenceGrant, RoleBinding and
a placeholder `catalog-runtime` Secret; `release.yaml`: Deployment and Service `rel-<releaseId>`; `route-release.yaml`
and `route-offline.yaml`: HTTPRoute `route-<projectId>` to a release or to the offline page; `preview.yaml`: HTTPRoute
`preview-<projectId>`). They are generated from the worker's renderer by `scripts/prototype/render-examples.py` with
fixed example values (decision 0014 section 20) and never edited by hand: `tests/unit/test_manifest_examples_drift.py`
fails when they differ from the renderer. `proto-smoke` runs the same script for its own project, release and image and
applies the result, so it needs the project's `.venv` (`make proto-smoke` runs `py-sync`). The Deployment's readiness
probe is `GET /healthz` (intent digest version 4), and it mounts a writable `/tmp` under the read-only root file system.

## Admission policies

Task W6-K (decision 0014 section 21 ruling 1, as amended by section 23; TR-35). Every policy is a
ValidatingAdmissionPolicy with a binding of the same name, `failurePolicy: Fail` and `validationActions: [Deny]`, in
`deploy/prototype/policy/`. Every refusal is HTTP 403 with a fixed message that names the policy and contains no value
from the request. `make proto-policy` (and step 8c of `proto-up`) applies them, waits until each is type-checked at its
generation without a warning, and repeats server-side dry runs until they hold three times in a row: bootstrap's
out-of-scope namespace refused and its tenant namespace admitted; in a throwaway tenant namespace, a worker Service
without the fence refused by `webblock-site-fence`, the same Service with the fence admitted, and bootstrap's
`catalog-runtime` admitted (`webblock-tenant-writer` evaluates without error).

The per-site policies match only the release worker, `system:serviceaccount:webblock:provisioning-worker`, and only in
namespaces labelled `websites.webblock.internal/tenant=true`. The project ID they compare with is the namespace's label
`websites.webblock.internal/project-id`, which only bootstrap writes.

| Policy | The worker's write must |
| --- | --- |
| `webblock-site-binding` | (Deployment, Service, HTTPRoute) carry the label `project-id` of the namespace's project; a Deployment's pod template too; a Deployment or Service be named `rel-<releaseId>` after its `release-id` label |
| `webblock-site-fence` | (Deployment, Service, HTTPRoute) carry `websites.webblock.internal/command-number`, an integer from 1 to 2^53-1 with no sign or leading zero |
| `webblock-site-image` | (Deployment) run in every container and init container exactly `<imageRepositoryPrefix><projectId>@sha256:<64 hex>` |
| `webblock-site-route-shape` | (HTTPRoute) be named `route-`, `preview-` or `redirect-<projectId>`; attach only to `webblock-gateway/webblock-public`; when attached, name hostnames, and only `<subdomain>.<siteDomain>` (`<subdomain>-preview.<siteDomain>` for the preview route, decision `0023` G18b), the sub-domain from the namespace label `websites.webblock.internal/subdomain` that the bootstrap writes (decision `0023` G18a); send traffic only to `rel-*` Services of its own namespace or to `webblock-offline/offline-page`, with no per-backend filter; use only the `ResponseHeaderModifier` and `RequestRedirect` filters, a redirect only to the project's own hostname; on the preview route, only one `ResponseHeaderModifier` that sets exactly `X-Webblock-Release` and `Cache-Control: private, no-store` (FR-52.1), required on every rule, and the removal of `X-Webblock-Preview` |
| `webblock-site-routed-release` | (DELETE of a Deployment or Service) not name the release that `route-<projectId>` sends traffic to |

**Parameters.** `imageRepositoryPrefix` and `siteDomain` are in the ConfigMap `webblock/webblock-site-policy`, which
`proto-policy.sh` writes from `site-params.yaml.tmpl` (`webblock-proto-registry.test:5000/webblock/sites/` and
`sites.webblock.test` here). Production's prefix is `docker.io/<org>/site-` (decision 0016): a move of registry or
domain is that ConfigMap. Without the ConfigMap the image and route policies refuse every worker write
(`parameterNotFoundAction: Deny`).

**The routed-release guard.** A ValidatingAdmissionPolicy cannot look up an arbitrary object, but its parameters can be
objects of any kind: `webblock-site-routed-release` takes `HTTPRoute` as its `paramKind`, and its binding's `paramRef`
has no namespace and an empty selector, so the API server evaluates it against each HTTPRoute in the namespace of the
request. A worker DELETE of `rel-<releaseId>` is refused while `route-<projectId>` has a backendRef to that Service. The
preview and redirect routes are not consulted (the preview route keeps naming the last candidate, which may be a failed
release cleanup must remove). Limits: the API server reads the routes from an informer cache that can lag a route change
by a moment (right after the route moves away from release A, the delete of A can still be refused for a moment; the
worker retries); it checks the route's spec, not what Envoy serves at that instant; it limits the worker only, not the
namespace controller, the garbage collector or administrators.

**Who writes in a tenant** (`webblock-tenant-writer`, the other half of `webblock-bootstrap-scope`):

| Object in a tenant namespace | Only |
| --- | --- |
| Deployments, Services, HTTPRoutes | the release worker |
| the tenant Namespace itself (named `wb-*` or labelled `tenant`, before or after the change), RoleBindings | the bootstrap identity |
| Secret `catalog-runtime` | the bootstrap identity |
| any other Secret | the bootstrap identity or cert-manager's controller |

Not matched: the groups `system:masters` and `kubeadm:cluster-admins` (Kind's admin kubeconfig; break-glass, and the
tests' scaffolding), ServiceAccounts in `kube-system` and `system:kube-controller-manager` (the Deployment controller
updates a Deployment's revision annotation, the garbage collector removes finalizers, the namespace controller deletes a
tenant's objects with its namespace), and status subresources (the Gateway, cert-manager and the controllers write
status). Which administrator identity DOKS uses is not known; the break-glass groups must be checked there. An ACME
HTTP-01 solver would create objects in the tenant as cert-manager and would be refused; the platform has no ACME solver
in tenants.

**Tests** (`tests/integration/stack/test_site_policies_live.py`, marker `kind`): every policy installed, type-checked
and denying; the renderer's own output (`render.py`, called by the test: a release's Deployment and Service, the route
to the release and to the offline page, the preview route, the detached redirect route) admitted for the worker; 30
planted bad objects, each refused for the worker (Kind's admin impersonating it, a server-side dry run) by the named
policy; the routed release's Deployment and Service refused, another release deleted for real by the worker's token, and
the former release deletable once the route points at the offline page; a group bound to `cluster-admin` refused every
tenant write by `webblock-tenant-writer` and admitted in a plain namespace; the worker with that group still refused its
tenant's namespace and RoleBindings; bootstrap with it refused a Deployment. By hand:

```sh
bin/kubectl --kubeconfig bin/kind-state/proto/kubeconfig --context kind-webblock-proto \
  --cache-dir bin/kind-state/proto/kube-cache get validatingadmissionpolicies,validatingadmissionpolicybindings
```

## Tenant network policy

`CiliumClusterwideNetworkPolicy webblock-tenant-network` (decision 0014 section 21 ruling 1), from
`deploy/prototype/policy/tenant-network.yaml.tmpl`, installed by `proto-policy.sh` with the catalog container's address
on the kind network (step 8c, after the catalog; `proto-policy.sh` waits until Cilium reports it `Valid`). One
cluster-wide policy selects every pod in a namespace labelled `websites.webblock.internal/tenant=true`, so nothing is
written per tenant. Default deny in both directions; a site pod may:

- **receive** from the Gateway's Envoy (Cilium's reserved identity `ingress`) and from its node (`host`: the kubelet's
  probes); nothing from any pod;
- **send** to the catalog database (its address, TCP 5432), to cluster DNS (kube-dns, port 53 UDP and TCP), and through
  the Gateway: to Envoy (`ingress`) and to site pods on TCP 8080. The last is needed because Cilium's Envoy checks the
  client's egress policy towards the final backend pod (measured: without it Envoy answers 403 to a tenant pod calling
  its own site through the Gateway). It opens no direct pod-to-pod path: the destination's ingress admits only Envoy and
  the kubelet.

Refused, among others: other tenants' pods, the site's own pods directly, the API server (its Service address and the
control-plane node), `169.254.169.254` (also in `egressDeny`), the image registry, Jenkins, BuildKit and the Git server.
In production the catalog rule selects the in-cluster CloudNativePG pods by label instead of an address (decision 0016).

**Tests** (`tests/integration/stack/test_tenant_network_live.py`): from a probe pod in tenant A, the catalog and A's own
site through the Gateway answer; tenant B's site pod, A's own site pod directly, the API server (Service address and
node), the metadata address and the registry do not, and for each refusal Cilium's drop monitor on the probe's node
(`cilium-dbg monitor --type drop`) reports a `Policy denied` drop towards that destination, so a destination that is
merely absent (nothing listens at 169.254.169.254 on Kind) cannot pass. The readiness of every test site (kubelet
probes) and the Gateway tests show the allowed ingress.

## Logs and Loki

Decision 0014 section 23 item 5. The Provisioning processes run on the host (`make proto-run`,
`scripts/prototype/run-provisioning.sh`) and write JSON log lines to
`bin/kind-state/proto/provisioning/logs/<UTC timestamp>/<process>.log`, one run folder per start (`logs/latest` links to
the newest). `proto-loki.sh` (step 8d of `proto-up`; idempotent) starts:

- `webblock-loki`: Loki single binary (`deploy/prototype/loki/loki.yaml`), filesystem storage in
  `bin/kind-state/proto/loki-data`, no authentication, on the kind network and on `127.0.0.1:3100` of the host
  (`PROTO_LOKI_URL`); memory limit 384 MB;
- `webblock-log-shipper`: Fluent Bit (`deploy/prototype/loki/fluent-bit.yaml`) with the log folder mounted read-only at
  `/logs` (its read positions in `bin/kind-state/proto/log-shipper`). It tails `/logs/*/*.log` except `logs/latest`
  (which would read each line twice), parses each line as JSON, and pushes it unchanged to Loki in the stream
  `{job="webblock-provisioning", process="<process>", run="<UTC timestamp>"}`. A line that is not JSON arrives as
  `{"log": "<line>"}`; the entry's timestamp is when the shipper read it, the line's own `time` stays in the JSON.
  Memory limit 96 MB.

Both run as the invoking user with no capability, a read-only root file system and a memory limit. `proto-loki.sh`
proves the path: it writes a probe line into `logs/proto-up-probe/stack.log` and waits until Loki returns it.
`proto-smoke` does the same with an `audit=true` line. A shipper container on the kind network was chosen over a Kind
`extraMount`: the processes are on the host, not in the cluster, and a container needs no change to the cluster.

**Querying.** `make proto-logs Q='<LogQL>' [SINCE=1h] [LIMIT=1000]`, or
`bash deploy/kind/proto-loki-query.sh [--since 1h] [--limit N] '<LogQL>'`, prints the matching lines, oldest first, one
JSON object per line; exit 0 with no output when nothing matches, non-zero when Loki refuses the query or does not
answer. Audit events:

```sh
make proto-logs Q='{job="webblock-provisioning"} | json | audit="true"'
make proto-logs Q='{job="webblock-provisioning", process="worker"} | json | audit="true" | project_id="<id>"' SINCE=10m
```

Loki's HTTP API is at `$PROTO_LOKI_URL/loki/api/v1/query_range` for tests in other languages or folders. **Tests**
(`tests/integration/stack/test_loki_live.py`): Loki ready; an audit line and a plain line written to a run folder of
their own both arrive with the three labels, the audit filter returns only the audit one, through the API and through
the query script; a malformed query fails the script.

## The build job

`webblock-site-build` takes `PROJECT_ID`, `RELEASE_ID`, `ATTEMPT`, `REPOSITORY_URL`, `COMMIT_SHA` and
`IMAGE_REPOSITORY`, triggered with `POST /job/webblock-site-build/buildWithParameters` and the API token (no crumb
needed). The answer's `Location` is the queue item; its `executable.url` is the build URL to store in `log_ref`. The
pipeline:

1. validates the parameters; `IMAGE_REPOSITORY` must be exactly `<WEBBLOCK_REGISTRY>/webblock/sites/<PROJECT_ID>`
   (`invalid_parameters` otherwise);
2. clones the repository (`clone_failed`);
3. checks out exactly `COMMIT_SHA` and compares `git rev-parse HEAD` (`commit_not_found`);
4. builds with BuildKit over mutual TLS, into the build cache only (`build_failed`);
5. pushes `IMAGE_REPOSITORY:<RELEASE_ID>-<ATTEMPT>` (`push_failed`);
6. archives `result.json`: `{"project_id", "release_id", "attempt", "commit_sha", "image_digest"}`, plus `failure_code`
   when a step failed (then `image_digest` is null, and a value is null when its parameter did not validate). A timeout
   (30 minutes) or abort reports the code of the step it interrupted.

The build result is SUCCESS exactly when `result.json` has no `failure_code`; read `<build URL>artifact/result.json`.
The pipeline is embedded in the job and sandboxed; nothing from the built repository is executed in Jenkins. Jenkins has
two executors, so at most two builds run at once and the rest wait. Jenkins merges identical parameter sets that are
both still queued into one item. A new Jenkins container (after `proto-down`, or when the image changes) starts its
build numbers at 1 again, so a stored `log_ref` from an earlier stack can name a different build.

To read a build's console: `bash scripts/prototype/jenkins-log.sh <number> [lines]`. To trigger and wait from the shell:
`bash scripts/prototype/jenkins-build.sh PROJECT_ID RELEASE_ID ATTEMPT REPOSITORY_URL COMMIT_SHA IMAGE_REPOSITORY`
(prints `result.json`).

## Sample sources

`bash scripts/prototype/sample-repo.sh <name> <variant> [version]` creates or updates
`bin/kind-state/proto/git/<name>.git` with one new commit and prints its full SHA. Variants
(`deploy/prototype/sample-site/`):

- `site`: unprivileged nginx on 8080, `GET /` answers 200 with `sample-site version: <version>`;
- `never-ready`: builds, but the container exits at once (crash loop, never ready);
- `broken-build`: the Dockerfile fails to build.

## Catalog

The shared catalog database of Notion "6 · Shared catalog" (decision 0014 section 14), with Provisioning's
`access_setup`, the Project service's schema and a stand-in for the Project service's runtime writes.

**The database.** `proto-up` starts container `webblock-catalog-db` (`postgres:18.6-alpine3.24` by digest,
`deploy/prototype/catalog/versions.sh`) on the `kind` network: pods reach it as `webblock-catalog-db:5432`
(`PROTO_CATALOG_IN_CLUSTER`), the host on `127.0.0.1:<PROTO_CATALOG_PORT_ON_HOST>`. Authentication is `scram-sha-256`
only, local and TCP (the smoke checks `pg_hba`). There is no TLS, so the Secret says `PGSSLMODE=disable`. The script is
`deploy/kind/proto-catalog.sh`; `proto-down` removes the container and its data.

**Schema: the Project service's catalog migrations (decision 0017 section 1).** The catalog schema belongs to the
Project service. `proto-catalog.sh` reads its checkout from `PROJECT_SERVICE_DIR` (default `../project-service`, the
sibling of this repository) and writes nothing there; it stops with an error naming the path when the directory,
`src/main/resources/db/migration/catalog/V1__catalog_tables.sql` or `scripts/db/catalog-bootstrap-roles.sql` is missing.
To use another checkout, pass its path: `PROJECT_SERVICE_DIR=<path> make proto-up`.

On a new container (step 2), the Project service's `scripts/db/catalog-bootstrap-roles.sql` creates the design's three
roles (`catalog_owner`, `catalog_platform`, `catalog_site`, all `NOLOGIN`), and `deploy/prototype/catalog/bootstrap.sql`
the three logins below, the database `catalog` (owner `catalog_owner`) and `catalog_migrator`'s session role
`catalog_owner` in that database.

Then (step 3, on every `proto-up`) the Flyway CLI container (`flyway/flyway:12.4.0-alpine` by digest,
`PROTO_FLYWAY_IMAGE` in `deploy/prototype/catalog/versions.sh`; pulled once by the local Docker) runs on the `kind`
network against `webblock-catalog-db:5432` with the migrations folder mounted read-only: login `catalog_migrator`,
`-initSql="SET ROLE catalog_owner"`, history in schema `catalog_flyway` (`catalog_flyway.flyway_schema_history`),
`-placeholderReplacement=false`. The password reaches the container only through the environment (`FLYWAY_PASSWORD`),
never the command line. Its output goes to `bin/kind-state/proto/catalog-flyway.log`; on failure the last 20 lines are
printed. The migrations:

- `V1` the tables, enums, constraints and indexes;
- `V2` `catalog.current_website_id()` (`session_user`), the immutability triggers, the product-type trigger and
  `catalog.track_media_bytes()`;
- `V3` forced row-level security, the policies and the grants;
- `V4` `SELECT` on `catalog.websites` and `catalog.business_types` for `catalog_platform`, and `TEMPORARY` revoked from
  `PUBLIC` on the database;
- `R__catalog_reference_data.sql` (repeatable, rerun when it changes) the reference data the demo shop needs: business
  type `clothing_shop`, product types `top`, `outerwear`, `trousers` and `accessory`, each with one active field schema.

Provisioning's former goose copies (`deploy/prototype/catalog/migrations/00001` to `00005`) are deleted; goose applies
only Provisioning's own migrations. **A stack made before this change carries goose's catalog history and must be
recreated**: `proto-catalog.sh` refuses a catalog database that has `public.goose_db_version` and says so; run
`make proto-down proto-up`.

**Logins.** Their passwords are generated into `bin/kind-state/proto/catalog-admin.env` (mode 0600,
`CATALOG_<NAME>_USER` and `CATALOG_<NAME>_PASSWORD`; never print it). Only SCRAM verifiers reach SQL. The superuser's
password is also in the container's environment.

| Login | Member of | Used by |
| --- | --- | --- |
| `postgres` | superuser | `proto-catalog.sh` and the smoke check only |
| `catalog_migrator` | `catalog_owner` (SET only) | Flyway; the stand-in tool's lookup of a business type by code |
| `project_catalog_platform` | `catalog_platform` | the stand-in tool's `register` |
| `provisioning_catalog_admin` | `CREATEROLE`, ADMIN OPTION on `catalog_site`; not a superuser and no member of `catalog_site` | Provisioning's `access_setup` |

**access_setup** (`src/provisioning/infrastructure/catalog/`) is run by the bootstrap component after `ensure_tenant`
has returned `ready`: `CatalogAccessSetup(site_access=PostgresSiteAccess(...), secrets=KubernetesTenantBootstrap(...),
endpoint=RuntimeEndpoint("webblock-catalog-db", 5432, "catalog", "disable")).run(project_id, has_live_release=...)`.
`has_live_release` comes from `project_hosting.live_release_id`.

What it does:

- When the role is absent, it creates `site_<project id without hyphens>`, a login member of `catalog_site` only.
- When the role exists but its Secret is missing and no release is live, it delivers a new password.
- When the role and its Secret both exist, it only verifies the role.
- When the role exists, its Secret is missing and a release is live, it refuses (`RuntimeSecretMissing`, for an
  operator), unless the namespace was created from nothing (next paragraph).

**The catalog-secret mark** (task F2B; decision 0016 section 8 ruling 4, amending 0014 section 24). The namespace
annotation `websites.webblock.internal/catalog-secret` records whether the tenant ever received its Secret:

- `ensure_tenant` creates `wb-<projectId>` with `pending`, in the namespace apply itself, before the worker's
  RoleBinding; later reruns carry the value they read and never change it.
- access_setup sets `delivered` (an apply that names the namespace's `resourceVersion`) before it writes the Secret.
- The bootstrap reads the mark before access_setup. `pending` with the Secret absent means no pod in the namespace ever
  held a password (the old pods went with the deleted namespace), so a new password is delivered although a release is
  live; this holds after any restart of the bootstrap. The rotation is the audit event `catalog_credential_rotated`
  (actor `bootstrap`), and the `access_setup done` log line shows `catalog_mark=pending->delivered`.
- `delivered` with the Secret absent is `RuntimeSecretMissing`, for an operator: the Secret was lost from a namespace
  whose pods may hold the password, or the bootstrap crashed between the mark and the Secret (the safe direction).
- **A namespace without the annotation** (created before task F2B, or by an administrator) counts as "not created from
  nothing", exactly as before: `RuntimeSecretMissing` when its Secret is missing while a release is live. An operator
  who knows the namespace's pods are gone may delete the namespace; the bootstrap then rebuilds it marked `pending`.
- `webblock-bootstrap-scope` lets the bootstrap set `pending` only on creation and move `pending` or none to
  `delivered`, never back, never removed. `webblock-tenant-writer` refuses any change of the mark by any other identity
  (break-glass administrators and the Kubernetes controllers are not matched, as for the rest of that policy). To look:
  `kubectl get namespace wb-<projectId> -o
  jsonpath='{.metadata.annotations.websites\.webblock\.internal/catalog-secret}'`.

It always writes the Secret `catalog-runtime` in `wb-<projectId>` before it sets the password. The Secret is `Opaque`,
with `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` and `PGSSLMODE`, and is written by bootstrap's server-side
apply.

The rendered Deployment takes these values through `envFrom` (intent digest version 3), so a release pod does not start
until the Secret exists. Bootstrap may get, create and patch only Secrets named `catalog-runtime`, and cannot list
Secrets. The worker has no access to Secrets.

**Stand-in tool** (Project and a site's CMS; reads `stack.env`, `catalog-admin.env` and the admin kubeconfig):

```sh
.venv/bin/python -m provisioning.hosts.devtools.catalog_platform register --project <id> --business-type clothing_shop [--name ...]
.venv/bin/python -m provisioning.hosts.devtools.catalog_platform seed --project <id> \
  --file deploy/prototype/demo-sites/clothing-shop/seed/seed.json
```

- `register` inserts the `catalog.websites` row and the first profile. Running it again changes nothing.
- `seed` reads the role's credential from the Secret, so `access_setup` must have run first. It then loads the file as
  the website's own role, through row-level security. It refuses a website that already has categories.

**Live tests.** With the stack up, run `.venv/bin/python -m pytest -m catalog_live tests/integration/catalog`. They
build a small probe image with the local Docker and push it to `localhost:5001` under the test project's repository.
They leave tenants, rows and roles behind, and `proto-down` removes them.

**Gaps in the stand-in:**

- Bootstrap's Secret permission applies in every namespace, because it comes from a ClusterRoleBinding.
- A new password reaches a running pod only when the pod restarts.

## The smoke check

`make proto-smoke` builds a fresh commit through Jenkins and checks `result.json` against the request and the registry;
shows that the worker cannot create a namespace or write to a tenant before its RoleBinding (403), and that bootstrap
can create the tenant but cannot bind `cluster-admin` or delete a namespace (403); applies Deployment, Service and
HTTPRoute with the worker identity, waits for readiness and gets 200 with the version string through the Gateway, over
HTTP and over HTTPS (the wildcard certificate verified against `local-ca.crt` alone, and refused without it, curl exit
60); shows that the worker cannot delete the route (403) nor the release it sends traffic to (refused by
`webblock-site-routed-release`); points the route at the offline page and gets 503 "Site offline"; checks that the
catalog allows `scram-sha-256` only, has its schema and reference data, and that only bootstrap may touch
`catalog-runtime` (no Secret access for the worker, no other name and no list for bootstrap); writes an `audit=true`
line to the Provisioning log folder and finds it in Loki; and deletes the smoke namespace and ReferenceGrant.
`KEEP_SMOKE=1` keeps them.

## Replicas

`make proto-run-replicas` (`PROV_REPLICAS=2`) runs a second API (`127.0.0.1:18090`, ops `18091`),
worker (ops `18092`, `WORKER_ID=prototype-worker-2`) and build worker (ops `18093`) beside the
first ones, and still one bootstrap; `make proto-e2e-replicas` runs `tests/e2e/replicas` against it.
Each replica has its own env, pid and log file (`<process>-2`); `make proto-stop` and
`proto-reset-sites` stop every replica. The default `make proto-run` is unchanged. See
[replicas.md](replicas.md), which also says why a second bootstrap only stands by.

The bootstrap (and `bootstrap-2`) runs under a supervisor in `run-provisioning.sh` (task HF-A):
when it exits non-zero it is started again after a back-off (1 s, doubling up to 30 s), with a JSON
line `"supervisor": "run-provisioning.sh"` in `bootstrap.log`; `stop-process` stops it for good and
`kill-process` kills the supervisor with it. A database stall (for example `docker pause
webblock-provisioning-db`) no longer stops the bootstrap: it logs `bootstrap singleton lock
uncertain` and pauses its rounds, then `confirmed on the same session; its rounds resume`
([replicas.md](replicas.md)).

## The k3d engine

A spike (design `.claude/docs/designs/k3s-site-cluster-spike.md`, step 1): the same stack on k3d (k3s in Docker)
instead of Kind. `make proto-up PROTO_ENGINE=k3d` chooses it; `proto-up` records the engine in
`bin/kind-state/proto/engine` (and `PROTO_ENGINE` in `stack.env`), and every other target reads that record, so
`make proto-down`, `proto-test-kind`, `proto-run` and `proto-e2e` need no setting. A request for the other engine while
one is recorded is refused: run `make proto-down` first. Only one local cluster runs at a time, of either engine.

| Step | Kind | k3d |
| --- | --- | --- |
| Cluster | `kindest/node`, control plane and worker | `rancher/k3s:v1.36.5-k3s1` by digest, one server (tainted `NoSchedule` like Kind's control plane) and one agent, on the Docker network `kind`; API on `127.0.0.1:<free port>` only; `metrics-server` off |
| Kubeconfig | `kind export kubeconfig` | `k3d kubeconfig merge ... --output` into `bin/kind-state/proto/kubeconfig`; k3d runs with `HOME` and `KUBECONFIG` in `bin/kind-state/proto/` and never updates `~/.kube/config` |
| Registry trust | containerd `hosts.toml` on each node | `deploy/kind/k3d/registries.yaml.tmpl` with the registry CA mounted at `/etc/webblock/registry-ca.crt` |
| Gateway API CRDs | the pinned v1.6.1 bundle | k3s's chart `traefik-crd` installs v1.6.1 itself; the pinned bundle is applied only if it installs none, and an older one stops `proto-up` |
| Gateway controller | Cilium 1.20 | k3s's Traefik 3.7.13 with `deploy/kind/k3d/traefik-config.yaml` (Gateway API provider only, GatewayClass `traefik`) |
| Gateway | `deploy/prototype/gateway/20-gateway.yaml`, ports 80 and 443 | `20-gateway-traefik.yaml`, ports 8000 and 8443 (Traefik's entry points; the Service serves them on 80 and 443) |
| Gateway address | Cilium LB-IPAM pool | the Traefik Service's ServiceLB address (a node address on network `kind`) |
| Tenant network policy | `CiliumClusterwideNetworkPolicy` from `proto-policy.sh` | none from `proto-up`; `tests/integration/stack/test_tenant_network_k3s_live.py` applies `tenant-network-k3s.yaml.tmpl` to its own test tenants |

The local CA, the wildcard certificate `*.sites.webblock.test` (which covers `<subdomain>-preview.sites.webblock.test`),
cert-manager, RBAC, the offline page, the tokens, Jenkins, BuildKit, the Git server, the catalog, the admission policies
and Loki are the same on both engines. `make proto-test-kind` runs `test_tenant_network_k3s_live.py` on k3d only and
says so on Kind; the Cilium test `test_tenant_network_live.py` runs on both and fails on k3d by design. `make proto-run
PROV_MODE=cluster` does not run on k3d yet: the chart renders `CiliumNetworkPolicy` objects (design G9). k3s pulls its
own images (Traefik, CoreDNS, ServiceLB) by tag from Docker Hub; the spike accepts that (design section 7).

## Memory

Memory, measured with `docker stats` with `proto-run` running and after `proto-test-kind` and `proto-e2e`:
control-plane node 1.29 GB, worker node 1.0 GB, Jenkins 0.44 GB (heap capped at 512 MB, container at 1.5 GB), Loki 98
MB (limit 384 MB), the shipper 14 MB (limit 96 MB), registry 60 MB, BuildKit 55 MB (capped at 2 GB), catalog 15 MB,
Provisioning's own PostgreSQL 64 MB and RabbitMQ 106 MB. The idle stack uses about 2.6 GB. `proto-up` asks for 7000 MB
available because Cilium's start and image pulls peak higher.

## Kept between runs

`proto-down` keeps, as caches: `registry-data/` and `registry-tls/` (the CA certificate is valid 30 days; a new one is
made when it expires while no cluster exists), `local-ca/` and `local-ca.crt` (the local CA of `webblock-local-ca`,
valid 365 days, renewed when under 30 days remain while no cluster exists), `cache/` (verified downloads), the
Provisioning processes' log files (`provisioning/logs/`; Loki's data and the shipper's read positions are removed, so a
new stack's Loki starts empty and the shipper reads the kept files again from the start) and the Docker image
`webblock-proto-jenkins:local`. To remove them too: `rm -rf bin/kind-state/proto` and
`docker image rm webblock-proto-jenkins:local` after `make proto-down`.

## Known limits

- Local test infrastructure only: no DOKS load balancer, storage or node pools; the wildcard certificate comes from a
  local CA, not a public CA (OD-58). If DOKS runs Cilium 1.20, it needs Gateway API v1.6.1 or later (Cilium 1.20's
  minimum; OD-67).
- No custom domains (decision 0014 section 23); see Certificates.
- The admission policies' break-glass identities (`system:masters`, `kubeadm:cluster-admins`) and the controllers'
  ServiceAccounts are Kind's (kubeadm with `--use-service-account-credentials`); DOKS's must be checked before the
  policies go there.
- Loki has no authentication and no retention beyond what a short-lived stack needs; it listens on `127.0.0.1` and the
  kind network only.
- BuildKit runs privileged and runs the generated sites' Dockerfile steps; containers on the kind network can reach
  Jenkins, the Git server and BuildKit's port (BuildKit requires a client certificate). Site pods cannot (tenant network
  policy); pods in other namespaces still can.
- Any logged-in Jenkins user can do anything (the `provisioning` user should be limited to building one job; that needs
  the matrix-auth plugin).
- The routed-release guard reads routes from the API server's cache, and the fence policy checks the annotation's form,
  not that it never goes down; see Admission policies.

## Using the Project service for access checks

Task PA, decision [`0017`](../decisions/0017-project-service-catalog-and-access.md) section 2 as amended by
[`0019`](../decisions/0019-service-identity-is-the-mesh.md). By default `make proto-run` keeps the access-file stand-in
(`PROJECT_ACCESS_MODE=prototype-file`, `access.json`). To have the API ask the Project service instead, start the
Project service first (its own runbook), then start Provisioning with the mode, the Project service's base URL, and the
platform user ID of each development token that should act as a project owner:

```sh
make proto-run PROJECT_ACCESS_MODE=project-service \
  PROJECT_SERVICE_URL=http://127.0.0.1:<project-service-port> \
  PROTO_TENANT_USER_ID=<the owner's account.users UUID>
```

`PROTO_OPERATOR_USER_ID` and `PROTO_READER_USER_ID` do the same for the other two tokens. A user ID is a canonical
lowercase UUID; anything else stops the start. `make proto-run-replicas` takes the same settings.

There is no token file any more (`0019`: the call carries no token; in the mesh the caller is its mTLS identity). A
`PROJECT_SERVICE_TOKEN_FILE` that is still set in the environment stops `make proto-run` before it starts anything,
naming the variable and never its value, and the API refuses the key by name too.

What the API then does for every operation: `POST {PROJECT_SERVICE_URL}/internal/v1/access-checks` with no
`Authorization` header, `X-Request-Id` (the operation's correlation ID) and `{"userId", "projectId", "action"}`, within
2 seconds. It keeps no cookies and relays no header of the caller. Allowed goes on; a denial (`NOT_FOUND`,
`NOT_MANAGER`, `PROJECT_DELETED`, `PROJECT_SUSPENDED`) answers 404; anything else (the Project service down, slow, or
answering something else) answers 503 `project_access_unavailable`. The API's log names the project, the action and the
reason or cause, never the user.

Limits:

- Kind has no Istio yet, so the Project service's `/internal/**` is open in the prototype: any process that can reach it
  can ask its access check. The mesh's gate exists only on a cluster with Istio (follow-up of the authentication plan).
- A token without a user ID is denied without a call. By default that is the operator's token. Rollback is `ADMIN` only
  (FR-50): the `prototype-admin` token rolls back any project without the call (decision `0019` section 7, OD-45;
  [identity.md](identity.md)), and every other token is refused 403. The operator CLI
  (`.claude/docs/runbooks/operator.md`) is unaffected.
- The end-to-end suite rewrites `access.json` to show refusals, so run `make proto-e2e` and `make proto-demo` with the
  default mode.
- The production profile accepts `PROJECT_ACCESS_MODE=project-service`, with an in-cluster `http` Service URL or an
  `https` one ([identity.md](identity.md)); the prototype accepts any `http` host, so `127.0.0.1` works here.
- With `IDENTITY_MODE=jwt` the user ID comes from the `user_id` claim (decision `0024`); a token without it is refused
  403. A development token's `user_id` is optional.
