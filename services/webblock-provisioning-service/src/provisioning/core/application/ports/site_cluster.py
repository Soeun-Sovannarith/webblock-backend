"""The cluster ports: per-site objects and tenant bootstrap (decisions 0013 section 3 and 0014).

The release worker owns ``SiteCluster``: Deployment and Service ``rel-<releaseId>`` and HTTPRoute
``route-<projectId>`` in namespace ``wb-<projectId>``, written by server-side apply under field
manager ``webblock-provisioning-worker`` with ``force=false`` and strict field validation, each
stamped with the command's number in the annotation ``websites.webblock.internal/command-number``.
It also owns HTTPRoute ``preview-<projectId>``, through which a promotion probes the candidate
before the site's route moves (decision 0014 section 17). Every rule of both routes sets the
response header ``X-Webblock-Release`` to the backend's release ID, or ``offline``, so a probe
knows which backend the Gateway used.
The bootstrap component owns ``TenantBootstrap`` and runs with its own identity. Names derive only
from the IDs passed here (decision 0009 section 3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

RELEASE_HEADER = "X-Webblock-Release"
"""Set by the platform on every route rule, never by generated code: the release ID of the
backend that answered, or ``offline`` (decision 0014 section 17)."""
OFFLINE_HEADER_VALUE = "offline"
PREVIEW_HEADER = "X-Webblock-Preview"
"""The preview route answers only requests that carry this header with the project's own probe
token (task T, recheck finding N1; decision 0023 G18c). ``probe_site`` sends it only when the
caller names the project's sub-domain and the host is its preview host
``<subdomain>-preview.<site domain>`` (FR-20, FR-52.1), never to a site host, and the preview
route removes it before the request reaches the site (FR-52.2)."""


@dataclass(frozen=True, slots=True)
class ReleaseWorkload:
    """One release's Deployment and Service. ``image`` is ``<repository>@sha256:<digest>``."""

    project_id: UUID
    release_id: UUID
    image: str
    command_number: int


class WorkloadState(StrEnum):
    PROGRESSING = "progressing"
    # READY: current generation observed, intent matches, every replica updated, ready and
    # available, old replicas gone, and ready endpoints behind the Service.
    READY = "ready"
    FAILED = "failed"  # ProgressDeadlineExceeded, image pull failure, or crash loop
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class WorkloadStatus:
    state: WorkloadState
    # A short machine code. For FAILED, the failure, for example "image_pull_failed". For
    # PROGRESSING, an optional diagnosis (insufficient_resources, quota_exceeded) that the attempt
    # ends with if it times out; it never ends the attempt early (task H5a). None otherwise.
    reason: str | None = None


class RouteTargetKind(StrEnum):
    RELEASE = "release"
    OFFLINE = (
        "offline"  # the shared offline page: suspended hosting, or a first publish that failed
    )


@dataclass(frozen=True, slots=True)
class RouteTarget:
    kind: RouteTargetKind
    release_id: UUID | None = None  # set exactly when kind is RELEASE


@dataclass(frozen=True, slots=True)
class RouteStatus:
    """``accepted`` is the parent status Accepted and ResolvedRefs from our Gateway at the route's
    current generation. ``target`` is what the route points at now, or None without a route."""

    accepted: bool
    target: RouteTarget | None
    command_number: int | None


class ClusterUnavailable(Exception):
    """API server unreachable, timed out, or answered 5xx/429. Transient."""


class TenantNotReady(Exception):
    """The tenant namespace or the worker's access to it does not exist yet. Transient."""


class ApplyConflict(Exception):
    """Another field manager owns a field we apply (HTTP 409 on apply). Not transient."""


class FenceRejected(Exception):
    """The object carries a higher command number: a newer command owns it. Not transient."""


class ClusterRefused(Exception):
    """The API server refused the object: validation (400, 422) or admission (403 with causes).
    Not transient: the same request will be refused again."""


# What a guarded ``delete_release`` raises, besides a bug, under the caller's time bound: the
# caller logs or defers these and lets anything else propagate.
DELETE_ERRORS: Final = (
    ClusterUnavailable,
    TenantNotReady,
    FenceRejected,
    ApplyConflict,
    ClusterRefused,
    TimeoutError,
)


class SiteDependencyUnavailable(Exception):
    """The expected backend answered the probe with 503 (its readiness reports a dependency down,
    decision 0014 section 15). Raised by ``probe_site`` only when the answer's
    ``X-Webblock-Release`` names exactly the expected release and that release has a ready
    endpoint (the Gateway's own 503 for a backend without one carries the header too); it carries
    no text from the site (task H5a, decision 0015 section 4 step 1)."""

    def __init__(self) -> None:
        super().__init__("the expected backend answered 503")


class SiteObjectKind(StrEnum):
    """The kinds of per-site object the worker owns in a tenant namespace."""

    DEPLOYMENT = "Deployment"
    SERVICE = "Service"
    HTTP_ROUTE = "HTTPRoute"


@dataclass(frozen=True, slots=True)
class SiteObject:
    """One per-site object as the cluster holds it, for the reconciler and cleanup.

    ``release_id`` is the release a ``rel-<releaseId>`` Deployment or Service belongs to, and None
    for a route or a name that is not ``rel-<uuid>``. ``command_number`` is the fence annotation:
    None when it is absent, ``-1`` when it is not a number. ``foreign_managers`` are the field
    managers in ``metadata.managedFields`` other than the worker's own, sorted and without
    duplicates; an empty tuple means only the worker wrote the object."""

    kind: SiteObjectKind
    name: str
    release_id: UUID | None
    command_number: int | None
    foreign_managers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SiteInventory:
    """Every per-site object of one project's tenant namespace, in no particular order."""

    project_id: UUID
    objects: tuple[SiteObject, ...]


class SiteCluster(Protocol):
    async def apply_release(self, workload: ReleaseWorkload) -> None: ...

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        """A redeploy's rolling restart of a release (decision 0023 G12): ``apply_release`` with
        one more annotation on the Deployment's pod template,
        ``websites.webblock.internal/restart-command``, set to ``workload.command_number``. A new
        value rolls the pods with the same image; the same value again changes nothing. The
        Service is applied too, unchanged but for the fence, so a missing one comes back. Same
        fence check, field manager and errors as ``apply_release``; never forced."""
        ...

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        """READY only when the observed objects are this workload: the image is ``workload.image``,
        the intent digest is the one rendered from it, and the fence annotation equals
        ``workload.command_number``, besides the readiness facts of WorkloadState.READY. A higher
        fence raises FenceRejected; a lower one or another image is PROGRESSING (our apply is not
        visible yet)."""
        ...

    async def delete_release(self, project_id: UUID, release_id: UUID, command_number: int) -> None:
        """Delete a candidate's Deployment and Service. Refuses (FenceRejected) the release the
        route points at. Missing objects are not an error. When the resourceVersion precondition
        keeps losing to status writes (five passes), raises ClusterUnavailable: retry later."""
        ...

    async def apply_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        """Point HTTPRoute ``route-<projectId>`` (``hostnames``) at ``target``, stamped with
        ``command_number`` and refused by a higher one (``FenceRejected``). ``subdomain`` is the
        project's ``project_hosting.subdomain``: under the site domain the route names only
        ``<subdomain>.<site domain>`` (decision 0023 G18), anything else is refused (a
        ``ValueError``) before anything is written."""
        ...

    async def route_status(self, project_id: UUID) -> RouteStatus: ...

    async def apply_preview(
        self,
        project_id: UUID,
        release_id: UUID,
        hostname: str,
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        """Point HTTPRoute ``preview-<projectId>`` (the one ``hostname``) at ``rel-<releaseId>``,
        stamped with ``command_number`` and refused by a higher one (``FenceRejected``), like the
        site's route. The worker re-points it for each candidate and never deletes it.
        ``hostname`` must be ``<subdomain>-preview.<site domain>`` with the project's
        ``project_hosting.subdomain`` (FR-20); anything else is refused (a
        ``ValueError``) before anything is written."""
        ...

    async def preview_status(self, project_id: UUID) -> RouteStatus:
        """As ``route_status``, for ``preview-<projectId>``."""
        ...

    async def probe_site(
        self,
        project_id: UUID,
        hostname: str,
        expected: RouteTarget,
        *,
        preview_of: str | None = None,
    ) -> bool:
        """True only when ``GET <probe path>`` through the Gateway with this Host answers 200
        and its ``RELEASE_HEADER`` names ``expected`` (the release ID, or ``offline``). A 503 whose
        ``RELEASE_HEADER`` names exactly ``expected``, a release of ``project_id`` with a ready
        endpoint, raises ``SiteDependencyUnavailable``. Any other status, header or a transport
        error is False (decision 0014 sections 15 and 17). ``hostname`` no longer names the project
        (it is ``<subdomain>.<site domain>``, decision 0023 G18), so the project is passed.
        ``preview_of`` is the project's sub-domain when ``hostname`` is its preview host: only then
        does the probe carry the project's preview token (FR-52.1). With ``preview_of`` set, any
        other ``hostname`` is refused (a ``ValueError``) before anything is sent."""
        ...

    async def list_tenant_projects(self) -> tuple[UUID, ...]:
        """The project IDs of every namespace labelled as a tenant, parsed from its name
        ``wb-<projectId>``; a labelled namespace whose name is not that form is left out. Read
        only. Raises ClusterUnavailable."""
        ...

    async def inventory(self, project_id: UUID) -> SiteInventory:
        """The Deployments, Services and HTTPRoutes of the project's tenant namespace, with their
        fence annotation and their foreign field managers (``SiteObject``). Read only; a missing
        namespace raises TenantNotReady, an unreachable API server ClusterUnavailable."""
        ...

    async def reclaim_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        """Take back HTTPRoute ``route-<projectId>``: the same object ``apply_route`` renders,
        written by a **forced** server-side apply that re-stamps the fence with
        ``command_number``, so it wins over a foreign field manager and over a higher or malformed
        command number (decision 0014 section 23 item 3). Only an explicit, audited operator
        rollback may call it; every other write, the reconciler's included, stays never-forced.
        Raises TenantNotReady, ClusterRefused or ClusterUnavailable; never ApplyConflict or
        FenceRejected."""
        ...


class TenantState(StrEnum):
    READY = "ready"
    CREATING = "creating"
    TERMINATING = "terminating"


@dataclass(frozen=True, slots=True)
class TenantProject:
    """A project that needs a tenant, with its ``project_hosting.subdomain`` (decision 0023
    G18a): what the bootstrap writes into the namespace. ``subdomain_taken`` when another
    project's hosting row holds the same sub-domain (FR-54, TR-53): the bootstrap then makes no
    tenant for it."""

    project_id: UUID
    subdomain: str
    subdomain_taken: bool = False


class TenantBootstrap(Protocol):
    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        """Create or complete the tenant (namespace with its labels, quota, limits, the worker's
        RoleBinding last). Idempotent. The returned state means what it meant before
        ``on_created`` existed. ``subdomain`` is the project's ``project_hosting.subdomain``, which
        the namespace carries as a label for the route-shape admission policy (decision 0023
        G18a); it is added to a namespace from before that label, and never changed.

        ``on_created`` (task BT; decision 0014 section 24, 0016 section 6 item 4) reports that
        **this call created the namespace**: it is called synchronously, once, with
        ``project_id``, right after the namespace apply succeeds on a call whose own read found
        no namespace, and before any later step. A later step that fails, or a cancellation (the
        bootstrap loop's call timeout) after the namespace apply, therefore cannot lose the
        report, which a flag on the returned value would. It is never awaited, and it must not
        raise: the implementation does not swallow what it raises, because that would hide a
        programming error.

        "Created" means the namespace was absent when this call looked. If a rival writer (only
        the bootstrap identity or an administrator may write a tenant namespace, policy
        ``webblock-tenant-writer``) created it between the read and the apply, the server-side
        apply still succeeds and the report is made: the namespace is then still one that did
        not exist when this call read it, which is the condition of decision 0014 section 24,
        so no pod in it can hold the old catalog password.

        Not reported: a namespace created by the apply whose own request is cancelled or times
        out before its answer arrives (the next call reads it as existing)."""
        ...
