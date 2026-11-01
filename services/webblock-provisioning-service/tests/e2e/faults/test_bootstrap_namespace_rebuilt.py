"""QA-04 live (task W6-L): an admin deletes a project's tenant namespace; the bootstrap component
rebuilds it without a restart, and the next release deploys.

First sequence: v1 live; ``kubectl delete namespace wb-<projectId>``; nothing published. The
bootstrap loop lists a hosted project (its hosting row names a live release), so its recheck
rebuilds the tenant within the bound below; the reconciler (W6-B) then re-applies the workload and
the route, which is not asserted here.

Second sequence (QA's): v1 live; ``kubectl delete namespace wb-<projectId>``; v2 published.

In both, the namespace comes back within ``BOOTSTRAP_RECHECK_ROUNDS`` x ``BOOTSTRAP_INTERVAL``
(10 x 1 s in the prototype) plus one ensure, with the worker's RoleBinding and the catalog Secret
``catalog-runtime`` (the live release's pods went with the namespace, so access_setup delivers a
new password although v1 is still recorded live). In the second, v2 is then built and deployed.
Before the fix the
deploy retried ``tenant_not_ready`` until its budget ended. QA's reproduction is
``tests/qa/test_qa_live_deleted_namespace_is_never_rebuilt.py`` at ``d487207``.

Third sequence (task F2B; H-V finding F-2; decision 0016 section 8 ruling 4): v1 live; the
bootstrap is killed (``kill-process``, so its supervisor does not restart it); the namespace is
deleted; the tenant is rebuilt by the bootstrap identity's own ``KubernetesTenantBootstrap``, run
from the test, which leaves exactly what a bootstrap killed between ``ensure_tenant`` and
access_setup leaves (the namespace marked ``catalog-secret: pending``, the worker's RoleBinding, no
Secret); then the bootstrap starts again. The new process has never seen the namespace, yet it
delivers a new password without an operator: no ``RuntimeSecretMissing``, the mark ends
``delivered``, one rotation is audited, and v2 deploys. Before task F2B that restart left
``runtime_secret_missing``.

The rotation is an audit event with actor ``bootstrap`` (decision 0014 section 24), checked in
Loki through W6-K's ``deploy/kind/proto-loki-query.sh``.

Cluster safety (CLAUDE.md section 0): ``kubectl`` is the project's pinned binary, run only with
``bin/kind-state/proto/kubeconfig`` after checking that its context is
``<engine>-webblock-proto`` (the engine proto-up recorded) on ``https://127.0.0.1:<port>``; its
cache and home stay under ``bin/kind-state/proto``.

Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from typing import Any

import pytest
from tests.e2e._stack import (
    PROTO,
    ROOT,
    Stack,
    new_project,
    new_repository_id,
    run,
    subdomain_of,
    until,
)
from tests.integration.kind_safety import expected_context

from provisioning.core.application.ports.site_cluster import TenantState
from provisioning.infrastructure.kubernetes import BOOTSTRAP_FIELD_MANAGER, connect_kubeconfig
from provisioning.infrastructure.kubernetes.bootstrap import (
    KubernetesTenantBootstrap,
    catalog_secret_mark,
)
from provisioning.infrastructure.kubernetes.render import (
    CATALOG_SECRET_DELIVERED,
    CATALOG_SECRET_PENDING,
    TenantSettings,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

KUBECONFIG = PROTO / "kubeconfig"
KUBECTL = ROOT / "bin" / "kubectl"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(PROTO / "home")}
LOKI_QUERY = ROOT / "deploy" / "kind" / "proto-loki-query.sh"
REBUILD_BOUND = 90.0  # seconds: 10 rounds of 1 s, one ensure and access_setup, and slack
BOOTSTRAP_KUBECONFIG = PROTO / "bootstrap.kubeconfig"
BOOTSTRAP_LOG = PROTO / "provisioning" / "logs" / "latest" / "bootstrap.log"
# The bootstrap's tenant settings in scripts/prototype/run-provisioning.sh (its TENANT_* keys).
TENANT = TenantSettings(
    quota_cpu="3",
    quota_memory="3Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)


def _kubectl_raw(*args: str) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603  # fixed argument list, no shell
        [
            str(KUBECTL),
            "--kubeconfig",
            str(KUBECONFIG),
            "--cache-dir",
            str(PROTO / "kubectl-cache"),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        env=ENV,
        check=False,
    )
    return done.returncode, done.stdout + done.stderr


def _check_context() -> None:
    """The owner's rule: only the project's Kind cluster, only on the loopback address."""
    rc, out = _kubectl_raw("config", "view", "--minify", "-o", "json")
    assert rc == 0, out
    view = json.loads(out)
    assert view["current-context"] == expected_context(), view["current-context"]
    server = view["clusters"][0]["cluster"]["server"]
    assert server.startswith("https://127.0.0.1:"), server


def kubectl(*args: str) -> tuple[int, str]:
    _check_context()
    return _kubectl_raw(*args)


def _exists(kind: str, name: str, namespace: str | None = None) -> bool:
    where = ("-n", namespace) if namespace is not None else ()
    rc, _ = kubectl("get", kind, name, *where, "-o", "name")
    return rc == 0


async def _published(
    stack: Stack, project: uuid.UUID, repository_id: int, name: str, v: str
) -> uuid.UUID:
    sha = await stack.commit(name, "site", v)
    return await stack.release_of(await stack.publish(project, repository_id, sha))


async def _built(stack: Stack, project: uuid.UUID, release: uuid.UUID) -> uuid.UUID:
    body = await stack.wait_release(project, release, {"built", "failed"}, 600)
    assert body["status"] == "built", body
    return release


async def _command(stack: Stack, project: uuid.UUID, release: uuid.UUID, action: str) -> Any:
    cmd = await stack.post(project, release, action, uuid.uuid4().hex)
    assert cmd.status_code == 202, cmd.body
    return await stack.wait_command(project, cmd.command_id, 600)


async def _live_project(stack: Stack, label: str) -> tuple[uuid.UUID, int, str]:
    project, repository_id = new_project(), new_repository_id()
    name = f"w6l-{label}-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    v1 = await _built(stack, project, await _published(stack, project, repository_id, name, "v1"))
    assert (await _command(stack, project, v1, "deploy"))["status"] == "succeeded"
    assert (await _command(stack, project, v1, "promote"))["status"] == "succeeded"
    return project, repository_id, name


def _delete_namespace(project: uuid.UUID) -> None:
    rc, out = kubectl("delete", "namespace", f"wb-{project}", "--wait=true", "--timeout=180s")
    assert rc == 0, out[-1500:]


async def _wait_rebuilt(project: uuid.UUID) -> float:
    """Seconds until the namespace, the worker's RoleBinding and the catalog Secret exist."""
    namespace = f"wb-{project}"
    started = time.monotonic()

    async def rebuilt() -> bool:
        return (
            _exists("namespace", namespace)
            and _exists("rolebinding", "webblock-tenant-worker", namespace)
            and _exists("secret", "catalog-runtime", namespace)
        )

    await until("the tenant to be rebuilt", rebuilt, lambda ok: ok, REBUILD_BOUND, 1)
    return time.monotonic() - started


async def _rotations(project: uuid.UUID) -> list[dict[str, Any]]:
    """The bootstrap's audit events of the project in Loki."""
    query = (
        '{job="webblock-provisioning", process="bootstrap"} | json | audit="true"'
        ' | project_id="' + str(project) + '"'
    )
    out = await run(["bash", str(LOKI_QUERY), "--since", "1h", "--limit", "100", query])
    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def _assert_rotation_audited(project: uuid.UUID) -> None:
    lines = await until("the rotation in Loki", lambda: _rotations(project), bool, 120, 5)
    assert [(e["action"], e["actor"], e["result"]) for e in lines] == [
        ("catalog_credential_rotated", "bootstrap", "succeeded")
    ], lines


async def test_a_deleted_namespace_of_a_live_site_is_rebuilt_without_a_new_publish() -> None:
    stack = Stack()
    project, _, _ = await _live_project(stack, "ns-live")
    _delete_namespace(project)
    took = await _wait_rebuilt(project)
    print(f"tenant rebuilt {took:.0f}s after the namespace was gone")
    await _assert_rotation_audited(project)


async def test_a_deleted_tenant_namespace_is_rebuilt_and_the_next_release_deploys() -> None:
    stack = Stack()
    project, repository_id, name = await _live_project(stack, "ns-qa")
    _delete_namespace(project)
    v2 = await _published(stack, project, repository_id, name, "v2")
    took = await _wait_rebuilt(project)
    print(f"tenant rebuilt {took:.0f}s after v2 was accepted")
    await _assert_rotation_audited(project)

    await _built(stack, project, v2)
    done = await _command(stack, project, v2, "deploy")
    assert done["status"] == "succeeded", done


def _mark(project: uuid.UUID) -> str | None:
    rc, out = kubectl("get", "namespace", f"wb-{project}", "-o", "json")
    assert rc == 0, out[-1500:]
    return catalog_secret_mark(json.loads(out))


async def _ensure_as_the_bootstrap(project: uuid.UUID) -> None:
    """The killed bootstrap's last step: ``ensure_tenant`` with the bootstrap identity, and no
    access_setup after it."""
    _check_context()
    api = await connect_kubeconfig(
        BOOTSTRAP_KUBECONFIG, field_manager=BOOTSTRAP_FIELD_MANAGER, request_timeout_seconds=10.0
    )
    try:
        assert (
            await KubernetesTenantBootstrap(api, TENANT).ensure_tenant(
                project, subdomain=subdomain_of(project)
            )
            is TenantState.READY
        )
    finally:
        await api.close()


def _bootstrap_log_after(offset: int) -> list[str]:
    with BOOTSTRAP_LOG.open("rb") as log:
        log.seek(offset)
        return log.read().decode(errors="replace").splitlines()


async def test_a_bootstrap_killed_between_the_rebuild_and_access_setup_recovers() -> None:
    stack = Stack()
    project, repository_id, name = await _live_project(stack, "ns-kill")
    namespace = f"wb-{project}"
    await stack.process_all("kill-process", "bootstrap")
    try:
        _delete_namespace(project)
        await _ensure_as_the_bootstrap(project)
        assert _mark(project) == CATALOG_SECRET_PENDING
        assert _exists("rolebinding", "webblock-tenant-worker", namespace)
        assert not _exists("secret", "catalog-runtime", namespace)
        offset = BOOTSTRAP_LOG.stat().st_size
    finally:
        await stack.process_all("start-process", "bootstrap")

    took = await _wait_rebuilt(project)
    print(f"catalog Secret delivered {took:.0f}s after the bootstrap started again")
    assert _mark(project) == CATALOG_SECRET_DELIVERED
    await _assert_rotation_audited(project)
    lines = [line for line in _bootstrap_log_after(offset) if str(project) in line]
    assert not [line for line in lines if "RuntimeSecretMissing" in line], lines
    assert [line for line in lines if "catalog_mark=pending->delivered" in line], lines

    v2 = await _published(stack, project, repository_id, name, "v2")
    await _built(stack, project, v2)
    done = await _command(stack, project, v2, "deploy")
    assert done["status"] == "succeeded", done
