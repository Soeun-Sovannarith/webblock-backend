# The Provisioning service in the Kind stack (task PK)

Decision 0016 section 8 rulings 1 and 2 and section 9. The prototype runs its four processes either on
the host (`make proto-run`, the default and the fast loop for code changes) or as pods of the Helm chart
`deploy/helm/provisioning` inside the Kind stack (`make proto-run PROV_MODE=cluster`). Both modes use
the same PostgreSQL and RabbitMQ containers (`webblock-provisioning-db`, `webblock-rabbitmq`: on the
`kind` Docker network, ports published on 127.0.0.1), the same settings (`configure()` in
`scripts/prototype/run-provisioning.sh`), the same e2e harness and the same log files. Local Kind
only: `.claude/CLAUDE.md` section 0 applies to every call, and every `kubectl` and `helm` call goes
through `deploy/kind/proto-lib.sh` (`k`, `h`).

## From nothing

1. `make kind-tools py-tools py-sync` (installs `bin/docker-buildx` too).
2. `make chart-check`: the chart, linted and rendered offline (it also renders the values the run
   script generates, `tests/chart/test_cluster_values.py`).
3. `make proto-up`. A stack made before task PK must be rebuilt first (`make proto-down`), because the
   kubelet setting of `deploy/kind/cluster.yaml` applies only to a new cluster.
4. `make proto-run PROV_MODE=cluster`.
5. `make proto-e2e` (it also runs `tests/e2e/incluster`), `make proto-demo`.
6. `make image-scan IMAGE_SCAN_TARGETS=provisioning IMAGE_SCAN_REFS="provisioning=<PROV_IMAGE_ON_HOST>"
   IMAGE_SCAN_REGISTRY_CA=bin/kind-state/proto/registry-tls/ca.crt` (`mk/image-scan.mk`; `PROV_IMAGE_ON_HOST` is in
   `bin/kind-state/proto/provisioning/image.env`).
7. `make proto-stop proto-down`.

After a code change on a running stack, `make proto-run PROV_MODE=cluster` again rebuilds the image and
upgrades the release (a new digest rolls the pods). `restart` and `proto-reset-sites` never rebuild.

The mode is chosen once per run: `start` records it in `bin/kind-state/proto/provisioning/mode` (a host
start always records `host`), and every other subcommand, `make proto-e2e` and the e2e harness read it.
A start in the other mode is refused until `make proto-stop` has removed the record. A record that holds
neither `host` nor `cluster` (an empty file, for example) stops every subcommand, `stop` included, and is
kept: write the running stack's mode into it and run the command again. `stop` also refuses, and keeps
the record, when the cluster exists but its kubeconfig fails the local check (`.claude/CLAUDE.md` section 0).

## What the cluster start does

1. Checks: the stack exists, `bin/helm`; no host-mode run is recorded; `PROV_REPLICAS=1`; project access
   is the access file or allow-all (`PROJECT_ACCESS_MODE=project-service` runs on the host only).
2. The containers, roles, the database and the logins as in host mode, plus `prov_migrator_login`
   (member of `provisioning_migrator` only). The migrations are not run on the host.
3. `configure cluster`: the same settings, with the containers by name (`webblock-provisioning-db:5432`,
   `webblock-rabbitmq:5672`, `webblock-jenkins:8080`, `webblock-catalog-db:5432`), listeners on
   `0.0.0.0` (API 8080 and 8081, the others 9090), the chart's mount paths, `KUBE_CONNECTION=in-cluster`
   with each identity's expected username, and `env/migrate.env` (`GOOSE_DBSTRING`). The operator CLI's
   files (`operator.env`, `operator_as_worker.env`, `operator_dlq.env`) keep the published ports.
4. `deploy/image/build.sh` builds the image (pinned buildx; the legacy builder when the plugin is
   missing); the archive is pushed with `crane` to `localhost:5001/webblock/platform/provisioning:<rev>`;
   pods pull `webblock-proto-registry.test:5000/webblock/platform/provisioning@sha256:…`
   (`provisioning/image.env`).
5. `scripts/prototype/cluster_values.py` turns the env files into `values-env.json` (non-secret keys)
   and the Secrets `provisioning-<component>-secret` (applied over a pipe, never printed);
   `values-stack.json` adds the image, the placement on the control-plane node, the API's
   LoadBalancer address (the last address of LB-IPAM pool `webblock-kind-pool`) and the
   network-policy addresses of the four containers. Secret `provisioning-dev-tokens`, ConfigMaps
   `provisioning-standins` and `provisioning-gateway-ca`.
6. A server-side dry run of everything the chart renders, in `webblock`, with warnings as errors:
   the schemas of Kubernetes 1.36 and the CRDs, and the namespace's admission chain, PodSecurity
   `restricted` included. It stores nothing; `--force-conflicts` only lets a re-run after a code change
   pass over the fields Helm and `kubectl scale` own. It does not run Helm's hooks or their order.
7. The log follower, then `helm upgrade --install provisioning --wait --timeout 10m`: the migration Job
   (a pre-install/pre-upgrade hook, goose as the migrator), then the four Deployments until ready.
8. `endpoints.env`: `API_URL=http://<address>:8080`. In host mode the same file names the address the
   API listens on (`http://127.0.0.1:18080`, and `API2_URL` on a replica run). The harness reads
   Jenkins from the stack's `jenkins.env` (its published port on 127.0.0.1), which the run script never
   rewrites.

## The run script's controls in cluster mode

| Subcommand | Host mode | Cluster mode |
| --- | --- | --- |
| `stop-process NAME` | SIGTERM, SIGKILL after 30 s | scale the Deployment to 0, wait until no pod is left |
| `start-process NAME` | start the process | scale to 1, `rollout status` |
| `kill-process NAME` | SIGKILL at once, stays down | SIGSTOP from the Kind node (`crictl inspect` gives the PID), scale to 0, wait until the pod is terminating (at most 30 s), SIGKILL from the node, wait; stays down. Refused before the scale-down when the pods cannot be listed, a running pod shows no container ID or the PID is not a running process |
| `stop-processes`, `restart` | every process | every Deployment |
| `standins-sync` | nothing | ConfigMap `provisioning-standins` from the host files; wait until every running pod that mounts it reads the same SHA-256 |
| `status` | pid files | Deployments and pods |
| `operator …` | on the host | on the host (it is a command, not a process), with the published ports |
| `stop` | the processes, the containers, the generated files | `helm uninstall`, then what Helm leaves (the hooks: Job and CiliumNetworkPolicy `provisioning-migrate`), the Secrets and ConfigMaps above, the follower; then as on the host |

The bootstrap has no supervisor in cluster mode: the kubelet restarts it (`restartPolicy: Always`,
CrashLoopBackOff 10 s doubling to 5 min).

## Identities (decision 0016 section 9 ruling 2)

- Every pod sets `automountServiceAccountToken: false`; every host exits 2 if a default token is mounted.
- The worker and the bootstrap run under `provisioning-worker` and `tenant-bootstrap` (the platform
  layer's, unchanged) with one projected token at `/var/run/secrets/webblock/kube/token` that expires
  after 600 s; the library reads it again every minute. At start a SelfSubjectReview must return
  `KUBE_EXPECTED_USERNAME`, or the process exits 1.
- The API and the build worker run under their own token-less ServiceAccounts; the Job under the
  namespace's default ServiceAccount, without a token.
- `make proto-tokens` and the two kubeconfigs stay: host mode, `proto-test-kind` and the harness use them.

## Stand-ins (decision 0016 section 9 ruling 3)

`access.json`, `entitlement.json` and `repositories.json` stay on the host in `bin/kind-state/proto/provisioning/`
(the harness writes them) and reach the pods as ConfigMap `provisioning-standins`, mounted whole at
`/etc/webblock/standins/` in the API, the worker and the build worker. The kubelet swaps the files
atomically; the processes' reload sees a new inode and mtime. `deploy/kind/cluster.yaml` sets
`syncFrequency: 5s`.

## Logs

`scripts/prototype/follow_logs.py` (pid in `pids/log-follower.pid`, its own output in
`log-follower.out`) copies each container instance's log into `logs/<run>/<process>.log`, one file per
process for the whole run, across restarts and new pods; the migration Job's log goes to `migrate.log`.
The log shipper sends them to Loki as in host mode (`make proto-logs`). The harness waits 3 s before it
reads a log in cluster mode, the follower's lag.

## Network policy

One default-deny `CiliumNetworkPolicy` per pod (`templates/networkpolicies.yaml`):

| Pod | In | Out |
| --- | --- | --- |
| api | 8080 from all; 8081 from the host (probes) | DNS; PostgreSQL |
| worker | 9090 from the host | DNS; PostgreSQL; RabbitMQ; the API server; the Gateway's Envoy and tenant pods on 8080 (the promotion probe) |
| build worker | 9090 from the host | DNS; PostgreSQL; RabbitMQ; Jenkins |
| bootstrap | 9090 from the host | DNS; PostgreSQL; the catalog; the API server |
| migrate | none | DNS; PostgreSQL |

The migrate policy is a Helm hook (created before the Job), so `helm uninstall` leaves it; the run
script's `stop` deletes it.

To see a drop: `bash -c '. deploy/kind/proto-lib.sh; k -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg
monitor --type drop'`.

## Resources

Each container's memory request equals its limit; CPU has a request and no limit
(`deploy/helm/provisioning/values.yaml`, pinned by `tests/chart/test_chart.py`):

| Container | Memory (request = limit) | CPU request |
| --- | --- | --- |
| api | 256Mi | 100m |
| worker | 384Mi | 100m |
| build worker | 256Mi | 100m |
| bootstrap | 256Mi | 100m |
| migration Job | 64Mi | 50m |

These are PK-design's estimates, with one change. The PK plan's decision 18 changes a number only after a
container is `OOMKilled`. The bootstrap's 192Mi is 256Mi by the owner's decision,
as an override of that rule: PK-V finding I-1 (`ai/reviews/pk-validation.md`) saw the bootstrap reach
`memory.peak` 201289728 B, 36 KiB under its 192Mi limit, with direct reclaim but no OOM kill, after the
full cluster e2e (43 tenants). The bootstrap is one replica with `Recreate`, so an OOM kill would stop
tenant creation until it restarts. The other containers peaked at 53% to 59% of their limits.

## The image and its pins

- Base: `deploy/image/versions.sh` (`BASE_IMAGE`, `BASE_IMAGE_PUBLISHED`), checked by `make py-age`.
- **Moving the base image.** `make py-age` trusts `BASE_IMAGE_PUBLISHED` as it is written and looks
  nothing up, and nothing ties that date to the digest beside it. So the publication date is read again
  for the new digest, at the time of the move, and never kept from the old pin: choose a tag whose
  Docker Hub `last_updated` is at least 14 days ago,
  `curl -sS https://hub.docker.com/v2/repositories/library/python/tags/<tag> | python3 -I -c 'import json,sys;
  t=json.load(sys.stdin); print(t["digest"], t["last_updated"])'`,
  confirm that the digest it prints is the one `bin/crane digest docker.io/library/python:<tag>` gives
  (HOME and DOCKER_CONFIG in a temporary folder), take the later of `last_updated` and the linux/amd64
  config's `created` (`bin/crane config`), and record `BASE_IMAGE` and `BASE_IMAGE_PUBLISHED` in one
  change, then run `make py-age`. When the tag has moved on since, its `last_updated` belongs to another
  digest: choose another tag rather than reuse that date.
- Builder: `bin/docker-buildx` (pin in `deploy/kind/versions.sh`) through `DOCKER_CONFIG=bin/kind-state/docker-cli`;
  the legacy builder when it is missing. The stack's BuildKit is never used for this image.
- Scan: the pushed image by reference (step 6 above). A High finding in the base without a fix gets an
  exception of at most 30 days in `scripts/py/image-scan-exceptions.toml`, signed off by the owner.

## Known limits

- The replica suite (`make proto-run-replicas proto-e2e-replicas`) and `PROJECT_ACCESS_MODE=project-service` run on the
  host only.
- The data containers on the `kind` network are reachable from the tenant builder (BuildKit) with credentials (OD-37,
  OD-62), as the catalog already was.
- Grype does not see the `libpq` and OpenSSL bundled in the `psycopg[binary]` wheel (decision 0016 section 9 ruling 4).
- The log follower (`scripts/prototype/follow_logs.py`) can lose or mix lines in three cases, none
  expected with the processes' JSON logs on a local cluster: a `--follow` stream that ends while the
  container still runs is not resumed (the instance counts as seen); a line longer than 1 MiB ends that
  instance's copy (the error goes to `log-follower.out`), so the rest of its log is missing; and at start,
  the `--previous` and `--follow` copies of one container run at once and may interleave in one file.
- A Kind pass proves nothing about GKE, DOKS, a cloud load balancer or production TLS.
