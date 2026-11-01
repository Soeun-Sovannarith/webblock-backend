"""Fakes shared by the routing and hosting tests (task F2). This module holds no test.

- ``World`` is one project's rows in memory. It is the ``RouteCommandStore`` (the three checks of
  every worker write, then the domain outcome function the use case passes, written in the order the
  SQL store writes, with the one-live and one-promoting indexes enforced) and the
  ``AcceptanceStore`` (the real acceptance decision of ``new_acceptance_decider``), so refusals come
  from the real rules, not from the fake.
- ``FakeCluster`` is the cluster's site route and preview route: ``apply_route`` and
  ``apply_preview`` store the target and command number, refusing a lower number as the adapter
  does; ``route_status`` and ``preview_status`` report a route accepted when its backend exists (a
  release in ``serving``, or the offline page); ``probe_site`` sees the backend the Gateway uses
  for the hostname and is True only when that backend answers (a release in ``answering``, or in
  ``preview_answering`` on the preview hostname when set; the offline page always) and is the
  expected one, as the release header makes the real probe. ``stale_site_probes`` makes that many
  probes of the site's hostname after each landed switch still reach the previous backend, as the
  Gateway does for a moment after a route is accepted. ``list_tenant_projects`` names the one
  project; ``inventory`` lists the two routes and a Deployment and Service per release in
  ``serving``, with ``foreign_managers`` by object name; ``reclaim_route`` is the forced apply: it
  lands whatever number the route carries and clears the route's foreign managers.
``delete_release`` (the cleanup of wave 6, task W6-B) records the release in ``deleted`` and
refuses the release the site's route points at, as the adapter does.

``run_failed_tries`` claims a promote command and runs its tries that are expected to fail
(FR-21: each claim is one try; decision 0027).

Every store and cluster call is appended to one shared ``log``, so their order is observable.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from provisioning.core.application import (
    AcceptanceDecider,
    AcceptCommandParams,
    AcceptedCommand,
    Claim,
    ClaimLost,
    LimitReached,
    NotFound,
    RetryLater,
    ref_of,
)
from provisioning.core.application.jobs.ports import AcceptanceFacts, Fence
from provisioning.core.application.operations.acceptance import new_acceptance_decider
from provisioning.core.application.ports.site_cluster import (
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
    WorkloadStatus,
)
from provisioning.core.application.routing.background.routing import (
    Routing,
    RoutingSettings,
    preview_hostname,
)
from provisioning.core.application.routing.ports import OutcomeDecider
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    IllegalTransition,
    OfflineReason,
    Outcome,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    RoutingStatus,
    check_fence,
    hosting_status_for,
    owner_status_version_of_key,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
LIVE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
CANDIDATE = UUID("0190a1b2-0000-7000-8000-0000000000a2")
OTHER_BUILT = UUID("0190a1b2-0000-7000-8000-0000000000a3")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
SHA = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64
SITE_DOMAIN = "sites.webblock.test"
# The hosting row's sub-domain: the site's host is <subdomain>.<site domain> (decision 0023 G18),
# its preview host <subdomain>-preview.<site domain> (FR-20).
SUBDOMAIN = "shop"
HOSTNAME = f"{SUBDOMAIN}.{SITE_DOMAIN}"
PREVIEW_HOSTNAME = f"{SUBDOMAIN}-preview.{SITE_DOMAIN}"
OFFLINE = RouteTarget(RouteTargetKind.OFFLINE)


def to_release(release_id: UUID) -> RouteTarget:
    return RouteTarget(RouteTargetKind.RELEASE, release_id)


def settings(
    switch: float = 0.15, restore: float = 0.15, interval: float = 0.001
) -> RoutingSettings:
    return RoutingSettings(
        site_domain=SITE_DOMAIN,
        switch_deadline=timedelta(seconds=switch),
        restore_deadline=timedelta(seconds=restore),
        poll_interval=timedelta(seconds=interval),
    )


class FakeClock:
    def __init__(self) -> None:
        self.at = T0

    def now(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at


class FakeIds:
    def __init__(self) -> None:
        self.issued: list[UUID] = []

    def new_id(self) -> UUID:
        value = UUID(int=0x0190A1B2_0000_7000_8000_0000_0000_F000 + len(self.issued) + 1)
        self.issued.append(value)
        return value


class FakeEntitlement:
    """B1's ``Entitlement`` for a promote: ``refuse`` raises LimitReached, ``fault`` any error."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.refuse = False
        self.fault: Exception | None = None

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        self.log.append(f"entitlement {kind}")
        if self.fault is not None:
            raise self.fault
        if self.refuse:
            raise LimitReached("trial ended")


class FakeHostingEntitlement:
    """``HostingEntitlement``: ``later`` is what the record knows of a Payment event after the
    command's own (True restored, False ended, None unknown); ``asked`` the event keys asked."""

    def __init__(self, log: list[str], later: bool | None = None) -> None:
        self.log = log
        self.later = later
        self.asked: list[str] = []
        self.fault: Exception | None = None

    async def entitlement_after(self, project_id: UUID, event_key: str) -> bool | None:
        self.log.append("hosting entitlement")
        self.asked.append(event_key)
        if self.fault is not None:
            raise self.fault
        return self.later


@dataclass
class Route:
    target: RouteTarget
    command_number: int


@dataclass
class FakeCluster:
    """The routes of one project. ``fail_apply`` raises for every apply to a target;
    ``fail_apply_once`` pops one error per apply to a target; ``fail_status_once`` pops one error
    per status read; ``fail_preview`` raises for every preview apply; ``on_probe`` (with the
    hostname) and ``on_apply`` run before the call does anything."""

    log: list[str]
    serving: set[UUID] = field(default_factory=set)
    answering: set[UUID] = field(default_factory=set)
    preview_answering: set[UUID] | None = None  # None: the same as ``answering``
    # Releases whose /readyz answers 503 (a dependency down, task H5a): a probe expecting one of
    # them raises SiteDependencyUnavailable, as the adapter does; any other probe gets False.
    outage: set[UUID] = field(default_factory=set)
    route: Route | None = None
    hostnames: tuple[str, ...] = ()
    preview: Route | None = None
    preview_host: str | None = None
    # Each probe's host and the sub-domain it named as the preview's (FR-52.1: only the preview
    # probe names it, so only it carries the project's token).
    probed_as: list[tuple[str, str | None]] = field(default_factory=list)
    previous: RouteTarget | None = None  # the site route's target before the last landed apply
    stale_site_probes: int = 0
    stale_left: int = 0
    fail_preview: Exception | None = None
    preview_lands: bool = True
    fail_apply: dict[RouteTarget, Exception] = field(default_factory=dict)
    fail_apply_once: dict[RouteTarget, list[Exception]] = field(default_factory=dict)
    fail_status_once: list[Exception] = field(default_factory=list)
    on_probe: Callable[[str], None] | None = None
    on_apply: Callable[[RouteTarget], None] | None = None
    hang_apply: bool = False  # apply_route never returns
    apply_lands: bool = True  # False: apply_route returns, but its effect never becomes visible
    foreign_managers: dict[str, tuple[str, ...]] = field(default_factory=dict)  # by object name
    fail_reclaim: Exception | None = None
    deleted: list[UUID] = field(default_factory=list)  # releases whose workload was deleted
    fail_delete: Exception | None = None

    async def apply_release(self, workload: ReleaseWorkload) -> None:
        raise AssertionError("routing never applies a release")

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        raise AssertionError("routing never restarts a release")

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        raise AssertionError("routing never reads a release")

    async def delete_release(self, project_id: UUID, release_id: UUID, command_number: int) -> None:
        """The cleanup's guarded delete (decision 0014 section 21 ruling 8), as the adapter does
        it: the release the site's route points at is refused (``FenceRejected``); a deleted
        release stops serving."""
        assert project_id == PROJECT
        self.log.append(f"delete_release {release_id} #{command_number}")
        if self.fail_delete is not None:
            raise self.fail_delete
        if self.route is not None and self.route.target == to_release(release_id):
            raise FenceRejected("the route points at this release")
        self.deleted.append(release_id)
        self.serving.discard(release_id)

    async def apply_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        assert project_id == PROJECT
        assert subdomain == SUBDOMAIN
        self.log.append(f"apply_route {describe(target)} #{command_number}")
        if self.on_apply is not None:
            self.on_apply(target)
        if self.hang_apply:
            await asyncio.Event().wait()
        if target in self.fail_apply:
            raise self.fail_apply[target]
        pending = self.fail_apply_once.get(target)
        if pending:
            raise pending.pop(0)
        if self.route is not None and self.route.command_number > command_number:
            raise FenceRejected("route-x carries a higher command number")
        if self.apply_lands:
            if self.route is None or self.route.target != target:
                self.previous = None if self.route is None else self.route.target
                self.stale_left = self.stale_site_probes
            self.route = Route(target, command_number)
            self.hostnames = hostnames

    async def route_status(self, project_id: UUID) -> RouteStatus:
        assert project_id == PROJECT
        self.log.append("route_status")
        if self.fail_status_once:
            raise self.fail_status_once.pop(0)
        if self.route is None:
            return RouteStatus(accepted=False, target=None, command_number=None)
        target = self.route.target
        accepted = target.kind is RouteTargetKind.OFFLINE or target.release_id in self.serving
        return RouteStatus(
            accepted=accepted, target=target, command_number=self.route.command_number
        )

    async def apply_preview(
        self,
        project_id: UUID,
        release_id: UUID,
        hostname: str,
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        assert project_id == PROJECT
        assert hostname == preview_hostname(SUBDOMAIN, SITE_DOMAIN)
        assert subdomain == SUBDOMAIN  # security review S3: the adapter checks the host with it
        self.log.append(f"apply_preview release {release_id} #{command_number}")
        if self.fail_preview is not None:
            raise self.fail_preview
        if self.preview is not None and self.preview.command_number > command_number:
            raise FenceRejected("preview-x carries a higher command number")
        if self.preview_lands:
            self.preview = Route(RouteTarget(RouteTargetKind.RELEASE, release_id), command_number)
            self.preview_host = hostname

    async def preview_status(self, project_id: UUID) -> RouteStatus:
        assert project_id == PROJECT
        self.log.append("preview_status")
        if self.preview is None:
            return RouteStatus(accepted=False, target=None, command_number=None)
        target = self.preview.target
        return RouteStatus(
            accepted=target.release_id in self.serving,
            target=target,
            command_number=self.preview.command_number,
        )

    async def probe_site(
        self,
        project_id: UUID,
        hostname: str,
        expected: RouteTarget,
        *,
        preview_of: str | None = None,
    ) -> bool:
        assert project_id == PROJECT
        self.probed_as.append((hostname, preview_of))
        self.log.append(f"probe {hostname} expect {describe(expected)}")
        if self.on_probe is not None:
            self.on_probe(hostname)
        answering = self.answering
        backend: RouteTarget | None
        if hostname == self.preview_host:
            backend = None if self.preview is None else self.preview.target
            if self.preview_answering is not None:
                answering = self.preview_answering
        elif self.stale_left > 0:
            self.stale_left -= 1
            backend = self.previous
        else:
            backend = None if self.route is None else self.route.target
        if backend is None:
            return False
        if backend.release_id in self.outage:
            if backend == expected:
                raise SiteDependencyUnavailable
            return False
        answers = backend.kind is RouteTargetKind.OFFLINE or backend.release_id in answering
        return answers and backend == expected

    async def list_tenant_projects(self) -> tuple[UUID, ...]:
        self.log.append("list_tenant_projects")
        return (PROJECT,)

    async def inventory(self, project_id: UUID) -> SiteInventory:
        assert project_id == PROJECT
        self.log.append("inventory")
        objects: list[SiteObject] = []
        for name, route in ((f"route-{PROJECT}", self.route), (f"preview-{PROJECT}", self.preview)):
            if route is not None:
                objects.append(self._object(SiteObjectKind.HTTP_ROUTE, name, None, route))
        for release_id in sorted(self.serving):
            for kind in (SiteObjectKind.DEPLOYMENT, SiteObjectKind.SERVICE):
                objects.append(self._object(kind, f"rel-{release_id}", release_id, None))
        return SiteInventory(project_id=PROJECT, objects=tuple(objects))

    def _object(
        self, kind: SiteObjectKind, name: str, release_id: UUID | None, route: Route | None
    ) -> SiteObject:
        return SiteObject(
            kind=kind,
            name=name,
            release_id=release_id,
            command_number=None if route is None else route.command_number,
            foreign_managers=self.foreign_managers.get(name, ()),
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
        assert project_id == PROJECT
        assert subdomain == SUBDOMAIN
        self.log.append(f"reclaim_route {describe(target)} #{command_number}")
        if self.fail_reclaim is not None:
            raise self.fail_reclaim
        if self.route is None or self.route.target != target:
            self.previous = None if self.route is None else self.route.target
            self.stale_left = self.stale_site_probes
        self.route = Route(target, command_number)  # forced: a higher number does not refuse it
        self.hostnames = hostnames
        self.foreign_managers.pop(f"route-{PROJECT}", None)

    def site_probes(self) -> list[str]:
        return [line for line in self.log if line.startswith(f"probe {HOSTNAME} ")]

    def preview_probes(self) -> list[str]:
        return [line for line in self.log if line.startswith(f"probe {PREVIEW_HOSTNAME} ")]

    def applied_targets(self) -> list[str]:
        return [line for line in self.log if line.startswith("apply_route")]


async def run_failed_tries(
    routing: Routing, world: World, command: Command, tries: int
) -> list[RetryLater]:
    """Claim ``command`` ``tries`` times and run each claim's try, which must fail between tries
    (``RetryLater``); returns what each raised, in order."""
    raised: list[RetryLater] = []
    for _ in range(tries):
        try:
            await routing.promote(world.claim(command))
        except RetryLater as exc:
            raised.append(exc)
        else:
            raise AssertionError("the try did not fail between tries")
    return raised


def describe(target: RouteTarget) -> str:
    if target.kind is RouteTargetKind.OFFLINE:
        return "offline"
    return f"release {target.release_id}"


def unavailable() -> ClusterUnavailable:
    return ClusterUnavailable("api server 503")


def make_release(
    release_id: UUID, status: ReleaseStatus, minutes: int, project_id: UUID = PROJECT
) -> Release:
    created = T0 - timedelta(hours=1) + timedelta(minutes=minutes)
    digest = None if status in (ReleaseStatus.ACCEPTED, ReleaseStatus.BUILDING) else DIGEST
    promoted = created if status in (ReleaseStatus.LIVE, ReleaseStatus.SUPERSEDED) else None
    return Release(
        id=release_id,
        project_id=project_id,
        generation_request_id=UUID(int=REQUEST.int + minutes),
        repository_id=4242,
        git_commit_sha=SHA,
        status=status,
        created_at=created,
        updated_at=created,
        container_image_digest=digest,
        promoted_at=promoted,
    )


class World:
    """One project's rows; the RouteCommandStore and AcceptanceStore fakes."""

    def __init__(
        self,
        log: list[str],
        *,
        live: bool = True,
        candidate: bool = True,
        project_id: UUID = PROJECT,
    ) -> None:
        self.log = log
        self.project_id = project_id
        self.clock = FakeClock()
        self.ids = FakeIds()
        self.releases: dict[UUID, Release] = {}
        self.commands: dict[UUID, Command] = {}
        self.claims: dict[UUID, int] = {}
        self.writes: list[str] = []
        self.hosting_calls: list[dict[str, object]] = []
        self.hosting = ProjectHosting(
            project_id=project_id,
            last_command_number=0,
            routing_status=RoutingStatus.STABLE,
            hosting_status=HostingStatus.ONLINE,
            updated_at=T0 - timedelta(hours=1),
            subdomain=SUBDOMAIN,
        )
        if live:
            self.releases[LIVE] = make_release(LIVE, ReleaseStatus.LIVE, 1)
            self.hosting = replace(self.hosting, live_release_id=LIVE)
        if candidate:
            self.releases[CANDIDATE] = make_release(CANDIDATE, ReleaseStatus.CANDIDATE_READY, 2)

    # --- set-up helpers -------------------------------------------------------------------------

    def accept(
        self, kind: CommandKind, release_id: UUID | None = None, key: str | None = None
    ) -> Command:
        """Accept a command through the real decision, as the API or the hosting intake does.
        A root owner-status key (no ``.retry-<n>``) newer than the row first records its version,
        as the owner-status intake does before it accepts (decision 0021); a refused decision
        undoes it, as the Pg store's rollback does. A retry key never moves the version."""
        before = self.hosting
        version = owner_status_version_of_key(key or "")
        if (
            kind.is_hosting()
            and version is not None
            and ".retry-" not in (key or "")
            and version > self.hosting.owner_status_version
        ):
            self.hosting = replace(self.hosting, owner_status_version=version)
        request = CommandRequest(
            project_id=self.project_id,
            kind=kind,
            release_id=release_id,
            idempotency_key=key or f"key-{len(self.commands) + 1}",
        )
        now = self.clock.now()
        params = AcceptCommandParams(
            request=request,
            command_id=self.ids.new_id(),
            # An event's hosting command has no requester; an admin's (key admin:<key>) does.
            requested_by_subject=(
                ""
                if kind.is_hosting() and not request.idempotency_key.startswith("admin:")
                else "operator@example.test"
            ),
            now=now,
            correlation_id="corr-1",
            deadline_at=now + timedelta(hours=1),
        )
        try:
            return self._accept(params, new_acceptance_decider(params)).command
        except BaseException:
            self.hosting = before
            raise

    def claim(self, command: Command) -> Claim:
        """A worker claims the command (accepted -> running, or a takeover of a running one)."""
        stored = self.commands[command.id]
        if stored.status is CommandStatus.ACCEPTED:
            stored = stored.transition(CommandStatus.RUNNING, "", self.clock.now())
            self.commands[command.id] = stored
        number = self.claims.get(command.id, 0) + 1
        self.claims[command.id] = number
        return Claim(
            ref=ref_of(stored),
            claimed_by_worker=f"worker-{number}",
            claim_number=number,
            execution_attempt=number,
            expires_at=T0 + timedelta(minutes=5),
            deadline_at=T0 + timedelta(hours=1),
        )

    def steal_claim(self, command_id: UUID) -> None:
        """Another worker claims the command after this claim expired."""
        self.claims[command_id] = self.claims[command_id] + 1

    def routing(self) -> RoutingStatus:
        """Read through a call, so a type checker does not narrow the field across awaits."""
        return self.hosting.routing_status

    def hosting_status(self) -> HostingStatus:
        return self.hosting.hosting_status

    def reasons(self) -> frozenset[OfflineReason]:
        """The hosting row's offline reasons (decision 0021)."""
        return self.hosting.offline_reasons

    def set_reasons(self, *reasons: OfflineReason) -> None:
        """Give the hosting row these offline reasons and the hosting status they imply."""
        held = frozenset(reasons)
        self.hosting = replace(
            self.hosting, offline_reasons=held, hosting_status=hosting_status_for(held)
        )

    def release(self, release_id: UUID) -> Release:
        return self.releases[release_id]

    def command(self, command_id: UUID) -> Command:
        return self.commands[command_id]

    # --- RouteCommandStore ----------------------------------------------------------------------

    async def load_route_command(self, fence: Fence) -> OutcomeInput:
        self.log.append("store load")
        return self._checked(fence)

    async def apply_route_outcome(self, fence: Fence, decide: OutcomeDecider) -> Outcome:
        self.log.append("store apply")
        out = decide(self._checked(fence))
        if out.superseded_release is not None:
            self._write_release(out.superseded_release)
        if out.release is not None:
            self._write_release(out.release)
        if out.hosting != self.hosting:
            self.writes.append(
                f"hosting routing={out.hosting.routing_status} "
                f"hosting={out.hosting.hosting_status} live={out.hosting.live_release_id}"
            )
            self.hosting = out.hosting
        if out.command != self.commands[out.command.id]:
            self.writes.append(f"command {out.command.kind} {out.command.status}")
            self.commands[out.command.id] = out.command
        return out

    def _checked(self, fence: Fence) -> OutcomeInput:
        if fence.project_id != self.project_id:
            raise NotFound("project")
        check_fence(fence.command_number, self.hosting.last_command_number)
        if self.claims.get(fence.command_id) != fence.claim_number:
            raise ClaimLost(f"command {fence.command_id}")
        command = self.commands[fence.command_id]
        if command.status is not CommandStatus.RUNNING:
            raise IllegalTransition(f"command {command.id} is {command.status}")
        release = None if command.release_id is None else self.releases[command.release_id]
        live_id = self.hosting.live_release_id
        live = None if live_id is None else self.releases[live_id]
        promoting = next(
            (r for r in self.releases.values() if r.status is ReleaseStatus.PROMOTING), None
        )
        return OutcomeInput(
            hosting=self.hosting,
            command=command,
            release=release,
            live_release=live,
            promoting_release=promoting,
        )

    def _write_release(self, release: Release) -> None:
        self.releases[release.id] = release
        for status in (ReleaseStatus.LIVE, ReleaseStatus.PROMOTING):
            if sum(1 for r in self.releases.values() if r.status is status) > 1:
                raise AssertionError(f"releases_one_{status} index violated")
        self.writes.append(f"release {release.id} {release.status}")

    # --- AcceptanceStore ------------------------------------------------------------------------

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        self.log.append("acceptance find")
        return self._by_key(request)

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        self.log.append("acceptance accept")
        return self._accept(params, decide)

    async def accept_hosting_command(
        self,
        *,
        project_id: UUID,
        kind: CommandKind,
        event_key: str,
        command_id: UUID,
        now: datetime,
        correlation_id: str,
        deadline_at: datetime,
    ) -> AcceptedCommand:
        """B3's ``HostingIntakeStore.accept_hosting_command``: the unlocked lookup by the event
        key (a replay or a conflict), then the acceptance with the real decision."""
        self.hosting_calls.append(
            {
                "project_id": project_id,
                "kind": kind,
                "event_key": event_key,
                "command_id": command_id,
                "now": now,
                "correlation_id": correlation_id,
                "deadline_at": deadline_at,
            }
        )
        request = CommandRequest(
            project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
        )
        stored = await self.find_command_by_key(request)
        if stored is not None:
            stored.check_replay(request)
            return AcceptedCommand(command=stored, replayed=True)
        params = AcceptCommandParams(
            request=request,
            command_id=command_id,
            requested_by_subject="",
            now=now,
            correlation_id=correlation_id,
            deadline_at=deadline_at,
        )
        return await self.accept_command(params, new_acceptance_decider(params))

    def _by_key(self, request: CommandRequest) -> Command | None:
        for command in self.commands.values():
            if (
                command.project_id == request.project_id
                and command.idempotency_key == request.idempotency_key
                and command.kind.is_hosting() == request.kind.is_hosting()
            ):
                return command
        return None

    def _accept(self, params: AcceptCommandParams, decide: AcceptanceDecider) -> AcceptedCommand:
        request = params.request
        in_flight = next((c for c in self.commands.values() if c.status.unfinished()), None)
        target = None if request.release_id is None else self.releases.get(request.release_id)
        live_id = self.hosting.live_release_id
        facts = AcceptanceFacts(
            hosting=self.hosting,
            by_key=self._by_key(request),
            in_flight=in_flight,
            target=target,
            live=None if live_id is None else self.releases[live_id],
        )
        decision = decide(facts)
        if not decision.insert:
            return AcceptedCommand(command=decision.command, replayed=True)
        command = decision.command
        self.commands[command.id] = command
        self.hosting = replace(self.hosting, last_command_number=command.command_number)
        return AcceptedCommand(command=command, replayed=False)


__all__ = [
    "CANDIDATE",
    "HOSTNAME",
    "LIVE",
    "OFFLINE",
    "OTHER_BUILT",
    "PREVIEW_HOSTNAME",
    "PROJECT",
    "SITE_DOMAIN",
    "FakeCluster",
    "FakeEntitlement",
    "FakeHostingEntitlement",
    "Route",
    "World",
    "make_release",
    "settings",
    "to_release",
    "unavailable",
]
