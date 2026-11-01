"""Helpers of the live policy tests: the identities and a kubectl runner that checks each
kubeconfig first (``kind_safety``) and always passes ``--kubeconfig``, ``--context`` and a
project-local ``--cache-dir``, with a poisoned ``HOME`` and ``KUBECONFIG`` (CLAUDE.md section 0).
See ``conftest.py``."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.integration.kind_safety import (
    KUBECTL,
    KUBECTL_TIMEOUT,
    check_local,
    expected_context,
    kube_env,
)

REPO = Path(__file__).resolve().parents[3]
STATE = REPO / "bin" / "kind-state" / "proto"
ADMIN_KUBECONFIG = STATE / "kubeconfig"
BOOTSTRAP_KUBECONFIG = STATE / "bootstrap.kubeconfig"
CACHE_DIR = STATE / "kube-cache-policy-tests"
BOOTSTRAP_USER = "system:serviceaccount:webblock:tenant-bootstrap"
POLICY = "webblock-bootstrap-scope"


@dataclass(frozen=True, slots=True)
class Identity:
    name: str
    kubeconfig: Path
    extra: tuple[str, ...]


TOKEN = Identity("token", BOOTSTRAP_KUBECONFIG, ())
POLICY_ALONE = Identity(
    "policy-alone",
    ADMIN_KUBECONFIG,
    ("--as", BOOTSTRAP_USER, "--as-group", "system:masters"),
)
ADMIN = Identity("admin", ADMIN_KUBECONFIG, ())


@dataclass(frozen=True, slots=True)
class Kubectl:
    home: Path

    def run(
        self, who: Identity, *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        check_local(who.kubeconfig, self.home)
        return subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [
                str(KUBECTL),
                "--kubeconfig",
                str(who.kubeconfig),
                "--context",
                expected_context(),
                "--cache-dir",
                str(CACHE_DIR),
                "--request-timeout=30s",
                *who.extra,
                *args,
            ],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT,
            env=kube_env(self.home),
            check=False,
        )

    def dry_run_apply(
        self, who: Identity, manifest: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        """Server-side apply, as bootstrap writes, under its field manager; nothing is stored."""
        return self.run(
            who,
            "apply",
            "--server-side",
            "--dry-run=server",
            "--field-manager=webblock-tenant-bootstrap",
            "-f",
            "-",
            stdin=json.dumps(manifest),
        )

    def admin_ok(self, *args: str, stdin: str | None = None) -> str:
        done = self.run(ADMIN, *args, stdin=stdin)
        assert done.returncode == 0, done.stderr
        return done.stdout


def policy_subdomain(project: UUID) -> str:
    """A test tenant's sub-domain label (decision 0023 G18a): one DNS label per project."""
    return f"policy-{project.hex}"
