"""The API surface on the command and query buses (decision 0025 CQ11, TR-54): every operation
has exactly one handler, and the wave 6 surface (decision 0014 section 21 rulings 3, 6 and 7): the
two listings, diagnostics from rows that exist, and the audit event of every API mutation.

The doubles here are small and record what they were asked; the store double honours the limit it
is given, as the SQL does, so a handler that asks for too much is seen in the result."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from provisioning.core.application import (
    ADMIN_ROLE,
    DIAGNOSTIC_COMMAND_LIMIT,
    DIAGNOSTIC_STEP_LIMIT,
    MAX_PAGE_SIZE,
    OPERATION_COMMANDS,
    OPERATION_QUERIES,
    AcceptanceDecider,
    AcceptCommandParams,
    AcceptedCommand,
    Action,
    ActionDenied,
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
    BuildDispatch,
    BuildReleaseCommand,
    BuildStarted,
    CommandBus,
    CommandKey,
    CorruptRow,
    DependencyUnavailable,
    Deployment,
    DeployReleaseCommand,
    DiagnosticFacts,
    EnabledOperations,
    GetReleaseDiagnosticsQuery,
    LimitReached,
    ListCommandsQuery,
    ListReleasesQuery,
    NotFound,
    OperationDeps,
    OperationNotAvailable,
    Principal,
    ProjectAccessDenied,
    PromoteReleaseCommand,
    QueryBus,
    ReleaseCursor,
    ReleaseKey,
    ReleaseWithSteps,
    RollbackReleaseCommand,
    ServiceAudit,
    SourceEventOutcome,
    SourceEventStatus,
    register_operations,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    IdempotencyConflict,
    InvalidId,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    new_command,
)

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
OTHER_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000002")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
SUBJECT = "alice"
TOKEN = "tok-" + "s" * 40  # never given to a handler; it must never appear in an audit event
LOG_WITH_CREDENTIAL = "https://builder:hunter2@jenkins.example/job/site/7/console"
FAILURE_TEXT = "npm ERR! password=hunter2 in .env"


def _principal() -> Principal:
    return Principal(subject=SUBJECT, scopes=("provisioning",))


def _uuid(n: int) -> UUID:
    return UUID(f"0190a1b2-0000-7000-8000-{n:012x}")


def _release(n: int, project_id: UUID = PROJECT, *, failed: bool = False) -> Release:
    return Release(
        id=_uuid(0x1000 + n),
        project_id=project_id,
        generation_request_id=_uuid(0x2000 + n),
        repository_id=42,
        git_commit_sha="a" * 40,
        status=ReleaseStatus.FAILED if failed else ReleaseStatus.ACCEPTED,
        created_at=T0 + timedelta(minutes=n),
        updated_at=T0 + timedelta(minutes=n),
        failure_code="build_failed" if failed else None,
        failure_summary=FAILURE_TEXT if failed else None,
    )


def _command(number: int, project_id: UUID = PROJECT) -> Command:
    return new_command(
        command_id=_uuid(0x3000 + number),
        request=CommandRequest(
            project_id=project_id,
            kind=CommandKind.DEPLOY,
            release_id=RELEASE,
            idempotency_key=f"key-{number}",
        ),
        requested_by_subject="someone",
        command_number=number,
        now=T0,
    )


def _step(n: int, release_id: UUID = RELEASE) -> ReleaseStep:
    return ReleaseStep(
        id=_uuid(0x4000 + n),
        release_id=release_id,
        name=ReleaseStepName.BUILD,
        attempt=n,
        status=ReleaseStepStatus.FAILED,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=1),
        deadline_at=T0 + timedelta(minutes=20),
        failure_code="build_failed",
        log_ref=LOG_WITH_CREDENTIAL,
    )


class _Policy:
    def __init__(self) -> None:
        self.denied: set[Action] = set()

    def permit(self, principal: Principal, action: Action) -> None:
        if action in self.denied:
            raise ActionDenied(str(action))


class _Access:
    def __init__(self) -> None:
        self.allowed = {PROJECT}
        self.asked: list[tuple[UUID, Action]] = []

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        self.asked.append((project_id, action))
        if project_id not in self.allowed:
            raise ProjectAccessDenied("no")


class _Entitlement:
    def __init__(self) -> None:
        self.error: Exception | None = None

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        if self.error is not None:
            raise self.error


class _Acceptance:
    def __init__(self) -> None:
        self.stored: Command | None = None
        self.accepted: list[AcceptCommandParams] = []

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        return self.stored

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        self.accepted.append(params)
        command = new_command(
            command_id=params.command_id,
            request=params.request,
            requested_by_subject=params.requested_by_subject,
            command_number=1,
            now=params.now,
        )
        return AcceptedCommand(command=command, replayed=False)


class _Reads:
    """Rows of every project; each read filters on the project and honours ``limit`` like the
    SQL. ``cheat`` makes a read return rows it should not (a store bug)."""

    def __init__(self) -> None:
        self.releases: list[Release] = [_release(n) for n in range(1, 8)]
        self.releases.append(_release(99, OTHER_PROJECT))
        self.commands: list[Command] = [_command(n) for n in range(1, 8)]
        self.steps: list[ReleaseStep] = [_step(n) for n in range(1, 4)]
        self.source: SourceEventOutcome | None = SourceEventOutcome(
            project_id=PROJECT,
            generation_request_id=REQUEST,
            status=SourceEventStatus.ACCEPTED,
            outcome_code=None,
            release_id=RELEASE,
            attempts=1,
            mismatched_repeat_count=2,
            received_at=T0,
            finished_at=T0 + timedelta(seconds=1),
        )
        self.release_row = dataclasses.replace(
            _release(0, failed=True), id=RELEASE, generation_request_id=REQUEST
        )
        self.asked: list[tuple[str, dict[str, Any]]] = []
        self.cheat: str = ""

    async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
        raise AssertionError("not used")

    async def read_command(self, key: CommandKey) -> Command:
        raise AssertionError("not used")

    async def read_deployment(self, project_id: UUID) -> Deployment:
        raise AssertionError("not used")

    async def list_releases(
        self, project_id: UUID, *, limit: int, after: ReleaseCursor | None
    ) -> tuple[Release, ...]:
        self.asked.append(("list_releases", {"limit": limit, "after": after}))
        rows = sorted(self.releases, key=lambda r: (r.created_at, r.id), reverse=True)
        if self.cheat != "foreign":
            rows = [r for r in rows if r.project_id == project_id]
        if after is not None and self.cheat != "ignore_cursor":
            rows = [r for r in rows if (r.created_at, r.id) < (after.created_at, after.release_id)]
        return tuple(rows if self.cheat == "no_limit" else rows[:limit])

    async def list_commands(
        self, project_id: UUID, *, limit: int, after: int | None
    ) -> tuple[Command, ...]:
        self.asked.append(("list_commands", {"limit": limit, "after": after}))
        rows = sorted(self.commands, key=lambda c: c.command_number, reverse=True)
        rows = [c for c in rows if c.project_id == project_id or self.cheat == "foreign"]
        if after is not None and self.cheat != "ignore_cursor":
            rows = [c for c in rows if c.command_number < after]
        return tuple(rows if self.cheat == "no_limit" else rows[:limit])

    async def read_diagnostics(
        self, key: ReleaseKey, *, step_limit: int, command_limit: int
    ) -> DiagnosticFacts:
        self.asked.append(
            ("read_diagnostics", {"step_limit": step_limit, "command_limit": command_limit})
        )
        if key.project_id != PROJECT or key.release_id != RELEASE:
            raise NotFound("release")
        steps = sorted(self.steps, key=lambda s: (s.name, s.attempt), reverse=True)
        commands = sorted(self.commands, key=lambda c: c.command_number, reverse=True)
        return DiagnosticFacts(
            release=self.release_row,
            steps=tuple(steps[:step_limit]),
            commands=tuple(commands[:command_limit]),
            source_event=self.source,
        )


class _Clock:
    def now(self) -> datetime:
        return T0


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> UUID:
        self.n += 1
        return _uuid(0x5000 + self.n)


class _Builds:
    def __init__(self) -> None:
        self.error: Exception | None = None

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        if self.error is not None:
            raise self.error
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=_uuid(0x6000),
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )


class _BuildReads(_Reads):
    async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
        return ReleaseWithSteps(release=self.release_row, steps=())


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.alerts: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        self.alerts.append(event)


@dataclasses.dataclass
class _Fixture:
    commands: CommandBus
    queries: QueryBus
    policy: _Policy
    access: _Access
    entitlement: _Entitlement
    acceptance: _Acceptance
    reads: _Reads
    builds: _Builds
    audit: _Audit
    deps: OperationDeps


def _fixture(*, audited: bool = True, history: bool = True) -> _Fixture:
    policy, access, entitlement = _Policy(), _Access(), _Entitlement()
    acceptance, reads, builds, audit = _Acceptance(), _BuildReads(), _Builds(), _Audit()
    deps = OperationDeps(
        policy=policy,
        access=access,
        entitlement=entitlement,
        acceptance=acceptance,
        reads=reads,
        history=reads if history else None,
        clock=_Clock(),
        ids=_Ids(),
        builds=builds,
        enabled=EnabledOperations(tuple(Action)),
        command_deadline=timedelta(minutes=15),
        audit=ServiceAudit(log=audit, clock=_Clock()) if audited else None,
    )
    commands, queries = CommandBus(), QueryBus()
    register_operations(commands, queries, deps)
    return _Fixture(
        commands, queries, policy, access, entitlement, acceptance, reads, builds, audit, deps
    )


# --- the buses ------------------------------------------------------------------------------


def test_every_operation_has_exactly_one_handler() -> None:
    """TR-54: each of the fourteen actions has one intent, a command or a query, and
    ``register_operations`` registers exactly one handler for it; a second registration of the
    same intent is refused."""
    f = _fixture()
    assert set(OPERATION_COMMANDS) | set(OPERATION_QUERIES) == set(Action)
    assert not set(OPERATION_COMMANDS) & set(OPERATION_QUERIES)
    assert f.commands.kinds() == frozenset(OPERATION_COMMANDS.values())
    assert f.queries.kinds() == frozenset(OPERATION_QUERIES.values())
    with pytest.raises(ValueError):
        register_operations(f.commands, f.queries, f.deps)


# --- listings -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_releases_pages_newest_first_and_stops() -> None:
    """FR-38.1: list releases pages newest first and stops."""
    f = _fixture()
    seen: list[UUID] = []
    after: ReleaseCursor | None = None
    pages = 0
    while True:
        page = await f.queries.send(
            ListReleasesQuery(_principal(), project_id=PROJECT, page_size=3, after=after)
        )
        pages += 1
        assert len(page.items) <= 3
        assert all(item.project_id == PROJECT for item in page.items)
        seen.extend(item.release_id for item in page.items)
        after = page.next_cursor
        if after is None:
            break
    assert pages == 3  # 7 releases: 3, 3, 1
    assert seen == [_release(n).id for n in range(7, 0, -1)]
    # The store is asked for one row more than the page, to know whether another page follows.
    assert [asked["limit"] for name, asked in f.reads.asked] == [4, 4, 4]


@pytest.mark.asyncio
async def test_list_releases_summary_has_no_failure_text() -> None:
    f = _fixture()
    f.reads.releases = [_release(1, failed=True)]
    page = await f.queries.send(
        ListReleasesQuery(_principal(), project_id=PROJECT, page_size=5, after=None)
    )
    assert page.next_cursor is None
    (item,) = page.items
    assert item.status is ReleaseStatus.FAILED and item.failure_code == "build_failed"
    assert FAILURE_TEXT not in repr(item)


@pytest.mark.asyncio
async def test_list_commands_pages_newest_first() -> None:
    """FR-38: list commands pages newest first."""
    f = _fixture()
    first = await f.queries.send(
        ListCommandsQuery(_principal(), project_id=PROJECT, page_size=5, after=None)
    )
    assert [c.command_number for c in first.items] == [7, 6, 5, 4, 3]
    assert first.next_after == 3
    second = await f.queries.send(
        ListCommandsQuery(_principal(), project_id=PROJECT, page_size=5, after=first.next_after)
    )
    assert [c.command_number for c in second.items] == [2, 1]
    assert second.next_after is None
    # The views carry no key, fingerprint or requester.
    assert "key-" not in repr(first) and "someone" not in repr(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [Action.LIST_RELEASES, Action.LIST_COMMANDS])
async def test_a_listing_checks_project_access_before_reading(action: Action) -> None:
    # Planted defect "a listing that ignores project access": without the gate, the other
    # project's rows would be read and returned.
    f = _fixture()
    with pytest.raises(ProjectAccessDenied):
        if action is Action.LIST_RELEASES:
            await f.queries.send(
                ListReleasesQuery(_principal(), project_id=OTHER_PROJECT, page_size=5, after=None)
            )
        else:
            await f.queries.send(
                ListCommandsQuery(_principal(), project_id=OTHER_PROJECT, page_size=5, after=None)
            )
    assert f.access.asked == [(OTHER_PROJECT, action)]
    assert f.reads.asked == []


@pytest.mark.asyncio
async def test_a_listing_checks_the_action_permission() -> None:
    f = _fixture()
    f.policy.denied = {Action.LIST_COMMANDS}
    with pytest.raises(ActionDenied):
        await f.queries.send(
            ListCommandsQuery(_principal(), project_id=PROJECT, page_size=5, after=None)
        )
    assert f.reads.asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cheat", ["foreign", "no_limit", "ignore_cursor"])
async def test_a_store_row_outside_the_request_is_never_returned(cheat: str) -> None:
    f = _fixture()
    f.reads.cheat = cheat
    cursor = ReleaseCursor(created_at=T0 + timedelta(minutes=5), release_id=_release(5).id)
    with pytest.raises(Exception) as releases_err:
        await f.queries.send(
            ListReleasesQuery(
                _principal(),
                project_id=PROJECT,
                page_size=3 if cheat == "no_limit" else 20,
                after=cursor if cheat == "ignore_cursor" else None,
            )
        )
    assert not isinstance(releases_err.value, AssertionError)
    f.reads.commands.append(_command(50, OTHER_PROJECT))
    with pytest.raises(Exception) as commands_err:
        await f.queries.send(
            ListCommandsQuery(
                _principal(),
                project_id=PROJECT,
                page_size=3 if cheat == "no_limit" else 50,
                after=6 if cheat == "ignore_cursor" else None,
            )
        )
    assert not isinstance(commands_err.value, AssertionError)
    if cheat == "no_limit":
        assert isinstance(releases_err.value, CorruptRow)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, -1, MAX_PAGE_SIZE + 1])
async def test_a_page_size_outside_the_bound_is_refused_before_reading(size: int) -> None:
    f = _fixture()
    with pytest.raises(ValueError):
        await f.queries.send(
            ListReleasesQuery(_principal(), project_id=PROJECT, page_size=size, after=None)
        )
    with pytest.raises(ValueError):
        await f.queries.send(
            ListCommandsQuery(_principal(), project_id=PROJECT, page_size=size, after=None)
        )
    assert f.reads.asked == []


@pytest.mark.asyncio
async def test_a_nil_cursor_release_is_an_invalid_id() -> None:
    f = _fixture()
    with pytest.raises(InvalidId):
        await f.queries.send(
            ListReleasesQuery(
                _principal(),
                project_id=PROJECT,
                page_size=5,
                after=ReleaseCursor(created_at=T0, release_id=UUID(int=0)),
            )
        )
    with pytest.raises(InvalidId):
        await f.queries.send(
            ListCommandsQuery(_principal(), project_id=PROJECT, page_size=5, after=0)
        )


# --- diagnostics ----------------------------------------------------------------------------


def _diagnostics(project_id: UUID = PROJECT) -> GetReleaseDiagnosticsQuery:
    return GetReleaseDiagnosticsQuery(_principal(), project_id=project_id, release_id=RELEASE)


@pytest.mark.asyncio
async def test_diagnostics_are_built_from_the_rows() -> None:
    f = _fixture()
    view = await f.queries.send(_diagnostics())
    assert view.release_id == RELEASE and view.project_id == PROJECT
    assert view.release_status is ReleaseStatus.FAILED
    assert view.release_failure_code == "build_failed"
    assert [s.attempt for s in view.steps] == [3, 2, 1]
    assert all(s.failure_code == "build_failed" for s in view.steps)
    assert not view.steps_truncated
    assert [c.command_number for c in view.commands] == [7, 6, 5, 4, 3, 2, 1]
    assert not view.commands_truncated
    assert view.source_event is not None
    assert view.source_event.status is SourceEventStatus.ACCEPTED
    assert view.source_event.mismatched_repeat_count == 2
    assert view.source_event.attempts == 1


@pytest.mark.asyncio
async def test_diagnostics_are_redacted() -> None:
    """FR-41.1: diagnostics are redacted."""
    f = _fixture()
    view = await f.queries.send(_diagnostics())
    text = repr(view)
    for secret in (LOG_WITH_CREDENTIAL, FAILURE_TEXT, "hunter2", "key-1", "someone", "a" * 40):
        assert secret not in text


@pytest.mark.asyncio
async def test_diagnostics_are_bounded() -> None:
    # Planted defect "diagnostics without a bound": a handler that asks the store for every row
    # returns more than the fixed number of entries.
    """FR-41.1: diagnostics are bounded."""
    f = _fixture()
    f.reads.steps = [_step(n) for n in range(1, 60)]
    f.reads.commands = [_command(n) for n in range(1, 60)]
    view = await f.queries.send(_diagnostics())
    assert len(view.steps) == DIAGNOSTIC_STEP_LIMIT
    assert len(view.commands) == DIAGNOSTIC_COMMAND_LIMIT
    assert view.steps_truncated and view.commands_truncated
    # The newest rows are kept.
    assert view.steps[0].attempt == 59
    assert view.commands[0].command_number == 59
    (asked,) = [a for name, a in f.reads.asked if name == "read_diagnostics"]
    assert asked == {
        "step_limit": DIAGNOSTIC_STEP_LIMIT + 1,
        "command_limit": DIAGNOSTIC_COMMAND_LIMIT + 1,
    }


@pytest.mark.asyncio
async def test_diagnostics_refuse_a_store_that_ignores_the_bound() -> None:
    f = _fixture()
    f.reads.steps = [_step(n) for n in range(1, 60)]
    original = f.reads.read_diagnostics

    async def unbounded(key: ReleaseKey, *, step_limit: int, command_limit: int) -> DiagnosticFacts:
        return await original(key, step_limit=1000, command_limit=1000)

    f.reads.read_diagnostics = unbounded  # type: ignore[method-assign]  # a faulty store
    with pytest.raises(CorruptRow):
        await f.queries.send(_diagnostics())


@pytest.mark.asyncio
async def test_diagnostics_are_project_scoped() -> None:
    f = _fixture()
    with pytest.raises(ProjectAccessDenied):
        await f.queries.send(_diagnostics(OTHER_PROJECT))
    assert f.reads.asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("what", ["release", "step", "command", "source_event"])
async def test_diagnostics_never_return_a_row_of_another_project_or_release(what: str) -> None:
    f = _fixture()
    if what == "release":
        f.reads.release_row = dataclasses.replace(f.reads.release_row, project_id=OTHER_PROJECT)
    elif what == "step":
        f.reads.steps.append(_step(9, release_id=_uuid(0x7777)))
    elif what == "command":
        f.reads.commands.append(_command(40, OTHER_PROJECT))
    else:
        assert f.reads.source is not None
        f.reads.source = dataclasses.replace(f.reads.source, generation_request_id=_uuid(0x8888))
    with pytest.raises(Exception) as err:
        await f.queries.send(_diagnostics())
    assert not isinstance(err.value, (AssertionError, NotFound, ProjectAccessDenied))


@pytest.mark.asyncio
async def test_diagnostics_without_a_source_event_row() -> None:
    f = _fixture()
    f.reads.source = None
    view = await f.queries.send(_diagnostics())
    assert view.source_event is None


@pytest.mark.asyncio
async def test_diagnostics_of_a_nil_release_is_an_invalid_id() -> None:
    f = _fixture()
    with pytest.raises(InvalidId):
        await f.queries.send(
            GetReleaseDiagnosticsQuery(_principal(), project_id=PROJECT, release_id=UUID(int=0))
        )
    assert f.reads.asked == []


# --- audit ----------------------------------------------------------------------------------


type _MutationCommand = DeployReleaseCommand | PromoteReleaseCommand | RollbackReleaseCommand


def _mutation[C: _MutationCommand](
    kind: type[C], key: str = "key-1", *, actor_kind: AuditActorKind = AuditActorKind.USER
) -> C:
    return kind(
        principal=_principal(),
        project_id=PROJECT,
        release_id=RELEASE,
        idempotency_key=key,
        correlation_id="corr-1",
        actor_kind=actor_kind,
    )


def _assert_no_secret(event: AuditEvent) -> None:
    for value in dataclasses.asdict(event).values():
        assert TOKEN not in str(value)


@pytest.mark.asyncio
async def test_an_accepted_mutation_is_audited_with_the_subject_as_actor() -> None:
    f = _fixture()
    accepted = await f.commands.send(_mutation(DeployReleaseCommand))
    (event,) = f.audit.events
    assert event == AuditEvent(
        actor_kind=AuditActorKind.USER,
        actor=SUBJECT,
        action=AuditAction.DEPLOY,
        project_id=PROJECT,
        release_id=RELEASE,
        command_id=accepted.command_id,
        result=AuditResult.ACCEPTED,
        failure_code=None,
        at=T0,
        correlation_id="corr-1",
    )
    _assert_no_secret(event)
    assert f.audit.alerts == []


@pytest.mark.asyncio
async def test_every_command_mutation_is_audited_by_an_agent() -> None:
    f = _fixture()
    await f.commands.send(_mutation(PromoteReleaseCommand, actor_kind=AuditActorKind.AGENT))
    (event,) = f.audit.events
    assert (event.action, event.actor_kind, event.result) == (
        AuditAction.PROMOTE,
        AuditActorKind.AGENT,
        AuditResult.ACCEPTED,
    )


@pytest.mark.asyncio
async def test_an_admins_rollback_through_an_agent_is_audited_as_an_operator() -> None:
    """FR-50.1: only an ADMIN rolls back, on any project; the audit event names an operator
    whichever transport the command came through (review finding M-2)."""
    f = _fixture()
    command = _mutation(RollbackReleaseCommand, actor_kind=AuditActorKind.AGENT)
    admin = Principal(subject=SUBJECT, scopes=("provisioning", ADMIN_ROLE))
    await f.commands.send(dataclasses.replace(command, principal=admin))
    (event,) = f.audit.events
    assert (event.action, event.actor_kind, event.actor, event.result) == (
        AuditAction.ROLLBACK,
        AuditActorKind.OPERATOR,
        SUBJECT,
        AuditResult.ACCEPTED,
    )


@pytest.mark.asyncio
async def test_a_refused_mutation_is_audited_with_its_code() -> None:
    f = _fixture()
    f.access.allowed = set()
    with pytest.raises(ProjectAccessDenied):
        await f.commands.send(_mutation(DeployReleaseCommand))
    assert f.acceptance.accepted == []
    (event,) = f.audit.events
    assert event.result is AuditResult.REFUSED
    assert event.failure_code == "project_access_denied"
    assert event.command_id is None and event.actor == SUBJECT


@pytest.mark.asyncio
async def test_a_permission_refusal_is_audited() -> None:
    f = _fixture()
    f.policy.denied = {Action.PROMOTE_RELEASE}
    with pytest.raises(ActionDenied):
        await f.commands.send(_mutation(PromoteReleaseCommand))
    (event,) = f.audit.events
    assert (event.result, event.failure_code) == (AuditResult.REFUSED, "action_denied")


@pytest.mark.asyncio
async def test_an_entitlement_refusal_is_audited() -> None:
    f = _fixture()
    f.entitlement.error = LimitReached("trial over")
    with pytest.raises(LimitReached):
        await f.commands.send(_mutation(DeployReleaseCommand))
    (event,) = f.audit.events
    assert (event.result, event.failure_code) == (AuditResult.REFUSED, "limit_reached")


@pytest.mark.asyncio
async def test_a_conflicted_mutation_is_audited() -> None:
    f = _fixture()
    f.acceptance.stored = dataclasses.replace(
        _command(3), command_number=3, request_fingerprint="0" * 64
    )
    with pytest.raises(IdempotencyConflict):
        await f.commands.send(_mutation(DeployReleaseCommand, "key-3"))
    (event,) = f.audit.events
    assert (event.result, event.failure_code) == (AuditResult.REFUSED, "idempotency_conflict")


@pytest.mark.asyncio
async def test_an_unavailable_dependency_is_audited_as_failed() -> None:
    f = _fixture()
    f.entitlement.error = RuntimeError("payment down: secret-url")
    with pytest.raises(DependencyUnavailable):
        await f.commands.send(_mutation(DeployReleaseCommand))
    (event,) = f.audit.events
    assert (event.result, event.failure_code) == (AuditResult.FAILED, "dependency_unavailable")
    assert "secret-url" not in repr(event)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason", "deadline"),
    [
        (DependencyUnavailable("x", upstream_reason="PAYMENT_PAUSED"), "PAYMENT_PAUSED", False),
        (DependencyUnavailable("x", deadline=True), None, True),
        (RuntimeError("anything"), None, False),
    ],
)
async def test_an_entitlement_failure_keeps_the_upstream_answer(
    error: Exception, reason: str | None, deadline: bool
) -> None:
    """Decision 0023 G14: the handler re-raises its own error but keeps what Payment said."""
    f = _fixture()
    f.entitlement.error = error
    with pytest.raises(DependencyUnavailable) as info:
        await f.commands.send(_mutation(DeployReleaseCommand))
    assert (info.value.upstream_reason, info.value.deadline) == (reason, deadline)
    assert info.value.__cause__ is None


@pytest.mark.asyncio
async def test_build_release_is_audited() -> None:
    f = _fixture()
    await f.commands.send(
        BuildReleaseCommand(
            _principal(),
            project_id=PROJECT,
            release_id=RELEASE,
            correlation_id="corr-b",
            actor_kind=AuditActorKind.AGENT,
        )
    )
    f.builds.error = NotFound("release")
    with pytest.raises(NotFound):
        await f.commands.send(
            BuildReleaseCommand(_principal(), project_id=PROJECT, release_id=RELEASE)
        )
    accepted, refused = f.audit.events
    assert (accepted.action, accepted.result, accepted.correlation_id) == (
        AuditAction.BUILD_RELEASE,
        AuditResult.ACCEPTED,
        "corr-b",
    )
    assert accepted.actor_kind is AuditActorKind.AGENT and accepted.command_id is None
    assert (refused.result, refused.failure_code, refused.correlation_id) == (
        AuditResult.REFUSED,
        "not_found",
        None,
    )


@pytest.mark.asyncio
async def test_reads_are_not_audited() -> None:
    f = _fixture()
    await f.queries.send(
        ListCommandsQuery(_principal(), project_id=PROJECT, page_size=5, after=None)
    )
    await f.queries.send(_diagnostics())
    assert f.audit.events == []


@pytest.mark.asyncio
async def test_without_an_audit_a_mutation_is_still_accepted() -> None:
    f = _fixture(audited=False)
    accepted = await f.commands.send(_mutation(DeployReleaseCommand))
    assert accepted.status is CommandStatus.ACCEPTED
    assert f.audit.events == []


@pytest.mark.asyncio
async def test_without_a_history_store_the_three_reads_are_not_available() -> None:
    f = _fixture(history=False)
    with pytest.raises(OperationNotAvailable):
        await f.queries.send(
            ListReleasesQuery(_principal(), project_id=PROJECT, page_size=5, after=None)
        )
    with pytest.raises(OperationNotAvailable):
        await f.queries.send(
            ListCommandsQuery(_principal(), project_id=PROJECT, page_size=5, after=None)
        )
    with pytest.raises(OperationNotAvailable):
        await f.queries.send(_diagnostics())
    # The gate still runs first: a caller without access learns nothing more.
    f.access.allowed = set()
    with pytest.raises(ProjectAccessDenied):
        await f.queries.send(_diagnostics())
