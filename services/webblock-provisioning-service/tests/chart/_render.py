"""Renders deploy/helm/provisioning offline with the pinned bin/helm (task PK). No cluster is ever
contacted: helm runs with HOME, its three homes and --kubeconfig inside a folder of the test run,
where no kubeconfig exists (CLAUDE.md section 0). This module holds no test."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only

ROOT = Path(__file__).resolve().parents[2]
HELM = ROOT / "bin" / "helm"
CHART = ROOT / "deploy" / "helm" / "provisioning"
VALUES = Path(__file__).resolve().parent / "values-kind.json"

type Doc = dict[str, Any]


def helm_template(
    home: Path, *values: Path, sets: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    never = home / "never-a-kubeconfig"
    command = [str(HELM), "template", "provisioning", str(CHART), "--namespace", "webblock",
               "--kubeconfig", str(never)]  # fmt: skip
    for path in values:
        command += ["--values", str(path)]
    for item in sets:
        command += ["--set", item]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "KUBECONFIG": str(never),
        "HELM_CONFIG_HOME": str(home / "config"),
        "HELM_CACHE_HOME": str(home / "cache"),
        "HELM_DATA_HOME": str(home / "data"),
    }
    return subprocess.run(  # noqa: S603 - fixed argv: the pinned helm and paths of this repository
        command, capture_output=True, text=True, timeout=60, env=env, check=False
    )


def documents(text: str) -> list[Doc]:
    return [doc for doc in yaml.safe_load_all(text) if doc]
