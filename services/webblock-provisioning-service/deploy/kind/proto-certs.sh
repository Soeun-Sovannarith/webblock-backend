#!/usr/bin/env bash
# Installs cert-manager and the local CA ClusterIssuer webblock-local-ca on the running prototype stack
# (task T1; decision 0014 section 16; review-k8s-nocp.md H4). Called by deploy/kind/proto-up.sh as
# step 4b, after Cilium or, on k3d, Traefik (the pods need the network). The Gateway API shim is off (decision 0014
# section 23: no per-domain Certificates; the wildcard is an ordinary Certificate). Idempotent.
#   1. the local CA in bin/kind-state/proto/local-ca/ (ensure_local_ca in proto-lib.sh), copied to
#      bin/kind-state/proto/local-ca.crt for clients
#   2. cert-manager v1.21.2 from the verified chart, every image from the local registry by digest:
#      the chart is rendered first and the install is refused unless its images are exactly the
#      cert-manager pins of PROTO_MIRROR_IMAGES with the localhost:5001 prefix
#   3. the CA's key pair as Secret cert-manager/webblock-local-ca (piped from the files to the API
#      server, never printed), then ClusterIssuer webblock-local-ca, retried until cert-manager's
#      webhook admits it, and waited on until Ready
#   4. a proof: a throwaway Certificate in cert-manager's namespace is issued, and its certificate
#      verifies against bin/kind-state/proto/local-ca.crt alone; then it is deleted
# Cluster safety (.claude/CLAUDE.md section 0): every kubectl and helm call goes through k and h from
# proto-lib.sh, which check the admin kubeconfig's context (<engine>-webblock-proto on
# https://127.0.0.1:<port>) first.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

require_tools
command -v openssl >/dev/null 2>&1 || die "openssl is required"
cluster_exists || die "cluster $CLUSTER does not exist: run make proto-up"
require_local_cluster

# --- 1. the local CA ----------------------------------------------------------------------------------------
ensure_local_ca

# --- 2. cert-manager ------------------------------------------------------------------------------------------
chart="$(fetch_verified "$PROTO_CERT_MANAGER_CHART_URL" "$PROTO_CERT_MANAGER_CHART_SHA256" "cert-manager-$PROTO_CERT_MANAGER_VERSION.tgz")"
values="$proto_dir/cert-manager/values.yaml"
rendered="$(HOME="$TOOLHOME" "$HELM" template cert-manager "$chart" --namespace "$CERT_MANAGER_NS" --values "$values" \
  | sed -nE 's/^[[:space:]-]*image:[[:space:]]*"?([^"[:space:]]+)"?[[:space:]]*$/\1/p' | sort -u)"
expected="$(printf '%s\n' "${PROTO_MIRROR_IMAGES[@]}" | grep '^quay.io/jetstack/' | sed "s#^quay.io/#$REGISTRY_LOCAL/#" | sort -u)"
[ -n "$rendered" ] && [ "$rendered" = "$expected" ] \
  || die "cert-manager: the chart renders images [$(printf '%s' "$rendered" | tr '\n' ' ')] that differ from the pins [$(printf '%s' "$expected" | tr '\n' ' ')]"
for ref in $expected; do
  [ "$(registry_digest "$ref")" = "${ref##*@}" ] || die "cert-manager: the local registry does not serve $ref"
done
k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/cert-manager/namespace.yaml" >/dev/null
log "cert-manager: installing chart $PROTO_CERT_MANAGER_VERSION from the local registry's images"
h upgrade --install cert-manager "$chart" --namespace "$CERT_MANAGER_NS" --values "$values" --timeout 10m >/dev/null
for d in cert-manager cert-manager-webhook cert-manager-cainjector; do
  k -n "$CERT_MANAGER_NS" rollout status "deployment/$d" --timeout=300s >/dev/null
done
k wait --for condition=Established --timeout=120s \
  crd/certificates.cert-manager.io crd/issuers.cert-manager.io crd/clusterissuers.cert-manager.io \
  crd/certificaterequests.cert-manager.io >/dev/null
log "cert-manager: controller, webhook and cainjector ready"

# --- 3. the CA Secret and the ClusterIssuer ----------------------------------------------------------------------
# The key pair goes from its files through a pipe to the API server; nothing echoes it.
k -n "$CERT_MANAGER_NS" create secret tls "$LOCAL_CA_ISSUER" \
  --cert="$LOCAL_CA_DIR/ca.crt" --key="$LOCAL_CA_DIR/ca.key" --dry-run=client -o json \
  | k apply --server-side --field-manager=webblock-proto-up -f - >/dev/null \
  || die "cert-manager: cannot write Secret $CERT_MANAGER_NS/$LOCAL_CA_ISSUER"
issuer_applied() {
  k apply --server-side --field-manager=webblock-proto-up -f "$proto_dir/cert-manager/local-ca-issuer.yaml" >/dev/null 2>&1
}
# The webhook's Service can lag its rollout by a few seconds (no endpoints yet, or its serving
# certificate not yet injected); a refused apply is retried.
poll 120 "cert-manager's webhook admitting ClusterIssuer $LOCAL_CA_ISSUER" issuer_applied
k wait --for condition=Ready "clusterissuer/$LOCAL_CA_ISSUER" --timeout=120s >/dev/null
# The issuer must sign with the CA on disk, not an older Secret's.
served="$(k -n "$CERT_MANAGER_NS" get secret "$LOCAL_CA_ISSUER" -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -fingerprint -sha256)"
[ "$served" = "$(openssl x509 -noout -fingerprint -sha256 -in "$LOCAL_CA_CRT")" ] \
  || die "cert-manager: Secret $CERT_MANAGER_NS/$LOCAL_CA_ISSUER does not hold the CA of $LOCAL_CA_CRT"

# --- 4. proof: one certificate issued and verified against local-ca.crt alone ---------------------------------
probe="webblock-ca-probe"
probe_dir="$(mktemp -d "$PROTO/.ca-probe.XXXXXX")"
cleanup_probe() {
  k -n "$CERT_MANAGER_NS" delete certificate "$probe" --ignore-not-found >/dev/null 2>&1 || true
  k -n "$CERT_MANAGER_NS" delete secret "$probe" --ignore-not-found >/dev/null 2>&1 || true
  rm -rf "$probe_dir"
}
trap cleanup_probe EXIT
cat <<EOF | k apply --server-side --field-manager=webblock-proto-up -f - >/dev/null
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: $probe
  namespace: $CERT_MANAGER_NS
spec:
  secretName: $probe
  dnsNames: ["ca-probe.sites.webblock.test"]
  duration: 1h
  privateKey: {algorithm: ECDSA, size: 256}
  issuerRef: {group: cert-manager.io, kind: ClusterIssuer, name: $LOCAL_CA_ISSUER}
EOF
k -n "$CERT_MANAGER_NS" wait --for condition=Ready "certificate/$probe" --timeout=120s >/dev/null
k -n "$CERT_MANAGER_NS" get secret "$probe" -o jsonpath='{.data.tls\.crt}' | base64 -d >"$probe_dir/leaf.crt"
openssl verify -CAfile "$LOCAL_CA_CRT" -verify_hostname ca-probe.sites.webblock.test "$probe_dir/leaf.crt" >/dev/null \
  || die "cert-manager: a certificate from $LOCAL_CA_ISSUER does not verify against $LOCAL_CA_CRT"
log "cert-manager: ClusterIssuer $LOCAL_CA_ISSUER Ready; a probe certificate verifies against $LOCAL_CA_CRT"
