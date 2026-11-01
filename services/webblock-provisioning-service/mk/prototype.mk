# Prototype stack targets (decision 0014), included by the Makefile with one line.
# .claude/docs/runbooks/prototype-stack.md explains each. None reads .env. Every cluster call uses the
# project-local kubeconfigs in bin/kind-state/proto/ (deploy/kind/proto-lib.sh).
#
#   proto-up            Kind cluster webblock-proto with Cilium 1.20 and the Gateway (HTTP and HTTPS on the
#                       platform subdomains), cert-manager and the local CA ClusterIssuer
#                       webblock-local-ca (deploy/kind/proto-certs.sh), prototype RBAC, the offline page,
#                       worker and bootstrap kubeconfigs, Git server, BuildKit, Jenkins, and the shared
#                       catalog database webblock-catalog-db (deploy/kind/proto-catalog.sh), the admission
#                       and network policies (proto-policy.sh), Loki and its log shipper (proto-loki.sh)
#   proto-smoke         end-to-end check of the stack alone (build, identities, deploy, route over HTTP
#                       and HTTPS with the local CA, offline); the per-site manifests come from the worker's
#                       renderer (scripts/prototype/render-examples.py), so it needs the project's .venv
#   proto-build-checks  the Jenkins job's failure reports (commit_not_found, build_failed, invalid_parameters)
#   proto-tokens        refresh the worker and bootstrap tokens (24 hours each)
#   proto-policy        (re)install the admission policies (webblock-bootstrap-scope, webblock-tenant-writer,
#                       webblock-site-*) and the tenant network policy, and wait until they are enforced
#                       (deploy/kind/proto-policy.sh; proto-up runs it too)
#   proto-logs          query the stack's Loki: make proto-logs Q='<LogQL>' [SINCE=1h] [LIMIT=1000], for
#                       example Q='{job="webblock-provisioning"} | json | audit="true"'
#                       (deploy/kind/proto-loki-query.sh; one JSON log line per output line)
#   proto-test-kind     build the site and never-ready images through the stack's Jenkins, export
#                       WEBBLOCK_KIND_*, and run pytest -m kind (tests/integration/kubernetes,
#                       tests/integration/policy and tests/integration/stack); fails, never skips, when
#                       the stack is down
#   proto-down          remove everything proto-up started

.PHONY: proto-up proto-down proto-smoke proto-build-checks proto-tokens proto-policy proto-test-kind \
	proto-logs

proto-up:
	bash deploy/kind/proto-up.sh

proto-down:
	bash deploy/kind/proto-down.sh

proto-smoke: py-sync
	@$(py_prelude) exec bash deploy/kind/proto-smoke.sh '$(PY_BIN)/python'

proto-build-checks:
	bash scripts/prototype/build-checks.sh

proto-tokens:
	bash deploy/kind/proto-tokens.sh

proto-policy:
	bash deploy/kind/proto-policy.sh

SINCE ?= 1h
LIMIT ?= 1000
proto-logs:
	@test -n '$(Q)' || { echo "usage: make proto-logs Q='<LogQL>' [SINCE=1h] [LIMIT=1000]" >&2; exit 2; }
	@bash deploy/kind/proto-loki-query.sh --since '$(SINCE)' --limit '$(LIMIT)' '$(Q)'

proto-test-kind: py-sync
	@$(py_prelude) exec bash deploy/kind/proto-test-kind.sh '$(PY_BIN)/python'

# Task G: Provisioning's own PostgreSQL and RabbitMQ, its four processes against the running stack,
# the end-to-end suite and the demo (.claude/docs/runbooks/prototype-demo.md). None reads .env.
#   proto-run    start Provisioning (scripts/prototype/run-provisioning.sh start); needs proto-up.
#                Project access defaults to the access-file stand-in; set PROJECT_ACCESS_MODE=
#                project-service with PROJECT_SERVICE_URL to ask the Project service instead (no
#                token, authentication design A3), and PROTO_TENANT_USER_ID (PROTO_OPERATOR_USER_ID,
#                PROTO_READER_USER_ID) to give a development token its platform user ID (task PA,
#                decision 0017 section 2; .claude/docs/runbooks/prototype-stack.md).
#                PROV_MODE=cluster runs the four processes as pods of the Helm chart deploy/helm/provisioning
#                instead (task PK; .claude/docs/runbooks/prototype-in-cluster.md): it builds and pushes the image and
#                installs the chart; make passes PROV_MODE to the script like the settings above
#   proto-run-replicas  as proto-run with two API, worker and build worker processes and one
#                bootstrap (PROV_REPLICAS=2; task H5b, .claude/docs/runbooks/replicas.md)
#   proto-e2e-replicas  the replica suite (tests/e2e/replicas), against proto-run-replicas only:
#                serialization across two APIs, no duplicate work and a fenced stale worker across two
#                workers, builds across two build workers, the reconciler with two workers
#   proto-stop   stop the processes (every replica), remove Provisioning's containers and secrets
#   proto-e2e    the end-to-end flows on the sample site (tests/e2e), against proto-run; clears the
#                finished sites first (proto-reset-sites); in cluster mode also tests/e2e/incluster (the
#                identities); in host mode that folder is deselected and the run says so
#   proto-demo   the clothing shop from commit to live, its timings, and five sites at once; clears
#                the finished sites first
#   proto-reset-sites  prototype only (task W6-X): stops the Provisioning processes, deletes the
#                tenant namespaces wb-*, truncates the Provisioning tables of the prototype's own
#                container webblock-provisioning-db, purges the prototype broker's queues, starts the
#                processes again and checks that nothing came back (scripts/prototype/reset-sites.sh).
#                Every test site stays running at 256 MiB, so without it the Kind worker node
#                is full after about 60 tenant pods (finding M-2 of wave 6's validation)
.PHONY: proto-run proto-run-replicas proto-stop proto-e2e proto-e2e-replicas proto-demo proto-reset-sites

# Task PA: PROJECT_ACCESS_MODE, PROJECT_SERVICE_URL and PROTO_{TENANT,OPERATOR,READER}_USER_ID
# reach the script only when the caller sets them, on the command line or in the environment: make
# passes both to the recipe's shell as they are, quotes included. They are not exported here, so an
# unset one stays unset and the script keeps its stand-in.
proto-run:
	bash scripts/prototype/run-provisioning.sh start

proto-run-replicas:
	PROV_REPLICAS=2 bash scripts/prototype/run-provisioning.sh start

proto-stop:
	bash scripts/prototype/run-provisioning.sh stop

proto-reset-sites:
	bash scripts/prototype/reset-sites.sh

proto-e2e: py-sync proto-reset-sites
	@$(py_prelude) mode="$$(cat bin/kind-state/proto/provisioning/mode 2>/dev/null || echo host)"; \
	if [ "$$mode" = cluster ]; then incluster=; else incluster=--ignore=tests/e2e/incluster; \
	  echo "proto-e2e: host mode: tests/e2e/incluster deselected (it needs make proto-run PROV_MODE=cluster)"; fi; \
	exec '$(PY_BIN)/python' -X dev -m pytest -m e2e tests/e2e --ignore=tests/e2e/demo \
	  --ignore=tests/e2e/replicas $$incluster

proto-e2e-replicas: py-sync proto-reset-sites
	@$(py_prelude) exec '$(PY_BIN)/python' -X dev -m pytest -m e2e tests/e2e/replicas

proto-demo: py-sync proto-reset-sites
	@$(py_prelude) exec '$(PY_BIN)/python' -X dev -m pytest -m e2e tests/e2e/demo
