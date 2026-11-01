#!/usr/bin/env bash
# End-to-end check of the prototype stack alone, with no Provisioning code (make proto-smoke):
#   1. Jenkins builds a fresh commit of the sample site, triggered over REST with the API token, and
#      archives a result.json that echoes the request and names the pushed digest, which the registry
#      serves.
#   2. The worker identity cannot create a namespace (403) nor write in a tenant namespace before its
#      RoleBinding exists (403). The bootstrap identity creates wb-<uuid>, its quota and limits, the
#      worker's RoleBinding and the offline ReferenceGrant; it cannot bind cluster-admin (403).
#   3. The worker applies Deployment, Service and HTTPRoute (image by digest) by server-side apply; the
#      Deployment becomes ready, the route is Accepted and ResolvedRefs, and the Gateway answers 200
#      with the commit's version string for Host smoke-<hex>.sites.webblock.test (the run's sub-domain,
#      decision 0023 G18; the tenant namespace carries it as a label), over HTTP and over HTTPS
#      (task T1: the wildcard certificate verifies against bin/kind-state/proto/local-ca.crt alone,
#      and is refused without it). The worker cannot delete the route (403, K9), nor the release the
#      route sends traffic to (refused by the admission policy webblock-site-routed-release; W6-K).
#   4. The worker points the route at the offline page: 503 with the offline text.
#   4b. The catalog database (task H) allows scram-sha-256 only and has its schema and reference data;
#      catalog_site cannot read catalog.websites; the worker has no Secret access and bootstrap only
#      get/create/patch of catalog-runtime, with no list.
#   4c. Loki (task W6-K): an audit line written to the Provisioning log folder is shipped to Loki and
#      found by a LogQL JSON filter (deploy/kind/proto-loki-query.sh).
#   5. The smoke namespace and its ReferenceGrant are deleted (admin), even on failure.
# KEEP_SMOKE=1 keeps the namespace for inspection.
# Usage: proto-smoke.sh PYTHON (make proto-smoke passes the project's .venv python). The tenant, release
# and route manifests are the worker's renderer's (decision 0014 section 20): scripts/prototype/
# render-examples.py writes them for this run's project, release and image, and the tenant file carries
# a placeholder catalog-runtime Secret, which the rendered Deployment names in envFrom.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

[ "$#" = 1 ] || { echo "usage: $0 PYTHON" >&2; exit 2; }
py="$1"
[ -x "$py" ] || die "$py is not an executable Python"
require_tools
cluster_exists || die "cluster $CLUSTER does not exist: run make proto-up"
require_local_cluster
require_local_kubeconfig "$KCFG_WORKER"
require_local_kubeconfig "$KCFG_BOOTSTRAP"
[ -f "$STACK_ENV" ] || die "$STACK_ENV is missing: run make proto-up"
[ -f "$LOCAL_CA_CRT" ] || die "$LOCAL_CA_CRT is missing: run make proto-up"

started="$(date +%s)"
uuid() { python3 -I -c 'import uuid; print(uuid.uuid4())'; }
PROJECT="$(uuid)"; RELEASE="$(uuid)"
SUBDOMAIN="smoke-${PROJECT//-/}"
NS="wb-$PROJECT"; HOST="$SUBDOMAIN.sites.webblock.test"
VERSION="smoke-$(date +%s)-$(python3 -I -c 'import secrets; print(secrets.token_hex(4))')"
IMAGE_REPOSITORY="$REGISTRY_IN_CLUSTER/webblock/sites/$PROJECT"
work="$PROTO/smoke"; mkdir -p "$work"
pass() { log "smoke PASS: $*"; }

cleanup() {
  local rc=$?
  if [ "${KEEP_SMOKE:-0}" = 1 ]; then log "smoke: KEEP_SMOKE=1, namespace $NS kept"; return; fi
  k delete namespace "$NS" --ignore-not-found --wait=true --timeout=180s >/dev/null 2>&1 || log "smoke: deleting $NS failed"
  k -n webblock-offline delete referencegrant "$NS" --ignore-not-found >/dev/null 2>&1 || log "smoke: deleting the ReferenceGrant failed"
  rm -rf "$work"
  if [ "$rc" = 0 ]; then log "smoke: cleaned up $NS"; else log "smoke: FAILED (exit $rc); cleaned up $NS"; fi
}
trap cleanup EXIT

# render: the example manifests of deploy/prototype/manifests, rendered by the worker's renderer for this
# run's project, release and image into $work/manifests (command numbers 1, and 2 for the offline route).
render() {
  "$py" -I "$kind_root/scripts/prototype/render-examples.py" --out "$work/manifests" \
    --project-id "$PROJECT" --release-id "$RELEASE" --subdomain "$SUBDOMAIN" --image "$IMAGE" \
    || die "scripts/prototype/render-examples.py failed"
}
# forbidden DESCRIPTION COMMAND...: the command fails with HTTP 403 Forbidden.
forbidden() {
  local what="$1" out; shift
  if out="$("$@" 2>&1)"; then die "$what was allowed"; fi
  # kubectl prints "(Forbidden)" for a plain RBAC denial and "... is forbidden: ..." for the RBAC
  # escalation check of a RoleBinding; both are HTTP 403.
  printf '%s' "$out" | grep -Eq '\(Forbidden\)| is forbidden: ' || die "$what failed, but not with Forbidden: $(printf '%s' "$out" | head -c 300)"
  pass "$what: 403 Forbidden ($(printf '%s' "$out" | grep -oE 'attempting to grant RBAC permissions not currently held|cannot [a-z]+ resource "[a-z]+"' | head -n 1))"
}

# --- 1. build through Jenkins ------------------------------------------------------------------------------
sha="$(bash "$kind_root/scripts/prototype/sample-repo.sh" smoke-site site "$VERSION")"
log "smoke: commit $sha of smoke-site (version $VERSION); project $PROJECT, release $RELEASE"
bash "$kind_root/scripts/prototype/jenkins-build.sh" "$PROJECT" "$RELEASE" 1 \
  "http://$PROTO_GIT_NAME:8080/smoke-site.git" "$sha" "$IMAGE_REPOSITORY" >"$work/result.json"
[ -z "$(json_get "$work/result.json" failure_code)" ] || die "build failed: $(cat "$work/result.json")"
[ "$(json_get "$work/result.json" project_id)" = "$PROJECT" ] || die "result.json project_id differs"
[ "$(json_get "$work/result.json" release_id)" = "$RELEASE" ] || die "result.json release_id differs"
[ "$(json_get "$work/result.json" attempt)" = 1 ] || die "result.json attempt differs"
[ "$(json_get "$work/result.json" commit_sha)" = "$sha" ] || die "result.json commit_sha differs"
digest="$(json_get "$work/result.json" image_digest)"
[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || die "result.json image_digest is '$digest'"
[ "$(registry_digest "$REGISTRY_LOCAL/webblock/sites/$PROJECT:$RELEASE-1")" = "$digest" ] \
  || die "the registry does not serve $digest for webblock/sites/$PROJECT:$RELEASE-1"
pass "Jenkins built $sha, result.json $(tr -d '\n' <"$work/result.json"), served by the registry"
IMAGE="$IMAGE_REPOSITORY@$digest"

# --- 2. identities ----------------------------------------------------------------------------------------------
forbidden "worker creates namespace $NS" kw create namespace "$NS"
render
cp "$work/manifests/tenant.yaml" "$work/tenant.yaml"
# The namespace, quota, limits and the placeholder catalog-runtime first; the worker has no access yet.
python3 -I - "$work/tenant.yaml" "$work" <<'PY'
import sys
docs = open(sys.argv[1]).read().split("\n---\n")
body = [d for d in docs if "\nkind: RoleBinding" not in d and "\nkind: ReferenceGrant" not in d]
rest = [d for d in docs if d not in body]
open(sys.argv[2] + "/tenant-base.yaml", "w").write("\n---\n".join(body) + "\n")
open(sys.argv[2] + "/tenant-grants.yaml", "w").write("\n---\n".join(rest) + "\n")
PY
kb apply --server-side --field-manager=webblock-tenant-bootstrap -f "$work/tenant-base.yaml" >/dev/null
cp "$work/manifests/release.yaml" "$work/release.yaml"
forbidden "worker applies its Deployment before the RoleBinding exists" \
  kw apply --server-side --field-manager=webblock-provisioning-worker -f "$work/release.yaml"
kb apply --server-side --field-manager=webblock-tenant-bootstrap -f "$work/tenant-grants.yaml" >/dev/null
pass "bootstrap created namespace $NS, its quota and limits, the worker's RoleBinding and the offline ReferenceGrant"
forbidden "bootstrap binds cluster-admin in $NS" \
  kb -n "$NS" create rolebinding smoke-escalation --clusterrole=cluster-admin --serviceaccount=webblock:tenant-bootstrap
forbidden "bootstrap deletes namespace $NS" kb delete namespace "$NS" --wait=false

# --- 3. worker deploys and routes ---------------------------------------------------------------------------------
kw apply --server-side --field-manager=webblock-provisioning-worker -f "$work/release.yaml" >/dev/null
kw -n "$NS" rollout status "deployment/rel-$RELEASE" --timeout=180s >/dev/null
running="$(kw -n "$NS" get pods -l "websites.webblock.internal/release-id=$RELEASE" -o jsonpath='{.items[0].status.containerStatuses[0].imageID}')"
case "$running" in *"$digest") ;; *) die "the pod runs imageID '$running', not $digest";; esac
pass "Deployment rel-$RELEASE ready, pod runs $digest"

cp "$work/manifests/route-release.yaml" "$work/route.yaml"
kw apply --server-side --field-manager=webblock-provisioning-worker -f "$work/route.yaml" >/dev/null
cond() {
  kw -n "$NS" get httproute "route-$PROJECT" \
    -o jsonpath="{.status.parents[?(@.parentRef.name==\"webblock-public\")].conditions[?(@.type==\"$1\")].$2}"
}
route_ok() {
  [ "$(cond Accepted status)" = True ] && [ "$(cond ResolvedRefs status)" = True ] \
    && [ "$(cond Accepted observedGeneration)" = "$(kw -n "$NS" get httproute "route-$PROJECT" -o jsonpath='{.metadata.generation}')" ]
}
poll 120 "route-$PROJECT Accepted and ResolvedRefs at its generation" route_ok

gw="$(sed -n 's/^PROTO_GATEWAY_ADDRESS=//p' "$STACK_ENV")"
[ -n "$gw" ] || die "no PROTO_GATEWAY_ADDRESS in $STACK_ENV"
http() { curl -s --max-time 5 -o "$work/body" -w '%{http_code}' -H "Host: $HOST" "http://$gw$1" || true; }
serves_site() { [ "$(http /)" = 200 ] && grep -qF "sample-site version: $VERSION" "$work/body"; }
poll 90 "the Gateway at $gw serving $HOST with version $VERSION" serves_site
pass "GET http://$gw/ Host $HOST: 200 with 'sample-site version: $VERSION'"
# HTTPS through the Gateway's https-sites listener. --cacert replaces the system roots, so the request
# succeeds only when the wildcard certificate chains to the local CA and names $HOST.
https_url="https://$HOST:$GATEWAY_HTTPS_PORT"
https_get() {
  curl -s --max-time 5 -o "$work/body" -w '%{http_code}' --resolve "$HOST:$GATEWAY_HTTPS_PORT:$gw" \
    --cacert "$LOCAL_CA_CRT" "$https_url$1" || true
}
serves_site_https() { [ "$(https_get /)" = 200 ] && grep -qF "sample-site version: $VERSION" "$work/body"; }
poll 90 "$https_url through the Gateway, verified against the local CA" serves_site_https
untrusted=0
curl -s --max-time 5 -o /dev/null --resolve "$HOST:$GATEWAY_HTTPS_PORT:$gw" "$https_url/" || untrusted=$?
[ "$untrusted" = 60 ] || die "HTTPS without the local CA: curl exit $untrusted, expected 60 (certificate not trusted)"
pass "GET $https_url/ (resolved to $gw): 200 with 'sample-site version: $VERSION', certificate verified against local-ca.crt alone; without it curl refuses (exit 60)"
forbidden "worker deletes route-$PROJECT" kw -n "$NS" delete httproute "route-$PROJECT" --wait=false
# The routed release is protected by admission (webblock-site-routed-release; task W6-K): a server-side
# dry run of the delete by the worker's own token is refused while the route names it.
for kind in deployment service; do
  if kw -n "$NS" delete "$kind" "rel-$RELEASE" --dry-run=server >"$work/routed.out" 2>&1; then
    die "the worker could delete the routed release's $kind rel-$RELEASE"
  fi
  grep -qF "ValidatingAdmissionPolicy 'webblock-site-routed-release'" "$work/routed.out" \
    || die "the delete of the routed $kind was refused, but not by webblock-site-routed-release: $(cut -c1-200 "$work/routed.out")"
done
pass "worker deletes the routed release rel-$RELEASE: refused by webblock-site-routed-release"

# --- 4. offline page ------------------------------------------------------------------------------------------------
cp "$work/manifests/route-offline.yaml" "$work/route-offline.yaml"
kw apply --server-side --field-manager=webblock-provisioning-worker -f "$work/route-offline.yaml" >/dev/null
poll 120 "route-$PROJECT Accepted and ResolvedRefs on the offline page" route_ok
serves_offline() { [ "$(http /any/path)" = 503 ] && grep -qF "Site offline" "$work/body"; }
poll 90 "the Gateway serving the offline page for $HOST" serves_offline
if [ "$(http /)" != 503 ] || ! grep -qF "Site offline" "$work/body"; then die "GET / is not the offline page"; fi
pass "route switched to webblock-offline/offline-page: GET /any/path and / answer 503 'Site offline'"

# --- the catalog database (task H, decision 0014 section 14) -------------------------------------------------
# catalog_sql USER-KEY SQL: one statement as a login of catalog-admin.env, over TCP inside the container.
catalog_sql() {
  PGPASSWORD="$(sed -n "s/^CATALOG_$1_PASSWORD=//p" "$CATALOG_ENV")" \
    docker exec -i -e PGPASSWORD "$PROTO_CATALOG_NAME" psql -X -q -tA -v ON_ERROR_STOP=1 -h 127.0.0.1 \
    -U "$(sed -n "s/^CATALOG_$1_USER=//p" "$CATALOG_ENV")" -d "$PROTO_CATALOG_DATABASE" -c "$2"
}
auth="$(catalog_sql SUPERUSER "SELECT string_agg(DISTINCT auth_method, ',') FROM pg_hba_file_rules")"
[ "$auth" = scram-sha-256 ] || die "catalog: pg_hba allows '$auth', expected scram-sha-256 only"
[ "$(catalog_sql MIGRATOR "SELECT count(*) FROM catalog.business_types WHERE code = 'clothing_shop'")" = 1 ] \
  || die "catalog: the reference data is missing"
[ "$(catalog_sql MIGRATOR "SELECT has_table_privilege('catalog_site', 'catalog.websites', 'SELECT')")" = f ] \
  || die "catalog: catalog_site can read catalog.websites"
pass "catalog $PROTO_CATALOG_NAME: scram-sha-256 only, schema and reference data present, catalog_site cannot read catalog.websites"
[ "$(kw auth can-i get secrets -n "$NS" 2>/dev/null)" = no ] || die "the worker may read Secrets in $NS"
[ "$(kb auth can-i get secret/catalog-runtime -n "$NS" 2>/dev/null)" = yes ] || die "bootstrap may not read catalog-runtime"
[ "$(kb auth can-i get secret/another -n "$NS" 2>/dev/null)" = no ] || die "bootstrap may read other Secrets"
[ "$(kb auth can-i list secrets -n "$NS" 2>/dev/null)" = no ] || die "bootstrap may list Secrets"
pass "Secrets in $NS: worker none; bootstrap get/create/patch of catalog-runtime only, no list"

# Loki (decision 0014 section 23 item 5): an audit line written where the Provisioning processes write
# their logs is shipped to Loki and found by a LogQL JSON filter.
loki_url="$(sed -n 's/^PROTO_LOKI_URL=//p' "$STACK_ENV")"
[ -n "$loki_url" ] || die "no PROTO_LOKI_URL in $STACK_ENV"
curl -sf --max-time 5 -o /dev/null "$loki_url/ready" || die "Loki at $loki_url is not ready"
marker="smoke-$(python3 -I -c 'import secrets; print(secrets.token_hex(8))')"
mkdir -p "$PROTO/provisioning/logs/proto-smoke"
printf '{"event":"smoke.audit_probe","audit":true,"marker":"%s","result":"ok"}\n' "$marker" \
  >>"$PROTO/provisioning/logs/proto-smoke/smoke.log"
audit_shipped() {
  bash "$kind_dir/proto-loki-query.sh" --since 10m \
    "{job=\"webblock-provisioning\", run=\"proto-smoke\"} | json | audit=\"true\" | marker=\"$marker\"" \
    | grep -qF "$marker"
}
poll 60 "the smoke's audit line in Loki" audit_shipped
pass "Loki at $loki_url: an audit line written to the Provisioning log folder is found with | json | audit=\"true\""

log "proto-smoke: all checks passed in $(( $(date +%s) - started ))s"
