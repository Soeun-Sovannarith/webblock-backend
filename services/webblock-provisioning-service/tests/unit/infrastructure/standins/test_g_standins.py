"""Task G's prototype stand-ins: the in-process record of Payment's events per project (F2
report, "Contract of the in-process entitlement record"; decision 0014 section 17 item 2) and the
repository file that is read again when it changes (so that a running build worker sees projects
registered after it started)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application import DependencyUnavailable, RegisteredRepository
from provisioning.infrastructure.standins import (
    PrototypePaymentEventRecord,
    PrototypeReloadingRepositoryFile,
    RepositoryFileError,
)

P1 = UUID("0190a1b2-0000-7000-8000-000000000001")
P2 = UUID("0190a1b2-0000-7000-8000-000000000002")


# --- the entitlement record -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_is_known_of_a_project_or_an_event_never_recorded() -> None:
    """Decision 0014 section 17 item 2 (M2): unknown is None, never "entitled"."""
    record = PrototypePaymentEventRecord(max_projects=10)
    assert await record.entitlement_after(P1, "evt-ended") is None
    record.record(P1, event_key="evt-other", entitled=True)
    assert await record.entitlement_after(P1, "evt-ended") is None  # its own event is unknown


@pytest.mark.asyncio
async def test_only_an_event_after_the_given_one_counts() -> None:
    record = PrototypePaymentEventRecord(max_projects=10)
    record.record(P1, event_key="evt-restored-0", entitled=True)
    record.record(P1, event_key="evt-ended-1", entitled=False)
    # The suspend's own event is the latest: nothing after it.
    assert await record.entitlement_after(P1, "evt-ended-1") is None
    # A restore before it does not count for it, but its own end counts for the earlier resume.
    assert await record.entitlement_after(P1, "evt-restored-0") is False
    record.record(P1, event_key="evt-restored-2", entitled=True)
    assert await record.entitlement_after(P1, "evt-ended-1") is True
    assert await record.entitlement_after(P2, "evt-ended-1") is None


@pytest.mark.asyncio
async def test_the_latest_later_event_wins() -> None:
    record = PrototypePaymentEventRecord(max_projects=10)
    record.record(P1, event_key="e1", entitled=False)
    record.record(P1, event_key="r2", entitled=True)
    record.record(P1, event_key="e3", entitled=False)
    assert await record.entitlement_after(P1, "e1") is False
    assert await record.entitlement_after(P1, "r2") is False


@pytest.mark.asyncio
async def test_a_redelivered_event_keeps_its_first_position() -> None:
    record = PrototypePaymentEventRecord(max_projects=10)
    record.record(P1, event_key="e1", entitled=False)
    record.record(P1, event_key="r2", entitled=True)
    record.record(P1, event_key="e1", entitled=False)  # redelivered after a busy refusal
    assert await record.entitlement_after(P1, "e1") is True
    assert record.event_count() == 2


@pytest.mark.asyncio
async def test_the_record_is_bounded_and_forgets_the_oldest_project() -> None:
    record = PrototypePaymentEventRecord(max_projects=2)
    record.record(P1, event_key="e1", entitled=False)
    record.record(P1, event_key="r1", entitled=True)
    record.record(P2, event_key="e2", entitled=False)
    third = UUID("0190a1b2-0000-7000-8000-000000000003")
    record.record(third, event_key="e3", entitled=False)
    assert len(record) == 2
    assert await record.entitlement_after(P1, "e1") is None  # forgotten: unknown, so it runs
    assert await record.entitlement_after(P2, "e2") is None
    record.record(third, event_key="r3", entitled=True)
    assert await record.entitlement_after(third, "e3") is True


@pytest.mark.asyncio
async def test_each_project_keeps_a_bounded_number_of_events_dropping_the_oldest() -> None:
    record = PrototypePaymentEventRecord(max_projects=10, max_events_per_project=3)
    for i in range(5):
        record.record(P1, event_key=f"e{i}", entitled=i % 2 == 1)
    assert record.event_count() == 3
    assert await record.entitlement_after(P1, "e0") is None  # dropped: unknown
    assert await record.entitlement_after(P1, "e1") is None
    assert await record.entitlement_after(P1, "e2") is False  # e4 is the latest, an end


def test_the_record_needs_positive_bounds() -> None:
    with pytest.raises(ValueError):
        PrototypePaymentEventRecord(max_projects=0)
    with pytest.raises(ValueError):
        PrototypePaymentEventRecord(max_projects=1, max_events_per_project=0)


# --- the reloading repository file ------------------------------------------------------------


def _write(path: Path, entries: list[dict[str, object]]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"repositories": entries}))
    os.replace(tmp, path)


def _entry(repository_id: int, project: UUID) -> dict[str, object]:
    return {
        "repository_id": repository_id,
        "project_id": str(project),
        "subdomain": f"site-{repository_id}",
        "git_url": f"http://webblock-git:8080/r{repository_id}.git",
    }


@pytest.mark.asyncio
async def test_a_project_registered_after_the_start_is_seen(tmp_path: Path) -> None:
    path = tmp_path / "repositories.json"
    _write(path, [_entry(1, P1)])
    file = PrototypeReloadingRepositoryFile.from_file(path)
    assert await file.registered_repository(P1) == RegisteredRepository(1, "site-1")
    assert await file.registered_repository(P2) is None
    _write(path, [_entry(1, P1), _entry(2, P2)])
    assert await file.registered_repository(P2) == RegisteredRepository(2, "site-2")
    assert await file.repository_url(P2, 2) == "http://webblock-git:8080/r2.git"


@pytest.mark.asyncio
async def test_a_broken_rewrite_keeps_the_last_good_copy(tmp_path: Path) -> None:
    path = tmp_path / "repositories.json"
    _write(path, [_entry(1, P1)])
    file = PrototypeReloadingRepositoryFile.from_file(path)
    path.write_text('{"repositories": [{"project_id": "secret-value"}]}')
    assert await file.registered_repository(P1) == RegisteredRepository(1, "site-1")
    assert await file.repository_url(P1, 1) == "http://webblock-git:8080/r1.git"


@pytest.mark.asyncio
async def test_a_foreign_repository_is_unavailable(tmp_path: Path) -> None:
    path = tmp_path / "repositories.json"
    _write(path, [_entry(1, P1)])
    file = PrototypeReloadingRepositoryFile.from_file(path)
    with pytest.raises(DependencyUnavailable):
        await file.repository_url(P2, 1)


def test_a_broken_file_at_the_start_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "repositories.json"
    path.write_text("not json")
    with pytest.raises(RepositoryFileError):
        PrototypeReloadingRepositoryFile.from_file(path)
