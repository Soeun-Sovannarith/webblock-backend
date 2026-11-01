"""``webblock-bootstrap-scope`` on the running prototype stack (decision 0014 section 17 item 3).

Refused: every out-of-scope write the review found possible (finding M3) and the shapes the brief
lists, each by the bootstrap token (RBAC and policy) and by the policy alone. Admitted: the exact
objects the bootstrap renderer writes, and the real ``KubernetesTenantBootstrap`` for a fresh
project, whose second run only verifies. ``conftest.py`` and ``_kube.py`` hold the identities and
the safety checks.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from tests.integration.policy._kube import (
    BOOTSTRAP_KUBECONFIG,
    POLICY,
    POLICY_ALONE,
    TOKEN,
    Identity,
    Kubectl,
    policy_subdomain,
)

from provisioning.core.application.ports.site_cluster import TenantState
from provisioning.infrastructure.kubernetes import (
    BOOTSTRAP_FIELD_MANAGER,
    connect_kubeconfig,
)
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    LIMITS_NAME,
    OFFLINE_NAMESPACE,
    QUOTA_NAME,
    RUNTIME_SECRET_NAME,
    WORKER_CLUSTER_ROLE,
    TenantSettings,
    render_limit_range,
    render_namespace,
    render_reference_grant,
    render_resource_quota,
    render_runtime_secret,
    render_worker_role_binding,
    tenant_namespace,
)

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


@dataclass(frozen=True, slots=True)
class Request:
    """Either a server-side apply of ``manifest`` or the kubectl ``args`` (each a dry run)."""

    manifest: Callable[[UUID], dict[str, Any]] | None = None
    args: Callable[[UUID], list[str]] = field(default=lambda _: [])


def _in(namespace: str, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["metadata"]["namespace"] = namespace
    return manifest


def _renamed(name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["metadata"]["name"] = name
    return manifest


def _labelled(key: str, value: str, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["metadata"]["labels"][key] = value
    return manifest


def _secret(project: UUID) -> dict[str, Any]:
    return render_runtime_secret(project, SECRET_VALUES)


def _token_secret(project: UUID) -> dict[str, Any]:
    secret = _secret(project)
    secret["type"] = "kubernetes.io/service-account-token"
    secret["metadata"]["annotations"] = {"kubernetes.io/service-account.name": "default"}
    return secret


def _binding_to(role: str, project: UUID) -> dict[str, Any]:
    binding = render_worker_role_binding(project)
    binding["roleRef"]["name"] = role
    return binding


def _binding_with_extra_subject(project: UUID) -> dict[str, Any]:
    binding = render_worker_role_binding(project)
    binding["subjects"].append(
        {"kind": "ServiceAccount", "name": "default", "namespace": tenant_namespace(project)}
    )
    return binding


def _grant_to(service: str, project: UUID) -> dict[str, Any]:
    grant = render_reference_grant(project)
    grant["spec"]["to"][0]["name"] = service
    return grant


def _config_map(project: UUID) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "not-in-scope", "namespace": tenant_namespace(project)},
        "data": {"k": "v"},
    }


def _label(namespace: Callable[[UUID], str], key: str, value: str) -> Callable[[UUID], list[str]]:
    return lambda p: ["label", "namespace", namespace(p), f"{key}={value}", "--overwrite"]


ENFORCE = "pod-security.kubernetes.io/enforce"
PROJECT_LABEL = "websites.webblock.internal/project-id"
SUBDOMAIN_LABEL = "websites.webblock.internal/subdomain"


def _new_namespace() -> dict[str, Any]:
    """A tenant namespace as bootstrap renders it for a new project, with its sub-domain label."""
    project = uuid4()
    return render_namespace(project, subdomain=policy_subdomain(project), catalog_secret=None)


def _unlabel(namespace: Callable[[UUID], str], key: str) -> Callable[[UUID], list[str]]:
    return lambda p: ["label", "namespace", namespace(p), f"{key}-"]


REFUSED: dict[str, Request] = {
    # Brief and finding M3.
    "label-kube-system-privileged": Request(
        args=_label(lambda _: "kube-system", ENFORCE, "privileged")
    ),
    "rolebinding-in-kube-system": Request(
        manifest=lambda p: _in("kube-system", render_worker_role_binding(p))
    ),
    "catalog-runtime-in-kube-system": Request(manifest=lambda p: _in("kube-system", _secret(p))),
    "secret-other-name-in-tenant": Request(manifest=lambda p: _renamed("other", _secret(p))),
    "secret-token-type-in-tenant": Request(manifest=_token_secret),
    "rolebinding-to-cluster-admin": Request(manifest=lambda p: _binding_to("cluster-admin", p)),
    "rolebinding-extra-subject": Request(manifest=_binding_with_extra_subject),
    "namespace-privileged": Request(
        manifest=lambda _: _labelled(ENFORCE, "privileged", _new_namespace())
    ),
    "namespace-wb-foo": Request(manifest=lambda _: _renamed("wb-foo", _new_namespace())),
    # Further edges of the same rules.
    "namespace-foreign-project-label": Request(
        manifest=lambda _: _labelled(PROJECT_LABEL, str(uuid4()), _new_namespace())
    ),
    "tenant-relabelled-privileged": Request(args=_label(tenant_namespace, ENFORCE, "privileged")),
    # Decision 0023 G18a: the sub-domain label is one DNS label, set once, never changed or removed.
    "namespace-subdomain-not-a-label": Request(
        manifest=lambda _: _labelled(SUBDOMAIN_LABEL, "Not_A_Label", _new_namespace())
    ),
    "tenant-subdomain-changed": Request(
        args=_label(tenant_namespace, SUBDOMAIN_LABEL, "someone-elses-shop")
    ),
    "tenant-subdomain-removed": Request(args=_unlabel(tenant_namespace, SUBDOMAIN_LABEL)),
    # FR-55 (preview-host security review L2): a new label never ends in -preview (another
    # project's preview host) nor passes 55 characters (its preview host would not be one label).
    "fr55-namespace-subdomain-ends-in-preview": Request(
        manifest=lambda _: _labelled(SUBDOMAIN_LABEL, "shop-preview", _new_namespace())
    ),
    "fr55-namespace-subdomain-of-56-characters": Request(
        manifest=lambda _: _labelled(SUBDOMAIN_LABEL, "a" * 56, _new_namespace())
    ),
    "rolebinding-in-webblock": Request(
        manifest=lambda p: _in("webblock", render_worker_role_binding(p))
    ),
    "referencegrant-to-another-service": Request(manifest=lambda p: _grant_to("kubernetes", p)),
    "referencegrant-in-tenant": Request(
        manifest=lambda p: _in(tenant_namespace(p), render_reference_grant(p))
    ),
    "configmap-in-tenant": Request(manifest=_config_map),
    "patch-tenant-namespace-status": Request(
        args=lambda p: [
            "patch",
            "namespace",
            tenant_namespace(p),
            "--subresource=status",
            "--type=merge",
            "-p",
            '{"status":{"phase":"Active"}}',
        ]
    ),
    "delete-tenant-namespace": Request(
        args=lambda p: ["delete", "namespace", tenant_namespace(p), "--wait=false"]
    ),
}

ADMITTED: dict[str, Callable[[UUID], dict[str, Any]]] = {
    "namespace": lambda _: _new_namespace(),
    "resource-quota": lambda p: render_resource_quota(p, SETTINGS),
    "limit-range": lambda p: render_limit_range(p, SETTINGS),
    "worker-rolebinding": render_worker_role_binding,
    "catalog-runtime": _secret,
    "reference-grant": render_reference_grant,
}


def _send(kubectl: Kubectl, who: Identity, request: Request, project: UUID) -> Any:
    if request.manifest is not None:
        return kubectl.dry_run_apply(who, request.manifest(project))
    return kubectl.run(who, *request.args(project), "--dry-run=server")


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_policy_alone_refuses(case: str, kubectl: Kubectl, tenant: UUID) -> None:
    """Impersonated with system:masters, RBAC allows everything; only the policy can refuse."""
    done = _send(kubectl, POLICY_ALONE, REFUSED[case], tenant)
    assert done.returncode != 0, f"{case} was admitted: {done.stdout.strip()}"
    assert f"ValidatingAdmissionPolicy '{POLICY}'" in done.stderr, done.stderr
    assert "forbidden" in done.stderr.lower()


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_bootstrap_token_is_refused(case: str, kubectl: Kubectl, tenant: UUID) -> None:
    """The real identity: refused by RBAC or by the policy, never admitted."""
    done = _send(kubectl, TOKEN, REFUSED[case], tenant)
    assert done.returncode != 0, f"{case} was admitted: {done.stdout.strip()}"
    assert "forbidden" in done.stderr.lower(), done.stderr


@pytest.mark.parametrize("case", sorted(ADMITTED))
def test_the_policy_admits_what_bootstrap_renders(
    case: str, kubectl: Kubectl, tenant: UUID
) -> None:
    """The refusals are specific: each object the renderer writes passes the policy alone."""
    done = kubectl.dry_run_apply(POLICY_ALONE, ADMITTED[case](tenant))
    assert done.returncode == 0, done.stderr


def _resource_versions(kubectl: Kubectl, project: UUID) -> dict[str, str]:
    ns = tenant_namespace(project)
    objects = {
        "namespace": ["namespace", ns],
        "quota": ["resourcequota", QUOTA_NAME, "-n", ns],
        "limits": ["limitrange", LIMITS_NAME, "-n", ns],
        "rolebinding": ["rolebinding", WORKER_CLUSTER_ROLE, "-n", ns],
        "secret": ["secret", RUNTIME_SECRET_NAME, "-n", ns],
        "grant": ["referencegrant", ns, "-n", OFFLINE_NAMESPACE],
    }
    versions = {}
    for key, args in objects.items():
        shown = json.loads(kubectl.admin_ok("get", *args, "-o", "json"))
        versions[key] = shown["metadata"]["resourceVersion"]
    return versions


@pytest.mark.asyncio
async def test_the_real_bootstrap_writes_a_tenant_and_reruns_as_a_check(kubectl: Kubectl) -> None:
    project = uuid4()
    api = await connect_kubeconfig(
        BOOTSTRAP_KUBECONFIG, field_manager=BOOTSTRAP_FIELD_MANAGER, request_timeout_seconds=10.0
    )
    try:
        bootstrap = KubernetesTenantBootstrap(api, SETTINGS)
        assert (
            await bootstrap.ensure_tenant(project, subdomain=policy_subdomain(project))
            is TenantState.READY
        )
        assert not await bootstrap.runtime_secret_exists(project)
        await bootstrap.write_runtime_secret(project, SECRET_VALUES)
        first = _resource_versions(kubectl, project)

        # A second run, as after a restart: everything exists, so nothing changes.
        assert (
            await bootstrap.ensure_tenant(project, subdomain=policy_subdomain(project))
            is TenantState.READY
        )
        assert await bootstrap.runtime_secret_exists(project)
        assert _resource_versions(kubectl, project) == first
    finally:
        await api.close()
        kubectl.admin_ok(
            "delete",
            "referencegrant",
            tenant_namespace(project),
            "-n",
            OFFLINE_NAMESPACE,
            "--ignore-not-found",
        )
        kubectl.admin_ok(
            "delete", "namespace", tenant_namespace(project), "--ignore-not-found", "--wait=false"
        )


def test_the_policy_admits_the_subdomain_label_added_once(kubectl: Kubectl) -> None:
    """A namespace from before the label (decision 0023 G18a): the bootstrap may add it once; the
    refusals above show it can then never change."""
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
        added = _label(tenant_namespace, SUBDOMAIN_LABEL, policy_subdomain(project))
        done = kubectl.run(POLICY_ALONE, *added(project), "--dry-run=server")
        assert done.returncode == 0, done.stderr
        done = kubectl.dry_run_apply(
            POLICY_ALONE,
            render_namespace(project, subdomain=policy_subdomain(project), catalog_secret=None),
        )
        assert done.returncode == 0, done.stderr
    finally:
        kubectl.admin_ok("delete", "namespace", tenant_namespace(project), "--wait=false")
