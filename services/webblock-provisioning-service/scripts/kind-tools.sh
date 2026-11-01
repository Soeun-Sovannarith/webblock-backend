#!/usr/bin/env bash
# Installs the pinned Kind stack tools into ./bin (decision 0009 section 1): bin/kind, bin/k3d (the
# second local engine, spike design k3s-site-cluster-spike G2), bin/kubectl, bin/helm, bin/crane and
# bin/docker-buildx (decision 0016 section 9 ruling 5). Their state (kubeconfig, caches, Helm homes)
# lives in bin/kind-state/, never in $HOME.
# Each download is checked against the SHA-256 pinned in deploy/kind/versions.sh AND against the
# checksum file the project publishes; any mismatch stops the script and installs nothing. A tool
# already installed with the pinned checksum is not downloaded again; one with any other checksum (an
# older version, a modified file) is replaced. Nothing is installed outside ./bin, nothing needs root,
# and no cluster is contacted.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../deploy/kind/versions.sh
. "$root/deploy/kind/versions.sh"
bindir="$root/bin"

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
  echo "kind-tools: unsupported platform $(uname -s)-$(uname -m): only linux_amd64 is pinned" >&2
  exit 2
fi
for c in curl sha256sum tar install mktemp; do
  command -v "$c" >/dev/null 2>&1 || { echo "kind-tools: $c is required" >&2; exit 2; }
done

mkdir -p "$bindir"
tmp="$(mktemp -d "$bindir/.kind-tools.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

# fetch URL FILE MAXSECONDS: bounded download, fails on any HTTP error.
fetch() { curl -sSfL --proto '=https' --retry 3 --retry-delay 3 --retry-all-errors --max-time "$3" -o "$2" "$1"; }

# verify NAME FILE PINNED PUBLISHED: the file must equal both the pin and the published checksum.
verify() {
  local got
  got="$(sha_of "$2")"
  if [ "$got" != "$3" ] || [ "$got" != "$4" ]; then
    echo "kind-tools: checksum mismatch for $1: got $got, pinned $3, published ${4:-<empty>}; nothing installed" >&2
    exit 3
  fi
}

# is_installed PATH PINNED: true when the file exists, is executable and has the pinned checksum.
is_installed() { [ -x "$1" ] && [ "$(sha_of "$1")" = "$2" ]; }

# --- kind -------------------------------------------------------------------------------------------
kind_bin="$bindir/kind"
if is_installed "$kind_bin" "$KIND_SHA256"; then
  echo "kind $KIND_VERSION: present ($kind_bin), checksum verified, nothing downloaded"
else
  base="https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION"
  echo "kind $KIND_VERSION: downloading"
  fetch "$base/kind-linux-amd64" "$tmp/kind" 300
  fetch "$base/kind-linux-amd64.sha256sum" "$tmp/kind.sha256sum" 60
  published="$(awk '$2 == "kind-linux-amd64" {print $1}' "$tmp/kind.sha256sum")"
  verify kind "$tmp/kind" "$KIND_SHA256" "$published"
  install -m 0755 "$tmp/kind" "$kind_bin"
  echo "kind $KIND_VERSION: installed $kind_bin (sha256 $KIND_SHA256)"
fi

# --- k3d --------------------------------------------------------------------------------------------
# Only installed here; it creates nothing. make proto-up PROTO_ENGINE=k3d runs it with its kubeconfig and
# HOME under bin/kind-state/proto/ (deploy/kind/proto-lib.sh).
k3d_bin="$bindir/k3d"
if is_installed "$k3d_bin" "$K3D_SHA256"; then
  echo "k3d $K3D_VERSION: present ($k3d_bin), checksum verified, nothing downloaded"
else
  base="https://github.com/k3d-io/k3d/releases/download/$K3D_VERSION"
  echo "k3d $K3D_VERSION: downloading"
  fetch "$base/k3d-linux-amd64" "$tmp/k3d" 300
  fetch "$base/checksums.txt" "$tmp/k3d.checksums" 60
  # The release's checksums.txt names the asset by its build path, _dist/k3d-linux-amd64.
  published="$(awk '$2 == "_dist/k3d-linux-amd64" {print $1}' "$tmp/k3d.checksums")"
  verify k3d "$tmp/k3d" "$K3D_SHA256" "$published"
  install -m 0755 "$tmp/k3d" "$k3d_bin"
  echo "k3d $K3D_VERSION: installed $k3d_bin (sha256 $K3D_SHA256)"
fi

# --- kubectl ----------------------------------------------------------------------------------------
kubectl_bin="$bindir/kubectl"
if is_installed "$kubectl_bin" "$KUBECTL_SHA256"; then
  echo "kubectl $KUBECTL_VERSION: present ($kubectl_bin), checksum verified, nothing downloaded"
else
  base="https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64"
  echo "kubectl $KUBECTL_VERSION: downloading"
  fetch "$base/kubectl" "$tmp/kubectl" 300
  fetch "$base/kubectl.sha256" "$tmp/kubectl.sha256" 60
  published="$(tr -d '[:space:]' < "$tmp/kubectl.sha256")"
  verify kubectl "$tmp/kubectl" "$KUBECTL_SHA256" "$published"
  install -m 0755 "$tmp/kubectl" "$kubectl_bin"
  echo "kubectl $KUBECTL_VERSION: installed $kubectl_bin (sha256 $KUBECTL_SHA256)"
fi

# --- helm -------------------------------------------------------------------------------------------
helm_bin="$bindir/helm"
if is_installed "$helm_bin" "$HELM_BIN_SHA256"; then
  echo "helm $HELM_VERSION: present ($helm_bin), checksum verified, nothing downloaded"
else
  tgz="helm-$HELM_VERSION-linux-amd64.tar.gz"
  echo "helm $HELM_VERSION: downloading"
  fetch "https://get.helm.sh/$tgz" "$tmp/helm.tgz" 300
  fetch "https://get.helm.sh/$tgz.sha256sum" "$tmp/helm.sha256sum" 60
  published="$(awk -v f="$tgz" '$2 == f {print $1}' "$tmp/helm.sha256sum")"
  verify "helm archive" "$tmp/helm.tgz" "$HELM_TGZ_SHA256" "$published"
  mkdir "$tmp/helm"
  tar -xzf "$tmp/helm.tgz" -C "$tmp/helm" linux-amd64/helm
  # The binary is checked against its own pin too, so a re-run can verify it without the archive.
  verify "helm binary" "$tmp/helm/linux-amd64/helm" "$HELM_BIN_SHA256" "$HELM_BIN_SHA256"
  install -m 0755 "$tmp/helm/linux-amd64/helm" "$helm_bin"
  echo "helm $HELM_VERSION: installed $helm_bin (sha256 $HELM_BIN_SHA256)"
fi

# --- crane ------------------------------------------------------------------------------------------
# Copies pinned images into the local registry by digest (deploy/kind/proto-up.sh). Talks only to image
# registries, never to a cluster.
crane_bin="$bindir/crane"
if is_installed "$crane_bin" "$CRANE_BIN_SHA256"; then
  echo "crane $CRANE_VERSION: present ($crane_bin), checksum verified, nothing downloaded"
else
  tgz="go-containerregistry_Linux_x86_64.tar.gz"
  base="https://github.com/google/go-containerregistry/releases/download/$CRANE_VERSION"
  echo "crane $CRANE_VERSION: downloading"
  fetch "$base/$tgz" "$tmp/crane.tgz" 300
  fetch "$base/checksums.txt" "$tmp/crane.checksums" 60
  published="$(awk -v f="$tgz" '$2 == f {print $1}' "$tmp/crane.checksums")"
  verify "crane archive" "$tmp/crane.tgz" "$CRANE_TGZ_SHA256" "$published"
  mkdir "$tmp/crane"
  tar -xzf "$tmp/crane.tgz" -C "$tmp/crane" crane
  verify "crane binary" "$tmp/crane/crane" "$CRANE_BIN_SHA256" "$CRANE_BIN_SHA256"
  install -m 0755 "$tmp/crane/crane" "$crane_bin"
  echo "crane $CRANE_VERSION: installed $crane_bin (sha256 $CRANE_BIN_SHA256)"
fi

# --- docker-buildx ----------------------------------------------------------------------------------
# The Docker CLI plugin that builds the service's runtime image with BuildKit (deploy/image/build.sh).
# It is used only through the project-local DOCKER_CONFIG bin/kind-state/docker-cli, whose
# cli-plugins/docker-buildx links to bin/docker-buildx; nothing is installed in ~/.docker or
# system-wide, and the stack's tenant BuildKit is never used for it.
buildx_bin="$bindir/docker-buildx"
if is_installed "$buildx_bin" "$BUILDX_SHA256"; then
  echo "docker-buildx $BUILDX_VERSION: present ($buildx_bin), checksum verified, nothing downloaded"
else
  asset="buildx-$BUILDX_VERSION.linux-amd64"
  base="https://github.com/docker/buildx/releases/download/$BUILDX_VERSION"
  echo "docker-buildx $BUILDX_VERSION: downloading"
  fetch "$base/$asset" "$tmp/docker-buildx" 300
  fetch "$base/checksums.txt" "$tmp/buildx.checksums" 60
  published="$(awk -v f="*$asset" '$2 == f {print $1}' "$tmp/buildx.checksums")"
  verify docker-buildx "$tmp/docker-buildx" "$BUILDX_SHA256" "$published"
  install -m 0755 "$tmp/docker-buildx" "$buildx_bin"
  echo "docker-buildx $BUILDX_VERSION: installed $buildx_bin (sha256 $BUILDX_SHA256)"
fi
plugins="$bindir/kind-state/docker-cli/cli-plugins"
mkdir -p "$plugins"
chmod 0700 "$bindir/kind-state" "$bindir/kind-state/docker-cli"
ln -sfn ../../../docker-buildx "$plugins/docker-buildx"
