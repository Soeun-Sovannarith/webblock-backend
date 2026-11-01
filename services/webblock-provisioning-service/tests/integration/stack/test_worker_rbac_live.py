"""The worker's RBAC on the prototype stack (tasks T1 and W6-K; decision 0014 section 21 ruling 8
and section 23). In a tenant namespace the worker writes its Deployments, Services and HTTPRoutes
and may delete the Deployments and Services of releases it cleans up, never an HTTPRoute (ruling
K9). It may list namespaces cluster-wide, for the reconciler, and nothing else outside tenant
namespaces. It has no access to Secrets, cannot create namespaces or RoleBindings, and has no grant
on ListenerSets or Certificates: the platform provides no custom domains (section 23). Checked with
``kubectl auth can-i`` by Kind's admin impersonating the worker, and with real requests by the
worker's own token."""

import pytest
from tests.integration.stack._stack import (
    GATEWAY_NAMESPACE,
    WORKER,
    Kubectl,
    Tenant,
)

DEPLOYMENTS = "deployments.apps"
HTTPROUTES = "httproutes.gateway.networking.k8s.io"

ALLOWED = [
    *[
        (verb, resource)
        for resource in (DEPLOYMENTS, "services")
        for verb in ("get", "list", "watch", "create", "patch", "delete")
    ],
    *[(verb, HTTPROUTES) for verb in ("get", "list", "watch", "create", "patch")],
    ("get", "endpointslices.discovery.k8s.io"),
    ("list", "pods"),
]
REFUSED = [
    ("delete", HTTPROUTES),
    ("deletecollection", DEPLOYMENTS),
    ("deletecollection", "services"),
    ("update", DEPLOYMENTS),
    *[
        (verb, resource)
        for resource in (
            "listenersets.gateway.networking.k8s.io",
            "certificates.cert-manager.io",
        )
        for verb in ("get", "list", "create", "patch", "delete")
    ],
    ("get", "secrets"),
    ("list", "secrets"),
    ("create", "secrets"),
    ("create", "rolebindings.rbac.authorization.k8s.io"),
    ("patch", "rolebindings.rbac.authorization.k8s.io"),
    ("create", "pods"),
]


@pytest.mark.parametrize(("verb", "resource"), ALLOWED)
def test_worker_may_in_a_tenant_namespace(
    kubectl: Kubectl, tenant: Tenant, verb: str, resource: str
) -> None:
    assert kubectl.can_i(verb, resource, tenant.namespace)


@pytest.mark.parametrize(("verb", "resource"), REFUSED)
def test_worker_may_not_in_a_tenant_namespace(
    kubectl: Kubectl, tenant: Tenant, verb: str, resource: str
) -> None:
    assert not kubectl.can_i(verb, resource, tenant.namespace)


def test_worker_may_list_namespaces_cluster_wide(kubectl: Kubectl) -> None:
    assert kubectl.can_i("list", "namespaces", None)


@pytest.mark.parametrize(
    ("verb", "resource"),
    [
        ("create", "namespaces"),
        ("patch", "namespaces"),
        ("delete", "namespaces"),
        ("watch", "namespaces"),
        ("create", "rolebindings.rbac.authorization.k8s.io"),
        ("create", "clusterrolebindings.rbac.authorization.k8s.io"),
        ("list", "secrets"),
        ("get", "secrets"),
    ],
)
def test_worker_may_not_cluster_wide(kubectl: Kubectl, verb: str, resource: str) -> None:
    assert not kubectl.can_i(verb, resource, None)


@pytest.mark.parametrize("resource", [DEPLOYMENTS, "services", HTTPROUTES])
@pytest.mark.parametrize("namespace", [GATEWAY_NAMESPACE, "webblock", "default"])
def test_worker_has_nothing_outside_tenant_namespaces(
    kubectl: Kubectl, resource: str, namespace: str
) -> None:
    for verb in ("get", "create", "patch", "delete"):
        assert not kubectl.can_i(verb, resource, namespace), (verb, resource, namespace)


def test_worker_token_lists_namespaces_and_reads_no_secret(
    kubectl: Kubectl, tenant: Tenant
) -> None:
    """Real requests with the worker's token: the namespace list works; a Secret read, a namespace
    create and a RoleBinding create are Forbidden (server-side dry runs, nothing is stored)."""
    listed = kubectl.ok(WORKER, "get", "namespaces", "-o", "name")
    assert f"namespace/{tenant.namespace}" in listed.split()
    for args in (
        ("-n", tenant.namespace, "get", "secret", "catalog-runtime"),
        ("create", "namespace", "wb-worker-may-not", "--dry-run=server"),
        (
            "-n",
            tenant.namespace,
            "create",
            "rolebinding",
            "worker-may-not",
            "--clusterrole=webblock-tenant-worker",
            "--serviceaccount=webblock:provisioning-worker",
            "--dry-run=server",
        ),
    ):
        done = kubectl.run(WORKER, *args)
        assert done.returncode != 0, f"the worker could {args}"
        assert "forbidden" in done.stderr.lower(), done.stderr
