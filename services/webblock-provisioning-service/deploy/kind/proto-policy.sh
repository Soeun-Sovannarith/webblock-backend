#!/usr/bin/env bash
# Installs the prototype stack's admission policies and the tenant network policy (deploy/prototype/policy;
# decision 0014 sections 17 item 3, 21 ruling 1 and 23) on the running stack and waits until the API server
# and Cilium enforce them. Idempotent: server-side apply under field manager webblock-proto-up, then probes.
# Called by deploy/kind/proto-up.sh (step 8c, after the catalog database exists); safe to run alone
# against a running stack (make proto-policy).
#   1. the ConfigMap webblock/webblock-site-policy (site-params.yaml.tmpl): the image repository
#      prefix and the site domain, the parameters of webblock-site-image and webblock-site-route-shape
#   2. every ValidatingAdmissionPolicy and binding in deploy/prototype/policy/*.yaml:
#      webblock-bootstrap-scope, webblock-tenant-writer and webblock-site-{binding,fence,image,
#      route-shape,routed-release}; each must be type-checked at its generation without a warning
#   3. probes, server-side dry runs that store nothing, repeated until all hold three times in a row
#      (the API server swaps in a changed policy a moment after the apply):
#        - an out-of-scope namespace by the bootstrap identity is refused by webblock-bootstrap-scope,
#          and a tenant namespace as bootstrap renders it is admitted (task FIX-K);
#        - in a throwaway tenant namespace: a Service without the fence annotation by the worker is
#          refused by webblock-site-fence, the same Service with the fence is admitted, and bootstrap's
#          catalog-runtime Secret is admitted (webblock-tenant-writer evaluates without error)
#      A policy whose expression errors refuses everything (failurePolicy Fail), so each refusal probe has
#      an admission probe beside it.
#   4. the CiliumClusterwideNetworkPolicy webblock-tenant-network (tenant-network.yaml.tmpl), with the
#      catalog container's address on the kind network, until Cilium reports it Valid. On the k3d engine
#      there is no cluster-wide policy (kube-router enforces only namespaced NetworkPolicy): the
#      per-tenant NetworkPolicy tenant-network-k3s.yaml.tmpl is applied by its live test to the test
#      tenants only, because no FR asks for tenant isolation yet (spike design G8, Q1)
#
# Cluster safety (.claude/CLAUDE.md section 0): every kubectl call goes through k from proto-lib.sh,
# which checks the admin kubeconfig's context (<engine>-webblock-proto on https://127.0.0.1:<port>) first.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

POLICIES=(webblock-bootstrap-scope webblock-tenant-writer webblock-site-binding webblock-site-fence
  webblock-site-image webblock-site-route-shape webblock-site-routed-release)
NETWORK_POLICY=webblock-tenant-network
BOOTSTRAP_USER=system:serviceaccount:webblock:tenant-bootstrap
WORKER_USER=system:serviceaccount:webblock:provisioning-worker
PROBE_NAMESPACE=webblock-policy-probe
SITE_DOMAIN=sites.webblock.test
IMAGE_REPOSITORY_PREFIX="$REGISTRY_IN_CLUSTER/webblock/sites/"

require_tools
cluster_exists || die "cluster $CLUSTER does not exist: run make proto-up"
require_local_cluster
docker container inspect "$PROTO_CATALOG_NAME" >/dev/null 2>&1 \
  || die "the catalog container $PROTO_CATALOG_NAME does not exist; the network policy needs its address"

# --- 1. parameters -------------------------------------------------------------------------------------------
[[ $IMAGE_REPOSITORY_PREFIX =~ ^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9/._-]+/$ ]] \
  || die "the image repository prefix is not <registry>/<path>/"
sed -e "s#@IMAGE_REPOSITORY_PREFIX@#$IMAGE_REPOSITORY_PREFIX#" -e "s#@SITE_DOMAIN@#$SITE_DOMAIN#" \
  "$proto_dir/policy/site-params.yaml.tmpl" | k apply --server-side --field-manager=webblock-proto-up -f - >/dev/null

# --- 2. policies and bindings ------------------------------------------------------------------------------------
k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/policy" >/dev/null
policy_observed() {
  local gen observed
  gen="$(k get validatingadmissionpolicy "$1" -o jsonpath='{.metadata.generation}')"
  observed="$(k get validatingadmissionpolicy "$1" -o jsonpath='{.status.observedGeneration}')"
  [ -n "$gen" ] && [ "$gen" = "$observed" ]
}
for policy in "${POLICIES[@]}"; do
  k get validatingadmissionpolicybinding "$policy" >/dev/null
  poll 60 "policy $policy type-checked (status.observedGeneration)" policy_observed "$policy"
  warnings="$(k get validatingadmissionpolicy "$policy" -o jsonpath='{.status.typeChecking.expressionWarnings}')"
  [ -z "$warnings" ] || die "policy $policy has type-checking warnings: $warnings"
done

# --- 3. probes -----------------------------------------------------------------------------------------------------
probe_out="$PROTO/.policy-probe.out"
project="$(python3 -I -c 'import uuid; print(uuid.uuid4())')"
tenant="wb-$project"
cleanup() {
  rm -f "$probe_out"
  k delete namespace "$tenant" --wait=false --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT
printf '{"apiVersion":"v1","kind":"Namespace","metadata":{"name":"%s","labels":{"websites.webblock.internal/tenant":"true","websites.webblock.internal/project-id":"%s","pod-security.kubernetes.io/enforce":"restricted"}}}\n' \
  "$tenant" "$project" | k apply --server-side --field-manager=webblock-proto-up -f - >/dev/null

refused_by() { ! "${@:2}" >"$probe_out" 2>&1 && grep -qF "ValidatingAdmissionPolicy '$1'" "$probe_out"; }
admitted() { "$@" >"$probe_out" 2>&1; }

bootstrap_refused() { refused_by webblock-bootstrap-scope k create namespace "$PROBE_NAMESPACE" --dry-run=server --as="$BOOTSTRAP_USER"; }
bootstrap_admitted() {
  local p
  p="$(python3 -I -c 'import uuid; print(uuid.uuid4())')"
  printf '{"apiVersion":"v1","kind":"Namespace","metadata":{"name":"wb-%s","labels":{"websites.webblock.internal/tenant":"true","websites.webblock.internal/project-id":"%s","websites.webblock.internal/subdomain":"policy-probe","pod-security.kubernetes.io/enforce":"restricted"}}}\n' \
    "$p" "$p" | admitted k apply --server-side --dry-run=server --field-manager=webblock-tenant-bootstrap --as="$BOOTSTRAP_USER" -f -
}
# A Service rel-<uuid> as the renderer writes it, with (FENCE=1) or without the fence annotation, by the
# worker identity with the group system:masters (RBAC then allows it in the probe namespace, and the site
# policies match the worker by name).
probe_service() {
  local release annotations=''
  release="$(python3 -I -c 'import uuid; print(uuid.uuid4())')"
  [ "$1" = 1 ] && annotations=',"annotations":{"websites.webblock.internal/command-number":"1"}'
  printf '{"apiVersion":"v1","kind":"Service","metadata":{"name":"rel-%s","namespace":"%s","labels":{"websites.webblock.internal/project-id":"%s","websites.webblock.internal/release-id":"%s"}%s},"spec":{"selector":{"websites.webblock.internal/release-id":"%s"},"ports":[{"name":"http","port":80,"targetPort":"http"}]}}\n' \
    "$release" "$tenant" "$project" "$release" "$annotations" "$release" \
    | k apply --server-side --dry-run=server --field-manager=webblock-provisioning-worker \
      --as="$WORKER_USER" --as-group=system:masters -f -
}
fence_refused() { refused_by webblock-site-fence probe_service 0; }
fence_admitted() { admitted probe_service 1; }
writer_admits_bootstrap() {
  printf '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"catalog-runtime","namespace":"%s"},"type":"Opaque","data":{"PGHOST":"cHJvYmU="}}\n' \
    "$tenant" | admitted k apply --server-side --dry-run=server --field-manager=webblock-tenant-bootstrap --as="$BOOTSTRAP_USER" -f -
}

streak=0
deadline=$(( $(date +%s) + 90 ))
while [ "$streak" -lt 3 ]; do
  if bootstrap_refused && bootstrap_admitted && fence_refused && fence_admitted && writer_admits_bootstrap; then
    streak=$((streak + 1))
  else
    streak=0
    [ "$(date +%s)" -lt "$deadline" ] \
      || die "policies: the probes did not hold within 90 s; last answer: $(cut -c1-300 "$probe_out")"
  fi
  [ "$streak" -ge 3 ] || sleep 3
done
log "policy: ${POLICIES[*]} installed and enforced"

# --- 4. the tenant network policy -------------------------------------------------------------------------------
if [ "$ENGINE" = k3d ]; then
  log "policy: k3d engine: no cluster-wide tenant network policy; the live test applies tenant-network-k3s.yaml.tmpl to its tenants"
  exit 0
fi
catalog_ip="$(docker container inspect --format '{{with index .NetworkSettings.Networks "kind"}}{{.IPAddress}}{{end}}' "$PROTO_CATALOG_NAME")"
[[ $catalog_ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "the catalog container has no IPv4 address on the kind network"
k wait --for condition=Established --timeout=60s crd/ciliumclusterwidenetworkpolicies.cilium.io >/dev/null
sed "s#@CATALOG_IP@#$catalog_ip#" "$proto_dir/policy/tenant-network.yaml.tmpl" \
  | k apply --server-side --field-manager=webblock-proto-up -f - >/dev/null
network_valid() {
  [ "$(k get ciliumclusterwidenetworkpolicy "$NETWORK_POLICY" -o jsonpath='{.status.conditions[?(@.type=="Valid")].status}')" = True ]
}
poll 60 "CiliumClusterwideNetworkPolicy $NETWORK_POLICY Valid" network_valid
log "policy: $NETWORK_POLICY installed (catalog at $catalog_ip:5432)"
