#!/usr/bin/env bash
# Copies the named files of web-block-contracts, at the commit in contracts.lock, into contracts/ (TR-11).
# The same shape as web-block-commons/scripts/contracts-sync.sh.
#
#   scripts/contracts-sync.sh           write contracts/ from the pinned commit
#   scripts/contracts-sync.sh --check   build the copy in a temporary folder and fail on any difference
#
# CONTRACTS_REPO is the local clone (default ../web-block-contracts, from the repository root). The commit is the
# full SHA in contracts.lock. Only git show reads the clone, so its working tree, branch and uncommitted files do
# not matter. contracts/ is never edited by hand.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE=sync
case "${1:-}" in
  '') ;;
  --check) MODE=check ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

# The files the service uses. The protobuf Gradle plugin compiles contracts/proto in the contracts source set
# (decision 0011); AccessActionListTest reads contracts/access (TR-27).
FILES=(
  proto/webblock/project/v1/access.proto
  access/actions.v1.json
  access/actions.v1.schema.json
)

REPO="${CONTRACTS_REPO:-../web-block-contracts}"
COPY=contracts

[ -f contracts.lock ] || { echo "contracts.lock is missing" >&2; exit 1; }
SHA="$(tr -d '[:space:]' < contracts.lock)"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] \
  || { echo "contracts.lock must hold one full commit SHA (40 lowercase hex characters)" >&2; exit 1; }
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 \
  || { echo "CONTRACTS_REPO $REPO is not a git repository; clone web-block-contracts there" >&2; exit 1; }
git -C "$REPO" cat-file -e "$SHA^{commit}" 2>/dev/null \
  || { echo "commit $SHA of contracts.lock is not in $REPO; fetch it" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/$COPY"
for file in "${FILES[@]}"; do
  mkdir -p "$TMP/$COPY/$(dirname "$file")"
  git -C "$REPO" show "$SHA:$file" > "$TMP/$COPY/$file" 2>/dev/null \
    || { echo "commit $SHA of contracts.lock has no $file" >&2; exit 1; }
done

if [ "$MODE" = check ]; then
  if [ ! -d "$COPY" ] || ! diff -r "$TMP/$COPY" "$COPY" >&2; then
    echo "contracts-check: $COPY/ differs from commit $SHA of web-block-contracts; run scripts/contracts-sync.sh" >&2
    exit 1
  fi
  echo "contracts-check: $COPY/ matches commit $SHA"
else
  rm -rf "$COPY"
  cp -R "$TMP/$COPY" "$COPY"
  echo "contracts-sync: $COPY/ written from commit $SHA"
fi
