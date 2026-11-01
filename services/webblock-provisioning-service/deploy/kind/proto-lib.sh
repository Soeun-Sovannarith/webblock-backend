# shellcheck shell=bash
# shellcheck disable=SC2034  # a sourced library: its variables are used by the scripts that source it
# Shared settings and helpers of the prototype stack (decision 0014; deploy/kind/proto-*.sh). Sourced,
# never executed. It sources deploy/kind/lib.sh for its helpers (logging, polling, verified downloads,
# the registry certificate, crane) and then moves every piece of state to bin/kind-state/proto/, so the
# prototype never reads or writes the kubeconfig, registry or caches of the Phase 4 stack (branch
# go-reference, decision 0014 section 18) that a machine may still hold in bin/kind-state/:
#   engine                the local engine proto-up started: kind (the default) or k3d (spike design
#                         .claude/docs/designs/k3s-site-cluster-spike.md G2 and G5); every later
#                         script reads it, so only make proto-up PROTO_ENGINE=k3d chooses
#   kubeconfig            the engine's admin kubeconfig of cluster webblock-proto
#   worker.kubeconfig     ServiceAccount webblock/provisioning-worker (TokenRequest, 24 h)
#   bootstrap.kubeconfig  ServiceAccount webblock/tenant-bootstrap (TokenRequest, 24 h)
#   jenkins.env           JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN for Provisioning (mode 0600)
#   jenkins-secrets/      the Jenkins users' generated passwords, mounted into Jenkins only (mode 0700)
#   buildkit-tls/         mutual TLS between Jenkins (buildctl) and the BuildKit daemon
#   git/                  bare repositories served by webblock-git (scripts/prototype/sample-repo.sh)
#   registry-tls/, registry-data/  the local registry's certificate and storage (kept as a pull cache)
#   stack.env             non-secret addresses of the running stack
#   catalog-admin.env     the catalog database's login credentials (proto-catalog.sh; mode 0600)
#   local-ca/             the local CA behind ClusterIssuer webblock-local-ca: ca.key (mode 0600, folder
#                         0700, never printed) and ca.crt; made at the first proto-up and kept by
#                         proto-down, like the registry's certificate (ensure_local_ca below)
#   local-ca.crt          a copy of the local CA's certificate for clients (curl --cacert, the probe)
#   kube-cache*/, home/, helm/, cache/, docker-config/  tool caches, HOME and verified downloads
#
# Cluster safety (.claude/CLAUDE.md section 0): every kubectl call goes through k (admin), kw (worker) or
# kb (bootstrap). Each wrapper first checks, without contacting a server, that its kubeconfig file is
# private, that its current context is <engine>-webblock-proto (kind-webblock-proto or
# k3d-webblock-proto, the recorded engine's), and that the context's server is
# https://127.0.0.1:<port>; then it passes --kubeconfig, --context and a project-local --cache-dir, with
# HOME pointed at bin/kind-state/proto/home. KUBECONFIG is exported to the admin file, so no client can
# fall back to ~/.kube/config.

KIND_CLUSTER_NAME="webblock-proto"
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=../prototype/versions.sh
. "$kind_root/deploy/prototype/versions.sh"
# shellcheck source=../prototype/catalog/versions.sh
. "$kind_root/deploy/prototype/catalog/versions.sh"

[ "$CLUSTER" = "$PROTO_CLUSTER" ] || die "proto-lib: cluster name $CLUSTER is not $PROTO_CLUSTER"

PROTO="$STATE/proto"
proto_dir="$kind_root/deploy/prototype"
K3D="$BIN/k3d"
ENGINE_FILE="$PROTO/engine"

# proto_engine: sets ENGINE (kind or k3d), CONTEXT (<engine>-webblock-proto, as each engine names
# the context of cluster webblock-proto) and NODE_PREFIX (the nodes' names: Kind's webblock-proto-*,
# k3d's k3d-webblock-proto-*). PROTO_ENGINE chooses it, on make proto-up only; every other script
# reads the engine proto-up recorded in $PROTO/engine (kind when there is no record). A request for
# the other engine while one is recorded is refused: one cluster at a time (G2).
proto_engine() {
  local recorded=""
  [ ! -f "$PROTO/engine" ] || recorded="$(cat "$PROTO/engine")"
  case "$recorded" in "" | kind | k3d) ;; *) die "$PROTO/engine names '$recorded', neither kind nor k3d" ;; esac
  if [ -n "${PROTO_ENGINE+set}" ]; then
    case "$PROTO_ENGINE" in kind | k3d) ;; *) die "PROTO_ENGINE must be kind or k3d (got '$PROTO_ENGINE')" ;; esac
    [ -z "$recorded" ] || [ "$recorded" = "$PROTO_ENGINE" ] \
      || die "a $recorded stack is recorded in $PROTO/engine; run make proto-down first"
    ENGINE="$PROTO_ENGINE"
  else
    ENGINE="${recorded:-kind}"
  fi
  CONTEXT="$ENGINE-$CLUSTER"
  case "$ENGINE" in
    kind) NODE_PREFIX="$CLUSTER" ;;
    k3d) NODE_PREFIX="k3d-$CLUSTER" ;;
  esac
}
proto_engine
KCFG="$PROTO/kubeconfig"
KCFG_WORKER="$PROTO/worker.kubeconfig"
KCFG_BOOTSTRAP="$PROTO/bootstrap.kubeconfig"
KUBECACHE="$PROTO/kube-cache"
TOOLHOME="$PROTO/home"
CACHE="$PROTO/cache"
REGISTRY_TLS="$PROTO/registry-tls"
REGISTRY_DATA="$PROTO/registry-data"
CRANE_DOCKER_CONFIG="$PROTO/docker-config"
JENKINS_ENV="$PROTO/jenkins.env"
JENKINS_SECRETS="$PROTO/jenkins-secrets"
BUILDKIT_TLS="$PROTO/buildkit-tls"
GIT_ROOT="$PROTO/git"
STACK_ENV="$PROTO/stack.env"
CATALOG_ENV="$PROTO/catalog-admin.env"
LOCAL_CA_DIR="$PROTO/local-ca"
LOCAL_CA_CRT="$PROTO/local-ca.crt"
LOCAL_CA_ISSUER="webblock-local-ca"
CERT_MANAGER_NS="cert-manager"
GATEWAY_HTTPS_PORT=443

REGISTRY_NAME="$PROTO_REGISTRY_NAME"
REGISTRY_ALIAS="$REGISTRY_NAME.test"
REGISTRY_LOCAL="localhost:$REGISTRY_PORT"
REGISTRY_IN_CLUSTER="$REGISTRY_ALIAS:5000"
JENKINS_URL_HOST="http://127.0.0.1:$PROTO_JENKINS_PORT"

mkdir -p "$PROTO" "$KUBECACHE" "$TOOLHOME" "$CACHE" "$CRANE_DOCKER_CONFIG"
chmod 0700 "$PROTO"

export KUBECONFIG="$KCFG"
export KUBECACHEDIR="$KUBECACHE"
export HELM_CONFIG_HOME="$PROTO/helm/config" HELM_CACHE_HOME="$PROTO/helm/cache" HELM_DATA_HOME="$PROTO/helm/data"

# require_local_kubeconfig FILE: refuses unless FILE exists with mode 0600, its current context is
# $CONTEXT (kind-webblock-proto or k3d-webblock-proto, by the recorded engine), and that context's
# server is https://127.0.0.1:<port> (k3d's default https://0.0.0.0:<port> is refused). Reads only the
# file.
require_local_kubeconfig() {
  local f="$1" mode cur server
  [ -f "$f" ] || die "$f does not exist"
  mode="$(stat -c %a "$f")"
  [ "$mode" = 600 ] || die "$f has mode $mode, expected 600"
  cur="$(HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$f" config current-context 2>/dev/null)" || die "$f has no current context"
  case "$cur" in
    kind-webblock-* | k3d-webblock-*) ;;
    *) die "$f: current context '$cur' is not kind-webblock-* or k3d-webblock-*; refusing" ;;
  esac
  [ "$cur" = "$CONTEXT" ] || die "$f: current context '$cur' is not $CONTEXT; refusing"
  server="$(HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$f" config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)" \
    || die "$f: cannot read the server of context $cur"
  printf '%s' "$server" | grep -Eq '^https://127\.0\.0\.1:[0-9]{1,5}$' \
    || die "$f: context $cur points at '$server', not https://127.0.0.1:<port>; refusing"
}
require_local_cluster() { require_local_kubeconfig "$KCFG"; }

# Admin, worker and bootstrap kubectl. Each identity has its own discovery cache.
k()  { require_local_kubeconfig "$KCFG" && kc --context "$CONTEXT" "$@"; }
kw() {
  require_local_kubeconfig "$KCFG_WORKER" \
    && HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$KCFG_WORKER" --context "$CONTEXT" --cache-dir "$KUBECACHE-worker" "$@"
}
kb() {
  require_local_kubeconfig "$KCFG_BOOTSTRAP" \
    && HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$KCFG_BOOTSTRAP" --context "$CONTEXT" --cache-dir "$KUBECACHE-bootstrap" "$@"
}
h() { require_local_kubeconfig "$KCFG" && HOME="$TOOLHOME" "$HELM" --kubeconfig "$KCFG" --kube-context "$CONTEXT" "$@"; }

# k3d_ ARGS...: bin/k3d with HOME and KUBECONFIG in bin/kind-state/proto/, so it can neither read nor
# write ~/.kube/config; the helper images pinned by digest (deploy/kind/versions.sh).
k3d_() {
  HOME="$TOOLHOME" KUBECONFIG="$KCFG" K3D_IMAGE_TOOLS="$K3D_TOOLS_IMAGE" K3D_IMAGE_LOADBALANCER="$K3D_PROXY_IMAGE" \
    "$K3D" "$@"
}

# cluster_exists: the recorded engine's cluster webblock-proto exists. Both engines label their node
# containers with the cluster's name (Kind io.x-k8s.kind.cluster, k3d k3d.cluster), so Docker answers
# for either without the other engine's binary.
cluster_exists() {
  case "$ENGINE" in
    kind) kind_ get clusters 2>/dev/null | grep -qx "$CLUSTER" ;;
    k3d) docker ps -a --filter "label=k3d.cluster=$CLUSTER" -q 2>/dev/null | grep -q . ;;
  esac
}

# node_containers: the Docker containers that are the cluster's Kubernetes nodes (their names are the
# node names): Kind's webblock-proto-control-plane and -worker, k3d's server and agent containers (not
# its load balancer).
node_containers() {
  case "$ENGINE" in
    kind) kind_ get nodes --name "$CLUSTER" ;;
    k3d)
      docker ps --filter "label=k3d.cluster=$CLUSTER" --format '{{.Label "k3d.role"}} {{.Names}}' \
        | awk '$1 == "server" || $1 == "agent" {print $2}'
      ;;
  esac
}

# require_no_other_cluster: one local cluster at a time on this machine (decision 0009 section 1; spike
# G2): no Kind or k3d cluster other than this engine's webblock-proto.
require_no_other_cluster() {
  local kinds k3ds others
  kinds="$(docker ps -a --filter label=io.x-k8s.kind.cluster --format '{{.Label "io.x-k8s.kind.cluster"}}' | sort -u)"
  k3ds="$(docker ps -a --filter label=k3d.cluster --format '{{.Label "k3d.cluster"}}' | sort -u)"
  others="$(
    { printf '%s\n' "$kinds" | sed '/^$/d; s/^/kind:/'; printf '%s\n' "$k3ds" | sed '/^$/d; s/^/k3d:/'; } \
      | grep -vx "$ENGINE:$CLUSTER" || true
  )"
  [ -z "$others" ] || die "another local cluster is running ($(printf '%s' "$others" | tr '\n' ' ')); one cluster at a time"
}

# ensure_local_ca: the local CA of ClusterIssuer webblock-local-ca (task T1; a stand-in for the
# production ACME CA, OD-58) in $LOCAL_CA_DIR, and its certificate copied to $LOCAL_CA_CRT for clients.
# Made once, at the first proto-up, and reused while it is valid for at least another 30 days and the
# key matches the certificate; otherwise made anew, which is refused while the cluster exists (its
# certificates were signed by the old CA; make proto-down first). EC P-256, valid 365 days, CA:TRUE
# with pathlen 0, and name-constrained to DNS names under the reserved top-level domain .test, so a
# browser or client that trusts it trusts it for nothing else. The key is written with umask 077 into
# a 0700 folder and never printed; it leaves the folder only into the Secret webblock-local-ca
# (proto-certs.sh).
ensure_local_ca() {
  local d="$LOCAL_CA_DIR" want got
  if [ -f "$d/ca.key" ] && [ -f "$d/ca.crt" ] \
    && openssl x509 -checkend 2592000 -noout -in "$d/ca.crt" >/dev/null 2>&1; then
    want="$(openssl x509 -noout -pubkey -in "$d/ca.crt" 2>/dev/null | sha256sum)"
    got="$(openssl pkey -pubout -in "$d/ca.key" 2>/dev/null | sha256sum)"
    [ "$want" = "$got" ] || die "local CA: $d/ca.key does not match $d/ca.crt; run make proto-down, then rm -rf $d"
  else
    if cluster_exists; then
      die "local CA: $d is missing or expires within 30 days while cluster $CLUSTER uses it; run make proto-down first"
    fi
    rm -rf "$d"
    mkdir -p "$d"
    chmod 0700 "$d"
    (
      umask 077
      openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
        -subj "/CN=webblock-local-ca (prototype stand-in for ACME, test only)" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "nameConstraints=critical,permitted;DNS:test" \
        -keyout "$d/ca.key" -out "$d/ca.crt" 2>/dev/null
    ) || die "local CA: openssl could not create the CA"
    log "local CA: created $d (365 days, name-constrained to .test; the key stays in that folder)"
  fi
  chmod 0700 "$d"
  chmod 0600 "$d/ca.key"
  install -m 0644 "$d/ca.crt" "$LOCAL_CA_CRT"
}

# catalog_port: the host port Docker published for the catalog database's 5432, or "".
catalog_port() {
  docker port "$PROTO_CATALOG_NAME" 5432/tcp 2>/dev/null | sed -nE 's/^127\.0\.0\.1:([0-9]+)$/\1/p' | head -n 1
}

# jenkins_curl ARGS...: curl against Jenkins as JENKINS_USER with the API token from jenkins.env. The
# credentials go to curl on standard input (-K -), never on a command line or in a log.
jenkins_curl() {
  [ -f "$JENKINS_ENV" ] || die "$JENKINS_ENV is missing: run make proto-up"
  local user token
  user="$(sed -n 's/^JENKINS_USER=//p' "$JENKINS_ENV")"
  token="$(sed -n 's/^JENKINS_API_TOKEN=//p' "$JENKINS_ENV")"
  [ -n "$user" ] && [ -n "$token" ] || die "$JENKINS_ENV lacks JENKINS_USER or JENKINS_API_TOKEN"
  printf 'user = "%s:%s"\n' "$user" "$token" | curl -sS -K - --max-time 30 "$@"
}

# json_get FILE KEY...: prints the value at the key path of a JSON file ("" for null or missing).
json_get() {
  python3 -I - "$@" <<'PY'
import json, sys
v = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    v = v.get(key) if isinstance(v, dict) else None
print("" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v))
PY
}
