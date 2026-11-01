"""Prototype stand-ins for B1's external ports (decision 0014 sections 3 and 10.2): project access
and entitlement allow and log the decision; the repository registry and locator are read from a
configured JSON file. Each class name starts with Prototype."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application import (
    Action,
    DependencyUnavailable,
    Principal,
    RegisteredRepository,
)
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.standins import (
    PrototypeAllowEntitlement,
    PrototypeAllowProjectAccess,
    PrototypeRepositoryFile,
    RepositoryFileError,
)
from provisioning.infrastructure.telemetry import new_logger

PROJECT = UUID("0190a1b2-0000-7000-8000-0000000000a1")
OTHER = UUID("0190a1b2-0000-7000-8000-0000000000a2")


@pytest.mark.asyncio
async def test_project_access_allows_and_logs_the_decision_without_the_subject() -> None:
    stream = io.StringIO()
    access = PrototypeAllowProjectAccess(new_logger(stream, logging.INFO))
    await access.check_access(Principal(subject="alice-visible"), PROJECT, Action.DEPLOY_RELEASE)
    (record,) = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert record["component"] == "prototype-project-access"
    assert record["project_id"] == str(PROJECT)
    assert record["action"] == "deploy_release"
    assert "alice-visible" not in stream.getvalue()


@pytest.mark.asyncio
async def test_entitlement_allows_and_logs_the_decision() -> None:
    stream = io.StringIO()
    entitlement = PrototypeAllowEntitlement(new_logger(stream, logging.INFO))
    await entitlement.check(PROJECT, CommandKind.PROMOTE)
    (record,) = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert record["component"] == "prototype-entitlement"
    assert record["project_id"] == str(PROJECT)
    assert record["kind"] == "promote"


def _repositories(entries: list[dict[str, object]]) -> bytes:
    return json.dumps({"repositories": entries}).encode()


ENTRY = {
    "repository_id": 4242,
    "project_id": str(PROJECT),
    "subdomain": "site-a1",
    "git_url": "http://webblock-git/site-a1.git",
}
OTHER_ENTRY = {
    "repository_id": 4243,
    "project_id": str(OTHER),
    "subdomain": "site-b2",
    "git_url": "http://webblock-git/site-b2.git",
}


@pytest.mark.asyncio
async def test_the_file_registers_and_locates_a_repository() -> None:
    repos = PrototypeRepositoryFile.from_json(_repositories([ENTRY]))
    assert await repos.registered_repository(PROJECT) == RegisteredRepository(
        repository_id=4242, subdomain="site-a1"
    )
    assert await repos.registered_repository(OTHER) is None
    assert await repos.repository_url(PROJECT, 4242) == "http://webblock-git/site-a1.git"


@pytest.mark.asyncio
@pytest.mark.parametrize(("project", "repository"), [(OTHER, 4242), (PROJECT, 4243)])
async def test_an_unknown_or_foreign_repository_is_unavailable(
    project: UUID, repository: int
) -> None:
    repos = PrototypeRepositoryFile.from_json(_repositories([ENTRY]))
    with pytest.raises(DependencyUnavailable):
        await repos.repository_url(project, repository)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (b"[]", "is not a JSON object"),
        (b'{"repositories": [], "repositories": []}', "duplicate key"),
        (b'{"repositories": [], "x": 1}', "unknown key"),
        (_repositories([{**ENTRY, "repository_id": 0}]), "entry 0: repository_id"),
        (
            _repositories([{**ENTRY, "repository_id": "4242"}]),
            "entry 0: repository_id",
        ),
        (_repositories([{**ENTRY, "repository_id": True}]), "entry 0: repository_id"),
        (_repositories([{**ENTRY, "project_id": "not-a-uuid"}]), "entry 0: project_id"),
        (
            _repositories([{**ENTRY, "git_url": "http://user:pw-visible@git/x.git"}]),
            "entry 0: git_url",
        ),
        (_repositories([{**ENTRY, "git_url": "file:///srv/git/x.git"}]), "entry 0: git_url"),
        (_repositories([{**ENTRY, "extra": 1}]), "entry 0"),
        (_repositories([ENTRY, ENTRY]), "entry 1: repository_id is listed twice"),
        (
            _repositories([ENTRY, {**OTHER_ENTRY, "project_id": str(PROJECT)}]),
            "entry 1: project_id already has a repository",
        ),
        # Decision 0021 D3 and D8: the file format changed with the sub-domain; the old key name
        # is refused, so a stale file is noticed.
        (
            _repositories(
                [
                    {
                        "github_repository_id": 4242,
                        **{k: v for k, v in ENTRY.items() if k != "repository_id"},
                    }
                ]
            ),
            "entry 0",
        ),
        (
            _repositories([{k: v for k, v in ENTRY.items() if k != "subdomain"}]),
            "entry 0",
        ),
        (_repositories([{**ENTRY, "subdomain": "Site-A1"}]), "entry 0: subdomain"),
        (_repositories([{**ENTRY, "subdomain": "-pw-visible"}]), "entry 0: subdomain"),
        (_repositories([{**ENTRY, "subdomain": ""}]), "entry 0: subdomain"),
        (_repositories([{**ENTRY, "subdomain": 7}]), "entry 0: subdomain"),
        (
            _repositories([ENTRY, {**OTHER_ENTRY, "subdomain": "site-a1"}]),
            "entry 1: subdomain is listed twice",
        ),
    ],
)
def test_a_bad_file_is_refused_without_its_values(raw: bytes, fragment: str) -> None:
    with pytest.raises(RepositoryFileError) as exc_info:
        PrototypeRepositoryFile.from_json(raw)
    assert fragment in str(exc_info.value)
    assert "pw-visible" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_reading_the_file(tmp_path: Path) -> None:
    path = tmp_path / "repositories.json"
    path.write_bytes(_repositories([ENTRY]))
    assert PrototypeRepositoryFile.from_file(path).count() == 1
    with pytest.raises(RepositoryFileError, match="cannot be read"):
        PrototypeRepositoryFile.from_file(tmp_path / "missing.json")
