#!/usr/bin/env bash
# proto-test-kind.sh PYTHON: the live Kind suites against the running prototype stack (review finding
# L1; decision 0014 section 17 item 5). Run through make proto-test-kind, which passes the project's
# virtual environment's python.
#   1. Refuses (exit 1, never a skip) when the stack is not up: no cluster webblock-proto, a kubeconfig
#      whose context is not <engine>-webblock-proto on https://127.0.0.1:<port>, or no stack.env.
#   2. Builds the two images the suite needs through the stack's Jenkins, under one fresh project ID:
#      the sample site (variant site) and the variant whose container exits at once (never-ready), each
#      from a new commit made by scripts/prototype/sample-repo.sh, with scripts/prototype/jenkins-build.sh.
#      Each result.json must echo the request and name a digest.
#   3. Exports WEBBLOCK_KIND_SITE_IMAGE, WEBBLOCK_KIND_NEVER_READY_IMAGE (by digest) and
#      WEBBLOCK_KIND_GATEWAY_URL (http://<PROTO_GATEWAY_ADDRESS>) and runs pytest -m kind on
#      tests/integration/kubernetes, tests/integration/policy and tests/integration/stack (tasks T1 and
#      W6-K: the HTTPS listener and the release header, the per-site admission policies, the tenant
#      network policy, the worker's RBAC, Loki). pytest's exit status is the result. The tenant network
#      test is per engine: test_tenant_network_live.py proves the Cilium policy and runs on both engines
#      (on k3d it fails, a Cilium-specific test of spike design section 4); test_tenant_network_k3s_live.py
#      proves the kube-router NetworkPolicy (G8) and runs on k3d only, so on Kind it is deselected and
#      the run says so.
# Never reads .env. Every cluster call goes through proto-lib.sh's checked wrappers; the tests check the
# kubeconfig context themselves and poison HOME and KUBECONFIG.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

[ "$#" = 1 ] || { echo "usage: $0 PYTHON" >&2; exit 2; }
py="$1"
[ -x "$py" ] || die "$py is not an executable Python"

require_tools
cluster_exists || die "the prototype stack is down (no cluster $CLUSTER): run make proto-up"
require_local_cluster
for f in "$KCFG_WORKER" "$KCFG_BOOTSTRAP"; do require_local_kubeconfig "$f"; done
[ -f "$STACK_ENV" ] || die "$STACK_ENV is missing: run make proto-up"
k get --raw /readyz >/dev/null || die "the API server of $CLUSTER does not answer"
gateway="$(sed -n 's/^PROTO_GATEWAY_ADDRESS=//p' "$STACK_ENV")"
[[ $gateway =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "PROTO_GATEWAY_ADDRESS in $STACK_ENV is not an IPv4 address"

new_uuid() { python3 -I -c 'import uuid; print(uuid.uuid4())'; }
project="$(new_uuid)"
repository="$REGISTRY_IN_CLUSTER/webblock/sites/$project"
work="$(mktemp -d "$PROTO/.test-kind.XXXXXX")"
trap 'rm -rf "$work"' EXIT

# build VARIANT: prints <repository>@sha256:<digest> for a fresh commit of that sample variant.
build() {
  local variant="$1" release sha digest
  release="$(new_uuid)"
  sha="$(bash "$kind_root/scripts/prototype/sample-repo.sh" kind-suite "$variant" "kind-$variant")"
  bash "$kind_root/scripts/prototype/jenkins-build.sh" "$project" "$release" 1 \
    "http://$PROTO_GIT_NAME:8080/kind-suite.git" "$sha" "$repository" >"$work/$variant.json"
  [ -z "$(json_get "$work/$variant.json" failure_code)" ] || die "build of $variant failed: $(cat "$work/$variant.json")"
  [ "$(json_get "$work/$variant.json" project_id)" = "$project" ] || die "$variant: result.json project_id differs"
  [ "$(json_get "$work/$variant.json" release_id)" = "$release" ] || die "$variant: result.json release_id differs"
  [ "$(json_get "$work/$variant.json" commit_sha)" = "$sha" ] || die "$variant: result.json commit_sha differs"
  digest="$(json_get "$work/$variant.json" image_digest)"
  [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || die "$variant: result.json image_digest is '$digest'"
  [ "$(registry_digest "$REGISTRY_LOCAL/webblock/sites/$project@$digest")" = "$digest" ] \
    || die "$variant: the registry does not serve $digest"
  printf '%s@%s\n' "$repository" "$digest"
}

log "proto-test-kind: building the site and never-ready images for project $project through Jenkins"
site_image="$(build site)"
never_ready_image="$(build never-ready)"
log "proto-test-kind: site $site_image"
log "proto-test-kind: never-ready $never_ready_image"

export WEBBLOCK_KIND_SITE_IMAGE="$site_image"
export WEBBLOCK_KIND_NEVER_READY_IMAGE="$never_ready_image"
export WEBBLOCK_KIND_GATEWAY_URL="http://$gateway"
# proto-lib.sh exported the admin kubeconfig for its own wrappers; the tests get none.
export KUBECONFIG=/nonexistent/webblock-tests-never-read-a-default-kubeconfig
unset KUBECACHEDIR
cd "$kind_root"
engine_only=()
if [ "$ENGINE" = kind ]; then
  engine_only=(--ignore=tests/integration/stack/test_tenant_network_k3s_live.py)
  log "proto-test-kind: Kind engine: tests/integration/stack/test_tenant_network_k3s_live.py deselected (k3d only)"
fi
"$py" -X dev -m pytest -m kind tests/integration/kubernetes tests/integration/policy tests/integration/stack \
  "${engine_only[@]}"
