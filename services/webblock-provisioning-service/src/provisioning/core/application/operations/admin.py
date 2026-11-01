"""The admin acceptance and the admin read's views (decision 0023 G9 to G11, G13; Notion
"5 · Provisioning"). The handlers of suspend_website, resume_website and redeploy_release call
``AdminAcceptor``; admin_list_commands is a query (``queries/admin_list_commands.py``).

Only a principal with the ``ADMIN`` role takes them, on any project and without the Project
access check (the gate's admin-only rule, 0023 G10). Each runs the gate first, then validation,
then the store.

- ``suspend_website`` and ``resume_website`` accept a ``suspend_hosting`` or ``resume_hosting``
  command whose key is ``admin:<key>`` (so it never takes a system key such as
  ``project-deleted``), whose reason is ``admin_suspended`` and whose requester is the admin's
  subject. A suspend adds only that reason and a resume removes only it; the worker applies the
  reason rules of decision 0021 (D5, D6, D7), so a resume when the reason is absent, or a suspend
  of a site already offline for another reason, is still accepted here.
- ``redeploy`` accepts a ``redeploy`` command of the project's live release, read under the lock
  of the hosting row: a live release and hosting online (else ``RedeployNotAllowed``), routing
  stable (else ``RouteNotStable``), no command in flight (else ``OperationInProgress``). Its key is
  the admin's key in the client key class.
- ``list_commands`` is the project's commands newest first with their requester, paged like the
  client listing, which never shows the requester.

The idempotency rules are the client's: the same key and the same request returns the original
command; the same key with another request is ``IdempotencyConflict``.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    AcceptRedeployParams,
    RedeployStore,
    insert_decision,
    replay_decision,
)
from provisioning.core.application.operations.views import (
    MAX_CORRELATION_ID_LEN,
    CommandAccepted,
    CommandView,
    new_command_accepted,
)
from provisioning.core.application.ports.dependencies import Clock, IdSource
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.errors import (
    IdempotencyKeyTooLong,
    NotFound,
    OperationInProgress,
    OperationNotAvailable,
    RedeployNotAllowed,
    Unauthenticated,
)
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import (
    ADMIN_KEY_PREFIX as _DOMAIN_ADMIN_KEY_PREFIX,
)
from provisioning.core.domain import (
    MAX_IDEMPOTENCY_KEY_LEN,
    Command,
    CommandKind,
    CommandRequest,
    DomainError,
    HostingOffline,
    IdempotencyConflict,
    InvalidId,
    admin_hosting_key,
    check_command_preconditions,
    validate_idempotency_key,
    validate_requester,
)

ADMIN_KEY_PREFIX: Final = _DOMAIN_ADMIN_KEY_PREFIX
"""The prefix of the stored key of an admin's suspend or resume: ``admin:``."""

MAX_ADMIN_KEY_BYTES: Final = MAX_IDEMPOTENCY_KEY_LEN - len(ADMIN_KEY_PREFIX.encode("utf-8"))
"""The longest key an admin may send for a suspend or resume (249 UTF-8 bytes), so the stored
``admin:<key>`` keeps the 255 byte rule."""


class AdminGate(Protocol):
    """The check every operation passes first (``OperationGate.check``)."""

    async def check(self, principal: Principal, action: Action, project_id: UUID) -> bool: ...


@dataclass(frozen=True, slots=True)
class AdminCommandView(CommandView):
    """A command as the admin read shows it: the client's view plus the requester (the subject
    that asked for it, None for a command from an event). The client read never shows it."""

    requested_by_subject: str | None


@dataclass(frozen=True, slots=True)
class AdminCommandPage:
    """One page of the admin read, newest first; ``next_after`` (the last item's command number)
    is None on the last page."""

    items: tuple[AdminCommandView, ...]
    next_after: int | None


def new_admin_command_view(command: Command) -> AdminCommandView:
    return AdminCommandView(
        command_id=command.id,
        project_id=command.project_id,
        release_id=command.release_id,
        kind=command.kind,
        command_number=command.command_number,
        status=command.status,
        failure_code=command.failure_code,
        created_at=command.created_at,
        updated_at=command.updated_at,
        requested_by_subject=command.requested_by_subject,
    )


@dataclass(frozen=True, slots=True)
class AdminDeps:
    """What the admin acceptance is built from. ``command_deadline`` is COMMAND_DEADLINE, required
    and positive. ``redeploys`` None makes ``redeploy`` ``OperationNotAvailable``."""

    gate: AdminGate
    acceptance: AcceptanceStore
    clock: Clock
    ids: IdSource
    command_deadline: timedelta
    redeploys: RedeployStore | None = None


class AdminAcceptor:
    """The three admin mutations. Results never carry a command's key or fingerprint."""

    __slots__ = ("_deps",)

    def __init__(self, deps: AdminDeps) -> None:
        """Refuse a command_deadline that is not positive (``ValueError``). Calls no port."""
        if deps.command_deadline <= timedelta(0):
            raise ValueError("admin operations: command_deadline must be positive")
        self._deps = deps

    async def suspend_website(
        self,
        principal: Principal,
        project_id: UUID,
        idempotency_key: str,
        *,
        correlation_id: str = "",
    ) -> CommandAccepted:
        """Accept an admin's suspend of one website: reason ``admin_suspended``."""
        return await self._hosting(
            principal,
            Action.SUSPEND_WEBSITE,
            CommandKind.SUSPEND_HOSTING,
            project_id,
            idempotency_key,
            correlation_id,
        )

    async def resume_website(
        self,
        principal: Principal,
        project_id: UUID,
        idempotency_key: str,
        *,
        correlation_id: str = "",
    ) -> CommandAccepted:
        """Accept an admin's resume of one website: removes only ``admin_suspended``."""
        return await self._hosting(
            principal,
            Action.RESUME_WEBSITE,
            CommandKind.RESUME_HOSTING,
            project_id,
            idempotency_key,
            correlation_id,
        )

    async def redeploy(
        self,
        principal: Principal,
        project_id: UUID,
        idempotency_key: str,
        *,
        correlation_id: str = "",
    ) -> CommandAccepted:
        """Accept a redeploy of the project's live release. The release is read under the lock,
        so the command names the release that is live when it is accepted."""
        await self._deps.gate.check(principal, Action.REDEPLOY_RELEASE, project_id)
        subject = _requester(principal)
        validate_idempotency_key(idempotency_key)
        store = self._deps.redeploys
        if store is None:
            raise OperationNotAvailable(str(Action.REDEPLOY_RELEASE))
        command_id = self._new_id()
        now = self._deps.clock.now()
        params = AcceptRedeployParams(
            project_id=project_id,
            idempotency_key=idempotency_key,
            command_id=command_id,
            requested_by_subject=subject,
            now=now,
            correlation_id=_correlation(correlation_id, command_id),
            deadline_at=now + self._deps.command_deadline,
        )
        try:
            accepted = await store.accept_redeploy(params, new_redeploy_decider(params))
        except NotFound:
            raise RedeployNotAllowed(f"project {project_id} has no hosting row") from None
        if accepted.command.kind is not CommandKind.REDEPLOY:
            raise _InternalFault(f"redeploy acceptance returned a {accepted.command.kind} command")
        return _accepted_for(project_id, accepted)

    async def _hosting(
        self,
        principal: Principal,
        action: Action,
        kind: CommandKind,
        project_id: UUID,
        idempotency_key: str,
        correlation_id: str,
    ) -> CommandAccepted:
        """The acceptance of an admin hosting command: the unlocked lookup by the stored key (a
        replay or a conflict writes nothing), then the acceptance transaction."""
        await self._deps.gate.check(principal, action, project_id)
        subject = _requester(principal)
        request = CommandRequest(
            project_id=project_id,
            kind=kind,
            release_id=None,
            idempotency_key=_admin_key(idempotency_key),
        )
        stored = await self._deps.acceptance.find_command_by_key(request)
        if stored is not None:
            stored.check_replay(request)
            return _accepted_for(project_id, AcceptedCommand(command=stored, replayed=True))
        command_id = self._new_id()
        now = self._deps.clock.now()
        params = AcceptCommandParams(
            request=request,
            command_id=command_id,
            requested_by_subject=subject,
            now=now,
            correlation_id=_correlation(correlation_id, command_id),
            deadline_at=now + self._deps.command_deadline,
        )
        accepted = await self._deps.acceptance.accept_command(
            params, new_admin_hosting_decider(params)
        )
        return _accepted_for(project_id, accepted)

    def _new_id(self) -> UUID:
        command_id = self._deps.ids.new_id()
        if command_id.int == 0:
            raise InvalidId("the ID source returned the nil UUID")
        return command_id


def new_admin_hosting_decider(params: AcceptCommandParams) -> AcceptanceDecider:
    """The decide function of an admin's suspend or resume, in this precedence: facts of another
    project (internal); a command under the key (a replay when the request is the same, else
    ``IdempotencyConflict``); a command in flight (``OperationInProgress``);
    ``check_command_preconditions`` (routing stable); ``insert_decision``. The hosting status is
    not checked: the worker applies the reason rules when it runs (decision 0021 D5, D7)."""
    request = params.request

    def decide(facts: AcceptanceFacts) -> AcceptanceDecision:
        if facts.hosting.project_id != request.project_id:
            raise _InternalFault(f"hosting row of project {facts.hosting.project_id}")
        if facts.by_key is not None:
            facts.by_key.check_replay(request)
            return replay_decision(facts.by_key)
        if facts.in_flight is not None:
            raise OperationInProgress(facts.in_flight.id)
        check_command_preconditions(request.kind, facts.hosting, None, facts.live)
        return insert_decision(params, facts.hosting)

    return decide


def new_redeploy_decider(params: AcceptRedeployParams) -> AcceptanceDecider:
    """The decide function of a redeploy (Notion "redeploy", decision 0023 G9), in this
    precedence: facts of another project (internal); a command under the key (a replay when it
    is a redeploy of the project, whatever release it names, since the admin sends none; else
    ``IdempotencyConflict``); a command in flight (``OperationInProgress``); no live release
    (``RedeployNotAllowed``); ``check_command_preconditions(REDEPLOY, ...)`` on the live release,
    where routing not stable is ``RouteNotStable`` and hosting offline ``RedeployNotAllowed``;
    ``insert_decision`` naming the live release."""

    def decide(facts: AcceptanceFacts) -> AcceptanceDecision:
        hosting = facts.hosting
        if hosting.project_id != params.project_id:
            raise _InternalFault(f"hosting row of project {hosting.project_id}")
        holder = facts.by_key
        if holder is not None:
            if holder.kind is not CommandKind.REDEPLOY or holder.release_id is None:
                raise IdempotencyConflict(f"command {holder.id}")
            holder.check_replay(_redeploy_request(params, holder.release_id))
            return replay_decision(holder)
        if facts.in_flight is not None:
            raise OperationInProgress(facts.in_flight.id)
        live = facts.live
        if live is None:
            raise RedeployNotAllowed(f"project {hosting.project_id} has no live release")
        try:
            check_command_preconditions(CommandKind.REDEPLOY, hosting, live, live)
        except HostingOffline:
            raise RedeployNotAllowed(f"project {hosting.project_id} is offline") from None
        return insert_decision(
            AcceptCommandParams(
                request=_redeploy_request(params, live.id),
                command_id=params.command_id,
                requested_by_subject=params.requested_by_subject,
                now=params.now,
                correlation_id=params.correlation_id,
                deadline_at=params.deadline_at,
            ),
            hosting,
        )

    return decide


class _InternalFault(Exception):
    """Facts or rows that do not belong to the request (a store fault), or a transport fault. It
    matches no classified error, so the transports report it as internal and never show the
    row."""


def _redeploy_request(params: AcceptRedeployParams, release_id: UUID) -> CommandRequest:
    return CommandRequest(
        project_id=params.project_id,
        kind=CommandKind.REDEPLOY,
        release_id=release_id,
        idempotency_key=params.idempotency_key,
    )


def _requester(principal: Principal) -> str:
    """The admin's subject, stored as the requester; one that fails the requester rule is the
    unauthenticated class, and its text never enters the error."""
    try:
        validate_requester(principal.subject)
    except DomainError:
        raise Unauthenticated("the subject is not a valid requester") from None
    return principal.subject


def _admin_key(client_key: str) -> str:
    """``admin:<client_key>``: a key over MAX_ADMIN_KEY_BYTES is ``IdempotencyKeyTooLong``, any
    other break of the key rule ``InvalidIdempotencyKey``."""
    if len(client_key.encode("utf-8", "surrogatepass")) > MAX_ADMIN_KEY_BYTES:
        raise IdempotencyKeyTooLong(f"an admin key is at most {MAX_ADMIN_KEY_BYTES} bytes")
    return admin_hosting_key(client_key)


def _correlation(correlation_id: str, command_id: UUID) -> str:
    """The request's correlation ID, or the command ID when the caller gave none. One over
    MAX_CORRELATION_ID_LEN characters is a transport fault: internal."""
    if not correlation_id:
        return str(command_id)
    if len(correlation_id) > MAX_CORRELATION_ID_LEN:
        raise _InternalFault(f"correlation ID over {MAX_CORRELATION_ID_LEN} characters")
    return correlation_id


def _accepted_for(project_id: UUID, accepted: AcceptedCommand) -> CommandAccepted:
    """The result of an acceptance, after checking that the store answered for the project."""
    if accepted.command.id.int == 0 or accepted.command.project_id != project_id:
        raise _InternalFault("acceptance returned a command that is not of the request's project")
    return new_command_accepted(accepted)
