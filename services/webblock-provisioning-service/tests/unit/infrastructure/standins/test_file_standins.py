"""The file-based, reloadable project-access and entitlement stand-ins (decision 0014 section 21
ruling 10): refusals can be shown end to end by rewriting a file while the processes run. A file
that cannot be read or is invalid fails closed (the answer is unknown, never "allowed"), and no
message names a value or the path."""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from provisioning.core.application import (
    Action,
    LimitReached,
    Principal,
    ProjectAccessDenied,
)
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.standins import (
    AccessFileError,
    EntitlementFileError,
    PrototypeFileEntitlement,
    PrototypeFileProjectAccess,
)
from provisioning.infrastructure.telemetry import new_logger

PROJECT_A = UUID("0190a1b2-0000-7000-8000-00000000000a")
PROJECT_B = UUID("0190a1b2-0000-7000-8000-00000000000b")
ALICE = Principal(subject="alice", scopes=("s",))
BOB = Principal(subject="bob", scopes=("s",))
OPERATOR = Principal(subject="operator", scopes=("s",))


def _write(path: Path, doc: Any) -> None:
    """Replace the file atomically, as a writer must (write, then rename)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc) if not isinstance(doc, str) else doc)
    os.replace(tmp, path)


def _access_doc(*grants: tuple[str, Any]) -> dict[str, Any]:
    return {"subjects": [{"subject": s, "projects": p} for s, p in grants]}


def _logger() -> Any:
    return new_logger(io.StringIO(), logging.INFO)


@pytest.fixture
def access_file(tmp_path: Path) -> Path:
    path = tmp_path / "access.json"
    _write(path, _access_doc(("alice", [str(PROJECT_A)]), ("operator", "*")))
    return path


@pytest.mark.asyncio
async def test_a_subject_may_act_only_on_its_projects(access_file: Path) -> None:
    access = PrototypeFileProjectAccess.from_file(access_file, _logger())
    await access.check_access(ALICE, PROJECT_A, Action.DEPLOY_RELEASE)
    await access.check_access(OPERATOR, PROJECT_B, Action.LIST_COMMANDS)
    for principal, project in ((ALICE, PROJECT_B), (BOB, PROJECT_A)):
        with pytest.raises(ProjectAccessDenied):
            await access.check_access(principal, project, Action.GET_RELEASE)


@pytest.mark.asyncio
async def test_the_access_file_is_read_again_when_it_changes(access_file: Path) -> None:
    # Planted defect "the access file never reloaded": the second answer would still allow.
    access = PrototypeFileProjectAccess.from_file(access_file, _logger())
    await access.check_access(ALICE, PROJECT_A, Action.DEPLOY_RELEASE)
    _write(access_file, _access_doc(("alice", [str(PROJECT_B)])))
    with pytest.raises(ProjectAccessDenied):
        await access.check_access(ALICE, PROJECT_A, Action.DEPLOY_RELEASE)
    await access.check_access(ALICE, PROJECT_B, Action.DEPLOY_RELEASE)
    with pytest.raises(ProjectAccessDenied):
        await access.check_access(OPERATOR, PROJECT_B, Action.DEPLOY_RELEASE)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["invalid", "missing"])
async def test_an_unusable_access_file_fails_closed(access_file: Path, change: str) -> None:
    access = PrototypeFileProjectAccess.from_file(access_file, _logger())
    if change == "invalid":
        _write(access_file, '{"subjects": "alice-secret-MARKER"}')
    else:
        os.unlink(access_file)
    with pytest.raises(AccessFileError) as err:
        await access.check_access(ALICE, PROJECT_A, Action.GET_RELEASE)
    assert not isinstance(err.value, ProjectAccessDenied)
    assert "MARKER" not in str(err.value) and str(access_file) not in str(err.value)
    # Repaired, it answers again.
    _write(access_file, _access_doc(("alice", [str(PROJECT_A)])))
    await access.check_access(ALICE, PROJECT_A, Action.GET_RELEASE)


@pytest.mark.parametrize(
    "doc",
    [
        "[]",
        '{"subjects": []} x',
        {"subjects": [], "extra": 1},
        {"subjects": [{"subject": "a", "projects": ["not-a-uuid"]}]},
        {"subjects": [{"subject": "a", "projects": "all"}]},
        {"subjects": [{"subject": "", "projects": "*"}]},
        {"subjects": [{"subject": "a", "projects": "*"}, {"subject": "a", "projects": []}]},
        {"subjects": [{"subject": "a", "projects": [str(UUID(int=0))]}]},
        {"subjects": [{"subject": "a"}]},
        {"subjects": [{"subject": "a", "projects": [], "scopes": []}]},
    ],
)
def test_an_invalid_access_file_is_refused_at_start(tmp_path: Path, doc: Any) -> None:
    path = tmp_path / "access.json"
    _write(path, doc)
    with pytest.raises(AccessFileError) as err:
        PrototypeFileProjectAccess.from_file(path, _logger())
    assert str(tmp_path) not in str(err.value)


def test_a_missing_access_file_is_refused_at_start(tmp_path: Path) -> None:
    with pytest.raises(AccessFileError):
        PrototypeFileProjectAccess.from_file(tmp_path / "absent.json", _logger())


# --- entitlement ----------------------------------------------------------------------------


@pytest.fixture
def entitlement_file(tmp_path: Path) -> Path:
    path = tmp_path / "entitlement.json"
    _write(path, {"default_entitled": True, "projects": {str(PROJECT_B): False}})
    return path


@pytest.mark.asyncio
async def test_a_project_marked_not_entitled_reaches_its_limit(entitlement_file: Path) -> None:
    entitlement = PrototypeFileEntitlement.from_file(entitlement_file, _logger())
    await entitlement.check(PROJECT_A, CommandKind.DEPLOY)
    with pytest.raises(LimitReached):
        await entitlement.check(PROJECT_B, CommandKind.PROMOTE)


@pytest.mark.asyncio
async def test_entitlement_withdrawn_and_restored_by_rewriting_the_file(
    entitlement_file: Path,
) -> None:
    entitlement = PrototypeFileEntitlement.from_file(entitlement_file, _logger())
    await entitlement.check(PROJECT_A, CommandKind.PROMOTE)
    _write(entitlement_file, {"default_entitled": True, "projects": {str(PROJECT_A): False}})
    with pytest.raises(LimitReached):
        await entitlement.check(PROJECT_A, CommandKind.PROMOTE)
    _write(entitlement_file, {"default_entitled": False, "projects": {str(PROJECT_A): True}})
    await entitlement.check(PROJECT_A, CommandKind.PROMOTE)
    with pytest.raises(LimitReached):
        await entitlement.check(PROJECT_B, CommandKind.DEPLOY)


@pytest.mark.asyncio
async def test_an_unusable_entitlement_file_is_unknown_not_a_limit(entitlement_file: Path) -> None:
    entitlement = PrototypeFileEntitlement.from_file(entitlement_file, _logger())
    _write(entitlement_file, {"default_entitled": "yes", "projects": {}})
    with pytest.raises(EntitlementFileError) as err:
        await entitlement.check(PROJECT_A, CommandKind.DEPLOY)
    assert not isinstance(err.value, LimitReached)


@pytest.mark.parametrize(
    "doc",
    [
        {"projects": {}},
        {"default_entitled": True, "projects": []},
        {"default_entitled": True, "projects": {"x": True}},
        {"default_entitled": True, "projects": {str(PROJECT_A): 1}},
        {"default_entitled": 1, "projects": {}},
    ],
)
def test_an_invalid_entitlement_file_is_refused_at_start(tmp_path: Path, doc: Any) -> None:
    path = tmp_path / "entitlement.json"
    _write(path, doc)
    with pytest.raises(EntitlementFileError):
        PrototypeFileEntitlement.from_file(path, _logger())
