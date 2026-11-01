"""Every test process points KUBECONFIG at a path that never exists (tests/conftest.py; the
Kubernetes test folders set their own such path), so a Kubernetes client library can never fall
back to the owner's ``~/.kube/config`` (CLAUDE.md section 0).
"""

import os
import sys
from pathlib import Path


def _is_poisoned(value: str) -> bool:
    return bool(value) and ".kube" not in Path(value).parts and not Path(value).exists()


def test_kubeconfig_is_poisoned_for_every_test() -> None:
    assert _is_poisoned(os.environ.get("KUBECONFIG", ""))


def test_the_kubernetes_library_default_is_a_poisoned_path() -> None:
    # Read through sys.modules: importing the config module directly is banned outside the wrapper.
    module = sys.modules.get("kubernetes_asyncio.config.kube_config")
    if module is not None:
        assert _is_poisoned(str(module.KUBE_CONFIG_DEFAULT_LOCATION))
