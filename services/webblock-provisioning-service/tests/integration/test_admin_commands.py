"""The admin operations on PostgreSQL 18 (decision 0023 G9 to G11, G13): the handlers the
transports send to, on the stores of a login that is a member of ``provisioning_api`` alone, so the
grants and the insert guard of migration 00020 (``commands_insert_shape``) are what is tested.

An admin's suspend or resume is stored under ``admin:<key>`` with reason ``admin_suspended`` and
the admin's subject; a redeploy names the live release and needs hosting online and routing
stable; the admin read shows the requester and the client read never does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration._pg import ROLE_API, ROLE_HOSTING_INTAKE, Database
from tests.integration.test_store_support import (
    COMMANDS,
    HOSTING,
    RELEASES,
    TIMEOUTS,
    at,
    command_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
    subdomain_for,
)

from provisioning.core.application import (
    ADMIN_ROLE,
    Action,
    AdminListCommandsQuery,
    BuildStarted,
    CommandBus,
    CommandView,
    EnabledOperations,
    ListCommandsQuery,
    OperationDeps,
    OperationInProgress,
    Principal,
    QueryBus,
    RedeployNotAllowed,
    RedeployReleaseCommand,
    ResumeWebsiteCommand,
    SuspendWebsiteCommand,
    register_operations,
)
from provisioning.core.application.hosting.ports import OwnerStatus, OwnerStatusOutcome
from provisioning.core.domain import (
    CommandKind,
    IdempotencyConflict,
    RouteNotStable,
)
from provisioning.infrastructure.postgres import (
    HostingIntakeStore,
    InvalidArgument,
    PgAcceptanceStore,
    PgReadStore,
    insert_dispatch,
)
from provisioning.infrastructure.postgres.hosting_intake import PgHostingHistory

pytestmark = pytest.mark.asyncio

ADMIN = Principal(subject="admin-1", scopes=(ADMIN_ROLE,))
USER = Principal(subject="user-1", scopes=("USER",))


class _Allow:
    """Permits every action and every project: the gate's admin-only rule still applies."""

    def permit(self, principal: Principal, action: Action) -> None:
        del principal, action

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        del principal, project_id, action


class _NoEntitlement:
    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        raise AssertionError("an admin operation never asks entitlement")


class _NoBuild:
    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        raise AssertionError("no build")


class _Clock:
    def now(self) -> datetime:
        return at(100)


class _Ids:
    def new_id(self) -> UUID:
        return uuid4()


@asynccontextmanager
async def api_buses(db: Database) -> AsyncIterator[tuple[CommandBus, QueryBus]]:
    """The operations' handlers on the API login, registered on their buses as the API host
    wires them (``hosts.api.wiring``)."""
    async with role_engine(db, ROLE_API) as engine:
        acceptance = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        reads = PgReadStore(engine, TIMEOUTS)
        allow = _Allow()
        commands, queries = CommandBus(), QueryBus()
        register_operations(
            commands,
            queries,
            OperationDeps(
                policy=allow,
                access=allow,
                entitlement=_NoEntitlement(),
                acceptance=acceptance,
                reads=reads,
                clock=_Clock(),
                ids=_Ids(),
                builds=_NoBuild(),
                enabled=EnabledOperations(tuple(Action)),
                command_deadline=timedelta(minutes=15),
                history=reads,
                redeploys=acceptance,
            ),
        )
        yield commands, queries


def live_project(
    db: Database, *, routing: str = "stable", hosting: str = "online", reasons: str | None = None
) -> tuple[UUID, UUID]:
    """A project with a live release, at command number 3."""
    project, live = uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "live", release_id=live)),
        (
            HOSTING,
            hosting_row(
                project, number=3, live=live, routing=routing, hosting=hosting, reasons=reasons
            ),
        ),
    )
    return project, live


def finish(db: Database, command_id: UUID) -> None:
    """End a command (as the worker would), so the project has none in flight."""
    with db.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE provisioning.commands SET status = 'succeeded' WHERE command_id = %s",
            (command_id,),
        )


def deploy_row(project: UUID, live: UUID, *, key: str | None = None) -> dict[str, object]:
    """The finished deploy at command number 3 that made ``live`` live."""
    return command_row(project, CommandKind.DEPLOY, 3, "succeeded", release=live, key=key)


def stored(db: Database, command_id: UUID) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT kind::text, idempotency_key, hosting_reason::text, requested_by_subject,"
        " release_id, status::text, command_number FROM provisioning.commands"
        " WHERE command_id = %s",
        command_id,
    )


def count(db: Database, query: str, *params: object) -> int:
    value = scalar(db, query, *params)
    assert isinstance(value, int)
    return value


async def test_admin_suspend_stores_admin_key_reason_and_subject(db: Database) -> None:
    """FR-33.1, FR-34: admin suspend stores admin key reason and subject."""
    project, _ = live_project(db)
    suspended, _ = live_project(db, hosting="offline", reasons="{admin_suspended}")
    online, _ = live_project(db)
    async with api_buses(db) as (commands, _queries):
        suspend = await commands.send(
            SuspendWebsiteCommand(ADMIN, project, "k-1", correlation_id="corr-a1")
        )
        resume = await commands.send(ResumeWebsiteCommand(ADMIN, suspended, "k-2"))
        # 0021 D5: a resume when admin_suspended is absent is accepted; the worker skips it.
        absent = await commands.send(ResumeWebsiteCommand(ADMIN, online, "k-3"))
    assert stored(db, suspend.command_id) == (
        "suspend_hosting",
        "admin:k-1",
        "admin_suspended",
        "admin-1",
        None,
        "accepted",
        4,
    )
    assert stored(db, resume.command_id)[:4] == (
        "resume_hosting",
        "admin:k-2",
        "admin_suspended",
        "admin-1",
    )
    assert stored(db, absent.command_id)[:3] == ("resume_hosting", "admin:k-3", "admin_suspended")
    assert row_of(
        db,
        "SELECT correlation_id, deadline_at FROM provisioning_ops.command_dispatch"
        " WHERE command_id = %s",
        suspend.command_id,
    ) == ("corr-a1", at(100) + timedelta(minutes=15))
    assert (
        count(
            db,
            "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s",
            project,
        )
        == 4
    )


async def test_admin_suspend_of_a_site_offline_for_another_reason_is_accepted(
    db: Database,
) -> None:
    """0021 D7: the worker records the reason; the API accepts the command."""
    project, _ = live_project(db, hosting="offline", reasons="{billing}")
    async with api_buses(db) as (commands, _queries):
        accepted = await commands.send(SuspendWebsiteCommand(ADMIN, project, "k-1"))
    assert stored(db, accepted.command_id)[:3] == (
        "suspend_hosting",
        "admin:k-1",
        "admin_suspended",
    )


@pytest.mark.parametrize("key", ["project-deleted", "owner-status:3"])
async def test_admin_key_never_takes_a_system_key(db: Database, key: str) -> None:
    """Review Focus 2: the admin's key is stored as ``admin:<key>``, a separate key from the
    system's, and the system's command still lands."""
    project, _ = live_project(db)
    async with api_buses(db) as (commands, _queries):
        admin = await commands.send(SuspendWebsiteCommand(ADMIN, project, key))
    assert stored(db, admin.command_id)[:4] == (
        "suspend_hosting",
        f"admin:{key}",
        "admin_suspended",
        "admin-1",
    )
    finish(db, admin.command_id)
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        intake = HostingIntakeStore(engine, TIMEOUTS)
        if key == "project-deleted":
            system = await intake.accept_hosting_command(
                project_id=project,
                kind=CommandKind.SUSPEND_HOSTING,
                event_key=key,
                command_id=uuid4(),
                now=at(110),
                correlation_id="corr-system",
                deadline_at=at(110) + timedelta(minutes=15),
            )
        else:
            result = await intake.accept_owner_status(
                project_id=project,
                status=OwnerStatus.SUSPENDED,
                version=3,
                subdomain=subdomain_for(project),
                command_id=uuid4(),
                now=at(110),
                correlation_id="corr-system",
                deadline_at=at(110) + timedelta(minutes=15),
            )
            assert result.outcome is OwnerStatusOutcome.ACCEPTED
            assert result.accepted is not None
            system = result.accepted
        # The hosting intake keeps refusing an event key that starts with admin:.
        with pytest.raises(InvalidArgument):
            await intake.accept_hosting_command(
                project_id=project,
                kind=CommandKind.SUSPEND_HOSTING,
                event_key=f"admin:{key}",
                command_id=uuid4(),
                now=at(120),
                correlation_id="corr-forged",
                deadline_at=at(120) + timedelta(minutes=15),
            )
    assert not system.replayed
    reason = "project_deleted" if key == "project-deleted" else "user_suspended"
    assert stored(db, system.command.id)[:4] == ("suspend_hosting", key, reason, None)
    assert (
        count(db, "SELECT count(*) FROM provisioning.commands WHERE project_id = %s", project) == 2
    )


@pytest.mark.parametrize(
    ("routing", "hosting", "reasons", "live", "error"),
    [
        pytest.param("stable", "online", None, False, RedeployNotAllowed, id="no-live-release"),
        pytest.param("stable", "offline", "{billing}", True, RedeployNotAllowed, id="offline"),
        pytest.param(
            "stable", "offline", "{admin_suspended}", True, RedeployNotAllowed, id="admin-offline"
        ),
        pytest.param("changing", "online", None, True, RouteNotStable, id="route-changing"),
        pytest.param("uncertain", "online", None, True, RouteNotStable, id="route-uncertain"),
    ],
)
async def test_redeploy_needs_a_live_release_and_online(
    db: Database,
    routing: str,
    hosting: str,
    reasons: str | None,
    live: bool,
    error: type[Exception],
) -> None:
    """FR-35.2: redeploy needs a live release and online."""
    if live:
        project, _ = live_project(db, routing=routing, hosting=hosting, reasons=reasons)
    else:
        project = uuid4()
        seed_rows(db, (HOSTING, hosting_row(project, number=3, routing=routing, hosting=hosting)))
    async with api_buses(db) as (commands, _queries):
        with pytest.raises(error):
            await commands.send(RedeployReleaseCommand(ADMIN, project, "r-1"))
    assert count(db, "SELECT count(*) FROM provisioning.commands") == 0


async def test_redeploy_without_a_hosting_row_or_with_a_command_in_flight_is_refused(
    db: Database,
) -> None:
    project, live = live_project(db)
    seed_rows(
        db,
        (COMMANDS, command_row(project, CommandKind.DEPLOY, 3, "running", release=live)),
    )
    async with api_buses(db) as (commands, _queries):
        with pytest.raises(RedeployNotAllowed):
            await commands.send(RedeployReleaseCommand(ADMIN, uuid4(), "r-1"))
        with pytest.raises(OperationInProgress):
            await commands.send(RedeployReleaseCommand(ADMIN, project, "r-2"))
    assert count(db, "SELECT count(*) FROM provisioning.commands") == 1


async def test_redeploy_names_the_live_release(db: Database) -> None:
    project, live = live_project(db)
    async with api_buses(db) as (commands, _queries):
        accepted = await commands.send(
            RedeployReleaseCommand(ADMIN, project, "r-1", correlation_id="corr-r")
        )
    assert stored(db, accepted.command_id) == (
        "redeploy",
        "r-1",
        None,
        "admin-1",
        live,
        "accepted",
        4,
    )
    assert row_of(
        db,
        "SELECT correlation_id FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        accepted.command_id,
    ) == ("corr-r",)
    # Acceptance touches neither the release nor the usage outbox (0023 G12).
    assert (
        scalar(db, "SELECT status::text FROM provisioning.releases WHERE release_id = %s", live)
        == "live"
    )
    assert count(db, "SELECT count(*) FROM provisioning_ops.usage_outbox") == 0


async def test_admin_read_returns_the_subject_and_client_read_never_does(db: Database) -> None:
    """FR-36.1, FR-39.1: admin read returns the subject and client read never does."""
    project, live = live_project(db)
    seed_rows(db, (COMMANDS, deploy_row(project, live)))
    async with api_buses(db) as (commands, queries):
        accepted = await commands.send(SuspendWebsiteCommand(ADMIN, project, "k-1"))
        admin_page = await queries.send(AdminListCommandsQuery(ADMIN, project, 1, None))
        rest = await queries.send(AdminListCommandsQuery(ADMIN, project, 1, admin_page.next_after))
        client_page = await queries.send(
            ListCommandsQuery(USER, project_id=project, page_size=20, after=None)
        )
    assert [(v.command_id, v.requested_by_subject) for v in admin_page.items] == [
        (accepted.command_id, "admin-1")
    ]
    assert admin_page.next_after == 4
    assert [(v.command_number, v.requested_by_subject) for v in rest.items] == [(3, "subject-1")]
    assert rest.next_after is None
    assert [v.command_number for v in client_page.items] == [4, 3]
    for view in client_page.items:
        assert type(view) is CommandView
        assert not hasattr(view, "requested_by_subject")


def go_live(db: Database, project: UUID, *, old: UUID, new: UUID) -> None:
    """``new`` replaces ``old`` as the live release (as a promotion would)."""
    with db.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE provisioning.releases SET status = 'superseded' WHERE release_id = %s", (old,)
        )
    seed_rows(db, (RELEASES, release_row(project, "live", release_id=new, name="n")))
    with db.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE provisioning.project_hosting SET live_release_id = %s WHERE project_id = %s",
            (new, project),
        )


async def test_same_admin_key_same_request_returns_the_original(db: Database) -> None:
    project, live = live_project(db)
    seed_rows(db, (COMMANDS, deploy_row(project, live, key="deploy-key")))
    newer = uuid4()
    async with api_buses(db) as (commands, _queries):
        first = await commands.send(SuspendWebsiteCommand(ADMIN, project, "k-1"))
        again = await commands.send(SuspendWebsiteCommand(ADMIN, project, "k-1"))
        with pytest.raises(IdempotencyConflict):
            await commands.send(ResumeWebsiteCommand(ADMIN, project, "k-1"))
        finish(db, first.command_id)
        redeploy = await commands.send(RedeployReleaseCommand(ADMIN, project, "r-1"))
        finish(db, redeploy.command_id)
        # A newer release goes live: the same redeploy key is still the same request, because
        # the admin sends no release.
        go_live(db, project, old=live, new=newer)
        replayed = await commands.send(RedeployReleaseCommand(ADMIN, project, "r-1"))
        # The redeploy key is in the client key class: a deploy key is another request.
        with pytest.raises(IdempotencyConflict):
            await commands.send(RedeployReleaseCommand(ADMIN, project, "deploy-key"))
    assert again.replayed
    assert again.command_id == first.command_id
    assert replayed.replayed
    assert replayed.command_id == redeploy.command_id
    assert stored(db, redeploy.command_id)[4] == live
    assert (
        count(db, "SELECT count(*) FROM provisioning.commands WHERE project_id = %s", project) == 3
    )


def fail(db: Database, command_id: UUID) -> None:
    """End a command failed (as the worker would after a route it could not confirm)."""
    with db.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE provisioning.commands SET status = 'failed', failure_code = 'route_unconfirmed'"
            " WHERE command_id = %s",
            (command_id,),
        )


async def test_a_failed_admin_command_is_never_listed_for_the_event_retry(db: Database) -> None:
    """Decision 0023 G11: the hosting intake refuses an ``admin:`` key, so the retry sweep never
    lists a project for its failed admin command; a failed billing suspend still is."""
    admin_project, _ = live_project(db)
    billing_project, _ = live_project(db)
    seed_rows(
        db,
        (
            COMMANDS,
            command_row(billing_project, CommandKind.SUSPEND_HOSTING, 3, "failed", key="evt-1"),
        ),
    )
    async with api_buses(db) as (commands, _queries):
        accepted = await commands.send(SuspendWebsiteCommand(ADMIN, admin_project, "k-1"))
    fail(db, accepted.command_id)
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        listed = await PgHostingHistory(engine, TIMEOUTS).projects_to_retry(limit=10, budget=3)
    assert listed == (billing_project,)
