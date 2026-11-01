# PK-V: independent validation of task PK (runtime image and Helm chart in Kind)

Validator: PK-V (wrote none of PK-1 to PK-5). Report only: no code changed.
Worktree `.claude/worktrees/task-pk`, branch `task-pk`, tip `e0d4f86` (includes `python` at `51acebf`).
Run of 18 October 2026, started 19:27 +07. Written as the run goes; an interruption leaves a partial report.

Cluster safety (CLAUDE.md section 0): every cluster call goes through `deploy/kind/proto-lib.sh` (`k`, `h`),
which checks the project-local kubeconfig `bin/kind-state/proto/kubeconfig`, context `kind-webblock-proto`,
server `https://127.0.0.1:<port>`. Nothing under `~/.kube` and no non-local kubeconfig was read.

## 0. Preconditions

- 19:27: `bin/kind get clusters`: "No kind clusters found."; no testcontainers, kind or webblock- container
  (only the owner's two excalidraw containers run); `free -m` available 8889 MB; another agent's unit run
  (`task-revoke`, `make py-test`, no containers) in progress.
- `PROJECT_SERVICE_TOKEN_FILE` unset in this environment; no `KUBECONFIG`, `PROV_*`, `KIND_*` set.

## 1. Offline gates (on `e0d4f86`, each `make -C <worktree> <target>`)

| Command | Exit | Counting line |
| --- | --- | --- |
| `kind-tools` | 0 | kind, kubectl, helm, crane, docker-buildx v0.37.2: "present, checksum verified, nothing downloaded" |
| `py-tools` | 0 | uv, osv-scanner, goose v3.28.0, grype v0.119.0: present, checksum verified |
| `py-sync` | 0 | `Checked 81 packages` |
| `py-fmt-check` | 0 | `487 files already formatted` |
| `py-lint` | 0 | `All checks passed!` |
| `py-typecheck` | 0 | `Success: no issues found in 482 source files` |
| `py-layers` | 0 | `Contracts: 8 kept, 0 broken.` |
| `py-test` | 0 | `5909 passed in 113.30s` (matches the merge report's 5909) |
| `chart-check` | 0 | `1 chart(s) linted, 0 chart(s) failed`; `22 passed in 1.60s` (19 chart + 3 cluster-values) |
| `py-age` | 0 | `84 uv.lock pins, 9 tool pins and 1 image pins, minimum 14 days, 12 waived, 0 failures`; base image `python 3.14.7-slim-trixie@sha256:51dafde8…: 2026-09-25 (22.7 d)`; only `rpds-py 2026.9.1` (13.8 d) still needs its waiver (until 2026-10-19); the other 11 print "the waiver is no longer needed (no young pin); remove it" |
| `py-vuln` | 0 | `uv.lock: 84 registry packages scanned offline`; `no known vulnerability` (advisories newest 2026-10-18T02:00Z, sha256 fa7a991d…) |

Image-scan exceptions file: 55 `[[exception]]` entries, all `image = "provisioning"`, all `expires = 2026-11-17`,
all `reviewer = "roa (owner), signed 18 October 2026"` (none says "pending", PK-5 review M-1 resolved).
Whether each matches a finding is checked by the scan in section 6.

## 2. Stack and cluster start (from nothing)

| Command | Exit | Counting line |
| --- | --- | --- |
| `make proto-down` (19:31) | 0 | `proto-down: done; no container of webblock-proto, … remains` |
| `PROJECT_SERVICE_DIR=…/project-service make proto-up` (19:31, 9497 MB available at the memory gate) | 0 | `proto-up: done in 125s` |
| context check (`require_local_cluster`) | 0 | `kind-webblock-proto`, `https://127.0.0.1:44297` |
| kubelet configz | 0 | `webblock-proto-control-plane 5s 100Mi`, `webblock-proto-worker 5s 100Mi` (`syncFrequency: 5s`, ruling 3) |
| `PROV_MODE=cluster make proto-run` (log `pkv-cluster-start.log`) | 0 | `build.sh: building webblock-provisioning-build:e0d4f869bc4b with docker-buildx v0.37.2`; `image: localhost:5001/webblock/platform/provisioning:e0d4f869bc4b is sha256:a2de0d32…f953`; `provisioning running (cluster mode) in 29 s: API http://172.18.255.249:8080` |
| `run-provisioning.sh status` (`pkv-status-1.log`) | 0 | four Deployments 1/1, four pods Running, 0 restarts, all on `webblock-proto-control-plane`, all on `webblock-proto-registry.test:5000/…@sha256:a2de0d32…` |
| `h history provisioning` | 0 | revision 1 `deployed`, `Install complete` |
| pods: ServiceAccount / `automountServiceAccountToken` | 0 | api `provisioning-api`/false; worker `provisioning-worker`/false; build-worker `provisioning-build-worker`/false; bootstrap `tenant-bootstrap`/false |
| `logs/latest/migrate.log` | – | `goose: successfully migrated database to version: 16` |
| `timings.txt` | – | postgres 2 s, rabbitmq 2 s, database 2 s, configuration 1 s, cluster_install 22 s, total 29 s |

Notes:
- The image build used the Docker build cache (build plus push 16 s); the cache was not pruned, because the
  daemon and its cache are shared with the owner's other work. The first, uncached build (wheel download)
  is therefore not exercised here, as in PK-5.
- The digest differs from PK-5's (`11095af9…`) because the tip moved (merge `c8ad5b2`, `e0d4f86`).

## 3. Negative cases done offline or read-only while the cluster e2e ran

### Case 4: `helm template` refuses a tag and an unknown key

Offline: `bin/helm template provisioning deploy/helm/provisioning --namespace webblock-system` with
`HOME`, the Helm homes and `--kubeconfig` in a fresh scratch folder where no kubeconfig exists, on the
run's own generated `values-env.json` and `values-stack.json`. Nothing was changed, so nothing to revert.

| Variant | Exit | What Helm said |
| --- | --- | --- |
| control: the generated values as they are | 0 | 17 objects rendered |
| `--set image=…/provisioning:latest` | 1 | `at '/image': '…:latest' does not match pattern '^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$'` |
| `--set image=…:latest@sha256:aaaa…` (tag plus digest) | 1 | same pattern refusal |
| `--set notAKey=1` | 1 | `at '': additional properties 'notAKey' not allowed` |
| `--set worker.hostNetwork=true` | 1 | `at '/worker': additional properties 'hostNetwork' not allowed` |
| `--set api.env.PG_PASS=x` (a secret key as plain env) | 1 | `invalid propertyName 'PG_PASS'` / `'not' failed` (Helm prints a wrong JSON path, `/image/node-role…`, before it: a cosmetic Helm quirk, the refusal is right) |
| no values (chart defaults) | 1 | `at '/image': '' does not match pattern …` (fails closed) |

Result: **passes**.

### Case 7: no secret value in any ConfigMap or values file (first pass, 19:45, during the e2e)

Script `secret_scan.py` (scratchpad; reads `provisioning/provisioning.env`, `jenkins.env`,
`catalog-admin.env` (`*_PASSWORD`), the secret keys of `provisioning/env/*.env` and the three tokens of
`tokens.json`, never prints a value, searches each raw and URL-encoded). 37 secret values of 8 or more
characters.

| Haystack | Bytes | Secret values found |
| --- | --- | --- |
| `k get configmap -n webblock-system -o json` | 20043 | 0 |
| `values-env.json` | 9607 | 0 |
| `values-stack.json` | 612 | 0 |
| `h get values provisioning --all -o json` | 8411 | 0 |
| `h get manifest provisioning` | 31180 | 0 |
| `k get deploy,job,pod -n webblock-system -o json` | 111296 | 0 |
| `logs/latest/*.log` (concatenated) | 24982 | 0 |
| control: `env/worker.env` (holds secrets by design) | 2799 | 10 (so the search works) |

Result: `no secret value found`, **passes**. Repeated after the negative cases (section 9a).

## 4. The full cluster `proto-e2e` (the exit run)

- `make proto-e2e` in cluster mode (log `bin/kind-state/proto/provisioning/pkv-cluster-e2e.log`), 19:39 to
  20:24: **exit 0**, `collected 63 items`, `63 passed in 2701.61s (0:45:01)`. `FAILED|ERROR|SKIPPED`: 0 lines.
  One run, no rerun. `tests/e2e/incluster/test_identities.py .......` (7) is included.
- Available memory fell to about 6.2 GB during the run (another agent's unit tests ran too); nothing failed.

## 5. Memory, restarts, OOM (read right after the e2e, before `proto-demo`)

Two sources: (a) `memory.peak`, `memory.max`, `memory.events` read inside each live container at 13:24:48Z;
(b) a poller on the control-plane node (`/var/tmp/peaks.sh`, every 1 s) that kept the latest
`memory.peak` of every Provisioning container instance by memory limit, mapped to pod names through a
`get pods --watch` of `webblock-system` started before the cluster install. (b) covers the pods that the
e2e's resets and kill tests replaced (17 instances).

| Component | Limit | Live pod peak (a) | Max over all instances (b) | Instances | Restarts | `oom_kill` |
| --- | --- | --- | --- | --- | --- | --- |
| api | 256Mi | 141361152 B (134.8 MiB, 53%) | 134.8 MiB (53%) | 2 | 0 | 0 |
| worker | 384Mi | 184205312 B (175.7 MiB, 46%) | 227.2 MiB (59%, pod `…-gsnfx`) | 9 | 0 | 0 |
| build-worker | 256Mi | 148230144 B (141.4 MiB, 55%) | 141.4 MiB (55%) | 3 | 0 | 0 |
| bootstrap | 192Mi | **201289728 B (192.0 MiB, 99.98%)** | **192.0 MiB (100%)** (pod `…-rshkq`; the other two 127.0 and 129.5 MiB) | 3 | 0 | 0 |
| migrate Job | 64Mi | – (deleted by the hook policy) | 28880896 B (27.5 MiB, 43%), one sample of the running container | 1 | – | – |

- **The bootstrap reached its limit.** Its pod cgroup `kubelet-kubepods-burstable-podd9d299cc….slice`
  shows `memory.events max=4` (the only cgroup on either node with a non-zero `max`, which is also the
  whole of the node-level `kubepods` count), and its container shows direct reclaim:
  `pgscan_direct 240`, `pgsteal_direct 240`, `workingset_refault_file 428`, memory PSI `total=73` µs.
  No OOM kill (`oom 0`, `oom_kill 0`), no restart. At 13:25Z it held `memory.current` 139231232 B
  (anon 134238208, file 2134016). The pod lived 12:41:41Z to the end of the run (44 min, 43 tenants,
  3734 `access_setup done` after 12:51:41Z). So the limit was met with page cache reclaimed; the
  anon peak is not observable after the fact. PK-5 measured 77% after a 2-minute demo only.
- No other container went above 59%. Both nodes: `kubelet-kubepods.slice/memory.events` `oom_kill 0`
  (control plane `max 4` = the bootstrap above; worker node `max 0`). `get events -A`: 0 lines naming OOM.
  All four live pods: restart count 0, no last termination.
- The migration Job: `backoffLimit: 0` and the install succeeded (`migrated database to version: 16`); the
  one 1-s sample of its container read 27.5 MiB. The hook deletes the Job and its pod on success, so no
  final peak can be read; see section 9a for the upgrade runs.

## 6. Token rotation (ruling 2; a pod living longer than 600 s)

Read inside the pods (token claims decoded, the token itself never printed), 13:25Z:

| Pod | Created | Token `iat` (= file mtime) | `exp` | Lifetime | `sub` |
| --- | --- | --- | --- | --- | --- |
| `provisioning-bootstrap-7778f4689b-rshkq` | 12:41:41Z | 13:21:38Z | 13:31:38Z | 600 s | `system:serviceaccount:webblock-system:tenant-bootstrap` |
| `provisioning-worker-5c46fdf76b-whsgb` | 13:14:25Z | 13:22:23Z (+478 s) | 13:32:23Z | 600 s | `system:serviceaccount:webblock-system:provisioning-worker` |

- The bootstrap pod's first token expired at about 12:51:41Z; its current token is from 13:21:38Z, so the
  file was replaced at least four times inside the one pod.
- `bootstrap.log` after 12:51:41Z: 7666 lines (7663 info, 3 warning), 3734 `access_setup done` (each
  reads the tenant's namespace and Secret through the API server) up to 13:25:50Z. The 3 warnings are
  the singleton lock's "uncertain"/"confirmed" pair and one "query cancellation failed" (the paused
  database test), none about the API server. No line matching Unauthorized, Forbidden, 401 or "token".
- The worker pod rotated at +478 s (the kubelet's 80% point). Its later lines (a promote, reconcile
  rounds with `failed=0`) come after its first expiry at 13:24:25Z, with no auth error.

Result: **rotation proven** on a 44-minute pod.

## 7. Loki (time-filtered, L-6)

- `make proto-logs Q='{job="webblock-provisioning", process="bootstrap"} |= "2026-10-18T13:2"' SINCE=15m LIMIT=3`:
  exit 0, three bootstrap lines of this run (13:20:00Z, `access_setup done`, `tenant ready`).
- `make proto-logs Q='{job="webblock-provisioning", process="migrate"} |= "2026/10/18 12:3"' SINCE=60m LIMIT=2`:
  exit 0, `OK 00001_schema_and_enums.sql`, `OK 00002_tables.sql` of this run's Job. The follower's files
  reach Loki, the migration Job's included.

## 8. Negative cases on the live cluster

### Case 5: a tenant site pod cannot reach Provisioning's database, broker or API

From `wb-01a14f06-…/rel-01a14f06-4b7c-…-k5csz` (a live sample site left by the e2e, on
`webblock-proto-worker`), `k exec … -- nc -z -w 4 <addr> <port>` (busybox `nc` in the site image):

| Destination | Result |
| --- | --- |
| `172.18.0.8:5432` (the catalog, allowed: positive control) | OPEN |
| `webblock-provisioning-db:5432` (by name) | refused/timeout |
| `172.18.0.11:5432` (`webblock-provisioning-db` on the `kind` network) | refused/timeout |
| `172.18.0.12:5672` (`webblock-rabbitmq`) | refused/timeout |
| `172.18.255.249:8080` (the API's LoadBalancer address) | refused/timeout |
| `10.244.0.160:8080` (the API pod) | refused/timeout |
| `172.18.255.249:8081` (the API's ops port) | refused/timeout |

`cilium-dbg monitor --type drop` on the site's node during a repeat showed `Policy denied` for
`-> 172.18.0.11:5432` and `-> 10.244.0.160:8080` (the LB address is translated to the API pod before
the tenant's egress policy drops it). Read-only; nothing to revert. Result: **passes**.

## 9. Demo, smoke, kind suites, service image scan (cluster mode)

| Command | Exit | Counting line |
| --- | --- | --- |
| `make proto-demo` (20:28, log `pkv-cluster-demo.log`) | 0 | `reset-sites: deleting 43 tenant namespaces` … `0 tenant namespaces, 0 Provisioning rows`; `2 passed in 164.55s` |
| `make proto-smoke` (log `pkv-smoke.log`) | 0 | 15 `smoke PASS`, 0 `FAIL`, `proto-smoke: all checks passed in 11s` |
| `make proto-test-kind` (the kubelet change's regression; log `pkv-test-kind.log`) | 0 | `proto-test-kind: site …/sites/5bd4d00c-…@sha256:a4672b61…`; `collected 225 items`; `225 passed in 241.36s`; 0 skipped/failed/error |
| `make image-scan IMAGE_SCAN_TARGETS=provisioning IMAGE_SCAN_REFS="provisioning=localhost:5001/…@sha256:a2de0d32…" IMAGE_SCAN_REGISTRY_CA=…/ca.crt` (log `pkv-image-scan.log`) | 2 | `scanning … from its registry (no local build)`; Grype DB `v6.1.10_2026-10-17T00:31:44Z`, built 2026-10-18T06:27:17Z, verified sha256 e3d1010e…; `3 Critical, 81 High, 70 Medium, 23 Low, 46 Negligible`; 55 `accepted until 2026-11-17 by roa (owner), signed 18 October 2026`; `FAILED: 29 unaccepted findings or exceptions` |

Image scan against the brief's expectation:
- **29 failures, exactly the expected set**, every one `no exception (fixed in …)`:
  CPython 3.14.7 (Critical CVE-2026-19445, High CVE-2026-19553; fixed in 3.14.8);
  OpenSSL deb13u2 (CVE-2026-54873, -72897, -84782, -84784 × libssl3t64, openssl, openssl-provider-legacy = 12;
  fixed in deb13u3); pcre2 (CVE-2026-103111, fixed in deb13u3); goose's Go stdlib go1.27.0 (Critical
  GO-2026-6612, High GO-2026-6603, -6605, -6607, -6608, -6610, -6611, -6613, -6617; fixed in 1.27.2) and
  golang.org/x/net v0.58.0 (Critical GO-2026-6612, High GO-2026-6603, -6610, -6611, -6617; fixed in 0.60.0).
  3 Critical + 26 High = 29. They stay red by the owner's decision.
- **All 55 exceptions match a finding**: 55 `accepted` lines, 55 distinct `(image, vulnerability, package)`
  keys, and no `the exception matches no finding; remove it` line (0). 55 + 29 = 84 = 3 Critical + 81 High.
- **No difference** from the expectation (same Grype database as PK-5; the new digest's base layers are
  the same pinned base).

## 9a. Negative cases on the live cluster (continued)

### Case 6: `kill-process worker` during a deploy (crash semantics)

Driver `case6_kill_during_deploy.py` (scratchpad; uses the e2e harness's `Site`, `log_mark` and
`worker_log_since`, which settle 3 s in cluster mode), log `pkv-case6.log`, 20:39:

- built release `01a14f3d-2f85-…` of project `01a14f3d-186c-…`; deploy accepted; the candidate Deployment
  appeared 0.29 s later; command `running` before the kill.
- `kill-process worker` returned in 1.68 s; worker pods right after: **none**; command still `running`.
- 40 s later: worker pods **none**; `worker.log` lines added since the post-kill mark: **0**; command still
  `running` (the dead worker's claim was not released).
- `start-process worker` returned in 4.46 s (pod `…-j2fdr` Running); the command ended `succeeded`.
  `worker.log` shows the new pod's `command executed (… kind=deploy command_number=1 claim_number=2 …)`
  at 13:40:26Z, so it re-claimed the command (claim 2) and finished it. (The driver's "0 lines after
  start-process" is its own timing: those lines were written before its second mark.)

Result: **passes**. Revert: none needed (the worker was started again by the driver).

### Case 1: the API Deployment with `automountServiceAccountToken: true`

`k -n webblock-system patch deployment provisioning-api --type=json` (replace
`/spec/template/spec/automountServiceAccountToken` with `true`), 45 s:
- new pod `provisioning-api-5d966dc8b6-7kzjs`: `automountServiceAccountToken` true, mount
  `/var/run/secrets/kubernetes.io/serviceaccount` present; `CrashLoopBackOff`, 3 restarts, last exit
  **2**, never ready; the EndpointSlice lists it `ready=false` and the old pod `ready=true`.
- its log and `logs/latest/api.log` (4 lines after the mark): `provisioning-api: a default ServiceAccount
  token is mounted at /var/run/secrets/kubernetes.io/serviceaccount/token; the pod must set
  automountServiceAccountToken: false (decision 0016 section 9 ruling 2)`; no `serving` line after the mark.

Revert: `PROV_MODE=cluster make proto-run` (log `pkv-revert1.log`): exit 0, `provisioning running (cluster
mode) in 15 s`, Helm revision 2 `Upgrade complete`; the template is back to `false`, ReplicaSet
`…-5d966dc8b6` scaled to 0, only the original pod remains. Result: **passes**.

### Case 2: the worker's ConfigMap with the bootstrap's `KUBE_EXPECTED_USERNAME`

`k patch configmap provisioning-worker-env` (`KUBE_EXPECTED_USERNAME` from
`system:serviceaccount:webblock-system:provisioning-worker` to `…:tenant-bootstrap`), `k rollout restart
deployment/provisioning-worker`, 50 s:
- new pod `…-5dc4d54564-r5dl9`: `CrashLoopBackOff`, 3 restarts, last exit **1**; its whole log is one line,
  `{"level":"error","msg":"worker could not be built","error":"the token is not the expected
  ServiceAccount's"}` (nothing before the review: no broker, no claim). `worker.log` has 4 such lines.
  The old pod stayed ready.

Revert: `PROV_MODE=cluster make proto-run` (`pkv-revert2.log`): exit 0, revision 3. Helm restored the
ConfigMap value; the crash-looping pod read it at its next restart and became ready (restart count 4).
kubectl's `restartedAt` annotation stays on the template (Helm does not own it); harmless. Result: **passes**.

### Case 3: the worker Deployment under ServiceAccount `tenant-bootstrap`

`k patch deployment provisioning-worker --type=json` (replace `/spec/template/spec/serviceAccountName`
with `tenant-bootstrap`), 50 s:
- new pod `…-66cd4cb874-hqrct` (SA `tenant-bootstrap`): `CrashLoopBackOff`, 3 restarts, last exit **1**,
  log `worker could not be built` / `the token is not the expected ServiceAccount's`; the old pod stayed
  ready. No admission policy refused the pod; the process's SelfSubjectReview did.

Revert: `PROV_MODE=cluster make proto-run` (`pkv-revert3.log`): exit 0, revision 4 `Upgrade complete`;
the template's `serviceAccountName` is `provisioning-worker` again; all four pods ready under their own
ServiceAccounts. Result: **passes**.

After the three reverts: `pytest -m e2e tests/e2e/incluster`: `7 passed in 5.49s`.

### Migration Job, upgrade runs

The node poller caught the pre-upgrade Job pod of the first revert (`provisioning-migrate-scblm`,
`goose: no migrations to run. current version: 16`): pod-level `memory.peak` **52318208 B (49.9 MiB,
78% of 64Mi)**. The pod cgroup counts the page cache of the 42 MB goose binary (`bin/goose` is
42090656 B), which explains most of it; the first install's container read 27.5 MiB in its one sample.

### Case 7, second pass (after the negative cases)

Same script, same 37 secret values: `configmaps.json` 28200 B, `values-env.json`, `values-stack.json`,
`h get values --all`, `h get manifest`, `k get deploy,job,pod,rs -o json` (201067 B) and **every run
folder's logs** (`logs/*/*.log`, 65785644 B): 0 secret values in each; `no secret value found`.

## 10. Back to the host: the regression run

| Command | Exit | Counting line |
| --- | --- | --- |
| `make proto-stop` (cluster mode; `pkv-stop-cluster.log`) | 0 | `provisioning stopped; containers removed`; `h list -n webblock-system`: header only; `k get secret,configmap,job,ciliumnetworkpolicy,deploy,sa \| grep provisioning-`: only `serviceaccount/provisioning-worker` (the platform layer's, not the chart's, kept by design); mode record removed; data containers removed |
| `make proto-run` (host; `pkv-host-start.log`) | 0 | `provisioning running (host mode) in 9 s: API http://127.0.0.1:18080`; mode `host` |
| `make proto-e2e` (host; `pkv-host-e2e.log`), 20:47 to 21:15 | 0 | `proto-e2e: host mode: tests/e2e/incluster deselected …`; `collected 56 items`; `56 passed in 1618.02s (0:26:58)`; `FAILED\|ERROR\|SKIPPED`: 0 |

## 11. Teardown

- `make proto-stop`: exit 0. `make proto-down`: exit 0, `proto-down: done; no container of webblock-proto, the
  registry, Jenkins, BuildKit, the Git server, the catalog or Loki remains` (21:15:21).
- `docker ps -a --format '{{.Names}}' | grep -E 'webblock-|kind' || echo clean`: `clean`.
  `bin/kind get clusters`: "No kind clusters found." Running containers: only the owner's `brave_galileo`
  and `mcp-excalidraw-canvas`.
- The worktree is unchanged (`git status`: only the `bin` symlink; tip `e0d4f86`). The node poller and the
  pod watch went with the cluster.

## 12. Rulings mapped to evidence

| Ruling | Evidence | Holds |
| --- | --- | --- |
| 0016 s8 r1: one image; API, worker, build worker, bootstrap as pods, each with its own ServiceAccount and identity | §2 (four Deployments on the one digest `a2de0d32…`; SAs `provisioning-api`, `provisioning-worker`, `provisioning-build-worker`, `tenant-bootstrap`); §6 token `sub`s; `test_identities.py` 7 passed (§4, and after the reverts) | yes |
| … a migration Job as `provisioning_migrator` | §2 (`migrated database to version: 16` from the hook Job); §9a (upgrade Job `no migrations to run`); Loki lines §7 | yes |
| … PostgreSQL and RabbitMQ outside the chart, by configured endpoints | `values-stack.json` network-policy addresses; case 5 (`172.18.0.11`, `172.18.0.12` on the `kind` network) | yes |
| … the full `proto-e2e` in one run against the in-cluster service | §4: 63 passed, one run | yes |
| 0016 s8 r2: one Helm chart, values per environment | `chart-check` (§1); `h history` revisions 1 to 4 (§2, §9a); case 4 (the schema refuses tags, unknown keys and secret keys) | yes |
| 0016 s9 r1: `proto-demo` once in cluster mode; host `proto-e2e` as regression; replicas host-only | §9 demo 2 passed; §10 host 56 passed; replica suite not run (host-only by ruling) | yes |
| 0016 s9 r2: projected token, 600 s; never the automount; API and build worker without a token | §2 (`automountServiceAccountToken` false on all four pods); §6 (lifetime 600 s, rotated inside a 44-min pod, no auth error); case 1 (automount refused, exit 2) | yes |
| … SelfSubjectReview against `KUBE_EXPECTED_USERNAME`, exit on mismatch | cases 2 and 3 (exit 1, `the token is not the expected ServiceAccount's`, nothing before it) | yes |
| … production refuses kubeconfig | unit tests in `py-test` (5909 passed; PK-1's `test_cluster_sections.py`); not live (prototype profile only) | yes (unit) |
| 0016 s9 r3: stand-ins as one ConfigMap, directory mount, harness waits; `syncFrequency: 5s` | configz `5s` on both nodes (§2); `test_api_surface.py` 7 passed in the cluster e2e (stand-in rewrites through `standins-sync`) | yes |
| 0016 s9 r4: `python:3.14-slim-trixie` by digest, ≥ 14 days, `psycopg[binary]`; exceptions ≤ 30 days signed by the owner | `py-age` (base 22.7 d); scan §9 (55 owner-signed exceptions to 2026-11-17, all matched); 29 fixable findings stay red by the owner's decision | yes, with the known red scan |
| 0016 s9 r5: pinned, checksum-verified buildx through a project-local `DOCKER_CONFIG`; never the tenant BuildKit | `kind-tools` (buildx v0.37.2 checksum verified); `build.sh: … with docker-buildx v0.37.2` (§2) | yes |
| CLAUDE.md section 0 | every call through `k`/`h` with `require_local_kubeconfig` (`kind-webblock-proto`, `https://127.0.0.1:44297`); offline Helm with a non-existent kubeconfig; nothing under `~/.kube` read | yes |

## 13. Findings

### Critical

None.

### Important

**I-1. The bootstrap reaches its 192Mi memory limit under the e2e load.** The bootstrap pod that lived
through the cluster e2e (`…-rshkq`, 44 min, 43 tenants) has `memory.peak` 201289728 B, 36 KiB under its
limit; its pod cgroup recorded `memory.events max=4` (the only non-zero `max` on either node), and the
container shows direct reclaim (`pgscan_direct 240`, `workingset_refault_file 428`, PSI 73 µs). There was
no OOM kill and no restart, and at the end it held 139 MB (134 MB anon), so the kernel reclaimed page cache
to stay under the limit. Decision 18 of the plan changes a limit only after an OOMKilled, so nothing was
violated, but the headroom is gone. A larger tenant count, or a heavier round, could push anonymous memory
over the limit, and the bootstrap is a single replica with `Recreate`: an OOM kill would stop tenant
creation until CrashLoopBackOff lets it back. PK-5's 77% was measured after a 2-minute demo only. For the
lead: raise `bootstrap.memory` (for example to 256Mi), or measure `memory.stat` anon across a full e2e
before deciding. The other containers peaked at 53% (api), 59% (worker, over 9 instances) and 55%
(build worker).

### Minor

**m-1. `make py-age` will fail from 20 October 2026.** It passes today. But all 12 waivers in
`scripts/py/age-waivers.toml` expire on 2026-10-19, 11 of them already print "no longer needed; remove it",
and `scripts/py/age.py` fails on an expired waiver whether or not it is still needed. This is outside PK.
The fix belongs on `python`: remove the 11 stale waivers, and rpds-py's once it is 14 days old.

**m-2. Migration Job headroom.** The pre-upgrade Job's pod peaked at 49.9 MiB, 78% of 64Mi. Most of this
is the page cache of the 42 MB `goose` binary. The first install's container was sampled once at 27.5 MiB.
Reclaimable page cache makes an OOM unlikely. But if a later goose or a heavier migration grows it, 64Mi
is tight. The Job deletes itself on success, so the only way to read its peak is a poller on the node, as
here.

**m-3. Carried, not re-tested: `kc --request-timeout` (PK-5 review L-3).** A single `k` call in
`cluster_kill_process`, `standins-sync` or `stop` has no timeout of its own. No hang happened in this run.

### Info

- **The service image scan is red with exactly the expected 29 fixable findings.** They are 3 Critical
  and 26 High:
  - CPython 3.14.7;
  - OpenSSL and pcre2 at deb13u2;
  - goose's go1.27.0 and x/net v0.58.0.

  This is the owner's decision; nothing differs from the expectation. All 55 owner-signed exceptions
  match a finding, and none is unused.
- **The image came from the build cache.** The cache was not pruned, because the Docker daemon is shared.
  So the first, uncached build was not exercised, as in PK-5. The digest is `a2de0d32…`.
- **A Helm revert does not restart pods.** It restores a hand-edited ConfigMap but does not restart a pod
  that already read it. In case 2 the guard made the pod crash, so it re-read the ConfigMap. Also, a
  `kubectl rollout restart` annotation stays on the template after `proto-run`. Both are normal Helm
  behaviour and worth one line in the runbook.
- **Helm prints a wrong JSON path** (`/image/node-role…`) when it refuses `api.env.PG_PASS`. The refusal is
  correct; the path is a cosmetic quirk of Helm itself.
- **Old runs are re-shipped to Loki (PK-5 review L-6).** Every Loki query here was filtered by time.

## 14. Skipped checks

- The replica suite (`proto-e2e-replicas`): host-only by ruling 1; not run.
- `make image-scan` of the sample site (F-10, PK-5 step 10): not in PK-V's list; not rerun.
- `make py-cover`, `py-test-integration`, `py-test-messaging`: not in PK-V's list; not run.
- The production profile in a pod: not run live. Only the unit tests cover the production refusal of
  the kubeconfig connection.
- The uncached first image build: not exercised (see Info).
- The bootstrap's anonymous-memory peak: it cannot be read after the fact; only the total `memory.peak`
  and the reclaim counters were read.

## 15. Verdict

**Approved with findings.** Every exit run passed on the first attempt:
- the cluster `proto-e2e`, 63 of 63 in one run;
- `proto-demo`, 2 of 2;
- `proto-smoke`, 15 of 15;
- `proto-test-kind`, 225 of 225;
- the host `proto-e2e`, 56 of 56;
- every offline gate, `chart-check` and `py-age`.

All seven negative cases behaved as specified and were reverted, and each ruling maps to evidence.

There are no Critical findings. One Important finding (I-1, the bootstrap's memory limit) needs a decision
from the lead. The scan stays red by the owner's decision, with no difference from the expectation.
