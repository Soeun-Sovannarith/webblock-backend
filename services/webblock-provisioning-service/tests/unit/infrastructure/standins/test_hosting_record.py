"""The worker's hosting record with project deletion (decision 0014 section 21 answer 4).

``PrototypeHostingRecord`` is the in-process entitlement record with one more fact: a project with
a deletion command is "ended" for every question, so a later "entitlement restored" never resumes
it. The deletion fact comes from the database (``DeletionLookup``), so a restarted worker still
knows it.
"""

from uuid import UUID

import pytest

from provisioning.infrastructure.standins.entitlement_record import PrototypeHostingRecord

PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
OTHER = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e02")


class _Deletions:
    def __init__(self, deleted: set[UUID], fault: Exception | None = None) -> None:
        self.deleted = deleted
        self.fault = fault
        self.asked: list[UUID] = []

    async def project_deleted(self, project_id: UUID) -> bool:
        self.asked.append(project_id)
        if self.fault is not None:
            raise self.fault
        return project_id in self.deleted


@pytest.mark.asyncio
async def test_a_deleted_project_is_ended_even_after_a_restore() -> None:
    record = PrototypeHostingRecord(max_projects=10, deletions=_Deletions({PROJECT}))
    record.record(PROJECT, event_key="ended", entitled=False)
    record.record(PROJECT, event_key="restored", entitled=True)
    assert await record.entitlement_after(PROJECT, "ended") is False
    # A restarted worker knows no event at all, and still answers ended.
    fresh = PrototypeHostingRecord(max_projects=10, deletions=_Deletions({PROJECT}))
    assert await fresh.entitlement_after(PROJECT, "restored-after-restart") is False


@pytest.mark.asyncio
async def test_a_project_that_was_not_deleted_answers_from_its_events() -> None:
    deletions = _Deletions({OTHER})
    record = PrototypeHostingRecord(max_projects=10, deletions=deletions)
    record.record(PROJECT, event_key="ended", entitled=False)
    record.record(PROJECT, event_key="restored", entitled=True)
    assert await record.entitlement_after(PROJECT, "ended") is True
    assert await record.entitlement_after(PROJECT, "restored") is None
    assert deletions.asked == [PROJECT, PROJECT]


@pytest.mark.asyncio
async def test_a_failed_deletion_lookup_is_raised_not_taken_as_not_deleted() -> None:
    record = PrototypeHostingRecord(
        max_projects=10, deletions=_Deletions(set(), fault=ConnectionError("down"))
    )
    record.record(PROJECT, event_key="ended", entitled=False)
    record.record(PROJECT, event_key="restored", entitled=True)
    with pytest.raises(ConnectionError):
        await record.entitlement_after(PROJECT, "ended")


def test_it_keeps_the_bounds_of_the_record() -> None:
    with pytest.raises(ValueError, match="positive bounds"):
        PrototypeHostingRecord(max_projects=0, deletions=_Deletions(set()))
