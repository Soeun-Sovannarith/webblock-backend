#!/usr/bin/env bash
# Queries the prototype stack's Loki (decision 0014 section 23 item 5; task W6-K) and prints one log line
# per output line, oldest first: the JSON object a Provisioning process wrote. Exit 0 with no output when
# nothing matches; non-zero when Loki does not answer or rejects the query.
#
#   bash deploy/kind/proto-loki-query.sh [--since DURATION] [--limit N] 'LOGQL'
#   make proto-logs Q='{job="webblock-provisioning"} | json | audit="true"' [SINCE=1h] [LIMIT=100]
#
# Streams: {job="webblock-provisioning", process="api|worker|build_worker|bootstrap", run="<UTC timestamp>"}
# (deploy/prototype/loki/fluent-bit.yaml). Examples:
#   '{job="webblock-provisioning"} | json | audit="true"'                         every audit event
#   '{job="webblock-provisioning", process="worker"} | json | audit="true" | project_id="<id>"'
# The address is PROTO_LOKI_URL from stack.env (http://127.0.0.1:<port>). Reads nothing else; no cluster
# call, no credential.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stack_env="$here/../../bin/kind-state/proto/stack.env"

since=1h
limit=1000
while [ $# -gt 1 ]; do
  case "$1" in
    --since) since="$2"; shift 2 ;;
    --limit) limit="$2"; shift 2 ;;
    *) echo "usage: $0 [--since DURATION] [--limit N] 'LOGQL'" >&2; exit 2 ;;
  esac
done
[ $# -eq 1 ] && [ -n "$1" ] || { echo "usage: $0 [--since DURATION] [--limit N] 'LOGQL'" >&2; exit 2; }
query="$1"
[[ $since =~ ^[0-9]+[smhd]$ ]] || { echo "--since must be like 30s, 10m, 1h or 2d" >&2; exit 2; }
[[ $limit =~ ^[0-9]{1,5}$ ]] || { echo "--limit must be a number up to 99999" >&2; exit 2; }

url="${PROTO_LOKI_URL:-}"
if [ -z "$url" ]; then
  [ -f "$stack_env" ] || { echo "$stack_env is missing: run make proto-up" >&2; exit 1; }
  url="$(sed -n 's/^PROTO_LOKI_URL=//p' "$stack_env")"
fi
[[ $url =~ ^http://127\.0\.0\.1:[0-9]{1,5}$ ]] || { echo "PROTO_LOKI_URL is not http://127.0.0.1:<port>" >&2; exit 1; }

case "$since" in
  *s) seconds="${since%s}" ;;
  *m) seconds=$(( ${since%m} * 60 )) ;;
  *h) seconds=$(( ${since%h} * 3600 )) ;;
  *d) seconds=$(( ${since%d} * 86400 )) ;;
esac
start_ns="$(( ($(date +%s) - seconds) * 1000000000 ))"

answer="$(curl -sS --fail-with-body --max-time 15 -G "$url/loki/api/v1/query_range" \
  --data-urlencode "query=$query" --data-urlencode "start=$start_ns" \
  --data-urlencode "limit=$limit" --data-urlencode "direction=forward")" \
  || { echo "Loki refused or did not answer: $(printf '%s' "$answer" | cut -c1-300)" >&2; exit 1; }
printf '%s' "$answer" | python3 -I -c '
import json, sys
body = json.load(sys.stdin)
if body.get("status") != "success":
    sys.exit("Loki answered status " + str(body.get("status")))
entries = [(ts, line) for stream in body["data"]["result"] for ts, line in stream["values"]]
for _, line in sorted(entries):
    print(line)
'
