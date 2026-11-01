"""The bootstrap component's read (QA-04, wave 6): which projects need a tenant, and that the
statement reads only the columns migrations 00015 and 00023 grant to ``provisioning_bootstrap``.
The rows it returns are checked on PostgreSQL 18 in
``tests/integration/test_tenant_projects.py``."""

from uuid import UUID

from sqlalchemy import Column
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors

from provisioning.core.domain import ReleaseStatus
from provisioning.infrastructure.postgres.tenant_projects import (
    NEEDS_TENANT,
    projects_needing_tenant_statement,
)

# GRANT SELECT (project_id, status) ON provisioning.releases and
# GRANT SELECT (project_id, live_release_id) ON provisioning.project_hosting (migration 00015);
# GRANT SELECT (subdomain) ON provisioning.project_hosting (migration 00023, decision 0023 G18a).
GRANTED = {
    ("releases", "project_id"),
    ("releases", "status"),
    ("project_hosting", "project_id"),
    ("project_hosting", "live_release_id"),
    ("project_hosting", "subdomain"),
}
AFTER = UUID("0190a1b2-0000-7000-8000-000000000001")


def _table_columns(after: UUID | None) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for element in visitors.iterate(projects_needing_tenant_statement(after, 10)):
        if isinstance(element, Column) and element.table is not None:
            # An alias of a table (FR-54's other hosting row) needs the same grants as the table.
            name = getattr(getattr(element.table, "element", element.table), "name", "")
            if name in {"releases", "project_hosting"}:
                found.add((name, element.name))
    return found


def test_the_statement_reads_only_the_granted_columns() -> None:
    assert _table_columns(None) <= GRANTED
    assert _table_columns(AFTER) <= GRANTED


def test_a_hosted_project_needs_a_tenant() -> None:
    """A live site's project is listed by its hosting row, not only by an unfinished release."""
    assert ("project_hosting", "live_release_id") in _table_columns(None)
    assert ("project_hosting", "live_release_id") in _table_columns(AFTER)


def test_each_project_comes_with_its_subdomain() -> None:
    """The bootstrap writes the sub-domain into the namespace (decision 0023 G18a)."""
    statement = projects_needing_tenant_statement(None, 10)
    assert [c.name for c in statement.selected_columns] == [
        "project_id",
        "subdomain",
        "subdomain_taken",
    ]


def test_fr54_the_taken_check_reads_another_hosting_row_by_granted_columns_only() -> None:
    """FR-54.4 (TR-53): whether another project's row holds the sub-domain is read with the
    columns the bootstrap may select (project_id, subdomain), in the same statement."""
    sql = str(
        projects_needing_tenant_statement(None, 10).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]  # SQLAlchemy's dialect factory is untyped
        )
    )
    assert sql.count("FROM provisioning.project_hosting") == 2
    assert _table_columns(None) <= GRANTED


def test_the_statuses_that_need_a_tenant_include_live_and_not_the_finished_ones() -> None:
    assert ReleaseStatus.LIVE in NEEDS_TENANT
    assert {
        ReleaseStatus.SUPERSEDED,
        ReleaseStatus.FAILED,
        ReleaseStatus.ROLLED_BACK,
    }.isdisjoint(NEEDS_TENANT)


def test_the_page_is_ordered_limited_and_starts_after_the_cursor() -> None:
    sql = str(
        projects_needing_tenant_statement(AFTER, 7).compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]  # SQLAlchemy's dialect factory is untyped
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "EXISTS (SELECT provisioning.releases.project_id" in sql
    assert "SELECT *" not in sql  # every column of releases is not granted
    assert "ORDER BY" in sql
    assert "LIMIT 7" in sql
    assert sql.count(str(AFTER)) == 1  # the cursor applies to the hosting row
