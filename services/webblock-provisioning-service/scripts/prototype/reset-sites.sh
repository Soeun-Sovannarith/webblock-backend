#!/usr/bin/env bash
# reset-sites.sh: clears the sites that finished tests left on the prototype stack (task W6-X, finding
# M-2 of ai/reviews/wave6-validation.md). Every e2e and demo test leaves its site running with 256 MiB
# requested; after about 60 tenant pods the Kind worker node has no memory left to schedule on. This
# script makes `make proto-e2e` and `make proto-demo` repeatable on one stack (make proto-reset-sites).
#
# Prototype only. In this order:
#   1. stops the four Provisioning processes, so nothing recreates a site while it is cleared;
#   2. deletes the tenant namespaces wb-* through the project's admin kubeconfig (require_local_kubeconfig:
#      context <engine>-webblock-proto on 127.0.0.1 only) and waits until they are gone;
#   3. truncates every table of the schemas `provisioning` and `provisioning_ops` in the prototype's own
#      container webblock-provisioning-db, as its superuser (the run script's path). The goose version
#      table and the roles stay, so the schema and the migrations are untouched; the owner's `postgres`
#      container is never addressed;
#   4. purges the queues of the prototype's own broker container webblock-rabbitmq, so no message
#      about a cleared command or release is redelivered;
#   5. starts the processes again (run-provisioning.sh restart) and checks, after a settle period longer
#      than the bootstrap and reconciler intervals, that no namespace wb-* and no row came back.
#
# Not touched: the catalog database webblock-catalog-db (its per-site roles are harmless and belong to
# the catalog, not to Provisioning), the Git server, Jenkins, the registry and the stack's policies.
# Nothing here reads .env.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

RUN="$kind_root/scripts/prototype/run-provisioning.sh"
PROV_PG_NAME="webblock-provisioning-db"
PROV_MQ_NAME="webblock-rabbitmq"
PROV_ENV="$PROTO/provisioning/provisioning.env"
SETTLE_SECONDS="${RESET_SETTLE_SECONDS:-12}"

get_var() {
  python3 -I - "$1" "$2" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
with open(path) as f:
    for line in f:
        line = line.rstrip("\n")
        if line.startswith(key + "="):
            value = line[len(key) + 1:]
            if len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1]
            sys.stdout.write(value)
            raise SystemExit(0)
raise SystemExit(1)
PY
}

# psql_prov SQL: runs SQL in the prototype's database as the superuser (password from the run script's
# 0600 file, passed to the container's psql by environment, never printed); prints the rows unaligned.
psql_prov() {
  docker exec -e PGPASSWORD="$(get_var "$PROV_ENV" PG_SUPERUSER_PASSWORD)" "$PROV_PG_NAME" \
    psql -tAq -h 127.0.0.1 -U postgres -d provisioning -v ON_ERROR_STOP=1 -c "$1"
}

tenant_namespaces() { k get namespaces -o name | sed 's#^namespace/##' | grep '^wb-' || true; }

node_memory_requests() { k describe nodes | grep -E '^  memory ' | sed 's/^ */  /'; }

row_count() {
  psql_prov "SELECT COALESCE(sum((xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, tablename), false, true, '')))[1]::text::bigint), 0)
             FROM pg_tables WHERE schemaname IN ('provisioning', 'provisioning_ops')"
}

# --- preconditions: the prototype's own containers and cluster ---------------------------------------
require_local_cluster
for c in "$PROV_PG_NAME" "$PROV_MQ_NAME"; do
  [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || true)" = true ] \
    || die "$c is not running; run make proto-run first"
done
[ -f "$PROV_ENV" ] || die "$PROV_ENV is missing; run make proto-run first"

log "reset-sites: node memory requests before:"
node_memory_requests

log "reset-sites: stopping the Provisioning processes"
bash "$RUN" stop-processes

# --- the cluster -------------------------------------------------------------------------------------
mapfile -t namespaces < <(tenant_namespaces)
if [ "${#namespaces[@]}" -gt 0 ]; then
  log "reset-sites: deleting ${#namespaces[@]} tenant namespaces"
  k delete namespace "${namespaces[@]}" --wait=false >/dev/null
  deadline=$((SECONDS + 240))
  while [ -n "$(tenant_namespaces)" ]; do
    [ "$SECONDS" -lt "$deadline" ] || die "tenant namespaces still present after 240 s: $(tenant_namespaces | tr '\n' ' ')"
    sleep 2
  done
else
  log "reset-sites: no tenant namespaces"
fi

# --- the database ------------------------------------------------------------------------------------
log "reset-sites: truncating the Provisioning tables of $PROV_PG_NAME"
psql_prov "DO \$\$
DECLARE t text;
BEGIN
  SELECT string_agg(format('%I.%I', schemaname, tablename), ', ') INTO t
  FROM pg_tables WHERE schemaname IN ('provisioning', 'provisioning_ops');
  IF t IS NOT NULL THEN
    EXECUTE 'TRUNCATE ' || t || ' RESTART IDENTITY CASCADE';
  END IF;
END \$\$" >/dev/null
[ "$(row_count)" = 0 ] || die "rows remain after the truncate"

# --- the broker --------------------------------------------------------------------------------------
log "reset-sites: purging the queues of $PROV_MQ_NAME"
while IFS= read -r queue; do
  [ -n "$queue" ] || continue
  docker exec -u rabbitmq "$PROV_MQ_NAME" rabbitmqctl -q purge_queue "$queue" >/dev/null
done < <(docker exec -u rabbitmq "$PROV_MQ_NAME" rabbitmqctl -q list_queues name --formatter json 2>/dev/null \
  | python3 -I -c 'import json,sys
for q in json.load(sys.stdin): print(q["name"])')

# --- start again and prove nothing came back ---------------------------------------------------------
log "reset-sites: starting the Provisioning processes"
bash "$RUN" restart
log "reset-sites: waiting ${SETTLE_SECONDS} s to see whether the reconciler or the bootstrap loop bring anything back"
sleep "$SETTLE_SECONDS"
[ -z "$(tenant_namespaces)" ] || die "tenant namespaces came back: $(tenant_namespaces | tr '\n' ' ')"
[ "$(row_count)" = 0 ] || die "rows came back after the restart"

log "reset-sites: node memory requests after:"
node_memory_requests
log "reset-sites: done; 0 tenant namespaces, 0 Provisioning rows"
