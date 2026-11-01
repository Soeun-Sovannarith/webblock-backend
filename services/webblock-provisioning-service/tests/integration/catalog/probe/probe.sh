#!/bin/sh
# Every 2 s: ask the catalog, as the role in PGUSER (from the Secret catalog-runtime), who it is, which
# website the catalog maps it to, and how many products it can see; keep the answer in /tmp (the only
# writable path under the restricted pod). Serve it on 8080, one connection at a time.
set -u
mkdir -p /tmp/probe
printf 'starting\n' > /tmp/probe/answer
query="SELECT current_user || ' ' || coalesce(catalog.current_website_id()::text, 'none') || ' ' || (SELECT count(*) FROM catalog.products)"
while :; do
  if out="$(psql -X -q -t -A -c "$query" 2>/dev/null)"; then
    printf '%s\n' "$out" > /tmp/probe/answer.next
  else
    printf 'error\n' > /tmp/probe/answer.next
  fi
  mv /tmp/probe/answer.next /tmp/probe/answer
  sleep 2
done &
while :; do
  nc -l -p 8080 -e /probe/respond.sh
done
