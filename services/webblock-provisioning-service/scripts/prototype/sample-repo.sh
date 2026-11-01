#!/usr/bin/env bash
# sample-repo.sh NAME VARIANT [VERSION]: creates or updates the bare Git repository NAME in
# bin/kind-state/proto/git/ (served by webblock-git as http://webblock-git:8080/NAME.git) with one new
# commit whose tree is exactly deploy/prototype/sample-site/VARIANT, with @VERSION@ in index.html
# replaced by VERSION (default v1). Prints the full commit SHA on standard output and nothing else.
#   VARIANT  site          builds, runs on 8080, GET / answers 200 with "sample-site version: VERSION"
#            never-ready   builds, but the container exits at once (never ready)
#            broken-build  the Dockerfile fails to build
#            preview-only  a test fixture: /readyz answers 200 only on a preview hostname (task T2)
# Every call makes a new commit, even with the same variant and version. The user's Git configuration
# (hooks, signing, templates) is not read.
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

usage() { echo "usage: $0 NAME site|never-ready|broken-build|unready|slow-start|preview-only [VERSION]" >&2; exit 2; }
name="${1-}"; variant="${2-}"; version="${3:-v1}"
[[ $name =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || usage
case "$variant" in site | never-ready | broken-build | unready | slow-start | preview-only) ;; *) usage ;; esac
[[ $version =~ ^[A-Za-z0-9._-]{1,64}$ ]] || { echo "VERSION must match [A-Za-z0-9._-]{1,64}" >&2; exit 2; }
command -v git >/dev/null 2>&1 || die "git is required"
src="$proto_dir/sample-site/$variant"
[ -f "$src/Dockerfile" ] || die "$src/Dockerfile is missing"

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_TEMPLATE_DIR
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
g() { git -c user.name="WebBlock sample" -c user.email="sample@webblock.test" -c commit.gpgsign=false \
  -c core.hooksPath=/dev/null -c init.defaultBranch=main "$@"; }

mkdir -p "$GIT_ROOT"
bare="$GIT_ROOT/$name.git"
[ -d "$bare" ] || g init --quiet --bare "$bare" >&2
work="$(mktemp -d "$PROTO/.sample-work.XXXXXX")"
trap 'rm -rf "$work"' EXIT
g clone --quiet "$bare" "$work/repo" 2>/dev/null
repo="$work/repo"
g -C "$repo" rm -rq --ignore-unmatch -- . >&2
cp -R "$src/." "$repo/"
[ ! -f "$repo/index.html" ] || sed -i "s/@VERSION@/$version/g" "$repo/index.html"
g -C "$repo" add -A >&2
g -C "$repo" commit --quiet --allow-empty -m "sample $variant $version" >&2
g -C "$repo" push --quiet origin HEAD:refs/heads/main >&2
g --git-dir="$bare" symbolic-ref HEAD refs/heads/main
g --git-dir="$bare" update-server-info
sha="$(g -C "$repo" rev-parse HEAD)"
[[ $sha =~ ^[0-9a-f]{40}$ ]] || die "unexpected commit id"
printf '%s\n' "$sha"
