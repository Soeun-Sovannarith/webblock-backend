# shellcheck shell=bash
# shellcheck disable=SC2034  # sourced pins: the variables are used by the scripts that source this file
# Pinned tools, node image and registry image of the local Kind stack (decision 0009 sections 1, 2 and
# 5), shared by the prototype stack (deploy/prototype/versions.sh holds its own Gateway API, Cilium,
# cert-manager and image pins). Sourced by scripts/kind-tools.sh and deploy/kind/lib.sh; it runs
# nothing. Every value was read on 7 October 2026 from the source named beside it. Only linux_amd64 is
# pinned. Change a version and its checksum together, never one alone. The Phase 4 stack's Gateway API
# v1.4.1, Cilium 1.19.8 and Crossplane 2.4.2 pins left with that stack; they are on branch
# go-reference (decision 0014 section 18).

# Tools installed by scripts/kind-tools.sh as bin/kind, bin/kubectl and bin/helm; the checksum check
# (not the file name) is what ties each one to its version.
KIND_VERSION="v0.33.0"
# kind-linux-amd64.sha256sum on https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0
KIND_SHA256="aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d"

KUBECTL_VERSION="v1.36.5"
# https://dl.k8s.io/release/v1.36.5/bin/linux/amd64/kubectl.sha256
KUBECTL_SHA256="33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e"

HELM_VERSION="v3.22.0"
# https://get.helm.sh/helm-v3.22.0-linux-amd64.tar.gz.sha256sum (the archive)
HELM_TGZ_SHA256="1e4ab49e429626cf6c6958d914248b78c9730803c2751b87627e171dc800e7bb"
# linux-amd64/helm inside that verified archive (lets a second run skip the download)
HELM_BIN_SHA256="7a319dee5ea7a1f7d7a0d92d5c223586f3b65abc33163c9de2a85f083aa3df2b"

# crane (go-containerregistry), used to copy pinned images into the local registry by digest
# (deploy/kind/proto-up.sh): unlike docker pull/push it copies a multi-platform index as it is, so the
# index digest the chart pins is the digest the local registry serves.
CRANE_VERSION="v0.22.1"
# go-containerregistry_Linux_x86_64.tar.gz in checksums.txt of release v0.22.1 (published 4 September 2026)
CRANE_TGZ_SHA256="0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0"
# crane inside that verified archive (lets a second run skip the download)
CRANE_BIN_SHA256="8e75c326adb43b053cfd78b9b987663f8f4fe4557f4d3915905643f25c730992"

# k3d (k3s in Docker), the second local engine of the prototype stack (spike design
# .claude/docs/designs/k3s-site-cluster-spike.md G2; make proto-up PROTO_ENGINE=k3d). Installed by
# scripts/kind-tools.sh as bin/k3d. v5.9.0, published 2026-06-02T14:41:12Z (GitHub release), the newest
# release. The SHA-256 of k3d-linux-amd64 as the release's checksums.txt lists it (as _dist/k3d-linux-amd64)
# and as GitHub reports the asset's digest.
K3D_VERSION="v5.9.0"
K3D_SHA256="06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0"
# The k3s node image of the k3d engine (G2): k3s v1.36.5+k3s1 (Kubernetes 1.36, the Kind node's minor),
# GitHub release published 2026-09-30T16:10:03Z, image created 2026-09-30. Index digest. It runs k3s's
# packaged Traefik 3.7.13 (chart traefik-40.1.5+up40.1.0) and Gateway API v1.6.1 standard CRDs (chart
# traefik-crd-40.1.5+up40.1.0), as k3s's manifests/traefik.yaml at that tag names them.
K3S_NODE_DIGEST="sha256:057cfd5a9d51b89f1f0ee139c28d5d5fdd5aa7ddeac2742a0ba90c6dbc0329bb"
K3S_NODE_IMAGE="docker.io/rancher/k3s:v1.36.5-k3s1@$K3S_NODE_DIGEST"
# k3d's helper images for v5.9.0 (created 2026-06-02), index digests, passed as K3D_IMAGE_TOOLS and
# K3D_IMAGE_LOADBALANCER (deploy/kind/proto-lib.sh k3d_) so k3d never pulls them by tag alone.
K3D_TOOLS_IMAGE="ghcr.io/k3d-io/k3d-tools:5.9.0@sha256:c57abce697e66feeb826922718738fe186b47779312d49d4e51e6fb9b8a6f34d"
K3D_PROXY_IMAGE="ghcr.io/k3d-io/k3d-proxy:5.9.0@sha256:7be40abfeb6ac9a81d0d4157a7cbf3aacc68a64a1780ca2efb160f1aea915177"

# Kubernetes 1.36 node image shipped with kind v0.33.0 (release notes). Also written in cluster.yaml.
NODE_IMAGE="kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed"

# Local registry (kind local-registry pattern), index digest of docker.io/library/registry:3. It serves
# TLS with a project-local CA (lib.sh ensure_registry_tls) and is reached from the nodes and pods by
# a Docker network alias with a dot (<name>.test:5000). REGISTRY_NAME is lib.sh's default; the
# prototype names its own container (PROTO_REGISTRY_NAME in deploy/prototype/versions.sh).
REGISTRY_IMAGE="docker.io/library/registry:3@sha256:ddf754342cfc8acc51a56d5d0ab6af06826461864460636d8bd5c546dab2a7b8"
REGISTRY_NAME="webblock-kind-registry"
REGISTRY_PORT="5001"

# docker-buildx, the Docker CLI plugin that builds the service's runtime image with BuildKit (decision
# 0016 section 9 ruling 5; deploy/image/build.sh). Installed by scripts/kind-tools.sh as bin/docker-buildx
# and used only through the project-local DOCKER_CONFIG bin/kind-state/docker-cli. v0.37.2, published
# 2026-09-30T17:55:10Z, the newest release at least 14 days old on 15 October 2026 (v0.38.0 was published
# 2026-10-07). The SHA-256 of buildx-v0.37.2.linux-amd64 as the release's checksums.txt lists it and as
# GitHub reports the asset's digest (both read 15 October 2026).
BUILDX_VERSION="v0.37.2"
BUILDX_SHA256="982ca20490b45ed1ec8d99795974d3d874a358f75938c9c237305010e6b7e548"
