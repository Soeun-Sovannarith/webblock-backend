"""``TenantBootstrap`` over Kubernetes, with the bootstrap component's own identity (decision 0013
section 3 and section 9, K1; review-k8s-nocp.md M5; decision 0014 section 3).

Order: the Namespace ``wb-<projectId>`` with its labels, the ResourceQuota, the LimitRange, the
ReferenceGrant in ``webblock-offline`` that lets this tenant's HTTPRoute reach ``offline-page``,
and the worker's RoleBinding to ``webblock-tenant-worker`` **last**. The worker can read nothing in
the namespace before its RoleBinding exists, so a readable namespace is a complete tenant. Each
step is a server-side apply under field manager ``webblock-tenant-bootstrap`` with ``force=false``,
so running it again changes nothing.

A namespace that is being deleted is reported as ``TERMINATING`` and nothing is written; one that
starts terminating during the steps is reported the same way. When this call's own read found no
namespace and its namespace apply then succeeded, ``on_created`` is called with the project at
once, before the later steps (task BT; the port's docstring says what "created" means, and why a
rival writer in that gap still counts). What it raises is not swallowed.

A failed step raises and leaves the RoleBinding unwritten (``ApplyConflict``, ``ClusterRefused``,
``ClusterUnavailable``, ``KubernetesForbidden``). The release worker never constructs this class:
it is built from the bootstrap kubeconfig or ServiceAccount only.

access_setup's Secret (decision 0014 section 14, decision 0011 section 5): ``runtime_secret_exists``
and ``write_runtime_secret`` read and apply the Secret ``catalog-runtime`` in ``wb-<projectId>``,
so this class is the ``RuntimeSecretSink`` of ``infrastructure.catalog.CatalogAccessSetup``. The
Secret's values are never logged and never put into an error; ``ensure_tenant`` does not touch it.

The durable "created from nothing" mark (task F2B; decision 0016 section 8 ruling 4, amending 0014
section 24): the namespace annotation ``websites.webblock.internal/catalog-secret``.
- ``ensure_tenant`` creates a namespace with ``pending``, in the namespace apply itself, so before
  the worker's RoleBinding and before anything can run in it. On a namespace that exists it
  carries the annotation's current value unchanged, or none when it has none (a namespace from
  before this change), so a rerun never moves the mark.
- ``write_runtime_secret`` sets ``delivered`` first, as an apply that names the namespace's
  ``resourceVersion`` (so it never creates a namespace and never overwrites a change it did not
  read), and only then applies the Secret. A crash between the two leaves ``delivered`` without a
  Secret: the operator case of decision 0011 section 5, the safe direction.
- ``catalog_secret_pending`` is the probe: the namespace exists and its mark is ``pending``. With
  the Secret absent, that is a namespace that never held a password, after any restart.
The admission policies ``webblock-bootstrap-scope`` and ``webblock-tenant-writer`` hold the mark
to these moves: ``pending`` only on creation, ``pending`` or none to ``delivered``, never back, and
only by the bootstrap identity.

The sub-domain label (decision 0023 G18a): ``ensure_tenant`` writes the project's
``project_hosting.subdomain`` as the namespace label ``websites.webblock.internal/subdomain``,
which the route-shape admission policy reads to allow only ``<subdomain>.<site domain>`` in that
namespace. A namespace from before the label gets it on the next ``ensure_tenant``;
``webblock-bootstrap-scope`` allows it to be added once and never changed or removed.
``write_runtime_secret`` re-applies the namespace with the label it read, so its apply under the
same field manager never drops it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from provisioning.core.application.ports.site_cluster import ClusterUnavailable, TenantState
from provisioning.infrastructure.kubernetes._client import (
    JsonObject,
    KubeApi,
    KubernetesForbidden,
    KubernetesNotFound,
    Resource,
)
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_CATALOG_SECRET,
    CATALOG_SECRET_DELIVERED,
    CATALOG_SECRET_PENDING,
    LABEL_SUBDOMAIN,
    RUNTIME_SECRET_NAME,
    TenantSettings,
    render_limit_range,
    render_namespace,
    render_reference_grant,
    render_resource_quota,
    render_runtime_secret,
    render_worker_role_binding,
    tenant_namespace,
)


class KubernetesTenantBootstrap:
    def __init__(self, api: KubeApi, settings: TenantSettings) -> None:
        self._api = api
        self._settings = settings

    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        existing = await self._api.read(Resource.NAMESPACE, tenant_namespace(project_id))
        if existing is not None and _terminating(existing):
            return TenantState.TERMINATING
        # A new namespace is created with the mark pending; an existing one keeps its own.
        mark = CATALOG_SECRET_PENDING if existing is None else catalog_secret_mark(existing)
        steps = (
            (
                Resource.NAMESPACE,
                render_namespace(project_id, subdomain=subdomain, catalog_secret=mark),
            ),
            (Resource.RESOURCE_QUOTA, render_resource_quota(project_id, self._settings)),
            (Resource.LIMIT_RANGE, render_limit_range(project_id, self._settings)),
            (Resource.REFERENCE_GRANT, render_reference_grant(project_id)),
            (Resource.ROLE_BINDING, render_worker_role_binding(project_id)),
        )
        for resource, manifest in steps:
            try:
                applied = await self._api.apply(resource, manifest)
            except KubernetesForbidden as e:
                if e.namespace_terminating:
                    return TenantState.TERMINATING
                raise
            if resource is Resource.NAMESPACE:
                if existing is None and on_created is not None:
                    # Reported at once, before any later step can fail or be cancelled (task BT).
                    on_created(project_id)
                if _terminating(applied):
                    return TenantState.TERMINATING
                if (applied.get("status") or {}).get("phase") != "Active":
                    return TenantState.CREATING
        return TenantState.READY

    async def runtime_secret_exists(self, project_id: UUID) -> bool:
        """Whether the Secret ``catalog-runtime`` exists in the tenant namespace."""
        found = await self._api.read(
            Resource.SECRET, RUNTIME_SECRET_NAME, tenant_namespace(project_id)
        )
        return found is not None

    async def catalog_secret_pending(self, project_id: UUID) -> bool:
        """Whether the tenant namespace exists with the mark ``pending``: the bootstrap created it
        and no catalog Secret was ever delivered to it, so no pod in it holds a password."""
        namespace = await self._api.read(Resource.NAMESPACE, tenant_namespace(project_id))
        return namespace is not None and catalog_secret_mark(namespace) == CATALOG_SECRET_PENDING

    async def write_runtime_secret(self, project_id: UUID, values: Mapping[str, str]) -> None:
        """Mark the namespace ``delivered``, then apply the Secret ``catalog-runtime`` with exactly
        the six connection keys. The object the server returns, which holds the password, is
        dropped at once. ``KubernetesNotFound`` when the namespace does not exist (nothing is
        written); a failed mark raises before the Secret is written."""
        secret = render_runtime_secret(project_id, values)  # refused input moves no mark
        namespace = await self._api.read(Resource.NAMESPACE, tenant_namespace(project_id))
        if namespace is None:
            raise KubernetesNotFound("kubernetes apply secrets: the tenant namespace is missing")
        if catalog_secret_mark(namespace) != CATALOG_SECRET_DELIVERED:
            marked = render_namespace(
                project_id,
                subdomain=subdomain_label(namespace),
                catalog_secret=CATALOG_SECRET_DELIVERED,
            )
            # The read's version: an apply that would create the namespace, or overwrite a change
            # made since the read, is refused (409) instead.
            marked["metadata"]["resourceVersion"] = _resource_version(namespace)
            await self._api.apply(Resource.NAMESPACE, marked)
        await self._api.apply(Resource.SECRET, secret)


def catalog_secret_mark(namespace: JsonObject) -> str | None:
    """The value of the namespace's annotation ``websites.webblock.internal/catalog-secret``, or
    None when it has none."""
    metadata = namespace.get("metadata")
    marks = metadata.get("annotations") if isinstance(metadata, dict) else None
    value = marks.get(ANNOTATION_CATALOG_SECRET) if isinstance(marks, dict) else None
    return value if isinstance(value, str) else None


def subdomain_label(namespace: JsonObject) -> str | None:
    """The value of the namespace's label ``websites.webblock.internal/subdomain``, or None when
    it has none."""
    metadata = namespace.get("metadata")
    labels = metadata.get("labels") if isinstance(metadata, dict) else None
    value = labels.get(LABEL_SUBDOMAIN) if isinstance(labels, dict) else None
    return value if isinstance(value, str) else None


def _resource_version(namespace: JsonObject) -> str:
    metadata = namespace.get("metadata")
    version = metadata.get("resourceVersion") if isinstance(metadata, dict) else None
    if not isinstance(version, str) or not version:
        raise ClusterUnavailable("kubernetes read namespaces: no resourceVersion")
    return version


def _terminating(namespace: dict[str, object]) -> bool:
    metadata = namespace.get("metadata")
    status = namespace.get("status")
    deleting = isinstance(metadata, dict) and bool(metadata.get("deletionTimestamp"))
    phase = status.get("phase") if isinstance(status, dict) else None
    return deleting or phase == "Terminating"
