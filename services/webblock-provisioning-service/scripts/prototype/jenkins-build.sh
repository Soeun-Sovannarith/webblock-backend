#!/usr/bin/env bash
# jenkins-build.sh PROJECT_ID RELEASE_ID ATTEMPT REPOSITORY_URL COMMIT_SHA IMAGE_REPOSITORY
# Triggers job webblock-site-build on the prototype's Jenkins over REST with the API token from
# bin/kind-state/proto/jenkins.env, waits for the build to finish (JENKINS_WAIT seconds, default 900),
# and prints the archived result.json on standard output. Progress goes to standard error. Exits 0
# when the build finished and archived result.json, whatever its outcome; non-zero when Jenkins could
# not be reached, refused the trigger, or the build left no result.json. The token is never printed.
# This is what Provisioning's Jenkins adapter does, minus persistence: test and demo use only.
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

[ "$#" = 6 ] || { echo "usage: $0 PROJECT_ID RELEASE_ID ATTEMPT REPOSITORY_URL COMMIT_SHA IMAGE_REPOSITORY" >&2; exit 2; }
wait_s="${JENKINS_WAIT:-900}"
say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

work="$(mktemp -d "$PROTO/.jenkins-build.XXXXXX")"
trap 'rm -rf "$work"' EXIT

code="$(jenkins_curl -o "$work/body" -D "$work/headers" -w '%{http_code}' -X POST \
  --data-urlencode "PROJECT_ID=$1" --data-urlencode "RELEASE_ID=$2" --data-urlencode "ATTEMPT=$3" \
  --data-urlencode "REPOSITORY_URL=$4" --data-urlencode "COMMIT_SHA=$5" --data-urlencode "IMAGE_REPOSITORY=$6" \
  "$JENKINS_URL_HOST/job/$PROTO_JOB/buildWithParameters")" || die "jenkins: trigger request failed"
[ "$code" = 201 ] || die "jenkins: trigger answered HTTP $code"
queue="$(tr -d '\r' <"$work/headers" | sed -n 's/^[Ll]ocation: *//p' | head -n 1)"
[[ $queue =~ ^$JENKINS_URL_HOST/queue/item/[0-9]+/$ ]] || die "jenkins: unexpected queue location '$queue'"
say "jenkins: queued $queue"

deadline=$(( $(date +%s) + wait_s ))
build=""
while [ -z "$build" ]; do
  [ "$(date +%s)" -lt "$deadline" ] || die "jenkins: queue item $queue did not start within ${wait_s}s"
  jenkins_curl -f -o "$work/queue.json" "${queue}api/json" || die "jenkins: cannot read $queue"
  [ "$(json_get "$work/queue.json" cancelled)" != True ] || die "jenkins: queue item $queue was cancelled"
  build="$(json_get "$work/queue.json" executable url)"
  [ -n "$build" ] || sleep 2
done
say "jenkins: build $build"

while :; do
  [ "$(date +%s)" -lt "$deadline" ] || die "jenkins: build $build did not finish within ${wait_s}s"
  jenkins_curl -f -o "$work/build.json" "${build}api/json?tree=building,result" || die "jenkins: cannot read $build"
  [ "$(json_get "$work/build.json" building)" = False ] && break
  sleep 3
done
say "jenkins: build $build finished with $(json_get "$work/build.json" result)"
code="$(jenkins_curl -o "$work/result.json" -w '%{http_code}' "${build}artifact/result.json")" || die "jenkins: cannot fetch result.json"
[ "$code" = 200 ] || die "jenkins: build $build archived no result.json (HTTP $code)"
cat "$work/result.json"
