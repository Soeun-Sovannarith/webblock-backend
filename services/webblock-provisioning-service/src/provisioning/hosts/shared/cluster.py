"""How the worker and the bootstrap connect to Kubernetes (decision 0016 section 9 ruling 2): from
the host with the project-local kubeconfig, or from a pod with the projected ServiceAccount token,
whose username a SelfSubjectReview checks. Their two composition roots are the only callers; the
API and the build worker never import this module (import-linter contract ``hosts-no-kubernetes``).
"""

from __future__ import annotations

from typing import assert_never

from provisioning.infrastructure.config.common import (
    ClusterSettings,
    InClusterConnection,
    KubeconfigConnection,
)
from provisioning.infrastructure.kubernetes import KubeApi, connect_in_cluster, connect_kubeconfig


async def connect_cluster(settings: ClusterSettings, *, field_manager: str) -> KubeApi:
    """A client for ``settings``; ``KubeconfigRefused`` when the kubeconfig, the pod's files or
    the token's identity is not the one this process may use."""
    timeout = settings.request_timeout.total_seconds()
    match settings.connection:
        case KubeconfigConnection(path=path):
            return await connect_kubeconfig(
                path, field_manager=field_manager, request_timeout_seconds=timeout
            )
        case InClusterConnection(token_file=token, ca_file=ca, expected_username=username):
            return await connect_in_cluster(
                token_file=token,
                ca_file=ca,
                expected_username=username,
                field_manager=field_manager,
                request_timeout_seconds=timeout,
            )
        case _:
            assert_never(settings.connection)
