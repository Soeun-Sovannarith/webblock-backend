"""Task H4: only ``provisioning.infrastructure.telemetry`` and the hosts import
``prometheus_client`` and ``sentry_sdk``. The contracts are run with import-linter on a temporary
copy of the package with a planted import, so they cannot pass vacuously."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def _contract_names() -> dict[str, str]:
    with (ROOT / "pyproject.toml").open("rb") as f:
        contracts = tomllib.load(f)["tool"]["importlinter"]["contracts"]
    return {c["id"]: c["name"] for c in contracts}


def _lint(root: Path) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/nonexistent"),
        "LANG": "C.UTF-8",
        "COLUMNS": "200",
        "NO_COLOR": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(root / "src"),
    }
    return subprocess.run(  # noqa: S603  # fixed argument list: the venv's lint-imports
        [
            str(Path(sys.executable).parent / "lint-imports"),
            "--no-cache",
            "--config",
            str(root / "pyproject.toml"),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize(
    ("planted", "source", "contract_ids"),
    [
        (
            "core/application/planted.py",
            "import prometheus_client\n",
            ("observability-libraries-only-in-telemetry", "core-no-frameworks"),
        ),
        (
            "core/domain/planted.py",
            "import sentry_sdk\n",
            ("observability-libraries-only-in-telemetry", "core-no-frameworks"),
        ),
        (
            "presentation/messaging/consumers/planted.py",
            "import prometheus_client\n",
            ("observability-libraries-only-in-telemetry",),
        ),
        (
            "presentation/http/planted.py",
            "import sentry_sdk\n",
            ("observability-libraries-only-in-telemetry",),
        ),
        (
            "infrastructure/postgres/planted.py",
            "from prometheus_client import Counter\n",
            ("observability-libraries-only-in-telemetry",),
        ),
    ],
)
def test_an_observability_import_outside_telemetry_breaks_the_contract(
    tmp_path: Path, planted: str, source: str, contract_ids: tuple[str, ...]
) -> None:
    shutil.copytree(
        ROOT / "src" / "provisioning",
        tmp_path / "src" / "provisioning",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "src" / "provisioning" / planted).write_text('"""Planted."""\n\n' + source)

    result = _lint(tmp_path)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    names = _contract_names()
    for contract_id in contract_ids:
        assert f"{names[contract_id]} BROKEN" in output, output
