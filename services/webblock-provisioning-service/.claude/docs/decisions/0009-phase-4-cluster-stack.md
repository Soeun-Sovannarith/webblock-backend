# 0009: Phase 4: local Kind stack, Crossplane composition, naming, tenancy

**Partly superseded by decision [`0013`](0013-python-port-without-crossplane.md)**: sections 4 (the XR mechanism; the
separate bootstrap identity stays), 5, 6, 13 and the XR parts of 11 no longer apply, because Crossplane is removed.
Sections 1 to 3 and 7 to 10 (local cluster, Gateway, naming, guarded candidate delete, catalog setup, candidates) still
apply to the Python service.

**Section 2 amended by decision [`0019`](0019-service-identity-is-the-mesh.md) section 5**: Istio everywhere, the Istio
ingress gateway for the public hosts and `istio-internal-gateway` for the Cloudflare tunnel; Cilium stays only as the
CNI. The Kind stack and the code still use Cilium's Gateway until a follow-up moves them.

**Amended by decision [`0011`](0011-phase-4-go-tracks.md)**: the acknowledgement of current intent (section 6, now
`0011` section 1), no in-cluster HTTP probe by the worker (`0011` section 2), candidate failures and cleanup (section 7,
now `0011` section 3), the replaced candidate (section 9, now `0011` section 4), the catalog credential (section 8, now
`0011` section 5).

Status: **Current for the sections that still apply** (see the notes above). The local Kind stack (`deploy/kind`)
and the naming rule in the code implement them. Amends decisions `0002` and `0005` section 5 where it says so; where
they differ, this record wins.

## 1. Local cluster instead of DOKS

- Production runs on DigitalOcean Kubernetes (DOKS). No real cluster is provided. Kind runs Crossplane locally for Phase
  4 tests and validation. Floci was considered and not used: it emulates AWS, Azure, GCP and OCI, not DigitalOcean.
- Kubernetes **1.36**, DOKS's newest minor: `kindest/node:v1.36.4` (pinned by digest), kubectl v1.36.5, client-go
  v0.36.5. Revisit before 1.36 reaches DOKS end of support.
- Tools are installed only into the git-ignored `./bin` by a pinned, checksum-verified Make target; nothing system-wide.
  Every `kind`, `kubectl` and `helm` call passes an explicit `--kubeconfig` naming a git-ignored project-local file;
  `~/.kube/config` and `../ksga-kubernetes-cluster-kubeconfig.yaml` are never read or written. Go acceptance tests read
  only their own kubeconfig variable and refuse to run unless the context is the local Kind cluster on `127.0.0.1`. The
  cluster is created and deleted per run (`KEEP_CLUSTER=1` keeps it for local iteration only) and never runs at the same
  time as the Testcontainers suites (about 5 GB of memory, 20 to 30 minutes, to be measured).
- Cilium 1.19.8 is the CNI with full kube-proxy replacement (`disableDefaultCNI`, `kubeProxyMode: none`), mirroring
  DOKS's VPC-native mode, so NetworkPolicy and CiliumNetworkPolicy are enforced. Images and function packages come from
  a local registry on `127.0.0.1:5001`, pulled by digest.
- **What a Kind pass proves and does not prove.** It proves XRD/CRD validation on a real API server, Composition and
  function behaviour, readiness and status semantics, RBAC and admission denials, restricted pod security, network
  policy under Cilium, Gateway `allowedRoutes` and hostname matching, and behaviour across Crossplane and worker
  restarts. It does not prove DigitalOcean load balancers, external HTTPS or certificates, DOKS's exact Cilium and
  Gateway API CRD versions, node pools and autoscaling, or production storage and backup. Every report says so.

- **The real cluster is never touched.** No `kubectl` or other client may run against the owner's DOKS cluster; its
  kubeconfig and everything under `~/.kube` are never read or changed; the only cluster allowed is the project's own
  Kind cluster from the `kindest/node` image, addressed only through `bin/kind-state/kubeconfig` on `127.0.0.1`. The
  binding text is section 0 of `.claude/CLAUDE.md`.

## 2. Gateway (OD-35)

Cilium's own Gateway API implementation, GatewayClass `cilium`, as DOKS provisions it. Kind installs the Gateway API
standard CRDs v1.4.1 (the version Cilium 1.19 supports); on DOKS the platform manages those CRDs and Provisioning never
installs them. One shared Gateway `webblock-public` in namespace `webblock-gateway`, listener
`allowedRoutes.namespaces.from: Selector` on the tenant label. The CRD bundle version on the owner's DOKS cluster is not
known; it is read from the CRDs' `gateway.networking.k8s.io/bundle-version` annotation by the owner before production.

## 3. Naming (OD-43)

| Item | Rule |
| --- | --- |
| Namespace | `wb-<projectId>` (lowercase UUID) |
| Namespace labels (set at bootstrap, immutable) | `websites.webblock.internal/project-id=<projectId>`, `websites.webblock.internal/tenant=true`, Pod Security `enforce/audit/warn=restricted` with a pinned `enforce-version` |
| Release Deployment, Service and XR | `rel-<releaseId>` |
| Route HTTPRoute, Usage and XR | `route-<projectId>` |
| Catalog credential Secret | `catalog-runtime` in the tenant namespace |
| Database role | `site_<projectId without hyphens>` (PLATFORM_DB_DESIGN section 6) |

A ValidatingAdmissionPolicy refuses an XR whose `spec.projectId` differs from its namespace's project label.

## 4. Tenant bootstrap (OD-34)

A cluster-scoped `WebsiteTenant` XR (spec: `projectId` only), composed by the same function, creates the namespace with
its labels, a ResourceQuota and LimitRange, a default-deny CiliumNetworkPolicy (ingress only from Cilium's Gateway
identity; egress to DNS and, once OD-46 names it, the catalog endpoint; no metadata IP, no other tenant, no Kubernetes
API) and the RoleBinding of the worker's ClusterRole. It is applied by a dedicated bootstrap identity that is never the
worker or the API. In Kind that is a `tenant-bootstrap` ServiceAccount; who holds it in production stays open (OD-34
remainder).

## 5. Crossplane (OD-30)

- Crossplane core and chart **2.4.2**. The CLI stays at v2.5.0: CLI and core are versioned separately, and the schema
  image `make xr-validate` uses is the part pinned to the core.
- **One Go composition function** of our own (`function-sdk-go` v0.7.1, in its own Go module under
  `deploy/crossplane/functions/website/`, so its dependencies stay out of the service's `go.mod`; its vulnerability scan
  is a separate gate step). It renders only from the validated spec, refuses intent the schema cannot (the registry
  allow-list of OD-32, the hostname suffix) with a fatal result, and decides readiness explicitly.
- Both XRDs set `enforcedCompositionRef` and `defaultCompositionUpdatePolicy: Manual`: an XR stays on its Composition
  revision; moving it is a reviewed operator step.
- Helm v3.22.0 now; Helm 4 before Helm 3's security support ends (10 February 2027), recorded as a follow-up.

## 6. XR contract amendments (OD-31) (amends `0002`)

- Both XRDs refuse a spec write whose `operationGeneration` is lower than the stored one:
  `self.operationGeneration > oldSelf.operationGeneration || self == oldSelf`. No write-stamp annotations.
- **Amended form of that rule.** Crossplane 2.4.2 keeps its own fields (`compositionRef`, `compositionRevisionRef`,
  `resourceRefs`) in `spec.crossplane` and writes them after creation without raising `operationGeneration`, so
  `self == oldSelf` on the whole spec would refuse Crossplane's own writes and no XR could bind to its Composition
  (shown through the API server's CustomResource strategy in `deploy/crossplane/test/xrd/rule0009_test.go`). The rule is
  therefore written field by field over the intent fields only: `operationGeneration` rises, or it is equal and every
  intent field (release: `projectId`, `releaseId`, `image`, `runtimeAccessRef`, `resources`; route: `projectId`,
  `hostname`, `targetReleaseId`) is unchanged. The owner's intent is kept: an intent change requires a newer generation.
  The worker never writes `spec.crossplane` (admission policy, K3).
- To confirm on Kind before anything relies on it: `crossplane render` shows `Ready` and `Synced` without
  `observedGeneration`; the acknowledgement of current intent above depends on the live controller stamping it, and the
  Phase 4 acceptance suite must prove it or this section changes.
- The worker writes XRs with server-side apply, field manager `webblock-provisioning-worker`, `fieldValidation=Strict`,
  `force=false`; another writer surfaces as a conflict (PR-18). An admission policy refuses changes to
  `spec.crossplane.*` by the worker.
- **Current intent is acknowledged** only when all hold: Crossplane's `Ready=True` with `observedGeneration` equal to
  the generation the worker's write returned (Crossplane 2.4.2 stamps it on its system conditions); the function-written
  `status.observedOperationGeneration` equals the command's fence (and `observedTargetReleaseId` for the route); the
  worker's own read of the composed Deployment, EndpointSlices and HTTPRoute agrees; and an in-cluster HTTP probe of the
  candidate passes. The function marks a Deployment ready only at its current `observedGeneration` with all replicas
  updated and available and the spec digest in the pod template, and an HTTPRoute only when our Gateway's parent status
  is `Accepted` and `ResolvedRefs` at the route's current generation. `Synced=True` alone is never enough.
- The route Composition also composes a Crossplane `Usage` so the routed release XR cannot be deleted; an admission
  policy limits deletes of website XRs to the worker and of tenant namespaces to the bootstrap identity.

## 7. A deploy may fail after its XR is written (amends `0005` section 5)

A release XR never carries public traffic, so a deploy may end `failed` (deadline, exhausted attempts, readiness
failure) after its candidate XR was written. Before writing `failed` the worker makes a fenced, best-effort delete of
that candidate XR, allowed only when the release is not the route target. A leftover candidate XR is an orphan for the
Phase 5 sweeper and is never adopted silently. This rule does not apply to the route XR, which `0005` section 4 still
governs.

## 8. Catalog access setup (OD-46, OD-38)

A **test-only** catalog stand-in under `tests/acceptance/catalog/`, behind a build tag, reproducing only what
PLATFORM_DB_DESIGN section 6 specifies (`catalog.websites`, one tenant table, the `catalog_site` group,
`catalog.current_website_id()`, forced row-level security), marked in its header as not the catalog owner's schema,
applied to throwaway `prov_test_catalog_*` databases. `access_setup` is proven against it (own rows only, `WITH CHECK`
refuses moving a row, no access to `catalog.websites`, idempotent re-run, an inactive website sees nothing). The
production adapter stays in `internal/adapters/unconfigured`, naming OD-46. The worker never gets Secret write; in Kind
a test-only adapter writes `catalog-runtime` with the bootstrap identity (OD-38 stays open for production).

## 9. Candidates and quotas (OD-47, OD-33)

One candidate per project at a time in Phase 4: a new deploy replaces an idle candidate. Kind uses placeholder quotas
(for example 2 CPU and 2 GiB per tenant), marked in the manifests as test values; production numbers stay OD-33.

## 10. Scheduling

Cluster-file work (`deploy/kind/`, `deploy/crossplane/` compositions and function, policy and RBAC) starts now, in
parallel with Phase 3, on files no Phase 3 task owns. The Go cluster adapters, the deploy executor and `access_setup`
start after Phase 3 is merged. Heavy Kind runs wait until Phase 3's database tests are finished. Phase 4 work merges
into a branch `phase-4` created from `phase-3` once Phase 3 is validated.

## 11. Choices accepted from the cluster tracks

- K1: Cilium LB-IPAM with L2 announcements serves the Gateway's address in Kind (no cloud-provider-kind). One local
  stack at a time (the address range and the kubeconfig are single). Tool state, kubeconfig and caches live under
  `bin/kind-state/`; kubectl and helm never write under `$HOME`. The containerd registry patch stays (needed on
  `kindest/node:v1.36.4`). Measured: 218 s and about 1.8 GB for a full bring-up.
- K3: Crossplane, not the bootstrap identity, holds `bind` on `webblock-tenant-worker`, because Crossplane creates the
  RoleBinding; the Crossplane aggregation role covers the tenant objects without `delete`; the bootstrap identity has
  its own namespace `webblock-bootstrap`; five more admission policies (namespace consistency, no later WebsiteTenant
  owner reference, how the worker role is bound, WebsiteTenant create/delete only by bootstrap) and a Cilium policy
  blocking the metadata address. Teardown depends on the garbage collector and the namespace controller being allowed to
  delete; their identity on DOKS must be checked before production.
- K2: the WebsiteTenant XR is named `wb-<projectId>`; the namespace carries all its labels and the owner reference at
  creation; pods run as UID/GID 65532 with requests equal to limits and a TCP readiness probe; Kind-only composition
  inputs (port 3000, `/app/.next/cache`, controller name `io.cilium/gateway-controller`, to confirm live); the function
  module raises `golang.org/x/net` to v0.58.0 for grpc under Go 1.27.

## 12. Start of Phase 4 integration

- Branch `phase-4` is created from `phase-3` (`d541902`). K1, K2 (with follow-up 1) and K3 are merged as `e436c42`,
  `9dfe9fc` and `bde704b` after the lead reran their checks in MAIN: `make kind-tools` (checksums verified, nothing
  downloaded), `bash -n` on every Kind script, `make xr-validate` (valid 3/3, rejected 57/57, transitions 9/9 and
  23/23), the function's `hack/check.sh` (coverage 95.7%, govulncheck clean) and `hack/render.sh` (11/11), K3's
  `check-static.py --self-test` and `check-live-guard-test.sh` (14/14). None of the three has yet run together on one
  live cluster; task K4 does that.
- **`make vuln` downloads the Go vulnerability database once per run, with bounded retries, and scans offline** against
  that copy, because `vuln.go.dev` resets connections from the development laptop (validation finding V3-07). A failed
  or unverifiable download still fails the gate; nothing falls back to an unscanned pass. The function module's own scan
  uses the same copy. Amends the mechanics of decision `0004` section 10, not what is scanned.

## 13. Live findings of task K4 and the lead's rulings

- **Facts measured on Kind** (Crossplane 2.4.2, evidence in K4's report): Crossplane stamps `observedGeneration` on the
  XR's `Ready`, `Synced` and `Responsive` conditions, equal to the XR's `metadata.generation` at reconcile. Its own
  writes after create (`compositionRef`, `compositionRevisionRef`, `resourceRefs`) raise `metadata.generation` from 1 to
  4 within about 30 ms, so `Ready.observedGeneration` never equals the generation returned by the worker's create; after
  a later worker write it equals that write's generation. Right after a write the XR still shows the previous
  `Ready=True` and `observedOperationGeneration`; stale status writes can move `observedGeneration` backwards for a
  moment, and `Ready` flips several times during a rollout. Server-side apply refuses an unknown spec field; the
  worker's request carries `fieldValidation=Strict`, `fieldManager=webblock-provisioning-worker`, `force=false`. How
  section 6's "equal to the generation the worker's write returned" is read in the light of these facts is a question
  for the owner.
- **"Spec digest in the pod template" means a digest of the whole release intent, not the image digest alone.** K4
  showed that a resources-only change was acknowledged about 0.05 s after the write while the Deployment still ran the
  old template, because the function compared only the image digest. The function writes the annotation
  `websites.webblock.internal/intent-digest` on the Deployment's pod template: `sha256:` and the hex SHA-256 of a
  versioned canonical text of the release intent fields (`projectId`, `releaseId`, `image.repository`, `image.digest`,
  `runtimeAccessRef.name`, `resources.cpuMillicores`, `resources.memoryMiB`; not `operationGeneration`), defined exactly
  in `deploy/crossplane/README.md` with a test vector. A Deployment is ready only when the observed pod template carries
  the desired digest, it is at its current generation, and its replicas are updated and available;
  `status.observedOperationGeneration` advances only then. The worker's own read of the Deployment recomputes the digest
  from its SQL records and compares it. This implements section 6; it changes no owner decision.
