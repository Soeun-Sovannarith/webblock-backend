"""ruff's banned-api list (TID251) fires on the banned imports under src/provisioning/, and the one
exempt path is the future Kubernetes client wrapper (decision 0013 section 9; review-stack.md H2,
review-port-arch.md M6).

Each snippet goes to ``ruff check`` on standard input with ``--stdin-filename`` set to a path under
src/provisioning/, so ruff applies the project configuration and its per-file rules exactly as it
would to a file at that path, and nothing is written into the tree.
"""

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

type EnvFactory = Callable[[Mapping[str, str] | None], dict[str, str]]

PLANTED = "src/provisioning/infrastructure/planted.py"
WRAPPER = "src/provisioning/infrastructure/kubernetes/_client.py"

KUBERNETES_LOADERS = [
    "from kubernetes_asyncio.config import load_kube_config\n",
    "from kubernetes_asyncio.config import load_config\n",
    "from kubernetes_asyncio.config import new_client_from_config\n",
    "from kubernetes_asyncio.config import list_kube_config_contexts\n",
    "from kubernetes_asyncio.config import load_incluster_config\n",
    "from kubernetes_asyncio.config.kube_config import KUBE_CONFIG_DEFAULT_LOCATION\n",
    "import kubernetes_asyncio.config\n",
    "from kubernetes_asyncio import config\n\nconfig.load_kube_config()\n",
    "from kubernetes.config import load_kube_config\n",
    "from kubernetes.config import new_client_from_config\n",
    "from kubernetes import config\n\nconfig.load_config()\n",
    "from kubernetes.aio.config import load_kube_config\n",
    "from kubernetes.aio.config import new_client_from_config\n",
]

OTHER_BANS = [
    "import httpx\n",
    "from httpx import AsyncClient\n",
    "import requests\n",
    "import lightkube\n",
    "from lightkube import AsyncClient\n",
    "import psycopg2\n",
    "from jwt import PyJWKClient\n",
    "import jwt\n\njwt.PyJWKClient('https://example.invalid/jwks')\n",
    "import importlib\n\nimportlib.import_module('provisioning')\n",
    "from importlib import import_module\n",
]


def ruff_check(
    venv_bin: Path, repo_root: Path, make_env: EnvFactory, path: str, source: str
) -> list[dict[str, Any]]:
    """Run ruff check with the project configuration on source as if it were the file at path."""
    result = subprocess.run(
        [
            str(venv_bin / "ruff"),
            "check",
            "--no-cache",
            "--output-format=json",
            "--stdin-filename",
            path,
            "-",
        ],
        cwd=repo_root,
        env=make_env(None),
        input=source,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    findings: list[dict[str, Any]] = json.loads(result.stdout)
    return findings


def codes(findings: list[dict[str, Any]]) -> set[str]:
    return {str(f["code"]) for f in findings}


@pytest.mark.parametrize("source", KUBERNETES_LOADERS + OTHER_BANS)
def test_banned_api_fires_under_src_provisioning(
    venv_bin: Path, repo_root: Path, make_env: EnvFactory, source: str
) -> None:
    findings = ruff_check(venv_bin, repo_root, make_env, PLANTED, source)
    assert "TID251" in codes(findings), findings


@pytest.mark.parametrize("source", KUBERNETES_LOADERS)
def test_the_wrapper_path_is_exempt_from_the_loader_ban(
    venv_bin: Path, repo_root: Path, make_env: EnvFactory, source: str
) -> None:
    findings = ruff_check(venv_bin, repo_root, make_env, WRAPPER, source)
    assert "TID251" not in codes(findings), findings


def test_the_exemption_covers_only_the_banned_api_rule(
    venv_bin: Path, repo_root: Path, make_env: EnvFactory
) -> None:
    source = "import os\nfrom kubernetes_asyncio.config import load_kube_config\n"
    findings = ruff_check(venv_bin, repo_root, make_env, WRAPPER, source)
    assert "F401" in codes(findings), findings
    assert "TID251" not in codes(findings), findings


def test_the_wrapper_is_the_only_banned_api_exemption(pyproject: dict[str, Any]) -> None:
    ignores: dict[str, list[str]] = pyproject["tool"]["ruff"]["lint"]["per-file-ignores"]
    exempt = sorted(path for path, rules in ignores.items() if {"TID", "TID251"} & set(rules))
    assert exempt == [WRAPPER]


def test_a_permitted_import_is_not_flagged(
    venv_bin: Path, repo_root: Path, make_env: EnvFactory
) -> None:
    source = '"""Fine."""\n\nimport asyncio\n\n__all__ = ["asyncio"]\n'
    findings = ruff_check(venv_bin, repo_root, make_env, PLANTED, source)
    assert findings == []
