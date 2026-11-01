"""The worker passes SITE_PROBE_SUCCESSES and SITE_PROBE_MAX_BODY_BYTES into the cluster adapter's
settings (wave 6 task W6-S; task W6-M makes the probe use them), and the settings refuse values
outside their bounds."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.unit.hosts.envs import worker_env

from provisioning.infrastructure.config.worker import load_worker

if TYPE_CHECKING:
    from provisioning.infrastructure.kubernetes.site_cluster import SiteClusterSettings

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")


def _settings(env: dict[str, str]) -> SiteClusterSettings:
    # Imported here, not at collection: the worker wiring imports kubernetes_asyncio, which fixes
    # its default kubeconfig path when first imported, and tests/unit/infrastructure/kubernetes/
    # conftest.py must poison that path first (its test_the_library_default_is_the_poisoned_path).
    from provisioning.hosts.worker.wiring import site_cluster_settings

    return site_cluster_settings(load_worker(env.get))


def test_the_probe_settings_default_to_one_success_and_64_kib() -> None:
    settings = _settings(worker_env(KUBECONFIG))
    assert (settings.probe_successes, settings.probe_max_body_bytes) == (1, 65536)


def test_the_probe_settings_are_passed_through() -> None:
    env = worker_env(KUBECONFIG) | {
        "SITE_PROBE_SUCCESSES": "3",
        "SITE_PROBE_MAX_BODY_BYTES": "8192",
    }
    settings = _settings(env)
    assert (settings.probe_successes, settings.probe_max_body_bytes) == (3, 8192)


@pytest.mark.parametrize(("successes", "max_body"), [(0, 65536), (11, 65536), (1, 0)])
def test_the_adapter_settings_refuse_values_out_of_bounds(successes: int, max_body: int) -> None:
    settings = _settings(worker_env(KUBECONFIG))
    with pytest.raises(ValueError, match="probe needs 1 to 10 successes"):
        dataclasses.replace(settings, probe_successes=successes, probe_max_body_bytes=max_body)
