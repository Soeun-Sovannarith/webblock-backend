"""QA-04: the bootstrap host passes ``BOOTSTRAP_RECHECK_ROUNDS`` to the bootstrap loop (moved out of
the loop's tests, tests/unit/core/application/tenants/background/test_recheck.py, by decision
0025 FL8: this one tests the host)."""

from pathlib import Path

from tests.unit.hosts.envs import bootstrap_env

from provisioning.core.application.tenants.background.bootstrap_loop import DEFAULT_RECHECK_ROUNDS


def test_the_host_passes_bootstrap_recheck_rounds_to_the_loop(tmp_path: Path) -> None:
    # Imported here: the host module imports the Kubernetes client, which the kubernetes test
    # folder's conftest poisons first when the whole suite is collected.
    from provisioning.hosts.bootstrap.__main__ import loop_settings
    from provisioning.infrastructure.config.bootstrap import load_bootstrap

    env = bootstrap_env(tmp_path / "kubeconfig") | {"BOOTSTRAP_RECHECK_ROUNDS": "7"}
    assert loop_settings(load_bootstrap(env.get)).recheck_rounds == 7
    default = bootstrap_env(tmp_path / "kubeconfig")
    assert loop_settings(load_bootstrap(default.get)).recheck_rounds == DEFAULT_RECHECK_ROUNDS
