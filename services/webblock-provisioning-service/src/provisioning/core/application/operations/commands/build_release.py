"""build_release (decision 0014 section 3, "Build"; decision 0016 section 5 item 2): start the
build of an accepted release, or the next attempt after a failed one, and return the release as
read after the start, with its audit event (decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.auditing import AuditContext, Auditor
from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import BuildStarter, ReadStore
from provisioning.core.application.operations.rows import read_release_view
from provisioning.core.application.operations.views import ReleaseView
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.audit import AuditAction, AuditActorKind
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class BuildReleaseCommand(cqrs.Command[ReleaseView]):
    """The verified principal and the release. ``correlation_id`` is the call's, for its audit
    event; empty when there is none."""

    principal: Principal
    project_id: UUID
    release_id: UUID
    correlation_id: str = ""
    actor_kind: AuditActorKind = AuditActorKind.USER


class BuildReleaseHandler:
    __slots__ = ("_auditor", "_builds", "_gate", "_reads")

    def __init__(
        self, *, gate: OperationGate, builds: BuildStarter, reads: ReadStore, auditor: Auditor
    ) -> None:
        self._gate = gate
        self._builds = builds
        self._reads = reads
        self._auditor = auditor

    async def handle(self, command: BuildReleaseCommand, /) -> ReleaseView:
        """Start the build of an accepted release, or the next attempt of a building release
        whose last attempt failed while attempts remain (decision 0014 section 3, "Build"), and
        return the release as read after the start. ``NotFound``, ``ReleaseNotBuildable`` or
        ``BuildInProgress`` otherwise, with nothing read.

        The answer is read under the build_release permission the gate checked; the caller is
        not asked for get_release, so a build is never started for a caller whose answer would
        then be refused. The principal's ``build_release`` audit event is recorded here; the
        build use case records the attempt it starts."""
        return await self._auditor.run(
            lambda: self._build_release(command),
            AuditContext(
                actor_kind=command.actor_kind,
                actor=command.principal.subject,
                action=AuditAction.BUILD_RELEASE,
                project_id=command.project_id,
                release_id=command.release_id,
                correlation_id=command.correlation_id or None,
            ),
            lambda _release: None,
        )

    async def _build_release(self, command: BuildReleaseCommand) -> ReleaseView:
        await self._gate.check(command.principal, Action.BUILD_RELEASE, command.project_id)
        if command.release_id.int == 0:
            raise InvalidId("release ID is required")
        await self._builds.start(command.project_id, command.release_id)
        return await read_release_view(self._reads, command.project_id, command.release_id)
