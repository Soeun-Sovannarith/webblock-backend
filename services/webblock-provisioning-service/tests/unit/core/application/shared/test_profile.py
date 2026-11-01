"""The production profile's typed refusals (task H2): ``OpenContract`` names the operation and the
open contracts, ``EnabledOperations`` refuses a switched-off mutation with it, and the real
handlers refuse a production mutation before any store, entitlement or build is asked, records
the refusal in the audit log, and still checks the permission of a read."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from tests.unit.presentation.fakes import PROJECT, RELEASE, T0
from tests.unit.presentation.memory import SCOPES, FakeBuilds, MemoryStores

from provisioning.core.application import (
    ADMIN_ROLE,
    Action,
    ActionDenied,
    AuditEvent,
    AuditResult,
    BuildReleaseCommand,
    CommandBus,
    DependencyNotConfigured,
    DeployReleaseCommand,
    EnabledOperations,
    GetDeploymentQuery,
    OperationDeps,
    OperationDisabled,
    Principal,
    ProjectAccessDenied,
    PromoteReleaseCommand,
    QueryBus,
    RollbackReleaseCommand,
    ServiceAudit,
    register_operations,
)
from provisioning.core.application.shared.profile import (
    ContractNotDecided,
    OpenContract,
    Profile,
    ProjectAccessClosed,
)
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.identity import ActionScopePolicy

MUTATION_CONTRACTS = {
    Action.DEPLOY_RELEASE: ("OD-01", "OD-04"),
    Action.PROMOTE_RELEASE: ("OD-01", "OD-04"),
    Action.ROLLBACK_RELEASE: ("OD-01",),
    Action.BUILD_RELEASE: ("OD-01",),
}
READS = tuple(a for a in Action if not a.is_mutation())
ALICE = Principal(subject="alice", scopes=tuple(SCOPES.values()))
# Only an ADMIN reaches the operation gate of a rollback (FR-50).
ADMIN = Principal(subject="admin", scopes=(*SCOPES.values(), ADMIN_ROLE))


def test_the_profile_has_exactly_two_values() -> None:
    assert [p.value for p in Profile] == ["prototype", "production"]


def test_open_contract_names_the_operation_and_its_contracts() -> None:
    err = OpenContract("deploy_release", ("OD-01", "OD-04"))
    assert isinstance(err, OperationDisabled)
    assert (err.action, err.contracts) == ("deploy_release", ("OD-01", "OD-04"))
    assert str(err) == (
        "deploy_release is refused in production: its contracts are open (OD-01, OD-04)"
    )
    with pytest.raises(ValueError, match="at least one contract"):
        OpenContract("deploy_release", ())


def test_the_closed_ports_raise_typed_errors_naming_the_contract() -> None:
    denied = ProjectAccessClosed()
    assert isinstance(denied, ProjectAccessDenied)
    assert denied.contract == "OD-01"
    closed = ContractNotDecided("entitlement", "OD-04")
    assert isinstance(closed, DependencyNotConfigured)
    assert (closed.port, closed.contract) == ("entitlement", "OD-04")
    assert "OD-04" in str(closed)


def test_a_refused_action_gets_open_contract_and_any_other_operation_disabled() -> None:
    enabled = EnabledOperations(READS, refused=MUTATION_CONTRACTS)
    deploy = enabled.refusal(Action.DEPLOY_RELEASE)
    assert isinstance(deploy, OpenContract)
    assert deploy.contracts == ("OD-01", "OD-04")
    plain = EnabledOperations().refusal(Action.GET_RELEASE)
    assert type(plain) is OperationDisabled


def test_an_action_cannot_be_both_enabled_and_refused_nor_refused_without_a_contract() -> None:
    with pytest.raises(ValueError, match="both enabled and refused"):
        EnabledOperations(Action, refused={Action.DEPLOY_RELEASE: ("OD-01",)})
    with pytest.raises(ValueError, match="without a contract"):
        EnabledOperations(READS, refused={Action.DEPLOY_RELEASE: ()})


class _Unasked:
    """Project access and entitlement that fail the test when asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        self.asked.append(f"access {action}")

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        self.asked.append(f"entitlement {kind}")


class _Clock:
    def now(self) -> datetime:
        return T0


class _Ids:
    def new_id(self) -> UUID:
        return UUID("0190a1b2-0000-7000-8000-00000000abcd")


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        self.events.append(event)


def _production_buses(
    stores: MemoryStores, gate: _Unasked, builds: FakeBuilds, audit: _Audit
) -> tuple[CommandBus, QueryBus]:
    commands, queries = CommandBus(), QueryBus()
    register_operations(
        commands,
        queries,
        OperationDeps(
            policy=ActionScopePolicy(SCOPES),
            access=gate,
            entitlement=gate,
            acceptance=stores,
            reads=stores,
            history=stores,
            clock=_Clock(),
            ids=_Ids(),
            builds=builds,
            enabled=EnabledOperations(READS, refused=MUTATION_CONTRACTS),
            command_deadline=timedelta(minutes=5),
            audit=ServiceAudit(log=audit, clock=_Clock()),
        ),
    )
    return commands, queries


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(MUTATION_CONTRACTS))
async def test_a_production_mutation_is_refused_before_anything_is_asked(action: Action) -> None:
    """FR-6.2: a production mutation is refused before anything is asked."""
    stores, gate, audit = MemoryStores(), _Unasked(), _Audit()
    builds = FakeBuilds(stores)
    commands, _ = _production_buses(stores, gate, builds, audit)
    with pytest.raises(OpenContract) as caught:
        if action is Action.BUILD_RELEASE:
            await commands.send(BuildReleaseCommand(ALICE, project_id=PROJECT, release_id=RELEASE))
        else:
            kind = {
                Action.DEPLOY_RELEASE: DeployReleaseCommand,
                Action.PROMOTE_RELEASE: PromoteReleaseCommand,
                Action.ROLLBACK_RELEASE: RollbackReleaseCommand,
            }[action]
            await commands.send(
                kind(
                    principal=ADMIN if action.is_admin_only() else ALICE,
                    project_id=PROJECT,
                    release_id=RELEASE,
                    idempotency_key="key-1",
                    correlation_id="corr-1",
                )
            )
    assert caught.value.contracts == MUTATION_CONTRACTS[action]
    assert gate.asked == []
    assert builds.starts == []
    assert stores.commands == []
    [event] = audit.events
    assert (event.result, event.failure_code) == (AuditResult.REFUSED, "open_contract")


@pytest.mark.asyncio
async def test_a_production_read_still_needs_its_permission() -> None:
    stores, gate, audit = MemoryStores(), _Unasked(), _Audit()
    _, queries = _production_buses(stores, gate, FakeBuilds(stores), audit)
    no_scope = Principal(subject="reader", scopes=("prov.other",))
    with pytest.raises(ActionDenied):
        await queries.send(GetDeploymentQuery(no_scope, project_id=PROJECT))
    assert gate.asked == []
