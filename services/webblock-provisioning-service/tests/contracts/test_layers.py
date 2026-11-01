"""The import-linter contracts hold on the tree and break on planted violations.

As review-port-arch.md H2 asks, a contract
that passes on the tree must also be shown to fail when its rule is broken, so it cannot pass
vacuously (for example because the graph was built from another copy of the package).
"""

import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

type EnvFactory = Callable[[Mapping[str, str] | None], dict[str, str]]


def lint_imports(
    venv_bin: Path, root: Path, make_env: EnvFactory
) -> subprocess.CompletedProcess[str]:
    """Run lint-imports with root/pyproject.toml on the package under root/src."""
    return subprocess.run(
        [str(venv_bin / "lint-imports"), "--no-cache", "--config", str(root / "pyproject.toml")],
        cwd=root,
        env=make_env({"PYTHONPATH": str(root / "src")}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def contract_names(pyproject: dict[str, Any]) -> list[str]:
    return [c["name"] for c in pyproject["tool"]["importlinter"]["contracts"]]


def test_every_contract_is_kept_on_the_tree(
    repo_root: Path, venv_bin: Path, make_env: EnvFactory, pyproject: dict[str, Any]
) -> None:
    result = lint_imports(venv_bin, repo_root, make_env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    names = contract_names(pyproject)
    assert len(names) >= 4
    for name in names:
        assert f"{name} KEPT" in output, output


@pytest.mark.parametrize(
    ("planted", "source", "contract_id"),
    [
        (
            "core/domain/planted.py",
            "import provisioning.infrastructure\n",
            "layers",
        ),
        (
            "core/application/planted.py",
            "import provisioning.presentation\n",
            "layers",
        ),
        (
            "infrastructure/planted.py",
            "import provisioning.presentation.messaging.consumers\n",
            "layers",
        ),
        (
            "hosts/api/planted.py",
            "import kubernetes_asyncio\n",
            "hosts-no-kubernetes",
        ),
        (
            "hosts/build_worker/planted.py",
            "import kubernetes.client\n",
            "hosts-no-kubernetes",
        ),
        (
            "hosts/api/planted.py",
            "import provisioning.infrastructure.kubernetes.site_cluster\n",
            "hosts-no-kubernetes",
        ),
        (
            "hosts/worker/planted.py",
            "import provisioning.infrastructure.kubernetes.bootstrap\n",
            "worker-no-bootstrap",
        ),
        (
            "hosts/worker/planted.py",
            "import provisioning.core.application.tenants.background.bootstrap_loop\n",
            "worker-no-bootstrap",
        ),
        (
            "hosts/bootstrap/planted.py",
            "import provisioning.core.application.builds.background.hints\n",
            "bootstrap-reads-and-creates-tenants-only",
        ),
        (
            "hosts/bootstrap/planted.py",
            "import provisioning.infrastructure.rabbitmq\n",
            "bootstrap-reads-and-creates-tenants-only",
        ),
        (
            "hosts/bootstrap/planted.py",
            "import provisioning.infrastructure.kubernetes.site_cluster\n",
            "bootstrap-reads-and-creates-tenants-only",
        ),
        (
            "hosts/bootstrap/planted.py",
            "import provisioning.core.application.jobs.background.dispatcher\n",
            "bootstrap-reads-and-creates-tenants-only",
        ),
        (
            "core/application/planted.py",
            "import sqlalchemy\n",
            "core-no-frameworks",
        ),
        (
            "core/domain/planted.py",
            "import structlog\n",
            "core-no-frameworks",
        ),
        (
            "core/application/shared/planted.py",
            "import provisioning.core.application.operations.registry\n",
            "application-shared-and-ports-are-leaves",
        ),
        (
            "core/application/ports/planted.py",
            "import provisioning.core.application.jobs.claims\n",
            "application-shared-and-ports-are-leaves",
        ),
        (
            "hosts/api/planted.py",
            "import provisioning.hosts.devtools.simulate\n",
            "devtools-stand-apart",
        ),
        (
            "hosts/devtools/planted.py",
            "import prometheus_client\n",
            "devtools-no-observability-libraries",
        ),
    ],
)
def test_a_planted_violation_breaks_its_contract(
    package_copy: Path,
    venv_bin: Path,
    make_env: EnvFactory,
    pyproject: dict[str, Any],
    planted: str,
    source: str,
    contract_id: str,
) -> None:
    target = package_copy / "src" / "provisioning" / planted
    target.write_text('"""Planted by test_layers.py."""\n\n' + source)

    result = lint_imports(venv_bin, package_copy, make_env)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    names = {c["id"]: c["name"] for c in pyproject["tool"]["importlinter"]["contracts"]}
    assert f"{names[contract_id]} BROKEN" in output, output
    planted_module = "provisioning." + planted.removesuffix(".py").replace("/", ".")
    assert planted_module in output, output
