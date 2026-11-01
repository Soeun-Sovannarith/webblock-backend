"""Test doubles shared by the presentation tests: handlers of the fourteen operations that record
the command or query they were sent and answer with fixed views or a configured error. Not a test
module (no ``test_`` prefix), imported as ``tests.unit.presentation.fakes``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict
from uuid import UUID

from provisioning.core.application import (
    OPERATION_COMMANDS,
    OPERATION_QUERIES,
    Action,
    AdminCommandPage,
    AdminCommandView,
    AuditActorKind,
    CommandAccepted,
    CommandBus,
    CommandPage,
    CommandSummaryView,
    CommandView,
    DeploymentView,
    DiagnosticsView,
    Principal,
    QueryBus,
    ReleaseCursor,
    ReleasePage,
    ReleaseStepView,
    ReleaseSummaryView,
    ReleaseView,
    SourceEventStatus,
    SourceEventView,
)
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    HostingStatus,
    ReleaseStatus,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
)

PROJECT = UUID("0190a1b2-0000-7000-8000-00000000000a")
RELEASE = UUID("0190a1b2-0000-7000-8000-00000000000b")
COMMAND = UUID("0190a1b2-0000-7000-8000-00000000000c")
OTHER = UUID("0190a1b2-0000-7000-8000-00000000000d")
STEP = UUID("0190a1b2-0000-7000-8000-00000000000e")
GENERATION_REQUEST = UUID("0190a1b2-0000-7000-8000-00000000000f")
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
T0 = datetime(2026, 10, 8, 12, 0, 0, 123456, tzinfo=UTC)
T1 = datetime(2026, 10, 8, 12, 0, 5, tzinfo=UTC)

PRINCIPAL = Principal(subject="alice", scopes=("all",))
REQUESTER = "admin-staff"
"""The requester of the fake admin page's first command: never the caller's subject or key, so a
test that looks for those in a body is not fooled by the admin read's requestedBySubject."""


def release_view(
    project_id: UUID = PROJECT, release_id: UUID = RELEASE, *, full: bool
) -> ReleaseView:
    """A release with every optional field set (``full``) or none."""
    step = ReleaseStepView(
        step_name=ReleaseStepName.BUILD,
        attempt=1,
        status=ReleaseStepStatus.FAILED if full else ReleaseStepStatus.PENDING,
        started_at=T0 if full else None,
        finished_at=T1 if full else None,
        deadline_at=T1 if full else None,
        failure_code="build_failed" if full else None,
    )
    return ReleaseView(
        release_id=release_id,
        project_id=project_id,
        generation_request_id=GENERATION_REQUEST,
        repository_id=4242,
        git_commit_sha=SHA,
        container_image_digest=DIGEST if full else None,
        status=ReleaseStatus.ROLLED_BACK if full else ReleaseStatus.ACCEPTED,
        failure_code="promotion_failed" if full else None,
        failure_summary="the candidate did not answer" if full else None,
        steps=(step,),
        created_at=T0,
        updated_at=T1,
        promoted_at=T1 if full else None,
    )


def command_view(*, full: bool) -> CommandView:
    return CommandView(
        command_id=COMMAND,
        project_id=PROJECT,
        release_id=RELEASE if full else None,
        kind=CommandKind.DEPLOY if full else CommandKind.SUSPEND_HOSTING,
        command_number=7,
        status=CommandStatus.FAILED if full else CommandStatus.ACCEPTED,
        failure_code="candidate_not_ready" if full else None,
        created_at=T0,
        updated_at=T1,
    )


def deployment_view(*, full: bool) -> DeploymentView:
    return DeploymentView(
        project_id=PROJECT,
        live_release_id=RELEASE if full else None,
        last_command_number=7 if full else 0,
        routing_status=RoutingStatus.UNCERTAIN if full else RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE if full else HostingStatus.OFFLINE,
        in_flight_command=(
            CommandSummaryView(
                command_id=COMMAND, kind=CommandKind.PROMOTE, status=CommandStatus.RUNNING
            )
            if full
            else None
        ),
        updated_at=T1,
    )


def diagnostics_view(*, full: bool) -> DiagnosticsView:
    """Diagnostics with every optional field and list entry (``full``) or none."""
    release = release_view(full=full)
    return DiagnosticsView(
        release_id=RELEASE,
        project_id=PROJECT,
        release_status=release.status,
        release_failure_code=release.failure_code,
        release_created_at=T0,
        release_updated_at=T1,
        release_promoted_at=release.promoted_at,
        steps=release.steps if full else (),
        steps_truncated=full,
        commands=(command_view(full=True), command_view(full=False)) if full else (),
        commands_truncated=full,
        source_event=(
            SourceEventView(
                status=SourceEventStatus.FAILED,
                outcome_code="repository_mismatch",
                attempts=3,
                mismatched_repeat_count=1,
                received_at=T0,
                finished_at=T1,
            )
            if full
            else None
        ),
    )


def release_page(project_id: UUID = PROJECT, *, full: bool) -> ReleasePage:
    """Two summaries and a next cursor (``full``), or an empty last page."""
    if not full:
        return ReleasePage(items=(), next_cursor=None)
    view = release_view(project_id, full=True)
    item = ReleaseSummaryView(
        release_id=view.release_id,
        project_id=project_id,
        git_commit_sha=view.git_commit_sha,
        container_image_digest=view.container_image_digest,
        status=view.status,
        failure_code=view.failure_code,
        created_at=view.created_at,
        updated_at=view.updated_at,
        promoted_at=view.promoted_at,
    )
    bare = replace(
        item,
        release_id=OTHER,
        container_image_digest=None,
        failure_code=None,
        promoted_at=None,
    )
    return ReleasePage(
        items=(item, bare), next_cursor=ReleaseCursor(created_at=T0, release_id=OTHER)
    )


def command_page(project_id: UUID = PROJECT, *, full: bool) -> CommandPage:
    """Two commands and a next position (``full``), or an empty last page."""
    if not full:
        return CommandPage(items=(), next_after=None)
    items = (
        replace(command_view(full=True), project_id=project_id),
        replace(command_view(full=False), project_id=project_id, command_number=6),
    )
    return CommandPage(items=items, next_after=6)


def admin_command_page(project_id: UUID = PROJECT, *, full: bool) -> AdminCommandPage:
    """The admin read's page: two commands, the first with a requester and the second from an
    event (no requester), and a next position (``full``); or an empty last page."""
    if not full:
        return AdminCommandPage(items=(), next_after=None)
    first, second = command_page(project_id, full=True).items
    return AdminCommandPage(
        items=(_admin_view(first, REQUESTER), _admin_view(second, None)),
        next_after=6,
    )


def _admin_view(view: CommandView, requester: str | None) -> AdminCommandView:
    return AdminCommandView(
        command_id=view.command_id,
        project_id=view.project_id,
        release_id=view.release_id,
        kind=view.kind,
        command_number=view.command_number,
        status=view.status,
        failure_code=view.failure_code,
        created_at=view.created_at,
        updated_at=view.updated_at,
        requested_by_subject=requester,
    )


_ACTOR_KINDS = frozenset(
    {
        Action.DEPLOY_RELEASE,
        Action.PROMOTE_RELEASE,
        Action.ROLLBACK_RELEASE,
        Action.BUILD_RELEASE,
        Action.SUSPEND_WEBSITE,
        Action.RESUME_WEBSITE,
        Action.REDEPLOY_RELEASE,
    }
)


@dataclass
class FakeOperations:
    """Answers the fourteen operations' commands and queries (decision 0025 CQ11) on its own
    ``commands`` and ``queries`` buses. ``errors`` makes an operation raise; ``delay`` makes every
    call wait first; ``results`` replaces a result (it is given the command or query); every call
    is recorded as (action, principal, command or query), and a mutation's actor kind in
    ``actor_kinds`` (its recorded command carries the default actor kind)."""

    errors: dict[Action, BaseException] = field(default_factory=dict)
    delay: float = 0.0
    full: bool = True
    replayed: bool = False
    calls: list[tuple[Action, Principal, object]] = field(default_factory=list)
    actor_kinds: list[AuditActorKind] = field(default_factory=list)
    results: dict[Action, Callable[[object], object]] = field(default_factory=dict)
    # When set, every call records itself, sets ``entered`` and waits for ``gate``.
    gate: asyncio.Event | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    commands: CommandBus = field(init=False, repr=False, compare=False)
    queries: QueryBus = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.commands, self.queries = CommandBus(), QueryBus()
        for action, kind in OPERATION_COMMANDS.items():
            self.commands.register(kind, _FakeHandler(self, action))
        for action, kind in OPERATION_QUERIES.items():
            self.queries.register(kind, _FakeHandler(self, action))

    async def handle(self, action: Action, intent: Any) -> object:
        """Record ``intent``, wait as configured, raise the configured error or answer."""
        recorded = intent
        if action in _ACTOR_KINDS:
            # The actor kind is recorded apart, so REST and MCP calls compare equal.
            self.actor_kinds.append(intent.actor_kind)
            recorded = replace(intent, actor_kind=AuditActorKind.USER)
        self.calls.append((action, intent.principal, recorded))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        error = self.errors.get(action)
        if error is not None:
            raise error
        make = self.results.get(action)
        if make is not None:
            return make(intent)
        return self._default(action, intent)

    def _default(self, action: Action, intent: Any) -> object:
        full = self.full
        match action:
            case Action.GET_RELEASE | Action.BUILD_RELEASE:
                return release_view(intent.project_id, intent.release_id, full=full)
            case Action.GET_COMMAND:
                return replace(
                    command_view(full=full),
                    project_id=intent.project_id,
                    command_id=intent.command_id,
                )
            case Action.GET_DEPLOYMENT:
                return replace(deployment_view(full=full), project_id=intent.project_id)
            case Action.GET_RELEASE_DIAGNOSTICS:
                return replace(
                    diagnostics_view(full=full),
                    release_id=intent.release_id,
                    project_id=intent.project_id,
                )
            case Action.LIST_RELEASES:
                return release_page(intent.project_id, full=full)
            case Action.LIST_COMMANDS:
                return command_page(intent.project_id, full=full)
            case Action.ADMIN_LIST_COMMANDS:
                return admin_command_page(intent.project_id, full=full)
        return CommandAccepted(
            command_id=COMMAND,
            project_id=intent.project_id,
            status=CommandStatus.ACCEPTED,
            replayed=self.replayed,
        )

    def actions(self) -> list[Action]:
        return [action for action, _, _ in self.calls]


class _FakeHandler:
    """Hands one command or query to the fake under its action."""

    __slots__ = ("_action", "_fake")

    def __init__(self, fake: FakeOperations, action: Action) -> None:
        self._fake = fake
        self._action = action

    async def handle(self, intent: Any, /) -> Any:
        return await self._fake.handle(self._action, intent)


class Operations(Protocol):
    """What the transport tests give an ``Invoker``: the fourteen operations on two buses
    (``FakeOperations``, or the real handlers of ``tests.unit.presentation.memory``)."""

    @property
    def commands(self) -> CommandBus: ...

    @property
    def queries(self) -> QueryBus: ...


class Buses(TypedDict):
    commands: CommandBus
    queries: QueryBus


def buses(operations: Operations) -> Buses:
    """The keyword arguments ``Invoker`` takes for ``operations``' buses."""
    return Buses(commands=operations.commands, queries=operations.queries)
