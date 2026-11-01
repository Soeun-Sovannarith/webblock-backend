"""The acceptance of deploy, promote and rollback (decision 0004 section 4; cut from the former
Service class, decision 0025 CQ8): the gate, validation, the unlocked lookup by key,
entitlement, then the acceptance transaction, with the mutation's audit event. The three command
handlers call ``CommandAcceptor.accept`` with their ``Action``; the admin's suspend, resume and
redeploy accept through ``admin.AdminAcceptor``."""

from __future__ import annotations

from datetime import timedelta
from typing import Final, Protocol
from uuid import UUID

from provisioning.core.application.jobs.ports import (
    AcceptanceDecider,
    AcceptanceDecision,
    AcceptanceFacts,
    AcceptanceStore,
    AcceptCommandParams,
    AcceptedCommand,
    insert_decision,
    replay_decision,
)
from provisioning.core.application.operations.auditing import AuditContext, Auditor, OwnerCheck
from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.rows import _InternalError
from provisioning.core.application.operations.views import (
    MAX_CORRELATION_ID_LEN,
    CommandAccepted,
    new_command_accepted,
)
from provisioning.core.application.ports.dependencies import Clock, Entitlement, IdSource
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.audit import AuditAction, AuditActorKind
from provisioning.core.application.shared.errors import (
    DependencyUnavailable,
    LimitReached,
    NotFound,
    OperationInProgress,
    Unauthenticated,
)
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    DomainError,
    InvalidId,
    OwnerStatusOutdated,
    check_command_preconditions,
    compute_request_fingerprint,
    error_chain,
    is_error,
    owner_status_version_of_key,
    validate_idempotency_key,
    validate_requester,
)


class ReleaseCommand(Protocol):
    """What a deploy, promote or rollback command carries (``DeployReleaseCommand``, ...)."""

    @property
    def principal(self) -> Principal: ...
    @property
    def project_id(self) -> UUID: ...
    @property
    def release_id(self) -> UUID: ...
    @property
    def idempotency_key(self) -> str: ...
    @property
    def correlation_id(self) -> str: ...
    @property
    def actor_kind(self) -> AuditActorKind: ...


class CommandAcceptor:
    """Accepts a deploy, promote or rollback with its audit event. ``command_deadline`` is
    COMMAND_DEADLINE (decision 0005 section 7): required and positive (``ValueError``)."""

    __slots__ = (
        "_acceptance",
        "_auditor",
        "_clock",
        "_command_deadline",
        "_entitlement",
        "_gate",
        "_ids",
    )

    def __init__(
        self,
        *,
        gate: OperationGate,
        acceptance: AcceptanceStore,
        entitlement: Entitlement,
        clock: Clock,
        ids: IdSource,
        command_deadline: timedelta,
        auditor: Auditor,
    ) -> None:
        if command_deadline <= timedelta(0):
            raise ValueError("application service: command_deadline must be positive")
        self._gate = gate
        self._acceptance = acceptance
        self._entitlement = entitlement
        self._clock = clock
        self._ids = ids
        self._command_deadline = command_deadline
        self._auditor = auditor

    async def accept(self, action: Action, command: ReleaseCommand) -> CommandAccepted:
        principal = command.principal
        owner_check = OwnerCheck()
        return await self._auditor.run(
            lambda: self._accept_command(principal, action, command, owner_check),
            AuditContext(
                actor_kind=command.actor_kind,
                actor=principal.subject,
                action=_AUDIT_ACTION[action],
                project_id=command.project_id,
                release_id=command.release_id,
                correlation_id=command.correlation_id or None,
            ),
            lambda accepted: accepted.command_id,
            owner_check,
        )

    async def _accept_command(
        self,
        principal: Principal,
        action: Action,
        request: ReleaseCommand,
        owner_check: OwnerCheck,
    ) -> CommandAccepted:
        """The acceptance of decision 0004 section 4 after the gate, outside any transaction:
        validation; the unlocked lookup by key (a replay or a conflict asks no other service);
        entitlement for deploy and promote; a new command ID and the acceptance time; the
        acceptance transaction, whose decision ``new_acceptance_decider`` makes under the lock."""
        owner_check.skipped = await self._gate.check(principal, action, request.project_id)
        kind = action.command_kind()
        if kind is None:
            raise _InternalError(f"action {action} accepts no command")
        command_request = _command_request(kind, principal, request)
        stored = await self._acceptance.find_command_by_key(command_request)
        if stored is not None:
            stored.check_replay(command_request)
            return _accepted_for(command_request, AcceptedCommand(command=stored, replayed=True))
        if kind in (CommandKind.DEPLOY, CommandKind.PROMOTE):
            await self._check_entitlement(command_request.project_id, kind)
        command_id = self._ids.new_id()
        if command_id.int == 0:
            raise InvalidId("the ID source returned the nil UUID")
        now = self._clock.now()
        params = AcceptCommandParams(
            request=command_request,
            command_id=command_id,
            requested_by_subject=principal.subject,
            now=now,
            correlation_id=request.correlation_id,
            deadline_at=now + self._command_deadline,
        )
        accepted = await self._acceptance.accept_command(params, new_acceptance_decider(params))
        return _accepted_for(command_request, accepted)

    async def _check_entitlement(self, project_id: UUID, kind: CommandKind) -> None:
        """The only two outcomes decision 0006 section 3 distinguishes until OD-04 settles
        Payment's contract: ``LimitReached``, or ``DependencyUnavailable`` for every other
        failure. The cause's text is kept, its identity is not, except for the upstream reason
        and deadline a ``DependencyUnavailable`` carries (decision 0023 G14)."""
        try:
            await self._entitlement.check(project_id, kind)
        except Exception as exc:
            if is_error(exc, LimitReached):
                raise LimitReached(f"{kind} of project {project_id}") from None
            upstream = next(
                (e for e in error_chain(exc) if isinstance(e, DependencyUnavailable)), None
            )
            raise DependencyUnavailable(
                f"entitlement of project {project_id}: {exc}",
                upstream_reason=None if upstream is None else upstream.upstream_reason,
                deadline=False if upstream is None else upstream.deadline,
            ) from None


def new_acceptance_decider(params: AcceptCommandParams) -> AcceptanceDecider:
    """The pure decide function of an acceptance (decision 0004 section 4), in this precedence:

    1. facts of another project, or a target other than the one requested: an internal error;
    2. a command under the key: a replay when the request is the same, else
       ``IdempotencyConflict`` (a key holder of another project is ``ProjectMismatch``);
    3. an owner-status key (``user_suspended``) whose version is not the locked row's
       ``owner_status_version``: ``OwnerStatusOutdated`` (decision 0021; a newer owner status
       replaced it, and the owner-status intake records its version before it accepts);
    4. a command in flight: ``OperationInProgress`` naming it;
    5. no target release in the project: ``NotFound``;
    6. ``check_command_preconditions``;
    7. ``insert_decision``.
    """
    request = params.request

    def decide(facts: AcceptanceFacts) -> AcceptanceDecision:
        if facts.hosting.project_id != request.project_id:
            raise _InternalError(f"hosting row of project {facts.hosting.project_id}")
        target = facts.target
        if target is not None and (
            request.release_id is None
            or target.id != request.release_id
            or target.project_id != request.project_id
        ):
            raise _InternalError(f"target release {target.id}")
        if facts.by_key is not None:
            # A key holder of another project is ProjectMismatch here: internal.
            facts.by_key.check_replay(request)
            return replay_decision(facts.by_key)
        version = owner_status_version_of_key(request.idempotency_key)
        if (
            request.kind.is_hosting()
            and version is not None
            and version != facts.hosting.owner_status_version
        ):
            raise OwnerStatusOutdated(
                f"owner status version {version}; the hosting row holds "
                f"{facts.hosting.owner_status_version}"
            )
        if facts.in_flight is not None:
            raise OperationInProgress(facts.in_flight.id)
        if request.release_id is not None and target is None:
            raise NotFound(f"release {request.release_id} in project {request.project_id}")
        check_command_preconditions(request.kind, facts.hosting, target, facts.live)
        return insert_decision(params, facts.hosting)

    return decide


_AUDIT_ACTION: Final = {
    Action.DEPLOY_RELEASE: AuditAction.DEPLOY,
    Action.PROMOTE_RELEASE: AuditAction.PROMOTE,
    Action.ROLLBACK_RELEASE: AuditAction.ROLLBACK,
}


def _command_request(
    kind: CommandKind, principal: Principal, request: ReleaseCommand
) -> CommandRequest:
    """Validate a mutation's input and return the normalized request. The subject is checked
    first, because a failing subject is the unauthenticated class; its text never enters the
    error. A correlation ID outside 1 to 128 characters is a transport fault: internal."""
    try:
        validate_requester(principal.subject)
    except DomainError:
        raise Unauthenticated("the subject is not a valid requester") from None
    if request.release_id.int == 0:
        raise InvalidId("release ID is required")
    validate_idempotency_key(request.idempotency_key)
    if not 1 <= len(request.correlation_id) <= MAX_CORRELATION_ID_LEN:
        raise _InternalError(
            f"correlation ID of {len(request.correlation_id)} characters, "
            f"want 1 to {MAX_CORRELATION_ID_LEN}"
        )
    command_request = CommandRequest(
        project_id=request.project_id,
        kind=kind,
        release_id=request.release_id,
        idempotency_key=request.idempotency_key,
    )
    compute_request_fingerprint(command_request)
    return command_request


def _accepted_for(request: CommandRequest, accepted: AcceptedCommand) -> CommandAccepted:
    """The result of an acceptance, after checking that the store answered for the request's
    project; a command of another project, or none, is a store fault and never returned."""
    if accepted.command.id.int == 0 or accepted.command.project_id != request.project_id:
        raise _InternalError("acceptance returned a command that is not of the request's project")
    return new_command_accepted(accepted)
