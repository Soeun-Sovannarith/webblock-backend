#!/usr/bin/env bash
# build.sh: builds the Provisioning service's runtime image (task PK; decision 0016 section 9 rulings 4
# and 5) and saves it as a docker-archive. Progress goes to standard error; the last line of standard
# output is the archive's path, bin/kind-state/provisioning-image/provisioning-<revision>.tar, where the
# revision is the commit's first 12 hex digits, with -dirty when src/, vendor/, pyproject.toml, uv.lock
# or deploy/image/ differ from it. scripts/prototype/run-provisioning.sh pushes the archive (PROV_MODE=cluster).
#
# - The build context is an allow-list, copied into bin/kind-state/provisioning-image/context: this
#   Dockerfile, pyproject.toml, uv.lock, the tracked files of src/provisioning (without hosts/devtools/),
#   src/contracts, src/migrations and vendor/ (the webblock-commons wheel uv.lock pins by hash), and
#   bin/uv and bin/goose after scripts/py/tools.sh verified them.
#   Nothing else of the repository (bin/kind-state, .env, untracked files) can reach the builder.
# - The base is BASE_IMAGE of deploy/image/versions.sh, by digest.
# - The builder is the pinned docker-buildx plugin (make kind-tools) through the project-local
#   DOCKER_CONFIG bin/kind-state/docker-cli, on the daemon of the caller's current Docker context; when
#   the plugin is not installed, the legacy builder (DOCKER_BUILDKIT=0), which this Dockerfile's syntax
#   allows. The stack's BuildKit is never used: it runs untrusted tenant Dockerfiles (OD-37, OD-62).
# - The local image is removed after the save.
# Never reads .env and contacts no cluster.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=versions.sh
. "$root/deploy/image/versions.sh"
# shellcheck source=../kind/versions.sh
. "$root/deploy/kind/versions.sh"
work="$root/bin/kind-state/provisioning-image"
context="$work/context"
docker_cli="$root/bin/kind-state/docker-cli"

bash "$root/scripts/py/tools.sh" uv goose >&2

rev="$(git -C "$root" rev-parse --short=12 HEAD)"
if [ -n "$(git -C "$root" status --porcelain -- src vendor pyproject.toml uv.lock deploy/image)" ]; then
  rev="$rev-dirty"
fi
tag="webblock-provisioning-build:$rev"

mkdir -p "$work"
chmod 0700 "$work"
rm -rf "$context"
mkdir "$context"
cp "$root/deploy/image/Dockerfile" "$root/pyproject.toml" "$root/uv.lock" "$root/bin/uv" "$root/bin/goose" "$context/"
git -C "$root" ls-files -z -- src/provisioning src/contracts src/migrations vendor \
  | grep -zv '^src/provisioning/hosts/devtools/' \
  | (cd "$root" && xargs -0 cp --parents -t "$context")
# cp keeps the source modes, so a checkout made under a restrictive umask would give files the image's
# non-root user (10001) cannot read. Make everything world-readable (directories and executables
# searchable) and not writable by group or others, whatever the caller's umask or checkout.
chmod -R a+rX,go-w "$context"

if [ -e "$docker_cli/cli-plugins/docker-buildx" ]; then
  [ "$(sha256sum "$root/bin/docker-buildx" | cut -d' ' -f1)" = "$BUILDX_SHA256" ] \
    || { echo "build.sh: bin/docker-buildx does not match its pin; run make kind-tools" >&2; exit 1; }
  host="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
  echo "build.sh: building $tag with docker-buildx $BUILDX_VERSION" >&2
  DOCKER_CONFIG="$docker_cli" DOCKER_HOST="$host" docker buildx build --builder default --load \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" --tag "$tag" "$context" >&2
else
  echo "build.sh: bin/docker-buildx is not installed (make kind-tools); building $tag with the legacy builder" >&2
  DOCKER_BUILDKIT=0 docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" --tag "$tag" "$context" >&2
fi

rm -f "$work"/provisioning-*.tar
archive="$work/provisioning-$rev.tar"
# The local image is removed whether or not the save succeeds.
trap 'docker image rm "$tag" >/dev/null 2>&1 || true' EXIT
docker save --output "$archive" "$tag"
echo "$archive"
