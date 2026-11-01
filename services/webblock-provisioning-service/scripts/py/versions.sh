# Pinned tool binaries for the Python port, linux x86_64 only (decision 0013 sections 9 and 10.3).
# make py-age checks that each version below is at least 14 days old (scripts/py/age.py).
# Sourced by scripts/py/tools.sh; never executed on its own.
#
# Version rule (ruling 10.5): the newest release published on or before 24 September 2026, the date
# of [tool.uv] exclude-newer in pyproject.toml, except goose, which stays at the Go reference's
# version. Each SHA-256 below was computed from the downloaded file on 8 October 2026 and matched
# the checksum file the project publishes and the digest GitHub lists for the release asset.
# Changing a pin is a reviewed change: version, URL and SHA-256 move together.

# uv 0.12.18, published 2026-09-22T23:00:51Z (github.com/astral-sh/uv/releases/tag/0.12.18).
# 0.12.19 was published 2026-09-25T00:33:02Z, after the cut-off.
# Published checksums: <asset>.sha256 next to each asset, and sha256.sum for the whole release.
UV_VERSION=0.12.18
UV_TGZ=uv-x86_64-unknown-linux-gnu.tar.gz
UV_BASE_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
UV_TGZ_SHA256=89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16
# The uv binary inside the archive (uv-x86_64-unknown-linux-gnu/uv), so an installed copy can be
# verified without the archive.
UV_BIN_SHA256=b97ae0e1ed3661fcd0c446cc59d81f5720c24d35627b21814a7581fb67eba673

# osv-scanner v2.6.0, published 2026-09-14T02:55:16Z (github.com/google/osv-scanner/releases/tag/v2.6.0).
# Published checksums: osv-scanner_SHA256SUMS. (The release also carries SLSA provenance,
# multiple.intoto.jsonl, which this script does not verify.)
OSV_SCANNER_VERSION=v2.6.0
OSV_SCANNER_ASSET=osv-scanner_linux_amd64
OSV_SCANNER_BASE_URL="https://github.com/google/osv-scanner/releases/download/$OSV_SCANNER_VERSION"
OSV_SCANNER_SHA256=ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108

# goose v3.28.0, published 2026-09-02T08:34:11Z (github.com/pressly/goose/releases/tag/v3.28.0), the
# version the Go reference used through go install (its Makefile's GOOSE_VERSION, branch go-reference).
# This is now the only goose pin: make migrate-up, migrate-status, py-test-integration and proto-run
# (scripts/prototype/run-provisioning.sh) use this bin/goose for Provisioning's own migrations. The
# prototype's catalog database is migrated by the Project service's Flyway migrations instead
# (deploy/kind/proto-catalog.sh, decision 0017 section 1).
# Published checksums: checksums.txt.
GOOSE_VERSION=v3.28.0
GOOSE_ASSET=goose_linux_x86_64
GOOSE_BASE_URL="https://github.com/pressly/goose/releases/download/$GOOSE_VERSION"
GOOSE_SHA256=ab073515b78ef345f64f018c0d79aa7db50106806efc686dda7181253765ae13

# grype v0.119.0, published 2026-09-17T16:45:23Z (github.com/anchore/grype/releases/tag/v0.119.0), the
# runtime image scanner of make image-scan (task H1). The newest release at least 14 days old on
# 10 October 2026: v0.120.0 was published 2026-10-02. Grype, not Trivy: Trivy's release binaries
# and GitHub actions were compromised in a supply-chain attack in March 2026. Both SHA-256 values
# were computed from the download on 10 October 2026 and matched grype_0.119.0_checksums.txt and the
# digest GitHub lists for the asset. (The checksum file is also signed, .sig and .pem, which this
# script does not verify.)
GRYPE_VERSION=v0.119.0
GRYPE_TGZ=grype_0.119.0_linux_amd64.tar.gz
GRYPE_SUMS=grype_0.119.0_checksums.txt
GRYPE_BASE_URL="https://github.com/anchore/grype/releases/download/$GRYPE_VERSION"
GRYPE_TGZ_SHA256=3fa2dc4b924621ab65404cf08d0b8438d896d80ab949c9d5a4ca283c36004c9b
# The grype binary inside the archive, so an installed copy can be verified without the archive.
GRYPE_BIN_SHA256=e02ba25615668c6bae03473e3c6493b6dfffc2e4e419f65f9bd62555ac10cd0e

# protoc 36.2, published 2026-09-17T17:33:01Z (github.com/protocolbuffers/protobuf/releases/tag/v36.2),
# the protobuf compiler of make contracts-sync (decision 0022 E3; it replaces grpcio-tools, which needs
# setuptools with an open advisory). protoc 36.2 matches the protobuf Python runtime 7.36.2 of
# uv.lock, so the generated code's version check passes at import. Published checksums: the release's
# tool_integrity.bzl (RELEASED_BINARY_INTEGRITY) and the digest GitHub lists for the asset; both
# matched the download of 24 October 2026. Only bin/protoc is installed from the zip; the generated
# code needs none of the well-known types in its include/ folder.
PROTOC_VERSION=v36.2
PROTOC_ZIP=protoc-36.2-linux-x86_64.zip
PROTOC_BASE_URL="https://github.com/protocolbuffers/protobuf/releases/download/$PROTOC_VERSION"
PROTOC_ZIP_SHA256=121f6c7afe1d4d0e3ea6aab9432038599250134cbf4474cb1167d2c7decd4278
# The protoc binary inside the zip (bin/protoc), so an installed copy can be verified without the zip.
PROTOC_BIN_SHA256=f6ee22d65361338459208da8bff801c9ca689741cba9187b34990dd45198f864
