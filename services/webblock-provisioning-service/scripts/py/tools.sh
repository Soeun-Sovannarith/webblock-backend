#!/usr/bin/env bash
# Installs the pinned tool binaries of the Python port into ./bin (decision 0013 sections 9 and 10.3):
# bin/uv, bin/osv-scanner, bin/goose, bin/grype (task H1) and bin/protoc (decision 0022 E3), for
# linux x86_64 only.
#
#   bash scripts/py/tools.sh                 # all five
#   bash scripts/py/tools.sh goose           # only the named tools (uv, osv-scanner, goose, grype, protoc)
#
# Each download is checked against the SHA-256 pinned in scripts/py/versions.sh AND against the
# checksum file the project publishes for that release; any mismatch stops the script, exit 3, and
# installs nothing for that tool. What each project publishes:
#   uv           <asset>.sha256 beside the archive, and sha256.sum for the whole release (both checked);
#                the uv binary inside the archive is checked against its own pin as well
#   osv-scanner  osv-scanner_SHA256SUMS
#   goose        checksums.txt
#   grype        grype_<version>_checksums.txt; the grype binary inside the archive is checked against
#                its own pin as well
#   protoc       tool_integrity.bzl (RELEASED_BINARY_INTEGRITY); the protoc binary inside the zip is
#                checked against its own pin as well
# A tool already installed with the pinned checksum is not downloaded again; one with any other
# checksum (an older version, a modified or truncated file) is downloaded, verified and replaced.
# Downloads are HTTPS only (redirects too), each with bounded retries and its own time limit, and the
# whole run stops after TOTAL_SECONDS. Nothing is written outside ./bin: the downloads go to a
# temporary directory inside ./bin that is removed on exit, and a verified binary replaces the old
# one by an atomic rename. Nothing needs root and no cluster is contacted.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=versions.sh
. "$root/scripts/py/versions.sh"
bindir="$root/bin"
TOTAL_SECONDS=900

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
  echo "py-tools: unsupported platform $(uname -s)-$(uname -m): only linux x86_64 is pinned" >&2
  exit 2
fi
for c in curl sha256sum tar mktemp awk; do
  command -v "$c" >/dev/null 2>&1 || { echo "py-tools: $c is required" >&2; exit 2; }
done

want_uv=0 want_osv=0 want_goose=0 want_grype=0 want_protoc=0
if [ "$#" -eq 0 ]; then
  want_uv=1 want_osv=1 want_goose=1 want_grype=1 want_protoc=1
fi
for arg in "$@"; do
  case "$arg" in
    uv) want_uv=1 ;;
    osv-scanner) want_osv=1 ;;
    goose) want_goose=1 ;;
    grype) want_grype=1 ;;
    protoc) want_protoc=1 ;;
    *) echo "py-tools: unknown tool '$arg' (expected uv, osv-scanner, goose, grype or protoc)" >&2; exit 2 ;;
  esac
done

mkdir -p "$bindir"
tmp="$(mktemp -d "$bindir/.py-tools.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

# fetch URL FILE MAXSECONDS: bounded HTTPS download that fails on any HTTP error. The time limit is
# the smaller of MAXSECONDS and what is left of TOTAL_SECONDS.
fetch() {
  local left=$((TOTAL_SECONDS - SECONDS)) limit="$3"
  if [ "$left" -le 0 ]; then
    echo "py-tools: the run exceeded $TOTAL_SECONDS s; nothing more is downloaded" >&2
    exit 4
  fi
  if [ "$left" -lt "$limit" ]; then limit="$left"; fi
  curl -sSfL --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --retry 3 --retry-delay 3 --retry-all-errors --connect-timeout 30 --max-time "$limit" \
    -o "$2" "$1"
}

# verify NAME FILE PINNED PUBLISHED...: the file must equal the pin and every published checksum.
verify() {
  local name="$1" file="$2" pinned="$3" got p
  shift 3
  got="$(sha_of "$file")"
  if [ "$got" != "$pinned" ]; then
    echo "py-tools: checksum mismatch for $name: got $got, pinned $pinned; nothing installed" >&2
    exit 3
  fi
  for p in "$@"; do
    if [ "$got" != "$p" ]; then
      echo "py-tools: checksum mismatch for $name: got $got, published ${p:-<empty>}; nothing installed" >&2
      exit 3
    fi
  done
}

# published_sum SUMSFILE ASSET: the checksum listed for ASSET (sha256sum format, text or binary mode).
published_sum() { awk -v f="$2" '$2 == f || $2 == "*" f {print $1}' "$1"; }

# is_installed PATH PINNED: true when the file exists, is executable and has the pinned checksum.
is_installed() { [ -x "$1" ] && [ "$(sha_of "$1")" = "$2" ]; }

# place SOURCE TARGET: atomic replacement of TARGET by a verified SOURCE.
place() {
  local staged
  staged="$(mktemp "$tmp/stage.XXXXXX")"
  cp "$1" "$staged"
  chmod 0755 "$staged"
  mv -f "$staged" "$2"
}

# check_version BINARY EXPECTED: the installed binary reports the pinned version.
check_version() {
  local out
  out="$("$1" --version 2>&1 | head -n 1)" || { echo "py-tools: $1 --version failed" >&2; exit 3; }
  case "$out" in
    *"$2"*) ;;
    *) echo "py-tools: $1 reports '$out', expected $2" >&2; exit 3 ;;
  esac
}

# --- uv ---------------------------------------------------------------------------------------------
if [ "$want_uv" = 1 ]; then
  uv_bin="$bindir/uv"
  if is_installed "$uv_bin" "$UV_BIN_SHA256"; then
    echo "uv $UV_VERSION: present ($uv_bin), checksum verified, nothing downloaded"
  else
    echo "uv $UV_VERSION: downloading"
    fetch "$UV_BASE_URL/$UV_TGZ" "$tmp/uv.tgz" 300
    fetch "$UV_BASE_URL/$UV_TGZ.sha256" "$tmp/uv.tgz.sha256" 60
    fetch "$UV_BASE_URL/sha256.sum" "$tmp/uv.sha256.sum" 60
    verify "uv archive" "$tmp/uv.tgz" "$UV_TGZ_SHA256" \
      "$(published_sum "$tmp/uv.tgz.sha256" "$UV_TGZ")" "$(published_sum "$tmp/uv.sha256.sum" "$UV_TGZ")"
    mkdir "$tmp/uv"
    tar -xzf "$tmp/uv.tgz" -C "$tmp/uv" uv-x86_64-unknown-linux-gnu/uv
    verify "uv binary" "$tmp/uv/uv-x86_64-unknown-linux-gnu/uv" "$UV_BIN_SHA256"
    place "$tmp/uv/uv-x86_64-unknown-linux-gnu/uv" "$uv_bin"
    echo "uv $UV_VERSION: installed $uv_bin (sha256 $UV_BIN_SHA256)"
  fi
  check_version "$uv_bin" "uv $UV_VERSION"
fi

# --- osv-scanner ------------------------------------------------------------------------------------
if [ "$want_osv" = 1 ]; then
  osv_bin="$bindir/osv-scanner"
  if is_installed "$osv_bin" "$OSV_SCANNER_SHA256"; then
    echo "osv-scanner $OSV_SCANNER_VERSION: present ($osv_bin), checksum verified, nothing downloaded"
  else
    echo "osv-scanner $OSV_SCANNER_VERSION: downloading"
    fetch "$OSV_SCANNER_BASE_URL/$OSV_SCANNER_ASSET" "$tmp/osv-scanner" 300
    fetch "$OSV_SCANNER_BASE_URL/osv-scanner_SHA256SUMS" "$tmp/osv.sums" 60
    verify osv-scanner "$tmp/osv-scanner" "$OSV_SCANNER_SHA256" \
      "$(published_sum "$tmp/osv.sums" "$OSV_SCANNER_ASSET")"
    place "$tmp/osv-scanner" "$osv_bin"
    echo "osv-scanner $OSV_SCANNER_VERSION: installed $osv_bin (sha256 $OSV_SCANNER_SHA256)"
  fi
  check_version "$osv_bin" "${OSV_SCANNER_VERSION#v}"
fi

# --- goose ------------------------------------------------------------------------------------------
if [ "$want_goose" = 1 ]; then
  goose_bin="$bindir/goose"
  if is_installed "$goose_bin" "$GOOSE_SHA256"; then
    echo "goose $GOOSE_VERSION: present ($goose_bin), checksum verified, nothing downloaded"
  else
    echo "goose $GOOSE_VERSION: downloading"
    fetch "$GOOSE_BASE_URL/$GOOSE_ASSET" "$tmp/goose" 300
    fetch "$GOOSE_BASE_URL/checksums.txt" "$tmp/goose.sums" 60
    verify goose "$tmp/goose" "$GOOSE_SHA256" "$(published_sum "$tmp/goose.sums" "$GOOSE_ASSET")"
    place "$tmp/goose" "$goose_bin"
    echo "goose $GOOSE_VERSION: installed $goose_bin (sha256 $GOOSE_SHA256)"
  fi
  check_version "$goose_bin" "$GOOSE_VERSION"
fi

# --- grype ------------------------------------------------------------------------------------------
if [ "$want_grype" = 1 ]; then
  grype_bin="$bindir/grype"
  if is_installed "$grype_bin" "$GRYPE_BIN_SHA256"; then
    echo "grype $GRYPE_VERSION: present ($grype_bin), checksum verified, nothing downloaded"
  else
    echo "grype $GRYPE_VERSION: downloading"
    fetch "$GRYPE_BASE_URL/$GRYPE_TGZ" "$tmp/grype.tgz" 300
    fetch "$GRYPE_BASE_URL/$GRYPE_SUMS" "$tmp/grype.sums" 60
    verify "grype archive" "$tmp/grype.tgz" "$GRYPE_TGZ_SHA256" "$(published_sum "$tmp/grype.sums" "$GRYPE_TGZ")"
    mkdir "$tmp/grype"
    tar -xzf "$tmp/grype.tgz" -C "$tmp/grype" grype
    verify "grype binary" "$tmp/grype/grype" "$GRYPE_BIN_SHA256"
    place "$tmp/grype/grype" "$grype_bin"
    echo "grype $GRYPE_VERSION: installed $grype_bin (sha256 $GRYPE_BIN_SHA256)"
  fi
  check_version "$grype_bin" "grype ${GRYPE_VERSION#v}"
fi

# --- protoc -----------------------------------------------------------------------------------------
# The release publishes no SHA256SUMS file; tool_integrity.bzl lists the SHA-256 of each protoc zip.
# The zip is read with unzip (bin/protoc only).
if [ "$want_protoc" = 1 ]; then
  protoc_bin="$bindir/protoc"
  if is_installed "$protoc_bin" "$PROTOC_BIN_SHA256"; then
    echo "protoc $PROTOC_VERSION: present ($protoc_bin), checksum verified, nothing downloaded"
  else
    command -v unzip >/dev/null 2>&1 || { echo "py-tools: unzip is required for protoc" >&2; exit 2; }
    echo "protoc $PROTOC_VERSION: downloading"
    fetch "$PROTOC_BASE_URL/$PROTOC_ZIP" "$tmp/protoc.zip" 300
    fetch "$PROTOC_BASE_URL/tool_integrity.bzl" "$tmp/protoc.integrity" 60
    verify "protoc zip" "$tmp/protoc.zip" "$PROTOC_ZIP_SHA256" \
      "$(awk -v f="\"$PROTOC_ZIP\":" '$1 == f {gsub(/[",]/, "", $2); print $2}' "$tmp/protoc.integrity")"
    mkdir "$tmp/protoc"
    unzip -q "$tmp/protoc.zip" bin/protoc -d "$tmp/protoc"
    verify "protoc binary" "$tmp/protoc/bin/protoc" "$PROTOC_BIN_SHA256"
    place "$tmp/protoc/bin/protoc" "$protoc_bin"
    echo "protoc $PROTOC_VERSION: installed $protoc_bin (sha256 $PROTOC_BIN_SHA256)"
  fi
  check_version "$protoc_bin" "libprotoc ${PROTOC_VERSION#v}"
fi
