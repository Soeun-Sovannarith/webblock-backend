"""hosts/shared/cluster.connect_cluster: the worker and the bootstrap connect by the configured mode
(decision 0016 section 9 ruling 2). Both paths end in the one client wrapper, whose own checks
refuse here before any request. The host and client modules are imported inside the tests,
not at collection: test_client.py compares the library's default kubeconfig path with its own
folder's poison, which only holds if that folder's conftest ran before the first import of
kubernetes_asyncio. (Safety does not depend on it: tests/conftest.py already poisons KUBECONFIG.)"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from provisioning.infrastructure.config.common import (
    ClusterSettings,
    InClusterConnection,
    KubeconfigConnection,
)

TIMEOUT = timedelta(seconds=5)


@pytest.mark.asyncio
async def test_the_kubeconfig_mode_goes_through_the_kubeconfig_checks(tmp_path: Path) -> None:
    from provisioning.hosts.shared.cluster import connect_cluster
    from provisioning.infrastructure.kubernetes import WORKER_FIELD_MANAGER, KubeconfigRefused

    settings = ClusterSettings(KubeconfigConnection(tmp_path / "absent"), TIMEOUT)
    with pytest.raises(KubeconfigRefused, match="the kubeconfig must be"):
        await connect_cluster(settings, field_manager=WORKER_FIELD_MANAGER)


@pytest.mark.asyncio
async def test_the_in_cluster_mode_goes_through_the_pod_checks(tmp_path: Path) -> None:
    from provisioning.hosts.shared.cluster import connect_cluster
    from provisioning.infrastructure.kubernetes import WORKER_FIELD_MANAGER, KubeconfigRefused

    connection = InClusterConnection(
        tmp_path / "token",
        tmp_path / "ca.crt",
        "system:serviceaccount:webblock:provisioning-worker",
    )
    with pytest.raises(KubeconfigRefused, match="the token and CA files"):
        await connect_cluster(
            ClusterSettings(connection, TIMEOUT), field_manager=WORKER_FIELD_MANAGER
        )
