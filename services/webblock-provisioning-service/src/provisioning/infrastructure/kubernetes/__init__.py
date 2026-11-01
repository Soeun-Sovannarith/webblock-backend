"""Kubernetes adapters: the release worker's ``SiteCluster`` (``site_cluster``), the bootstrap
component's ``TenantBootstrap`` (``bootstrap``), the manifests they apply (``render``), and the one
client wrapper they share (``_client``, re-exported here). Decisions 0013 sections 3 and 9, 0014.

The worker and the bootstrap component each build their own ``KubeApi`` from their own kubeconfig
or ServiceAccount; the worker never constructs ``KubernetesTenantBootstrap``.
"""

from provisioning.infrastructure.kubernetes._client import (
    BOOTSTRAP_FIELD_MANAGER,
    WORKER_FIELD_MANAGER,
    JsonObject,
    KubeApi,
    KubeconfigRefused,
    KubernetesConflict,
    KubernetesForbidden,
    KubernetesNotFound,
    Resource,
    connect_in_cluster,
    connect_kubeconfig,
)

__all__ = [
    "BOOTSTRAP_FIELD_MANAGER",
    "WORKER_FIELD_MANAGER",
    "JsonObject",
    "KubeApi",
    "KubeconfigRefused",
    "KubernetesConflict",
    "KubernetesForbidden",
    "KubernetesNotFound",
    "Resource",
    "connect_in_cluster",
    "connect_kubeconfig",
]
