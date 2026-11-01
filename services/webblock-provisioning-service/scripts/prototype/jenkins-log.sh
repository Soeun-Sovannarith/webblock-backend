#!/usr/bin/env bash
# jenkins-log.sh BUILD_NUMBER [LINES]: prints the last LINES (default 60) lines of the console log of
# build BUILD_NUMBER of webblock-site-build, with the API token from bin/kind-state/proto/jenkins.env.
# For debugging the prototype; the token is never printed.
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

[[ ${1-} =~ ^[0-9]+$ ]] || { echo "usage: $0 BUILD_NUMBER [LINES]" >&2; exit 2; }
jenkins_curl -f "$JENKINS_URL_HOST/job/$PROTO_JOB/$1/consoleText" | tail -n "${2:-60}"
