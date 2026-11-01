"""The operator CLI's commands (decision 0014 section 21 ruling 11;
.claude/docs/runbooks/operator.md).

    list    [--limit N]     the failed and pending events of the "source published" inbox
    redrive --project P --generation-request G --by NAME [--extra-attempts N]
                            re-drive one event: a failed one goes back to pending with N more
                            attempts (default 3), a pending one is made due now
    subdomain missing [--limit N]
                            the projects whose hosting row has no sub-domain yet (from before
                            migration 00020)
    subdomain set --project P --subdomain S --by NAME
                            set that sub-domain once, from the value Project holds; refused when
                            it is already set, the row is missing or S is not a DNS label

Every re-drive attempt and every ``subdomain set`` attempt, done or refused, is one audit event
(``inbox_redrive`` or ``subdomain_backfill``, actor kind ``operator``, the actor ``--by``). A read
is not audited. Exit status: 0 done, 1 refused or failed (the reason on standard error), 2 a usage
or configuration error. A refused sub-domain is never echoed.

``execute`` takes the store, the audit log and the clock, so tests run it on fakes; ``__main__``
builds the real ones on the ``provisioning_operator`` login.

The ``dlq`` command (task W6-E2) lists and re-drives the broker's dead-letter queues; its
subcommands and their execution are in ``hosts.operator.dlq_cli``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from typing import Final, Protocol, TextIO
from uuid import UUID

from provisioning.core.application import Clock, NotFound
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.domain import InvalidSubdomain, validate_subdomain
from provisioning.hosts.operator.args import bounded, operator_name, project_uuid
from provisioning.hosts.operator.dlq_cli import add_dlq_parser
from provisioning.infrastructure.postgres import DatabaseError
from provisioning.infrastructure.postgres.operator_store import (
    MAX_EXTRA_ATTEMPTS,
    MAX_LIST_LIMIT,
    InboxRow,
    NotRedrivable,
    RedriveResult,
)

EXIT_DONE: Final = 0
EXIT_REFUSED: Final = 1
DEFAULT_EXTRA_ATTEMPTS: Final = 3
DEFAULT_LIST_LIMIT: Final = 50

_HEADER: Final = (
    "state",
    "project_id",
    "generation_request_id",
    "attempts",
    "max_attempts",
    "outcome_code",
    "next_attempt_at",
    "received_at",
    "last_manual_retry_by",
    "last_manual_retry_at",
)


class OperatorStore(Protocol):
    """``infrastructure.postgres.operator_store.PgOperatorStore``."""

    async def list_stuck(self, limit: int) -> tuple[InboxRow, ...]: ...

    async def redrive(
        self, project_id: UUID, generation_request_id: UUID, *, by: str, extra_attempts: int
    ) -> RedriveResult: ...

    async def missing_subdomains(self, limit: int) -> tuple[UUID, ...]: ...

    async def set_subdomain(self, project_id: UUID, subdomain: str, now: datetime) -> bool: ...


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The command line; a usage error exits 2 (``SystemExit``)."""
    parser = argparse.ArgumentParser(
        prog="python -m provisioning.hosts.operator",
        description="Inspect and re-drive Project's source-published inbox and the broker's "
        "dead-letter queues, and fill older sub-domains (operator tool).",
        epilog="list, redrive and subdomain connect as the provisioning_operator login from PG_* "
        "and DB_*; dlq connects to the broker from RABBITMQ_URL. Every re-drive and every "
        "subdomain set is an audit event.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="the failed and pending events")
    listing.add_argument("--limit", type=bounded(1, MAX_LIST_LIMIT), default=DEFAULT_LIST_LIMIT)
    redrive = commands.add_parser("redrive", help="re-drive one event")
    redrive.add_argument("--project", type=project_uuid, required=True)
    redrive.add_argument("--generation-request", type=project_uuid, required=True)
    redrive.add_argument("--by", type=operator_name, required=True, help="the operator's name")
    redrive.add_argument(
        "--extra-attempts",
        type=bounded(1, MAX_EXTRA_ATTEMPTS),
        default=DEFAULT_EXTRA_ATTEMPTS,
        help=f"attempts beyond those used (default {DEFAULT_EXTRA_ATTEMPTS})",
    )
    subdomain = commands.add_parser(
        "subdomain", help="fill the sub-domain of hosting rows from before migration 00020"
    )
    steps = subdomain.add_subparsers(dest="subdomain_command", required=True)
    missing = steps.add_parser("missing", help="the projects with no sub-domain yet")
    missing.add_argument("--limit", type=bounded(1, MAX_LIST_LIMIT), default=DEFAULT_LIST_LIMIT)
    setting = steps.add_parser("set", help="set one project's sub-domain, once")
    setting.add_argument("--project", type=project_uuid, required=True)
    setting.add_argument(
        "--subdomain", required=True, help="the DNS label Project holds for the project"
    )
    setting.add_argument("--by", type=operator_name, required=True, help="the operator's name")
    dlq = commands.add_parser("dlq", help="the broker's dead-letter queues (list, redrive)")
    add_dlq_parser(dlq)
    return parser.parse_args(argv)


def _text(value: str | int | UUID | datetime | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _line(row: InboxRow) -> str:
    return "\t".join(
        _text(v)
        for v in (
            row.state.value if row.state is not None else row.status.value,
            row.project_id,
            row.generation_request_id,
            row.attempts,
            row.max_attempts,
            row.outcome_code,
            row.next_attempt_at,
            row.received_at,
            row.last_manual_retry_by,
            row.last_manual_retry_at,
        )
    )


def _refusal(exc: Exception, what: str = "re-drive") -> tuple[AuditResult, str, str] | None:
    """(audit result, failure code, message) of a refused or failed ``what``, or None for an
    error that is not the store's answer."""
    if isinstance(exc, NotFound):
        return AuditResult.REFUSED, "not_found", "no such inbox event"
    if isinstance(exc, NotRedrivable):
        return AuditResult.REFUSED, "not_redrivable", "the event is accepted or rejected (final)"
    if isinstance(exc, DatabaseError):
        if exc.sqlstate == "42501":
            return (
                AuditResult.REFUSED,
                "permission_denied",
                f"this login may not {what} (SQLSTATE 42501): use the provisioning_operator login",
            )
        if exc.sqlstate == "23514":
            return (
                AuditResult.REFUSED,
                "guard_refused",
                f"a database guard refused the {what} (SQLSTATE 23514)",
            )
        return AuditResult.FAILED, "database_error", f"database error (SQLSTATE {exc.sqlstate})"
    return None


async def execute(
    args: argparse.Namespace,
    *,
    store: OperatorStore,
    audit: AuditLog,
    clock: Clock,
    out: TextIO,
    err: TextIO,
) -> int:
    """Run one parsed command; returns the exit status."""
    if args.command == "subdomain":
        return await _subdomain(args, store=store, audit=audit, clock=clock, out=out, err=err)
    if args.command == "list":
        rows = await store.list_stuck(args.limit)
        out.write("\t".join(_HEADER) + "\n")
        for row in rows:
            out.write(_line(row) + "\n")
        return EXIT_DONE
    project: UUID = args.project
    request: UUID = args.generation_request

    record = _operator_audit(
        audit,
        clock,
        by=args.by,
        action=AuditAction.INBOX_REDRIVE,
        project=project,
        correlation_id=f"redrive-{request}",
    )

    try:
        done = await store.redrive(project, request, by=args.by, extra_attempts=args.extra_attempts)
    except Exception as exc:
        refusal = _refusal(exc)
        if refusal is None:
            record(AuditResult.FAILED, "operator_error")
            raise
        result, code, message = refusal
        record(result, code)
        err.write(f"re-drive {code}: {message}\n")
        return EXIT_REFUSED
    record(AuditResult.SUCCEEDED, None)
    after = done.after
    out.write(
        f"{done.kind.value} project_id={after.project_id} "
        f"generation_request_id={after.generation_request_id} status={after.status.value} "
        f"attempts={after.attempts} max_attempts={after.max_attempts} "
        f"next_attempt_at={_text(after.next_attempt_at)}\n"
    )
    return EXIT_DONE


def _operator_audit(
    audit: AuditLog,
    clock: Clock,
    *,
    by: str,
    action: AuditAction,
    project: UUID,
    correlation_id: str,
) -> Callable[[AuditResult, str | None], None]:
    """Records the operator's audit event of ``action`` on ``project`` with a result and an
    optional failure code, timed when it is recorded."""

    def record(outcome: AuditResult, failure: str | None) -> None:
        audit.record(
            AuditEvent(
                actor_kind=AuditActorKind.OPERATOR,
                actor=by,
                action=action,
                project_id=project,
                release_id=None,
                command_id=None,
                result=outcome,
                failure_code=failure,
                at=clock.now(),
                correlation_id=correlation_id,
            )
        )

    return record


_BACKFILL_OVER: Final = (
    "refused (SQLSTATE 42501): the backfill runs only between migrations 00020 and 00021, as the "
    "provisioning_operator login; after migration 00021 every sub-domain is set and the operator "
    "holds no grant for it"
)


async def _subdomain(
    args: argparse.Namespace,
    *,
    store: OperatorStore,
    audit: AuditLog,
    clock: Clock,
    out: TextIO,
    err: TextIO,
) -> int:
    """``subdomain missing`` and ``subdomain set`` (decision 0021 D8)."""
    if args.subdomain_command == "missing":
        try:
            projects = await store.missing_subdomains(args.limit)
        except DatabaseError as exc:
            if exc.sqlstate != "42501":
                raise
            err.write(f"subdomain missing permission_denied: {_BACKFILL_OVER}\n")
            return EXIT_REFUSED
        out.write("project_id\n")
        for project_id in projects:
            out.write(f"{project_id}\n")
        return EXIT_DONE
    project: UUID = args.project
    value: str = args.subdomain

    record = _operator_audit(
        audit,
        clock,
        by=args.by,
        action=AuditAction.SUBDOMAIN_BACKFILL,
        project=project,
        correlation_id=f"subdomain-{project}",
    )

    def refuse(outcome: AuditResult, failure: str, reason: str) -> int:
        record(outcome, failure)
        err.write(f"subdomain set {failure}: {reason}\n")
        return EXIT_REFUSED

    try:
        validate_subdomain(value)
    except InvalidSubdomain:
        return refuse(
            AuditResult.REFUSED,
            "invalid_subdomain",
            "expected 1 to 63 lower-case letters, digits or '-', not starting or ending with '-'",
        )
    try:
        done = await store.set_subdomain(project, value, clock.now())
    except Exception as exc:
        refusal = _refusal(exc, "set a sub-domain")
        if refusal is None:
            record(AuditResult.FAILED, "operator_error")
            raise
        result, code, message = refusal
        if code == "permission_denied":
            message = _BACKFILL_OVER
        return refuse(result, code, message)
    if not done:
        return refuse(
            AuditResult.REFUSED,
            "already_set_or_missing",
            "the project has no hosting row, or its sub-domain is already set (it is set once)",
        )
    record(AuditResult.SUCCEEDED, None)
    out.write(f"subdomain_set project_id={project}\n")
    return EXIT_DONE
