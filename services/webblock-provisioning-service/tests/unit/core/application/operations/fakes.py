"""Recording doubles and fixed values shared by the operation tests (decision 0004 sections 4 and
6, decision 0006 section 3): the gate, acceptance, decide, reads and error table tests, and the
acceptance decision tests of ``jobs/ports``. Test adapters only; nothing in ``src/`` imports
them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from provisioning.core.application import (
    AcceptanceDecider,
    AcceptanceDecision,
    AcceptanceFacts,
    AcceptCommandParams,
    AcceptedCommand,
    Action,
    ActionDenied,
    BuildDispatch,
    BuildInProgress,
    BuildStarted,
    CommandBus,
    CommandKey,
    Contention,
    DependencyNotConfigured,
    DependencyUnavailable,
    Deployment,
    DiagnosticFacts,
    EnabledOperations,
    LimitReached,
    NotFound,
    OperationDeps,
    OperationDisabled,
    OperationInProgress,
    OperationNotAvailable,
    Overloaded,
    Principal,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    QueryBus,
    ReleaseCursor,
    ReleaseKey,
    ReleaseNotBuildable,
    ReleaseWithSteps,
    ServiceAudit,
    Timeout,
    Unauthenticated,
    register_operations,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandNumberOverflow,
    CommandRequest,
    HostingOffline,
    HostingStatus,
    IdempotencyConflict,
    IllegalTransition,
    InvalidId,
    InvalidIdempotencyKey,
    ProjectHosting,
    Release,
    ReleaseOutdated,
    ReleaseStatus,
    RouteNotStable,
    RoutingStatus,
    is_error,
    new_command,
)

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)

ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
ID_PROJECT2 = UUID("0190a1b2-0000-7000-8000-000000000002")
ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
ID_ACTIVE = UUID("0190a1b2-0000-7000-8000-0000000000b1")
ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
ID_COMMAND2 = UUID("0190a1b2-0000-7000-8000-0000000000c2")
ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
ID_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")

SHA40 = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64

# The subject that issued the client commands of these tests.
REQUESTER = "user-1"
# The command deadline every fixture is built with.
DEADLINE = timedelta(minutes=15)

# Names of the calls in the shared log.
CALL_POLICY = "policy"
CALL_ACCESS = "access"
CALL_ENTITLEMENT = "entitlement"
CALL_FIND = "find"
CALL_ACCEPT = "accept"
CALL_READ_RELEASE = "read_release"
CALL_READ_COMMAND = "read_command"
CALL_READ_DEPLOYMENT = "read_deployment"
CALL_READ_DIAGNOSTICS = "read_diagnostics"
CALL_CLOCK = "clock"
CALL_ID = "id"
CALL_BUILD = "build"


def requester_principal() -> Principal:
    """A verified principal whose subject is REQUESTER."""
    return Principal(subject=REQUESTER, scopes=("provisioning",))


def project_hosting(last_command_number: int) -> ProjectHosting:
    return ProjectHosting(
        project_id=ID_PROJECT,
        last_command_number=last_command_number,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        subdomain="shop",
    )


def release_in(release_id: UUID, status: ReleaseStatus, created: datetime) -> Release:
    """A release of ID_PROJECT with the columns its status carries."""
    has_digest = status in (
        ReleaseStatus.BUILT,
        ReleaseStatus.CANDIDATE_READY,
        ReleaseStatus.PROMOTING,
        ReleaseStatus.LIVE,
        ReleaseStatus.SUPERSEDED,
    )
    return Release(
        id=release_id,
        project_id=ID_PROJECT,
        generation_request_id=ID_REQUEST,
        repository_id=42,
        git_commit_sha=SHA40,
        status=status,
        created_at=created,
        updated_at=created,
        container_image_digest=DIGEST if has_digest else None,
        promoted_at=created if status is ReleaseStatus.LIVE else None,
    )


def command_for(
    kind: CommandKind, command_id: UUID, key: str, by: str, command_number: int
) -> Command:
    """An accepted command of ``kind`` on ID_RELEASE of ID_PROJECT under ``key`` by ``by``."""
    return new_command(
        command_id=command_id,
        request=CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key=key
        ),
        requested_by_subject=by,
        command_number=command_number,
        now=T0,
    )


def stored_command(command_number: int) -> Command:
    """A deploy command as the store would return it, accepted at T0 and issued by REQUESTER."""
    return command_for(CommandKind.DEPLOY, ID_COMMAND, "key-1", REQUESTER, command_number)


class CallLog:
    def __init__(self) -> None:
        self.calls: list[str] = []


class FakePolicy:
    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.err: Exception | None = None
        self.principals: list[Principal] = []
        self.actions: list[Action] = []

    def permit(self, principal: Principal, action: Action) -> None:
        self.log.calls.append(CALL_POLICY)
        self.principals.append(principal)
        self.actions.append(action)
        if self.err is not None:
            raise self.err


class FakeAccess:
    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.err: Exception | None = None
        self.principals: list[Principal] = []
        self.projects: list[UUID] = []
        self.actions: list[Action] = []

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        self.log.calls.append(CALL_ACCESS)
        self.principals.append(principal)
        self.projects.append(project_id)
        self.actions.append(action)
        if self.err is not None:
            raise self.err


class FakeEntitlement:
    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.err: Exception | None = None
        self.projects: list[UUID] = []
        self.kinds: list[CommandKind] = []

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        self.log.calls.append(CALL_ENTITLEMENT)
        self.projects.append(project_id)
        self.kinds.append(kind)
        if self.err is not None:
            raise self.err


class FakeAcceptance:
    """Scripts the unlocked lookup and the acceptance transaction. ``accept_command`` calls decide
    once for each scripted set of facts, in order, as the store does on the raced-violation path,
    and returns what the last decision says. With ``accept_err`` set it raises that without calling
    decide (the store's NotFound for a missing hosting row, contention)."""

    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.found: Command | None = None
        self.find_err: Exception | None = None
        self.requests: list[CommandRequest] = []
        self.facts: list[AcceptanceFacts] = []
        self.accept_err: Exception | None = None
        # When set, replaces the command the store returns (a faulty store).
        self.result: AcceptedCommand | None = None
        self.params: list[AcceptCommandParams] = []
        self.decisions: list[AcceptanceDecision] = []
        self.decide_errors: list[Exception] = []

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        self.log.calls.append(CALL_FIND)
        self.requests.append(request)
        if self.find_err is not None:
            raise self.find_err
        return self.found

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        self.log.calls.append(CALL_ACCEPT)
        self.params.append(params)
        if self.accept_err is not None:
            raise self.accept_err
        last: AcceptanceDecision | None = None
        for facts in self.facts:
            try:
                decision = decide(facts)
            except Exception as exc:
                self.decide_errors.append(exc)
                raise
            self.decisions.append(decision)
            last = decision
        if self.result is not None:
            return self.result
        assert last is not None, "the double has no facts to decide on"
        return AcceptedCommand(command=last.command, replayed=not last.insert)


class FakeReads:
    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.release: ReleaseWithSteps | None = None
        self.command: Command | None = None
        self.deployment: Deployment | None = None
        self.err: Exception | None = None
        self.release_keys: list[ReleaseKey] = []
        self.command_keys: list[CommandKey] = []
        self.projects: list[UUID] = []

    async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
        self.log.calls.append(CALL_READ_RELEASE)
        self.release_keys.append(key)
        if self.err is not None:
            raise self.err
        assert self.release is not None, "the double has no release"
        return self.release

    async def read_command(self, key: CommandKey) -> Command:
        self.log.calls.append(CALL_READ_COMMAND)
        self.command_keys.append(key)
        if self.err is not None:
            raise self.err
        assert self.command is not None, "the double has no command"
        return self.command

    async def read_deployment(self, project_id: UUID) -> Deployment:
        self.log.calls.append(CALL_READ_DEPLOYMENT)
        self.projects.append(project_id)
        if self.err is not None:
            raise self.err
        assert self.deployment is not None, "the double has no deployment"
        return self.deployment

    async def read_diagnostics(
        self, key: ReleaseKey, *, step_limit: int, command_limit: int
    ) -> DiagnosticFacts:
        self.log.calls.append(CALL_READ_DIAGNOSTICS)
        self.release_keys.append(key)
        if self.err is not None:
            raise self.err
        assert self.release is not None, "the double has no release"
        return DiagnosticFacts(
            release=self.release.release,
            steps=self.release.steps[:step_limit],
            commands=(),
            source_event=None,
        )

    async def list_releases(
        self, project_id: UUID, *, limit: int, after: ReleaseCursor | None
    ) -> tuple[Release, ...]:
        raise AssertionError("not used here (test_bus_surface.py)")

    async def list_commands(
        self, project_id: UUID, *, limit: int, after: int | None
    ) -> tuple[Command, ...]:
        raise AssertionError("not used here (test_bus_surface.py)")


class FakeClock:
    def __init__(self, log: CallLog, now: datetime) -> None:
        self.log = log
        self.at = now

    def now(self) -> datetime:
        self.log.calls.append(CALL_CLOCK)
        return self.at


class FakeIds:
    def __init__(self, log: CallLog, new: UUID) -> None:
        self.log = log
        self.id = new
        self.err: Exception | None = None

    def new_id(self) -> UUID:
        self.log.calls.append(CALL_ID)
        if self.err is not None:
            raise self.err
        return self.id


class FakeBuilds:
    def __init__(self, log: CallLog) -> None:
        self.log = log
        self.started: list[tuple[UUID, UUID]] = []
        self.err: Exception | None = None

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        self.log.calls.append(CALL_BUILD)
        self.started.append((project_id, release_id))
        if self.err is not None:
            raise self.err
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=ID_STEP,
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )


class OperationsFixture:
    """The operations' handlers wired to recording doubles, with every operation enabled, a policy
    and project access that permit, entitlement that allows, no stored command under the key, and
    clean acceptance facts for a deploy of a built release at command number 4. With
    ``with_history`` the reads double also serves the listings and diagnostics; ``audit`` set
    before ``build`` records audit events."""

    def __init__(self, *, with_history: bool = False) -> None:
        self.with_history = with_history
        self.log = CallLog()
        self.policy = FakePolicy(self.log)
        self.access = FakeAccess(self.log)
        self.entitlement = FakeEntitlement(self.log)
        self.acceptance = FakeAcceptance(self.log)
        self.reads = FakeReads(self.log)
        self.clock = FakeClock(self.log, T1)
        self.ids = FakeIds(self.log, ID_COMMAND2)
        self.builds = FakeBuilds(self.log)
        self.acceptance.facts = [
            AcceptanceFacts(
                hosting=project_hosting(4),
                target=release_in(ID_RELEASE, ReleaseStatus.BUILT, T0),
            )
        ]
        self.enabled = EnabledOperations(Action)
        self.audit: ServiceAudit | None = None
        self.build()

    def build(self) -> None:
        """(Re)build the handlers on new buses from the doubles and the enabled set."""
        self.commands, self.queries = CommandBus(), QueryBus()
        register_operations(self.commands, self.queries, self.deps())

    def deps(self) -> OperationDeps:
        return OperationDeps(
            policy=self.policy,
            access=self.access,
            entitlement=self.entitlement,
            acceptance=self.acceptance,
            reads=self.reads,
            history=self.reads if self.with_history else None,
            clock=self.clock,
            ids=self.ids,
            builds=self.builds,
            enabled=self.enabled,
            command_deadline=DEADLINE,
            audit=self.audit,
        )


# The classes that decision 0006 section 3 maps to a class other than internal,
# plus the two conflict errors of the prototype's build endpoint.
TYPED_ERRORS: tuple[type[Exception], ...] = (
    Unauthenticated,
    ActionDenied,
    ProjectAccessDenied,
    NotFound,
    LimitReached,
    DependencyUnavailable,
    DependencyNotConfigured,
    ProjectAccessUnavailable,
    OperationNotAvailable,
    OperationDisabled,
    OperationInProgress,
    Contention,
    Timeout,
    Overloaded,
    ReleaseNotBuildable,
    BuildInProgress,
    InvalidId,
    InvalidIdempotencyKey,
    IdempotencyConflict,
    RouteNotStable,
    HostingOffline,
    IllegalTransition,
    ReleaseOutdated,
    CommandNumberOverflow,
)


def assert_internal(exc: BaseException, name: str = "") -> None:
    """The error is internal: no typed class anywhere in its cause chain. A stub's
    NotImplementedError is not an answer either."""
    assert not is_error(exc, NotImplementedError), f"{name}: {exc!r} is a stub"
    for typed in TYPED_ERRORS:
        assert not is_error(exc, typed), f"{name}: {exc!r} is typed {typed.__name__}"
