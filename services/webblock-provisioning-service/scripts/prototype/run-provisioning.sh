#!/usr/bin/env bash
# run-provisioning.sh: runs the Provisioning service (task G, decision 0014) against the prototype
# stack that `make proto-up` started.
#
#   run-provisioning.sh start          Provisioning's own PostgreSQL 18 (webblock-provisioning-db) and
#                                      RabbitMQ (webblock-rabbitmq) containers, roles and migrations,
#                                      one login per role, the configuration of the four processes,
#                                      and the processes (api, worker, build_worker, bootstrap); waits
#                                      until the API is ready (make proto-run)
#   run-provisioning.sh stop           stops the processes and removes the two containers and the
#                                      generated secrets; the logs stay (make proto-stop)
#   run-provisioning.sh status         the processes and containers
#   run-provisioning.sh restart        stops and starts the four processes (code changes), waits ready;
#                                      with replicas, every replica (the count `start` recorded)
#   run-provisioning.sh start-process NAME   starts one process again (api|worker|build_worker|bootstrap,
#                                      or a second replica api-2|worker-2|build_worker-2, or on a
#                                      replica run the standby bootstrap-2, task H5c)
#   run-provisioning.sh stop-process NAME    SIGTERM, then SIGKILL after 30 s
#   run-provisioning.sh stop-processes       stop-process for every replica of every process
#   run-provisioning.sh kill-process NAME    SIGKILL at once (crash tests)
#   run-provisioning.sh operator ARGS...     the operator CLI (python -m provisioning.hosts.operator)
#                                      as the provisioning_operator login: list | redrive ...
#                                      (.claude/docs/runbooks/operator.md); standard error is also
#                                      appended to logs/latest/operator.log (the audit lines)
#   run-provisioning.sh operator-as-worker ARGS...   the same CLI with the worker's login, which
#                                      the database refuses a re-drive (the runbook's proof)
#   run-provisioning.sh operator-dlq ARGS...   the CLI's dead-letter commands (dlq list | dlq
#                                      redrive ...; task W6-E2) with the broker settings only, from
#                                      env/operator_dlq.env; audit lines go to operator.log too
#   run-provisioning.sh standins-sync  after a stand-in file was rewritten (the e2e harness): nothing on
#                                      the host; in cluster mode ConfigMap provisioning-standins from the
#                                      three files, then wait until every pod that mounts it reads them
#
# Mode (task PK; decision 0016 sections 8 and 9; .claude/docs/runbooks/prototype-in-cluster.md). PROV_MODE=cluster
# on `start` (make proto-run PROV_MODE=cluster) runs the four processes as pods of the Helm chart
# deploy/helm/provisioning in webblock instead of host processes: it builds the image
# (deploy/image/build.sh), pushes it to the stack's registry by digest, writes the same configuration
# with in-cluster addresses and the chart's mount paths, turns it into the chart's values and Secrets
# (scripts/prototype/cluster_values.py), installs the chart (the migration Job first), and follows the
# pods' logs into the same log files (scripts/prototype/follow_logs.py). `start` records the mode in
# provisioning/mode (a host start always records host); every other subcommand reads it, so the
# harness never chooses. In cluster mode stop-process scales a Deployment to 0, start-process scales it
# to 1 and waits until it is ready, kill-process freezes the process from its Kind node, scales to 0
# and kills it (crash semantics), and restart does both for all four. The replica run (PROV_REPLICAS=2)
# and PROJECT_ACCESS_MODE=project-service run on the host only. Both modes put the PostgreSQL and
# RabbitMQ containers on the kind network. endpoints.env names the API for the harness in both modes
# (API_URL, and API2_URL on a replica run): on the host the address each API listens on, in the
# cluster the API Service's LoadBalancer address.
#
# State lives in bin/kind-state/proto/provisioning/ (folder 0700):
#   provisioning.env   generated passwords and the broker URL (0600; never printed)
#   env/<process>.env  each process's complete environment (0600; never printed)
#   tokens.json        the development bearer tokens (0600): prototype-operator and prototype-tenant
#                      (provisioning:all), prototype-reader (provisioning:none, refusals shown live),
#                      prototype-admin (provisioning:all and the ADMIN role: rollback, FR-50, and
#                      the admin paths of decision 0023 G9, on any project; no access.json entry,
#                      no user ID);
#                      repositories.json the repository locator
#   access.json, entitlement.json  the reloadable project-access and entitlement stand-ins (0600;
#                      created once with allow-all content; tests rewrite them atomically) (in cluster
#                      mode also ConfigMap provisioning-standins)
#   PREVIEW_TOKEN in provisioning.env: the worker's preview probe token (PREVIEW_PROBE_TOKEN; the
#                      preview route answers only requests carrying it; never printed)
#   logs/<UTC timestamp>/  one folder per `start`: <process>.log (JSON logs, no secrets), migrations.log
#                          and timings.txt (seconds per start-up phase); logs/latest links to the
#                          newest folder, restart and start-process append to it, and stop keeps all
#                          of them (L5, decision 0014 section 17)
#   pids/<process>.pid
#   replicas           the replica count `start` ran with (1 or 2)
#   mode, endpoints.env, image.env, values-env.json, values-stack.json  (task PK; see "Mode")
#
# Replicas (task H5b, phase P-H; .claude/docs/runbooks/replicas.md). `PROV_REPLICAS=2 run-provisioning.sh
# start` (make proto-run-replicas) also runs a second API, worker and build worker: api-2, worker-2
# and build_worker-2, each with its own env file (env/<name>.env, the first replica's file with its
# own listeners, and for the worker its own WORKER_ID), pid file, log file and ports. The default is
# one of each. There is no load balancer: the second API listens beside the first and tests call
# both. The bootstrap is a singleton (task H5c): its rounds run only while it holds a PostgreSQL
# advisory lock, so `start` runs one, and a replica run also writes env/bootstrap-2.env so that
# `start-process bootstrap-2` can start a second one, which stands by until the first one's session
# ends (the replica suite's standby test; .claude/docs/runbooks/replicas.md).
#
# Project access (task PA, decision 0017 section 2). The default is the reloadable access file
# (PROJECT_ACCESS_MODE=prototype-file). A caller may set, in the environment of `start`:
#   PROJECT_ACCESS_MODE         prototype-file (the default), prototype-allow or project-service
#   PROJECT_SERVICE_URL         the Project service's base URL (project-service only); the call
#                               carries no token (authentication design A3, 18 October 2026), so
#                               a PROJECT_SERVICE_TOKEN_FILE that is still set stops the start
#   PROTO_TENANT_USER_ID, PROTO_OPERATOR_USER_ID, PROTO_READER_USER_ID
#                               the platform user ID (a canonical lowercase UUID) of each development
#                               token's tokens.json entry; unset leaves the entry without one
# The Project service's checks are for owners only: a token without a user ID (the operator's, by
# default) is denied project access in project-service mode.
#
# The owner's `postgres` and `rabbitmq` containers are never touched: this script only creates,
# inspects and removes the two containers named above. No .env is read. The processes run with a
# cleared environment (env -i) plus their own file, so no KUBECONFIG or proxy variable reaches them;
# the worker and bootstrap processes get their project-local kubeconfig by explicit path, and the
# client refuses any kubeconfig that is not the local Kind cluster on 127.0.0.1.
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

# Pinned by digest; both are the images already used on this machine (task B2/H's PostgreSQL 18,
# task E2's RabbitMQ).
PROV_PG_IMAGE="postgres:18.6-alpine3.24@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873"
PROV_MQ_IMAGE="rabbitmq:4.3.6-management-alpine@sha256:3ef7f7e89fed709a78f79b8b5f4e30e1312a1d4089da752e23595566d9d61a95"
PROV_PG_NAME="webblock-provisioning-db"
PROV_MQ_NAME="webblock-rabbitmq"
PROV_API_PORT="${PROV_API_PORT:-18080}"
PROV_OPS_PORT="${PROV_OPS_PORT:-18081}"
# Task H4: the operational listeners (GET /metrics, GET /healthz) of the other long-lived processes.
PROV_WORKER_OPS_PORT="${PROV_WORKER_OPS_PORT:-18082}"
PROV_BUILD_WORKER_OPS_PORT="${PROV_BUILD_WORKER_OPS_PORT:-18083}"
PROV_BOOTSTRAP_OPS_PORT="${PROV_BOOTSTRAP_OPS_PORT:-18084}"
# Task H5b: the second replicas' listeners, clear of 18080-18084.
PROV_API2_PORT="${PROV_API2_PORT:-18090}"
PROV_OPS2_PORT="${PROV_OPS2_PORT:-18091}"
PROV_WORKER2_OPS_PORT="${PROV_WORKER2_OPS_PORT:-18092}"
PROV_BUILD_WORKER2_OPS_PORT="${PROV_BUILD_WORKER2_OPS_PORT:-18093}"
# Task H5c: the standby bootstrap's listener (started only by start-process bootstrap-2).
PROV_BOOTSTRAP2_OPS_PORT="${PROV_BOOTSTRAP2_OPS_PORT:-18094}"
PROCESSES=(api worker build_worker bootstrap)
# The processes `start` runs twice; bootstrap is not among them (a second one is started only by
# start-process bootstrap-2 and stands by; see the header).
REPLICATED=(api worker build_worker)
# Every name, second replicas included, in the order they are stopped.
ALL_NAMES=(bootstrap-2 bootstrap build_worker-2 build_worker worker-2 worker api-2 api)
# Task HF-A (H-V finding F-1): the processes this script restarts when they exit non-zero, as
# Kubernetes restarts a pod's container (supervise, below). Only the bootstraps: when a bootstrap
# stops, no new tenant ever becomes ready and nothing else notices, while the others' work is
# durable in SQL and their absence is seen at once (the API is waited on; a stopped worker or build
# worker leaves accepted rows that the next start claims), and the suites stop them on purpose.
SUPERVISED=(bootstrap bootstrap-2)
# The supervisor's first back-off before a restart, in milliseconds; it doubles up to 30 s while
# the bootstrap keeps exiting within a minute of its start.
PROV_RESTART_BACKOFF_MS="${PROV_RESTART_BACKOFF_MS:-1000}"

PROV="$PROTO/provisioning"
PROV_ENV="$PROV/provisioning.env"
ENV_DIR="$PROV/env"
RUNS="$PROV/logs"
LOGS="$RUNS/latest"
PIDS="$PROV/pids"
TOKENS="$PROV/tokens.json"
REPOSITORIES="$PROV/repositories.json"
PY="$kind_root/.venv/bin/python"
REPLICAS_FILE="$PROV/replicas"
MODE_FILE="$PROV/mode"
ENDPOINTS="$PROV/endpoints.env"
FOLLOWER_PID="$PIDS/log-follower.pid"
# Task PK: the chart, its release and namespace, and what the run script creates beside it.
NS=webblock
RELEASE=provisioning
CHART="$kind_root/deploy/helm/provisioning"
CLUSTER_SECRETS=(provisioning-api-secret provisioning-worker-secret provisioning-build-worker-secret
  provisioning-bootstrap-secret provisioning-migrate-secret provisioning-dev-tokens)
CLUSTER_CONFIGMAPS=(provisioning-standins provisioning-gateway-ca)

usage() { echo "usage: $0 start|stop|status|restart|start-process NAME|stop-process NAME|stop-processes|kill-process NAME|standins-sync|operator ARGS...|operator-as-worker ARGS...|operator-dlq ARGS..." >&2; exit 2; }

valid_process() {
  case "$1" in
    api | worker | build_worker | bootstrap | api-2 | worker-2 | build_worker-2 | bootstrap-2) ;;
    *) usage ;;
  esac
}

# replica_count: the count `start` recorded (1 when nothing was recorded).
replica_count() {
  local n=1
  [ -f "$REPLICAS_FILE" ] && n="$(cat "$REPLICAS_FILE")"
  case "$n" in 1 | 2) printf '%s' "$n" ;; *) die "$REPLICAS_FILE holds neither 1 nor 2" ;; esac
}

# process_names: the processes of this run, first replicas first.
process_names() {
  local name
  for name in "${PROCESSES[@]}"; do echo "$name"; done
  if [ "$(replica_count)" = 2 ]; then
    for name in "${REPLICATED[@]}"; do echo "$name-2"; done
  fi
}

supervised() {
  local s
  for s in "${SUPERVISED[@]}"; do [ "$s" = "$1" ] && return 0; done
  return 1
}

pid_of() { [ -f "$PIDS/$1.pid" ] && cat "$PIDS/$1.pid"; }

running() { local p; p="$(pid_of "$1")" && [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

# get_var FILE KEY: the value of KEY in a KEY=value or KEY='value' file, read without printing it.
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

container_port() { docker port "$1" "$2/tcp" | head -n 1 | sed 's/.*://'; }

# add_secret_if_missing KEY: appends KEY with a new random value to the secrets file unless it is
# there already; the value is never printed.
add_secret_if_missing() {
  get_var "$PROV_ENV" "$1" >/dev/null && return 0
  (umask 077 && python3 -I -c 'import secrets, sys
with open(sys.argv[1], "a") as f:
    f.write(f"{sys.argv[2]}={secrets.token_urlsafe(32)}\n")' "$PROV_ENV" "$1")
}

ensure_secrets() {
  if [ -f "$PROV_ENV" ]; then
    # A secrets file from before task T has no preview token, and one from before wave 6 no
    # operator password: add what is missing.
    add_secret_if_missing PREVIEW_TOKEN
    add_secret_if_missing OPERATOR_PASSWORD
    add_secret_if_missing TENANT_TOKEN
    add_secret_if_missing READER_TOKEN
    add_secret_if_missing ADMIN_TOKEN
    add_secret_if_missing MIGRATOR_PASSWORD
    return 0
  fi
  (umask 077 && python3 -I - "$PROV_ENV" <<'PY'
import secrets, sys
names = ("PG_SUPERUSER", "API", "BUILD", "WORKER", "HOSTING_INTAKE", "INTAKE", "BOOTSTRAP", "OPERATOR", "MQ", "TOKEN",
         "MIGRATOR")
with open(sys.argv[1], "x") as f:
    f.write("# Provisioning's prototype secrets (run-provisioning.sh). Never print this file.\n")
    for n in names:
        f.write(f"{n}_PASSWORD={secrets.token_urlsafe(32)}\n")
    # The preview route's probe token (task T, recheck N1): 43 URL-safe characters.
    f.write(f"PREVIEW_TOKEN={secrets.token_urlsafe(32)}\n")
    # The tenant's and the reader's development tokens (wave 6, task W6-D).
    f.write(f"TENANT_TOKEN={secrets.token_urlsafe(32)}\n")
    f.write(f"READER_TOKEN={secrets.token_urlsafe(32)}\n")
    # The admin's development token (Phase C, decision 0023 G10).
    f.write(f"ADMIN_TOKEN={secrets.token_urlsafe(32)}\n")
PY
  )
}

# remove_if_stopped NAME: one of this script's own containers that exited is removed, so that
# start creates it again.
remove_if_stopped() {
  if docker container inspect "$1" >/dev/null 2>&1 \
      && [ "$(docker container inspect --format '{{.State.Running}}' "$1")" != true ]; then
    docker rm -f "$1" >/dev/null
  fi
}

# join_kind NAME: one of this script's containers joins the Docker network kind (task PK), where the
# pods reach it by name; a container made before task PK, on the default bridge, joins it too.
join_kind() {
  docker container inspect --format '{{with index .NetworkSettings.Networks "kind"}}yes{{end}}' "$1" \
    | grep -qx yes || docker network connect kind "$1"
}

start_postgres() {
  remove_if_stopped "$PROV_PG_NAME"
  if ! docker container inspect "$PROV_PG_NAME" >/dev/null 2>&1; then
    log "starting $PROV_PG_NAME"
    local envf="$PROV/pg-container.env"
    (umask 077 && printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_HOST_AUTH_METHOD=scram-sha-256\nPOSTGRES_INITDB_ARGS=--auth-local=scram-sha-256 --auth-host=scram-sha-256\n' \
      "$(get_var "$PROV_ENV" PG_SUPERUSER_PASSWORD)" > "$envf")
    docker run -d --restart=no --name "$PROV_PG_NAME" --network kind -p 127.0.0.1::5432 --env-file "$envf" \
      "$PROV_PG_IMAGE" >/dev/null
    rm -f "$envf"
  fi
  join_kind "$PROV_PG_NAME"
  local i
  for i in $(seq 1 120); do
    docker exec "$PROV_PG_NAME" pg_isready -q -h 127.0.0.1 -U postgres 2>/dev/null && return 0
    sleep 0.5
  done
  die "$PROV_PG_NAME did not become ready"
}

# psql_admin DB: runs SQL from standard input as the superuser, with PGPASSWORD from the file.
psql_admin() {
  docker exec -i -e PGPASSWORD="$(get_var "$PROV_ENV" PG_SUPERUSER_PASSWORD)" "$PROV_PG_NAME" \
    psql -q -h 127.0.0.1 -U postgres -d "$1" -v ON_ERROR_STOP=1 >/dev/null
}

# A login role that is a member of exactly one group, with a SCRAM verifier computed here, so the
# cleartext password never reaches SQL or a server log.
ensure_login() {
  local login="$1" group="$2" key="$3" verifier
  verifier="$("$PY" -I -c 'import sys
from provisioning.infrastructure.catalog.scram import new_scram_verifier
sys.stdout.write(new_scram_verifier(sys.stdin.read().rstrip("\n")))' <<<"$(get_var "$PROV_ENV" "$key")")"
  psql_admin postgres <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$login') THEN
    CREATE ROLE $login LOGIN IN ROLE $group;
  END IF;
END \$\$;
ALTER ROLE $login PASSWORD '$verifier';
SQL
}

setup_database() {
  local mode="$1"
  log "database: roles, database, migrations, logins"
  psql_admin postgres < "$kind_root/scripts/db/bootstrap-roles.sql"
  if ! docker exec -e PGPASSWORD="$(get_var "$PROV_ENV" PG_SUPERUSER_PASSWORD)" "$PROV_PG_NAME" \
      psql -tA -h 127.0.0.1 -U postgres -d postgres -c "SELECT 1 FROM pg_database WHERE datname = 'provisioning'" | grep -q 1; then
    psql_admin postgres <<<"CREATE DATABASE provisioning OWNER provisioning_migrator;"
  fi
  # In cluster mode the chart's migration Job migrates, as the migrator's login (task PK).
  if [ "$mode" = host ]; then
    local port pw
    port="$(container_port "$PROV_PG_NAME" 5432)"
    pw="$(get_var "$PROV_ENV" PG_SUPERUSER_PASSWORD)"
    GOOSE_DRIVER=postgres GOOSE_MIGRATION_DIR="$kind_root/src/migrations" \
      GOOSE_DBSTRING="postgres://postgres:$pw@127.0.0.1:$port/provisioning?sslmode=disable&options=-c%20role%3Dprovisioning_migrator" \
      "$kind_root/bin/goose" up > "$LOGS/migrations.log" 2>&1 || { tail -n 5 "$LOGS/migrations.log" >&2; die "migrations failed"; }
  fi
  ensure_login prov_api_login provisioning_api API_PASSWORD
  ensure_login prov_build_login provisioning_build BUILD_PASSWORD
  ensure_login prov_worker_login provisioning_worker WORKER_PASSWORD
  ensure_login prov_hosting_login provisioning_hosting_intake HOSTING_INTAKE_PASSWORD
  ensure_login prov_intake_login provisioning_intake INTAKE_PASSWORD
  ensure_login prov_bootstrap_login provisioning_bootstrap BOOTSTRAP_PASSWORD
  # The operator CLI's login (decision 0014 section 21 ruling 11): the inbox re-drive only.
  ensure_login prov_operator_login provisioning_operator OPERATOR_PASSWORD
  # Task PK: the migration Job's login (decision 0016 section 3; a member of provisioning_migrator only).
  ensure_login prov_migrator_login provisioning_migrator MIGRATOR_PASSWORD
}

start_rabbitmq() {
  remove_if_stopped "$PROV_MQ_NAME"
  if ! docker container inspect "$PROV_MQ_NAME" >/dev/null 2>&1; then
    log "starting $PROV_MQ_NAME"
    local envf="$PROV/mq-container.env"
    (umask 077 && printf 'RABBITMQ_DEFAULT_USER=provisioning\nRABBITMQ_DEFAULT_PASS=%s\n' \
      "$(get_var "$PROV_ENV" MQ_PASSWORD)" > "$envf")
    docker run -d --restart=no --name "$PROV_MQ_NAME" --network kind -p 127.0.0.1::5672 -p 127.0.0.1::15672 \
      --env-file "$envf" "$PROV_MQ_IMAGE" >/dev/null
    rm -f "$envf"
  fi
  join_kind "$PROV_MQ_NAME"
  local i
  for i in $(seq 1 120); do
    # As the rabbitmq user: a root CLI call can create the Erlang cookie before the server does,
    # which the server then cannot read (eacces).
    docker exec -u rabbitmq "$PROV_MQ_NAME" rabbitmq-diagnostics -q check_port_connectivity >/dev/null 2>&1 && return 0
    [ "$(docker container inspect --format '{{.State.Running}}' "$PROV_MQ_NAME")" = true ] \
      || die "$PROV_MQ_NAME exited; see docker logs $PROV_MQ_NAME" 
    sleep 1
  done
  die "$PROV_MQ_NAME did not become ready"
}

write_files() {
  [ -f "$REPOSITORIES" ] || (umask 077 && printf '{"repositories": []}\n' > "$REPOSITORIES")
  # The reloadable stand-ins of W6-D, allow-all until a test rewrites them (write, then rename).
  [ -f "$PROV/access.json" ] || (umask 077 \
    && printf '%s\n' '{"subjects": [{"subject": "prototype-operator", "projects": "*"}]}' > "$PROV/access.json")
  [ -f "$PROV/entitlement.json" ] || (umask 077 \
    && printf '%s\n' '{"default_entitled": true, "projects": {}}' > "$PROV/entitlement.json")
  (umask 077 && python3 -I - "$TOKENS" "$(get_var "$PROV_ENV" TOKEN_PASSWORD)" \
    "$(get_var "$PROV_ENV" TENANT_TOKEN)" "$(get_var "$PROV_ENV" READER_TOKEN)" \
    "$(get_var "$PROV_ENV" ADMIN_TOKEN)" \
    "${PROTO_OPERATOR_USER_ID:-}" "${PROTO_TENANT_USER_ID:-}" "${PROTO_READER_USER_ID:-}" <<'PY'
import json, re, sys
path, operator, tenant, reader, admin, *users = sys.argv[1:9]
tokens = [
    {"token": operator, "subject": "prototype-operator", "scopes": ["provisioning:all"]},
    {"token": tenant, "subject": "prototype-tenant", "scopes": ["provisioning:all"]},
    {"token": reader, "subject": "prototype-reader", "scopes": ["provisioning:none"]},
]
# Phase C (decision 0023 G10): the admin paths need the ADMIN role, which a development token
# carries as a scope, as a JWT's roles claim does. No user ID: the admin paths ask no project.
admin_entry = {"token": admin, "subject": "prototype-admin", "scopes": ["provisioning:all", "ADMIN"]}
# Task PA: the optional platform user IDs (PROTO_<NAME>_USER_ID), checked here so that a typo
# fails before the API starts; the message names the variable, never the value.
names = ("PROTO_OPERATOR_USER_ID", "PROTO_TENANT_USER_ID", "PROTO_READER_USER_ID")
uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
for entry, name, user in zip(tokens, names, users, strict=True):
    if not user:
        continue
    if not uuid.fullmatch(user) or set(user) <= {"0", "-"}:
        sys.exit(f"{name} must be a canonical lowercase, non-nil UUID")
    entry["user_id"] = user
with open(path, "w") as f:
    json.dump({"tokens": [*tokens, admin_entry]}, f)
PY
  )
}

# q VALUE: the value in single quotes for an env file (generated values never hold a quote).
q() { case "$1" in *"'"*) die "a configuration value holds a quote" ;; esac; printf "'%s'" "$1"; }

write_env() {
  local name="$1"; shift
  (umask 077 && : > "$ENV_DIR/$name.env")
  local kv
  for kv in "$@"; do printf '%s=%s\n' "${kv%%=*}" "$(q "${kv#*=}")" >> "$ENV_DIR/$name.env"; done
}

# derive_env FROM TO KEY=VALUE...: env/TO.env is env/FROM.env with each KEY's value replaced (task
# H5b, the second replicas). Every KEY must already be in FROM, so a replica differs from the first
# only where it must; no value is printed.
derive_env() {
  local from="$ENV_DIR/$1.env" to="$ENV_DIR/$2.env"; shift 2
  [ -f "$from" ] || die "$from is missing"
  local kv overrides=()
  for kv in "$@"; do overrides+=("${kv%%=*}=$(q "${kv#*=}")"); done
  (umask 077 && python3 -I - "$from" "$to" "${overrides[@]}" <<'PY'
import sys
source, target, *pairs = sys.argv[1:]
wanted = dict(pair.split("=", 1) for pair in pairs)
seen = set()
lines = []
with open(source) as f:
    for line in f:
        key = line.split("=", 1)[0]
        if key in wanted:
            line = f"{key}={wanted[key]}\n"
            seen.add(key)
        lines.append(line)
missing = sorted(set(wanted) - seen)
if missing:
    sys.exit("derive_env: not in the source file: " + ",".join(missing))
with open(target, "w") as f:
    f.writelines(lines)
PY
  )
}

# configure_replicas: the second API, worker and build worker (PROV_REPLICAS=2). The API keeps its
# MCP host allow-list to its own port; the worker gets its own WORKER_ID, so claims and logs name it.
configure_replicas() {
  derive_env api api-2 "HTTP_LISTEN_ADDR=127.0.0.1:$PROV_API2_PORT" \
    "OPS_LISTEN_ADDR=127.0.0.1:$PROV_OPS2_PORT" \
    "MCP_ALLOWED_HOSTS=127.0.0.1:$PROV_API2_PORT,localhost:$PROV_API2_PORT"
  derive_env worker worker-2 "OPS_LISTEN_ADDR=127.0.0.1:$PROV_WORKER2_OPS_PORT" \
    WORKER_ID=prototype-worker-2
  derive_env build_worker build_worker-2 "OPS_LISTEN_ADDR=127.0.0.1:$PROV_BUILD_WORKER2_OPS_PORT"
  # Task H5c: a second bootstrap stands by on the singleton lock; only start-process starts it.
  derive_env bootstrap bootstrap-2 "OPS_LISTEN_ADDR=127.0.0.1:$PROV_BOOTSTRAP2_OPS_PORT"
}

# refuse_stale_token_file: a PROJECT_SERVICE_TOKEN_FILE that is still set stops the start, naming
# the variable and never its value (finding N-1 of the PR-3 review; the API refuses it too).
refuse_stale_token_file() {
  [ -z "${PROJECT_SERVICE_TOKEN_FILE:-}" ] \
    || die "PROJECT_SERVICE_TOKEN_FILE is gone: calls to the Project service carry no token (authentication design A3); unset it"
}

# access_settings [FILE]: sets the caller's array `access` to the API's project-access keys (task PA):
# the access file FILE by default ($PROV/access.json; in cluster mode the chart's mount path, task PK),
# or what the caller's PROJECT_ACCESS_MODE and its URL name.
access_settings() {
  refuse_stale_token_file
  local file="${1:-$PROV/access.json}"
  case "${PROJECT_ACCESS_MODE:-prototype-file}" in
    prototype-file)
      access=(PROJECT_ACCESS_MODE=prototype-file "PROJECT_ACCESS_FILE=$file") ;;
    prototype-allow)
      access=(PROJECT_ACCESS_MODE=prototype-allow) ;;
    project-service)
      [ -n "${PROJECT_SERVICE_URL:-}" ] || die "PROJECT_ACCESS_MODE=project-service needs PROJECT_SERVICE_URL"
      access=(PROJECT_ACCESS_MODE=project-service "PROJECT_SERVICE_URL=$PROJECT_SERVICE_URL") ;;
    *) die "PROJECT_ACCESS_MODE must be prototype-file, prototype-allow or project-service" ;;
  esac
}

configure() {
  local mode="$1"
  [ -f "$STACK_ENV" ] || die "$STACK_ENV is missing; run make proto-up first"
  [ -f "$JENKINS_ENV" ] || die "$JENKINS_ENV is missing; run make proto-up first"
  [ -f "$CATALOG_ENV" ] || die "$CATALOG_ENV is missing; run make proto-up first"
  local pg_port mq_port gateway_https registry offline_port catalog_rt mq_pass
  pg_port="$(container_port "$PROV_PG_NAME" 5432)"
  mq_port="$(container_port "$PROV_MQ_NAME" 5672)"
  gateway_https="$(get_var "$STACK_ENV" PROTO_GATEWAY_HTTPS_URL)" \
    || die "$STACK_ENV has no PROTO_GATEWAY_HTTPS_URL; the stack predates task T1, run make proto-up"
  [ -f "$PROTO/local-ca.crt" ] || die "$PROTO/local-ca.crt is missing; the stack predates task T1"
  registry="$(get_var "$STACK_ENV" PROTO_IMAGE_REGISTRY)"
  offline_port="$(get_var "$STACK_ENV" PROTO_OFFLINE_SERVICE)"; offline_port="${offline_port##*:}"
  catalog_rt="$(get_var "$STACK_ENV" PROTO_CATALOG_IN_CLUSTER)"
  mq_pass="$(get_var "$PROV_ENV" MQ_PASSWORD)"
  local db_rest=(PG_DATABASE=provisioning PG_SSLMODE=disable DB_MAX_CONNS=8 DB_CONNECT_TIMEOUT=5s
    DB_TX_TIMEOUT=30s DB_STATEMENT_TIMEOUT=10s DB_LOCK_TIMEOUT=5s)
  # The operator CLI runs on the host in both modes: the published ports.
  local host_db=(PG_HOST=127.0.0.1 "PG_PORT=$pg_port" "${db_rest[@]}")
  local host_mq_url="amqp://provisioning:$mq_pass@127.0.0.1:$mq_port/"
  # Task PK: where the four processes reach their dependencies and find their files. On the host, the
  # published ports and the files in $PROV; in the cluster, the containers by name on the kind network
  # and the chart's mounts (deploy/helm/provisioning).
  local db mq_url jenkins_url catalog api_listen api_ops worker_ops build_ops bootstrap_ops mcp_hosts
  local tokens_file standins gateway_ca worker_kube bootstrap_kube worker_id
  if [ "$mode" = cluster ]; then
    db=("PG_HOST=$PROV_PG_NAME" PG_PORT=5432 "${db_rest[@]}")
    mq_url="amqp://provisioning:$mq_pass@$PROV_MQ_NAME:5672/"
    jenkins_url="http://$PROTO_JENKINS_NAME:8080"
    catalog=("CATALOG_HOST=${catalog_rt%%:*}" "CATALOG_PORT=${catalog_rt##*:}")
    api_listen=0.0.0.0:8080 api_ops=0.0.0.0:8081
    worker_ops=0.0.0.0:9090 build_ops=0.0.0.0:9090 bootstrap_ops=0.0.0.0:9090
    mcp_hosts="$(api_address):8080"
    tokens_file=/etc/webblock/dev-tokens/tokens.json standins=/etc/webblock/standins
    gateway_ca=/etc/webblock/gateway-ca/ca.crt
    worker_kube=(KUBE_CONNECTION=in-cluster KUBE_TOKEN_FILE=/var/run/secrets/webblock/kube/token
      KUBE_CA_FILE=/var/run/secrets/webblock/kube/ca.crt
      KUBE_EXPECTED_USERNAME=system:serviceaccount:webblock:provisioning-worker)
    bootstrap_kube=(KUBE_CONNECTION=in-cluster KUBE_TOKEN_FILE=/var/run/secrets/webblock/kube/token
      KUBE_CA_FILE=/var/run/secrets/webblock/kube/ca.crt
      KUBE_EXPECTED_USERNAME=system:serviceaccount:webblock:tenant-bootstrap)
    worker_id=()  # the chart sets WORKER_ID to the pod's name
  else
    db=("${host_db[@]}")
    mq_url="$host_mq_url"
    jenkins_url="$(get_var "$JENKINS_ENV" JENKINS_URL)"
    catalog=("CATALOG_HOST=$(get_var "$STACK_ENV" PROTO_CATALOG_HOST_ON_HOST)"
      "CATALOG_PORT=$(get_var "$STACK_ENV" PROTO_CATALOG_PORT_ON_HOST)")
    api_listen="127.0.0.1:$PROV_API_PORT" api_ops="127.0.0.1:$PROV_OPS_PORT"
    worker_ops="127.0.0.1:$PROV_WORKER_OPS_PORT" build_ops="127.0.0.1:$PROV_BUILD_WORKER_OPS_PORT"
    bootstrap_ops="127.0.0.1:$PROV_BOOTSTRAP_OPS_PORT"
    mcp_hosts="127.0.0.1:$PROV_API_PORT,localhost:$PROV_API_PORT"
    tokens_file="$TOKENS" standins="$PROV" gateway_ca="$PROTO/local-ca.crt"
    worker_kube=(KUBE_CONNECTION=kubeconfig "WORKER_KUBECONFIG=$KCFG_WORKER")
    bootstrap_kube=(KUBE_CONNECTION=kubeconfig "BOOTSTRAP_KUBECONFIG=$KCFG_BOOTSTRAP")
    worker_id=(WORKER_ID=prototype-worker-1)
  fi
  local mq=("RABBITMQ_URL=$mq_url" RABBITMQ_HEARTBEAT=10s RABBITMQ_DIAL_TIMEOUT=5s
    RABBITMQ_RECONNECT_BACKOFF_BASE=200ms RABBITMQ_RECONNECT_BACKOFF_MAX=5s RABBITMQ_DECLARE_TOPOLOGY=true
    RABBITMQ_MAX_IN_FLIGHT_PUBLISHES=64 RABBITMQ_MAX_MESSAGE_BYTES=65536 RABBITMQ_QUEUE_MAX_LENGTH=10000
    RABBITMQ_DELIVERY_LIMIT=5 RABBITMQ_CONSUMER_TIMEOUT=30m RABBITMQ_DLQ_MAX_LENGTH=10000
    RABBITMQ_DLQ_MESSAGE_TTL=168h)
  local build=("BUILD_IMAGE_REGISTRY=$registry" BUILD_DURATION=20m BUILD_CALL_TIMEOUT=30s BUILD_MAX_ATTEMPTS=3
    "JENKINS_URL=$jenkins_url" "JENKINS_USER=$(get_var "$JENKINS_ENV" JENKINS_USER)"
    "JENKINS_API_TOKEN=$(get_var "$JENKINS_ENV" JENKINS_API_TOKEN)"
    "JENKINS_JOB=$(get_var "$STACK_ENV" PROTO_JENKINS_JOB)" JENKINS_REQUEST_TIMEOUT=10s
    JENKINS_OPERATION_TIMEOUT=25s JENKINS_QUEUE_POLLS=20 JENKINS_QUEUE_POLL_INTERVAL=500ms
    JENKINS_RECENT_BUILDS=100 REPOSITORY_MODE=prototype-file "PROTOTYPE_REPOSITORIES_FILE=$standins/repositories.json")
  # Task H2: every process requires its profile; the prototype runs its stand-ins.
  local profile=PROVISIONING_PROFILE=prototype
  # Task H4: every long-lived process requires SENTRY_DSN; the prototype sends nothing to Sentry.
  local sentry=SENTRY_DSN=none
  # Decision 0022 E9: tracing off (no collector in the prototype); each process names itself.
  local otel=OTEL_EXPORTER_OTLP_ENDPOINT=none
  local access
  access_settings "$standins/access.json"
  local scopes="" a
  for a in deploy_release promote_release get_release get_command get_deployment get_release_diagnostics build_release list_releases list_commands; do
    scopes="${scopes:+$scopes,}$a=provisioning:all"
  done
  # The admin-only actions need the ADMIN scope: rollback (FR-50) and the four of Phase C (0023 G10).
  for a in rollback_release suspend_website resume_website redeploy_release admin_list_commands; do
    scopes="$scopes,$a=ADMIN"
  done
  # The API holds neither the Jenkins token nor the provisioning_build login: build_release only
  # records the wish (the pending step) and sends the build hint (decision 0016 section 5 item 2).
  write_env api "$profile" "$sentry" "$otel" OTEL_SERVICE_NAME=provisioning-api LOG_LEVEL=info COMMAND_DEADLINE=15m "${db[@]}" \
    PG_USER=prov_api_login "PG_PASS=$(get_var "$PROV_ENV" API_PASSWORD)" \
    "HTTP_LISTEN_ADDR=$api_listen" "OPS_LISTEN_ADDR=$api_ops" \
    HTTP_REQUEST_TIMEOUT=60s HTTP_IDLE_TIMEOUT=60s HTTP_SHUTDOWN_GRACE=10s HTTP_MAX_HEADER_BYTES=16384 \
    HTTP_MAX_REQUEST_BODY_BYTES=65536 HTTP_MAX_IN_FLIGHT=64 MCP_MAX_REQUEST_BODY_BYTES=65536 \
    MCP_ALLOWED_ORIGINS=none "MCP_ALLOWED_HOSTS=$mcp_hosts" \
    IDENTITY_MODE=dev-tokens "AUTH_DEV_TOKENS_FILE=$tokens_file" "AUTH_ACTION_SCOPES=$scopes" \
    "${access[@]}" \
    ENTITLEMENT_MODE=prototype-file "ENTITLEMENT_FILE=$standins/entitlement.json"
  write_env worker "$profile" "$sentry" "$otel" OTEL_SERVICE_NAME=provisioning-worker "OPS_LISTEN_ADDR=$worker_ops" \
    LOG_LEVEL=info "${db[@]}" "${mq[@]}" \
    PG_USER=prov_worker_login "PG_PASS=$(get_var "$PROV_ENV" WORKER_PASSWORD)" \
    PG_HOSTING_INTAKE_USER=prov_hosting_login "PG_HOSTING_INTAKE_PASS=$(get_var "$PROV_ENV" HOSTING_INTAKE_PASSWORD)" \
    COMMAND_DEADLINE=15m ENTITLEMENT_MODE=prototype-file "ENTITLEMENT_FILE=$standins/entitlement.json" \
    HOSTING_ENTITLEMENT_MODE=prototype-payment-events \
    DISPATCH_POLL_INTERVAL=250ms DISPATCH_BATCH_SIZE=50 DISPATCH_PUBLISH_CONCURRENCY=8 \
    DISPATCH_PUBLISH_CONFIRM_TIMEOUT=5s DISPATCH_VISIBILITY_TIMEOUT=10s DISPATCH_REDELIVERY_INTERVAL=10s \
    DISPATCH_BACKOFF_BASE=250ms DISPATCH_BACKOFF_MAX=5s \
    "${worker_id[@]}" WORKER_CONCURRENCY=8 WORKER_LEASE_TTL=15s WORKER_HEARTBEAT_INTERVAL=3s \
    WORKER_ATTEMPT_TIMEOUT=10m WORKER_MAX_ATTEMPTS=20 WORKER_RETRY_BACKOFF_BASE=1s WORKER_RETRY_BACKOFF_MAX=10s \
    WORKER_STORE_HOLD_BASE=200ms WORKER_STORE_HOLD_MAX=5s WORKER_STORE_FAILURE_LIMIT=5 \
    "DEPLOY_IMAGE_REGISTRY=$registry" DEPLOY_READY_TIMEOUT=150s DEPLOY_POLL_INTERVAL=1s DEPLOY_CALL_TIMEOUT=10s \
    DEPLOY_DELETE_TIMEOUT=30s DEPLOY_FINISH_MARGIN=30s DEPLOY_ABANDON_TIMEOUT=30s \
    SITE_CPU_MILLICORES=100 SITE_MEMORY_MIB=256 SITE_PROGRESS_DEADLINE=60s "SITE_OFFLINE_SERVICE_PORT=$offline_port" \
    "SITE_DOMAIN=$(get_var "$STACK_ENV" PROTO_SITE_HOST_SUFFIX)" "SITE_GATEWAY_HTTPS_URL=$gateway_https" \
    "SITE_GATEWAY_CA_FILE=$gateway_ca" \
    "PREVIEW_PROBE_TOKEN=$(get_var "$PROV_ENV" PREVIEW_TOKEN)" \
    SITE_PROBE_PATH=/readyz SITE_PROBE_TIMEOUT=5s SITE_PROBE_SUCCESSES=3 SITE_DELETE_RETRY_BASE=250ms \
    ROUTE_SWITCH_DEADLINE=20s ROUTE_RESTORE_DEADLINE=60s ROUTE_POLL_INTERVAL=500ms \
    PAYMENT_CONSUMER_CONCURRENCY=2 PAYMENT_RETRY_BACKOFF_BASE=1s PAYMENT_RETRY_BACKOFF_MAX=10s PAYMENT_MAX_RETRIES=100 \
    "${worker_kube[@]}" KUBE_REQUEST_TIMEOUT=10s
  write_env build_worker "$profile" "$sentry" "$otel" OTEL_SERVICE_NAME=provisioning-build-worker "OPS_LISTEN_ADDR=$build_ops" \
    LOG_LEVEL=info "${db[@]}" "${mq[@]}" "${build[@]}" \
    PG_INTAKE_USER=prov_intake_login "PG_INTAKE_PASS=$(get_var "$PROV_ENV" INTAKE_PASSWORD)" \
    PG_BUILD_USER=prov_build_login "PG_BUILD_PASS=$(get_var "$PROV_ENV" BUILD_PASSWORD)" \
    INBOX_CONSUMER_CONCURRENCY=4 INBOX_STORE_HOLD_BASE=200ms INBOX_STORE_HOLD_MAX=5s INBOX_STORE_FAILURE_LIMIT=5 \
    INBOX_POLL_INTERVAL=1s INBOX_BATCH_SIZE=10 INBOX_MAX_PER_PROJECT=5 INBOX_VISIBILITY_TIMEOUT=10m \
    INBOX_CONCURRENCY=5 INBOX_REGISTRY_TIMEOUT=5s INBOX_TX_TIMEOUT=60s INBOX_MAX_ATTEMPTS=10 INBOX_DEADLINE=1h \
    INBOX_BACKOFF_BASE=1s INBOX_BACKOFF_MAX=30s INTAKE_MAX_PENDING_PER_PROJECT=20 \
    BUILD_POLL_INTERVAL=2s BUILD_POLL_LIMIT=50 BUILD_POLL_CONCURRENCY=4 BUILD_SWEEP_INTERVAL=30s \
    BUILD_SWEEP_LIMIT=50 BUILD_SWEEP_CONCURRENCY=4 BUILD_SWEEP_TX_TIMEOUT=10s BUILD_DISPATCH_GRACE=1m \
    BUILD_HINT_QUEUE_SIZE=256 BUILD_HINT_LIVENESS=30s BUILD_HINT_RECONNECT_BASE=500ms \
    BUILD_HINT_RECONNECT_MAX=10s
  # The operator CLI's environment (decision 0014 section 21 ruling 11): the database only.
  write_env operator "$profile" LOG_LEVEL=info "${host_db[@]}" \
    PG_USER=prov_operator_login "PG_PASS=$(get_var "$PROV_ENV" OPERATOR_PASSWORD)"
  # The operator CLI with the worker's login, which the database refuses a re-drive (the runbook's
  # proof); on the host in both modes (task PK).
  write_env operator_as_worker "$profile" LOG_LEVEL=info "${host_db[@]}" \
    PG_USER=prov_worker_login "PG_PASS=$(get_var "$PROV_ENV" WORKER_PASSWORD)"
  write_env bootstrap "$profile" "$sentry" "$otel" OTEL_SERVICE_NAME=provisioning-bootstrap "OPS_LISTEN_ADDR=$bootstrap_ops" \
    LOG_LEVEL=info "${db[@]}" \
    PG_USER=prov_bootstrap_login "PG_PASS=$(get_var "$PROV_ENV" BOOTSTRAP_PASSWORD)" \
    "${bootstrap_kube[@]}" KUBE_REQUEST_TIMEOUT=10s \
    TENANT_QUOTA_CPU=3 TENANT_QUOTA_MEMORY=3Gi TENANT_QUOTA_PODS=10 TENANT_CONTAINER_MAX_CPU=1 \
    TENANT_CONTAINER_MAX_MEMORY=1Gi BOOTSTRAP_INTERVAL=1s BOOTSTRAP_BATCH_SIZE=50 BOOTSTRAP_CONCURRENCY=4 \
    BOOTSTRAP_CALL_TIMEOUT=60s "${catalog[@]}" \
    "CATALOG_DATABASE=$(get_var "$STACK_ENV" PROTO_CATALOG_DATABASE)" CATALOG_SSLMODE=disable \
    "CATALOG_ADMIN_USER=$(get_var "$CATALOG_ENV" CATALOG_ACCESS_ADMIN_USER)" \
    "CATALOG_ADMIN_PASS=$(get_var "$CATALOG_ENV" CATALOG_ACCESS_ADMIN_PASSWORD)" \
    CATALOG_CONNECT_TIMEOUT=5s CATALOG_STATEMENT_TIMEOUT=5s CATALOG_LOCK_TIMEOUT=2s \
    "CATALOG_RUNTIME_HOST=${catalog_rt%%:*}" "CATALOG_RUNTIME_PORT=${catalog_rt##*:}"
  if [ "$mode" = cluster ]; then
    # The migration Job's database URL: the migrator's login, with its role set, so that the guard of
    # src/migrations/00001 passes (the chart's Job reads it from Secret provisioning-migrate-secret).
    write_env migrate "GOOSE_DBSTRING=postgres://prov_migrator_login:$(get_var "$PROV_ENV" MIGRATOR_PASSWORD)@$PROV_PG_NAME:5432/provisioning?sslmode=disable&options=-c%20role%3Dprovisioning_migrator"
  fi
  # The operator CLI's dead-letter commands (task W6-E2): the broker only, no database setting.
  # The prototype broker has one user, so this is the same broker identity the processes use.
  write_env operator_dlq "$profile" LOG_LEVEL=info "RABBITMQ_URL=$host_mq_url" RABBITMQ_DIAL_TIMEOUT=5s \
    DLQ_CALL_TIMEOUT=10s
}

# supervise NAME ENVFILE PY MODULE SRC HOME BACKOFF_MS: runs `PY -m provisioning.hosts.MODULE` with
# a cleared environment plus ENVFILE, and starts it again whenever it exits non-zero, after a
# back-off that starts at BACKOFF_MS and doubles up to 30 s (back to BACKOFF_MS once a run lasted a
# minute), as Kubernetes restarts a pod's container (task HF-A; H-V finding F-1). Each restart is
# one JSON line on standard output, which is the process's log. SIGTERM or SIGINT is passed on to
# the process once, and when it has exited the supervisor exits with its status and never restarts
# it; a process that exits 0 ends the supervisor too. start_process runs it in a session of its
# own, so kill_process can SIGKILL the supervisor and its process together.
supervise() {
  local name="$1" envfile="$2" py="$3" module="$4" src="$5" home="$6" base_ms="$7"
  local stopping=0 child="" sent="" status rc started delay_ms="$7" restarts=0
  trap 'stopping=1; if [ -n "$child" ]; then kill -TERM "$child" 2>/dev/null; sent="$child"; fi' TERM INT
  while :; do
    started=$SECONDS
    env -i PATH=/usr/bin:/bin LANG=C.UTF-8 HOME="$home" PYTHONPATH="$src" \
      bash -c 'set -a; . "$1"; set +a; exec "$2" -m "provisioning.hosts.$3"' _ \
      "$envfile" "$py" "$module" &
    child=$!
    # A stop that came before the line above is passed on here (and never twice: a second
    # SIGTERM ends the bootstrap at once).
    if [ "$stopping" = 1 ] && [ "$sent" != "$child" ]; then kill -TERM "$child" 2>/dev/null; sent="$child"; fi
    status=""
    while [ -z "$status" ]; do
      wait "$child"; rc=$?
      kill -0 "$child" 2>/dev/null || status="$rc" # still there: the wait was ended by a signal
    done
    child=""
    if [ "$stopping" = 1 ] || [ "$status" = 0 ]; then return "$status"; fi
    restarts=$((restarts + 1))
    [ $((SECONDS - started)) -lt 60 ] || delay_ms="$base_ms"
    printf '{"time":"%s","level":"warning","msg":"%s exited with status %s; the run script restarts it after %s ms","process":"%s","supervisor":"run-provisioning.sh","exit_status":%s,"restart":%s,"backoff_ms":%s}\n' \
      "$(date -u +%Y-%m-%dT%H:%M:%S.%6N+00:00)" "$name" "$status" "$delay_ms" "$name" "$status" \
      "$restarts" "$delay_ms"
    sleep "$((delay_ms / 1000)).$(printf '%03d' $((delay_ms % 1000)))" &
    child=$!
    wait "$child"
    child=""
    [ "$stopping" = 0 ] || return "$status"
    delay_ms=$((delay_ms * 2 > 30000 ? 30000 : delay_ms * 2))
  done
}

start_process() {
  local name="$1"
  valid_process "$name"
  if running "$name"; then log "$name already running (pid $(pid_of "$name"))"; return 0; fi
  [ -f "$ENV_DIR/$name.env" ] || die "$ENV_DIR/$name.env is missing; run $0 start"
  # A cleared environment plus the process's own file; standard output and errors go to its log.
  # A second replica (<process>-2) runs the same module as the first. A supervised process runs
  # under supervise in a session of its own (setsid), and its pid file names the supervisor.
  if supervised "$name"; then
    setsid bash -c "$(declare -f supervise)"'; supervise "$@"' _ "$name" "$ENV_DIR/$name.env" \
      "$PY" "${name%-2}" "$kind_root/src" "$TOOLHOME" "$PROV_RESTART_BACKOFF_MS" \
      >> "$LOGS/$name.log" 2>&1 < /dev/null &
  else
    env -i PATH=/usr/bin:/bin LANG=C.UTF-8 HOME="$TOOLHOME" PYTHONPATH="$kind_root/src" \
      bash -c 'set -a; . "$1"; set +a; exec "$2" -m "provisioning.hosts.$3"' _ \
      "$ENV_DIR/$name.env" "$PY" "${name%-2}" >> "$LOGS/$name.log" 2>&1 < /dev/null &
  fi
  echo $! > "$PIDS/$name.pid"
  log "$name started (pid $!)"
}

# stop_process NAME: SIGTERM, then SIGKILL after 30 s. A supervised process's supervisor passes
# the SIGTERM on and exits once the process has, without a restart; the SIGKILL goes to its whole
# session.
stop_process() {
  local name="$1" p i
  valid_process "$name"
  p="$(pid_of "$name")" || return 0
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
    kill -TERM "$p" 2>/dev/null || true
    for i in $(seq 1 60); do kill -0 "$p" 2>/dev/null || break; sleep 0.5; done
    if kill -0 "$p" 2>/dev/null; then
      if supervised "$name"; then kill -KILL -- "-$p" 2>/dev/null || true; else kill -KILL "$p" 2>/dev/null || true; fi
      log "$name killed after 30 s"
    fi
  fi
  rm -f "$PIDS/$name.pid"
}

# kill_process NAME: SIGKILL at once; for a supervised process, to its whole session (the
# supervisor and the process together), so nothing restarts it.
kill_process() {
  local name="$1" p
  valid_process "$name"
  p="$(pid_of "$name")" || return 0
  if [ -n "$p" ]; then
    if supervised "$name"; then kill -KILL -- "-$p" 2>/dev/null || true; else kill -KILL "$p" 2>/dev/null || true; fi
  fi
  for _ in $(seq 1 20); do kill -0 "$p" 2>/dev/null || break; sleep 0.1; done
  rm -f "$PIDS/$name.pid"
  log "$name killed (SIGKILL)"
}

wait_api_ready() {
  local name="$1" port="$2" i code
  for i in $(seq 1 120); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/readyz" || true)"
    [ "$code" = 200 ] && break
    running "$name" || { tail -n 5 "$LOGS/$name.log" >&2; die "the $name process exited"; }
    sleep 0.5
  done
  [ "$code" = 200 ] || die "the $name process did not become ready"
}

wait_ready() {
  local i name others=(worker build_worker bootstrap)
  wait_api_ready api "$PROV_OPS_PORT"
  if [ "$(replica_count)" = 2 ]; then
    wait_api_ready api-2 "$PROV_OPS2_PORT"
    others+=(worker-2 build_worker-2)
  fi
  for name in "${others[@]}"; do
    for i in $(seq 1 60); do
      grep -q 'running' "$LOGS/$name.log" 2>/dev/null && break
      running "$name" || { tail -n 5 "$LOGS/$name.log" >&2; die "the $name process exited"; }
      sleep 0.5
    done
    running "$name" || die "the $name process is not running"
  done
}

# The operator CLI in the foreground with a cleared environment plus one process's file: the
# operator's own, or the worker's to show that its login is refused. Its exit status is the CLI's.
run_operator() {
  local envname="$1"; shift
  [ -f "$ENV_DIR/$envname.env" ] || die "$ENV_DIR/$envname.env is missing; run $0 start"
  mkdir -p "$LOGS"
  env -i PATH=/usr/bin:/bin LANG=C.UTF-8 HOME="$TOOLHOME" PYTHONPATH="$kind_root/src" \
    bash -c 'set -a; . "$1"; set +a; shift; exec "$@"' _ \
    "$ENV_DIR/$envname.env" "$PY" -m provisioning.hosts.operator "$@" \
    2> >(tee -a "$LOGS/operator.log" >&2)
}

# run_mode: the mode `start` recorded (host when none was recorded).
run_mode() {
  local m=host
  [ -f "$MODE_FILE" ] && m="$(cat "$MODE_FILE")"
  case "$m" in
    host | cluster) printf '%s' "$m" ;;
    *) die "$MODE_FILE holds neither host nor cluster; write the mode of the running stack into it (host or cluster) and run the command again" ;;
  esac
}
# cluster_mode: whether the recorded run is a cluster run. Fails closed (PK-3 review): a damaged record
# stops the subcommand here, with the record kept, instead of being taken for a host run (bash ignores
# the status of a command substitution inside [ ]).
cluster_mode() { local m; m="$(run_mode)" || exit 1; [ "$m" = cluster ]; }

# start_mode: the mode of this start (PROV_MODE; host by default), after the checks of task PK: a
# recorded run of the other mode must be stopped first, and the cluster mode runs one replica of each
# process with the file-based project access (decision 0016 section 9 ruling 1).
start_mode() {
  local mode="${PROV_MODE:-host}" recorded
  case "$mode" in host | cluster) ;; *) die "PROV_MODE must be host or cluster" ;; esac
  recorded="$(run_mode)" || exit 1
  if [ -f "$MODE_FILE" ] && [ "$recorded" != "$mode" ]; then
    die "a $recorded-mode run is recorded ($MODE_FILE); run make proto-stop first"
  fi
  if [ "$mode" = cluster ]; then
    [ "${PROV_REPLICAS:-1}" = 1 ] || die "PROV_REPLICAS=2 runs on the host only; the cluster mode runs one replica of each process"
    [ "${PROJECT_ACCESS_MODE:-prototype-file}" != project-service ] \
      || die "PROJECT_ACCESS_MODE=project-service runs on the host only (the pods have no route to the Project service)"
  fi
  printf '%s' "$mode"
}

# api_address: the API Service's address in cluster mode. Kind: its fixed LB-IPAM address, the last
# address of the pool proto-up made (the Gateway takes the first free one). k3d (spike design G5): k3s
# ServiceLB serves every LoadBalancer Service on the node addresses, so the API answers on the same
# node address as the Traefik Service (stack.env PROTO_GATEWAY_ADDRESS), on its own port 8080.
api_address() {
  local ip
  if [ "$ENGINE" = k3d ]; then
    ip="$(k -n kube-system get service traefik -o jsonpath='{range .status.loadBalancer.ingress[*]}{.ip}{"\n"}{end}' \
      | grep -E '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' | head -n 1)" || true
    [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "cannot read the load balancer address of Service kube-system/traefik"
  else
    ip="$(k get ciliumloadbalancerippool webblock-kind-pool -o jsonpath='{.spec.blocks[0].stop}')"
    [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "cannot read the last address of LB-IPAM pool webblock-kind-pool"
  fi
  printf '%s' "$ip"
}

# container_ip NAME: a container's IPv4 address on the kind network.
container_ip() {
  local ip
  ip="$(docker container inspect --format '{{with index .NetworkSettings.Networks "kind"}}{{.IPAddress}}{{end}}' "$1")"
  [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "$1 has no IPv4 address on the kind network"
  printf '%s' "$ip"
}

# write_endpoints MODE: endpoints.env for the e2e harness (tests/e2e/_stack.py), in both modes. On the
# host, API_URL is the address the API listens on (HTTP_LISTEN_ADDR of env/api.env, which the harness
# read before task PK), and on a replica run API2_URL the second API's (env/api-2.env); in the cluster,
# the API Service's LoadBalancer address.
write_endpoints() {
  local api api2=""
  if [ "$1" = cluster ]; then
    api="http://$(api_address):8080"
  else
    api="http://$(get_var "$ENV_DIR/api.env" HTTP_LISTEN_ADDR)"
    [ "$(replica_count)" = 1 ] || api2="http://$(get_var "$ENV_DIR/api-2.env" HTTP_LISTEN_ADDR)"
  fi
  (umask 077 && {
    printf 'API_URL=%s\n' "$api"
    [ -z "$api2" ] || printf 'API2_URL=%s\n' "$api2"
  } > "$ENDPOINTS")
}

# The chart's Deployment of a process, and the selector of its pods. Cluster mode runs one replica of
# each process, so there is no -2 name.
deployment_of() {
  case "$1" in
    api | worker | bootstrap) printf 'provisioning-%s' "$1" ;;
    build_worker) printf 'provisioning-build-worker' ;;
    *) die "$1: the cluster mode runs one replica of each process" ;;
  esac
}
selector_of() { printf 'app.kubernetes.io/instance=%s,app.kubernetes.io/component=%s' "$RELEASE" "${1/_/-}"; }

# wait_no_pods NAME: until no pod of the process is left (its grace period is 30 s).
wait_no_pods() {
  local i
  for i in $(seq 1 90); do
    [ -z "$(k -n "$NS" get pods -l "$(selector_of "$1")" -o name)" ] && return 0
    sleep 1
  done
  die "pods of $1 are still present after 90 s"
}

cluster_stop_process() {
  k -n "$NS" scale "deployment/$(deployment_of "$1")" --replicas=0 >/dev/null
  wait_no_pods "$1"
  log "$1 stopped (scaled to 0)"
}

cluster_start_process() {
  local d; d="$(deployment_of "$1")"
  k -n "$NS" scale "deployment/$d" --replicas=1 >/dev/null
  k -n "$NS" rollout status "deployment/$d" --timeout=180s >/dev/null || { cluster_diagnose; die "$1 did not become ready"; }
  log "$1 started"
}

cluster_stop_processes() {
  local name
  k -n "$NS" scale deployment -l "app.kubernetes.io/instance=$RELEASE" --replicas=0 >/dev/null
  for name in "${PROCESSES[@]}"; do wait_no_pods "$name"; done
}

# cluster_kill_process NAME: a crash, as kill-process is on the host (SIGKILL at once, and the process
# stays down). PID 1 of a container ignores a SIGKILL sent from inside it, and a SIGKILL from the node
# alone would let the kubelet restart the container before the scale-down lands. So the process is
# frozen from its node (SIGSTOP; Kind's and k3d's nodes are Docker containers of the node's name),
# the Deployment is scaled to 0 (the SIGTERM that follows stays pending), and once the pod is
# terminating (its deletionTimestamp is set, so the kubelet restarts nothing) the process is killed
# from the node; nothing it does after the freeze reaches anyone. Any doubt stops the kill before the
# scale-down, which would otherwise be a graceful stop: a pod listing that fails, a running pod
# without a container ID, or a process ID that is not a running process (crictl reports 0 for a
# container that is not running). Only this cluster's nodes ($NODE_PREFIX-*) are touched (CLAUDE.md
# section 0).
cluster_kill_process() {
  local listing pod node phase id pid state name deleting frozen=() pods=() f i
  listing="$(k -n "$NS" get pods -l "$(selector_of "$1")" \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.nodeName}{" "}{.status.phase}{" "}{.status.containerStatuses[0].containerID}{"\n"}{end}')" \
    || die "cannot list the pods of $1; nothing was killed"
  while read -r pod node phase id; do
    [ -n "$pod" ] || continue
    if [ -z "$id" ]; then
      [ "$phase" != Running ] || die "pod $pod of $1 runs but shows no container ID; nothing was killed"
      continue
    fi
    [[ $node == "$NODE_PREFIX"-* ]] || die "$node is not a node of $CLUSTER"
    pid="$(docker exec "$node" crictl inspect --output go-template --template '{{.info.pid}}' "${id#containerd://}")"
    [[ $pid =~ ^[1-9][0-9]*$ ]] && [ "$pid" -gt 1 ] \
      || die "no process ID for the container of $1 on $node (is it running?)"
    docker exec "$node" kill -STOP "$pid"
    frozen+=("$node:$pid")
    pods+=("$pod")
  done <<< "$listing"
  k -n "$NS" scale "deployment/$(deployment_of "$1")" --replicas=0 >/dev/null
  for pod in "${pods[@]}"; do
    for i in $(seq 1 30); do
      state="$(k -n "$NS" get pod "$pod" --ignore-not-found \
        -o jsonpath='{.metadata.name}{" "}{.metadata.deletionTimestamp}')" \
        || die "cannot read pod $pod of $1; it stays frozen"
      read -r name deleting <<< "$state" || true
      [ -z "$name" ] || [ -n "$deleting" ] && continue 2
      sleep 1
    done
    die "pod $pod of $1 is not terminating 30 s after the scale-down; it stays frozen, and no SIGKILL was sent"
  done
  for f in "${frozen[@]}"; do docker exec "${f%%:*}" kill -KILL "${f##*:}" 2>/dev/null || true; done
  wait_no_pods "$1"
  log "$1 killed (frozen, scaled to 0, SIGKILL from its node once the pod was terminating)"
}

# k_apply: server-side apply of the objects on standard input, as the run script's field manager.
k_apply() { k -n "$NS" apply --server-side --field-manager=webblock-proto-run -f - >/dev/null; }

# standins_apply: ConfigMap provisioning-standins from the three host files.
standins_apply() {
  k -n "$NS" create configmap provisioning-standins --from-file=access.json="$PROV/access.json" \
    --from-file=entitlement.json="$PROV/entitlement.json" --from-file=repositories.json="$REPOSITORIES" \
    --dry-run=client -o json | k_apply
}

# standins_seen POD CONTAINER WANT: whether the pod reads the three files with these SHA-256 sums.
standins_seen() {
  local got
  got="$(k -n "$NS" exec "$1" -c "$2" -- /opt/venv/bin/python -c 'import hashlib, sys
print("".join(hashlib.sha256(open(p, "rb").read()).hexdigest() + " " for p in sys.argv[1:]), end="")' \
    /etc/webblock/standins/access.json /etc/webblock/standins/entitlement.json \
    /etc/webblock/standins/repositories.json 2>/dev/null)" || return 1
  [ "$got" = "$3" ]
}

# cmd_standins_sync: see the header. Each running pod gets 60 s; the kubelet's syncFrequency on Kind
# is 5 s (deploy/kind/cluster.yaml; decision 0016 section 9 ruling 3).
cmd_standins_sync() {
  cluster_mode || return 0
  standins_apply
  local want c pod i
  want="$(sha256sum "$PROV/access.json" "$PROV/entitlement.json" "$REPOSITORIES" | cut -d' ' -f1 | tr '\n' ' ')"
  local pods
  for c in api worker build-worker; do
    pods="$(k -n "$NS" get pods -l "app.kubernetes.io/instance=$RELEASE,app.kubernetes.io/component=$c" \
      --field-selector=status.phase=Running -o name)" || die "cannot list the $c pods; the stand-in files are not confirmed"
    for pod in $pods; do
      for i in $(seq 1 60); do
        standins_seen "$pod" "$c" "$want" && continue 2
        sleep 1
      done
      die "$pod does not read the new stand-in files after 60 s"
    done
  done
}

# push_image: builds the image (deploy/image/build.sh), pushes it to the stack's registry, and sets
# PROV_IMAGE to the reference the nodes pull, by digest (image.env keeps it, and the host's
# reference for make image-scan).
push_image() {
  local archive rev ref digest
  archive="$(bash "$kind_root/deploy/image/build.sh" | tail -n 1)"
  [ -f "$archive" ] || die "deploy/image/build.sh wrote no image archive"
  rev="$(basename "$archive" .tar)"; rev="${rev#provisioning-}"
  ref="$REGISTRY_LOCAL/webblock/platform/provisioning:$rev"
  crane_ push "$archive" "$ref" >/dev/null || die "pushing $ref failed"
  rm -f "$archive"
  digest="$(registry_digest "$ref")"
  [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || die "the registry serves no digest for $ref"
  PROV_IMAGE="$REGISTRY_IN_CLUSTER/webblock/platform/provisioning@$digest"
  printf 'PROV_IMAGE=%s\nPROV_IMAGE_ON_HOST=%s\n' "$PROV_IMAGE" "$REGISTRY_LOCAL/webblock/platform/provisioning@$digest" > "$PROV/image.env"
  log "image: $ref is $digest"
}

# write_stack_values: the stack's values beside the generated settings (values-stack.json). The pods
# run on the control-plane node: Kind labels it node-role.kubernetes.io/control-plane="", k3s "true"
# (proto-up taints the k3s server like Kind's control plane). Kind gives the API Service its LB-IPAM
# address by annotation; on k3d ServiceLB needs none (spike design G5).
write_stack_values() {
  local api_ip db mq jenkins catalog control_plane annotations
  db="$(container_ip "$PROV_PG_NAME")"; mq="$(container_ip "$PROV_MQ_NAME")"
  jenkins="$(container_ip "$PROTO_JENKINS_NAME")"; catalog="$(container_ip "$PROTO_CATALOG_NAME")"
  if [ "$ENGINE" = k3d ]; then
    control_plane=true
    annotations='{}'
  else
    api_ip="$(api_address)"
    control_plane=''
    annotations="{\"lbipam.cilium.io/ips\": \"$api_ip\"}"
  fi
  (umask 077 && cat > "$PROV/values-stack.json" <<EOF
  {
    "image": "$PROV_IMAGE",
    "standins": true,
    "nodeSelector": {"node-role.kubernetes.io/control-plane": "$control_plane"},
    "tolerations": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
    "api": {"service": {"type": "LoadBalancer", "annotations": $annotations}},
    "networkPolicy": {"database": "$db/32", "broker": "$mq/32", "jenkins": "$jenkins/32", "catalog": "$catalog/32"}
  }
EOF
  )
}

# start_follower and stop_follower: scripts/prototype/follow_logs.py for this run's log folder.
start_follower() {
  stop_follower
  require_local_cluster
  HOME="$TOOLHOME" setsid python3 -I "$kind_root/scripts/prototype/follow_logs.py" "$RUNS/$(readlink "$LOGS")" \
    "$KUBECTL" --kubeconfig "$KCFG" --context "$CONTEXT" --cache-dir "$KUBECACHE-follower" \
    >> "$PROV/log-follower.out" 2>&1 < /dev/null &
  echo $! > "$FOLLOWER_PID"
}
# stop_follower signals the follower's process group only when the pid file holds a process ID above 1
# (kill -- -1 would reach every process of the user) and that process is still the follower (a process
# ID can be reused once the follower ended).
stop_follower() {
  local p
  p="$(cat "$FOLLOWER_PID" 2>/dev/null)" || return 0
  rm -f "$FOLLOWER_PID"
  [[ $p =~ ^[1-9][0-9]*$ ]] && [ "$p" -gt 1 ] || return 0
  tr '\0' ' ' 2>/dev/null < "/proc/$p/cmdline" | grep -q 'follow_logs\.py' || return 0
  kill -TERM -- "-$p" 2>/dev/null || true
}

# cluster_diagnose: what failed, on standard error; pod logs hold no secret.
cluster_diagnose() {
  k -n "$NS" get pods -l "app.kubernetes.io/instance=$RELEASE" -o wide >&2 || true
  k -n "$NS" get events --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -n 15 >&2 || true
  k -n "$NS" logs job/provisioning-migrate --tail=20 >&2 2>/dev/null || true
}

# cluster_install: the image, the objects outside the chart, the values, a server-side dry run of
# everything the chart renders, then the follower, then the chart itself (the migration Job first; --wait
# until every Deployment is ready). The dry run sends each object to the API server in webblock,
# where it passes the schema validation of Kubernetes 1.36 and of the CRDs (CiliumNetworkPolicy) and the
# namespace's admission chain, PodSecurity restricted included; every warning is an error. It stores
# nothing and takes no field ownership, so --force-conflicts only lets a re-run after a code change pass
# over the fields Helm and kubectl scale own on the live objects. It does not run Helm's hooks or order.
cluster_install() {
  push_image
  python3 -I "$kind_root/scripts/prototype/cluster_values.py" values "$ENV_DIR" > "$PROV/values-env.json"
  chmod 0600 "$PROV/values-env.json"
  write_stack_values
  python3 -I "$kind_root/scripts/prototype/cluster_values.py" secrets "$ENV_DIR" | k_apply
  k -n "$NS" create secret generic provisioning-dev-tokens --from-file=tokens.json="$TOKENS" \
    --dry-run=client -o json | k_apply
  k -n "$NS" create configmap provisioning-gateway-ca --from-file=ca.crt="$PROTO/local-ca.crt" \
    --dry-run=client -o json | k_apply
  standins_apply
  local values=(--namespace "$NS" --values "$PROV/values-env.json" --values "$PROV/values-stack.json")
  h template "$RELEASE" "$CHART" "${values[@]}" \
    | k -n "$NS" apply --server-side --dry-run=server --force-conflicts --field-manager=webblock-proto-run \
      --warnings-as-errors -f - >/dev/null \
    || die "the API server refuses the chart's objects (server-side dry run in $NS)"
  start_follower
  h upgrade --install "$RELEASE" "$CHART" "${values[@]}" --wait --timeout 10m >/dev/null \
    || { cluster_diagnose; die "helm upgrade --install $RELEASE failed"; }
}

# cluster_stop: the release, what Helm leaves behind, the objects outside the chart, the follower.
# helm uninstall never deletes a hook (helm.sh/hook): the migration Job when a failed install left it,
# and the Job's CiliumNetworkPolicy provisioning-migrate, which is a pre-install hook so that the Job
# never runs unconfined (deploy/helm/provisioning/templates/networkpolicies.yaml). Without a cluster
# there is nothing to remove; a cluster whose kubeconfig fails the local check is never touched, and the
# stop fails, so that cmd_stop keeps the mode record (CLAUDE.md section 0).
cluster_stop() {
  stop_follower
  cluster_exists || return 0
  require_local_cluster
  if h status "$RELEASE" --namespace "$NS" >/dev/null 2>&1; then
    h uninstall "$RELEASE" --namespace "$NS" --wait --timeout 5m >/dev/null
  fi
  k -n "$NS" delete job provisioning-migrate --ignore-not-found >/dev/null
  # k3s has no CiliumNetworkPolicy type (spike design G5): a NetworkPolicy of that name instead.
  case "$ENGINE" in
    kind) k -n "$NS" delete ciliumnetworkpolicy provisioning-migrate --ignore-not-found >/dev/null ;;
    k3d) k -n "$NS" delete networkpolicy provisioning-migrate --ignore-not-found >/dev/null ;;
  esac
  k -n "$NS" delete secret "${CLUSTER_SECRETS[@]}" --ignore-not-found >/dev/null
  k -n "$NS" delete configmap "${CLUSTER_CONFIGMAPS[@]}" --ignore-not-found >/dev/null
}

# Decision 0022 E6: Provisioning only checks project.events and payment.events, which Project and
# Payment own. The simulator declares them as their owners would, before the processes start, and
# again once they run, so that Payment's usage queue is bound to provisioning.events.
declare_producers() {
  local port url
  port="$(container_port "$PROV_MQ_NAME" 5672)"
  url="amqp://provisioning:$(get_var "$PROV_ENV" MQ_PASSWORD)@127.0.0.1:$port/"
  WEBBLOCK_SIMULATOR_AMQP_URL="$url" "$PY" -m provisioning.hosts.devtools.simulate declare-producers \
    >>"$LOGS/simulator.log" 2>&1 || die "declaring the producers' exchanges failed; see $LOGS/simulator.log"
}

cmd_start() {
  local t0; t0="$(date +%s)"
  refuse_stale_token_file
  [ -x "$PY" ] || die "$PY is missing; run make py-sync"
  [ -x "$kind_root/bin/goose" ] || die "bin/goose is missing; run make py-tools"
  cluster_exists || die "no prototype cluster; run make proto-up first"
  local replicas="${PROV_REPLICAS:-1}"
  local mode
  mode="$(start_mode)"
  case "$replicas" in 1 | 2) ;; *) die "PROV_REPLICAS must be 1 or 2" ;; esac
  local name
  if [ "$replicas" = 1 ]; then
    for name in "${REPLICATED[@]}" bootstrap; do
      ! running "$name-2" || die "$name-2 is running; run $0 stop-processes first"
    done
  fi
  if [ "$mode" = cluster ]; then
    require_local_cluster
    [ -x "$HELM" ] || die "bin/helm is missing; run make kind-tools"
  else
    require_local_kubeconfig "$KCFG_WORKER"
    require_local_kubeconfig "$KCFG_BOOTSTRAP"
  fi
  local stamp; stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$PROV" "$ENV_DIR" "$RUNS/$stamp" "$PIDS"; chmod 0700 "$PROV" "$ENV_DIR"
  ln -sfn "$stamp" "$LOGS"
  # Task PK: the mode of this run, before its first step (a host start always records host), and no
  # endpoints.env until this run wrote its own.
  printf '%s\n' "$mode" > "$MODE_FILE"
  rm -f "$ENDPOINTS"
  printf '%s\n' "$replicas" > "$REPLICAS_FILE"
  local t_last="$t0"
  phase() { local now; now="$(date +%s)"; echo "$1 $(( now - t_last )) s" >> "$LOGS/timings.txt"; t_last="$now"; }
  ensure_secrets
  start_postgres; phase postgres
  start_rabbitmq; declare_producers; phase rabbitmq
  setup_database "$mode"; phase database
  write_files
  configure "$mode"
  [ "$replicas" = 1 ] || configure_replicas
  phase configuration
  if [ "$mode" = cluster ]; then
    cluster_install; phase cluster_install
  else
    while IFS= read -r name; do start_process "$name"; done < <(process_names)
    wait_ready; phase processes_ready
  fi
  declare_producers
  write_endpoints "$mode"
  echo "total $(( $(date +%s) - t0 )) s" >> "$LOGS/timings.txt"
  log "provisioning running ($mode mode) in $(( $(date +%s) - t0 )) s: API $(sed -n 's/^API_URL=//p' "$ENDPOINTS"), logs in $LOGS"
  [ "$replicas" = 1 ] || log "second replicas: API http://127.0.0.1:$PROV_API2_PORT, worker-2, build_worker-2 (one bootstrap; start-process bootstrap-2 adds a standby)"
}

# Every replica of every process, whatever count was recorded.
stop_processes() {
  local name
  for name in "${ALL_NAMES[@]}"; do stop_process "$name"; done
}

cmd_stop() {
  if cluster_mode; then cluster_stop; else stop_processes; fi
  docker rm -f "$PROV_PG_NAME" "$PROV_MQ_NAME" >/dev/null 2>&1 || true
  rm -rf "$ENV_DIR" "$PIDS"
  rm -f "$PROV_ENV" "$TOKENS" "$REPOSITORIES"
  rm -f "$PROV/access.json" "$PROV/entitlement.json" "$REPLICAS_FILE"
  rm -f "$MODE_FILE" "$ENDPOINTS" "$PROV/image.env" "$PROV/values-env.json" "$PROV/values-stack.json"
  log "provisioning stopped; containers removed; logs kept in $LOGS"
}

cmd_restart() {
  local name
  if cluster_mode; then
    cluster_stop_processes
    for name in "${PROCESSES[@]}"; do cluster_start_process "$name"; done
  else
    stop_processes
    while IFS= read -r name; do start_process "$name"; done < <(process_names)
    wait_ready
  fi
  log "provisioning restarted"
}

cmd_status() {
  local name
  if cluster_mode; then
    k -n "$NS" get deployments,pods -l "app.kubernetes.io/instance=$RELEASE" -o wide
  else
    while IFS= read -r name; do
      if running "$name"; then echo "$name running (pid $(pid_of "$name"))"; else echo "$name stopped"; fi
    done < <(process_names)
  fi
  docker ps --filter "name=^($PROV_PG_NAME|$PROV_MQ_NAME)\$" --format '{{.Names}} {{.Status}}'
}

cmd_start_process() { valid_process "$1"; if cluster_mode; then cluster_start_process "$1"; else start_process "$1"; fi; }
cmd_stop_process() { valid_process "$1"; if cluster_mode; then cluster_stop_process "$1"; else stop_process "$1"; fi; }
cmd_kill_process() { valid_process "$1"; if cluster_mode; then cluster_kill_process "$1"; else kill_process "$1"; fi; }
cmd_stop_processes() { if cluster_mode; then cluster_stop_processes; else stop_processes; fi; }

case "${1-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  restart) cmd_restart ;;
  start-process) [ $# -eq 2 ] || usage; cmd_start_process "$2" ;;
  stop-process) [ $# -eq 2 ] || usage; cmd_stop_process "$2" ;;
  stop-processes) [ $# -eq 1 ] || usage; cmd_stop_processes ;;
  kill-process) [ $# -eq 2 ] || usage; cmd_kill_process "$2" ;;
  standins-sync) [ $# -eq 1 ] || usage; cmd_standins_sync ;;
  operator) shift; run_operator operator "$@" ;;
  operator-as-worker) shift; run_operator operator_as_worker "$@" ;;
  operator-dlq) shift; run_operator operator_dlq dlq "$@" ;;
  *) usage ;;
esac
