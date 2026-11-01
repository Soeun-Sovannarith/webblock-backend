"""What each profile builds where a contract is open (task H2): the operations and command kinds,
and the closed adapters of production, each failing with a typed error that names its contract."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application import (
    Action,
    DependencyNotConfigured,
    Principal,
    ProjectAccessDenied,
)
from provisioning.core.application.shared.profile import (
    ContractNotDecided,
    OpenContract,
    Profile,
    ProjectAccessClosed,
)
from provisioning.core.domain import CommandKind
from provisioning.hosts.shared.open_contracts import (
    PRODUCTION_REFUSED,
    PROTOTYPE_KINDS,
    ClosedEntitlement,
    ClosedProjectAccess,
    api_operations,
    entitlement,
    project_access,
    worker_kinds,
)
from provisioning.infrastructure.telemetry import new_logger

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
PRINCIPAL = Principal(subject="alice", scopes=("x",))


def _logger() -> object:
    return new_logger(io.StringIO(), logging.INFO)


def test_the_prototype_enables_every_operation_and_every_kind_but_sync_domains() -> None:
    assert api_operations(Profile.PROTOTYPE).actions() == tuple(Action)
    assert set(worker_kinds(Profile.PROTOTYPE).kinds()) == set(CommandKind) - {
        CommandKind.SYNC_DOMAINS,
    }


def test_redeploy_runs_in_the_prototype_only() -> None:
    """Decision 0023 G12, G13: the redeploy executor exists, so the prototype runs the kind (it
    waited under decision 0021 D10); production still runs no kind."""
    assert CommandKind.REDEPLOY in PROTOTYPE_KINDS
    assert CommandKind.REDEPLOY in worker_kinds(Profile.PROTOTYPE).kinds()
    assert CommandKind.REDEPLOY not in worker_kinds(Profile.PRODUCTION).kinds()


def test_production_enables_the_reads_and_refuses_each_mutation_naming_its_contracts() -> None:
    enabled = api_operations(Profile.PRODUCTION)
    assert enabled.actions() == tuple(a for a in Action if not a.is_mutation())
    assert set(PRODUCTION_REFUSED) == {a for a in Action if a.is_mutation()}
    for action, contracts in PRODUCTION_REFUSED.items():
        refusal = enabled.refusal(action)
        assert isinstance(refusal, OpenContract)
        assert refusal.contracts == contracts
        assert "OD-01" in contracts
    assert PRODUCTION_REFUSED[Action.DEPLOY_RELEASE] == ("OD-01", "OD-04")
    assert PRODUCTION_REFUSED[Action.ROLLBACK_RELEASE] == ("OD-01",)


def test_production_refuses_the_admin_mutations_and_allows_the_admin_read() -> None:
    """Decision 0023 G13: every mutation stays refused, the admin ones included; the admin read
    is allowed like the other reads."""
    enabled = api_operations(Profile.PRODUCTION)
    for action in (Action.SUSPEND_WEBSITE, Action.RESUME_WEBSITE, Action.REDEPLOY_RELEASE):
        assert not enabled.enabled(action)
        assert isinstance(enabled.refusal(action), OpenContract)
        assert PRODUCTION_REFUSED[action] == ("OD-01", "OD-04")
    assert enabled.enabled(Action.ADMIN_LIST_COMMANDS)


def test_production_enables_no_command_kind() -> None:
    assert worker_kinds(Profile.PRODUCTION).kinds() == ()


@pytest.mark.asyncio
async def test_closed_project_access_denies_every_request() -> None:
    for action in Action:
        with pytest.raises(ProjectAccessClosed) as caught:
            await ClosedProjectAccess().check_access(PRINCIPAL, PROJECT, action)
        assert isinstance(caught.value, ProjectAccessDenied)
        assert caught.value.contract == "OD-01"


@pytest.mark.asyncio
async def test_closed_entitlement_is_never_entitled() -> None:
    for kind in CommandKind:
        with pytest.raises(ContractNotDecided) as caught:
            await ClosedEntitlement().check(PROJECT, kind)
        assert isinstance(caught.value, DependencyNotConfigured)
        assert caught.value.contract == "OD-04"


def test_production_builds_the_closed_adapters_whatever_the_mode(tmp_path: Path) -> None:
    path = tmp_path / "access.json"  # never read in production
    for mode in ("prototype-allow", "prototype-file", ""):
        access = project_access(Profile.PRODUCTION, mode, path, _logger())
        allowed = entitlement(Profile.PRODUCTION, mode, path, _logger())
        assert isinstance(access, ClosedProjectAccess)
        assert isinstance(allowed, ClosedEntitlement)


def test_the_prototype_builds_its_stand_ins() -> None:
    access = project_access(Profile.PROTOTYPE, "prototype-allow", None, _logger())
    allowed = entitlement(Profile.PROTOTYPE, "prototype-allow", None, _logger())
    assert type(access).__name__ == "PrototypeAllowProjectAccess"
    assert type(allowed).__name__ == "PrototypeAllowEntitlement"
