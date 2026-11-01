# The service's Helm chart (task PK; decision 0016 sections 8 and 9), included by the Makefile with one
# line. .claude/docs/runbooks/prototype-in-cluster.md explains it.
#
#   chart-check   no cluster: bin/helm lint --strict of deploy/helm/provisioning with the fixture values,
#                 then pytest -m chart tests/chart (the rendered objects). Helm runs with HOME, its homes
#                 and --kubeconfig in a temporary folder under bin/, where no kubeconfig exists (CLAUDE.md
#                 section 0). Needs make kind-tools (bin/helm).

.PHONY: chart-check

chart-check: py-sync
	@$(py_prelude) test -x bin/helm || { echo "chart-check: bin/helm is missing; run make kind-tools" >&2; exit 2; }; \
	tmp="$$(mktemp -d bin/.chart-check.XXXXXX)"; trap 'rm -rf "$$tmp"' EXIT; \
	HOME="$$tmp" KUBECONFIG="$$tmp/never-a-kubeconfig" HELM_CONFIG_HOME="$$tmp/config" \
	  HELM_CACHE_HOME="$$tmp/cache" HELM_DATA_HOME="$$tmp/data" \
	  bin/helm lint --strict deploy/helm/provisioning --namespace webblock \
	  --kubeconfig "$$tmp/never-a-kubeconfig" --values tests/chart/values-kind.json && \
	'$(PY_BIN)/python' -X dev -m pytest -m chart tests/chart
