#!/usr/bin/env bash
# Copies the pinned commit of web-block-contracts into this repository and generates the protobuf
# Python code from its protos (decision 0022 E3). make contracts-sync and make contracts-check run it.
#
#   scripts/contracts/sync.sh           write src/contracts/shared/ and the generated package
#   scripts/contracts/sync.sh --check   build into a temporary folder and fail on any difference
#
# CONTRACTS_REPO is the local clone (default ../web-block-contracts, from the module root). The commit
# is the full SHA in contracts.lock. Only git archive reads the clone, so its working tree, branch and
# uncommitted files do not matter. The compiler is the pinned, checksum-verified bin/protoc (make
# py-tools; scripts/py/versions.sh), not grpcio-tools. Only --python_out and --pyi_out are generated:
# no gRPC stubs, and google.rpc comes from googleapis-common-protos (a dependency of grpcio-status).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

MODE=sync
case "${1:-}" in
  '') ;;
  --check) MODE=check ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

REPO="${CONTRACTS_REPO:-../web-block-contracts}"
SHARED=src/contracts/shared
GEN=src/provisioning/infrastructure/grpc/_gen
GEN_PKG=provisioning.infrastructure.grpc._gen
PROTOC="$ROOT/bin/protoc"

[ -f contracts.lock ] || { echo "contracts.lock is missing" >&2; exit 1; }
SHA="$(tr -d '[:space:]' < contracts.lock)"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "contracts.lock must hold one full commit SHA (40 lowercase hex)" >&2; exit 1; }
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 \
  || { echo "CONTRACTS_REPO $REPO is not a git repository; clone web-block-contracts there" >&2; exit 1; }
git -C "$REPO" cat-file -e "$SHA^{commit}" 2>/dev/null \
  || { echo "commit $SHA of contracts.lock is not in $REPO; fetch it" >&2; exit 1; }
# shellcheck source=../py/versions.sh
. "$ROOT/scripts/py/versions.sh"
[ -x "$PROTOC" ] && [ "$(sha256sum "$PROTOC" | cut -d' ' -f1)" = "$PROTOC_BIN_SHA256" ] \
  || { echo "$PROTOC is missing or not the pinned protoc $PROTOC_VERSION; run make py-tools" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src" "$TMP/shared" "$TMP/gen"

git -C "$REPO" archive "$SHA" events proto | tar -x -C "$TMP/src"
cp -R "$TMP/src/events" "$TMP/src/proto" "$TMP/shared/"

mapfile -t PROTOS < <(cd "$TMP/src/proto" && find . -name '*.proto' -printf '%P\n' | LC_ALL=C sort)
[ "${#PROTOS[@]}" -gt 0 ] || { echo "commit $SHA has no .proto file under proto/" >&2; exit 1; }
(cd "$TMP/src/proto" && "$PROTOC" -I . --python_out="$TMP/gen" --pyi_out="$TMP/gen" "${PROTOS[@]}")

# The generated modules import each other as top-level "webblock"; they live under the package.
find "$TMP/gen" -type f \( -name '*.py' -o -name '*.pyi' \) -print0 \
  | xargs -0 sed -i -E "s/^(from|import) webblock\./\1 ${GEN_PKG}.webblock./"
find "$TMP/gen" -type d -print0 | while IFS= read -r -d '' dir; do : > "$dir/__init__.py"; done

if [ "$MODE" = check ]; then
  status=0
  diff -r "$TMP/shared" "$SHARED" || status=1
  diff -r -x __pycache__ "$TMP/gen" "$GEN" || status=1
  if [ "$status" -ne 0 ]; then
    echo "contracts-check: $SHARED or $GEN differs from commit $SHA; run make contracts-sync" >&2
    exit 1
  fi
  echo "contracts-check: $SHARED and $GEN match commit $SHA"
else
  rm -rf "$SHARED" "$GEN"
  mkdir -p "$(dirname "$SHARED")" "$(dirname "$GEN")"
  cp -R "$TMP/shared" "$SHARED"
  cp -R "$TMP/gen" "$GEN"
  echo "contracts-sync: $SHARED and $GEN written from commit $SHA"
fi
