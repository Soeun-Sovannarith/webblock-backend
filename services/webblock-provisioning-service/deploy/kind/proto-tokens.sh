#!/usr/bin/env bash
# Writes (or refreshes) one kubeconfig per Provisioning cluster identity of the prototype, each holding
# a fresh TokenRequest token that expires after 24 hours (make proto-tokens; also run by proto-up):
#   bin/kind-state/proto/worker.kubeconfig     webblock/provisioning-worker
#   bin/kind-state/proto/bootstrap.kubeconfig  webblock/tenant-bootstrap
# Both name the cluster's API server on https://127.0.0.1:<port> with Kind's CA, under the context
# <engine>-webblock-proto, and have mode 0600. A token is never printed; each file is checked with
# kubectl auth whoami. Old tokens stay valid until they expire (TokenRequest tokens are not revocable
# without deleting the ServiceAccount).
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

[ -x "$KUBECTL" ] || die "$KUBECTL is missing: run make kind-tools"
cluster_exists || die "cluster $CLUSTER does not exist: run make proto-up"
require_local_cluster

server="$(kc config view --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.server}')"
ca_data="$(kc config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
[ -n "$ca_data" ] || die "the admin kubeconfig has no certificate-authority-data for $CONTEXT"

# write_kubeconfig FILE SERVICEACCOUNT
write_kubeconfig() {
  local file="$1" sa="$2" token tmp who
  token="$(k -n webblock create token "$sa" --duration=24h)" || die "TokenRequest for $sa failed"
  printf '%s' "$token" | grep -Eq '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$' || die "TokenRequest for $sa returned no token"
  tmp="$(mktemp "$PROTO/.kubeconfig.XXXXXX")"
  chmod 0600 "$tmp"
  cat >"$tmp" <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: $CONTEXT
    cluster:
      server: $server
      certificate-authority-data: $ca_data
users:
  - name: webblock/$sa
    user:
      token: $token
contexts:
  - name: $CONTEXT
    context:
      cluster: $CONTEXT
      user: webblock/$sa
current-context: $CONTEXT
EOF
  mv "$tmp" "$file"
  require_local_kubeconfig "$file"
  who="$(HOME="$TOOLHOME" "$KUBECTL" --kubeconfig "$file" --context "$CONTEXT" --cache-dir "$KUBECACHE-whoami" \
    auth whoami -o jsonpath='{.status.userInfo.username}')" || die "$file does not authenticate"
  [ "$who" = "system:serviceaccount:webblock:$sa" ] || die "$file authenticates as '$who', not $sa"
  log "tokens: $file authenticates as $who (expires $(date -d '+24 hours' '+%Y-%m-%d %H:%M'))"
}

write_kubeconfig "$KCFG_WORKER" provisioning-worker
write_kubeconfig "$KCFG_BOOTSTRAP" tenant-bootstrap
