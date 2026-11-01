"""The release intake use case (decision 0008 sections 1 to 3), extended by
decision 0014: a row accepted with its release starts the release's build (``BuildStarter``).

Audit (decision 0014 section 21 answer 3): each "source published" event the batch settles is one
``release_accepted`` line, actor ``project`` of kind ``event``: accepted with the release, refused
with the rejection code, or failed ``repository_unconfirmed``. A retried or superseded row is not
settled and records nothing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from provisioning.core.application.intake.ports import (
    MAX_INT32,
    InboxEntry,
    InboxTake,
    ReleaseAcceptanceDecider,
    ReleaseAcceptanceDecision,
    ReleaseAcceptanceFacts,
    ReleaseAcceptanceParams,
    ReleaseIntakeStore,
    SourceEventStatus,
    TakeInboxParams,
    accept_existing_release,
    accept_new_release,
    reject_release,
)
from provisioning.core.application.operations.ports import BuildStarter
from provisioning.core.application.operations.views import BuildStarted
from provisioning.core.application.ports.dependencies import IdSource, RepositoryRegistry
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.application.shared.bounded import for_each_bounded
from provisioning.core.application.shared.errors import (
    BuildInProgress,
    CorruptRow,
    DependencyNotConfigured,
    ReleaseNotBuildable,
    SubdomainTaken,
    Timeout,
)
from provisioning.core.domain import (
    CODE_RELEASE_CONFLICT,
    CODE_REPOSITORY_MISMATCH,
    CODE_REPOSITORY_NOT_REGISTERED,
    CODE_REPOSITORY_UNCONFIRMED,
    DomainError,
    IdempotencyConflict,
    SourcePublished,
    is_error,
    parse_repository_id,
    validate_subdomain,
)

# Bounds the inbox batch size, and with it the registry calls and row transactions of one batch.
MAX_INBOX_BATCH_SIZE = 1000

# Why a registry answer was not usable; none carries the adapter's error text.
REGISTRY_REASON_UNAVAILABLE = "registry_unavailable"
REGISTRY_REASON_TIMEOUT = "registry_timeout"
REGISTRY_REASON_INVALID_ANSWER = "registry_invalid_answer"
# Why a row failed repository_unconfirmed although the registry answered: the sub-domain it gave
# belongs to another project's hosting row (FR-54, TR-53; Task 12 security re-review L2).
INTAKE_REASON_SUBDOMAIN_TAKEN = "subdomain_taken"


def inbox_visibility_floor(
    batch: int, concurrency: int, registry_timeout: timedelta, transaction_timeout: timedelta
) -> timedelta | None:
    """(ceil(batch / concurrency) + 1) x (registry_timeout + transaction_timeout), or None when an
    argument is not positive. The build start of each accepted row runs within the row's
    transaction round's budget only if the caller includes it; see ``InboxBatchParams``."""
    if (
        batch < 1
        or concurrency < 1
        or registry_timeout <= timedelta(0)
        or transaction_timeout <= timedelta(0)
    ):
        return None
    rounds = -(-batch // concurrency) + 1
    try:
        return rounds * (registry_timeout + transaction_timeout)
    except OverflowError:
        return None


@dataclass(frozen=True, slots=True)
class InboxBatchParams:
    """The processor's configuration for one batch; every value required."""

    take: TakeInboxParams
    concurrency: int
    registry_timeout: timedelta
    transaction_timeout: timedelta
    max_attempts: int
    deadline: timedelta
    retry_delay: Callable[[int], timedelta]

    def validate(self) -> None:
        """Refuse a missing or out-of-range value and a visibility timeout that does not exceed
        ``inbox_visibility_floor`` (``ValueError``)."""
        self.take.validate()
        if self.take.batch_size > MAX_INBOX_BATCH_SIZE:
            raise ValueError(f"inbox batch: batch size must be at most {MAX_INBOX_BATCH_SIZE}")
        if not 1 <= self.concurrency <= self.take.batch_size:
            raise ValueError("inbox batch: concurrency must be 1 to the batch size")
        if self.registry_timeout <= timedelta(0) or self.transaction_timeout <= timedelta(0):
            raise ValueError("inbox batch: registry and transaction timeouts must be positive")
        if not 1 <= self.max_attempts <= MAX_INT32:
            raise ValueError("inbox batch: the attempt budget must be 1 to the INTEGER maximum")
        if self.deadline <= timedelta(0):
            raise ValueError("inbox batch: the retry deadline must be positive")
        floor = inbox_visibility_floor(
            self.take.batch_size, self.concurrency, self.registry_timeout, self.transaction_timeout
        )
        if floor is None:
            raise ValueError(
                "inbox batch: (ceil(batch / concurrency) + 1) x (registry timeout + "
                "transaction timeout) overflows"
            )
        if self.take.visibility <= floor:
            raise ValueError(
                "inbox batch: visibility timeout must exceed (ceil(batch / concurrency) + 1) x "
                f"(registry timeout + transaction timeout) = {floor}"
            )


class InboxRowOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    RETRIED = "retried"
    SUPERSEDED = "superseded"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class InboxRowResult:
    """One taken row. Never carries the registered repository ID or the registry's error text.
    ``build`` is the build start of an accepted row, ``build_error`` its failure. The row stays
    accepted either way, and the release keeps a pending or running build attempt: the build
    worker's loop starts a pending one after BUILD_PENDING_GRACE, and dispatches a running one
    without a handle after BUILD_DISPATCH_GRACE (QA-10, OD-51)."""

    take: InboxTake
    outcome: InboxRowOutcome
    code: str = ""
    reason: str = ""
    release_id: UUID | None = None
    created: bool = False
    retry_after: timedelta | None = None
    error: Exception | None = None
    build: BuildStarted | None = None
    build_error: Exception | None = None


@dataclass(frozen=True, slots=True)
class InboxBatchResult:
    now: datetime
    rows: tuple[InboxRowResult, ...]


class ReleaseIntake:
    """``process_batch`` confirms the repository of a batch of due rows and accepts, rejects,
    retries or fails each one, then starts the build of each accepted release. Storing the event
    in the inbox is ``ReceiveSourcePublishedCommand`` (intake/commands/)."""

    __slots__ = ("_audit", "_builds", "_ids", "_registry", "_store")

    def __init__(
        self,
        *,
        store: ReleaseIntakeStore,
        registry: RepositoryRegistry,
        ids: IdSource,
        builds: BuildStarter,
        audit: AuditLog,
    ) -> None:
        self._store = store
        self._registry = registry
        self._ids = ids
        self._builds = builds
        self._audit = audit

    async def process_batch(self, params: InboxBatchParams) -> InboxBatchResult:
        """One batch (decision 0008 sections 2 and 3):

        1. take up to ``take.batch_size`` due rows, bounded by ``transaction_timeout``;
        2. outside any transaction, ask the registry once per project, at most ``concurrency``
           at a time, each bounded by ``registry_timeout``; ``DependencyNotConfigured`` cancels
           the calls in flight and is raised without writing any row;
        3. per row, at most ``concurrency`` at a time, one store call bounded by
           ``transaction_timeout``: accept, reject repository_mismatch or
           repository_not_registered, or retry / fail repository_unconfirmed;
        4. per accepted row, start the release's build (``BuildStarter.start``);
           ``BuildInProgress`` and ``ReleaseNotBuildable`` are not errors (the build is already
           running or done).

        Rows are returned in the take's order; a row's own failure is in its result."""
        params.validate()
        try:
            async with asyncio.timeout(params.transaction_timeout.total_seconds()):
                batch = await self._store.take_due(params.take)
        except TimeoutError:
            raise Timeout("take due inbox rows") from None
        if not batch.entries:
            return InboxBatchResult(now=batch.now, rows=())
        answers = await _ask_registry(self, batch.entries, params)
        entries = batch.entries
        rows: list[InboxRowResult | None] = [None] * len(entries)

        async def process(index: int) -> None:
            entry = entries[index]
            rows[index] = await _process_row(
                self, entry, answers[entry.event.project_id], batch.now, params
            )

        await for_each_bounded(len(entries), params.concurrency, process)
        handled = [row for row in rows if row is not None]
        for row in handled:
            _record_settled(self._audit, row, batch.now)

        async def build(index: int) -> None:
            row = handled[index]
            if row.outcome == InboxRowOutcome.ACCEPTED and row.release_id is not None:
                handled[index] = await _start_build(self, row, row.release_id)

        await for_each_bounded(len(handled), params.concurrency, build)
        return InboxBatchResult(now=batch.now, rows=tuple(handled))


_SETTLED_RESULT = {
    InboxRowOutcome.ACCEPTED: AuditResult.ACCEPTED,
    InboxRowOutcome.REJECTED: AuditResult.REFUSED,
    InboxRowOutcome.FAILED: AuditResult.FAILED,
}


def _record_settled(audit: AuditLog, row: InboxRowResult, at: datetime) -> None:
    result = _SETTLED_RESULT.get(row.outcome)
    if result is None:
        return
    audit.record(
        AuditEvent(
            actor_kind=AuditActorKind.EVENT,
            actor="project",
            action=AuditAction.RELEASE_ACCEPTED,
            project_id=row.take.project_id,
            release_id=row.release_id,
            command_id=None,
            result=result,
            failure_code=row.code or None,
            at=at,
            correlation_id=None,
        )
    )


@dataclass(frozen=True, slots=True)
class _RegistryAnswer:
    """The registry's answer for one project of a batch: the registered ID and sub-domain, or a
    reason when it is not usable (transient)."""

    repository_id: int | None = None
    subdomain: str = ""
    reason: str = ""


class _RegistryNotConfigured(Exception):
    """Raised inside the registry fan-out so that the task group cancels the other calls."""


async def _ask(registry: RepositoryRegistry, project_id: UUID, limit: timedelta) -> _RegistryAnswer:
    """One registry call bounded by ``limit``; the adapter's error text is dropped here, so it
    reaches no log."""
    try:
        async with asyncio.timeout(limit.total_seconds()):
            registered = await registry.registered_repository(project_id)
    except DependencyNotConfigured:
        raise _RegistryNotConfigured from None
    except TimeoutError:
        return _RegistryAnswer(reason=REGISTRY_REASON_TIMEOUT)
    except Exception:  # any other failure means the answer is unknown: transient
        return _RegistryAnswer(reason=REGISTRY_REASON_UNAVAILABLE)
    if registered is None:
        return _RegistryAnswer()
    try:
        parse_repository_id(registered.repository_id)
        validate_subdomain(registered.subdomain)
    except DomainError:  # an ID outside the range or a sub-domain that is no DNS label: not usable
        return _RegistryAnswer(reason=REGISTRY_REASON_INVALID_ANSWER)
    return _RegistryAnswer(repository_id=registered.repository_id, subdomain=registered.subdomain)


def _acceptance_decider(confirmed: SourcePublished, subdomain: str) -> ReleaseAcceptanceDecider:
    """A new release (with the registry's sub-domain for a new hosting row) when none is stored
    under the key, the stored one when it holds the event's source, a rejection release_conflict
    when it holds another. Facts for another event are ``CorruptRow``: the locked row's event
    columns never change."""

    def decide(facts: ReleaseAcceptanceFacts) -> ReleaseAcceptanceDecision:
        if facts.entry.event != confirmed:
            raise CorruptRow(
                f"the locked inbox row of project {confirmed.project_id} is not the confirmed event"
            )
        if facts.existing is None:
            return accept_new_release(facts, subdomain=subdomain)
        try:
            return accept_existing_release(facts)
        except IdempotencyConflict:
            return reject_release(CODE_RELEASE_CONFLICT)

    return decide


def _conditional(take: InboxTake, applied: bool, ok: InboxRowResult) -> InboxRowResult:
    if not applied:
        return InboxRowResult(take=take, outcome=InboxRowOutcome.SUPERSEDED)
    return ok


async def _ask_registry(
    self: ReleaseIntake, entries: tuple[InboxEntry, ...], params: InboxBatchParams
) -> dict[UUID, _RegistryAnswer]:
    projects = list(dict.fromkeys(e.event.project_id for e in entries))
    answers: dict[UUID, _RegistryAnswer] = {}

    async def ask(index: int) -> None:
        project_id = projects[index]
        answers[project_id] = await _ask(self._registry, project_id, params.registry_timeout)

    try:
        await for_each_bounded(len(projects), params.concurrency, ask)
    except* _RegistryNotConfigured:
        raise DependencyNotConfigured(
            "repository registry (OD-01); the batch's rows stay pending"
        ) from None
    return answers


async def _process_row(
    self: ReleaseIntake,
    entry: InboxEntry,
    answer: _RegistryAnswer,
    now: datetime,
    params: InboxBatchParams,
) -> InboxRowResult:
    take = entry.take()
    try:
        async with asyncio.timeout(params.transaction_timeout.total_seconds()):
            if answer.reason:
                return await _retry_or_fail(self, entry, answer.reason, now, params)
            if answer.repository_id == entry.event.repository_id:
                try:
                    return await _accept(self, entry, answer.subdomain)
                except SubdomainTaken as exc:
                    return await _subdomain_taken(self, entry, exc)
            code = (
                CODE_REPOSITORY_MISMATCH
                if answer.repository_id is not None
                else CODE_REPOSITORY_NOT_REGISTERED
            )
            applied = await self._store.reject(take, code)
            return _conditional(
                take,
                applied,
                InboxRowResult(take=take, outcome=InboxRowOutcome.REJECTED, code=code),
            )
    except Exception as exc:  # a row's own failure is its result, never the batch's
        return InboxRowResult(take=take, outcome=InboxRowOutcome.ERROR, error=exc)


async def _accept(self: ReleaseIntake, entry: InboxEntry, subdomain: str) -> InboxRowResult:
    take = entry.take()
    release_id = self._ids.new_id()
    step_id = self._ids.new_id()
    result = await self._store.accept_release(
        ReleaseAcceptanceParams(take=take, release_id=release_id, step_id=step_id),
        _acceptance_decider(entry.event, subdomain),
    )
    if result.superseded:
        return InboxRowResult(take=take, outcome=InboxRowOutcome.SUPERSEDED)
    if result.status == SourceEventStatus.ACCEPTED and result.release_id is not None:
        return InboxRowResult(
            take=take,
            outcome=InboxRowOutcome.ACCEPTED,
            release_id=result.release_id,
            created=result.created,
        )
    if result.status == SourceEventStatus.REJECTED:
        return InboxRowResult(take=take, outcome=InboxRowOutcome.REJECTED, code=result.outcome_code)
    return InboxRowResult(
        take=take,
        outcome=InboxRowOutcome.ERROR,
        error=CorruptRow(
            f"release acceptance of project {take.project_id} returned inbox status {result.status}"
        ),
    )


async def _subdomain_taken(
    self: ReleaseIntake, entry: InboxEntry, exc: SubdomainTaken
) -> InboxRowResult:
    """The registry's sub-domain belongs to another project (Task 12 security re-review L2). The
    condition is permanent, so it must not retry forever: within the row's attempt budget it is
    an error row (taken again after the visibility timeout, the same-project race being already
    handled by the store); once ``attempts`` reach the row's ``max_attempts`` the row fails with
    the existing code ``repository_unconfirmed`` and the reason ``subdomain_taken``, which the
    processor logs at error. A named failure code is the owner's catalogue decision, and no new
    code may be added (FR-54). The PostgreSQL store's login cannot read ``subdomain`` (TR-53), so
    it never raises this today; the bootstrap refuses the tenant instead. A store whose login may
    read it raises it here."""
    take = entry.take()
    if entry.attempts < entry.max_attempts:
        return InboxRowResult(take=take, outcome=InboxRowOutcome.ERROR, error=exc)
    applied = await self._store.fail(take, CODE_REPOSITORY_UNCONFIRMED)
    return _conditional(
        take,
        applied,
        InboxRowResult(
            take=take,
            outcome=InboxRowOutcome.FAILED,
            code=CODE_REPOSITORY_UNCONFIRMED,
            reason=INTAKE_REASON_SUBDOMAIN_TAKEN,
            error=exc,
        ),
    )


async def _retry_or_fail(
    self: ReleaseIntake,
    entry: InboxEntry,
    reason: str,
    now: datetime,
    params: InboxBatchParams,
) -> InboxRowResult:
    """Fail the row repository_unconfirmed when its budget is spent (attempts at the row's own
    max_attempts, or the deadline passed since its last manual retry, else since received_at),
    otherwise schedule the next attempt."""
    take = entry.take()
    since = entry.last_manual_retry_at or entry.received_at
    if entry.attempts >= entry.max_attempts or not now < since + params.deadline:
        applied = await self._store.fail(take, CODE_REPOSITORY_UNCONFIRMED)
        return _conditional(
            take,
            applied,
            InboxRowResult(
                take=take,
                outcome=InboxRowOutcome.FAILED,
                code=CODE_REPOSITORY_UNCONFIRMED,
                reason=reason,
            ),
        )
    delay = max(params.retry_delay(entry.attempts), timedelta(microseconds=1))
    applied = await self._store.backoff(take, delay)
    return _conditional(
        take,
        applied,
        InboxRowResult(
            take=take, outcome=InboxRowOutcome.RETRIED, reason=reason, retry_after=delay
        ),
    )


async def _start_build(
    self: ReleaseIntake, row: InboxRowResult, release_id: UUID
) -> InboxRowResult:
    """Start the build of an accepted row's release (decision 0014). A build already running or
    past building is not an error; any other failure is reported in the row."""
    try:
        started = await self._builds.start(row.take.project_id, release_id)
    except Exception as exc:  # the row stays accepted; the build worker's loop starts the attempt
        if is_error(exc, BuildInProgress) or is_error(exc, ReleaseNotBuildable):
            return row
        return replace(row, build_error=exc)
    return replace(row, build=started)
