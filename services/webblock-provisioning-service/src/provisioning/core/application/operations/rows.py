"""Row checks the operations share (cut from the former Service class, decision 0025 CQ8):
the store faults no transport classifies, the release read of get_release and build_release,
and the page size check of the listings. A row of another project or release is never returned."""

from __future__ import annotations

from uuid import UUID

from provisioning.core.application.operations.ports import (
    MAX_PAGE_SIZE,
    MAX_RELEASE_STEPS,
    ReadStore,
    ReleaseKey,
)
from provisioning.core.application.operations.views import ReleaseView, new_release_view
from provisioning.core.application.shared.errors import CorruptRow


class _InternalError(Exception):
    """A store fault found by the application: facts that do not belong to the request. It
    matches no classified error, so the transports report it as internal."""


class _ForeignRow(_InternalError):
    """A read store returned a row that does not match the request; it is never returned, so a
    store bug cannot show one project's data to a caller authorized for another."""


async def read_release_view(reads: ReadStore, project_id: UUID, release_id: UUID) -> ReleaseView:
    """The release and its steps from the read store, refused when a row belongs to another
    project or release, or there are more steps than a release can have."""
    found = await reads.read_release(ReleaseKey(project_id=project_id, release_id=release_id))
    if found.release.project_id != project_id or found.release.id != release_id:
        raise _ForeignRow(f"release {found.release.id}")
    if len(found.steps) > MAX_RELEASE_STEPS:
        raise CorruptRow(f"release {found.release.id} has {len(found.steps)} steps")
    for step in found.steps:
        if step.release_id != release_id:
            raise _ForeignRow(f"step {step.id} of release {step.release_id}")
    return new_release_view(found)


def check_page_size(size: int) -> None:
    """A page size outside 1 to MAX_PAGE_SIZE is a transport fault (the transports validate it
    first): ``ValueError``, reported as internal."""
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise ValueError(f"page size {size} outside 1 to {MAX_PAGE_SIZE}")
