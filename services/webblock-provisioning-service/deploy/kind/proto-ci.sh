#!/usr/bin/env bash
# Starts the prototype's build side on the kind Docker network (run by proto-up after the cluster
# exists, because the cluster creates that network); each step reuses what is already running:
#   webblock-git       unprivileged nginx serving bin/kind-state/proto/git read-only (Git dumb HTTP)
#   webblock-buildkit  BuildKit daemon, mutual TLS on webblock-buildkit:1234, trusting the registry CA
#   webblock-jenkins   Jenkins LTS with pinned plugins, configuration as code and job webblock-site-build,
#                      published on http://127.0.0.1:8090 only; no Docker socket
# Then it logs in once as the Jenkins user "provisioning", creates an API token and writes
# bin/kind-state/proto/jenkins.env (mode 0600). Passwords and the token are never printed.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

command -v docker >/dev/null 2>&1 || die "docker is required"
command -v openssl >/dev/null 2>&1 || die "openssl is required"
docker network inspect kind >/dev/null 2>&1 || die "the Docker network kind does not exist: run make proto-up"

# Jenkins runs as UID 1000 in its container. When the host user is not 1000, the mounted secret and
# client-certificate folders must be readable by others (they stay inside the 0700 bin/kind-state).
jenkins_readable() {
  if [ "$(id -u)" != 1000 ]; then chmod 0755 "$1"; find "$1" -type f -exec chmod 0644 {} +; fi
}

# container_running NAME: true when the container exists and runs.
container_running() { [ "$(docker container inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

# --- Git server ---------------------------------------------------------------------------------------------
mkdir -p "$GIT_ROOT"
if ! container_running "$PROTO_GIT_NAME"; then
  docker rm -f -v "$PROTO_GIT_NAME" >/dev/null 2>&1 || true
  docker pull -q "$PROTO_NGINX_IMAGE" >/dev/null || die "cannot pull $PROTO_NGINX_IMAGE"
  docker run -d --restart=no --name "$PROTO_GIT_NAME" --network kind --user "$(id -u):$(id -g)" \
    --memory 64m --read-only --tmpfs /tmp:rw,size=16m \
    -v "$GIT_ROOT:/srv/git:ro" -v "$proto_dir/git/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -e NGINX_ENTRYPOINT_QUIET_LOGS=1 --label io.webblock.proto=git "$PROTO_NGINX_IMAGE" >/dev/null
fi
log "git: $PROTO_GIT_NAME serves $GIT_ROOT as http://$PROTO_GIT_NAME:8080/<name>.git on the kind network"

# --- BuildKit mutual TLS -------------------------------------------------------------------------------------
# A project-local CA (key deleted after signing), a server certificate for webblock-buildkit and a
# client certificate for Jenkins, valid 30 days; regenerated when missing or within a day of expiry.
ensure_buildkit_tls() {
  local d="$BUILDKIT_TLS" tmp
  if [ -f "$d/server/cert.pem" ] && [ -f "$d/client/client.crt" ] \
    && openssl x509 -checkend 86400 -noout -in "$d/server/cert.pem" >/dev/null 2>&1 \
    && openssl x509 -checkend 86400 -noout -in "$d/client/client.crt" >/dev/null 2>&1 \
    && openssl verify -CAfile "$d/server/ca.crt" "$d/client/client.crt" >/dev/null 2>&1; then
    return 0
  fi
  docker rm -f -v "$PROTO_BUILDKIT_NAME" "$PROTO_JENKINS_NAME" >/dev/null 2>&1 || true
  rm -rf "$d"
  mkdir -p "$d/server" "$d/client"
  chmod 0700 "$d" "$d/server" "$d/client"
  tmp="$(mktemp -d "$PROTO/.bktls.XXXXXX")"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 \
    -subj "/CN=webblock-buildkit local CA (test only)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$tmp/ca.key" -out "$tmp/ca.crt" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$PROTO_BUILDKIT_NAME" \
    -keyout "$d/server/key.pem" -out "$tmp/server.csr" 2>/dev/null
  printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n' \
    "$PROTO_BUILDKIT_NAME" >"$tmp/server.cnf"
  openssl x509 -req -in "$tmp/server.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" -CAcreateserial -CAserial "$tmp/ca.srl" \
    -days 30 -extfile "$tmp/server.cnf" -out "$d/server/cert.pem" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$PROTO_JENKINS_NAME" \
    -keyout "$d/client/client.key" -out "$tmp/client.csr" 2>/dev/null
  printf 'extendedKeyUsage=clientAuth\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n' >"$tmp/client.cnf"
  openssl x509 -req -in "$tmp/client.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" -CAserial "$tmp/ca.srl" \
    -days 30 -extfile "$tmp/client.cnf" -out "$d/client/client.crt" 2>/dev/null
  cp "$tmp/ca.crt" "$d/server/ca.crt"
  cp "$tmp/ca.crt" "$d/client/ca.crt"
  rm -rf "$tmp"
  chmod 0600 "$d/server/key.pem" "$d/client/client.key"
  openssl verify -CAfile "$d/server/ca.crt" "$d/server/cert.pem" "$d/client/client.crt" >/dev/null \
    || die "the BuildKit certificates do not verify"
  log "buildkit: created a local CA, server and client certificates in $d (30 days; CA key deleted)"
}
ensure_buildkit_tls
jenkins_readable "$BUILDKIT_TLS/client"

# --- BuildKit daemon -----------------------------------------------------------------------------------------
# Rootful BuildKit needs --privileged to create its build sandboxes. It runs only the trusted pipeline's
# builds; nothing mounts the host's Docker socket.
sed "s#@REGISTRY@#$REGISTRY_IN_CLUSTER#g" "$proto_dir/buildkit/buildkitd.toml.tmpl" >"$PROTO/buildkitd.toml"
if ! container_running "$PROTO_BUILDKIT_NAME"; then
  docker rm -f -v "$PROTO_BUILDKIT_NAME" >/dev/null 2>&1 || true
  docker pull -q "$PROTO_BUILDKIT_IMAGE" >/dev/null || die "cannot pull $PROTO_BUILDKIT_IMAGE"
  docker run -d --restart=no --name "$PROTO_BUILDKIT_NAME" --network kind --privileged --memory 2g \
    -v "$BUILDKIT_TLS/server:/certs:ro" -v "$PROTO/buildkitd.toml:/etc/buildkit/buildkitd.toml:ro" \
    -v "$REGISTRY_TLS/ca.crt:/etc/buildkit/registry-ca.crt:ro" \
    --label io.webblock.proto=buildkit "$PROTO_BUILDKIT_IMAGE" \
    --addr tcp://0.0.0.0:1234 --tlscacert /certs/ca.crt --tlscert /certs/cert.pem --tlskey /certs/key.pem >/dev/null
fi
log "buildkit: $PROTO_BUILDKIT_NAME started ($PROTO_BUILDKIT_IMAGE)"

# --- Jenkins image --------------------------------------------------------------------------------------------
ctx="$PROTO/jenkins-image"
rm -rf "$ctx"; mkdir -p "$ctx"
cp "$proto_dir/jenkins/Dockerfile" "$proto_dir/jenkins/plugins.txt" "$proto_dir/jenkins/casc.yaml" \
  "$proto_dir/jenkins/webblock-buildctl" "$ctx/"
chmod 0755 "$ctx/webblock-buildctl"
chmod 0644 "$ctx/Dockerfile" "$ctx/plugins.txt" "$ctx/casc.yaml"
python3 -I - "$proto_dir/jenkins/job-config.xml.tmpl" "$proto_dir/jenkins/Jenkinsfile" "$ctx/config.xml" <<'PY'
import sys
from xml.sax.saxutils import escape
template, script, out = sys.argv[1:4]
text = open(template, encoding="utf-8").read()
assert text.count("@PIPELINE_SCRIPT@") == 1
with open(out, "w", encoding="utf-8") as f:
    f.write(text.replace("@PIPELINE_SCRIPT@", escape(open(script, encoding="utf-8").read())))
PY
log "jenkins: building image $PROTO_JENKINS_IMAGE_TAG (plugins from updates.jenkins.io on the first run)"
docker build --quiet --tag "$PROTO_JENKINS_IMAGE_TAG" \
  --build-arg "JENKINS_BASE=$PROTO_JENKINS_BASE" --build-arg "BUILDKIT_IMAGE=$PROTO_BUILDKIT_IMAGE" \
  "$ctx" >"$PROTO/jenkins-image.log" 2>&1 || die "docker build of the Jenkins image failed (see $PROTO/jenkins-image.log)"
want_image="$(docker image inspect --format '{{.Id}}' "$PROTO_JENKINS_IMAGE_TAG")"

# --- Jenkins container ----------------------------------------------------------------------------------------
# A new container (new image, or none running) gets new passwords and, below, a new API token; its
# JENKINS_HOME is an anonymous volume that proto-down removes with it.
if container_running "$PROTO_JENKINS_NAME" \
  && [ "$(docker container inspect --format '{{.Image}}' "$PROTO_JENKINS_NAME")" = "$want_image" ] \
  && [ -f "$JENKINS_ENV" ]; then
  log "jenkins: reusing $PROTO_JENKINS_NAME"
else
  docker rm -f -v "$PROTO_JENKINS_NAME" >/dev/null 2>&1 || true
  rm -f "$JENKINS_ENV"
  rm -rf "$JENKINS_SECRETS"
  mkdir -p "$JENKINS_SECRETS"
  chmod 0700 "$JENKINS_SECRETS"
  for s in WEBBLOCK_ADMIN_PASSWORD WEBBLOCK_PROVISIONING_PASSWORD; do
    (umask 077; openssl rand -hex 24 | tr -d '\n' >"$JENKINS_SECRETS/$s")
    [ "$(wc -c <"$JENKINS_SECRETS/$s")" = 48 ] || die "could not generate $s"
  done
  jenkins_readable "$JENKINS_SECRETS"
  if docker ps --format '{{.Ports}}' | grep -qF "127.0.0.1:$PROTO_JENKINS_PORT->"; then
    die "port 127.0.0.1:$PROTO_JENKINS_PORT is taken by another container"
  fi
  docker run -d --restart=no --name "$PROTO_JENKINS_NAME" --network kind --memory 1536m \
    -p "127.0.0.1:$PROTO_JENKINS_PORT:8080" \
    -e "WEBBLOCK_JENKINS_ROOT_URL=$JENKINS_URL_HOST/" \
    -e "WEBBLOCK_REGISTRY=$REGISTRY_IN_CLUSTER" \
    -e "WEBBLOCK_BUILDKIT_ADDR=tcp://$PROTO_BUILDKIT_NAME:1234" \
    -e "WEBBLOCK_BUILDKIT_SERVER_NAME=$PROTO_BUILDKIT_NAME" \
    -v "$JENKINS_SECRETS:/run/secrets:ro" -v "$BUILDKIT_TLS/client:/etc/webblock/buildkit:ro" \
    --label io.webblock.proto=jenkins "$PROTO_JENKINS_IMAGE_TAG" >/dev/null
  log "jenkins: started $PROTO_JENKINS_NAME on $JENKINS_URL_HOST"
fi
jenkins_up() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$JENKINS_URL_HOST/login")" = 200 ]; }
poll 300 "Jenkins answering on $JENKINS_URL_HOST/login" jenkins_up

# --- API token for Provisioning --------------------------------------------------------------------------------
if [ ! -f "$JENKINS_ENV" ]; then
  pw="$(cat "$JENKINS_SECRETS/WEBBLOCK_PROVISIONING_PASSWORD")"
  jar="$(mktemp "$PROTO/.jenkins-jar.XXXXXX")"
  resp="$(mktemp "$PROTO/.jenkins-resp.XXXXXX")"
  chmod 0600 "$jar" "$resp"
  printf 'user = "provisioning:%s"\n' "$pw" \
    | curl -sS -f -K - --max-time 30 -c "$jar" -o "$resp" "$JENKINS_URL_HOST/crumbIssuer/api/json" \
    || { rm -f "$jar" "$resp"; die "jenkins: the provisioning user cannot read a crumb"; }
  crumb_field="$(json_get "$resp" crumbRequestField)"; crumb="$(json_get "$resp" crumb)"
  printf 'user = "provisioning:%s"\n' "$pw" \
    | curl -sS -f -K - --max-time 30 -b "$jar" -H "$crumb_field: $crumb" -o "$resp" \
      --data-urlencode "newTokenName=provisioning-$(date +%Y%m%dT%H%M%S)" \
      "$JENKINS_URL_HOST/me/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken" \
    || { rm -f "$jar" "$resp"; die "jenkins: creating the API token failed"; }
  token="$(json_get "$resp" data tokenValue)"
  rm -f "$jar" "$resp"
  unset pw
  printf '%s' "$token" | grep -Eq '^[0-9a-f]{32,}$' || die "jenkins: no API token in the response"
  (umask 077; printf 'JENKINS_URL=%s\nJENKINS_USER=provisioning\nJENKINS_API_TOKEN=%s\n' "$JENKINS_URL_HOST" "$token" >"$JENKINS_ENV.tmp")
  mv "$JENKINS_ENV.tmp" "$JENKINS_ENV"
  unset token
  log "jenkins: API token for user provisioning written to $JENKINS_ENV"
fi

# --- checks: the token works, the plugins are the pinned ones, the job exists, buildctl reaches BuildKit --
check="$(mktemp "$PROTO/.jenkins-check.XXXXXX")"
trap 'rm -f "$check"' EXIT
[ "$(jenkins_curl -o "$check" -w '%{http_code}' "$JENKINS_URL_HOST/me/api/json")" = 200 ] \
  || die "jenkins: the API token in $JENKINS_ENV is refused"
[ "$(json_get "$check" id)" = provisioning ] || die "jenkins: the API token does not belong to user provisioning"
jenkins_curl -f -o "$check" "$JENKINS_URL_HOST/pluginManager/api/json?tree=plugins%5BshortName,version%5D" \
  || die "jenkins: cannot list plugins"
installed="$(python3 -I -c 'import json,sys; print("\n".join(sorted(p["shortName"]+":"+p["version"] for p in json.load(open(sys.argv[1]))["plugins"])))' "$check")"
pinned="$(grep -Ev '^[[:space:]]*(#|$)' "$proto_dir/jenkins/plugins.txt" | sort)"
[ "$installed" = "$pinned" ] || die "jenkins: installed plugins differ from plugins.txt: $(diff <(printf '%s\n' "$pinned") <(printf '%s\n' "$installed") | grep '^[<>]' | tr '\n' ' ')"
jenkins_curl -f -o "$check" "$JENKINS_URL_HOST/job/$PROTO_JOB/api/json?tree=name,buildable,property%5BparameterDefinitions%5Bname%5D%5D" \
  || die "jenkins: job $PROTO_JOB does not exist"
params="$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(" ".join(p["name"] for pr in d["property"] for p in pr.get("parameterDefinitions") or []))' "$check")"
[ "$params" = "PROJECT_ID RELEASE_ID ATTEMPT REPOSITORY_URL COMMIT_SHA IMAGE_REPOSITORY" ] \
  || die "jenkins: job $PROTO_JOB has parameters '$params'"
executors="$(jenkins_curl -f "$JENKINS_URL_HOST/computer/(built-in)/api/json?tree=numExecutors" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)["numExecutors"])')"
[ "$executors" = 2 ] || die "jenkins: the built-in node has $executors executors, not 2"
bk_ready() { docker exec "$PROTO_JENKINS_NAME" webblock-buildctl debug workers >/dev/null 2>&1; }
poll 120 "buildctl in Jenkins reaching $PROTO_BUILDKIT_NAME over mutual TLS" bk_ready
if docker container inspect --format '{{range .Mounts}}{{.Source}} {{end}}' "$PROTO_JENKINS_NAME" | grep -q 'docker.sock'; then
  die "jenkins: a Docker socket is mounted"
fi
log "jenkins: token works, $(printf '%s\n' "$installed" | wc -l) pinned plugins, job $PROTO_JOB with 6 parameters, 2 executors, BuildKit reachable, no Docker socket"
