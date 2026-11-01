#!/usr/bin/env bash
# build-checks.sh (make proto-build-checks): the Jenkins job's failure reports, against a running
# prototype stack. Three failing builds are triggered at once:
#   - a SHA that is not in the repository        -> result.json failure_code commit_not_found
#   - the broken-build sample variant            -> failure_code build_failed, nothing pushed
#   - an IMAGE_REPOSITORY for another project    -> failure_code invalid_parameters
# Each result.json must echo the request's project, release and attempt and carry no image_digest.
# Then three good builds at once, and the never-ready variant, must all succeed.
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

[ -f "$JENKINS_ENV" ] || die "$JENKINS_ENV is missing: run make proto-up"
started="$(date +%s)"
work="$(mktemp -d "$PROTO/.jenkins-checks.XXXXXX")"
trap 'rm -rf "$work"' EXIT
uuid() { python3 -I -c 'import uuid; print(uuid.uuid4())'; }
pass() { log "build-checks PASS: $*"; }
build_script="$kind_root/scripts/prototype/jenkins-build.sh"

good_sha="$(bash "$kind_root/scripts/prototype/sample-repo.sh" checks-site site checks-v1)"
broken_sha="$(bash "$kind_root/scripts/prototype/sample-repo.sh" checks-broken broken-build checks-v1)"
missing_sha="$(python3 -I -c 'import secrets; print(secrets.token_hex(20))')"
[ "$missing_sha" != "$good_sha" ] || die "random SHA collided"

p1="$(uuid)"; r1="$(uuid)"; p2="$(uuid)"; r2="$(uuid)"; p3="$(uuid)"; r3="$(uuid)"
bash "$build_script" "$p1" "$r1" 1 "http://$PROTO_GIT_NAME:8080/checks-site.git" "$missing_sha" \
  "$REGISTRY_IN_CLUSTER/webblock/sites/$p1" >"$work/missing.json" &
pid1=$!
bash "$build_script" "$p2" "$r2" 2 "http://$PROTO_GIT_NAME:8080/checks-broken.git" "$broken_sha" \
  "$REGISTRY_IN_CLUSTER/webblock/sites/$p2" >"$work/broken.json" &
pid2=$!
bash "$build_script" "$p3" "$r3" 1 "http://$PROTO_GIT_NAME:8080/checks-site.git" "$good_sha" \
  "$REGISTRY_IN_CLUSTER/webblock/sites/$p1" >"$work/foreign.json" &
pid3=$!
wait "$pid1" || die "the missing-commit build left no result"
wait "$pid2" || die "the broken-build build left no result"
wait "$pid3" || die "the foreign-repository build left no result"

# expect FILE CODE PROJECT RELEASE ATTEMPT
expect() {
  local f="$1"
  [ "$(json_get "$f" failure_code)" = "$2" ] || die "$f: failure_code '$(json_get "$f" failure_code)', expected $2: $(cat "$f")"
  [ -z "$(json_get "$f" image_digest)" ] || die "$f: a failed build reports an image digest"
  [ "$(json_get "$f" project_id)" = "$3" ] && [ "$(json_get "$f" release_id)" = "$4" ] && [ "$(json_get "$f" attempt)" = "$5" ] \
    || die "$f does not echo project $3, release $4, attempt $5: $(cat "$f")"
  pass "$2: $(tr -d '\n' <"$f")"
}
expect "$work/missing.json" commit_not_found "$p1" "$r1" 1
[ "$(json_get "$work/missing.json" commit_sha)" = "$missing_sha" ] || die "commit_not_found does not report the requested SHA"
expect "$work/broken.json" build_failed "$p2" "$r2" 2
[ -z "$(registry_digest "$REGISTRY_LOCAL/webblock/sites/$p2:$r2-2")" ] || die "the broken build pushed an image"
expect "$work/foreign.json" invalid_parameters "$p3" "$r3" 1

# Three good builds at once: all succeed, each with its own project's digest. The limit of two running
# builds is structural (proto-ci.sh checks that the built-in node has exactly 2 executors and inbound
# agents are disabled); cached sample builds take about a second, too short to observe the queue.
pids=(); projects=(); releases=()
for i in 1 2 3; do
  projects[i]="$(uuid)"; releases[i]="$(uuid)"
  bash "$build_script" "${projects[i]}" "${releases[i]}" 1 "http://$PROTO_GIT_NAME:8080/checks-site.git" "$good_sha" \
    "$REGISTRY_IN_CLUSTER/webblock/sites/${projects[i]}" >"$work/good-$i.json" 2>"$work/good-$i.log" &
  pids[i]=$!
done
for i in 1 2 3; do
  wait "${pids[i]}" || die "good build $i left no result: $(tail -n 2 "$work/good-$i.log")"
  [ -z "$(json_get "$work/good-$i.json" failure_code)" ] || die "good build $i failed: $(cat "$work/good-$i.json")"
  [[ $(json_get "$work/good-$i.json" image_digest) =~ ^sha256:[0-9a-f]{64}$ ]] || die "good build $i has no digest"
  [ "$(json_get "$work/good-$i.json" project_id)" = "${projects[i]}" ] || die "good build $i echoes another project"
  [ "$(registry_digest "$REGISTRY_LOCAL/webblock/sites/${projects[i]}:${releases[i]}-1")" = "$(json_get "$work/good-$i.json" image_digest)" ] \
    || die "good build $i: the registry does not serve its digest"
done
pass "three concurrent good builds succeeded, each digest served by the registry under its own project"

# The never-ready variant builds and pushes like the site (it only fails at run time).
nr_sha="$(bash "$kind_root/scripts/prototype/sample-repo.sh" checks-never-ready never-ready checks-v1)"
p4="$(uuid)"; r4="$(uuid)"
bash "$build_script" "$p4" "$r4" 1 "http://$PROTO_GIT_NAME:8080/checks-never-ready.git" "$nr_sha" \
  "$REGISTRY_IN_CLUSTER/webblock/sites/$p4" >"$work/never-ready.json"
[ -z "$(json_get "$work/never-ready.json" failure_code)" ] || die "the never-ready variant failed to build: $(cat "$work/never-ready.json")"
[[ $(json_get "$work/never-ready.json" image_digest) =~ ^sha256:[0-9a-f]{64}$ ]] || die "the never-ready build has no digest"
pass "never-ready variant built: $(tr -d '\n' <"$work/never-ready.json")"
log "proto-build-checks: all checks passed in $(( $(date +%s) - started ))s"
