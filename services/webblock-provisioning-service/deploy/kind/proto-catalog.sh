#!/usr/bin/env bash
# The shared catalog database of the prototype stack (decision 0014 section 14; Notion "6 · Shared
# catalog"), started by proto-up.sh after the Docker network kind exists. Idempotent: a running
# container is kept and only the migrations are brought up to date.
#   1. container webblock-catalog-db (PostgreSQL 18 by digest) on the network kind, published on
#      127.0.0.1 at a port Docker picks; scram-sha-256 is the only authentication, local and TCP
#   2. on a new container: the credentials, generated once into bin/kind-state/proto/catalog-admin.env
#      (mode 0600, never printed); then, as the superuser, the Project service's
#      scripts/db/catalog-bootstrap-roles.sql (the NOLOGIN roles catalog_owner, catalog_platform and
#      catalog_site) and deploy/prototype/catalog/bootstrap.sql (the login roles, the database and
#      catalog_migrator's session role), with the login roles' SCRAM-SHA-256 verifiers computed here
#      (no cleartext password reaches SQL)
#   3. the Project service's catalog migrations (Flyway V1-V4 and the repeatable reference data,
#      decision 0017 section 1) with the Flyway CLI image pinned by digest, on the network kind, as
#      catalog_migrator with SET ROLE catalog_owner; Flyway's history is in schema catalog_flyway
# The Project service is read from PROJECT_SERVICE_DIR (default: ../project-service beside this
# repository); nothing in it is written. Passwords reach docker and Flyway through the environment,
# never through a command line.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

# The Project service's catalog material (decision 0017 section 1).
PROJECT_SERVICE_DIR="${PROJECT_SERVICE_DIR:-$kind_root/../project-service}"
[ -d "$PROJECT_SERVICE_DIR" ] \
  || die "catalog: the Project service is not at PROJECT_SERVICE_DIR=$PROJECT_SERVICE_DIR; set PROJECT_SERVICE_DIR to its checkout"
PROJECT_SERVICE_DIR="$(cd "$PROJECT_SERVICE_DIR" && pwd)"
case "$PROJECT_SERVICE_DIR" in
  *:*) die "catalog: PROJECT_SERVICE_DIR must not contain ':' (docker -v cannot mount it): $PROJECT_SERVICE_DIR" ;;
esac
catalog_migrations="$PROJECT_SERVICE_DIR/src/main/resources/db/migration/catalog"
catalog_roles_sql="$PROJECT_SERVICE_DIR/scripts/db/catalog-bootstrap-roles.sql"
[ -f "$catalog_migrations/V1__catalog_tables.sql" ] \
  || die "catalog: $catalog_migrations/V1__catalog_tables.sql is missing; PROJECT_SERVICE_DIR=$PROJECT_SERVICE_DIR has no catalog migrations"
[ -f "$catalog_roles_sql" ] \
  || die "catalog: $catalog_roles_sql is missing; PROJECT_SERVICE_DIR=$PROJECT_SERVICE_DIR has no catalog role script"

command -v python3 >/dev/null 2>&1 || die "python3 is required"
docker network inspect kind >/dev/null 2>&1 || die "the Docker network kind does not exist; run make proto-up"

catalog_env_get() { sed -n "s/^$1=//p" "$CATALOG_ENV"; }

# catalog_psql DATABASE: psql as the container's superuser over TCP inside the container, reading SQL
# from standard input.
catalog_psql() {
  PGPASSWORD="$(catalog_env_get CATALOG_SUPERUSER_PASSWORD)" \
    docker exec -i -e PGPASSWORD "$PROTO_CATALOG_NAME" \
    psql -X -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d "$1" "${@:2}"
}
catalog_answers() { catalog_psql postgres -tAc 'SELECT 1' </dev/null >/dev/null 2>&1; }

# --- 1. the container -----------------------------------------------------------------------------------
if docker container inspect "$PROTO_CATALOG_NAME" >/dev/null 2>&1; then
  [ "$(docker container inspect --format '{{.Config.Image}}' "$PROTO_CATALOG_NAME")" = "$PROTO_CATALOG_IMAGE" ] \
    || die "container $PROTO_CATALOG_NAME does not run the pinned $PROTO_CATALOG_IMAGE; run make proto-down"
  [ -f "$CATALOG_ENV" ] || die "container $PROTO_CATALOG_NAME exists without $CATALOG_ENV; run make proto-down"
  [ "$(docker container inspect --format '{{.State.Running}}' "$PROTO_CATALOG_NAME")" = true ] \
    || docker start "$PROTO_CATALOG_NAME" >/dev/null
  log "catalog: reusing $PROTO_CATALOG_NAME"
else
  log "catalog: generating credentials into $CATALOG_ENV"
  rm -f "$CATALOG_ENV"
  (umask 077 && python3 -I - "$CATALOG_ENV" <<'PY'
import secrets, sys
names = ("SUPERUSER", "MIGRATOR", "PLATFORM", "ACCESS_ADMIN")
users = {"SUPERUSER": "postgres", "MIGRATOR": "catalog_migrator",
         "PLATFORM": "project_catalog_platform", "ACCESS_ADMIN": "provisioning_catalog_admin"}
with open(sys.argv[1], "x") as f:
    f.write("# Catalog database credentials of the prototype stack (make proto-up). Never print this file.\n")
    for n in names:
        f.write(f"CATALOG_{n}_USER={users[n]}\n")
        f.write(f"CATALOG_{n}_PASSWORD={secrets.token_urlsafe(32)}\n")
PY
  )
  docker_env="$(mktemp "$PROTO/.catalog-docker-env.XXXXXX")"
  {
    printf 'POSTGRES_PASSWORD=%s\n' "$(catalog_env_get CATALOG_SUPERUSER_PASSWORD)"
    printf 'POSTGRES_HOST_AUTH_METHOD=scram-sha-256\n'
    printf 'POSTGRES_INITDB_ARGS=--auth-local=scram-sha-256 --auth-host=scram-sha-256\n'
  } >"$docker_env"
  log "catalog: starting $PROTO_CATALOG_NAME ($PROTO_CATALOG_IMAGE)"
  if ! docker run -d --restart=no --name "$PROTO_CATALOG_NAME" --network kind -p 127.0.0.1::5432 \
      --memory "$PROTO_CATALOG_MEMORY" --env-file "$docker_env" --label io.webblock.proto=catalog \
      "$PROTO_CATALOG_IMAGE" >/dev/null; then
    rm -f "$docker_env"
    die "catalog: docker run failed"
  fi
  rm -f "$docker_env"
fi
poll 120 "the catalog database answering on TCP" catalog_answers

# --- 2. roles and database (once per container) -----------------------------------------------------------
# catalog_migrator is the first role only bootstrap.sql creates; the Project service's role script is safe
# to run again.
if [ "$(catalog_psql postgres -tAc "SELECT count(*) FROM pg_roles WHERE rolname = 'catalog_migrator'" </dev/null)" != 1 ]; then
  log "catalog: creating the roles ($catalog_roles_sql, deploy/prototype/catalog/bootstrap.sql) and the database"
  {
    python3 -I - "$CATALOG_ENV" <<'PY'
import base64, hashlib, hmac, os, sys
values = dict(line.rstrip("\n").split("=", 1) for line in open(sys.argv[1]) if "=" in line and not line.startswith("#"))
def verifier(password: str) -> str:
    salt, iterations = os.urandom(16), 4096
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda b: base64.b64encode(b).decode()
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(hashlib.sha256(client_key).digest())}:{b64(server_key)}"
for var, key in (("migrator_verifier", "MIGRATOR"), ("platform_verifier", "PLATFORM"),
                 ("access_admin_verifier", "ACCESS_ADMIN")):
    print(f"\\set {var} '{verifier(values[f'CATALOG_{key}_PASSWORD'])}'")
PY
    cat "$catalog_roles_sql"
    cat "$proto_dir/catalog/bootstrap.sql"
  } | catalog_psql postgres
fi

# --- 3. migrations ------------------------------------------------------------------------------------------
# A catalog that Provisioning's former goose copies built (before decision 0017) cannot be taken over by
# Flyway: its tables exist without Flyway's history.
if [ "$(catalog_psql "$PROTO_CATALOG_DATABASE" -tAc "SELECT to_regclass('public.goose_db_version') IS NOT NULL" </dev/null)" = t ]; then
  die "catalog: $PROTO_CATALOG_NAME was migrated by goose before decision 0017; recreate the stack: make proto-down proto-up"
fi
docker image inspect "$PROTO_FLYWAY_IMAGE" >/dev/null 2>&1 \
  || docker pull -q "$PROTO_FLYWAY_IMAGE" >/dev/null || die "cannot pull $PROTO_FLYWAY_IMAGE"
flog="$PROTO/catalog-flyway.log"
log "catalog: applying $catalog_migrations with Flyway (log $flog)"
if ! FLYWAY_PASSWORD="$(catalog_env_get CATALOG_MIGRATOR_PASSWORD)" \
    docker run --rm --network kind --memory "$PROTO_FLYWAY_MEMORY" --label io.webblock.proto=catalog-flyway \
    -e FLYWAY_PASSWORD -e REDGATE_DISABLE_TELEMETRY=true \
    -v "$catalog_migrations:/flyway/sql:ro" "$PROTO_FLYWAY_IMAGE" \
    -url="jdbc:postgresql://$PROTO_CATALOG_NAME:5432/$PROTO_CATALOG_DATABASE" -user=catalog_migrator \
    -initSql="SET ROLE catalog_owner" -schemas=catalog_flyway -defaultSchema=catalog_flyway \
    -locations=filesystem:/flyway/sql -placeholderReplacement=false migrate >"$flog" 2>&1; then
  tail -n 20 "$flog" >&2
  die "catalog: flyway migrate failed (see $flog)"
fi
history="$(catalog_psql "$PROTO_CATALOG_DATABASE" -tAc "SELECT string_agg(coalesce(version, 'R') || ':' || success, ' ' ORDER BY installed_rank) FROM catalog_flyway.flyway_schema_history WHERE type <> 'SCHEMA'" </dev/null)"
case " $history " in
  *:false\ *) die "catalog: catalog_flyway.flyway_schema_history records a failed migration: $history" ;;
esac
CATALOG_PORT="$(catalog_port)"
[ -n "$CATALOG_PORT" ] || die "catalog: cannot read the published port of $PROTO_CATALOG_NAME"
log "catalog: $PROTO_CATALOG_NAME ready at 127.0.0.1:$CATALOG_PORT (in the cluster $PROTO_CATALOG_NAME:5432), Flyway history (version:success): ${history:-?}"
