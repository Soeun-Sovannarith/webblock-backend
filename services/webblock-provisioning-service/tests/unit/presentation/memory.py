"""An in-memory acceptance and read store, and the real handlers of the fourteen operations built on
it and registered on their buses, so the transport tests run idempotent replay, key conflicts and
the busy refusal through the real acceptance rules (decision 0004 section 4) rather than a fake that
repeats them. Not a test module; imported as ``tests.unit.presentation.memory``."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from uuid import UUID

from tests.unit.presentation.fakes import GENERATION_REQUEST, PROJECT, SHA, T0

from provisioning.core.application import (
    AcceptanceDecider,
    AcceptanceFacts,
    AcceptCommandParams,
    AcceptedCommand,
    AcceptRedeployParams,
    Action,
    BuildDispatch,
    BuildStarted,
    CommandBus,
    CommandKey,
    Deployment,
    DiagnosticFacts,
    EnabledOperations,
    NotFound,
    OperationDeps,
    Principal,
    ProjectAccess,
    QueryBus,
    ReleaseCursor,
    ReleaseKey,
    ReleaseWithSteps,
    ServiceAudit,
    register_operations,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    ProjectHosting,
    Release,
    ReleaseStatus,
    new_project_hosting,
    new_release,
)
from provisioning.infrastructure.identity import ActionScopePolicy

SCOPES = {a: f"prov.{a.value}" for a in Action}
ALL_SCOPES = tuple(SCOPES.values())
READ_SCOPES = tuple(s for a, s in SCOPES.items() if not a.is_mutation())

_IN_FLIGHT = (CommandStatus.ACCEPTED, CommandStatus.RUNNING)
_HOSTING_KINDS = (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING)


@dataclass
class MemoryStores:
    """AcceptanceStore and ReadStore over dictionaries; one acceptance at a time (no awaits
    inside), which is what the hosting row lock gives the real store."""

    hosting: dict[UUID, ProjectHosting] = field(default_factory=dict)
    releases: dict[UUID, Release] = field(default_factory=dict)
    commands: list[Command] = field(default_factory=list)

    def add_release(
        self, release_id: UUID, status: ReleaseStatus, project_id: UUID = PROJECT
    ) -> None:
        if project_id not in self.hosting:
            self.hosting[project_id] = new_project_hosting(project_id, T0, subdomain="shop")
        release = new_release(
            release_id=release_id,
            project_id=project_id,
            generation_request_id=GENERATION_REQUEST,
            repository_id=4242,
            git_commit_sha=SHA,
            now=T0,
        )
        digest = "sha256:" + "c" * 64 if status != ReleaseStatus.ACCEPTED else None
        self.releases[release_id] = replace(release, status=status, container_image_digest=digest)

    def _in_flight(self, project_id: UUID) -> Command | None:
        return next(
            (c for c in self.commands if c.project_id == project_id and c.status in _IN_FLIGHT),
            None,
        )

    def _by_key(self, request: CommandRequest) -> Command | None:
        for command in self.commands:
            if (
                command.project_id == request.project_id
                and command.idempotency_key == request.idempotency_key
                and (command.kind in _HOSTING_KINDS) == (request.kind in _HOSTING_KINDS)
            ):
                return command
        return None

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        return self._by_key(request)

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        request = params.request
        hosting = self.hosting.get(request.project_id)
        if hosting is None:
            raise NotFound(f"project {request.project_id}")
        target = self.releases.get(request.release_id) if request.release_id else None
        if target is not None and target.project_id != request.project_id:
            target = None
        live = self.releases.get(hosting.live_release_id) if hosting.live_release_id else None
        in_flight = next(
            (
                c
                for c in self.commands
                if c.project_id == request.project_id and c.status in _IN_FLIGHT
            ),
            None,
        )
        decision = decide(
            AcceptanceFacts(
                hosting=hosting,
                by_key=self._by_key(request),
                in_flight=in_flight,
                target=target,
                live=live,
            )
        )
        return self._apply(hosting, decision.insert, decision.command)

    def _apply(self, hosting: ProjectHosting, insert: bool, command: Command) -> AcceptedCommand:
        if not insert:
            return AcceptedCommand(command=command, replayed=True)
        self.commands.append(command)
        self.hosting[hosting.project_id] = replace(
            hosting, last_command_number=command.command_number
        )
        return AcceptedCommand(command=command, replayed=False)

    async def accept_redeploy(
        self, params: AcceptRedeployParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        """The redeploy acceptance (decision 0023 G9): the key in the client key class, the live
        release of the hosting row, no target."""
        hosting = self.hosting.get(params.project_id)
        if hosting is None:
            raise NotFound(f"project {params.project_id}")
        live = self.releases.get(hosting.live_release_id) if hosting.live_release_id else None
        by_key = next(
            (
                c
                for c in self.commands
                if c.project_id == params.project_id
                and c.idempotency_key == params.idempotency_key
                and c.kind not in _HOSTING_KINDS
            ),
            None,
        )
        decision = decide(
            AcceptanceFacts(
                hosting=hosting,
                by_key=by_key,
                in_flight=self._in_flight(params.project_id),
                target=None,
                live=live,
            )
        )
        return self._apply(hosting, decision.insert, decision.command)

    async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
        release = self.releases.get(key.release_id)
        if release is None or release.project_id != key.project_id:
            raise NotFound(f"release {key.release_id}")
        return ReleaseWithSteps(release=release, steps=())

    async def read_command(self, key: CommandKey) -> Command:
        for command in self.commands:
            if command.id == key.command_id and command.project_id == key.project_id:
                return command
        raise NotFound(f"command {key.command_id}")

    async def read_deployment(self, project_id: UUID) -> Deployment:
        hosting = self.hosting.get(project_id)
        if hosting is None:
            raise NotFound(f"project {project_id}")
        in_flight = next(
            (c for c in self.commands if c.project_id == project_id and c.status in _IN_FLIGHT),
            None,
        )
        return Deployment(hosting=hosting, in_flight=in_flight)

    async def list_releases(
        self, project_id: UUID, *, limit: int, after: ReleaseCursor | None
    ) -> tuple[Release, ...]:
        rows = sorted(
            (r for r in self.releases.values() if r.project_id == project_id),
            key=lambda r: (r.created_at, r.id),
            reverse=True,
        )
        if after is not None:
            rows = [r for r in rows if (r.created_at, r.id) < (after.created_at, after.release_id)]
        return tuple(rows[:limit])

    async def list_commands(
        self, project_id: UUID, *, limit: int, after: int | None
    ) -> tuple[Command, ...]:
        rows = sorted(
            (c for c in self.commands if c.project_id == project_id),
            key=lambda c: c.command_number,
            reverse=True,
        )
        if after is not None:
            rows = [c for c in rows if c.command_number < after]
        return tuple(rows[:limit])

    async def read_diagnostics(
        self, key: ReleaseKey, *, step_limit: int, command_limit: int
    ) -> DiagnosticFacts:
        release = self.releases.get(key.release_id)
        if release is None or release.project_id != key.project_id:
            raise NotFound(f"release {key.release_id}")
        commands = await self.list_commands(key.project_id, limit=command_limit, after=None)
        return DiagnosticFacts(release=release, steps=(), commands=commands, source_event=None)


class _Allow:
    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        del principal, project_id, action

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        del project_id, kind


class _Clock:
    def now(self) -> datetime:
        return T0


@dataclass
class _Ids:
    next: int = 0x100

    def new_id(self) -> UUID:
        self.next += 1
        return UUID(f"0190a1b2-0000-7000-8000-{self.next:012x}")


@dataclass
class FakeBuilds:
    """The BuildStarter the service calls for build_release: records each start and raises the
    configured error, else reports the start and moves an accepted release to building."""

    stores: MemoryStores
    error: BaseException | None = None
    starts: list[tuple[UUID, UUID]] = field(default_factory=list)

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        self.starts.append((project_id, release_id))
        if self.error is not None:
            raise self.error
        release = self.stores.releases[release_id]
        self.stores.releases[release_id] = replace(release, status=ReleaseStatus.BUILDING)
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=UUID(int=release_id.int ^ 1),
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )


@dataclass(frozen=True, slots=True)
class MemoryOperations:
    """The fourteen operations' buses, as an ``Invoker`` takes them."""

    commands: CommandBus
    queries: QueryBus


def memory_operations(
    stores: MemoryStores,
    builds: FakeBuilds | None = None,
    audit: ServiceAudit | None = None,
    access: ProjectAccess | None = None,
    enabled: EnabledOperations | None = None,
) -> MemoryOperations:
    """The real handlers over the memory stores, on their buses, every operation enabled
    (``enabled`` replaces the set), the action policy of the configured scopes, stand-ins that
    allow (``access`` replaces the project access one), and ``audit`` when given."""
    allow = _Allow()
    operations = MemoryOperations(commands=CommandBus(), queries=QueryBus())
    register_operations(
        operations.commands,
        operations.queries,
        OperationDeps(
            policy=ActionScopePolicy(SCOPES),
            access=access if access is not None else allow,
            entitlement=allow,
            acceptance=stores,
            reads=stores,
            history=stores,
            clock=_Clock(),
            ids=_Ids(),
            builds=builds if builds is not None else FakeBuilds(stores),
            enabled=enabled if enabled is not None else EnabledOperations(Action),
            command_deadline=timedelta(minutes=5),
            audit=audit,
            redeploys=stores,
        ),
    )
    return operations
