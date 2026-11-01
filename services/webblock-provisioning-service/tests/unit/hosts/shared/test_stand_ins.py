"""The stand-in builders shared by the hosts (wave 6 tasks W6-S and W6-D; task H2):
prototype-allow builds the allow-all classes; prototype-file builds the reloadable file stand-ins,
and a file that cannot be used at start-up is a configuration error; no other mode ever means
"allow". In the production profile every builder refuses, naming its key, whatever the mode."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application import Action, LimitReached, Principal, ProjectAccessDenied
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import CommandKind
from provisioning.hosts.shared.runtime import InvalidSettings
from provisioning.hosts.shared.stand_ins import (
    StandinNotAvailable,
    build_entitlement,
    build_payment_event_record,
    build_project_access,
    read_dev_tokens,
    read_repositories,
)
from provisioning.infrastructure.standins import (
    PrototypeAllowEntitlement,
    PrototypeAllowProjectAccess,
    PrototypeFileEntitlement,
    PrototypeFileProjectAccess,
)
from provisioning.infrastructure.telemetry import new_logger

PROTO = Profile.PROTOTYPE
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
SECRET_PATH = Path("/project/secret-path-MARKER-5d1/access.json")


def _logger() -> object:
    return new_logger(io.StringIO(), logging.INFO)


@pytest.mark.asyncio
async def test_prototype_allow_builds_the_allow_all_classes() -> None:
    entitlement = build_entitlement(PROTO, "prototype-allow", None, _logger())
    access = build_project_access(PROTO, "prototype-allow", None, _logger())
    assert isinstance(entitlement, PrototypeAllowEntitlement)
    assert isinstance(access, PrototypeAllowProjectAccess)
    await entitlement.check(PROJECT, CommandKind.DEPLOY)
    await access.check_access(Principal(subject="s", scopes=()), PROJECT, Action.DEPLOY_RELEASE)


@pytest.mark.asyncio
async def test_prototype_file_builds_the_file_stand_ins(tmp_path: Path) -> None:
    access_path = tmp_path / "access.json"
    access_path.write_text(json.dumps({"subjects": [{"subject": "s", "projects": [str(PROJECT)]}]}))
    entitlement_path = tmp_path / "entitlement.json"
    entitlement_path.write_text(
        json.dumps({"default_entitled": True, "projects": {str(PROJECT): False}})
    )
    access = build_project_access(PROTO, "prototype-file", access_path, _logger())
    entitlement = build_entitlement(PROTO, "prototype-file", entitlement_path, _logger())
    assert isinstance(access, PrototypeFileProjectAccess)
    assert isinstance(entitlement, PrototypeFileEntitlement)
    await access.check_access(Principal(subject="s", scopes=()), PROJECT, Action.GET_RELEASE)
    with pytest.raises(ProjectAccessDenied):
        await access.check_access(Principal(subject="t", scopes=()), PROJECT, Action.GET_RELEASE)
    with pytest.raises(LimitReached):
        await entitlement.check(PROJECT, CommandKind.DEPLOY)


@pytest.mark.parametrize("content", [None, "{}", "not json"])
def test_an_unusable_file_is_a_configuration_error_without_the_path(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "secret-path-MARKER-5d1.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(StandinNotAvailable) as entitlement_err:
        build_entitlement(PROTO, "prototype-file", path, _logger())
    with pytest.raises(StandinNotAvailable) as access_err:
        build_project_access(PROTO, "prototype-file", path, _logger())
    assert "ENTITLEMENT_FILE" in str(entitlement_err.value)
    assert "PROJECT_ACCESS_FILE" in str(access_err.value)
    for err in (entitlement_err.value, access_err.value):
        assert "MARKER" not in str(err)
        assert err.__cause__ is None


@pytest.mark.parametrize("mode", ["", "allow-everything"])
def test_any_other_mode_is_a_configuration_error_without_the_path(mode: str) -> None:
    with pytest.raises(StandinNotAvailable) as entitlement_err:
        build_entitlement(PROTO, mode, SECRET_PATH, _logger())
    with pytest.raises(StandinNotAvailable) as access_err:
        build_project_access(PROTO, mode, SECRET_PATH, _logger())
    assert "ENTITLEMENT_MODE" in str(entitlement_err.value)
    assert "PROJECT_ACCESS_MODE" in str(access_err.value)
    for err in (entitlement_err.value, access_err.value):
        assert isinstance(err, InvalidSettings)  # the hosts exit with the configuration code
        assert "MARKER" not in str(err)


class _NoDeletions:
    async def project_deleted(self, project_id: UUID) -> bool:
        return False


@pytest.mark.parametrize("mode", ["prototype-allow", "prototype-file"])
def test_production_refuses_every_stand_in_naming_its_key(tmp_path: Path, mode: str) -> None:
    """Task H2: a production process never constructs a stand-in, even with a valid mode and a
    usable file (the configuration refuses those keys first; this is the second line)."""
    access_path = tmp_path / "access.json"
    access_path.write_text(json.dumps({"subjects": []}))
    prod = Profile.PRODUCTION
    builders: dict[str, Callable[[], object]] = {
        "ENTITLEMENT_MODE": lambda: build_entitlement(prod, mode, access_path, _logger()),
        "PROJECT_ACCESS_MODE": lambda: build_project_access(prod, mode, access_path, _logger()),
        "REPOSITORY_MODE": lambda: read_repositories(prod, "prototype-file", access_path),
        "HOSTING_ENTITLEMENT_MODE": lambda: build_payment_event_record(
            prod, "prototype-payment-events", max_projects=10, deletions=_NoDeletions()
        ),
        "IDENTITY_MODE": lambda: read_dev_tokens(prod, access_path),
    }
    for key, build in builders.items():
        with pytest.raises(StandinNotAvailable) as err:
            build()
        assert str(err.value) == f"{key} selects a prototype stand-in, refused in production"
        assert isinstance(err.value, InvalidSettings)


@pytest.mark.asyncio
async def test_the_prototype_builds_the_payment_event_record() -> None:
    record = build_payment_event_record(
        PROTO, "prototype-payment-events", max_projects=10, deletions=_NoDeletions()
    )
    record.record(PROJECT, event_key="pay-1", entitled=False)
    record.record(PROJECT, event_key="pay-2", entitled=True)
    assert await record.entitlement_after(PROJECT, "pay-1") is True
    with pytest.raises(StandinNotAvailable, match="HOSTING_ENTITLEMENT_MODE"):
        build_payment_event_record(PROTO, "other", max_projects=10, deletions=_NoDeletions())


def test_the_repository_file_needs_its_mode_and_path(tmp_path: Path) -> None:
    with pytest.raises(StandinNotAvailable, match="REPOSITORY_MODE"):
        read_repositories(PROTO, "", tmp_path / "x.json")
    with pytest.raises(StandinNotAvailable, match="REPOSITORY_MODE"):
        read_repositories(PROTO, "prototype-file", None)
    with pytest.raises(StandinNotAvailable, match="AUTH_DEV_TOKENS_FILE"):
        read_dev_tokens(PROTO, None)
