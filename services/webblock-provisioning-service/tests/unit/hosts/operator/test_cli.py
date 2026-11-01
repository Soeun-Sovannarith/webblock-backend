"""The operator CLI (decision 0014 section 21 ruling 11): ``list`` shows the failed and pending
inbox events, ``redrive`` re-drives one and writes one audit event per attempt, whatever its
result. Exit status 0 done, 1 refused or failed, 2 usage or configuration. The store is a fake;
the real one runs on PostgreSQL 18 in ``tests/integration/test_operator_store.py``.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from uuid import UUID

import pytest

from provisioning.core.application import NotFound, SourceEventStatus
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.hosts.operator.cli import EXIT_REFUSED, execute, parse_args
from provisioning.infrastructure.postgres import DatabaseError
from provisioning.infrastructure.postgres.operator_store import (
    InboxRow,
    InboxState,
    NotRedrivable,
    RedriveKind,
    RedriveResult,
)

PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
REQUEST = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e02")
OTHER = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e03")
AT = datetime(2026, 10, 9, 12, tzinfo=UTC)


def row(status: SourceEventStatus, state: InboxState | None, **over: object) -> InboxRow:
    values: dict[str, object] = {
        "project_id": PROJECT,
        "generation_request_id": REQUEST,
        "repository_id": 42,
        "git_commit_sha": "c" * 40,
        "status": status,
        "state": state,
        "attempts": 3,
        "max_attempts": 3,
        "outcome_code": "repository_unconfirmed" if status is SourceEventStatus.FAILED else None,
        "release_id": None,
        "next_attempt_at": AT,
        "received_at": AT,
        "finished_at": AT if status is SourceEventStatus.FAILED else None,
        "last_manual_retry_by": None,
        "last_manual_retry_at": None,
    }
    values.update(over)
    return InboxRow(**values)  # type: ignore[arg-type]  # test values by name


FAILED = row(SourceEventStatus.FAILED, InboxState.FAILED)
REOPENED = row(
    SourceEventStatus.PENDING,
    InboxState.WAITING,
    max_attempts=6,
    outcome_code=None,
    finished_at=None,
    last_manual_retry_by="alice",
    last_manual_retry_at=AT,
)


class Store:
    def __init__(self) -> None:
        self.rows: tuple[InboxRow, ...] = (FAILED,)
        self.fault: Exception | None = None
        self.calls: list[tuple[UUID, UUID, str, int]] = []
        self.limits: list[int] = []
        self.missing: tuple[UUID, ...] = (PROJECT, OTHER)
        self.missing_limits: list[int] = []
        self.set_answer = True
        self.sets: list[tuple[UUID, str, datetime]] = []

    async def missing_subdomains(self, limit: int) -> tuple[UUID, ...]:
        self.missing_limits.append(limit)
        if self.fault is not None:
            raise self.fault
        return self.missing

    async def set_subdomain(self, project_id: UUID, subdomain: str, now: datetime) -> bool:
        self.sets.append((project_id, subdomain, now))
        if self.fault is not None:
            raise self.fault
        return self.set_answer

    async def list_stuck(self, limit: int) -> tuple[InboxRow, ...]:
        self.limits.append(limit)
        return self.rows

    async def redrive(
        self, project_id: UUID, generation_request_id: UUID, *, by: str, extra_attempts: int
    ) -> RedriveResult:
        self.calls.append((project_id, generation_request_id, by, extra_attempts))
        if self.fault is not None:
            raise self.fault
        return RedriveResult(kind=RedriveKind.REOPENED, before=FAILED, after=REOPENED)


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("no alert expected")


class Clock:
    def now(self) -> datetime:
        return AT


async def run(argv: list[str], store: Store | None = None) -> tuple[int, str, str, Audit, Store]:
    store = store or Store()
    audit = Audit()
    out, err = io.StringIO(), io.StringIO()
    code = await execute(
        parse_args(argv), store=store, audit=audit, clock=Clock(), out=out, err=err
    )
    return code, out.getvalue(), err.getvalue(), audit, store


REDRIVE = [
    "redrive",
    "--project",
    str(PROJECT),
    "--generation-request",
    str(REQUEST),
    "--by",
    "alice",
]


@pytest.mark.asyncio
async def test_list_prints_a_header_and_one_line_per_event() -> None:
    code, out, _, audit, store = await run(["list", "--limit", "5"])
    assert code == 0
    header, line = out.splitlines()
    assert header.split("\t")[:3] == ["state", "project_id", "generation_request_id"]
    assert line.split("\t")[:3] == ["failed", str(PROJECT), str(REQUEST)]
    assert "repository_unconfirmed" in line
    assert store.limits == [5]
    assert audit.events == []  # a read is not audited


@pytest.mark.asyncio
async def test_a_redrive_prints_the_new_budget_and_is_audited() -> None:
    code, out, _, audit, store = await run([*REDRIVE, "--extra-attempts", "3"])
    assert code == 0
    assert store.calls == [(PROJECT, REQUEST, "alice", 3)]
    assert out.startswith("reopened ")
    assert "max_attempts=6" in out
    (event,) = audit.events
    assert event == AuditEvent(
        actor_kind=AuditActorKind.OPERATOR,
        actor="alice",
        action=AuditAction.INBOX_REDRIVE,
        project_id=PROJECT,
        release_id=None,
        command_id=None,
        result=AuditResult.SUCCEEDED,
        failure_code=None,
        at=AT,
        correlation_id=f"redrive-{REQUEST}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "result", "code_text"),
    [
        (NotFound("x"), AuditResult.REFUSED, "not_found"),
        (NotRedrivable("x"), AuditResult.REFUSED, "not_redrivable"),
        (DatabaseError("redrive", sqlstate="42501"), AuditResult.REFUSED, "permission_denied"),
        (DatabaseError("redrive", sqlstate="23514"), AuditResult.REFUSED, "guard_refused"),
        (DatabaseError("redrive", sqlstate="08006"), AuditResult.FAILED, "database_error"),
    ],
)
async def test_a_refused_redrive_exits_1_and_is_audited_with_its_code(
    fault: Exception, result: AuditResult, code_text: str
) -> None:
    store = Store()
    store.fault = fault
    code, out, err, audit, _ = await run(REDRIVE, store)
    assert code == EXIT_REFUSED
    assert out == ""
    assert code_text in err
    (event,) = audit.events
    assert (event.result, event.failure_code) == (result, code_text)


# --- the sub-domain backfill (decision 0021 D8; migration 00020 to 00021) -------------------------

SET_SUBDOMAIN = ["subdomain", "set", "--project", str(PROJECT), "--by", "alice"]


def backfill_event(result: AuditResult, code: str | None) -> AuditEvent:
    return AuditEvent(
        actor_kind=AuditActorKind.OPERATOR,
        actor="alice",
        action=AuditAction.SUBDOMAIN_BACKFILL,
        project_id=PROJECT,
        release_id=None,
        command_id=None,
        result=result,
        failure_code=code,
        at=AT,
        correlation_id=f"subdomain-{PROJECT}",
    )


@pytest.mark.asyncio
async def test_subdomain_missing_prints_one_project_per_line() -> None:
    code, out, _, audit, store = await run(["subdomain", "missing", "--limit", "7"])
    assert code == 0
    assert out.splitlines() == ["project_id", str(PROJECT), str(OTHER)]
    assert store.missing_limits == [7]
    assert audit.events == []  # a read is not audited
    _, _, _, _, store = await run(["subdomain", "missing"])
    assert store.missing_limits == [50]


@pytest.mark.asyncio
async def test_subdomain_set_is_audited() -> None:
    code, out, err, audit, store = await run([*SET_SUBDOMAIN, "--subdomain", "shop-1"])
    assert code == 0
    assert err == ""
    assert out == f"subdomain_set project_id={PROJECT}\n"
    assert store.sets == [(PROJECT, "shop-1", AT)]
    assert audit.events == [backfill_event(AuditResult.SUCCEEDED, None)]


@pytest.mark.asyncio
async def test_subdomain_set_refuses_a_row_already_set_or_missing() -> None:
    store = Store()
    store.set_answer = False
    code, out, err, audit, _ = await run([*SET_SUBDOMAIN, "--subdomain", "shop-1"], store)
    assert code == EXIT_REFUSED
    assert out == ""
    assert "already_set_or_missing" in err
    assert audit.events == [backfill_event(AuditResult.REFUSED, "already_set_or_missing")]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["Shop", "-shop", "shop.example", "a" * 64, "", "s\x1bp"])
async def test_subdomain_set_refuses_invalid_value_without_echoing_it(value: str) -> None:
    # The = form: argparse reads a separate value that starts with '-' as an option.
    code, out, err, audit, store = await run([*SET_SUBDOMAIN, f"--subdomain={value}"])
    assert code == EXIT_REFUSED
    assert out == ""
    assert "invalid_subdomain" in err
    if value:
        assert value not in err
    assert store.sets == []  # refused before the store
    assert audit.events == [backfill_event(AuditResult.REFUSED, "invalid_subdomain")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "result", "code_text"),
    [
        (DatabaseError("set", sqlstate="42501"), AuditResult.REFUSED, "permission_denied"),
        (DatabaseError("set", sqlstate="23514"), AuditResult.REFUSED, "guard_refused"),
        (DatabaseError("set", sqlstate="08006"), AuditResult.FAILED, "database_error"),
    ],
)
async def test_a_refused_subdomain_set_exits_1_and_is_audited_with_its_code(
    fault: Exception, result: AuditResult, code_text: str
) -> None:
    store = Store()
    store.fault = fault
    code, _, err, audit, _ = await run([*SET_SUBDOMAIN, "--subdomain", "shop"], store)
    assert code == EXIT_REFUSED
    assert code_text in err
    assert audit.events == [backfill_event(result, code_text)]


@pytest.mark.asyncio
@pytest.mark.parametrize("argv", [["subdomain", "missing"], [*SET_SUBDOMAIN, "--subdomain", "s"]])
async def test_after_00021_the_backfill_commands_say_why_they_are_refused(argv: list[str]) -> None:
    """Migration 00021 takes every backfill grant back, so both commands get SQLSTATE 42501;
    the message says the backfill is over, not only that the login is wrong."""
    store = Store()
    store.fault = DatabaseError("subdomain", sqlstate="42501")
    code, out, err, _, _ = await run(argv, store)
    assert code == EXIT_REFUSED
    assert out == ""
    assert "permission_denied" in err
    assert "after migration 00021" in err


@pytest.mark.asyncio
async def test_an_unexpected_error_of_subdomain_set_is_audited_and_raised() -> None:
    store, audit = Store(), Audit()
    store.fault = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await execute(
            parse_args([*SET_SUBDOMAIN, "--subdomain", "shop"]),
            store=store,
            audit=audit,
            clock=Clock(),
            out=io.StringIO(),
            err=io.StringIO(),
        )
    assert audit.events == [backfill_event(AuditResult.FAILED, "operator_error")]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["subdomain"],
        ["subdomain", "missing", "--limit", "0"],
        ["subdomain", "missing", "--limit", "1001"],
        ["subdomain", "set", "--project", str(PROJECT), "--subdomain", "shop"],
        ["subdomain", "set", "--project", str(PROJECT), "--by", "alice"],
        ["subdomain", "set", "--project", "nope", "--subdomain", "shop", "--by", "alice"],
        ["subdomain", "set", "--project", str(PROJECT), "--subdomain", "shop", "--by", " a"],
        ["redrive", "--project", str(PROJECT), "--generation-request", str(REQUEST)],
        ["redrive", "--project", "nope", "--generation-request", str(REQUEST), "--by", "a"],
        [*REDRIVE[:-1], " padded"],
        [*REDRIVE, "--extra-attempts", "0"],
        [*REDRIVE, "--extra-attempts", "101"],
        ["list", "--limit", "0"],
    ],
)
def test_usage_errors_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_args(argv)
    assert caught.value.code == 2
