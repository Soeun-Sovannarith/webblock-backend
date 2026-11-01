#!/usr/bin/env bash
# publish-demo-site.sh NAME: publishes the demo website deploy/prototype/demo-sites/NAME into the local
# Git server as repository NAME (bin/kind-state/proto/git/NAME.git, served by webblock-git as
# http://webblock-git:8080/NAME.git), in the manner of sample-repo.sh: one new commit whose tree is
# exactly the folder's files, with the folder as the repository root. Prints the full commit SHA on
# standard output and nothing else.
#
# The files are the ones Git would track in that folder: tracked files and new files that are not
# ignored, as they are in the working tree. The folder's own .gitignore keeps node_modules, .next and
# other build output out. The commit message names the module commit it came from, with "+changes"
# when the folder differs from it. Every call makes a new commit, even when nothing changed. The user's
# Git configuration (hooks, signing, templates) is not read.
. "$(dirname "${BASH_SOURCE[0]}")/../../deploy/kind/proto-lib.sh"

usage() { echo "usage: $0 NAME   (a folder under deploy/prototype/demo-sites/)" >&2; exit 2; }
name="${1-}"
[[ $name =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || usage
command -v git >/dev/null 2>&1 || die "git is required"
src_rel="deploy/prototype/demo-sites/$name"
src="$kind_root/$src_rel"
[ -d "$src" ] || die "$src_rel does not exist"
[ -f "$src/Dockerfile" ] || die "$src_rel/Dockerfile is missing"

unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_TEMPLATE_DIR
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0
g() { git -c user.name="WebBlock demo" -c user.email="demo@webblock.test" -c commit.gpgsign=false \
  -c core.hooksPath=/dev/null -c init.defaultBranch=main "$@"; }

# Where the files come from, for the commit message.
origin="$(g -C "$kind_root" rev-parse --short=12 HEAD 2>/dev/null)" || die "$kind_root is not a Git checkout"
if [ -n "$(g -C "$kind_root" status --porcelain -- "$src_rel")" ]; then origin="$origin+changes"; fi

mkdir -p "$GIT_ROOT"
bare="$GIT_ROOT/$name.git"
[ -d "$bare" ] || g init --quiet --bare "$bare" >&2
work="$(mktemp -d "$PROTO/.publish-work.XXXXXX")"
trap 'rm -rf "$work"' EXIT
g clone --quiet "$bare" "$work/repo" 2>/dev/null
repo="$work/repo"
g -C "$repo" rm -rq --ignore-unmatch -- . >&2

count=0
while IFS= read -r -d '' path; do
  [ -f "$kind_root/$path" ] || continue # tracked but deleted in the working tree
  rel="${path#"$src_rel"/}"
  mkdir -p "$repo/$(dirname "$rel")"
  cp -p "$kind_root/$path" "$repo/$rel"
  count=$((count + 1))
done < <(g -C "$kind_root" ls-files -z --cached --others --exclude-standard -- "$src_rel")
[ "$count" -gt 0 ] || die "no files to publish in $src_rel"
[ -f "$repo/Dockerfile" ] || die "the published tree has no Dockerfile at its root"

g -C "$repo" add -A >&2
g -C "$repo" commit --quiet --allow-empty -m "demo site $name from $origin" >&2
g -C "$repo" push --quiet origin HEAD:refs/heads/main >&2
g --git-dir="$bare" symbolic-ref HEAD refs/heads/main
g --git-dir="$bare" update-server-info
sha="$(g -C "$repo" rev-parse HEAD)"
[[ $sha =~ ^[0-9a-f]{40}$ ]] || die "unexpected commit id"
printf '%s\n' "$sha"
