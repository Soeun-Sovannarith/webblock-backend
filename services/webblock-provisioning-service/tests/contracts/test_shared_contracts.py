"""The pinned copy of web-block-contracts (decision 0022 E3): no drift, generated code, one SHA."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_contracts_check_reports_no_drift() -> None:
    result = subprocess.run(
        ["make", "-s", "contracts-check"],  # noqa: S607 (make from PATH, fixed arguments)
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", ["access_pb2", "repository_pb2"])
def test_project_messages_are_generated(name: str) -> None:
    assert (
        ROOT / "src/provisioning/infrastructure/grpc/_gen/webblock/project/v1" / f"{name}.py"
    ).is_file()


def test_lock_holds_one_full_sha() -> None:
    sha = (ROOT / "contracts.lock").read_text().strip()
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
