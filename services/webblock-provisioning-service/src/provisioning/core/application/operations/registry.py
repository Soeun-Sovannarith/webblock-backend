"""The fourteen REST and MCP operations on the buses (decision 0025 CQ8, CQ10, CQ11; TR-54).

``register_operations`` builds each handler from its ports, by hand, and registers it on the
command or query bus; the API host calls it once at start, then checks with ``OPERATION_INTENTS``
that every ``Action`` of the operation table has exactly one handler. Nothing is discovered and
nothing is built later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from provisioning.core.application.jobs.ports import AcceptanceStore, RedeployStore
from provisioning.core.application.operations.acceptance import CommandAcceptor
from provisioning.core.application.operations.admin import AdminAcceptor, AdminDeps
from provisioning.core.application.operations.auditing import Auditor, ServiceAudit
from provisioning.core.application.operations.commands.build_release import (
    BuildReleaseCommand,
    BuildReleaseHandler,
)
from provisioning.core.application.operations.commands.deploy_release import (
    DeployReleaseCommand,
    DeployReleaseHandler,
)
from provisioning.core.application.operations.commands.promote_release import (
    PromoteReleaseCommand,
    PromoteReleaseHandler,
)
from provisioning.core.application.operations.commands.redeploy_release import (
    RedeployReleaseCommand,
    RedeployReleaseHandler,
)
from provisioning.core.application.operations.commands.resume_website import (
    ResumeWebsiteCommand,
    ResumeWebsiteHandler,
)
from provisioning.core.application.operations.commands.rollback_release import (
    RollbackReleaseCommand,
    RollbackReleaseHandler,
)
from provisioning.core.application.operations.commands.suspend_website import (
    SuspendWebsiteCommand,
    SuspendWebsiteHandler,
)
from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import BuildStarter, HistoryStore, ReadStore
from provisioning.core.application.operations.queries.admin_list_commands import (
    AdminListCommandsHandler,
    AdminListCommandsQuery,
)
from provisioning.core.application.operations.queries.get_command import (
    GetCommandHandler,
    GetCommandQuery,
)
from provisioning.core.application.operations.queries.get_deployment import (
    GetDeploymentHandler,
    GetDeploymentQuery,
)
from provisioning.core.application.operations.queries.get_release import (
    GetReleaseHandler,
    GetReleaseQuery,
)
from provisioning.core.application.operations.queries.get_release_diagnostics import (
    GetReleaseDiagnosticsHandler,
    GetReleaseDiagnosticsQuery,
)
from provisioning.core.application.operations.queries.list_commands import (
    ListCommandsHandler,
    ListCommandsQuery,
)
from provisioning.core.application.operations.queries.list_releases import (
    ListReleasesHandler,
    ListReleasesQuery,
)
from provisioning.core.application.ports.dependencies import (
    ActionPolicy,
    Clock,
    Entitlement,
    IdSource,
    ProjectAccess,
)
from provisioning.core.application.shared.action import Action, EnabledOperations
from provisioning.core.application.shared.cqrs import CommandBus, QueryBus

OPERATION_COMMANDS: Final[dict[Action, type]] = {
    Action.DEPLOY_RELEASE: DeployReleaseCommand,
    Action.PROMOTE_RELEASE: PromoteReleaseCommand,
    Action.ROLLBACK_RELEASE: RollbackReleaseCommand,
    Action.SUSPEND_WEBSITE: SuspendWebsiteCommand,
    Action.RESUME_WEBSITE: ResumeWebsiteCommand,
    Action.REDEPLOY_RELEASE: RedeployReleaseCommand,
    Action.BUILD_RELEASE: BuildReleaseCommand,
}
"""The command of each write operation: 7 of the 14."""

OPERATION_QUERIES: Final[dict[Action, type]] = {
    Action.GET_RELEASE: GetReleaseQuery,
    Action.GET_COMMAND: GetCommandQuery,
    Action.GET_DEPLOYMENT: GetDeploymentQuery,
    Action.GET_RELEASE_DIAGNOSTICS: GetReleaseDiagnosticsQuery,
    Action.LIST_RELEASES: ListReleasesQuery,
    Action.LIST_COMMANDS: ListCommandsQuery,
    Action.ADMIN_LIST_COMMANDS: AdminListCommandsQuery,
}
"""The query of each read operation: 7 of the 14."""


@dataclass(frozen=True, slots=True)
class OperationDeps:
    """The ports the fourteen handlers are built from. ``command_deadline`` is COMMAND_DEADLINE
    (decision 0005 section 7): required and positive. ``history`` serves the listings and
    diagnostics; None makes those three operations and the admin read ``OperationNotAvailable``.
    ``redeploys`` accepts the admin's redeploy (the acceptance store on PostgreSQL); None makes
    redeploy_release ``OperationNotAvailable``. ``audit`` None records no audit event (tests that
    do not observe audit). The API host gives all three."""

    policy: ActionPolicy
    access: ProjectAccess
    entitlement: Entitlement
    acceptance: AcceptanceStore
    reads: ReadStore
    clock: Clock
    ids: IdSource
    builds: BuildStarter
    enabled: EnabledOperations
    command_deadline: timedelta
    history: HistoryStore | None = None
    audit: ServiceAudit | None = None
    redeploys: RedeployStore | None = None


def register_operations(commands: CommandBus, queries: QueryBus, deps: OperationDeps) -> None:
    """Build the fourteen handlers and register each on its bus. A command_deadline that is not
    positive is ``ValueError``; a handler already registered is ``ValueError``. Calls no port."""
    if deps.command_deadline <= timedelta(0):
        raise ValueError("application service: command_deadline must be positive")
    gate = OperationGate(policy=deps.policy, access=deps.access, enabled=deps.enabled)
    auditor = Auditor(deps.audit)
    acceptor = CommandAcceptor(
        gate=gate,
        acceptance=deps.acceptance,
        entitlement=deps.entitlement,
        clock=deps.clock,
        ids=deps.ids,
        command_deadline=deps.command_deadline,
        auditor=auditor,
    )
    admin = AdminAcceptor(
        AdminDeps(
            gate=gate,
            acceptance=deps.acceptance,
            clock=deps.clock,
            ids=deps.ids,
            command_deadline=deps.command_deadline,
            redeploys=deps.redeploys,
        )
    )
    commands.register(DeployReleaseCommand, DeployReleaseHandler(acceptor))
    commands.register(PromoteReleaseCommand, PromoteReleaseHandler(acceptor))
    commands.register(RollbackReleaseCommand, RollbackReleaseHandler(acceptor))
    commands.register(SuspendWebsiteCommand, SuspendWebsiteHandler(admin=admin, auditor=auditor))
    commands.register(ResumeWebsiteCommand, ResumeWebsiteHandler(admin=admin, auditor=auditor))
    commands.register(RedeployReleaseCommand, RedeployReleaseHandler(admin=admin, auditor=auditor))
    commands.register(
        BuildReleaseCommand,
        BuildReleaseHandler(gate=gate, builds=deps.builds, reads=deps.reads, auditor=auditor),
    )
    queries.register(GetReleaseQuery, GetReleaseHandler(gate=gate, reads=deps.reads))
    queries.register(GetCommandQuery, GetCommandHandler(gate=gate, reads=deps.reads))
    queries.register(GetDeploymentQuery, GetDeploymentHandler(gate=gate, reads=deps.reads))
    queries.register(
        GetReleaseDiagnosticsQuery,
        GetReleaseDiagnosticsHandler(gate=gate, history=deps.history),
    )
    queries.register(ListReleasesQuery, ListReleasesHandler(gate=gate, history=deps.history))
    queries.register(ListCommandsQuery, ListCommandsHandler(gate=gate, history=deps.history))
    queries.register(
        AdminListCommandsQuery, AdminListCommandsHandler(gate=gate, history=deps.history)
    )
