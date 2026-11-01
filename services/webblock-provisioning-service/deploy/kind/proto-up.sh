#!/usr/bin/env bash
# Brings up the prototype stack (decision 0014) from nothing, or continues on a kept one, and waits on
# every component with a bounded timeout. No Crossplane, no composition function and no XRDs; the
# admission policies of deploy/prototype/policy limit the bootstrap identity and the release worker and
# say who writes in a tenant, and a Cilium policy confines site pods (0014 sections 17, 21 and 23).
# Engine (spike design .claude/docs/designs/k3s-site-cluster-spike.md G2 and G5): Kind by default;
# make proto-up PROTO_ENGINE=k3d runs the same steps on k3d (k3s in Docker), with the replacements
# marked "k3d:" below. proto-up records the engine in bin/kind-state/proto/engine and every other
# script reads it. Both engines join the Docker network kind, so every other container keeps its name
# and address.
# Steps:
#    0. memory and one-cluster rule (either engine)
#    1. local registry webblock-proto-registry (TLS, project-local CA) and the image mirror by digest
#    2. Kind cluster webblock-proto (deploy/kind/cluster.yaml) and the nodes' registry wiring
#       k3d: k3d cluster webblock-proto, one server (tainted NoSchedule like Kind's control plane) and
#       one agent, API on 127.0.0.1 only, registry trust by registries.yaml (deploy/kind/k3d)
#    3. Gateway API v1.6.1 standard CRDs (Cilium 1.20's minimum; task T1)
#       k3d: k3s's traefik-crd chart owns them; used when they are v1.6.1 or newer (one owner per CRD)
#    4. Cilium 1.20 from the local registry (kube-proxy replacement, Gateway API, LB-IPAM + L2)
#       k3d: k3s's Traefik with deploy/kind/k3d/traefik-config.yaml, GatewayClass traefik
#   4b. cert-manager and the local CA ClusterIssuer webblock-local-ca (proto-certs.sh; task T1)
#    5. LB-IPAM pool, the wildcard certificate of *.sites.webblock.test and the shared Gateway
#       webblock-gateway/webblock-public with HTTP (80) and HTTPS (443) listeners, routes from tenant
#       namespaces only, no ListenerSets (deploy/prototype/gateway; 0014 section 23)
#       k3d: no pool (k3s ServiceLB); 20-gateway-traefik.yaml (listener ports 8000 and 8443, served as
#       80 and 443); the address is the Traefik Service's load balancer address
#    6. prototype RBAC (deploy/prototype/rbac) and the offline page in webblock-offline
#    7. worker and bootstrap kubeconfigs (proto-tokens.sh)
#    8. Git server, BuildKit and Jenkins on the kind Docker network (proto-ci.sh)
#   8b. the shared catalog database webblock-catalog-db with the Project service's catalog schema,
#       applied by Flyway from PROJECT_SERVICE_DIR (proto-catalog.sh; decision 0017 section 1)
#   8c. the admission policies and the tenant network policy (proto-policy.sh; after 8b, because the
#       network policy names the catalog's address). k3d: the admission policies only; the tenant
#       NetworkPolicy (G8) is applied by its live test to test tenants
#   8d. Loki and the log shipper for the Provisioning processes' JSON logs (proto-loki.sh; 0014
#       section 23 item 5)
#    9. bin/kind-state/proto/stack.env with the stack's non-secret addresses
# On failure everything is removed (proto-down.sh) unless KEEP_CLUSTER=1. Environment:
#   PROTO_ENGINE            kind (default) or k3d; refused while the other engine's stack is recorded
#   KEEP_CLUSTER=1          keep everything after a failure
#   KIND_MIN_AVAILABLE_MB   memory that must be available before the cluster is created, default 7000
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

started="$(date +%s)"
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    if cluster_exists && [ -f "$KCFG" ] && (require_local_cluster) >/dev/null 2>&1; then
      log "diagnostics: pods not Running or not ready"
      k get pods -A --no-headers 2>/dev/null | awk '{ split($3, r, "/") } $4 != "Running" || r[1] != r[2]' | head -n 30 || true
      log "diagnostics: latest Warning events"
      k get events -A --field-selector type=Warning --sort-by=.lastTimestamp \
        -o custom-columns=NS:.metadata.namespace,OBJECT:.involvedObject.name,REASON:.reason,MESSAGE:.message --no-headers 2>/dev/null \
        | tail -n 15 || true
    fi
    if [ "${KEEP_CLUSTER:-0}" = 1 ]; then
      log "proto-up failed (exit $rc); KEEP_CLUSTER=1, so everything is kept. Remove it with make proto-down."
    else
      log "proto-up failed (exit $rc); removing the prototype stack (set KEEP_CLUSTER=1 to keep it)"
      bash "$kind_dir/proto-down.sh" || log "proto-down after the failure also failed; check docker ps"
    fi
  fi
}
require_tools
[ "$ENGINE" = kind ] || { [ -x "$K3D" ] && [ -f "$K3D" ]; } || die "$K3D is missing: run make kind-tools"
command -v openssl >/dev/null 2>&1 || die "openssl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
# Before the trap: a start refused here (another cluster runs) removes nothing of that other stack.
require_no_other_cluster
printf '%s\n' "$ENGINE" >"$ENGINE_FILE"
trap on_exit EXIT

# --- 0. memory rule, before anything is started ------------------------------------------------------
create_cluster=0
if ! cluster_exists; then
  create_cluster=1
  min_mb="${KIND_MIN_AVAILABLE_MB:-7000}"
  avail_mb=$(( $(awk '/^MemAvailable:/ {print $2}' /proc/meminfo) / 1024 ))
  if [ "$avail_mb" -lt "$min_mb" ]; then
    die "only ${avail_mb} MB of memory available, at least ${min_mb} MB needed to create the prototype stack"
  fi
  [ "$ENGINE" = k3d ] || grep -qF "image: ${NODE_IMAGE}" "$kind_dir/cluster.yaml" || die "cluster.yaml does not pin $NODE_IMAGE"
fi
# The prototype pins its own Gateway API, Cilium and cert-manager (deploy/prototype/versions.sh, task
# T1). A cluster made before that runs older CRDs and a Cilium operator that never saw ListenerSet.
if [ "$create_cluster" = 0 ] && [ -f "$KCFG" ] && (require_local_cluster) >/dev/null 2>&1; then
  have="$(k get crd gateways.gateway.networking.k8s.io -o jsonpath='{.metadata.annotations.gateway\.networking\.k8s\.io/bundle-version}' 2>/dev/null || true)"
  [ -z "$have" ] || [ "$have" = "$PROTO_GATEWAY_API_VERSION" ] \
    || die "cluster $CLUSTER runs Gateway API $have, not $PROTO_GATEWAY_API_VERSION; run make proto-down, then make proto-up"
fi

# --- 1. local registry and the image mirror -------------------------------------------------------
ensure_registry_tls
# The local CA of ClusterIssuer webblock-local-ca (task T1), made before the cluster exists: it may
# not be replaced while a cluster uses it (proto-lib.sh). proto-certs.sh installs it in step 4b.
ensure_local_ca
if [ ! -d "$REGISTRY_DATA" ] && [ -d "$STATE/registry-data/docker" ]; then
  # First prototype run on a machine that ran the Phase 4 stack (now on branch go-reference): start
  # from its pull cache (a copy, reflinked where the file system allows), so the Cilium images are not
  # fetched from quay.io again.
  log "registry: seeding $REGISTRY_DATA from the Phase 4 pull cache"
  cp -a --reflink=auto "$STATE/registry-data" "$REGISTRY_DATA"
fi
mkdir -p "$REGISTRY_DATA"
if docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
  [ "$(docker container inspect --format '{{.Config.Image}}' "$REGISTRY_NAME")" = "$REGISTRY_IMAGE" ] \
    || die "container $REGISTRY_NAME does not run the pinned $REGISTRY_IMAGE; run make proto-down"
  [ "$(docker container inspect --format '{{.State.Running}}' "$REGISTRY_NAME")" = true ] || docker start "$REGISTRY_NAME" >/dev/null
  log "registry: reusing $REGISTRY_NAME"
else
  if docker ps --format '{{.Ports}}' | grep -qF "127.0.0.1:$REGISTRY_PORT->"; then
    die "port 127.0.0.1:$REGISTRY_PORT is taken by another container (the Phase 4 registry? remove it with make kind-down on branch go-reference)"
  fi
  log "registry: starting $REGISTRY_NAME on https://127.0.0.1:$REGISTRY_PORT"
  docker run -d --restart=no -p "127.0.0.1:$REGISTRY_PORT:5000" --network bridge --user "$(id -u):$(id -g)" \
    -v "$REGISTRY_TLS/tls.crt:/certs/tls.crt:ro" -v "$REGISTRY_TLS/tls.key:/certs/tls.key:ro" \
    -v "$REGISTRY_DATA:/var/lib/registry" \
    -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/tls.crt -e REGISTRY_HTTP_TLS_KEY=/certs/tls.key \
    --label io.webblock.proto=registry --name "$REGISTRY_NAME" "$REGISTRY_IMAGE" >/dev/null
fi
poll 60 "registry answering on https://127.0.0.1:$REGISTRY_PORT" \
  curl -sf --max-time 3 --cacert "$REGISTRY_TLS/ca.crt" -o /dev/null "https://127.0.0.1:$REGISTRY_PORT/v2/"

mlog="$PROTO/mirror.log"
for ref in "${PROTO_MIRROR_IMAGES[@]}"; do
  printf '%s' "$ref" | grep -Eq '^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$' \
    || die "mirror entry is not host/path:tag@sha256:<64 hex>: $ref"
  name="${ref%@*}"; digest="${ref##*@}"; path="${name#*/}"; tag="${path##*:}"; path="${path%:*}"
  dst="$REGISTRY_LOCAL/$path"
  if [ "$(registry_digest "$dst@$digest")" != "$digest" ]; then
    n=1
    until timeout 900 env SSL_CERT_DIR="$REGISTRY_TLS/trust" DOCKER_CONFIG="$CRANE_DOCKER_CONFIG" HOME="$TOOLHOME" \
        "$CRANE" copy "${ref%%/*}/$path@$digest" "$dst:$tag" >>"$mlog" 2>&1; do
      [ "$n" -lt 5 ] || die "mirror: copying $ref failed 5 times (see $mlog)"
      log "mirror: attempt $n for $ref failed; retrying in 30 s"
      n=$((n + 1)); sleep 30
    done
    log "mirror: copied $ref"
  fi
  [ "$(registry_digest "$dst:$tag")" = "$digest" ] || crane_ tag "$dst@$digest" "$tag" >>"$mlog" 2>&1 \
    || die "mirror: cannot tag $dst@$digest as $tag"
  [ "$(registry_digest "$dst:$tag")" = "$digest" ] || die "mirror: the registry does not serve $digest for $dst:$tag"
done
log "mirror: ${#PROTO_MIRROR_IMAGES[@]} images by digest in the local registry"

# --- 2. the cluster and the registry wiring ------------------------------------------------------------
# k3d_kubeconfig: the admin kubeconfig of the k3d cluster, written only to $KCFG (through a new file
# beside it); k3d never touches ~/.kube/config (k3d_ points HOME and KUBECONFIG into $PROTO).
k3d_kubeconfig() {
  local tmp="$PROTO/.kubeconfig.k3d.$$"
  rm -f "$tmp"
  k3d_ kubeconfig merge "$CLUSTER" --output "$tmp" >/dev/null
  chmod 0600 "$tmp"
  mv "$tmp" "$KCFG"
  kc config use-context "$CONTEXT" >/dev/null
}
# free_port: a TCP port on 127.0.0.1 that is free now (the k3d API port; Kind picks its own).
free_port() {
  python3 -I -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}
if [ "$create_cluster" = 0 ]; then
  log "cluster: $CLUSTER exists; reusing it"
  case "$ENGINE" in
    kind) kind_ export kubeconfig --name "$CLUSTER" --kubeconfig "$KCFG" ;;
    k3d) k3d_kubeconfig ;;
  esac
elif [ "$ENGINE" = k3d ]; then
  docker network inspect kind >/dev/null 2>&1 || docker network create kind >/dev/null
  api_port="$(free_port)"
  sed -e "s#@REGISTRY_LOCAL@#$REGISTRY_LOCAL#g" -e "s#@REGISTRY_IN_CLUSTER@#$REGISTRY_IN_CLUSTER#g" \
    "$kind_dir/k3d/registries.yaml.tmpl" >"$PROTO/k3d-registries.yaml"
  log "cluster: creating k3d $CLUSTER (${avail_mb} MB available; node image $K3S_NODE_IMAGE; API 127.0.0.1:$api_port)"
  k3d_ cluster create "$CLUSTER" --image "$K3S_NODE_IMAGE" --servers 1 --agents 1 \
    --api-port "127.0.0.1:$api_port" --network kind \
    --kubeconfig-update-default=false --kubeconfig-switch-context=false \
    --registry-config "$PROTO/k3d-registries.yaml" \
    --volume "$REGISTRY_TLS/ca.crt:/etc/webblock/registry-ca.crt@server:*;agent:*" \
    --volume "$kind_dir/k3d/traefik-config.yaml:/var/lib/rancher/k3s/server/manifests/traefik-config.yaml@server:0" \
    --k3s-arg "--disable=metrics-server@server:0" \
    --k3s-arg "--node-taint=node-role.kubernetes.io/control-plane:NoSchedule@server:0" \
    --wait --timeout 300s
  k3d_kubeconfig
else
  log "cluster: creating $CLUSTER (${avail_mb} MB available; node image $NODE_IMAGE)"
  kind_ create cluster --name "$CLUSTER" --config "$kind_dir/cluster.yaml" --kubeconfig "$KCFG" --wait 0s
fi
chmod 0600 "$KCFG"
require_local_cluster
log "cluster: $ENGINE context $CONTEXT at $(kc config view --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.server}')"

reg_aliases="$(docker container inspect --format '{{with index .NetworkSettings.Networks "kind"}}joined {{range .Aliases}}{{.}} {{end}}{{end}}' "$REGISTRY_NAME")"
case " $reg_aliases " in
  *" $REGISTRY_ALIAS "*) ;;
  *" joined "*) docker network disconnect kind "$REGISTRY_NAME"; docker network connect --alias "$REGISTRY_ALIAS" kind "$REGISTRY_NAME" ;;
  *) docker network connect --alias "$REGISTRY_ALIAS" kind "$REGISTRY_NAME" ;;
esac
for node in $(node_containers); do
  if [ "$ENGINE" = k3d ]; then
    # k3s reads /etc/rancher/k3s/registries.yaml (k3d --registry-config) and the mounted CA.
    docker exec "$node" grep -qF "https://$REGISTRY_IN_CLUSTER" /etc/rancher/k3s/registries.yaml \
      || die "node $node: /etc/rancher/k3s/registries.yaml does not name https://$REGISTRY_IN_CLUSTER"
    docker exec "$node" test -s /etc/webblock/registry-ca.crt || die "node $node: the registry CA is not mounted"
    continue
  fi
  docker exec "$node" getent hosts "$REGISTRY_ALIAS" >/dev/null || die "node $node cannot resolve $REGISTRY_ALIAS"
  docker exec "$node" containerd config dump 2>/dev/null \
    | grep -A1 -F "[plugins.'io.containerd.cri.v1.images'.registry]" \
    | grep -Eq "config_path = [\"']/etc/containerd/certs.d[\"']" \
    || die "node $node: containerd's effective registry config_path is not /etc/containerd/certs.d"
  # localhost:5001 (Cilium values, the offline page) and webblock-proto-registry.test:5000 (images that
  # Jenkins pushes) both go to the registry container over HTTPS, verified against the project-local CA.
  for host in "$REGISTRY_LOCAL" "$REGISTRY_IN_CLUSTER"; do
    d="/etc/containerd/certs.d/$host"
    docker exec "$node" mkdir -p "$d"
    docker exec -i "$node" cp /dev/stdin "$d/ca.crt" <"$REGISTRY_TLS/ca.crt"
    printf 'server = "https://%s"\n\n[host."https://%s"]\n  capabilities = ["pull", "resolve"]\n  ca = "%s/ca.crt"\n' \
      "$REGISTRY_IN_CLUSTER" "$REGISTRY_IN_CLUSTER" "$d" \
      | docker exec -i "$node" cp /dev/stdin "$d/hosts.toml"
  done
done
log "registry: $REGISTRY_LOCAL and $REGISTRY_IN_CLUSTER resolve to https://$REGISTRY_IN_CLUSTER on every node"

# --- 3. Gateway API standard CRDs ----------------------------------------------------------------------
# Before Cilium: its operator enables ListenerSet support only when the CRD exists at its start.
# k3d: k3s's chart traefik-crd installs Gateway API CRDs itself (with forceConflicts), so the pinned
# bundle is applied only when it installed none; a CRD never has two owners. Its version must be
# $PROTO_GATEWAY_API_VERSION or newer.
version_at_least() {
  python3 -I -c '
import re, sys
def parts(v):
    m = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)", v)
    if m is None:
        sys.exit(2)
    return tuple(int(x) for x in m.groups())
sys.exit(0 if parts(sys.argv[1]) >= parts(sys.argv[2]) else 1)
' "$1" "$2"
}
gw_bundle() { k get crd gateways.gateway.networking.k8s.io -o jsonpath='{.metadata.annotations.gateway\.networking\.k8s\.io/bundle-version}' 2>/dev/null; }
traefik_crds_done() { [ "$(k -n kube-system get job helm-install-traefik-crd -o jsonpath='{.status.succeeded}' 2>/dev/null)" = 1 ]; }
gw_source=pinned
if [ "$ENGINE" = k3d ]; then
  poll 300 "k3s chart traefik-crd installed (job helm-install-traefik-crd succeeded)" traefik_crds_done
  have="$(gw_bundle || true)"
  if [ -n "$have" ]; then
    version_at_least "$have" "$PROTO_GATEWAY_API_VERSION" \
      || die "k3s installed Gateway API $have, older than $PROTO_GATEWAY_API_VERSION; the pinned bundle cannot take over its CRDs"
    gw_source="k3s traefik-crd, $have $(k get crd gateways.gateway.networking.k8s.io -o jsonpath='{.metadata.annotations.gateway\.networking\.k8s\.io/channel}')"
  fi
fi
if [ "$gw_source" = pinned ]; then
  gw_crds="$(fetch_verified "$PROTO_GATEWAY_API_URL" "$PROTO_GATEWAY_API_SHA256" "gateway-api-$PROTO_GATEWAY_API_VERSION-standard-install.yaml")"
  k apply --server-side -f "$gw_crds" >/dev/null
  gw_source="pinned $PROTO_GATEWAY_API_VERSION"
fi
k wait --for condition=Established --timeout=120s \
  crd/gatewayclasses.gateway.networking.k8s.io crd/gateways.gateway.networking.k8s.io \
  crd/httproutes.gateway.networking.k8s.io crd/referencegrants.gateway.networking.k8s.io \
  crd/listenersets.gateway.networking.k8s.io crd/tlsroutes.gateway.networking.k8s.io \
  crd/grpcroutes.gateway.networking.k8s.io crd/backendtlspolicies.gateway.networking.k8s.io >/dev/null
log "gateway-api: CRDs established ($gw_source), ListenerSet included"

# --- 4. Cilium (k3d: Traefik) ---------------------------------------------------------------------------
if [ "$ENGINE" = k3d ]; then
  # k3s installs Traefik with deploy/kind/k3d/traefik-config.yaml merged in; the provider flags in the
  # Deployment show that the values took effect (helm-controller upgrades Traefik if it ran without them).
  traefik_args() { k -n kube-system get deployment traefik -o jsonpath='{.spec.template.spec.containers[0].args}' 2>/dev/null; }
  traefik_configured() {
    local args
    args="$(traefik_args)" || return 1
    printf '%s' "$args" | grep -qF -- '--providers.kubernetesgateway' \
      && ! printf '%s' "$args" | grep -qF -- '--providers.kubernetesingress' \
      && ! printf '%s' "$args" | grep -qF -- '--providers.kubernetescrd'
  }
  k wait --for condition=Ready node --all --timeout=300s
  k -n kube-system rollout status deployment/coredns --timeout=300s
  poll 300 "deployment traefik with the Gateway API provider only (traefik-config.yaml applied)" traefik_configured
  k -n kube-system rollout status deployment/traefik --timeout=300s
  gatewayclass_made() { k get gatewayclass traefik >/dev/null 2>&1; }
  poll 120 "GatewayClass traefik" gatewayclass_made
  k wait --for condition=Accepted gatewayclass/traefik --timeout=180s >/dev/null
  log "traefik: healthy; $(k -n kube-system get deployment traefik -o jsonpath='{.spec.template.spec.containers[0].image}'); GatewayClass traefik Accepted"
else
# Kind: Cilium.
cilium_chart="$(fetch_verified "$PROTO_CILIUM_CHART_URL" "$PROTO_CILIUM_CHART_SHA256" "cilium-$PROTO_CILIUM_VERSION.tgz")"
cilium_images() {
  HOME="$TOOLHOME" "$HELM" template cilium "$cilium_chart" --namespace kube-system --values "$kind_dir/values/cilium.yaml" "$@" \
    --set "k8sServiceHost=$CLUSTER-control-plane" \
    | sed -nE 's/^[[:space:]-]*image:[[:space:]]*"?([^"[:space:]]+)"?[[:space:]]*$/\1/p' | sort -u
}
upstream_imgs="$(cilium_images)"
local_imgs="$(cilium_images --values "$kind_dir/values/cilium-local-images.yaml")"
pinned_imgs="$(printf '%s\n' "${PROTO_MIRROR_IMAGES[@]}" | grep '^quay.io/cilium/' | sort -u)"
[ -n "$upstream_imgs" ] && [ "$upstream_imgs" = "$pinned_imgs" ] \
  || die "cilium: the chart renders images [$(printf '%s ' "$upstream_imgs")] that differ from the pins [$(printf '%s ' "$pinned_imgs")]"
[ "$local_imgs" = "$(printf '%s\n' "$upstream_imgs" | sed "s#^quay.io/#$REGISTRY_LOCAL/#" | sort -u)" ] \
  || die "cilium: values/cilium-local-images.yaml changes more than the registry host"
log "cilium: installing chart $PROTO_CILIUM_VERSION from the local registry's images"
h upgrade --install cilium "$cilium_chart" --namespace kube-system \
  --values "$kind_dir/values/cilium.yaml" --values "$kind_dir/values/cilium-local-images.yaml" \
  --set "k8sServiceHost=$CLUSTER-control-plane" --timeout 10m >/dev/null
k -n kube-system rollout status daemonset/cilium --timeout=900s
k -n kube-system rollout status daemonset/cilium-envoy --timeout=600s
k -n kube-system rollout status deployment/cilium-operator --timeout=600s
k wait --for condition=Ready node --all --timeout=300s
k -n kube-system rollout status deployment/coredns --timeout=300s
if k -n kube-system get daemonset kube-proxy >/dev/null 2>&1; then die "kube-proxy is running; the cluster must not have it"; fi
kpr="$(k -n kube-system exec daemonset/cilium -c cilium-agent -- cilium-dbg status 2>/dev/null | grep -E '^KubeProxyReplacement:' || true)"
printf '%s' "$kpr" | grep -Eq '^KubeProxyReplacement:[[:space:]]+True' || die "kube-proxy replacement is not active: '$kpr'"
log "cilium: healthy; $kpr"
fi

# --- 4b. cert-manager and the local CA ------------------------------------------------------------------
bash "$kind_dir/proto-certs.sh"

# --- 5. LB-IPAM and the shared Gateway (k3d: ServiceLB) -------------------------------------------------
if [ "$ENGINE" = k3d ]; then
  gateway_file="$proto_dir/gateway/20-gateway-traefik.yaml"
else
# Kind: Cilium's LB-IPAM pool and GatewayClass cilium.
gateway_file="$proto_dir/gateway/20-gateway.yaml"
subnet="$(kind_network_subnet)"
[ -n "$subnet" ] || die "cannot read the IPv4 subnet of the Docker network kind"
prefix="${subnet#*/}"
[ "$prefix" -le 24 ] || die "Kind network $subnet is smaller than /24; no room for an LB range"
IFS=. read -r o1 o2 o3 o4 <<<"${subnet%/*}"
net=$(( (o1 << 24) | (o2 << 16) | (o3 << 8) | o4 ))
bcast=$(( net | ((1 << (32 - prefix)) - 1) ))
ip4() { printf '%d.%d.%d.%d' $(( ($1 >> 24) & 255 )) $(( ($1 >> 16) & 255 )) $(( ($1 >> 8) & 255 )) $(( $1 & 255 )); }
lb_start="$(ip4 $(( bcast - 55 )))"; lb_stop="$(ip4 $(( bcast - 6 )))"
k wait --for condition=Established --timeout=120s \
  crd/ciliumloadbalancerippools.cilium.io crd/ciliuml2announcementpolicies.cilium.io >/dev/null
sed -e "s/@LB_START@/$lb_start/" -e "s/@LB_STOP@/$lb_stop/" "$kind_dir/manifests/lb-ipam.yaml.tmpl" | k apply -f - >/dev/null
k wait --for condition=Accepted gatewayclass/cilium --timeout=180s >/dev/null
fi
k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/gateway/00-namespace.yaml" >/dev/null
k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/gateway/10-wildcard-certificate.yaml" >/dev/null
k -n webblock-gateway wait --for condition=Ready certificate/sites-wildcard --timeout=120s >/dev/null
k apply --server-side --field-manager=webblock-proto-up -f "$gateway_file" >/dev/null
k -n webblock-gateway wait --for condition=Programmed gateway/webblock-public --timeout=300s >/dev/null
# Programmed on the Gateway says nothing about each listener; both must be programmed with their refs
# resolved (the HTTPS listener's certificate Secret above).
listeners_ready() {
  local states
  states="$(k -n webblock-gateway get gateway webblock-public -o json | python3 -I -c '
import json, sys
g = json.load(sys.stdin)
gen = g["metadata"]["generation"]
for listener in g.get("status", {}).get("listeners", []):
    ok = all(any(c["type"] == t and c["status"] == "True" and c.get("observedGeneration", gen) == gen
                 for c in listener.get("conditions", [])) for t in ("Programmed", "ResolvedRefs"))
    print(listener["name"], "ok" if ok else "waiting")
' | sort | tr '\n' ' ')"
  [ "$states" = "http-sites ok https-sites ok " ]
}
poll 120 "listeners http-sites and https-sites of webblock-public Programmed and ResolvedRefs" listeners_ready
if [ "$ENGINE" = k3d ]; then
  # The Traefik Service's load balancer address (k3s ServiceLB): a node's address on network kind,
  # reachable from the host; the Service serves the entry points 8000 and 8443 as 80 and 443.
  gw_addr() {
    k -n kube-system get service traefik -o jsonpath='{range .status.loadBalancer.ingress[*]}{.ip}{"\n"}{end}' \
      | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1
  }
  gw_where="Traefik Service, k3s ServiceLB"
else
  gw_addr() { k -n webblock-gateway get gateway webblock-public -o jsonpath='{.status.addresses[0].value}'; }
  gw_where="pool $lb_start-$lb_stop"
fi
gw_has_addr() { [ -n "$(gw_addr)" ]; }
poll 120 "an address on gateway webblock-public" gw_has_addr
GATEWAY_ADDRESS="$(gw_addr)"
log "gateway: webblock-gateway/webblock-public Programmed at $GATEWAY_ADDRESS ($gw_where); HTTP on 80, HTTPS on $GATEWAY_HTTPS_PORT with the local CA's wildcard certificate"

# --- 6. prototype RBAC and the offline page ------------------------------------------------------------
k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/rbac/00-namespaces.yaml" >/dev/null
k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/rbac" >/dev/null
offline_image="$REGISTRY_LOCAL/${PROTO_NGINX_IMAGE#docker.io/}"
sed "s#@OFFLINE_IMAGE@#$offline_image#" "$proto_dir/offline/offline-page.yaml.tmpl" \
  | k apply --server-side --field-manager=webblock-proto-up -f - >/dev/null
k -n webblock-offline rollout status deployment/offline-page --timeout=180s
log "rbac: deploy/prototype/rbac applied; offline page ready in webblock-offline ($offline_image)"

# --- 7. identity kubeconfigs ------------------------------------------------------------------------------
bash "$kind_dir/proto-tokens.sh"

# --- 8. Git server, BuildKit and Jenkins -------------------------------------------------------------------
bash "$kind_dir/proto-ci.sh"

# --- 8b. the shared catalog database -------------------------------------------------------------------------
bash "$kind_dir/proto-catalog.sh"
CATALOG_PORT="$(catalog_port)"
[ -n "$CATALOG_PORT" ] || die "catalog: no published port for $PROTO_CATALOG_NAME"

# --- 8c. admission policies and the tenant network policy -----------------------------------------------------
bash "$kind_dir/proto-policy.sh"

# --- 8d. Loki and the log shipper ------------------------------------------------------------------------------
bash "$kind_dir/proto-loki.sh"

# --- 9. the stack's addresses --------------------------------------------------------------------------------
umask 077
cat >"$STACK_ENV.tmp" <<EOF
# Non-secret addresses of the prototype stack, written by make proto-up. Credentials are in
# jenkins.env, catalog-admin.env, worker.kubeconfig and bootstrap.kubeconfig beside this file.
PROTO_CLUSTER=$CLUSTER
PROTO_ENGINE=$ENGINE
PROTO_CONTEXT=$CONTEXT
PROTO_KUBECONFIG_ADMIN=$KCFG
PROTO_KUBECONFIG_WORKER=$KCFG_WORKER
PROTO_KUBECONFIG_BOOTSTRAP=$KCFG_BOOTSTRAP
PROTO_GATEWAY_ADDRESS=$GATEWAY_ADDRESS
PROTO_GATEWAY_HTTPS_URL=https://$GATEWAY_ADDRESS:$GATEWAY_HTTPS_PORT
PROTO_LOCAL_CA=$LOCAL_CA_CRT
PROTO_CLUSTER_ISSUER=$LOCAL_CA_ISSUER
PROTO_SITE_HOST_SUFFIX=sites.webblock.test
PROTO_IMAGE_REGISTRY=$REGISTRY_IN_CLUSTER
PROTO_REGISTRY_ON_HOST=$REGISTRY_LOCAL
PROTO_REGISTRY_CA=$REGISTRY_TLS/ca.crt
PROTO_OFFLINE_SERVICE=webblock-offline/offline-page:8080
PROTO_JENKINS_URL=$JENKINS_URL_HOST
PROTO_JENKINS_JOB=$PROTO_JOB
PROTO_JENKINS_ENV=$JENKINS_ENV
PROTO_GIT_URL_BASE=http://$PROTO_GIT_NAME:8080
PROTO_CATALOG_HOST_ON_HOST=127.0.0.1
PROTO_CATALOG_PORT_ON_HOST=$CATALOG_PORT
PROTO_CATALOG_IN_CLUSTER=$PROTO_CATALOG_NAME:5432
PROTO_CATALOG_DATABASE=$PROTO_CATALOG_DATABASE
PROTO_LOKI_URL=http://127.0.0.1:$PROTO_LOKI_PORT
PROTO_PROVISIONING_LOGS=$PROTO/provisioning/logs
PROTO_CATALOG_ENV=$CATALOG_ENV
EOF
mv "$STACK_ENV.tmp" "$STACK_ENV"
log "proto-up: done in $(( $(date +%s) - started ))s; addresses in $STACK_ENV"
