#!/usr/bin/env bash
# docker-check.sh [IMAGE]: builds the shop image (unless IMAGE is given) and checks its runtime
# contract with Docker alone, no cluster and no database:
#   1. it runs as UID 65532 with a read-only root file system and only /tmp writable;
#   2. GET /healthz answers 200;
#   3. with the catalog unreachable, /readyz answers 503 while /healthz stays 200; / and a product page show the friendly error page with status 503, an invalid
#      product ID shows the 404 page, the pictures are served, and the server keeps running;
#   4. the database password never appears in the logs;
#   5. with no catalog settings at all, the log names the missing keys, the error page is shown, and
#      /readyz answers 503.
# Exits non-zero on the first failed check. Removes its containers on exit.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${1-}"
fake_password="never-in-logs-$RANDOM$RANDOM"
containers=()

cleanup() { for c in "${containers[@]}"; do docker rm -f "$c" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok: $*"; }

if [ -z "$image" ]; then
  image="webblock-clothing-shop:docker-check"
  docker build --platform linux/amd64 -t "$image" "$here" >/dev/null
  ok "built $image"
fi

# start NAME [ENV...]: runs the image locked down, publishes 8080 on a free loopback port, and waits
# for /healthz; sets url and secs (time to ready).
start() {
  local name="$1"; shift
  local args=()
  for kv in "$@"; do args+=(-e "$kv"); done
  docker run -d --name "$name" --read-only --tmpfs /tmp:rw,size=64m,mode=1777 --user 65532:65532 \
    --cap-drop ALL --security-opt no-new-privileges --memory 256m --cpus 0.5 \
    -p 127.0.0.1::8080 "${args[@]}" "$image" >/dev/null
  containers+=("$name")
  local port
  port="$(docker port "$name" 8080/tcp | head -n 1 | sed 's/.*://')"
  url="http://127.0.0.1:$port"
  local t0=$SECONDS
  until curl -fsS -o /dev/null "$url/healthz" 2>/dev/null; do
    [ $((SECONDS - t0)) -lt 30 ] || { docker logs "$name" >&2; fail "$name: /healthz not 200 within 30 s"; }
    [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] || { docker logs "$name" >&2; fail "$name exited"; }
    sleep 0.5
  done
  secs=$((SECONDS - t0))
}

# status URL: prints the HTTP status code of GET URL (bounded to 20 s).
status() { curl -s -o /dev/null -m 20 -w '%{http_code}' "$1"; }
body() { curl -s -m 20 "$1"; }

# 1-4: catalog configured but unreachable (nothing listens on port 9 inside the container).
name="clothing-shop-check-$$"
start "$name" PGHOST=127.0.0.1 PGPORT=9 PGDATABASE=catalog \
  PGUSER=site_00000000000000000000000000000000 "PGPASSWORD=$fake_password" PGSSLMODE=disable
ok "ready in ${secs} s at $url"

[ "$(docker inspect -f '{{.Config.User}}' "$name")" = "65532:65532" ] || fail "not running as 65532"
[ "$(docker exec "$name" id -u)" = 65532 ] || fail "process UID is not 65532"
docker exec "$name" sh -c 'touch /app/x 2>/dev/null' && fail "root file system is writable"
docker exec "$name" sh -c 'touch /tmp/x' || fail "/tmp is not writable"
ok "UID 65532, read-only root, writable /tmp"

[ "$(status "$url/healthz")" = 200 ] || fail "/healthz is not 200"
[ "$(body "$url/healthz")" = ok ] || fail "/healthz body is not ok"
ok "/healthz 200"

[ "$(status "$url/readyz")" = 503 ] || fail "/readyz is not 503 with the catalog unreachable"
[ "$(body "$url/readyz")" = "not ready" ] || fail "/readyz body is not 'not ready'"
[ "$(status "$url/healthz")" = 200 ] || fail "/healthz is not 200 with the catalog unreachable"
ok "catalog unreachable: /readyz 503, /healthz still 200"

code="$(status "$url/")"
page="$(body "$url/")"
grep -q "be right back" <<<"$page" || fail "/ does not show the friendly error page (status $code)"
[ "$code" = 503 ] || fail "/ shows the error page with status $code, not 503"
[ -n "$(curl -s -o /dev/null -m 20 -D - "$url/" | grep -i '^retry-after:')" ] || fail "/ has no Retry-After on the outage page"
ok "/ shows the friendly error page with status 503"

code="$(status "$url/products/0b6f7a52-3c1d-4e8f-9a2b-5c6d7e8f9a0b")"
grep -q "be right back" <<<"$(body "$url/products/0b6f7a52-3c1d-4e8f-9a2b-5c6d7e8f9a0b")" \
  || fail "a product page does not show the friendly error page (status $code)"
[ "$code" = 503 ] || fail "a product page shows the error page with status $code, not 503"
ok "a product page shows the friendly error page with status 503"

[ "$(status "$url/products/not-a-product")" = 404 ] || fail "an invalid product ID is not 404"
ok "an invalid product ID is 404 without a query"

[ "$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$url/products/crew-tee-white.svg")" = "200 image/svg+xml" ] \
  || fail "the product picture is not served"
ok "pictures are served"

[ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$name")" = "true 0" ] || fail "the server stopped"
logs="$(docker logs "$name" 2>&1)"
grep -qF "$fake_password" <<<"$logs" && fail "the password appears in the logs"
grep -q "catalog: .* failed" <<<"$logs" || fail "the outage is not logged"
grep -q "readyz: not ready (Error ECONNREFUSED)" <<<"$logs" || fail "the readiness failure is not logged"
ok "still running; the outage is logged without the password"
echo "--- log lines ---"; grep -E "catalog:|readyz:" <<<"$logs" | sort | uniq -c

# 5: no catalog settings at all.
name="clothing-shop-check-noenv-$$"
start "$name"
grep -q "be right back" <<<"$(body "$url/")" || fail "no settings: / does not show the friendly error page"
[ "$(status "$url/")" = 503 ] || fail "no settings: / is not 503"
[ "$(status "$url/readyz")" = 503 ] || fail "no settings: /readyz is not 503"
logs="$(docker logs "$name" 2>&1)"
grep -q "PGHOST is not set; PGPORT is not set; PGDATABASE is not set; PGUSER is not set; PGPASSWORD is not set; PGSSLMODE is not set" <<<"$logs" \
  || { echo "$logs" >&2; fail "no settings: the missing keys are not logged"; }
ok "no settings: error page shown, missing keys named, /readyz 503"

echo "all docker checks passed"
