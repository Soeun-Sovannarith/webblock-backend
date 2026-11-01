"""The only place that builds a Kubernetes client (decision 0013 section 9, real-cluster safety;
review-stack.md H1, H2, M1, M6).

Where a client may come from:
- ``connect_kubeconfig``: an explicit, absolute, project-local kubeconfig path. The file is read
  once and refused unless its current context is ``kind-webblock-*`` or ``k3d-webblock-*`` (the
  local Kind or k3d cluster), its server is exactly ``https://127.0.0.1:<port>``, it names no
  proxy, and its user authenticates by token or client certificate (an ``exec`` or
  ``auth-provider`` user would run code or fetch tokens). It is then
  loaded with ``persist_config=False``, so nothing is ever written back, and the loaded host is
  checked again. ``$KUBECONFIG`` and ``~/.kube/config`` are never consulted.
- ``connect_in_cluster``: from a pod, the explicitly mounted, projected ServiceAccount token and the
  cluster's CA, named by absolute path, never the default automount path; the client is returned
  only after a SelfSubjectReview names the expected ServiceAccount (decision 0016 section 9
  ruling 2). The library reads the token file again every minute, so a rotated token is used.

What a client may do: ``KubeApi`` exposes server-side apply, read, a preconditioned delete and a
paged list, through the library's **typed** API only. Every apply sends
``Content-Type: application/apply-patch+yaml`` with ``fieldManager``, ``force=false`` and
``fieldValidation=Strict``; every call has an explicit ``_request_timeout`` and an
``asyncio.timeout``. Two exceptions, each asked for by keyword: ``force=True`` sends
``force=true``, for an HTTPRoute only (the operator rollback's reclaim, decision 0014 section 23
item 3; any other kind is refused before a request); ``dry_run=True`` sends ``dryRun=All``, so the
API server validates and admits the object and stores nothing. A list reads at most
``_LIST_PAGES`` pages of ``_LIST_PAGE`` objects and refuses a longer one (``ClusterUnavailable``).

Errors: answers become typed errors that carry the operation, the resource and the HTTP status,
never a response body; the library's exception is dropped (``from None``).
- 409 on apply: ``ApplyConflict`` (port). 409 on delete: ``KubernetesConflict``.
- 403 from authorization (details naming the resource, no causes) or from a terminating
  namespace: ``KubernetesForbidden``. 403 from admission, 400 and 422: ``ClusterRefused`` (port).
- 500 on apply whose Status message starts with "failed to create typed patch object":
  ``ClusterRefused``. Kubernetes 1.36 answers a server-side apply that carries a field its schema
  does not declare this way, with no reason or details, whatever ``fieldValidation`` says (measured
  live on Kind by task D); the same manifest is refused every time, so it is not transient. The
  message is only matched, never carried.
- 404: ``KubernetesNotFound`` (a read returns None instead).
- 401, 429, 5xx, a timeout or a connection failure: ``ClusterUnavailable`` (port).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import aiohttp
from kubernetes_asyncio import client as k8s
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config import (
    ConfigException,
    load_incluster_config,
    load_kube_config,
)
from kubernetes_asyncio.config.kube_config import KubeConfigMerger

from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
)

type JsonObject = dict[str, Any]

WORKER_FIELD_MANAGER = "webblock-provisioning-worker"
BOOTSTRAP_FIELD_MANAGER = "webblock-tenant-bootstrap"

_APPLY_CONTENT_TYPE = "application/apply-patch+yaml"
# The generated client sends a Python bool as "False"; the API server accepts both spellings, and
# the canonical lowercase form is what the brief and the request-shape test pin.
_FORCE = "false"
# Sent only by apply(..., force=True) of an HTTPRoute: tests/unit/infrastructure/kubernetes/
# test_forced_apply.py checks that this is the one use and reclaim_route the one caller.
_FORCED = "true"
_STRICT = "Strict"
_DRY_RUN = "All"
# A list reads pages of _LIST_PAGE objects and stops after _LIST_PAGES pages (50 000 objects), far
# above a tenant namespace or the cluster's tenants; a longer list is refused, never truncated.
_LIST_PAGE = 500
_LIST_PAGES = 100

_LOCAL_CONTEXT = re.compile(r"(?:kind|k3d)-webblock-[a-z0-9]([a-z0-9-]*[a-z0-9])?")
_LOCAL_SERVER = re.compile(r"https://127\.0\.0\.1:([0-9]{1,5})")
_FIELD_MANAGER = re.compile(r"[a-z][a-z0-9-]{0,62}")


class KubeconfigRefused(Exception):
    """The kubeconfig, or the in-cluster environment, is not one this service may use."""


class KubernetesNotFound(Exception):
    """404: the object, or the namespace it would live in, does not exist."""


class KubernetesForbidden(Exception):
    """403 from authorization: the identity may not do this here, or the namespace is
    terminating (``namespace_terminating``)."""

    def __init__(self, message: str, *, namespace_terminating: bool = False) -> None:
        super().__init__(message)
        self.namespace_terminating = namespace_terminating


class KubernetesConflict(Exception):
    """409 outside apply: a delete precondition (UID or resourceVersion) no longer holds."""


class Resource(StrEnum):
    DEPLOYMENT = "deployments"
    SERVICE = "services"
    HTTP_ROUTE = "httproutes"
    NAMESPACE = "namespaces"
    RESOURCE_QUOTA = "resourcequotas"
    LIMIT_RANGE = "limitranges"
    ROLE_BINDING = "rolebindings"
    REFERENCE_GRANT = "referencegrants"
    ENDPOINT_SLICE = "endpointslices"
    POD = "pods"
    # access_setup's Secret catalog-runtime, applied and read by the bootstrap identity only
    # (decision 0014 section 14); the worker's RBAC has no Secret access at all.
    SECRET = "secrets"  # noqa: S105 - the resource's plural, not a credential


@dataclass(frozen=True, slots=True)
class _Kind:
    kind: str
    api_version: str
    namespaced: bool
    api: str  # "apps", "core", "rbac", "discovery", or "custom"
    suffix: str  # the typed method suffix, or the plural of a custom resource


_KINDS: dict[Resource, _Kind] = {
    Resource.DEPLOYMENT: _Kind("Deployment", "apps/v1", True, "apps", "namespaced_deployment"),
    Resource.SERVICE: _Kind("Service", "v1", True, "core", "namespaced_service"),
    Resource.NAMESPACE: _Kind("Namespace", "v1", False, "core", "namespace"),
    Resource.RESOURCE_QUOTA: _Kind(
        "ResourceQuota", "v1", True, "core", "namespaced_resource_quota"
    ),
    Resource.LIMIT_RANGE: _Kind("LimitRange", "v1", True, "core", "namespaced_limit_range"),
    Resource.ROLE_BINDING: _Kind(
        "RoleBinding", "rbac.authorization.k8s.io/v1", True, "rbac", "namespaced_role_binding"
    ),
    Resource.HTTP_ROUTE: _Kind(
        "HTTPRoute", "gateway.networking.k8s.io/v1", True, "custom", "httproutes"
    ),
    Resource.REFERENCE_GRANT: _Kind(
        "ReferenceGrant", "gateway.networking.k8s.io/v1beta1", True, "custom", "referencegrants"
    ),
    Resource.ENDPOINT_SLICE: _Kind(
        "EndpointSlice", "discovery.k8s.io/v1", True, "discovery", "namespaced_endpoint_slice"
    ),
    Resource.POD: _Kind("Pod", "v1", True, "core", "namespaced_pod"),
    Resource.SECRET: _Kind("Secret", "v1", True, "core", "namespaced_secret"),
}

# What this service lists: a release's pods and endpoints (readiness), the tenant namespaces and a
# tenant's per-site objects (the reconciler's inventory). Never Secrets or RoleBindings.
_LISTED = frozenset(
    {
        Resource.POD,
        Resource.ENDPOINT_SLICE,
        Resource.NAMESPACE,
        Resource.DEPLOYMENT,
        Resource.SERVICE,
        Resource.HTTP_ROUTE,
    }
)


class KubeApi:
    """One identity's typed access to the API server. Built only by the two connect functions."""

    def __init__(self, api_client: Any, field_manager: str, request_timeout_seconds: float) -> None:
        self._client = api_client
        self._field_manager = field_manager
        self._timeout = request_timeout_seconds
        self._apis: dict[str, Any] = {
            "apps": k8s.AppsV1Api(api_client),
            "core": k8s.CoreV1Api(api_client),
            "rbac": k8s.RbacAuthorizationV1Api(api_client),
            "discovery": k8s.DiscoveryV1Api(api_client),
            "custom": k8s.CustomObjectsApi(api_client),
        }

    @property
    def field_manager(self) -> str:
        return self._field_manager

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def apply(
        self,
        resource: Resource,
        manifest: Mapping[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> JsonObject:
        """Server-side apply of ``manifest``; returns the object as the server stored it (or, with
        ``dry_run``, would store it). For a custom resource (HTTPRoute, ReferenceGrant) that this
        apply creates, the result is ``{}``: the generated client maps only HTTP 200 of a custom
        object patch to a body and drops the 201 one (measured on Kind, task W6-M); no caller
        uses that body. ``force`` takes conflicting fields from other field managers; only an
        HTTPRoute may be forced (decision 0014 section 23 item 3)."""
        kind = _KINDS[resource]
        if force and resource is not Resource.HTTP_ROUTE:
            raise ValueError(f"only an HTTPRoute is ever force-applied, never {resource}")
        if manifest.get("kind") != kind.kind or manifest.get("apiVersion") != kind.api_version:
            raise ValueError(f"the manifest's kind or apiVersion is not that of {resource}")
        metadata = manifest.get("metadata") or {}
        name = metadata.get("name")
        namespace = metadata.get("namespace")
        if not isinstance(name, str) or not name or (namespace is None) == kind.namespaced:
            raise ValueError(f"the manifest's name or namespace does not suit {resource}")
        options: dict[str, Any] = {
            "field_manager": self._field_manager,
            "force": _FORCED if force else _FORCE,
            "field_validation": _STRICT,
            "_content_type": _APPLY_CONTENT_TYPE,
            "_request_timeout": self._timeout,
        }
        if dry_run:
            options["dry_run"] = _DRY_RUN
        body = dict(manifest)
        api = self._apis[kind.api]
        if kind.api == "custom":
            group, version = kind.api_version.split("/")
            call = api.patch_namespaced_custom_object(
                group, version, namespace, kind.suffix, name, body, **options
            )
        elif kind.namespaced:
            call = getattr(api, f"patch_{kind.suffix}")(name, namespace, body, **options)
        else:
            call = getattr(api, f"patch_{kind.suffix}")(name, body, **options)
        try:
            return await self._send(call)
        except ApiException as e:
            if e.status == 409:
                raise ApplyConflict(_message("apply", resource, e)) from None
            raise _failure("apply", resource, e) from None
        except TimeoutError, aiohttp.ClientError, OSError:
            raise ClusterUnavailable(f"kubernetes apply {resource}: no answer") from None

    async def read(
        self, resource: Resource, name: str, namespace: str | None = None
    ) -> JsonObject | None:
        """The object, or None when it does not exist."""
        kind = _KINDS[resource]
        options = {"_request_timeout": self._timeout}
        api = self._apis[kind.api]
        if kind.api == "custom":
            group, version = kind.api_version.split("/")
            call = api.get_namespaced_custom_object(
                group, version, namespace, kind.suffix, name, **options
            )
        elif kind.namespaced:
            call = getattr(api, f"read_{kind.suffix}")(name, namespace, **options)
        else:
            call = getattr(api, f"read_{kind.suffix}")(name, **options)
        try:
            return await self._send(call)
        except ApiException as e:
            if e.status == 404:
                return None
            raise _failure("read", resource, e) from None
        except TimeoutError, aiohttp.ClientError, OSError:
            raise ClusterUnavailable(f"kubernetes read {resource}: no answer") from None

    async def delete(
        self, resource: Resource, name: str, namespace: str, *, uid: str, resource_version: str
    ) -> bool:
        """Delete with UID and resourceVersion preconditions and background propagation. False
        when the object does not exist; ``KubernetesConflict`` when a precondition fails."""
        kind = _KINDS[resource]
        if resource not in (Resource.DEPLOYMENT, Resource.SERVICE):
            raise ValueError(f"{resource} is never deleted by this service")
        body = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": uid, "resourceVersion": resource_version},
            "propagationPolicy": "Background",
        }
        api = self._apis[kind.api]
        call = getattr(api, f"delete_{kind.suffix}")(
            name, namespace, body=body, _request_timeout=self._timeout
        )
        try:
            await self._send(call)
        except ApiException as e:
            if e.status == 404:
                return False
            if e.status == 409:
                raise KubernetesConflict(_message("delete", resource, e)) from None
            raise _failure("delete", resource, e) from None
        except TimeoutError, aiohttp.ClientError, OSError:
            raise ClusterUnavailable(f"kubernetes delete {resource}: no answer") from None
        return True

    async def list_objects(
        self, resource: Resource, namespace: str | None, label_selector: str = ""
    ) -> list[JsonObject]:
        """The objects of one namespace (None for a cluster-scoped kind) that match
        ``label_selector`` (every object when it is empty), page by page."""
        kind = _KINDS[resource]
        if resource not in _LISTED:
            raise ValueError(f"{resource} is not listed by this service")
        if (namespace is None) == kind.namespaced:
            raise ValueError(f"the namespace does not suit a list of {resource}")
        items: list[JsonObject] = []
        token: str | None = None
        for _ in range(_LIST_PAGES):
            options: dict[str, Any] = {"limit": _LIST_PAGE, "_request_timeout": self._timeout}
            if label_selector:
                options["label_selector"] = label_selector
            if token:
                options["_continue"] = token
            listed = await self._list_page(resource, kind, namespace, options)
            items += [item for item in listed.get("items") or [] if isinstance(item, dict)]
            token = (listed.get("metadata") or {}).get("continue")
            if not isinstance(token, str) or not token:
                return items
        raise ClusterUnavailable(f"kubernetes list {resource}: more than {_LIST_PAGES} pages")

    async def _list_page(
        self, resource: Resource, kind: _Kind, namespace: str | None, options: dict[str, Any]
    ) -> JsonObject:
        api = self._apis[kind.api]
        if kind.api == "custom":
            group, version = kind.api_version.split("/")
            call = api.list_namespaced_custom_object(
                group, version, namespace, kind.suffix, **options
            )
        elif kind.namespaced:
            call = getattr(api, f"list_{kind.suffix}")(namespace, **options)
        else:
            call = getattr(api, f"list_{kind.suffix}")(**options)
        try:
            return await self._send(call)
        except ApiException as e:
            raise _failure("list", resource, e) from None
        except TimeoutError, aiohttp.ClientError, OSError:
            raise ClusterUnavailable(f"kubernetes list {resource}: no answer") from None

    async def _send(self, call: Any) -> JsonObject:
        async with asyncio.timeout(self._timeout):
            result = await call
        plain = self._client.sanitize_for_serialization(result)
        return plain if isinstance(plain, dict) else {}


_TYPED_PATCH_REFUSAL = "failed to create typed patch object"


def _body_of(e: ApiException) -> JsonObject:
    try:
        body = json.loads(e.body) if e.body else {}
    except ValueError, TypeError:
        return {}
    return body if isinstance(body, dict) else {}


def _status_of(e: ApiException) -> tuple[JsonObject, list[Any]]:
    """The details and causes of a ``Status`` body; never carried into a message."""
    body = _body_of(e)
    details = body.get("details")
    if not isinstance(details, dict):
        return {}, []
    causes = details.get("causes")
    return details, causes if isinstance(causes, list) else []


def _message(op: str, resource: Resource, e: ApiException) -> str:
    return f"kubernetes {op} {resource}: HTTP {int(e.status or 0)}"


def _failure(op: str, resource: Resource, e: ApiException) -> Exception:
    message = _message(op, resource, e)
    code = int(e.status or 0)
    if code == 404:
        return KubernetesNotFound(message)
    if code == 409:
        return KubernetesConflict(message)
    if code == 403:
        details, causes = _status_of(e)
        reasons = {c.get("reason") for c in causes if isinstance(c, dict)}
        if "NamespaceTerminating" in reasons:
            return KubernetesForbidden(message, namespace_terminating=True)
        if details.get("kind") and not causes:
            return KubernetesForbidden(message)
        return ClusterRefused(message)
    if code in (400, 422):
        return ClusterRefused(message)
    status_message = _body_of(e).get("message")
    if (
        op == "apply"
        and code == 500
        and isinstance(status_message, str)
        and status_message.startswith(_TYPED_PATCH_REFUSAL)
    ):
        return ClusterRefused(message)
    return ClusterUnavailable(message)


def _check_settings(field_manager: str, request_timeout_seconds: float) -> None:
    if not _FIELD_MANAGER.fullmatch(field_manager):
        raise ValueError("the field manager must be a lowercase name of 1 to 63 characters")
    if not request_timeout_seconds > 0:
        raise ValueError("the request timeout must be positive")


def _named(entries: Any, name: Any) -> JsonObject | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return None


def _checked_kubeconfig(path: Path) -> tuple[str, str]:
    """Read the kubeconfig once and return its current context and server, or refuse it."""
    text = str(path)
    if not path.is_absolute() or os.pathsep in text or not path.is_file():
        raise KubeconfigRefused("the kubeconfig must be one existing file named by absolute path")
    try:
        raw = KubeConfigMerger(text).config_files.get(text)
    except Exception:
        # Any parse failure is a refusal; the parser's text may quote the file, so it is dropped.
        raise KubeconfigRefused("the kubeconfig cannot be read") from None
    if not isinstance(raw, dict):
        raise KubeconfigRefused("the kubeconfig cannot be read")
    current = raw.get("current-context")
    if not isinstance(current, str) or not _LOCAL_CONTEXT.fullmatch(current):
        raise KubeconfigRefused(
            "the current context is not a kind-webblock-* or k3d-webblock-* context"
        )
    context = (_named(raw.get("contexts"), current) or {}).get("context")
    if not isinstance(context, dict):
        raise KubeconfigRefused("the current context is not defined")
    cluster = (_named(raw.get("clusters"), context.get("cluster")) or {}).get("cluster")
    if not isinstance(cluster, dict):
        raise KubeconfigRefused("the current context names no cluster")
    server = cluster.get("server")
    if not isinstance(server, str):
        raise KubeconfigRefused("the server is not https://127.0.0.1:<port>")
    match = _LOCAL_SERVER.fullmatch(server)
    if match is None or not 1 <= int(match.group(1)) <= 65535:
        raise KubeconfigRefused("the server is not https://127.0.0.1:<port>")
    if "proxy-url" in cluster:
        raise KubeconfigRefused("the cluster names a proxy")
    user = (_named(raw.get("users"), context.get("user")) or {}).get("user") or {}
    if not isinstance(user, dict) or "exec" in user or "auth-provider" in user:
        raise KubeconfigRefused("the user must authenticate by token or client certificate")
    return current, server


def _configuration() -> Any:
    configuration = k8s.Configuration()
    # The adapter reads plain JSON from the typed answers; a partial object must not fail
    # deserialisation on the client side.
    configuration.client_side_validation = False
    return configuration


async def connect_kubeconfig(
    path: Path, *, field_manager: str, request_timeout_seconds: float
) -> KubeApi:
    """A client from an explicit project-local kubeconfig for a Kind or k3d cluster on
    127.0.0.1."""
    _check_settings(field_manager, request_timeout_seconds)
    context, server = _checked_kubeconfig(path)
    configuration = _configuration()
    try:
        await load_kube_config(
            config_file=str(path),
            context=context,
            client_configuration=configuration,
            persist_config=False,
        )
    except Exception:
        # The loader's text may quote the file (a token, a key); refuse without it.
        raise KubeconfigRefused("the kubeconfig cannot be loaded") from None
    if configuration.host != server.rstrip("/"):
        raise KubeconfigRefused("the loaded server is not the checked one")
    return KubeApi(
        k8s.ApiClient(configuration=configuration), field_manager, request_timeout_seconds
    )


async def connect_in_cluster(
    *,
    token_file: Path,
    ca_file: Path,
    expected_username: str,
    field_manager: str,
    request_timeout_seconds: float,
) -> KubeApi:
    """A client from a pod: the projected token ``token_file`` and the cluster's CA ``ca_file``
    (both existing files named by absolute path), the API server from KUBERNETES_SERVICE_HOST and
    KUBERNETES_SERVICE_PORT. One SelfSubjectReview must return ``expected_username`` before the
    client is returned; ``KubeconfigRefused`` otherwise, and the client is closed."""
    _check_settings(field_manager, request_timeout_seconds)
    for path in (token_file, ca_file):
        if not path.is_absolute() or not path.is_file():
            raise KubeconfigRefused(
                "the token and CA files must be existing files named by absolute path"
            )
    configuration = _configuration()
    try:
        load_incluster_config(
            client_configuration=configuration,
            token_filename=str(token_file),
            cert_filename=str(ca_file),
            try_refresh_token=True,
        )
        api_client = k8s.ApiClient(configuration=configuration)
    except ConfigException:
        raise KubeconfigRefused("not running in a cluster") from None
    except OSError, UnicodeDecodeError:  # ssl.SSLError is an OSError
        # The operating system's or OpenSSL's text may quote a path; refuse without it.
        raise KubeconfigRefused("the token or CA file cannot be read") from None
    try:
        if await _reviewed_username(api_client, request_timeout_seconds) != expected_username:
            raise KubeconfigRefused("the token is not the expected ServiceAccount's")
    except BaseException:
        await api_client.close()
        raise
    return KubeApi(api_client, field_manager, request_timeout_seconds)


async def _reviewed_username(api_client: Any, seconds: float) -> str:
    """The username a SelfSubjectReview returns for this client's token ("" when none)."""
    api = k8s.AuthenticationV1Api(api_client)
    try:
        async with asyncio.timeout(seconds):
            answer = await api.create_self_subject_review(
                k8s.V1SelfSubjectReview(
                    api_version="authentication.k8s.io/v1", kind="SelfSubjectReview"
                ),
                _request_timeout=seconds,
            )
    except ApiException as e:
        if e.status in (401, 403):
            raise KubeconfigRefused(f"the token is refused: HTTP {int(e.status)}") from None
        raise ClusterUnavailable(
            f"kubernetes self-subject review: HTTP {int(e.status or 0)}"
        ) from None
    except TimeoutError, aiohttp.ClientError, OSError:
        raise ClusterUnavailable("kubernetes self-subject review: no answer") from None
    plain = api_client.sanitize_for_serialization(answer)
    status = plain.get("status") if isinstance(plain, dict) else None
    user = status.get("userInfo") if isinstance(status, dict) else None
    username = user.get("username") if isinstance(user, dict) else None
    return username if isinstance(username, str) else ""
