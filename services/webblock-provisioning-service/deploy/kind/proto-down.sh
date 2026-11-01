#!/usr/bin/env bash
# Removes the prototype stack (make proto-down): first Provisioning itself (scripts/prototype/run-provisioning.sh
# stop, in either mode), then the containers webblock-jenkins, webblock-buildkit,
# webblock-git, the catalog database webblock-catalog-db, Loki webblock-loki and the log shipper
# webblock-log-shipper with their anonymous volumes, the cluster webblock-proto of the recorded engine
# (Kind, or k3d: spike design k3s-site-cluster-spike G2 and G5), the registry
# container webblock-proto-registry, the Docker network kind when no container is left on it, and the
# stack's credentials and per-run state in bin/kind-state/proto/ (kubeconfigs, jenkins.env, Jenkins
# passwords, BuildKit certificates, Git repositories, catalog-admin.env, stack.env, Loki's data and the
# shipper's read positions), and last the engine record. Safe to run when nothing exists.
# Kept, as caches: the registry's storage and certificate (registry-data/, registry-tls/), verified
# downloads (cache/), the local CA of ClusterIssuer webblock-local-ca (local-ca/, local-ca.crt; task T1),
# the locally built image webblock-proto-jenkins:local, and the Provisioning processes' log files
# (provisioning/logs/, which make proto-stop keeps too). Verifies at the end that no container of the
# stack is left.
. "$(dirname "${BASH_SOURCE[0]}")/proto-lib.sh"

case "$ENGINE" in
  kind) [ -x "$KIND" ] || die "$KIND is missing: run make kind-tools" ;;
  k3d) [ -x "$K3D" ] || die "$K3D is missing: run make kind-tools" ;;
esac

# Task PK: Provisioning goes first. Its PostgreSQL and RabbitMQ containers sit on the kind network (both
# modes) and would keep it alive, and in cluster mode its release and objects live in the cluster. The run
# script's stop handles either mode and is safe when nothing runs.
bash "$kind_root/scripts/prototype/run-provisioning.sh" stop || log "run-provisioning.sh stop failed; continuing"

for c in "$PROTO_JENKINS_NAME" "$PROTO_BUILDKIT_NAME" "$PROTO_GIT_NAME" "$PROTO_CATALOG_NAME" "$PROTO_LOG_SHIPPER_NAME" "$PROTO_LOKI_NAME"; do
  if docker container inspect "$c" >/dev/null 2>&1; then
    log "container: removing $c and its volumes"
    docker rm -f -v "$c" >/dev/null
  fi
done

if cluster_exists; then
  log "cluster: deleting $ENGINE cluster $CLUSTER"
  case "$ENGINE" in
    kind) timeout 300 "$KIND" delete cluster --name "$CLUSTER" --kubeconfig "$KCFG" ;;
    # k3d removes the cluster's entries from the kubeconfig KUBECONFIG names: the project-local one.
    k3d)
      timeout 300 env HOME="$TOOLHOME" KUBECONFIG="$KCFG" K3D_IMAGE_TOOLS="$K3D_TOOLS_IMAGE" \
        K3D_IMAGE_LOADBALANCER="$K3D_PROXY_IMAGE" "$K3D" cluster delete "$CLUSTER"
      ;;
  esac
else
  log "cluster: $CLUSTER does not exist"
fi

if docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
  log "registry: removing $REGISTRY_NAME and its volume"
  docker rm -f -v "$REGISTRY_NAME" >/dev/null
fi

if docker network inspect kind >/dev/null 2>&1; then
  left="$(docker network inspect kind --format '{{len .Containers}}')"
  if [ "$left" = 0 ]; then
    log "network: removing the empty Docker network kind"
    docker network rm kind >/dev/null
  else
    log "network: kind still has $left container(s) that are not the prototype's; kept"
  fi
fi

rm -f "$KCFG" "$KCFG_WORKER" "$KCFG_BOOTSTRAP" "$JENKINS_ENV" "$JENKINS_ENV.tmp" "$STACK_ENV" "$PROTO/buildkitd.toml" \
  "$PROTO/jenkins-image.log" "$PROTO/mirror.log" "$CATALOG_ENV" "$PROTO/catalog-goose.log" "$PROTO/catalog-flyway.log" \
  "$PROTO/k3d-registries.yaml"
rm -rf "$JENKINS_SECRETS" "$BUILDKIT_TLS" "$GIT_ROOT" "$PROTO/jenkins-image" "$KUBECACHE" "$KUBECACHE-worker" \
  "$KUBECACHE-bootstrap" "$KUBECACHE-whoami" "$PROTO/smoke" "$PROTO/loki-data" "$PROTO/log-shipper"
rm -rf "$PROTO"/.kubeconfig.* "$PROTO"/.jenkins-* "$PROTO"/.sample-work.* "$PROTO"/.bktls.* "$PROTO"/.catalog-docker-env.* \
  "$PROTO"/.ca-probe.* "$PROTO"/.test-kind.* "$KUBECACHE"-stack-tests-*
log "state: removed the kubeconfigs, jenkins.env, catalog-admin.env, Jenkins passwords, BuildKit certificates and Git repositories"

case "$ENGINE" in
  kind) left="$(docker ps -a --filter "label=io.x-k8s.kind.cluster=$CLUSTER" -q)" ;;
  k3d) left="$(docker ps -a --filter "label=k3d.cluster=$CLUSTER" -q)" ;;
esac
[ -z "$left" ] || die "containers of cluster $CLUSTER are still present: $left"
for c in "$PROTO_JENKINS_NAME" "$PROTO_BUILDKIT_NAME" "$PROTO_GIT_NAME" "$PROTO_CATALOG_NAME" "$PROTO_LOG_SHIPPER_NAME" "$PROTO_LOKI_NAME" "$REGISTRY_NAME"; do
  ! docker container inspect "$c" >/dev/null 2>&1 || die "container $c is still present"
done
rm -f "$ENGINE_FILE"
log "proto-down: done; no container of $ENGINE cluster $CLUSTER, the registry, Jenkins, BuildKit, the Git server, the catalog or Loki remains"
