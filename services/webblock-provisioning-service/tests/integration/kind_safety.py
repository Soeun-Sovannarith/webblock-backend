"""The safety check the live policy and stack tests run before every kubectl call (CLAUDE.md
section 0): the pinned kubectl, the prototype stack's context on https://127.0.0.1:<port>, and an
environment whose ``HOME`` is the test's and whose ``KUBECONFIG`` names no real file.

The context is ``<engine>-webblock-proto``, where the engine (``kind`` or ``k3d``) is the one
``make proto-up`` recorded in ``bin/kind-state/proto/engine`` (TR-34; spike design
``k3s-site-cluster-spike`` G3). ``expected_context`` is the one place the harness learns it; the
e2e and Kind suites call it instead of a literal."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KUBECTL = REPO / "bin" / "kubectl"
ENGINE_FILE = REPO / "bin" / "kind-state" / "proto" / "engine"
ENGINES = ("kind", "k3d")
CLUSTER = "webblock-proto"
POISON = "/nonexistent/webblock-tests-never-read-a-default-kubeconfig"
KUBECTL_TIMEOUT = 60.0
_LOCAL_SERVER = re.compile(r"https://127\.0\.0\.1:[0-9]{1,5}")


def engine(path: Path = ENGINE_FILE) -> str:
    """The local engine proto-up recorded: ``kind`` or ``k3d``. A stack without the record was made
    by Kind before the record existed. Any other content stops the test."""
    if not path.exists():
        return "kind"
    found = path.read_text().strip()
    if found not in ENGINES:
        pytest.fail(f"{path}: the engine {found!r} is neither kind nor k3d; refusing")
    return found


def expected_context(path: Path = ENGINE_FILE) -> str:
    """The only context the live tests accept: ``kind-webblock-proto`` or ``k3d-webblock-proto``."""
    return f"{engine(path)}-{CLUSTER}"


def kube_env(home: Path) -> dict[str, str]:
    """The environment of every kubectl call: a test ``HOME`` and a poisoned ``KUBECONFIG``."""
    return {"HOME": str(home), "KUBECONFIG": POISON, "PATH": os.environ.get("PATH", "/usr/bin")}


def check_local(kubeconfig: Path, home: Path) -> None:
    """Refuse any kubeconfig that is not the prototype stack's (reads only the file)."""
    if not kubeconfig.is_file():
        pytest.fail(f"{kubeconfig} is missing: run make proto-up first")
    if not KUBECTL.is_file():
        pytest.fail(f"{KUBECTL} is missing: run make kind-tools first")
    shown = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [str(KUBECTL), "--kubeconfig", str(kubeconfig), "config", "view", "--minify", "-o", "json"],
        capture_output=True,
        text=True,
        timeout=KUBECTL_TIMEOUT,
        env=kube_env(home),
        check=False,
    )
    if shown.returncode != 0:
        pytest.fail(f"{kubeconfig.name}: kubectl config view failed")
    config = json.loads(shown.stdout)
    servers = [c["cluster"]["server"] for c in config.get("clusters", [])]
    context = expected_context()
    if config.get("current-context") != context or len(servers) != 1:
        pytest.fail(f"{kubeconfig.name}: the current context is not {context}; refusing")
    if not _LOCAL_SERVER.fullmatch(servers[0]):
        pytest.fail(f"{kubeconfig.name}: the server is not https://127.0.0.1:<port>; refusing")
