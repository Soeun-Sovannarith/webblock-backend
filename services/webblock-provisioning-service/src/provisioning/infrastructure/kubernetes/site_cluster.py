"""``SiteCluster`` over Kubernetes, with the release worker's identity (decision 0013 section 3,
decision 0014 section 3).

Fence. Before every apply and delete the adapter reads the object and refuses (``FenceRejected``)
when its ``websites.webblock.internal/command-number`` is higher than the command's, or is not a
number. The same or a lower number lets the write proceed. Without the admission policies of
``0013`` (not installed in the prototype, ``0014`` section 5) this check is the only fence, and a
write racing between the read and the apply is not caught.

Acknowledgement (``release_status(workload)``, review-k8s-nocp.md M1, revised port). No
Deployment is MISSING. A fence annotation above ``workload.command_number`` (or not a number)
raises ``FenceRejected``. The Deployment is this workload only when its fence annotation equals
the command's number, its container image is ``workload.image``, and its pod template's intent
digest equals the one rendered from the workload and the configured resources; otherwise our apply
is not visible yet and the state is PROGRESSING. For this workload, READY needs, in one read:
``observedGeneration == generation``; updated, ready and available replicas and
``status.replicas`` all equal to ``spec.replicas``, and no ``unavailableReplicas``; and at least
that many (and at least one) endpoints with ``ready=true`` in the Service's EndpointSlices.
FAILED, with a short reason: ``progress_deadline_exceeded`` (only at the current generation),
``image_pull_failed`` or ``crash_loop`` from a container of the release's pods. Anything else is
PROGRESSING.

Restart (decision 0023 G12). ``restart_release`` is ``apply_release`` with the pod template's
``websites.webblock.internal/restart-command`` set to the command's number: a redeploy's rolling
restart of the live release, with the same image. ``release_status`` at that number then tells
when the rollout is done (the new generation observed, every replica updated, ready and available,
none unavailable, the old pods gone).

Precise causes (task H5a, decision 0015 section 4 step 1). Two causes of a candidate that never
becomes ready have codes of their own, ``quota_exceeded`` (the Deployment's ``ReplicaFailure``
condition, which the controller copies from the ReplicaSet, is ``True`` with reason
``FailedCreate`` and its message contains ``exceeded quota``) and ``insufficient_resources`` (a pod
of the release has ``PodScheduled`` ``False`` with reason ``Unschedulable``); the quota wins when
both are present. At the progress deadline the precise cause replaces
``progress_deadline_exceeded`` as the FAILED reason. Before it, the state stays PROGRESSING and the
cause is its ``reason``, a diagnosis the deploy uses only if the attempt times out. Only the
condition's reason and the presence of the phrase are read: no message text leaves this module.

Routes (decision 0014 section 17). ``route-<projectId>`` and ``preview-<projectId>`` are applied
after the same fence check. Their status names a target only when the rule's backend and its
``X-Webblock-Release`` header filter agree, so a route without the header is never confirmed; a
preview route counts only while it matches the project's own probe token and removes that header
before the site (recheck N1; decision 0023 G18c). A route is
accepted only when our Gateway reports ``Accepted`` and ``ResolvedRefs`` for it at its current
generation. The probe (``probe_site``) sends ``GET <probe path>`` over HTTPS to the Gateway with the
hostname as TLS server name and Host, the project's preview token header only when the caller
names the project's sub-domain and the host is exactly its preview host (FR-52.1), and no
connection reuse, and
succeeds only on 200 with exactly one ``X-Webblock-Release`` value equal to the expected release ID,
or ``offline`` (a 503 with exactly the expected release ID raises ``SiteDependencyUnavailable`` when
that release's Service has a ready endpoint, read from its EndpointSlices: Envoy's own 503 for a
backend without one carries the route's header too; task H5a): the Gateway sets that header from the
route, so it names the backend that answered. The body is streamed and abandoned past
``probe_max_body_bytes`` (a failed probe, QA-01), and a probe needs ``probe_successes`` good answers
in a row, ``probe_success_gap_seconds`` apart (QA-08).

Wave 6 (task W6-M). ``list_tenant_projects`` lists the namespaces labelled tenant (the worker's one
cluster-wide grant) and keeps those named ``wb-<canonical UUID>``. ``inventory`` lists the
Deployments, Services and HTTPRoutes of ``wb-<projectId>`` with their fence and the field managers
other than the worker's in ``metadata.managedFields``; entries for a subresource (status) are not
counted, nor the Deployment controller's own revision annotation. ``reclaim_route`` is the only
forced server-side apply in this package (decision 0014 section 23 item 3): it renders the route as
``apply_route`` does and writes it with ``force=true``, without the fence check, so it takes the
route's fields back from a foreign manager and re-stamps the fence.

Guarded delete (decision 0011 section 12, F2; review-k8s-nocp.md M2). Each pass reads the route
and refuses the release it points at, then deletes the Deployment and then the Service, each after
the fence check and with UID and resourceVersion preconditions. A lost precondition (409) starts
the next pass after ``delete_retry_base_seconds`` times the pass number; after five passes the
delete ends with ``ClusterUnavailable``: retry later.

Errors. A 403 from authorization (the worker's RoleBinding is missing), a terminating namespace,
or a 404 on apply (the namespace is missing) raise ``TenantNotReady``. ``ApplyConflict``,
``ClusterRefused`` (400, 422, admission 403), ``ClusterUnavailable`` and ``FenceRejected`` come
through as the port names them.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx2

from provisioning.core.application.ports.site_cluster import (
    OFFLINE_HEADER_VALUE,
    PREVIEW_HEADER,
    RELEASE_HEADER,
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    RouteStatus,
    RouteTarget,
    RouteTargetKind,
    SiteDependencyUnavailable,
    SiteInventory,
    SiteObject,
    SiteObjectKind,
    TenantNotReady,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.core.application.routing.background.routing import preview_hostname
from provisioning.core.domain import CODE_INSUFFICIENT_RESOURCES, CODE_QUOTA_EXCEEDED
from provisioning.infrastructure.kubernetes._client import (
    JsonObject,
    KubeApi,
    KubernetesConflict,
    KubernetesForbidden,
    KubernetesNotFound,
    Resource,
)
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_COMMAND_NUMBER,
    ANNOTATION_INTENT_DIGEST,
    CONTAINER_NAME,
    GATEWAY_GROUP,
    GATEWAY_NAME,
    GATEWAY_NAMESPACE,
    LABEL_TENANT,
    OFFLINE_NAMESPACE,
    OFFLINE_SERVICE,
    InvalidManifestInput,
    SiteSettings,
    is_hostname,
    preview_header_removal,
    preview_match,
    preview_response_filter,
    preview_route_name,
    preview_token_for,
    release_header_filter,
    release_intent_digest,
    release_name,
    release_selector,
    render_deployment,
    render_preview_route,
    render_route,
    render_service,
    route_name,
    tenant_namespace,
)

MAX_DELETE_PASSES = 5

_NUMBER = re.compile(r"[1-9][0-9]{0,15}")
_IMAGE_PULL_REASONS = frozenset(
    {"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull"}
)
_CRASH_REASONS = frozenset({"CrashLoopBackOff"})
_QUOTA_PHRASE = "exceeded quota"
# One or more "/segment" parts of unreserved characters: no "//", query, fragment or space.
_PROBE_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)+", re.ASCII)
_PREVIEW_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}")


@dataclass(frozen=True, slots=True)
class SiteClusterSettings:
    site: SiteSettings
    # Where the Gateway's HTTPS listeners answer, for example https://172.18.255.200; the probe
    # connects here and names the site's hostname as the TLS server name and Host.
    gateway_url: str
    # The promotion probe's path, /readyz in the prototype (decision 0014 section 15): 200 only
    # when the site's dependencies work.
    probe_path: str
    probe_timeout_seconds: float
    delete_retry_base_seconds: float  # 0.25 in service (F2); 0 in unit tests
    site_domain: str  # sites.webblock.test in the prototype (OD-36)
    # PREVIEW_PROBE_TOKEN, the secret of the preview tokens: each project's preview route
    # matches, and its probe sends, only preview_token_for(this, projectId) (recheck N1;
    # decision 0023 G18c). Never logged, never sent.
    preview_token: str = field(repr=False)
    # How many consecutive good answers a probe needs (QA-08), and the most bytes of a probe's
    # body it reads (QA-01); SITE_PROBE_SUCCESSES and SITE_PROBE_MAX_BODY_BYTES.
    probe_successes: int = 1
    probe_max_body_bytes: int = 65536
    # The pause between two answers of one probe (QA-08), so that the answers sample the site
    # over time and not one instant. 0 to 10 seconds; no configuration key yet.
    probe_success_gap_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not self.gateway_url.startswith(("http://", "https://")):
            raise ValueError("the gateway URL must be http or https")
        if _PROBE_PATH.fullmatch(self.probe_path) is None or "/../" in self.probe_path + "/":
            raise ValueError("the probe path must be an absolute path without query or fragment")
        if not self.probe_timeout_seconds > 0 or self.delete_retry_base_seconds < 0:
            raise ValueError("the probe timeout must be positive and the retry base not negative")
        if not is_hostname(self.site_domain):
            raise ValueError("the site domain must be a lowercase DNS name")
        if _PREVIEW_TOKEN.fullmatch(self.preview_token) is None:
            raise ValueError("the preview token must be 32 to 256 URL-safe characters")
        if not 1 <= self.probe_successes <= 10 or self.probe_max_body_bytes < 1:
            raise ValueError("the probe needs 1 to 10 successes and a positive body bound")
        if not 0 <= self.probe_success_gap_seconds <= 10:
            raise ValueError("the probe's gap between answers must be 0 to 10 seconds")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _command_number(obj: Mapping[str, Any]) -> int | None:
    """The stored command number; None when absent; -1 when it is not a number."""
    raw = _mapping(_mapping(obj.get("metadata")).get("annotations")).get(ANNOTATION_COMMAND_NUMBER)
    if raw is None:
        return None
    return int(raw) if isinstance(raw, str) and _NUMBER.fullmatch(raw) else -1


def _check_fence(obj: JsonObject | None, command_number: int) -> None:
    if obj is None:
        return
    stored = _command_number(obj)
    if stored is not None and (stored < 0 or stored > command_number):
        raise FenceRejected("the object carries a higher or malformed command number")


def _header_value(target: RouteTarget) -> str:
    return OFFLINE_HEADER_VALUE if target.release_id is None else str(target.release_id)


def _route_target(route: Mapping[str, Any], *, preview: bool = False) -> RouteTarget | None:
    """The backend of the route's one rule when that rule also sets the release header to it,
    and, on the preview route, then removes the probe's header (decision 0023 G18c)."""
    target = _backend_target(route)
    if target is None:
        return None
    rules = _sequence(_mapping(route.get("spec")).get("rules"))
    filters = _sequence(_mapping(rules[0]).get("filters"))
    if preview:
        want = [preview_response_filter(_header_value(target)), preview_header_removal()]
    else:
        want = [release_header_filter(_header_value(target))]
    return target if filters == want else None


def _backend_target(route: Mapping[str, Any]) -> RouteTarget | None:
    """Where the route's first rule sends traffic, whatever its filters."""
    rules = _sequence(_mapping(route.get("spec")).get("rules"))
    refs = _sequence(_mapping(rules[0]).get("backendRefs")) if rules else []
    if not refs:
        return None
    ref = _mapping(refs[0])
    name = ref.get("name")
    namespace = ref.get("namespace")
    if namespace == OFFLINE_NAMESPACE and name == OFFLINE_SERVICE:
        return RouteTarget(RouteTargetKind.OFFLINE)
    if namespace is None and isinstance(name, str) and name.startswith("rel-"):
        try:
            return RouteTarget(RouteTargetKind.RELEASE, UUID(name.removeprefix("rel-")))
        except ValueError:
            return None
    return None


def _accepted(route: Mapping[str, Any]) -> bool:
    generation = _mapping(route.get("metadata")).get("generation")
    for parent in _sequence(_mapping(route.get("status")).get("parents")):
        ref = _mapping(_mapping(parent).get("parentRef"))
        if (
            ref.get("group", GATEWAY_GROUP) != GATEWAY_GROUP
            or ref.get("namespace") != GATEWAY_NAMESPACE
            or ref.get("name") != GATEWAY_NAME
        ):
            continue
        current = {
            c.get("type"): c.get("status")
            for c in map(_mapping, _sequence(_mapping(parent).get("conditions")))
            if c.get("observedGeneration") == generation
        }
        if current.get("Accepted") == "True" and current.get("ResolvedRefs") == "True":
            return True
    return False


def _matches_token(route: Mapping[str, Any], token: str) -> bool:
    """The preview route's one rule matches exactly the probe's token header."""
    rules = _sequence(_mapping(route.get("spec")).get("rules"))
    if not rules:
        return False
    want = preview_match(token)["headers"]
    return all(
        _sequence(_mapping(m).get("headers")) == want
        for m in _sequence(_mapping(rules[0]).get("matches")) or [{}]
    )


def _pod_failure(pods: list[JsonObject]) -> str | None:
    for pod in pods:
        for container in _sequence(_mapping(pod.get("status")).get("containerStatuses")):
            reason = _mapping(_mapping(_mapping(container).get("state")).get("waiting")).get(
                "reason"
            )
            if reason in _IMAGE_PULL_REASONS:
                return "image_pull_failed"
            if reason in _CRASH_REASONS:
                return "crash_loop"
    return None


def _quota_refused(status: Mapping[str, Any]) -> bool:
    """The Deployment's ReplicaFailure condition is FailedCreate by a ResourceQuota. Only the
    reason and the presence of the phrase are read; the message is never kept."""
    return any(
        c.get("type") == "ReplicaFailure"
        and c.get("status") == "True"
        and c.get("reason") == "FailedCreate"
        and isinstance(c.get("message"), str)
        and _QUOTA_PHRASE in c["message"]
        for c in map(_mapping, _sequence(status.get("conditions")))
    )


def _unschedulable(pods: list[JsonObject]) -> bool:
    """A pod of the release that the scheduler cannot place (PodScheduled False, Unschedulable)."""
    return any(
        c.get("type") == "PodScheduled"
        and c.get("status") == "False"
        and c.get("reason") == "Unschedulable"
        for pod in pods
        for c in map(_mapping, _sequence(_mapping(pod.get("status")).get("conditions")))
    )


def _diagnosis(status: Mapping[str, Any], pods: list[JsonObject]) -> str | None:
    """The precise cause of a candidate that does not progress, the quota first; None without
    evidence."""
    if _quota_refused(status):
        return CODE_QUOTA_EXCEEDED
    if _unschedulable(pods):
        return CODE_INSUFFICIENT_RESOURCES
    return None


def _ready_endpoints(slices: list[JsonObject]) -> int:
    return sum(
        1
        for s in slices
        for endpoint in map(_mapping, _sequence(s.get("endpoints")))
        if _mapping(endpoint.get("conditions")).get("ready") is True
    )


@asynccontextmanager
async def _tenant_required() -> AsyncIterator[None]:
    """A missing namespace or RoleBinding, or a terminating namespace, is TenantNotReady."""
    try:
        yield
    except KubernetesForbidden, KubernetesNotFound:
        raise TenantNotReady(
            "the tenant namespace or the worker's access to it is missing"
        ) from None


class KubernetesSiteCluster:
    """The per-site objects of decision 0013 section 3, written by the release worker."""

    def __init__(self, api: KubeApi, settings: SiteClusterSettings, http: httpx2.AsyncClient):
        self._api = api
        self._settings = settings
        self._http = http

    async def apply_release(self, workload: ReleaseWorkload) -> None:
        await self._apply_workload(workload, restart=False)

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        await self._apply_workload(workload, restart=True)

    async def _apply_workload(self, workload: ReleaseWorkload, *, restart: bool) -> None:
        """The Deployment and the Service after the fence check of both; with ``restart`` the
        pod template carries the restart annotation at the command's number."""
        deployment = render_deployment(
            workload,
            self._settings.site,
            restart_command=workload.command_number if restart else None,
        )
        service = render_service(workload)
        namespace = tenant_namespace(workload.project_id)
        name = release_name(workload.release_id)
        async with _tenant_required():
            for resource in (Resource.DEPLOYMENT, Resource.SERVICE):
                current = await self._api.read(resource, name, namespace)
                _check_fence(current, workload.command_number)
            await self._api.apply(Resource.DEPLOYMENT, deployment)
            await self._api.apply(Resource.SERVICE, service)

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        project_id, release_id = workload.project_id, workload.release_id
        namespace = tenant_namespace(project_id)
        name = release_name(release_id)
        async with _tenant_required():
            deployment = await self._api.read(Resource.DEPLOYMENT, name, namespace)
            if deployment is None:
                return WorkloadStatus(WorkloadState.MISSING)
            _check_fence(deployment, workload.command_number)
            spec = _mapping(deployment.get("spec"))
            if _command_number(deployment) != workload.command_number or not self._is_workload(
                workload, spec
            ):
                return WorkloadStatus(WorkloadState.PROGRESSING)
            pods = await self._api.list_objects(
                Resource.POD, namespace, release_selector(project_id, release_id)
            )
            slices = await self._api.list_objects(
                Resource.ENDPOINT_SLICE, namespace, f"kubernetes.io/service-name={name}"
            )
        metadata = _mapping(deployment.get("metadata"))
        status = _mapping(deployment.get("status"))
        generation = metadata.get("generation")
        current = generation is not None and status.get("observedGeneration") == generation
        diagnosis = _diagnosis(status, pods)
        if current and any(
            c.get("type") == "Progressing"
            and c.get("status") == "False"
            and c.get("reason") == "ProgressDeadlineExceeded"
            for c in map(_mapping, _sequence(status.get("conditions")))
        ):
            return WorkloadStatus(WorkloadState.FAILED, diagnosis or "progress_deadline_exceeded")
        failure = _pod_failure(pods)
        if failure is not None:
            return WorkloadStatus(WorkloadState.FAILED, failure)
        desired = spec.get("replicas")
        desired = desired if isinstance(desired, int) else 1
        replicas_done = all(
            status.get(key, 0) == desired
            for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")
        ) and not status.get("unavailableReplicas", 0)
        if current and replicas_done and _ready_endpoints(slices) >= max(1, desired):
            return WorkloadStatus(WorkloadState.READY)
        return WorkloadStatus(WorkloadState.PROGRESSING, diagnosis)

    def _is_workload(self, workload: ReleaseWorkload, spec: Mapping[str, Any]) -> bool:
        """The template runs ``workload.image`` and carries the digest rendered from it."""
        template = _mapping(spec.get("template"))
        observed = _mapping(_mapping(template.get("metadata")).get("annotations")).get(
            ANNOTATION_INTENT_DIGEST
        )
        containers = _sequence(_mapping(template.get("spec")).get("containers"))
        image = next(
            (c.get("image") for c in map(_mapping, containers) if c.get("name") == CONTAINER_NAME),
            None,
        )
        if image != workload.image:
            return False
        try:
            expected = release_intent_digest(
                workload.project_id, workload.release_id, workload.image, self._settings.site
            )
        except InvalidManifestInput:
            return False
        return observed == expected

    async def delete_release(self, project_id: UUID, release_id: UUID, command_number: int) -> None:
        for attempt in range(1, MAX_DELETE_PASSES + 1):
            try:
                async with _tenant_required():
                    await self._delete_pass(project_id, release_id, command_number)
                return
            except KubernetesConflict:
                if attempt == MAX_DELETE_PASSES:
                    raise ClusterUnavailable(
                        f"the delete lost its precondition {MAX_DELETE_PASSES} times"
                    ) from None
                await asyncio.sleep(self._settings.delete_retry_base_seconds * attempt)

    async def _delete_pass(self, project_id: UUID, release_id: UUID, command_number: int) -> None:
        namespace = tenant_namespace(project_id)
        route = await self._api.read(Resource.HTTP_ROUTE, route_name(project_id), namespace)
        if route is not None and _backend_target(route) == RouteTarget(
            RouteTargetKind.RELEASE, release_id
        ):
            raise FenceRejected("the route points at this release")
        name = release_name(release_id)
        for resource in (Resource.DEPLOYMENT, Resource.SERVICE):
            current = await self._api.read(resource, name, namespace)
            if current is None:
                continue
            _check_fence(current, command_number)
            metadata = _mapping(current.get("metadata"))
            if metadata.get("deletionTimestamp"):
                continue
            await self._api.delete(
                resource,
                name,
                namespace,
                uid=str(metadata.get("uid", "")),
                resource_version=str(metadata.get("resourceVersion", "")),
            )

    async def apply_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        route = render_route(
            project_id,
            target,
            hostnames,
            command_number,
            self._settings.site,
            site_domain=self._settings.site_domain,
            subdomain=subdomain,
        )
        namespace = tenant_namespace(project_id)
        async with _tenant_required():
            current = await self._api.read(Resource.HTTP_ROUTE, route_name(project_id), namespace)
            _check_fence(current, command_number)
            await self._api.apply(Resource.HTTP_ROUTE, route)

    async def route_status(self, project_id: UUID) -> RouteStatus:
        return await self._status_of(route_name(project_id), project_id)

    async def apply_preview(
        self,
        project_id: UUID,
        release_id: UUID,
        hostname: str,
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        route = render_preview_route(
            project_id,
            release_id,
            hostname,
            command_number,
            preview_secret=self._settings.preview_token,
            site_domain=self._settings.site_domain,
            subdomain=subdomain,
        )
        async with _tenant_required():
            current = await self._api.read(
                Resource.HTTP_ROUTE, preview_route_name(project_id), tenant_namespace(project_id)
            )
            _check_fence(current, command_number)
            await self._api.apply(Resource.HTTP_ROUTE, route)

    async def preview_status(self, project_id: UUID) -> RouteStatus:
        return await self._status_of(preview_route_name(project_id), project_id, preview=True)

    async def _status_of(
        self, name: str, project_id: UUID, *, preview: bool = False
    ) -> RouteStatus:
        async with _tenant_required():
            route = await self._api.read(Resource.HTTP_ROUTE, name, tenant_namespace(project_id))
        if route is None:
            return RouteStatus(accepted=False, target=None, command_number=None)
        number = _command_number(route)
        target = _route_target(route, preview=preview)
        if preview and not _matches_token(route, self._preview_token(project_id)):
            target = None  # a preview route without the project's token is not ours to confirm
        return RouteStatus(
            accepted=_accepted(route),
            target=target,
            command_number=number if number is not None and number > 0 else None,
        )

    def _preview_token(self, project_id: UUID) -> str:
        return preview_token_for(self._settings.preview_token, project_id)

    async def probe_site(
        self,
        project_id: UUID,
        hostname: str,
        expected: RouteTarget,
        *,
        preview_of: str | None = None,
    ) -> bool:
        if not is_hostname(hostname):
            raise ValueError("the hostname must be a lowercase DNS name")
        # Decision 0023 G18c, FR-52.1 (Task 12 security review S1, preview-host review L1): the
        # header goes only to the project's own preview host, named by its sub-domain, with this
        # project's own token; any other probe, a site host's included, goes without it.
        token: str | None = None
        if preview_of is not None:
            if hostname != preview_hostname(preview_of, self._settings.site_domain):
                raise ValueError("a preview probe goes only to the sub-domain's own preview host")
            token = self._preview_token(project_id)
        want = _header_value(expected)
        outage = expected.kind is RouteTargetKind.RELEASE
        # QA-08: every one of probe_successes answers in a row must be good, each on its own
        # connection and a gap after the previous one; the first bad answer ends the probe.
        for answer in range(self._settings.probe_successes):
            if answer:
                await asyncio.sleep(self._settings.probe_success_gap_seconds)
            named_503 = False
            try:
                if not await self._answers_once(hostname, want, outage=outage, token=token):
                    return False
            except SiteDependencyUnavailable:
                named_503 = True
            if named_503:
                # The Gateway's own 503 (no ready endpoint) carries the route's release header
                # too, so the site answered only when the release has a ready endpoint (task
                # H5a, lead's ruling B). Inconclusive is the old answer: False.
                if expected.release_id is not None and await self._has_ready_endpoint(
                    project_id, expected.release_id
                ):
                    raise SiteDependencyUnavailable
                return False
        return True

    async def _has_ready_endpoint(self, project_id: UUID, release_id: UUID) -> bool:
        """Whether Service ``rel-<releaseId>`` of the project has a ready endpoint, read from
        its EndpointSlices with the worker's own access. A failed read or a timeout is False."""
        try:
            async with asyncio.timeout(self._settings.probe_timeout_seconds):
                slices = await self._api.list_objects(
                    Resource.ENDPOINT_SLICE,
                    tenant_namespace(project_id),
                    f"kubernetes.io/service-name={release_name(release_id)}",
                )
        except (
            KubernetesForbidden,
            KubernetesNotFound,
            KubernetesConflict,
            ClusterRefused,
            ClusterUnavailable,
            TenantNotReady,
            TimeoutError,
        ):
            return False
        return _ready_endpoints(slices) > 0

    async def _answers_once(
        self, hostname: str, want: str, *, outage: bool, token: str | None
    ) -> bool:
        """One ``GET <probe path>``: 200, exactly one release header equal to ``want``, and a
        body of at most ``probe_max_body_bytes``, read as it arrives and abandoned at the bound
        (QA-01), so a site's answer never fills the worker's memory. With ``outage`` (a release
        is expected), a 503 with exactly that one header raises ``SiteDependencyUnavailable``
        (task H5a); its body is not read."""
        timeout = self._settings.probe_timeout_seconds
        bound = self._settings.probe_max_body_bytes
        headers = {
            "Host": hostname,
            # One connection per answer: a reused TLS connection would carry the server name of
            # an earlier probe, and an answer of a moment ago.
            "Connection": "close",
            # The bound counts the bytes on the wire; nothing is ever inflated.
            "Accept-Encoding": "identity",
        }
        if token is not None:
            headers[PREVIEW_HEADER] = token
        try:
            async with (
                asyncio.timeout(timeout * 2),
                self._http.stream(
                    "GET",
                    self._settings.gateway_url.rstrip("/") + self._settings.probe_path,
                    headers=headers,
                    extensions={"sni_hostname": hostname},
                    timeout=httpx2.Timeout(timeout),
                    follow_redirects=False,
                ) as response,
            ):
                # Decision 0014 section 15: only 200 on the readiness path means the site works;
                # section 17: and only the expected backend, named by the header the Gateway
                # sets, counts.
                named = response.headers.get_list(RELEASE_HEADER) == [want]
                if outage and response.status_code == 503 and named:
                    raise SiteDependencyUnavailable
                if response.status_code != 200 or not named:
                    return False
                declared = response.headers.get("Content-Length")
                if declared is not None and (not declared.isdigit() or int(declared) > bound):
                    return False
                if response.is_stream_consumed:
                    # A transport that hands over a body it already holds (in-memory transports
                    # do); a network answer is always a stream and is read below.
                    return len(response.content) <= bound
                read = 0
                async for chunk in response.aiter_raw():
                    read += len(chunk)
                    if read > bound:
                        return False
                return True
        except httpx2.HTTPError, TimeoutError:
            return False

    # --- the reconciler's reads and the operator rollback's reclaim (wave 6, task W6-M) ---------

    async def list_tenant_projects(self) -> tuple[UUID, ...]:
        try:
            namespaces = await self._api.list_objects(
                Resource.NAMESPACE, None, f"{LABEL_TENANT}=true"
            )
        except KubernetesForbidden, KubernetesNotFound:
            # The worker's one cluster-wide grant (list namespaces) is missing.
            raise ClusterUnavailable("the worker may not list the tenant namespaces") from None
        projects = {
            project
            for name in (_mapping(n.get("metadata")).get("name") for n in namespaces)
            if (project := _uuid_after(name, "wb-")) is not None
        }
        return tuple(sorted(projects))

    async def inventory(self, project_id: UUID) -> SiteInventory:
        namespace = tenant_namespace(project_id)
        objects: list[SiteObject] = []
        async with _tenant_required():
            for kind, resource in _INVENTORY:
                listed = await self._api.list_objects(resource, namespace)
                objects += sorted(
                    (self._site_object(kind, obj) for obj in listed), key=lambda o: o.name
                )
        return SiteInventory(project_id, tuple(objects))

    def _site_object(self, kind: SiteObjectKind, obj: Mapping[str, Any]) -> SiteObject:
        metadata = _mapping(obj.get("metadata"))
        name = metadata.get("name")
        name = name if isinstance(name, str) else ""
        release = None if kind is SiteObjectKind.HTTP_ROUTE else _uuid_after(name, "rel-")
        return SiteObject(
            kind=kind,
            name=name,
            release_id=release,
            command_number=_command_number(obj),
            foreign_managers=_foreign_managers(metadata, self._api.field_manager),
        )

    async def reclaim_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        route = render_route(
            project_id,
            target,
            hostnames,
            command_number,
            self._settings.site,
            site_domain=self._settings.site_domain,
            subdomain=subdomain,
        )
        async with _tenant_required():
            try:
                # The one forced apply (decision 0014 section 23 item 3): no fence check first,
                # and the route's fields are taken from any other field manager.
                await self._api.apply(Resource.HTTP_ROUTE, route, force=True)
            except ApplyConflict:
                # A forced apply has no field conflict to report; a 409 here is the API
                # server's own retryable conflict.
                raise ClusterUnavailable("the forced apply of the route met a conflict") from None


_INVENTORY = (
    (SiteObjectKind.DEPLOYMENT, Resource.DEPLOYMENT),
    (SiteObjectKind.SERVICE, Resource.SERVICE),
    (SiteObjectKind.HTTP_ROUTE, Resource.HTTP_ROUTE),
)
# Fields the Kubernetes controllers write on the object itself, not a subresource, as part of their
# normal work: an entry that owns nothing else is not a foreign writer.
_CONTROLLER_ANNOTATIONS = frozenset({"f:deployment.kubernetes.io/revision"})


def _uuid_after(name: Any, prefix: str) -> UUID | None:
    """The UUID of ``<prefix><uuid>`` in its canonical lowercase form, else None."""
    if not isinstance(name, str) or not name.startswith(prefix):
        return None
    text = name.removeprefix(prefix)
    try:
        value = UUID(text)
    except ValueError:
        return None
    return value if str(value) == text else None


def _only_controller_annotations(fields: Any) -> bool:
    """``fieldsV1`` that owns nothing but controller bookkeeping annotations."""
    if not isinstance(fields, Mapping) or set(fields) != {"f:metadata"}:
        return False
    metadata = fields["f:metadata"]
    if not isinstance(metadata, Mapping) or set(metadata) != {"f:annotations"}:
        return False
    owned = metadata["f:annotations"]
    return isinstance(owned, Mapping) and bool(
        set(owned) - {"."} and set(owned) - {"."} <= _CONTROLLER_ANNOTATIONS
    )


def _foreign_managers(metadata: Mapping[str, Any], own: str) -> tuple[str, ...]:
    """The field managers other than ``own`` that own a field of the object itself: entries for a
    subresource (status) and the controllers' bookkeeping annotations are left out."""
    found: set[str] = set()
    for entry in map(_mapping, _sequence(metadata.get("managedFields"))):
        manager = entry.get("manager")
        if not isinstance(manager, str) or manager == own or entry.get("subresource"):
            continue
        if _only_controller_annotations(entry.get("fieldsV1")):
            continue
        found.add(manager)
    return tuple(sorted(found))
