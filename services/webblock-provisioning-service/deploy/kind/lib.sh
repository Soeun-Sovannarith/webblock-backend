# shellcheck shell=bash
# Shared settings and helpers of the Kind stack, sourced by deploy/kind/proto-lib.sh (which then moves
# every piece of state to bin/kind-state/proto/). Sourced, never executed. The Phase 4 scripts that
# also sourced it (up.sh, down.sh, status.sh, smoke.sh, prove-stack.sh) are on branch go-reference
# (decision 0014 section 18).
#
# Cluster safety (decision 0009 section 1, .claude/CLAUDE.md): every kind, kubectl and helm call goes
# through the wrappers below. All their state lives in bin/kind-state/ (ignored with the rest of bin/):
#   kubeconfig    every kind, kubectl and helm call passes --kubeconfig naming it, and kubectl and helm
#                 also pass the Kind context; KUBECONFIG is exported to the same file, so a call that
#                 missed a flag still cannot fall back to ~/.kube/config.
#   kube-cache/   kubectl's discovery and HTTP cache: --cache-dir on every kubectl call, and KUBECACHEDIR
#                 (honoured by kubectl and by helm's Kubernetes client) for every call.
#   helm/         HELM_CONFIG_HOME, HELM_CACHE_HOME and HELM_DATA_HOME.
#   home/         HOME for every kubectl and helm process, so no default path derived from $HOME (such
#                 as ~/.kube/cache) can be written either.
#   cache/        verified downloads (charts, the Gateway API bundle).
#   registry-tls/ the local registry's project-local CA certificate and server key pair (the CA key is
#                 deleted right after signing); trust/ holds only the CA certificate, for crane.
#   registry-data/ the local registry's storage, kept by the down script as a pull cache, so a new stack
#                 does not fetch the mirrored images from quay.io again.
#   docker-config/ an empty Docker config for crane, so it never reads ~/.docker credentials.
# Before kubectl or helm talks to a server, require_local_cluster checks that the kubeconfig has mode
# 0600 and that the Kind context points at https://127.0.0.1:<port>.

set -euo pipefail

kind_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
kind_dir="$kind_root/deploy/kind"
# shellcheck source=versions.sh
. "$kind_dir/versions.sh"

CLUSTER="${KIND_CLUSTER_NAME:-webblock-p4}"
if ! printf '%s' "$CLUSTER" | grep -Eq '^webblock-[a-z0-9-]{1,40}$'; then
  echo "KIND_CLUSTER_NAME must match ^webblock-[a-z0-9-]{1,40}\$ (got '$CLUSTER')" >&2
  exit 2
fi
CONTEXT="kind-$CLUSTER"

BIN="$kind_root/bin"
STATE="$BIN/kind-state"
KCFG="$STATE/kubeconfig"
KUBECACHE="$STATE/kube-cache"
TOOLHOME="$STATE/home"
CACHE="$STATE/cache"
KIND="$BIN/kind"
KUBECTL="$BIN/kubectl"
HELM="$BIN/helm"
CRANE="$BIN/crane"
REGISTRY_TLS="$STATE/registry-tls"
REGISTRY_DATA="$STATE/registry-data"
CRANE_DOCKER_CONFIG="$STATE/docker-config"
# How the registry is addressed: from the host and, through containerd's hosts.toml, from the nodes
# (localhost:5001); from pods and nodes by the network alias webblock-kind-registry.test:5000 on the Kind
# network. The alias has a dot (Crossplane, which the Phase 4 stack ran, refused a package host without
# one, and the name was kept); .test is a reserved top-level domain.
REGISTRY_ALIAS="$REGISTRY_NAME.test"
REGISTRY_LOCAL="localhost:$REGISTRY_PORT"
REGISTRY_IN_CLUSTER="$REGISTRY_ALIAS:5000"

mkdir -p "$STATE" "$KUBECACHE" "$TOOLHOME" "$CACHE" "$CRANE_DOCKER_CONFIG"
chmod 0700 "$STATE"

export KUBECONFIG="$KCFG"
export KUBECACHEDIR="$KUBECACHE"
export HELM_CONFIG_HOME="$STATE/helm/config" HELM_CACHE_HOME="$STATE/helm/cache" HELM_DATA_HOME="$STATE/helm/data"
# kind uses Docker; never another provider or network picked up from the environment.
export KIND_EXPERIMENTAL_PROVIDER=docker
unset KIND_EXPERIMENTAL_DOCKER_NETWORK

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

require_tools() {
  local t
  for t in "$KIND" "$KUBECTL" "$HELM" "$CRANE"; do
    [ -x "$t" ] && [ -f "$t" ] || die "$t is missing: run make kind-tools"
  done
  command -v docker >/dev/null 2>&1 || die "docker is required"
  command -v curl >/dev/null 2>&1 || die "curl is required"
}

kind_() { "$KIND" "$@"; }   # kind create/delete/export get --kubeconfig "$KCFG" at each call site
# kubectl without a context, for kubeconfig-only subcommands (config view, config get-contexts).
kc() { HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$KCFG" --cache-dir "$KUBECACHE" "$@"; }
k()  { kc --context "$CONTEXT" "$@"; }
h()  { HOME="$TOOLHOME" "$HELM" --kubeconfig "$KCFG" --kube-context "$CONTEXT" "$@"; }

cluster_exists() { kind_ get clusters 2>/dev/null | grep -qx "$CLUSTER"; }

# crane trusts the system roots plus the project-local registry CA (Go reads SSL_CERT_DIR in addition to
# the system bundle file), uses an empty Docker config (anonymous pulls only) and the project-local HOME.
crane_() { SSL_CERT_DIR="$REGISTRY_TLS/trust" DOCKER_CONFIG="$CRANE_DOCKER_CONFIG" HOME="$TOOLHOME" "$CRANE" "$@"; }

# registry_digest REF: the manifest digest the registry serves for REF, or nothing when it has none.
registry_digest() { crane_ digest "$1" 2>/dev/null || true; }

# ensure_registry_tls: a project-local CA and a server certificate for the local registry, valid for 30
# days, for the names webblock-kind-registry, webblock-kind-registry.test and localhost and the address 127.0.0.1. Reused while both
# are valid for at least another day; otherwise regenerated, which is refused while the cluster exists
# (its nodes trust the old CA; make proto-down first). The CA key is deleted right after
# signing, so nothing else can ever be signed by this CA.
ensure_registry_tls() {
  local d="$REGISTRY_TLS" tmp
  if [ -f "$d/ca.crt" ] && [ -f "$d/tls.crt" ] && [ -f "$d/tls.key" ] && [ -f "$d/trust/ca.pem" ] \
    && openssl x509 -checkend 86400 -noout -in "$d/ca.crt" >/dev/null 2>&1 \
    && openssl x509 -checkend 86400 -noout -in "$d/tls.crt" >/dev/null 2>&1 \
    && openssl verify -CAfile "$d/ca.crt" "$d/tls.crt" >/dev/null 2>&1 \
    && openssl x509 -noout -ext subjectAltName -in "$d/tls.crt" 2>/dev/null | grep -qF "DNS:$REGISTRY_ALIAS,"; then
    return 0
  fi
  if cluster_exists; then
    die "the registry certificate in $d is missing, lacks a current name or expires within a day while cluster $CLUSTER trusts the old CA; run make proto-down first"
  fi
  command -v openssl >/dev/null 2>&1 || die "openssl is required to create the registry certificate"
  rm -rf "$d"
  mkdir -p "$d/trust"
  chmod 0700 "$d"
  tmp="$(mktemp -d "$STATE/.tls.XXXXXX")"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 \
    -subj "/CN=webblock-kind-registry local CA (test only)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$tmp/ca.key" -out "$d/ca.crt" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$REGISTRY_NAME" \
    -keyout "$d/tls.key" -out "$tmp/tls.csr" 2>/dev/null
  printf 'subjectAltName=DNS:%s,DNS:%s,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n' \
    "$REGISTRY_NAME" "$REGISTRY_ALIAS" >"$tmp/ext.cnf"
  openssl x509 -req -in "$tmp/tls.csr" -CA "$d/ca.crt" -CAkey "$tmp/ca.key" -CAserial "$tmp/ca.srl" -CAcreateserial \
    -days 30 -extfile "$tmp/ext.cnf" -out "$d/tls.crt" 2>/dev/null
  rm -rf "$tmp"
  chmod 0600 "$d/tls.key"
  cp "$d/ca.crt" "$d/trust/ca.pem"
  openssl verify -CAfile "$d/ca.crt" "$d/tls.crt" >/dev/null || die "the new registry certificate does not verify against its CA"
  log "registry: created a project-local CA and server certificate in $d (30 days; CA key deleted)"
}

# Refuses to continue unless the kubeconfig is the project-local file, private, and its Kind context
# points at the local API server on 127.0.0.1. Reads only the kubeconfig; contacts no server.
require_local_cluster() {
  [ -f "$KCFG" ] || die "$KCFG does not exist"
  local mode server
  mode="$(stat -c %a "$KCFG")"
  [ "$mode" = 600 ] || die "$KCFG has mode $mode, expected 600"
  server="$(kc config view --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)" \
    || die "context $CONTEXT is not in $KCFG"
  printf '%s' "$server" | grep -Eq '^https://127\.0\.0\.1:[0-9]{1,5}$' \
    || die "context $CONTEXT points at '$server', not https://127.0.0.1:<port>; refusing"
}

# fetch_verified URL SHA256 NAME: downloads into the cache once and checks the pinned SHA-256 at
# every use, so a corrupted or replaced cache file is refused. Prints the cached path.
fetch_verified() {
  local url="$1" want="$2" name="$3" path got tmp
  path="$CACHE/$name"
  if [ ! -f "$path" ]; then
    tmp="$(mktemp "$CACHE/.dl.XXXXXX")"
    if ! curl -sSfL --proto '=https' --retry 3 --retry-delay 3 --retry-all-errors --max-time 300 -o "$tmp" "$url"; then
      rm -f "$tmp"; die "download failed: $url"
    fi
    mv "$tmp" "$path"
  fi
  got="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    rm -f "$path"
    die "checksum mismatch for $name: got $got, pinned $want (cache entry removed)"
  fi
  printf '%s\n' "$path"
}

# poll SECONDS DESCRIPTION COMMAND...: runs COMMAND every 3 s until it succeeds; fails after SECONDS.
poll() {
  local limit="$1" what="$2" start
  shift 2
  start="$(date +%s)"
  until "$@"; do
    if [ $(( $(date +%s) - start )) -ge "$limit" ]; then
      die "timed out after ${limit}s waiting for: $what"
    fi
    sleep 3
  done
}

# The Kind Docker network's IPv4 subnet, for example 172.18.0.0/16.
kind_network_subnet() {
  docker network inspect kind --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' 2>/dev/null \
    | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$' | head -n1
}
