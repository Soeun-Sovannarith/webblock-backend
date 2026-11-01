---
name: kind-slot-run
description: Use when the owner gives a Kind slot and asks for the full local validation of Provisioning on the
  prototype Kind stack (proto-up, proto-test-kind, proto-run, proto-e2e).
disable-model-invocation: true
---

# Full validation on the local Kind stack

The only cluster allowed is the project's own Kind cluster `webblock-proto` on `127.0.0.1` (CLAUDE.md section 0).
Never touch DOKS or any other cluster, never read `~/.kube`, never set `KUBECONFIG` outside the project. The
`proto-*` targets use the project-local kubeconfig in `bin/kind-state/proto/` and check the context themselves.

Runbooks: `.claude/docs/runbooks/prototype-stack.md` (stack), `prototype-demo.md` (run, e2e, demo, reset),
`prototype-in-cluster.md` (`PROV_MODE=cluster`), `replicas.md` (two replicas). Targets: `mk/prototype.mk`.

## 1. Preconditions (stop and tell the owner if one fails)

- The owner gave this slot. One Kind stack at a time on the machine, across all worktrees.
- `free -m`: about 7000 MB available (`proto-up` refuses below `KIND_MIN_AVAILABLE_MB`, default 7000).
- No Testcontainers suite is running (`docker ps`: no `testcontainers-ryuk` container and no pytest-started
  `postgres:18` or `rabbitmq` container). `py-test-integration` must never run next to the stack.
- `make kind-tools py-tools` done once; `../project-service` present (`PROJECT_SERVICE_DIR`, catalog migrations).
- The code is final for this run (a Kind run costs about 10 minutes; do not run it on half-done work).

## 2. Run (one go, on the final code)

```sh
make proto-down; make proto-up      # fresh stack; add KEEP_CLUSTER=1 to keep a failed one for debugging
make proto-test-kind                # live Kubernetes, policy and stack suites (pytest -m kind)
make proto-run                      # Provisioning's own PostgreSQL, RabbitMQ and four processes
make proto-e2e                      # end-to-end flows on the sample site (clears finished sites first)
make proto-demo                     # optional: the clothing shop and five sites at once
```

Variants, only when the change needs them:

- In-cluster mode (chart, identities): `make proto-stop`, then `make proto-run PROV_MODE=cluster` and
  `make proto-e2e` (also runs `tests/e2e/incluster`).
- Replicas: `make proto-run-replicas` then `make proto-e2e-replicas`.
- Coverage recording: add `PY_COVER=1` to `proto-test-kind`, `py-test-catalog-live`, `py-test-jenkins`.

## 3. On a failure

1. Read the failing test output first.
2. Logs: `make proto-logs Q='{job="webblock-provisioning"} | json'` (add `SINCE=1h`, `LIMIT=1000`).
3. Too many finished sites (`Insufficient memory`): `make proto-reset-sites`.
4. Fix the cause, then rerun only the failed suite. Use the `superpowers:systematic-debugging` skill.
5. Never weaken a policy, a test or a timeout to make the run pass.

## 4. Record

- Counts per suite (passed, failed, skipped must be 0) and the commit you ran on.
- Put them in the current phase's decision record (the handoff names it) and in the handoff
  `ai/handoffs/<phase>.md`.
- Say what a local Kind pass does not prove: DOKS, GKE, cloud load balancer, production TLS, backups.

## 5. Clean up (always, also after a failure you are not debugging now)

```sh
make proto-stop proto-down
```

Then tell the owner the slot is free.
