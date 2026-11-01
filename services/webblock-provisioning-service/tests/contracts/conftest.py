"""Fixtures shared by the gate self-tests in tests/contracts.

Each self-test runs the real tool in a subprocess, on the tree or on a temporary copy of it, with an
environment built here from scratch, so nothing from the caller's shell (PYTHONPATH, VIRTUAL_ENV,
KUBECONFIG, credentials) reaches the tool.
"""

import os
import shutil
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def clean_env(extra: Mapping[str, str] | None) -> dict[str, str]:
    """A minimal environment for a tool subprocess: PATH, HOME and a wide terminal only."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/nonexistent"),
        "LANG": "C.UTF-8",
        "COLUMNS": "200",
        "NO_COLOR": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if extra:
        env.update(extra)
    return env


type EnvFactory = Callable[[Mapping[str, str] | None], dict[str, str]]


@pytest.fixture(scope="session")
def make_env() -> EnvFactory:
    """clean_env as a fixture: test modules cannot import from a conftest."""
    return clean_env


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def venv_bin() -> Path:
    """The bin directory of the interpreter running the tests (the project's .venv under make)."""
    return Path(sys.executable).parent


@pytest.fixture(scope="session")
def pyproject() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


@pytest.fixture
def package_copy(tmp_path: Path) -> Path:
    """A temporary copy of src/provisioning and pyproject.toml; returns the copy's root."""
    shutil.copytree(
        REPO_ROOT / "src" / "provisioning",
        tmp_path / "src" / "provisioning",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(REPO_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path
