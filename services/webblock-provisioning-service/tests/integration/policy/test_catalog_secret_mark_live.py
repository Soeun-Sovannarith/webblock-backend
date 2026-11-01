"""The catalog-secret mark of a tenant namespace on the running prototype stack (task F2B; H-V
finding F-2; decision 0016 section 8 ruling 4, amending decision 0014 section 24).

The annotation ``websites.webblock.internal/catalog-secret`` says whether a tenant namespace ever
received its catalog Secret. ``webblock-bootstrap-scope`` lets the bootstrap set it to ``pending``
only when it creates the namespace, and move it from ``pending`` or none to ``delivered``, never
back; ``webblock-tenant-writer`` lets no other identity set, change or remove it, nor the
sub-domain label ``websites.webblock.internal/subdomain`` (Task 12 security review S6).

Identities (``_kube.py``): ``policy-alone`` (Kind's admin as the bootstrap's username with
``system:masters``: RBAC allows everything and ``webblock-tenant-writer`` does not match the
group, so only ``webblock-bootstrap-scope`` can refuse) and ``token`` (the bootstrap's own token).
For ``webblock-tenant-writer``: the worker's username and an unrelated user, each in a group bound
to ``cluster-admin`` for this module, so RBAC allows them and only the policy can refuse. Every
refused or admitted request is a server-side dry run; the namespaces are written by Kind's admin,
whom neither policy matches, except in the last test, where the real ``KubernetesTenantBootstrap``
writes for real. Cluster safety: see ``conftest.py``.
"""

import json
import subprocess
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from tests.integration.policy._kube import (
    ADMIN,
    BOOTSTRAP_KUBECONFIG,
    POLICY,
    POLICY_ALONE,
    TOKEN,
    Identity,
    Kubectl,
    policy_subdomain,
)

from provisioning.core.application.ports.site_cluster import ClusterRefused, TenantState
from provisioning.infrastructure.kubernetes import (
    BOOTSTRAP_FIELD_MANAGER,
    Resource,
    connect_kubeconfig,
)
from provisioning.infrastructure.kubernetes.bootstrap import (
    KubernetesTenantBootstrap,
    catalog_secret_mark,
)
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_CATALOG_SECRET,
    CATALOG_SECRET_DELIVERED,
    CATALOG_SECRET_PENDING,
    LABEL_SUBDOMAIN,
    OFFLINE_NAMESPACE,
    TenantSettings,
    render_namespace,
    tenant_namespace,
)

WRITER = "webblock-tenant-writer"
WORKER_USER = "system:serviceaccount:webblock:provisioning-worker"
OTHER_USER = "webblock-policy-test-intruder"
INTRUDERS = "webblock-policy-test:mark-intruders"
MARK_MESSAGE = "catalog-secret mark"
SETTINGS = TenantSettings(
    quota_cpu="2",
    quota_memory="2Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)
SECRET_VALUES = {
    "PGHOST": "webblock-catalog-db",
    "PGPORT": "5432",
    "PGDATABASE": "catalog",
    "PGUSER": "site_policy_test",
    "PGPASSWORD": "placeholder-never-used-to-connect",
    "PGSSLMODE": "disable",
}


def _marked(project: UUID, mark: str | None) -> dict[str, Any]:
    return render_namespace(project, subdomain=policy_subdomain(project), catalog_secret=mark)


def _admin_namespace(kubectl: Kubectl, mark: str | None) -> UUID:
    project = uuid4()
    kubectl.admin_ok(
        "apply",
        "--server-side",
        "--field-manager=webblock-policy-tests",
        "-f",
        "-",
        stdin=json.dumps(_marked(project, mark)),
    )
    return project


@pytest.fixture(scope="module")
def marked(kubectl: Kubectl) -> Iterator[dict[str, UUID]]:
    """Three tenant namespaces written by Kind's admin: marked pending, delivered, and none."""
    projects = {
        "pending": _admin_namespace(kubectl, CATALOG_SECRET_PENDING),
        "delivered": _admin_namespace(kubectl, CATALOG_SECRET_DELIVERED),
        "none": _admin_namespace(kubectl, None),
    }
    try:
        yield projects
    finally:
        for project in projects.values():
            kubectl.admin_ok("delete", "namespace", tenant_namespace(project), "--wait=false")


@pytest.fixture(scope="module")
def intruders(kubectl: Kubectl) -> Iterator[str]:
    """A group bound to cluster-admin for this module, not a break-glass group: RBAC allows its
    members everything, so only an admission policy can refuse them."""
    name = "webblock-policy-test-mark-intruders"
    binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": name},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "cluster-admin",
        },
        "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": INTRUDERS}],
    }
    kubectl.admin_ok(
        "apply",
        "--server-side",
        "--field-manager=webblock-policy-tests",
        "-f",
        "-",
        stdin=json.dumps(binding),
    )
    try:
        yield INTRUDERS
    finally:
        kubectl.admin_ok("delete", "clusterrolebinding", name, "--ignore-not-found")


def _annotate(
    kubectl: Kubectl, who: Identity, project: UUID, value: str | None
) -> subprocess.CompletedProcess[str]:
    """An UPDATE of only the mark (``None`` removes it), as a server-side dry run."""
    change = (
        f"{ANNOTATION_CATALOG_SECRET}-" if value is None else f"{ANNOTATION_CATALOG_SECRET}={value}"
    )
    return kubectl.run(
        who,
        "annotate",
        "namespace",
        tenant_namespace(project),
        change,
        "--overwrite",
        "--dry-run=server",
    )


def _as(user: str, group: str) -> Identity:
    return Identity(user, ADMIN.kubeconfig, ("--as", user, "--as-group", group))


def _refused_by(done: subprocess.CompletedProcess[str], policy: str) -> None:
    assert done.returncode != 0, f"admitted: {done.stdout.strip()}"
    assert f"ValidatingAdmissionPolicy '{policy}'" in done.stderr, done.stderr
    assert "forbidden" in done.stderr.lower(), done.stderr
    assert MARK_MESSAGE in done.stderr, done.stderr


def _admitted(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 0, done.stderr


# --- webblock-bootstrap-scope: the bootstrap's own moves -------------------------------------


@pytest.mark.parametrize("value", [CATALOG_SECRET_DELIVERED, "something-else", ""])
def test_the_bootstrap_cannot_create_a_namespace_marked_other_than_pending(
    kubectl: Kubectl, value: str
) -> None:
    done = kubectl.dry_run_apply(POLICY_ALONE, _marked(uuid4(), value))
    _refused_by(done, POLICY)


@pytest.mark.parametrize(
    ("start", "value"),
    [
        ("delivered", CATALOG_SECRET_PENDING),  # never back
        ("delivered", None),  # never removed
        ("pending", None),
        ("pending", "something-else"),
        ("none", CATALOG_SECRET_PENDING),  # an existing namespace is never "created from nothing"
        ("none", "something-else"),
    ],
)
@pytest.mark.parametrize("who", [POLICY_ALONE, TOKEN], ids=["policy-alone", "token"])
def test_the_bootstrap_cannot_move_the_mark_any_other_way(
    kubectl: Kubectl, marked: dict[str, UUID], who: Identity, start: str, value: str | None
) -> None:
    _refused_by(_annotate(kubectl, who, marked[start], value), POLICY)


def test_the_bootstrap_may_create_a_namespace_pending(kubectl: Kubectl) -> None:
    _admitted(kubectl.dry_run_apply(TOKEN, _marked(uuid4(), CATALOG_SECRET_PENDING)))


@pytest.mark.parametrize("start", ["pending", "none"])
@pytest.mark.parametrize("who", [POLICY_ALONE, TOKEN], ids=["policy-alone", "token"])
def test_the_bootstrap_may_mark_delivered(
    kubectl: Kubectl, marked: dict[str, UUID], who: Identity, start: str
) -> None:
    _admitted(_annotate(kubectl, who, marked[start], CATALOG_SECRET_DELIVERED))


@pytest.mark.parametrize("start", ["pending", "delivered", "none"])
def test_the_bootstrap_may_reapply_a_namespace_with_its_mark_unchanged(
    kubectl: Kubectl, marked: dict[str, UUID], start: str
) -> None:
    """What every recheck of ``ensure_tenant`` sends."""
    project = marked[start]
    mark = {"pending": CATALOG_SECRET_PENDING, "delivered": CATALOG_SECRET_DELIVERED}.get(start)
    _admitted(kubectl.dry_run_apply(TOKEN, _marked(project, mark)))


# --- webblock-tenant-writer: nobody else touches it ------------------------------------------


@pytest.mark.parametrize(
    ("start", "value"),
    [
        ("pending", CATALOG_SECRET_DELIVERED),  # the bootstrap's own move, by anyone else
        ("pending", None),
        ("delivered", CATALOG_SECRET_PENDING),
        ("delivered", None),
        ("none", CATALOG_SECRET_PENDING),
        ("none", CATALOG_SECRET_DELIVERED),
    ],
)
@pytest.mark.parametrize("user", [WORKER_USER, OTHER_USER], ids=["worker", "unrelated"])
def test_no_other_identity_changes_the_mark(
    kubectl: Kubectl,
    marked: dict[str, UUID],
    intruders: str,
    user: str,
    start: str,
    value: str | None,
) -> None:
    _refused_by(_annotate(kubectl, _as(user, intruders), marked[start], value), WRITER)


@pytest.mark.parametrize("user", [WORKER_USER, OTHER_USER], ids=["worker", "unrelated"])
def test_no_other_identity_creates_a_marked_tenant_namespace(
    kubectl: Kubectl, intruders: str, user: str
) -> None:
    done = kubectl.run(
        _as(user, intruders),
        "apply",
        "--server-side",
        "--dry-run=server",
        "--field-manager=webblock-policy-tests",
        "-f",
        "-",
        stdin=json.dumps(_marked(uuid4(), CATALOG_SECRET_PENDING)),
    )
    _refused_by(done, WRITER)


# --- webblock-tenant-writer: the sub-domain label (Task 12 security review S6) ----------------
# Written for Task 9's Kind slot; not run in the fix round.

LABEL_MESSAGE = "sub-domain label"


def _relabel(
    kubectl: Kubectl, who: Identity, project: UUID, value: str | None
) -> subprocess.CompletedProcess[str]:
    """An UPDATE of only the sub-domain label (``None`` removes it), as a server-side dry run."""
    change = f"{LABEL_SUBDOMAIN}-" if value is None else f"{LABEL_SUBDOMAIN}={value}"
    return kubectl.run(
        who,
        "label",
        "namespace",
        tenant_namespace(project),
        change,
        "--overwrite",
        "--dry-run=server",
    )


@pytest.fixture(scope="module")
def unlabelled(kubectl: Kubectl) -> Iterator[UUID]:
    """A tenant namespace without the sub-domain label, written by Kind's admin."""
    project = uuid4()
    kubectl.admin_ok(
        "apply",
        "--server-side",
        "--field-manager=webblock-policy-tests",
        "-f",
        "-",
        stdin=json.dumps(render_namespace(project, subdomain=None, catalog_secret=None)),
    )
    try:
        yield project
    finally:
        kubectl.admin_ok("delete", "namespace", tenant_namespace(project), "--wait=false")


@pytest.mark.parametrize("value", ["another-shop", None], ids=["change", "remove"])
@pytest.mark.parametrize("user", [WORKER_USER, OTHER_USER], ids=["worker", "unrelated"])
def test_s6_no_other_identity_changes_or_removes_the_subdomain_label(
    kubectl: Kubectl, marked: dict[str, UUID], intruders: str, user: str, value: str | None
) -> None:
    done = _relabel(kubectl, _as(user, intruders), marked["none"], value)
    assert done.returncode != 0, f"admitted: {done.stdout.strip()}"
    assert f"ValidatingAdmissionPolicy '{WRITER}'" in done.stderr, done.stderr
    assert LABEL_MESSAGE in done.stderr, done.stderr


@pytest.mark.parametrize("user", [WORKER_USER, OTHER_USER], ids=["worker", "unrelated"])
def test_s6_no_other_identity_adds_the_subdomain_label(
    kubectl: Kubectl, unlabelled: UUID, intruders: str, user: str
) -> None:
    done = _relabel(kubectl, _as(user, intruders), unlabelled, "taken-shop")
    assert done.returncode != 0, f"admitted: {done.stdout.strip()}"
    assert f"ValidatingAdmissionPolicy '{WRITER}'" in done.stderr, done.stderr
    assert LABEL_MESSAGE in done.stderr, done.stderr


# --- the real adapter ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_real_bootstrap_marks_pending_then_delivered_and_never_back(
    kubectl: Kubectl,
) -> None:
    project = uuid4()
    namespace = tenant_namespace(project)
    api = await connect_kubeconfig(
        BOOTSTRAP_KUBECONFIG, field_manager=BOOTSTRAP_FIELD_MANAGER, request_timeout_seconds=10.0
    )
    try:
        bootstrap = KubernetesTenantBootstrap(api, SETTINGS)
        assert (
            await bootstrap.ensure_tenant(project, subdomain=policy_subdomain(project))
            is TenantState.READY
        )
        assert _live_mark(kubectl, namespace) == CATALOG_SECRET_PENDING
        assert await bootstrap.catalog_secret_pending(project)

        await bootstrap.write_runtime_secret(project, SECRET_VALUES)
        assert _live_mark(kubectl, namespace) == CATALOG_SECRET_DELIVERED
        assert not await bootstrap.catalog_secret_pending(project)

        # A recheck keeps delivered; a stale writer that sends pending is refused.
        assert (
            await bootstrap.ensure_tenant(project, subdomain=policy_subdomain(project))
            is TenantState.READY
        )
        assert _live_mark(kubectl, namespace) == CATALOG_SECRET_DELIVERED
        with pytest.raises(ClusterRefused):
            await api.apply(Resource.NAMESPACE, _marked(project, CATALOG_SECRET_PENDING))
        assert _live_mark(kubectl, namespace) == CATALOG_SECRET_DELIVERED
    finally:
        await api.close()
        kubectl.admin_ok(
            "delete", "referencegrant", namespace, "-n", OFFLINE_NAMESPACE, "--ignore-not-found"
        )
        kubectl.admin_ok("delete", "namespace", namespace, "--ignore-not-found", "--wait=false")


def _live_mark(kubectl: Kubectl, namespace: str) -> str | None:
    shown = json.loads(kubectl.admin_ok("get", "namespace", namespace, "-o", "json"))
    return catalog_secret_mark(shown)
