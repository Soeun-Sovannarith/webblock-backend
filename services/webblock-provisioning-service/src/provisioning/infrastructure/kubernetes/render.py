"""Pure functions from the cluster port's values to the manifests the worker and the bootstrap
component apply (decision 0013 section 3, decision 0009 section 3, review-k8s-nocp.md (a) 1).

Per site, in namespace ``wb-<projectId>``:
- Deployment and Service ``rel-<releaseId>``. The Deployment's metadata carries the command number
  ``websites.webblock.internal/command-number`` (never the pod template, or every command would
  roll the pods); its pod template carries the intent digest
  ``websites.webblock.internal/intent-digest``, and, for a redeploy only (decision 0023 G12),
  ``websites.webblock.internal/restart-command`` with the redeploy's command number, which rolls
  the pods with the same image. One unprivileged container ``site`` on port 8080
  with equal requests and limits, an HTTP readiness probe ``GET /healthz`` on that port (decision
  0014 section 21 ruling 12; section 15: 200 while the process is up), and
  ``progressDeadlineSeconds`` below the readiness deadline. The container takes its catalog
  credential from the Secret ``catalog-runtime`` through ``envFrom`` (decision 0014 section 14);
  the worker only names the Secret and never reads it. A ClusterIP Service on port 80 targets it.
- HTTPRoute ``route-<projectId>`` on the shared Gateway ``webblock-gateway/webblock-public``,
  with the caller's hostnames, to ``rel-<releaseId>`` or to the shared offline Service
  ``webblock-offline/offline-page`` (task A creates it; bootstrap grants the reference). Under
  the site domain it names only the project's own host ``<subdomain>.<site domain>``, the
  sub-domain from ``project_hosting.subdomain`` (decision 0023 G18); any other name there belongs
  to another project and is refused (hostname takeover).
- HTTPRoute ``preview-<projectId>`` on the same Gateway, with the one preview hostname, to the
  candidate's ``rel-<releaseId>``: a promotion probes the candidate through it before the site's
  route moves (decision 0014 section 17). It matches only requests carrying ``X-Webblock-Preview``
  with the project's own probe token, so the public gets 404 there (recheck N1), and it removes
  that header before the request reaches the site. The token is
  ``base64url(HMAC-SHA256(PREVIEW_PROBE_TOKEN, projectId))`` without padding (decision 0023 G18c,
  Task 12 security review S1): a site that reads the header learns only its own preview's token,
  never the worker's secret or another project's token. Its answers also carry
  ``Cache-Control: private, no-store``: a public preview passes through Cloudflare, which caches
  by host and path, not by the token header, so no shared cache may keep them (FR-52.1, security
  review M1).

Every rule of both routes carries a ``ResponseHeaderModifier`` filter that sets the response header
``X-Webblock-Release`` to the backend's release ID, or ``offline``. The Gateway overwrites any value
the site sends, so the header names the backend the Gateway used, and the promotion probe requires
it (decision 0014 section 17).

Per tenant: Namespace with its labels (immutable after creation, except that the sub-domain
label ``websites.webblock.internal/subdomain`` may be added once to a namespace from before it,
decision 0023 G18a), ResourceQuota, LimitRange, the
ReferenceGrant in ``webblock-offline``, and the worker's RoleBinding; and access_setup's Secret
``catalog-runtime`` (``type: Opaque``, the keys ``PGHOST``, ``PGPORT``, ``PGDATABASE``, ``PGUSER``,
``PGPASSWORD`` and ``PGSSLMODE``), which only the bootstrap identity writes. The prototype has no
CiliumNetworkPolicy.

The namespace may carry the annotation ``websites.webblock.internal/catalog-secret`` (task F2B;
decision 0016 section 8 ruling 4, amending 0014 section 24): ``pending`` when the bootstrap
created the namespace, so no pod in it has ever received a catalog password, and ``delivered``
once access_setup is about to write the Secret. ``render_namespace`` writes the value it is given
and none by default; ``KubernetesTenantBootstrap`` decides which.

Intent digest ``webblock.release-intent.v4``: ``sha256:`` and the hex SHA-256 of nine lines, each
ended by a line feed: the version, ``projectId=``, ``releaseId=``, ``image=`` (the full reference by
digest), ``runtimeAccessRef.name=`` (the Secret the container's ``envFrom`` names),
``resources.cpuMillicores=``, ``resources.memoryMiB=``, ``readinessProbe.httpGet.path=`` and
``readinessProbe.httpGet.port=`` (the probe the container carries). Version 3 had no probe lines
(its probe was a TCP connect); version 2 had no Secret reference (decision 0014 section 3);
version 1 (``0009`` section 13) named it with the image split into repository and digest. A
changed field list is a new version, never a silent change.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from provisioning.core.application.ports.site_cluster import (
    OFFLINE_HEADER_VALUE,
    PREVIEW_HEADER,
    RELEASE_HEADER,
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
)
from provisioning.core.domain import preview_label

type JsonObject = dict[str, Any]

LABEL_PROJECT_ID = "websites.webblock.internal/project-id"
LABEL_RELEASE_ID = "websites.webblock.internal/release-id"
LABEL_TENANT = "websites.webblock.internal/tenant"
# Decision 0023 G18a: the project's sub-domain, from project_hosting.subdomain, on the tenant
# namespace; the route-shape policy allows only <subdomain>.<site domain> in that namespace.
LABEL_SUBDOMAIN = "websites.webblock.internal/subdomain"
ANNOTATION_COMMAND_NUMBER = "websites.webblock.internal/command-number"
ANNOTATION_INTENT_DIGEST = "websites.webblock.internal/intent-digest"
# Decision 0023 G12: a redeploy's command number on the pod template; a new value rolls the pods.
ANNOTATION_RESTART_COMMAND = "websites.webblock.internal/restart-command"
# Task F2B: whether a tenant namespace ever received the Secret catalog-runtime (render_namespace).
ANNOTATION_CATALOG_SECRET = "websites.webblock.internal/catalog-secret"  # noqa: S105 - a key name
CATALOG_SECRET_PENDING = "pending"  # noqa: S105 - created by the bootstrap; no Secret yet
CATALOG_SECRET_DELIVERED = "delivered"  # noqa: S105 - the Secret is written, or about to be
INTENT_VERSION = "webblock.release-intent.v4"
RUNTIME_SECRET_NAME = "catalog-runtime"  # noqa: S105 - the Secret's name, not a credential
RUNTIME_SECRET_KEYS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")

GATEWAY_GROUP = "gateway.networking.k8s.io"
GATEWAY_NAMESPACE = "webblock-gateway"
GATEWAY_NAME = "webblock-public"
OFFLINE_NAMESPACE = "webblock-offline"
OFFLINE_SERVICE = "offline-page"
WORKER_CLUSTER_ROLE = "webblock-tenant-worker"
WORKER_SERVICE_ACCOUNT = "provisioning-worker"
WORKER_SERVICE_ACCOUNT_NAMESPACE = "webblock"
QUOTA_NAME = "webblock-tenant-quota"
LIMITS_NAME = "webblock-tenant-limits"

CONTAINER_NAME = "site"
PORT_NAME = "http"
CONTAINER_PORT = 8080
SERVICE_PORT = 80
POD_USER = 65532
TMP_SIZE_LIMIT = "64Mi"
READINESS_PERIOD_SECONDS = 5
# The site contract (decision 0014 section 15, README "Contract for generated sites"): 200 while the
# process is up, without touching dependencies. /readyz is the promotion probe's, not Kubernetes'.
READINESS_PATH = "/healthz"
POD_SECURITY_LEVEL = "restricted"
POD_SECURITY_VERSION = "v1.36"  # Kubernetes 1.36, the Kind node of decision 0009 section 1

MIN_CPU_MILLICORES, MAX_CPU_MILLICORES = 50, 2000
MIN_MEMORY_MIB, MAX_MEMORY_MIB = 64, 4096
MAX_COMMAND_NUMBER = 2**53 - 1
MAX_HOSTNAMES = 16

_IMAGE = re.compile(
    r"(?P<registry>[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?)"
    r"/webblock/sites/(?P<project>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"@sha256:[0-9a-f]{64}"
)
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_PREVIEW_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}")


class InvalidManifestInput(ValueError):
    """A value the renderer refuses before anything is written. Names the field, never its
    value."""


@dataclass(frozen=True, slots=True)
class SiteSettings:
    """Configuration of the per-site objects (production numbers are OD-33)."""

    cpu_millicores: int
    memory_mib: int
    progress_deadline_seconds: int
    readiness_deadline_seconds: int
    offline_service_port: int

    def __post_init__(self) -> None:
        if not MIN_CPU_MILLICORES <= self.cpu_millicores <= MAX_CPU_MILLICORES:
            raise ValueError(f"cpu must be {MIN_CPU_MILLICORES} to {MAX_CPU_MILLICORES} millicores")
        if not MIN_MEMORY_MIB <= self.memory_mib <= MAX_MEMORY_MIB:
            raise ValueError(f"memory must be {MIN_MEMORY_MIB} to {MAX_MEMORY_MIB} MiB")
        if not 0 < self.progress_deadline_seconds < self.readiness_deadline_seconds:
            raise ValueError(
                "the progress deadline must be positive and below the readiness deadline"
            )
        if not 1 <= self.offline_service_port <= 65535:
            raise ValueError("the offline service port must be 1 to 65535")


@dataclass(frozen=True, slots=True)
class TenantSettings:
    """Quota and container limits of a tenant namespace, as Kubernetes quantities (OD-33)."""

    quota_cpu: str
    quota_memory: str
    quota_pods: int
    container_max_cpu: str
    container_max_memory: str


def tenant_namespace(project_id: UUID) -> str:
    return f"wb-{project_id}"


def release_name(release_id: UUID) -> str:
    return f"rel-{release_id}"


def route_name(project_id: UUID) -> str:
    return f"route-{project_id}"


def preview_route_name(project_id: UUID) -> str:
    return f"preview-{project_id}"


def release_labels(project_id: UUID, release_id: UUID) -> dict[str, str]:
    return {LABEL_PROJECT_ID: str(project_id), LABEL_RELEASE_ID: str(release_id)}


def release_selector(project_id: UUID, release_id: UUID) -> str:
    """The label selector of one release's pods."""
    return ",".join(f"{k}={v}" for k, v in release_labels(project_id, release_id).items())


def is_hostname(value: str) -> bool:
    """A lowercase DNS name of at most 253 characters, without a wildcard or a trailing dot."""
    labels = value.split(".")
    return 0 < len(value) <= 253 and len(labels) > 1 and all(_LABEL.fullmatch(x) for x in labels)


def _check_command_number(number: int) -> None:
    if not 1 <= number <= MAX_COMMAND_NUMBER:
        raise InvalidManifestInput("the command number must be 1 to 2^53-1")


def _check_image(project_id: UUID, image: str) -> None:
    match = _IMAGE.fullmatch(image)
    if match is None or match.group("project") != str(project_id):
        raise InvalidManifestInput(
            "the image must be <registry>/webblock/sites/<projectId>@sha256:<digest> "
            "of this project"
        )


def release_intent_digest(
    project_id: UUID, release_id: UUID, image: str, settings: SiteSettings
) -> str:
    """The digest written on the pod template and recomputed before acknowledgement."""
    _check_image(project_id, image)
    probe = readiness_probe()["httpGet"]
    text = "".join(
        f"{line}\n"
        for line in (
            INTENT_VERSION,
            f"projectId={project_id}",
            f"releaseId={release_id}",
            f"image={image}",
            f"runtimeAccessRef.name={RUNTIME_SECRET_NAME}",
            f"resources.cpuMillicores={settings.cpu_millicores}",
            f"resources.memoryMiB={settings.memory_mib}",
            f"readinessProbe.httpGet.path={probe['path']}",
            f"readinessProbe.httpGet.port={probe['port']}",
        )
    )
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def readiness_probe() -> JsonObject:
    """The site container's readiness probe: ``GET /healthz`` over HTTP at the site port. The
    intent digest is computed from this same structure."""
    return {
        "httpGet": {"path": READINESS_PATH, "port": PORT_NAME, "scheme": "HTTP"},
        "periodSeconds": READINESS_PERIOD_SECONDS,
    }


def _metadata(
    name: str, namespace: str | None, labels: dict[str, str], command_number: int | None = None
) -> JsonObject:
    metadata: JsonObject = {"name": name}
    if namespace is not None:
        metadata["namespace"] = namespace
    metadata["labels"] = labels
    if command_number is not None:
        metadata["annotations"] = {ANNOTATION_COMMAND_NUMBER: str(command_number)}
    return metadata


def render_deployment(
    workload: ReleaseWorkload, settings: SiteSettings, *, restart_command: int | None = None
) -> JsonObject:
    """Deployment ``rel-<releaseId>``. With ``restart_command`` (a redeploy, decision 0023 G12)
    the pod template also carries ``ANNOTATION_RESTART_COMMAND`` with that number, which rolls the
    pods; without it the output is exactly what it was before redeploy existed.

    No ``strategy`` is written, so Kubernetes uses its default RollingUpdate with ``maxSurge`` and
    ``maxUnavailable`` of 25%. For the one replica rendered here that is a surge of 1 (rounded up)
    and 0 unavailable (rounded down): the old pod serves until the new one is ready."""
    _check_command_number(workload.command_number)
    digest = release_intent_digest(
        workload.project_id, workload.release_id, workload.image, settings
    )
    template_annotations = {ANNOTATION_INTENT_DIGEST: digest}
    if restart_command is not None:
        _check_command_number(restart_command)
        template_annotations[ANNOTATION_RESTART_COMMAND] = str(restart_command)
    labels = release_labels(workload.project_id, workload.release_id)
    resources = {"cpu": f"{settings.cpu_millicores}m", "memory": f"{settings.memory_mib}Mi"}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _metadata(
            release_name(workload.release_id),
            tenant_namespace(workload.project_id),
            labels,
            workload.command_number,
        ),
        "spec": {
            "replicas": 1,
            "revisionHistoryLimit": 1,
            "progressDeadlineSeconds": settings.progress_deadline_seconds,
            "selector": {"matchLabels": dict(labels)},
            "template": {
                "metadata": {
                    "labels": dict(labels),
                    "annotations": template_annotations,
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": POD_USER,
                        "runAsGroup": POD_USER,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": CONTAINER_NAME,
                            "image": workload.image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [
                                {
                                    "name": PORT_NAME,
                                    "containerPort": CONTAINER_PORT,
                                    "protocol": "TCP",
                                }
                            ],
                            "envFrom": [{"secretRef": {"name": RUNTIME_SECRET_NAME}}],
                            "resources": {"requests": dict(resources), "limits": dict(resources)},
                            "readinessProbe": readiness_probe(),
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],  # noqa: S108 - an emptyDir inside the pod, not the host's /tmp
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": TMP_SIZE_LIMIT}}],
                },
            },
        },
    }


def render_service(workload: ReleaseWorkload) -> JsonObject:
    _check_command_number(workload.command_number)
    labels = release_labels(workload.project_id, workload.release_id)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _metadata(
            release_name(workload.release_id),
            tenant_namespace(workload.project_id),
            labels,
            workload.command_number,
        ),
        "spec": {
            "type": "ClusterIP",
            "selector": dict(labels),
            "ports": [
                {
                    "name": PORT_NAME,
                    "port": SERVICE_PORT,
                    "protocol": "TCP",
                    "targetPort": PORT_NAME,
                }
            ],
        },
    }


def _release_backend(release_id: UUID) -> JsonObject:
    return {"group": "", "kind": "Service", "name": release_name(release_id), "port": SERVICE_PORT}


def release_header_filter(value: str) -> JsonObject:
    """The filter that sets ``X-Webblock-Release`` on every response of a rule."""
    return {
        "type": "ResponseHeaderModifier",
        "responseHeaderModifier": {"set": [{"name": RELEASE_HEADER, "value": value}]},
    }


PREVIEW_CACHE_CONTROL = "private, no-store"
"""The ``Cache-Control`` of every preview answer (FR-52.1, security review M1)."""


def preview_response_filter(value: str) -> JsonObject:
    """The preview rule's response filter: ``X-Webblock-Release`` as on every rule, and
    ``Cache-Control: private, no-store`` so no shared cache serves the candidate without the token.
    One filter, since the Gateway API allows one ``ResponseHeaderModifier`` per rule."""
    return {
        "type": "ResponseHeaderModifier",
        "responseHeaderModifier": {
            "set": [
                {"name": RELEASE_HEADER, "value": value},
                {"name": "Cache-Control", "value": PREVIEW_CACHE_CONTROL},
            ]
        },
    }


_GATEWAY_PARENT: JsonObject = {
    "group": GATEWAY_GROUP,
    "kind": "Gateway",
    "namespace": GATEWAY_NAMESPACE,
    "name": GATEWAY_NAME,
}


def _check_hostnames(hostnames: tuple[str, ...]) -> None:
    if not 1 <= len(hostnames) <= MAX_HOSTNAMES or len(set(hostnames)) != len(hostnames):
        raise InvalidManifestInput(f"a route needs 1 to {MAX_HOSTNAMES} distinct hostnames")
    if not all(is_hostname(h) for h in hostnames):
        raise InvalidManifestInput("a route hostname must be a lowercase DNS name, no wildcard")


def _check_site_domain(site_domain: str) -> None:
    if not is_hostname(site_domain):
        raise InvalidManifestInput("the site domain must be a lowercase DNS name")


def _under(hostname: str, site_domain: str) -> bool:
    return hostname == site_domain or hostname.endswith("." + site_domain)


def _check_subdomain(subdomain: str) -> None:
    if not _LABEL.fullmatch(subdomain):
        raise InvalidManifestInput("the sub-domain must be one lowercase DNS label")


def _check_owned(subdomain: str, hostnames: tuple[str, ...], site_domain: str) -> None:
    """A route names, under the platform suffix, only this project's own host
    ``<subdomain>.<site domain>``: any other name there belongs to another project (hostname
    takeover, measured by T1)."""
    own = f"{subdomain}.{site_domain}"
    if any(_under(h, site_domain) and h != own for h in hostnames):
        raise InvalidManifestInput(
            "a route names no hostname under the site domain but the project's own subdomain"
        )


def _route_object(
    name: str,
    project_id: UUID,
    hostnames: tuple[str, ...],
    rule: JsonObject,
    command_number: int,
) -> JsonObject:
    return {
        "apiVersion": f"{GATEWAY_GROUP}/v1",
        "kind": "HTTPRoute",
        "metadata": _metadata(
            name,
            tenant_namespace(project_id),
            {LABEL_PROJECT_ID: str(project_id)},
            command_number,
        ),
        "spec": {
            "parentRefs": [dict(_GATEWAY_PARENT)],
            "hostnames": list(hostnames),
            "rules": [rule],
        },
    }


def _serving_rule(
    backend: JsonObject, matches: list[JsonObject], *filters: JsonObject
) -> JsonObject:
    return {"matches": matches, "filters": list(filters), "backendRefs": [backend]}


_ALL_PATHS: JsonObject = {"path": {"type": "PathPrefix", "value": "/"}}


def render_route(
    project_id: UUID,
    target: RouteTarget,
    hostnames: tuple[str, ...],
    command_number: int,
    settings: SiteSettings,
    *,
    site_domain: str,
    subdomain: str,
) -> JsonObject:
    """HTTPRoute ``route-<projectId>``: ``hostnames`` to the target, on the shared Gateway.
    ``subdomain`` is the project's ``project_hosting.subdomain``: under ``site_domain`` the route
    names only ``<subdomain>.<site_domain>``."""
    _check_command_number(command_number)
    _check_hostnames(hostnames)
    _check_site_domain(site_domain)
    _check_subdomain(subdomain)
    _check_owned(subdomain, hostnames, site_domain)
    backend: JsonObject
    if target.kind is RouteTargetKind.RELEASE and target.release_id is not None:
        backend = _release_backend(target.release_id)
        header_value = str(target.release_id)
    elif target.kind is RouteTargetKind.OFFLINE and target.release_id is None:
        backend = {
            "group": "",
            "kind": "Service",
            "namespace": OFFLINE_NAMESPACE,
            "name": OFFLINE_SERVICE,
            "port": settings.offline_service_port,
        }
        header_value = OFFLINE_HEADER_VALUE
    else:
        raise InvalidManifestInput("a route target names a release exactly when it is a release")
    return _route_object(
        route_name(project_id),
        project_id,
        hostnames,
        _serving_rule(backend, [dict(_ALL_PATHS)], release_header_filter(header_value)),
        command_number,
    )


def preview_token_for(secret: str, project_id: UUID) -> str:
    """The project's preview token: ``base64url(HMAC-SHA256(secret, str(projectId)))`` without
    padding, 43 URL-safe characters (decision 0023 G18c). ``secret`` is the worker's
    ``PREVIEW_PROBE_TOKEN``; it never leaves the worker."""
    digest = hmac.new(secret.encode(), str(project_id).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def preview_header_removal() -> JsonObject:
    """The preview rule's second filter: the Gateway removes ``X-Webblock-Preview`` before the
    request reaches the site (filters run after the match; decision 0023 G18c)."""
    return {"type": "RequestHeaderModifier", "requestHeaderModifier": {"remove": [PREVIEW_HEADER]}}


def preview_match(token: str) -> JsonObject:
    """The preview route's one match: every path, and only with ``X-Webblock-Preview: <token>``
    (recheck finding N1)."""
    return {
        "path": {"type": "PathPrefix", "value": "/"},
        "headers": [{"type": "Exact", "name": PREVIEW_HEADER, "value": token}],
    }


def _check_preview_host(subdomain: str, hostname: str, site_domain: str) -> None:
    """A preview route names only ``<subdomain>-preview.<site domain>`` (FR-20; Task 12 security
    review S3), as ``_check_owned`` holds the site route to its own host."""
    if hostname != f"{preview_label(subdomain)}.{site_domain}":
        raise InvalidManifestInput("a preview route names only the project's own preview hostname")


def render_preview_route(
    project_id: UUID,
    release_id: UUID,
    hostname: str,
    command_number: int,
    *,
    preview_secret: str,
    site_domain: str,
    subdomain: str,
) -> JsonObject:
    """HTTPRoute ``preview-<projectId>``: the one preview hostname to the candidate's Service,
    matched only with the project's token header (``preview_token_for(preview_secret,
    project_id)``), so the public gets 404 there, and the header removed before the site. Its
    answers say ``Cache-Control: private, no-store`` (``preview_response_filter``).
    ``hostname`` must be ``<subdomain>-preview.<site_domain>``, ``subdomain`` the project's
    ``project_hosting.subdomain``."""
    _check_command_number(command_number)
    _check_hostnames((hostname,))
    _check_site_domain(site_domain)
    _check_subdomain(subdomain)
    _check_preview_host(subdomain, hostname, site_domain)
    if not _PREVIEW_TOKEN.fullmatch(preview_secret):
        raise InvalidManifestInput("the preview secret must be 32 to 256 URL-safe characters")
    rule = _serving_rule(
        _release_backend(release_id),
        [preview_match(preview_token_for(preview_secret, project_id))],
        preview_response_filter(str(release_id)),
        preview_header_removal(),
    )
    return _route_object(
        preview_route_name(project_id), project_id, (hostname,), rule, command_number
    )


def render_namespace(
    project_id: UUID, *, subdomain: str | None, catalog_secret: str | None
) -> JsonObject:
    """The tenant namespace. ``subdomain`` is the value of the label ``LABEL_SUBDOMAIN`` (the
    project's ``project_hosting.subdomain``), or None for a namespace without it.
    ``catalog_secret`` is the value of the annotation ``ANNOTATION_CATALOG_SECRET``, or None for a
    namespace without it (see the module docstring). Both required, with no default: an apply
    without the label or the mark removes it from the bootstrap's field set, which admission
    refuses."""
    labels = {
        LABEL_PROJECT_ID: str(project_id),
        LABEL_TENANT: "true",
        "pod-security.kubernetes.io/enforce": POD_SECURITY_LEVEL,
        "pod-security.kubernetes.io/enforce-version": POD_SECURITY_VERSION,
        "pod-security.kubernetes.io/audit": POD_SECURITY_LEVEL,
        "pod-security.kubernetes.io/audit-version": POD_SECURITY_VERSION,
        "pod-security.kubernetes.io/warn": POD_SECURITY_LEVEL,
        "pod-security.kubernetes.io/warn-version": POD_SECURITY_VERSION,
    }
    if subdomain is not None:
        _check_subdomain(subdomain)
        labels[LABEL_SUBDOMAIN] = subdomain
    metadata = _metadata(tenant_namespace(project_id), None, labels)
    if catalog_secret is not None:
        metadata["annotations"] = {ANNOTATION_CATALOG_SECRET: catalog_secret}
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": metadata}


def render_resource_quota(project_id: UUID, settings: TenantSettings) -> JsonObject:
    return {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": _metadata(
            QUOTA_NAME, tenant_namespace(project_id), {LABEL_PROJECT_ID: str(project_id)}
        ),
        "spec": {
            "hard": {
                "requests.cpu": settings.quota_cpu,
                "limits.cpu": settings.quota_cpu,
                "requests.memory": settings.quota_memory,
                "limits.memory": settings.quota_memory,
                "pods": str(settings.quota_pods),
                "services.loadbalancers": "0",
                "services.nodeports": "0",
                "persistentvolumeclaims": "0",
            }
        },
    }


def render_limit_range(project_id: UUID, settings: TenantSettings) -> JsonObject:
    largest = {"cpu": settings.container_max_cpu, "memory": settings.container_max_memory}
    return {
        "apiVersion": "v1",
        "kind": "LimitRange",
        "metadata": _metadata(
            LIMITS_NAME, tenant_namespace(project_id), {LABEL_PROJECT_ID: str(project_id)}
        ),
        "spec": {
            "limits": [
                {
                    "type": "Container",
                    "default": dict(largest),
                    "defaultRequest": dict(largest),
                    "max": dict(largest),
                }
            ]
        },
    }


def render_reference_grant(project_id: UUID) -> JsonObject:
    """Lets this tenant's HTTPRoute reach the shared offline Service, nothing else."""
    return {
        "apiVersion": f"{GATEWAY_GROUP}/v1beta1",
        "kind": "ReferenceGrant",
        "metadata": _metadata(
            tenant_namespace(project_id), OFFLINE_NAMESPACE, {LABEL_PROJECT_ID: str(project_id)}
        ),
        "spec": {
            "from": [
                {
                    "group": GATEWAY_GROUP,
                    "kind": "HTTPRoute",
                    "namespace": tenant_namespace(project_id),
                }
            ],
            "to": [{"group": "", "kind": "Service", "name": OFFLINE_SERVICE}],
        },
    }


def render_worker_role_binding(project_id: UUID) -> JsonObject:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": _metadata(
            WORKER_CLUSTER_ROLE, tenant_namespace(project_id), {LABEL_PROJECT_ID: str(project_id)}
        ),
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": WORKER_CLUSTER_ROLE,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": WORKER_SERVICE_ACCOUNT,
                "namespace": WORKER_SERVICE_ACCOUNT_NAMESPACE,
            }
        ],
    }


def render_runtime_secret(project_id: UUID, values: Mapping[str, str]) -> JsonObject:
    """access_setup's Secret ``catalog-runtime``: exactly the six connection keys, each a non-empty
    string, base64-encoded under ``data`` (never ``stringData``). Refusals name no value."""
    if set(values) != set(RUNTIME_SECRET_KEYS):
        raise InvalidManifestInput("the runtime secret must hold exactly the six PG* keys")
    if not all(isinstance(v, str) and v for v in values.values()):
        raise InvalidManifestInput("every runtime secret value must be a non-empty string")
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _metadata(
            RUNTIME_SECRET_NAME, tenant_namespace(project_id), {LABEL_PROJECT_ID: str(project_id)}
        ),
        "type": "Opaque",
        "data": {
            key: base64.b64encode(values[key].encode()).decode() for key in RUNTIME_SECRET_KEYS
        },
    }
