# shellcheck shell=bash
# shellcheck disable=SC2034  # sourced pins: the variables are used by the scripts that source this file
# Pinned images and charts of the prototype stack (decision 0014), on top of deploy/kind/versions.sh,
# whose tools, node image and registry image the prototype shares (the Phase 4 stack's Gateway API,
# Cilium and Crossplane pins left with it for branch go-reference; task T1 replaced them here).
# Sourced by deploy/kind/proto-lib.sh; it runs nothing. Each digest is the index digest the
# registry served for the tag beside it (crane digest; 8 October 2026, and 9 October 2026 for task
# T1's pins). The 14-day version-age rule is suspended for the prototype (0014 section 5); every pin
# here was at least three weeks old when chosen anyway.

# Jenkins LTS controller (owner answer 3). 2.568.3-lts-jdk21, created 2 September 2026.
PROTO_JENKINS_BASE="docker.io/jenkins/jenkins:2.568.3-lts-jdk21@sha256:c1e4c349365f6d16d88595b2c5f7e8ff39b8ae1d061f62420bac193b4b9616d0"

# BuildKit daemon (and buildctl, copied from the same image into the Jenkins image). v0.33.1, created
# 30 September 2026.
PROTO_BUILDKIT_IMAGE="docker.io/moby/buildkit:v0.33.1@sha256:cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea"

# Unprivileged nginx (listens on 8080, runs as UID 101 or any UID): the offline page in the cluster,
# the Git HTTP server, and the base image of the sample site. 1.31.5-alpine, created 14 September 2026.
PROTO_NGINX_IMAGE="docker.io/nginxinc/nginx-unprivileged:1.31.5-alpine@sha256:19c132c9ab02d3b783f478743dafc7a7f42e27aa7d2bdcbec1bb1128ca8f2a07"

# Gateway API v1.6.1, standard channel (task T1, decision 0014 section 16). The standard channel
# carries ListenerSet (gateway.networking.k8s.io/v1, Standard since v1.5.0) and TLSRoute v1.
# Cilium 1.20 requires v1.6.1 at a minimum ("Cilium's Gateway API support now requires Gateway API
# v1.6.1 at a minimum to work correctly", Documentation/operations/upgrade-current.inc of v1.20.2;
# its go.mod pins sigs.k8s.io/gateway-api v1.6.1), so v1.5.x is not an option with Cilium 1.20.
# v1.6.1 was published 16 July 2026; the SHA-256 is the asset digest GitHub reports for
# standard-install.yaml in that release. Installed in Kind only; on DOKS the platform
# manages these CRDs (OD-67).
PROTO_GATEWAY_API_VERSION="v1.6.1"
PROTO_GATEWAY_API_URL="https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.1/standard-install.yaml"
PROTO_GATEWAY_API_SHA256="24d931f22abd8e40c973264319ead7cfa09d0fb7716b7ab1ee2ff174cb063a73"

# Cilium 1.20.2 (published 16 September 2026), the first minor with Standard ListenerSet support
# (Documentation/network/servicemesh/gateway-api/listenerset.rst). Chart digest from
# https://helm.cilium.io/index.yaml; the chart pins every image by digest (image.useDigest: true).
PROTO_CILIUM_VERSION="1.20.2"
PROTO_CILIUM_CHART_URL="https://helm.cilium.io/cilium-1.20.2.tgz"
PROTO_CILIUM_CHART_SHA256="b2afd87b7f75f875f92a14559f14f59b7babbb479d968e3fd625a20bf30ec20e"

# cert-manager v1.21.2 (chart published 11 September 2026; supports Kubernetes 1.33-1.36), with
# its Gateway API shim off since task W6-K (decision 0014 section 23). Chart digest from
# https://charts.jetstack.io/index.yaml. Image digests: crane digest of each quay.io/jetstack image at
# tag v1.21.2 on 9 October 2026. deploy/prototype/cert-manager/values.yaml repeats the digests; proto-up
# renders the chart and refuses to install unless its images are exactly these, from the local registry.
PROTO_CERT_MANAGER_VERSION="v1.21.2"
PROTO_CERT_MANAGER_CHART_URL="https://charts.jetstack.io/charts/cert-manager-v1.21.2.tgz"
PROTO_CERT_MANAGER_CHART_SHA256="73a56e1728edd6c99f1f31082618c3259d279a76b7ebd3d4bdc5475c2442d34a"

# Images proto-up copies into the local registry by digest (host/path:tag@sha256:...): the images the
# Cilium 1.20.2 chart renders with deploy/kind/values/cilium.yaml (proto-up renders the chart and
# refuses to install when the lists differ), cert-manager's controller, webhook and cainjector (its
# startupapicheck job is disabled and the ACME solver is never started: the only issuer is the local
# CA), and nginx for the offline page and as BuildKit's docker.io mirror for the sample site's base
# image.
PROTO_MIRROR_IMAGES=(
  "quay.io/cilium/cilium:v1.20.2@sha256:2939231d0d3e3ebddcd80fffa168b7ddcc78fdf0dc864d1c8c126ff523c54f01"
  "quay.io/cilium/operator-generic:v1.20.2@sha256:64d8798350e8569b8e7622563fed6e44dce2625f311e4651b774816516c744fc"
  "quay.io/cilium/cilium-envoy:v1.37.6-1789133542-cbec91f666af0bf742da986d43832932dbb26b82@sha256:af7382699576b9e65e9184efa52eeca0b58aea70ad6e511bf260c91d9f740463"
  "quay.io/jetstack/cert-manager-controller:v1.21.2@sha256:70f532fd9cfde0b09d55687200942399d89838bc2d5d5b45152eb799a15912b8"
  "quay.io/jetstack/cert-manager-webhook:v1.21.2@sha256:a60e2dac46dbb8a7f3df95c54ce941012f54c2fe022f0ee55aaa1ab40ed957ae"
  "quay.io/jetstack/cert-manager-cainjector:v1.21.2@sha256:c85268c64f2e0e76684bf5fe8906caff34b82523561c6affe0fae3546bd87562"
  "$PROTO_NGINX_IMAGE"
)

# Names and addresses. The registry is a separate container from the Phase 4 stack's
# webblock-kind-registry (branch go-reference), so neither stack's down script removes the other's. The Jenkins port is published on 127.0.0.1 only.
PROTO_CLUSTER="webblock-proto"
PROTO_REGISTRY_NAME="webblock-proto-registry"
PROTO_JENKINS_NAME="webblock-jenkins"
PROTO_JENKINS_PORT="8090"
PROTO_JENKINS_IMAGE_TAG="webblock-proto-jenkins:local"
PROTO_BUILDKIT_NAME="webblock-buildkit"
PROTO_GIT_NAME="webblock-git"
PROTO_JOB="webblock-site-build"

# Loki and its log shipper (decision 0014 section 23 item 5; task W6-K), pulled from Docker Hub by digest
# (crane digest on 9 October 2026; "created" is the image config's date for linux/amd64).
# grafana/loki 3.7.8, created 17 September 2026: single binary, filesystem storage.
PROTO_LOKI_IMAGE="docker.io/grafana/loki:3.7.8@sha256:1107dd5274e0ada47e42472b7a7e71f3b2a2fe878878108f3e2f9e51528f0193"
# fluent/fluent-bit 5.1.3, created 30 September 2026: tails the Provisioning processes' JSON log files.
PROTO_LOG_SHIPPER_IMAGE="docker.io/fluent/fluent-bit:5.1.3@sha256:c5542543523c9678398dd78d927c05e8425ec15b038f226b67b3b019b1a70845"
PROTO_LOKI_NAME="webblock-loki"
PROTO_LOKI_PORT="3100"
PROTO_LOKI_MEMORY="384m"
PROTO_LOG_SHIPPER_NAME="webblock-log-shipper"
PROTO_LOG_SHIPPER_MEMORY="96m"
