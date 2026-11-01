#!/usr/bin/env bash
# Scans gradle.lockfile for known vulnerabilities with a pinned, checksum-verified osv-scanner.
# Not part of `./gradlew check`: it downloads the advisory database. Exits non-zero on findings.
set -euo pipefail

# osv-scanner v2.6.0, published 2026-09-14T02:55:16Z (github.com/google/osv-scanner/releases/tag/v2.6.0).
# Checksum from the release's osv-scanner_SHA256SUMS. Recorded in .claude/docs/dependency-versions.md.
OSV_SCANNER_VERSION=v2.6.0
OSV_SCANNER_SHA256=ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108
OSV_SCANNER_URL="https://github.com/google/osv-scanner/releases/download/${OSV_SCANNER_VERSION}/osv-scanner_linux_amd64"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="$root/bin/osv-scanner"

verify() {
  local actual
  actual="$(sha256sum "$1" | cut -d' ' -f1)"
  [ "$actual" = "$OSV_SCANNER_SHA256" ]
}

if [ ! -x "$bin" ]; then
  mkdir -p "$root/bin"
  tmp="$(mktemp "$root/bin/osv-scanner.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  curl --fail --silent --show-error --location "$OSV_SCANNER_URL" --output "$tmp"
  if ! verify "$tmp"; then
    echo "osv-scanner checksum mismatch; refusing to use the download" >&2
    exit 1
  fi
  chmod +x "$tmp"
  mv "$tmp" "$bin"
  trap - EXIT
elif ! verify "$bin"; then
  echo "bin/osv-scanner does not match the pinned checksum; delete it and rerun" >&2
  exit 1
fi

cd "$root"
exec "$bin" scan source --lockfile gradle.lockfile
