"""access_setup after a ready tenant, with the rule for a tenant created from nothing (QA-04;
decision 0014 section 24 as amended by decision 0016 section 8 ruling 4, task F2B).

access_setup refuses to write a new catalog password while the project has a live release and its
Secret ``catalog-runtime`` is missing (decision 0011 section 5): the live pods may still hold the
old password, so an operator must act. That rule assumes the Secret was lost from a namespace that
still runs the live release. When the whole namespace ``wb-<projectId>`` was deleted, the live
release's pods went with it, so nothing holds the old password and delivering a new one is safe;
without it the tenant that the loop rebuilds could never run a deploy again.

Which namespaces were created from nothing is durable in the namespace itself: the bootstrap
creates it with the annotation ``websites.webblock.internal/catalog-secret: pending``, and
access_setup sets ``delivered`` before it writes the Secret (``KubernetesTenantBootstrap``). So
"namespace present, Secret absent, mark ``pending``" means no pod in it ever received a password,
and it holds after a restart of the bootstrap, which the in-process marks of tasks H2 and BT
(``FreshTenants``, removed) forgot. A namespace without the mark (one from before task F2B) or
with ``delivered`` keeps the rule of decision 0011 section 5 (``runtime_secret_missing``, for an
operator): the safe direction. The admission policies keep the mark from moving back.

``AccessSetupHook`` is the loop's after-ready step: it reads the mark, then runs access_setup with
that rule. A new password given to an existing site role (``DELIVERED``) is audited as
``catalog_credential_rotated`` with actor ``bootstrap`` (section 24, "Audit"); every delivery
(``CREATED`` or ``DELIVERED``) moved the mark to ``delivered``, which the "access_setup done" log
line shows with the mark it read.
"""

from __future__ import annotations

import logging
from typing import Final, Protocol
from uuid import UUID

from provisioning.core.application.ports.dependencies import Clock
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.infrastructure.catalog import AccessOutcome

AUDIT_ACTOR: Final = "bootstrap"


class CatalogSecretMarks(Protocol):
    """``KubernetesTenantBootstrap.catalog_secret_pending``."""

    async def catalog_secret_pending(self, project_id: UUID) -> bool: ...


def pods_may_hold_the_secret(*, has_live_release: bool, created_from_nothing: bool) -> bool:
    """access_setup's ``has_live_release`` argument: a live release whose pods may still run."""
    return has_live_release and not created_from_nothing


class AccessSetup(Protocol):
    """``infrastructure.catalog.CatalogAccessSetup.run``."""

    async def run(self, project_id: UUID, *, has_live_release: bool) -> AccessOutcome: ...


class LiveReleases(Protocol):
    """``PgTenantProjects.has_live_release``."""

    async def has_live_release(self, project_id: UUID) -> bool: ...


class AccessSetupHook:
    """The bootstrap loop's ``after_ready``: access_setup for a ready tenant (decision 0014
    sections 14 and 24). Its failure is raised, so the loop ensures the tenant again next round;
    the mark stays in the namespace."""

    __slots__ = ("_access", "_audit", "_clock", "_hosting", "_logger", "_marks")

    def __init__(
        self,
        *,
        access: AccessSetup,
        hosting: LiveReleases,
        marks: CatalogSecretMarks,
        audit: AuditLog,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        self._access = access
        self._hosting = hosting
        self._marks = marks
        self._audit = audit
        self._clock = clock
        self._logger = logger

    async def __call__(self, project_id: UUID) -> None:
        pending = await self._marks.catalog_secret_pending(project_id)
        outcome = await self._access.run(
            project_id,
            has_live_release=pods_may_hold_the_secret(
                has_live_release=await self._hosting.has_live_release(project_id),
                created_from_nothing=pending,
            ),
        )
        if outcome is AccessOutcome.DELIVERED:
            self._audit.record(
                AuditEvent(
                    actor_kind=AuditActorKind.SYSTEM,
                    actor=AUDIT_ACTOR,
                    action=AuditAction.CATALOG_CREDENTIAL_ROTATED,
                    project_id=project_id,
                    release_id=None,
                    command_id=None,
                    result=AuditResult.SUCCEEDED,
                    failure_code=None,
                    at=self._clock.now(),
                    correlation_id=None,
                )
            )
        if outcome is AccessOutcome.VERIFIED:
            mark = "pending" if pending else "unchanged"
        else:  # a Secret was delivered, after the mark was set
            mark = "pending->delivered" if pending else "delivered"
        self._logger.info(
            "access_setup done (project_id=%s outcome=%s catalog_mark=%s)",
            project_id,
            outcome,
            mark,
        )
