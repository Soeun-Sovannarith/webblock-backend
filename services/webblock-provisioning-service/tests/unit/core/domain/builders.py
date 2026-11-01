"""Fixed values and release rows shared by the domain tests (decision 0001 section 2): the IDs, the
commit, the repository and ``valid_row``, a release with the fields the database constraints
require for its status. Test adapters only; nothing in ``src/`` imports them."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import UUID

from provisioning.core.domain import Release, ReleaseStatus

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)

ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
ID_PROJECT2 = UUID("0190a1b2-0000-7000-8000-000000000002")
ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
ID_RELEASE2 = UUID("0190a1b2-0000-7000-8000-0000000000a2")
ID_PREV = UUID("0190a1b2-0000-7000-8000-0000000000b1")
ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")

SHA40 = "0123456789abcdef0123456789abcdef01234567"
REPO_ID = 4242


def image_digest(fill: str) -> str:
    """A sha256 image digest of 64 times ``fill``."""
    return "sha256:" + fill * 64


def base_release(status: ReleaseStatus) -> Release:
    """A release in the status with only its identity set: a fixture for transition rules, not
    necessarily a valid stored row (``valid_row`` gives that)."""
    return Release(
        id=ID_RELEASE,
        project_id=ID_PROJECT,
        generation_request_id=ID_REQUEST,
        repository_id=REPO_ID,
        git_commit_sha=SHA40,
        status=status,
        created_at=T0,
        updated_at=T0,
    )


def valid_row(status: ReleaseStatus) -> Release:
    """``base_release`` with the fields the database constraints require for the status."""
    s = ReleaseStatus
    r = base_release(status)
    if status in (s.BUILT, s.CANDIDATE_READY, s.PROMOTING, s.LIVE, s.SUPERSEDED, s.ROLLED_BACK):
        r = dataclasses.replace(r, container_image_digest=image_digest("a"))
    if status in (s.LIVE, s.SUPERSEDED):
        r = dataclasses.replace(r, promoted_at=T0)
    if status in (s.FAILED, s.ROLLED_BACK):
        r = dataclasses.replace(r, failure_code="promotion_failed")
    return r
