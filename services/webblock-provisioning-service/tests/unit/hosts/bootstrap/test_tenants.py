"""QA-04, the second half: a tenant rebuilt from nothing gets its catalog Secret again although the
project still records a live release, because the live release's pods went with the namespace.
Since task F2B the hook reads that from the namespace's mark ``catalog-secret: pending``
(``tests/unit/infrastructure/kubernetes/test_catalog_secret_mark.py`` drives the adapter that
writes and reads it), so it holds after a restart."""

import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest

from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.hosts.bootstrap.tenants import AccessSetupHook, pods_may_hold_the_secret
from provisioning.infrastructure.catalog import AccessOutcome
from provisioning.infrastructure.telemetry import scrub

A = UUID("0190a1b2-0000-7000-8000-000000000001")
T = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("live", "fresh", "expected"),
    [(True, False, True), (True, True, False), (False, False, False), (False, True, False)],
)
def test_only_a_live_release_in_a_namespace_that_survived_may_hold_the_secret(
    live: bool, fresh: bool, expected: bool
) -> None:
    assert pods_may_hold_the_secret(has_live_release=live, created_from_nothing=fresh) is expected


class _Marks:
    """The namespace's mark as ``KubernetesTenantBootstrap.catalog_secret_pending`` reads it."""

    def __init__(self, pending: bool) -> None:
        self.pending = pending
        self.reads: list[UUID] = []

    async def catalog_secret_pending(self, project_id: UUID) -> bool:
        self.reads.append(project_id)
        return self.pending


class _Access:
    def __init__(self, outcome: AccessOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[UUID, bool]] = []
        self.error: Exception | None = None

    async def run(self, project_id: UUID, *, has_live_release: bool) -> AccessOutcome:
        self.calls.append((project_id, has_live_release))
        if self.error is not None:
            raise self.error
        return self.outcome


class _Hosting:
    def __init__(self, live: bool) -> None:
        self.live = live

    async def has_live_release(self, project_id: UUID) -> bool:
        return self.live


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("access_setup never alerts")


class _Clock:
    def now(self) -> datetime:
        return T


def _hook(
    *, live: bool, pending: bool, outcome: AccessOutcome
) -> tuple[AccessSetupHook, _Access, _Audit, _Marks]:
    access, audit, marks = _Access(outcome), _Audit(), _Marks(pending)
    hook = AccessSetupHook(
        access=access,
        hosting=_Hosting(live),
        marks=marks,
        audit=audit,
        clock=_Clock(),
        logger=logging.getLogger("test.bootstrap"),
    )
    return hook, access, audit, marks


@pytest.mark.asyncio
async def test_a_pending_namespace_of_a_live_site_rotates_and_audits_as_bootstrap() -> None:
    hook, access, audit, marks = _hook(live=True, pending=True, outcome=AccessOutcome.DELIVERED)
    await hook(A)
    assert marks.reads == [A]
    assert access.calls == [(A, False)]  # no pod can hold the old password
    assert audit.events == [
        AuditEvent(
            actor_kind=AuditActorKind.SYSTEM,
            actor="bootstrap",
            action=AuditAction.CATALOG_CREDENTIAL_ROTATED,
            project_id=A,
            release_id=None,
            command_id=None,
            result=AuditResult.SUCCEEDED,
            failure_code=None,
            at=T,
            correlation_id=None,
        )
    ]


@pytest.mark.asyncio
async def test_a_namespace_not_pending_of_a_live_site_keeps_the_rule_of_0011() -> None:
    """``delivered``, or no mark at all (a namespace from before task F2B)."""
    hook, access, audit, _ = _hook(live=True, pending=False, outcome=AccessOutcome.VERIFIED)
    await hook(A)
    assert access.calls == [(A, True)]
    assert audit.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [True, False])
async def test_without_a_live_release_the_mark_changes_nothing(pending: bool) -> None:
    hook, access, _, _ = _hook(live=False, pending=pending, outcome=AccessOutcome.CREATED)
    await hook(A)
    assert access.calls == [(A, False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [AccessOutcome.CREATED, AccessOutcome.VERIFIED])
async def test_a_first_password_or_a_verification_is_not_a_rotation(
    outcome: AccessOutcome,
) -> None:
    hook, _, audit, _ = _hook(live=False, pending=True, outcome=outcome)
    await hook(A)
    assert audit.events == []


@pytest.mark.asyncio
async def test_any_delivered_new_password_is_audited_as_a_rotation() -> None:
    """An existing role whose Secret is missing while nothing is live also gets a new password."""
    hook, _, audit, _ = _hook(live=False, pending=False, outcome=AccessOutcome.DELIVERED)
    await hook(A)
    assert [e.action for e in audit.events] == [AuditAction.CATALOG_CREDENTIAL_ROTATED]


@pytest.mark.asyncio
async def test_a_failed_access_setup_audits_nothing_and_raises() -> None:
    """The loop ensures the tenant again next round; the mark stays in the namespace."""
    hook, access, audit, _ = _hook(live=True, pending=True, outcome=AccessOutcome.DELIVERED)
    access.error = RuntimeError("catalog down")
    with pytest.raises(RuntimeError):
        await hook(A)
    assert audit.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending", "outcome", "shown"),
    [
        (True, AccessOutcome.DELIVERED, "pending->delivered"),
        (False, AccessOutcome.CREATED, "delivered"),
        (True, AccessOutcome.VERIFIED, "pending"),
        (False, AccessOutcome.VERIFIED, "unchanged"),
    ],
)
async def test_the_done_line_shows_the_mark_and_survives_the_log_scrubber(
    caplog: pytest.LogCaptureFixture, pending: bool, outcome: AccessOutcome, shown: str
) -> None:
    """The e2e test reads the mark from the bootstrap's log, where every message goes through
    ``scrub``; a key such as ``catalog_secret`` would be redacted to ``[REDACTED]`` there."""
    hook, _, _, _ = _hook(live=False, pending=pending, outcome=outcome)
    with caplog.at_level(logging.INFO, logger="test.bootstrap"):
        await hook(A)
    assert [scrub(r.getMessage()) for r in caplog.records] == [
        f"access_setup done (project_id={A} outcome={outcome} catalog_mark={shown})"
    ]
