#!/usr/bin/env bash
# Loki and the log shipper of the prototype stack (decision 0014 section 23 item 5; task W6-K), started by
# proto-up.sh after the Docker network kind exists. Idempotent: running containers made from the pinned
# images are kept.
#   1. webblock-loki (grafana/loki by digest; deploy/prototype/loki/loki.yaml), single binary with
#      filesystem storage in bin/kind-state/proto/loki-data, on the network kind as webblock-loki:3100
#      and published to the host on 127.0.0.1:$PROTO_LOKI_PORT only (PROTO_LOKI_URL in stack.env)
#   2. webblock-log-shipper (fluent/fluent-bit by digest; deploy/prototype/loki/fluent-bit.yaml) on the
#      network kind, with the Provisioning processes' log folder bin/kind-state/proto/provisioning/logs
#      mounted read-only at /logs (created here when missing; scripts/prototype/run-provisioning.sh
#      writes one folder per start into it) and its read positions in bin/kind-state/proto/log-shipper
# Both run as the invoking user (the folders are this user's, mode 0700), with no capability, a
# read-only root file system and a memory limit. Neither publishes anything but Loki's port on 127.0.0.1.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

docker network inspect kind >/dev/null 2>&1 || die "the Docker network kind does not exist; run make proto-up"

LOKI_DATA="$PROTO/loki-data"
SHIPPER_STATE="$PROTO/log-shipper"
PROV_LOGS="$PROTO/provisioning/logs"
LOKI_URL="http://127.0.0.1:$PROTO_LOKI_PORT"

# keep_or_remove NAME IMAGE: true when NAME runs IMAGE (started if stopped); removes it when it runs
# another image (a pin changed); false when it does not exist then.
keep_or_remove() {
  docker container inspect "$1" >/dev/null 2>&1 || return 1
  if [ "$(docker container inspect --format '{{.Config.Image}}' "$1")" != "$2" ]; then
    log "loki: $1 runs another image than the pin; replacing it"
    docker rm -f -v "$1" >/dev/null
    return 1
  fi
  [ "$(docker container inspect --format '{{.State.Running}}' "$1")" = true ] || docker start "$1" >/dev/null
}

# --- 1. Loki ------------------------------------------------------------------------------------------------
mkdir -p "$LOKI_DATA" "$SHIPPER_STATE" "$PROV_LOGS"
chmod 0700 "$LOKI_DATA" "$SHIPPER_STATE" "$PROTO/provisioning"
if keep_or_remove "$PROTO_LOKI_NAME" "$PROTO_LOKI_IMAGE"; then
  log "loki: reusing $PROTO_LOKI_NAME"
else
  if docker ps --format '{{.Ports}}' | grep -qF "127.0.0.1:$PROTO_LOKI_PORT->"; then
    die "port 127.0.0.1:$PROTO_LOKI_PORT is taken by another container"
  fi
  log "loki: starting $PROTO_LOKI_NAME on $LOKI_URL"
  docker run -d --restart=no --name "$PROTO_LOKI_NAME" --network kind -p "127.0.0.1:$PROTO_LOKI_PORT:3100" \
    --user "$(id -u):$(id -g)" --memory "$PROTO_LOKI_MEMORY" --cap-drop ALL --security-opt no-new-privileges \
    --read-only --tmpfs /tmp:size=16m \
    -v "$proto_dir/loki/loki.yaml:/etc/loki/webblock.yaml:ro" -v "$LOKI_DATA:/loki" \
    --label io.webblock.proto=loki "$PROTO_LOKI_IMAGE" -config.file=/etc/loki/webblock.yaml >/dev/null
fi
# /ready answers 200 once the ingester has joined its (in-memory) ring, about 15 s after a cold start.
poll 120 "Loki ready on $LOKI_URL/ready" curl -sf --max-time 3 -o /dev/null "$LOKI_URL/ready"

# --- 2. the log shipper --------------------------------------------------------------------------------------
if keep_or_remove "$PROTO_LOG_SHIPPER_NAME" "$PROTO_LOG_SHIPPER_IMAGE"; then
  log "loki: reusing $PROTO_LOG_SHIPPER_NAME"
else
  log "loki: starting $PROTO_LOG_SHIPPER_NAME, tailing $PROV_LOGS"
  docker run -d --restart=no --name "$PROTO_LOG_SHIPPER_NAME" --network kind \
    --user "$(id -u):$(id -g)" --memory "$PROTO_LOG_SHIPPER_MEMORY" --cap-drop ALL --security-opt no-new-privileges \
    --read-only \
    -v "$proto_dir/loki/fluent-bit.yaml:/config/fluent-bit.yaml:ro" -v "$PROV_LOGS:/logs:ro" \
    -v "$SHIPPER_STATE:/state" \
    --label io.webblock.proto=log-shipper "$PROTO_LOG_SHIPPER_IMAGE" \
    /fluent-bit/bin/fluent-bit -c /config/fluent-bit.yaml >/dev/null
fi

# --- 3. proof: a line written into the log folder is found in Loki ---------------------------------------------
# One JSON line under a run folder of its own (proto-up-probe/stack.log), with a fresh marker, must come back
# from Loki's query API by its labels and a LogQL JSON filter within 60 s.
marker="probe-$(python3 -I -c 'import secrets; print(secrets.token_hex(8))')"
mkdir -p "$PROV_LOGS/proto-up-probe"
printf '{"event":"stack.log_probe","marker":"%s","audit":false,"level":"info"}\n' "$marker" \
  >>"$PROV_LOGS/proto-up-probe/stack.log"
probe_found() {
  PROTO_LOKI_URL="$LOKI_URL" bash "$kind_dir/proto-loki-query.sh" --since 10m \
    "{job=\"webblock-provisioning\", run=\"proto-up-probe\", process=\"stack\"} | json | marker=\"$marker\"" \
    | grep -qF "$marker"
}
poll 60 "the probe line shipped to Loki by $PROTO_LOG_SHIPPER_NAME" probe_found
log "loki: $PROTO_LOKI_NAME ($LOKI_URL) and $PROTO_LOG_SHIPPER_NAME (tails $PROV_LOGS) running; a probe line arrived"
