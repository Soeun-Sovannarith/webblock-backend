"""The admin use cases (decision 0023 G9 to G11, G13; Notion "5 · Provisioning"): an admin's
suspend and resume of one website (key ``admin:<key>``, reason ``admin_suspended``, the admin's
subject), the acceptance of a redeploy of the live release, and the admin read of the commands
with their requester."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.application import (
    ADMIN_KEY_PREFIX,
    ADMIN_ROLE,
    MAX_ADMIN_KEY_BYTES,
    AcceptanceDecider,
    AcceptanceFacts,
    AcceptCommandParams,
    AcceptedCommand,
    AcceptRedeployParams,
    Action,
    ActionDenied,
    AdminAcceptor,
    AdminCommandPage,
    AdminCommandView,
    AdminDeps,
    AdminListCommandsHandler,
    AdminListCommandsQuery,
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
    BuildStarted,
    CommandBus,
    CommandView,
    CorruptRow,
    DiagnosticFacts,
    EnabledOperations,
    IdempotencyKeyTooLong,
    ListCommandsQuery,
    NotFound,
    OperationDeps,
    OperationGate,
    OperationInProgress,
    OperationNotAvailable,
    Principal,
    QueryBus,
    RedeployNotAllowed,
    RedeployReleaseCommand,
    ReleaseCursor,
    ReleaseKey,
    ResumeWebsiteCommand,
    ServiceAudit,
    SuspendWebsiteCommand,
    Unauthenticated,
    register_operations,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    IdempotencyConflict,
    OfflineReason,
    ProjectHosting,
    Release,
    ReleaseStatus,
    RouteNotStable,
    RoutingStatus,
    new_command,
)

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DEADLINE = timedelta(minutes=15)

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
OTHER_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000002")
LIVE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
NEWER_LIVE = UUID("0190a1b2-0000-7000-8000-0000000000a2")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
STORED = UUID("0190a1b2-0000-7000-8000-0000000000c9")
FIRST_ID = UUID("0190a1b2-0000-7000-8000-0000000000c1")

ADMIN = Principal(subject="admin-1", scopes=(ADMIN_ROLE,))
USER = Principal(subject="user-1", scopes=("USER",))


# ---- doubles ----


class _Policy:
    """Permits every action: the gate's own admin-only rule is what these tests exercise."""

    def permit(self, principal: Principal, action: Action) -> None:
        del principal, action


class _Access:
    """Project access that records every call; an admin-only action must never call it."""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, Action]] = []

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        del principal
        self.calls.append((project_id, action))


class _Clock:
    def now(self) -> datetime:
        return T0


class _Ids:
    def __init__(self) -> None:
        self._next = FIRST_ID.int

    def new_id(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class _Store:
    """``AcceptanceStore`` and ``RedeployStore`` in memory: the decide function runs on the facts
    the test set, as the PostgreSQL store runs it on the facts it loaded under the lock. No
    facts is a project without a hosting row (``NotFound``)."""

    def __init__(self, facts: AcceptanceFacts | None) -> None:
        self.facts = facts
        self.by_key: Command | None = None
        self.calls: list[str] = []
        self.inserted: list[Command] = []
        self.params: list[object] = []

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        self.calls.append("find")
        if self.by_key is not None and self.by_key.idempotency_key == request.idempotency_key:
            return self.by_key
        return None

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        self.calls.append("accept")
        self.params.append(params)
        return self._decide(decide)

    async def accept_redeploy(
        self, params: AcceptRedeployParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        self.calls.append("accept_redeploy")
        self.params.append(params)
        return self._decide(decide)

    def _decide(self, decide: AcceptanceDecider) -> AcceptedCommand:
        if self.facts is None:
            raise NotFound("no hosting row")
        decision = decide(self.facts)
        if decision.insert:
            self.inserted.append(decision.command)
        return AcceptedCommand(command=decision.command, replayed=not decision.insert)


class _History:
    """``HistoryStore`` returning fixed command rows; it records the limit and cursor asked."""

    def __init__(self, rows: tuple[Command, ...]) -> None:
        self.rows = rows
        self.asked: list[tuple[UUID, int, int | None]] = []

    async def list_releases(
        self, project_id: UUID, *, limit: int, after: ReleaseCursor | None
    ) -> tuple[Release, ...]:
        raise AssertionError("not used")

    async def list_commands(
        self, project_id: UUID, *, limit: int, after: int | None
    ) -> tuple[Command, ...]:
        self.asked.append((project_id, limit, after))
        below = tuple(r for r in self.rows if after is None or r.command_number < after)
        return below[:limit]

    async def read_diagnostics(
        self, key: ReleaseKey, *, step_limit: int, command_limit: int
    ) -> DiagnosticFacts:
        raise AssertionError("not used")


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("an admin operation raises no alert")


# ---- fixtures ----


def _release(release_id: UUID, status: ReleaseStatus = ReleaseStatus.LIVE) -> Release:
    return Release(
        id=release_id,
        project_id=PROJECT,
        generation_request_id=REQUEST,
        repository_id=42,
        git_commit_sha="0123456789abcdef0123456789abcdef01234567",
        status=status,
        created_at=T0,
        updated_at=T0,
        container_image_digest="sha256:" + "a" * 64,
        promoted_at=T0 if status is ReleaseStatus.LIVE else None,
    )


def _hosting(
    *,
    live: UUID | None = LIVE,
    routing: RoutingStatus = RoutingStatus.STABLE,
    reasons: frozenset[OfflineReason] = frozenset(),
    number: int = 4,
) -> ProjectHosting:
    return ProjectHosting(
        project_id=PROJECT,
        last_command_number=number,
        routing_status=routing,
        hosting_status=HostingStatus.OFFLINE if reasons else HostingStatus.ONLINE,
        updated_at=T0,
        live_release_id=live,
        offline_reasons=reasons,
        subdomain="shop",
    )


def _facts(
    hosting: ProjectHosting | None = None,
    *,
    in_flight: Command | None = None,
    by_key: Command | None = None,
) -> AcceptanceFacts:
    hosting = hosting or _hosting()
    live = None if hosting.live_release_id is None else _release(hosting.live_release_id)
    return AcceptanceFacts(hosting=hosting, by_key=by_key, in_flight=in_flight, live=live)


def _command(
    kind: CommandKind,
    key: str,
    *,
    number: int,
    release: UUID | None = None,
    by: str | None = "admin-1",
    command_id: UUID = STORED,
) -> Command:
    return new_command(
        command_id=command_id,
        request=CommandRequest(
            project_id=PROJECT, kind=kind, release_id=release, idempotency_key=key
        ),
        requested_by_subject=by,
        command_number=number,
        now=T0,
    )


class _AdminOperations:
    """The three admin mutations (``AdminAcceptor``) and the admin read
    (``AdminListCommandsHandler``) on one gate, as the former ``AdminOperations``."""

    def __init__(self, acceptor: AdminAcceptor, listing: AdminListCommandsHandler) -> None:
        self.suspend_website = acceptor.suspend_website
        self.resume_website = acceptor.resume_website
        self.redeploy = acceptor.redeploy
        self._listing = listing

    async def list_commands(
        self, principal: Principal, project_id: UUID, limit: int, cursor: int | None
    ) -> AdminCommandPage:
        return await self._listing.handle(
            AdminListCommandsQuery(principal, project_id, limit, cursor)
        )


def _admin(
    store: _Store, *, history: _History | None = None, access: _Access | None = None
) -> _AdminOperations:
    gate = OperationGate(
        policy=_Policy(), access=access or _Access(), enabled=EnabledOperations(tuple(Action))
    )
    return _AdminOperations(
        AdminAcceptor(
            AdminDeps(
                gate=gate,
                acceptance=store,
                redeploys=store,
                clock=_Clock(),
                ids=_Ids(),
                command_deadline=DEADLINE,
            )
        ),
        AdminListCommandsHandler(gate=gate, history=history),
    )


# ---- suspend and resume ----


async def test_suspend_stores_the_admin_key_reason_and_subject() -> None:
    """FR-33.1: suspend stores the admin key reason and subject."""
    store = _Store(_facts())
    access = _Access()
    accepted = await _admin(store, access=access).suspend_website(ADMIN, PROJECT, "k-1")
    assert not accepted.replayed
    assert accepted.status is CommandStatus.ACCEPTED
    [command] = store.inserted
    assert command.kind is CommandKind.SUSPEND_HOSTING
    assert command.idempotency_key == "admin:k-1"
    assert command.hosting_reason is OfflineReason.ADMIN_SUSPENDED
    assert command.requested_by_subject == "admin-1"
    assert command.release_id is None
    assert command.command_number == 5
    assert accepted.command_id == command.id
    assert access.calls == []  # an admin acts on any project without the Project check


async def test_resume_stores_the_admin_key_and_names_only_admin_suspended() -> None:
    """FR-34: resume stores the admin key and names only admin suspended."""
    store = _Store(_facts(_hosting(reasons=frozenset({OfflineReason.ADMIN_SUSPENDED}))))
    await _admin(store).resume_website(ADMIN, PROJECT, "k-2")
    [command] = store.inserted
    assert command.kind is CommandKind.RESUME_HOSTING
    assert command.idempotency_key == "admin:k-2"
    assert command.hosting_reason is OfflineReason.ADMIN_SUSPENDED
    assert command.requested_by_subject == "admin-1"


async def test_resume_without_admin_suspended_is_still_accepted() -> None:
    """0021 D5: the worker ends it skipped hosting_already_online; the API accepts it."""
    store = _Store(_facts(_hosting(reasons=frozenset({OfflineReason.BILLING}))))
    accepted = await _admin(store).resume_website(ADMIN, PROJECT, "k-3")
    assert not accepted.replayed
    assert store.inserted[0].hosting_reason is OfflineReason.ADMIN_SUSPENDED


async def test_suspend_of_a_site_offline_for_another_reason_is_accepted() -> None:
    """0021 D7: the worker records the reason without a route change."""
    store = _Store(_facts(_hosting(reasons=frozenset({OfflineReason.PROJECT_DELETED}))))
    await _admin(store).suspend_website(ADMIN, PROJECT, "k-4")
    assert store.inserted[0].kind is CommandKind.SUSPEND_HOSTING


@pytest.mark.parametrize("key", ["project-deleted", "owner-status:3", "admin:x"])
async def test_an_admin_key_never_takes_a_system_key(key: str) -> None:
    store = _Store(_facts())
    await _admin(store).suspend_website(ADMIN, PROJECT, key)
    [command] = store.inserted
    assert command.idempotency_key == ADMIN_KEY_PREFIX + key
    assert command.hosting_reason is OfflineReason.ADMIN_SUSPENDED


async def test_a_key_over_249_bytes_is_too_long_and_249_fits() -> None:
    assert MAX_ADMIN_KEY_BYTES == 249
    store = _Store(_facts())
    with pytest.raises(IdempotencyKeyTooLong):
        await _admin(store).suspend_website(ADMIN, PROJECT, "k" * 250)
    with pytest.raises(IdempotencyKeyTooLong):
        await _admin(store).resume_website(ADMIN, PROJECT, "é" * 125)  # 250 bytes
    assert store.calls == []
    await _admin(store).suspend_website(ADMIN, PROJECT, "k" * 249)
    assert len(store.inserted[0].idempotency_key) == 255


async def test_the_same_admin_key_and_request_returns_the_original() -> None:
    store = _Store(_facts())
    store.by_key = _command(CommandKind.SUSPEND_HOSTING, "admin:k-5", number=2)
    accepted = await _admin(store).suspend_website(ADMIN, PROJECT, "k-5")
    assert accepted.replayed
    assert accepted.command_id == STORED
    assert store.calls == ["find"]


async def test_the_same_admin_key_for_the_other_kind_is_a_reused_key() -> None:
    store = _Store(_facts())
    store.by_key = _command(CommandKind.SUSPEND_HOSTING, "admin:k-6", number=2)
    with pytest.raises(IdempotencyConflict):
        await _admin(store).resume_website(ADMIN, PROJECT, "k-6")
    assert store.inserted == []


async def test_a_replay_found_under_the_lock_returns_the_original() -> None:
    """The key holder appears between the unlocked lookup and the lock."""
    holder = _command(CommandKind.SUSPEND_HOSTING, "admin:k-7", number=2)
    store = _Store(_facts(by_key=holder))
    accepted = await _admin(store).suspend_website(ADMIN, PROJECT, "k-7")
    assert accepted.replayed
    assert store.inserted == []


async def test_an_admin_hosting_command_waits_for_a_command_in_flight_and_a_stable_route() -> None:
    running = _command(CommandKind.DEPLOY, "deploy-1", number=4, release=LIVE, by="user-1")
    with pytest.raises(OperationInProgress) as caught:
        await _admin(_Store(_facts(in_flight=running))).suspend_website(ADMIN, PROJECT, "k-8")
    assert caught.value.command_id == STORED
    with pytest.raises(RouteNotStable):
        await _admin(_Store(_facts(_hosting(routing=RoutingStatus.CHANGING)))).suspend_website(
            ADMIN, PROJECT, "k-9"
        )


@pytest.mark.parametrize("action", ["suspend", "resume", "redeploy", "list"])
async def test_a_user_is_refused_before_any_call(action: str) -> None:
    store = _Store(_facts())
    access = _Access()
    history = _History(())
    admin = _admin(store, history=history, access=access)
    with pytest.raises(ActionDenied):
        if action == "suspend":
            await admin.suspend_website(USER, PROJECT, "k")
        elif action == "resume":
            await admin.resume_website(USER, PROJECT, "k")
        elif action == "redeploy":
            await admin.redeploy(USER, PROJECT, "k")
        else:
            await admin.list_commands(USER, PROJECT, 20, None)
    assert store.calls == []
    assert access.calls == []
    assert history.asked == []


async def test_an_admin_whose_subject_is_no_requester_is_unauthenticated() -> None:
    store = _Store(_facts())
    nameless = Principal(subject="", scopes=(ADMIN_ROLE,))
    with pytest.raises(Unauthenticated):
        await _admin(store).suspend_website(nameless, PROJECT, "k")
    with pytest.raises(Unauthenticated):
        await _admin(store).redeploy(nameless, PROJECT, "k")
    assert store.calls == []


# ---- redeploy ----


async def test_redeploy_names_the_live_release_and_the_subject() -> None:
    """FR-35.1: redeploy names the live release and the subject."""
    store = _Store(_facts())
    accepted = await _admin(store).redeploy(ADMIN, PROJECT, "r-1", correlation_id="corr-1")
    [command] = store.inserted
    assert command.kind is CommandKind.REDEPLOY
    assert command.release_id == LIVE
    assert command.idempotency_key == "r-1"  # the client key class, no prefix
    assert command.requested_by_subject == "admin-1"
    assert command.hosting_reason is None
    assert accepted.command_id == command.id
    [params] = store.params
    assert isinstance(params, AcceptRedeployParams)
    assert params.correlation_id == "corr-1"
    assert params.deadline_at == T0 + DEADLINE


@pytest.mark.parametrize(
    "facts",
    [
        pytest.param(None, id="no-hosting-row"),
        pytest.param(_facts(_hosting(live=None)), id="no-live-release"),
        pytest.param(
            _facts(_hosting(reasons=frozenset({OfflineReason.BILLING}))), id="hosting-offline"
        ),
        pytest.param(
            _facts(_hosting(reasons=frozenset({OfflineReason.ADMIN_SUSPENDED}))),
            id="admin-suspended",
        ),
    ],
)
async def test_redeploy_needs_a_live_release_online(facts: AcceptanceFacts | None) -> None:
    """FR-35.2: redeploy needs a live release online."""
    store = _Store(facts)
    with pytest.raises(RedeployNotAllowed):
        await _admin(store).redeploy(ADMIN, PROJECT, "r-2")
    assert store.inserted == []


async def test_redeploy_needs_a_stable_route_and_no_command_in_flight() -> None:
    with pytest.raises(RouteNotStable):
        await _admin(_Store(_facts(_hosting(routing=RoutingStatus.UNCERTAIN)))).redeploy(
            ADMIN, PROJECT, "r-3"
        )
    running = _command(CommandKind.SUSPEND_HOSTING, "ev-1", number=4, by=None)
    with pytest.raises(OperationInProgress):
        await _admin(_Store(_facts(in_flight=running))).redeploy(ADMIN, PROJECT, "r-4")


async def test_redeploy_same_key_returns_the_original_after_the_live_release_changed() -> None:
    """The client sends no release, so the same key is the same request whatever is live now."""
    holder = _command(CommandKind.REDEPLOY, "r-5", number=3, release=LIVE)
    store = _Store(_facts(_hosting(live=NEWER_LIVE), by_key=holder))
    accepted = await _admin(store).redeploy(ADMIN, PROJECT, "r-5")
    assert accepted.replayed
    assert accepted.command_id == STORED
    assert store.inserted == []


async def test_redeploy_key_of_another_client_command_is_a_reused_key() -> None:
    holder = _command(CommandKind.DEPLOY, "r-6", number=3, release=LIVE, by="user-1")
    with pytest.raises(IdempotencyConflict):
        await _admin(_Store(_facts(by_key=holder))).redeploy(ADMIN, PROJECT, "r-6")


async def test_redeploy_without_its_store_is_not_available() -> None:
    gate = OperationGate(policy=_Policy(), access=_Access(), enabled=EnabledOperations(Action))
    admin = AdminAcceptor(
        AdminDeps(
            gate=gate,
            acceptance=_Store(_facts()),
            clock=_Clock(),
            ids=_Ids(),
            command_deadline=DEADLINE,
        )
    )
    with pytest.raises(OperationNotAvailable):
        await admin.redeploy(ADMIN, PROJECT, "r-7")


# ---- the admin read ----


def _listed(number: int, *, project: UUID = PROJECT) -> Command:
    command = _command(
        CommandKind.SUSPEND_HOSTING,
        f"admin:list-{number}",
        number=number,
        command_id=UUID(int=0x0190A1B2_0000_7000_8000_000000000F00 + number),
    )
    return command if project == PROJECT else dataclasses.replace(command, project_id=project)


async def test_the_admin_read_returns_the_subject_and_pages_newest_first() -> None:
    """FR-36.1: the admin read returns the subject and pages newest first."""
    history = _History((_listed(9), _listed(8), _listed(7)))
    admin = _admin(_Store(_facts()), history=history)
    page = await admin.list_commands(ADMIN, PROJECT, 2, None)
    assert [v.command_number for v in page.items] == [9, 8]
    assert all(isinstance(v, AdminCommandView) for v in page.items)
    assert [v.requested_by_subject for v in page.items] == ["admin-1", "admin-1"]
    assert page.next_after == 8
    assert history.asked == [(PROJECT, 3, None)]
    last = await admin.list_commands(ADMIN, PROJECT, 2, 8)
    assert history.asked[-1] == (PROJECT, 3, 8)
    assert [v.command_number for v in last.items] == [7]
    assert last.next_after is None


async def test_the_admin_read_never_returns_a_row_of_another_project_or_out_of_order() -> None:
    with pytest.raises(Exception) as foreign:
        await _admin(
            _Store(None), history=_History((_listed(3, project=OTHER_PROJECT),))
        ).list_commands(ADMIN, PROJECT, 5, None)
    assert not isinstance(foreign.value, (NotFound, ActionDenied))
    with pytest.raises(CorruptRow):
        await _admin(_Store(None), history=_History((_listed(3), _listed(4)))).list_commands(
            ADMIN, PROJECT, 5, None
        )


# ---- through the buses the transports send to ----


class _Unused:
    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        raise AssertionError("entitlement is never asked by an admin operation")

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        raise AssertionError("no build")

    async def read_release(self, key: object) -> None:
        raise AssertionError("not used")

    async def read_command(self, key: object) -> None:
        raise AssertionError("not used")

    async def read_deployment(self, project_id: UUID) -> None:
        raise AssertionError("not used")


def _buses(store: _Store, history: _History, audit: _Audit) -> tuple[CommandBus, QueryBus]:
    unused = _Unused()
    commands, queries = CommandBus(), QueryBus()
    register_operations(
        commands,
        queries,
        OperationDeps(
            policy=_Policy(),
            access=_Access(),
            entitlement=unused,
            acceptance=store,
            reads=unused,  # type: ignore[arg-type]  # the reads are not used here
            clock=_Clock(),
            ids=_Ids(),
            builds=unused,
            enabled=EnabledOperations(tuple(Action)),
            command_deadline=DEADLINE,
            history=history,
            audit=ServiceAudit(log=audit, clock=_Clock()),
            redeploys=store,
        ),
    )
    return commands, queries


async def test_the_buses_run_the_admin_operations_and_audit_them_as_an_operator() -> None:
    store = _Store(_facts())
    audit = _Audit()
    commands, _ = _buses(store, _History(()), audit)
    await commands.send(SuspendWebsiteCommand(ADMIN, PROJECT, "s-1", correlation_id="corr-s"))
    await commands.send(ResumeWebsiteCommand(ADMIN, PROJECT, "s-2", correlation_id="corr-r"))
    await commands.send(RedeployReleaseCommand(ADMIN, PROJECT, "s-3", correlation_id="corr-d"))
    assert [c.kind for c in store.inserted] == [
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
        CommandKind.REDEPLOY,
    ]
    assert [(e.action, e.result, e.actor_kind, e.actor) for e in audit.events] == [
        (AuditAction.SUSPEND_HOSTING, AuditResult.ACCEPTED, AuditActorKind.OPERATOR, "admin-1"),
        (AuditAction.RESUME_HOSTING, AuditResult.ACCEPTED, AuditActorKind.OPERATOR, "admin-1"),
        (AuditAction.REDEPLOY, AuditResult.ACCEPTED, AuditActorKind.OPERATOR, "admin-1"),
    ]
    assert [e.command_id for e in audit.events] == [c.id for c in store.inserted]
    assert [e.correlation_id for e in audit.events] == ["corr-s", "corr-r", "corr-d"]


async def test_the_buses_audit_a_refused_admin_operation() -> None:
    audit = _Audit()
    commands, _ = _buses(_Store(_facts(_hosting(live=None))), _History(()), audit)
    with pytest.raises(RedeployNotAllowed):
        await commands.send(RedeployReleaseCommand(ADMIN, PROJECT, "s-4"))
    with pytest.raises(ActionDenied):
        await commands.send(SuspendWebsiteCommand(USER, PROJECT, "s-5"))
    assert [(e.action, e.result, e.failure_code, e.actor_kind) for e in audit.events] == [
        (
            AuditAction.REDEPLOY,
            AuditResult.REFUSED,
            "redeploy_not_allowed",
            AuditActorKind.OPERATOR,
        ),
        (AuditAction.SUSPEND_HOSTING, AuditResult.REFUSED, "action_denied", AuditActorKind.USER),
    ]


async def test_the_admin_read_returns_the_subject_and_the_client_read_never_does() -> None:
    history = _History((_listed(2),))
    _, queries = _buses(_Store(_facts()), history, _Audit())
    admin_page = await queries.send(AdminListCommandsQuery(ADMIN, PROJECT, 20, None))
    client_page = await queries.send(
        ListCommandsQuery(USER, project_id=PROJECT, page_size=20, after=None)
    )
    [admin_view] = admin_page.items
    [client_view] = client_page.items
    assert admin_view.requested_by_subject == "admin-1"
    assert type(client_view) is CommandView
    assert not hasattr(client_view, "requested_by_subject")
