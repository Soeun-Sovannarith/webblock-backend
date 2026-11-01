"""Live tests of the bootstrap admission policy ``webblock-bootstrap-scope`` on the prototype stack
(decision 0014 section 17 item 3; review finding M3). Marked ``kind`` by directory; a skip is a
failure. Run with ``make proto-test-kind`` or ``pytest -m kind tests/integration/policy`` after
``make proto-up``.

Two identities make each request:
- ``token``: the bootstrap ServiceAccount's own token
  (``bin/kind-state/proto/bootstrap.kubeconfig``), so RBAC and the policy both apply, as for the
  real bootstrap process;
- ``policy-alone``: Kind's admin impersonating that ServiceAccount's username with the group
  ``system:masters``, which RBAC lets do anything, so a refusal can come from the policy only.

Every refused request is a server-side dry run (nothing is stored even when it is admitted, as it is
before the policy is installed). Cluster safety (CLAUDE.md section 0): kubectl gets
``--kubeconfig``, ``--context`` and a project-local ``--cache-dir`` on every call, runs with a
poisoned ``HOME`` and ``KUBECONFIG``, and each kubeconfig is checked first to name the context
``<engine>-webblock-proto`` on ``https://127.0.0.1:<port>``.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from tests.integration.policy._kube import Kubectl, policy_subdomain

from provisioning.infrastructure.kubernetes.render import render_namespace, tenant_namespace


@pytest.fixture(scope="session")
def kubectl(tmp_path_factory: pytest.TempPathFactory) -> Kubectl:
    home = tmp_path_factory.mktemp("poisoned-home")
    (home / ".kube").mkdir()
    (home / ".kube" / "config").write_text("this file must never be read\n")
    return Kubectl(home)


@pytest.fixture(autouse=True)
def poisoned_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """For the Python client in the adapter test: no default kubeconfig can be found or written."""
    home = tmp_path / "poisoned-home"
    (home / ".kube").mkdir(parents=True)
    (home / ".kube" / "config").write_text("this file must never be read\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KUBECONFIG", str(home / ".kube" / "config"))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def tenant(kubectl: Kubectl) -> Iterator[UUID]:
    """A tenant namespace created by Kind's admin (whom the policy does not match), for the
    requests that need an existing ``wb-<uuid>`` namespace. Deleted afterwards."""
    project = uuid4()
    kubectl.admin_ok(
        "apply",
        "--server-side",
        "--field-manager=webblock-policy-tests",
        "-f",
        "-",
        stdin=json.dumps(
            render_namespace(project, subdomain=policy_subdomain(project), catalog_secret=None)
        ),
    )
    try:
        yield project
    finally:
        kubectl.admin_ok("delete", "namespace", tenant_namespace(project), "--wait=false")
