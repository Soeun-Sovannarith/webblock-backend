"""Migration 00017 as written, without a database (decision 0014 section 23, amendment of
18 October 2026, owner). ``tests/integration/test_schema_00017.py`` proves it on PostgreSQL 18.

- The migrations are numbered without a gap but the reverted 00024 (it may be recorded as applied
  in a test or dev database, so its number is never reused; ``new-migration`` skill), and 00017
  is the intake revoke.
- 00017 revokes exactly the privileges 00015 granted ``provisioning_intake`` (except ``USAGE``,
  which 00008 granted first), and grants again exactly the two 00008 reads the intake guards need.
- It changes nothing else, refuses any role but the migrator, checks its role exists, and has no
  Down section (decision 0014 section 17 item 4).
"""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "migrations"
REVOKE = MIGRATIONS / "00017_revoke_intake_command_grants.sql"
REVERTED = 24  # migration 00024, reverted; its number is skipped, never reused


def _statements(path: Path) -> list[str]:
    """The top-level statements of a goose file, comments dropped, whitespace collapsed; the
    ``DO`` blocks between ``StatementBegin`` and ``StatementEnd`` are left out."""
    text = re.sub(
        r"-- \+goose StatementBegin.*?-- \+goose StatementEnd", "", path.read_text(), flags=re.S
    )
    text = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    return [" ".join(s.split()) for s in text.split(";") if s.strip()]


def _privileges(statement: str, verb: str, role: str) -> set[tuple[str, str]]:
    """(privilege, object) pairs of one ``GRANT ... TO role`` or ``REVOKE ... FROM role``,
    one pair per object; an empty set when the statement is something else."""
    preposition = "TO" if verb == "GRANT" else "FROM"
    match = re.fullmatch(rf"{verb} (.+?) ON (.+) {preposition} {role}", statement)
    if match is None:
        return set()
    privilege, objects = match.groups()
    return {(privilege, name.strip()) for name in objects.split(",")}


def test_the_migrations_are_numbered_without_a_gap_and_00017_is_the_revoke() -> None:
    numbers = sorted(int(p.name[:5]) for p in MIGRATIONS.glob("*.sql"))
    assert numbers[-1] > REVERTED
    assert numbers == [n for n in range(1, numbers[-1] + 1) if n != REVERTED]
    assert [p.name for p in MIGRATIONS.glob("00017_*.sql")] == [REVOKE.name]


def test_00017_revokes_exactly_the_intake_grants_of_00015_but_usage() -> None:
    (sync_domains,) = MIGRATIONS.glob("00015_*.sql")
    granted = set().union(
        *(_privileges(s, "GRANT", "provisioning_intake") for s in _statements(sync_domains))
    )
    revoked = set().union(
        *(_privileges(s, "REVOKE", "provisioning_intake") for s in _statements(REVOKE))
    )
    assert ("USAGE", "SCHEMA provisioning") in granted  # 00015's no-op repeat of 00008
    assert revoked == {p for p in granted if p[0] != "USAGE"}
    assert revoked == {
        ("SELECT", "provisioning.releases"),
        ("SELECT", "provisioning.project_hosting"),
        ("SELECT", "provisioning.release_steps"),
        ("SELECT", "provisioning.commands"),
        ("INSERT", "provisioning.commands"),
        ("UPDATE (last_command_number, updated_at)", "provisioning.project_hosting"),
        ("INSERT", "provisioning_ops.command_dispatch"),
    }


def test_00017_grants_again_exactly_the_two_00008_reads() -> None:
    (roles,) = MIGRATIONS.glob("00008_*.sql")
    renamed = {"provisioning.project_release_state": "provisioning.project_hosting"}  # 00011
    granted_by_00008 = {
        (privilege, renamed.get(name, name))
        for s in _statements(roles)
        for privilege, name in _privileges(s, "GRANT", "provisioning_intake")
    }
    regranted = set().union(
        *(_privileges(s, "GRANT", "provisioning_intake") for s in _statements(REVOKE))
    )
    assert regranted == {
        ("SELECT", "provisioning.releases"),
        ("SELECT (project_id)", "provisioning.project_hosting"),
    }
    assert regranted <= granted_by_00008


def test_00017_holds_nothing_but_those_revokes_and_grants() -> None:
    statements = _statements(REVOKE)
    assert len(statements) == 6
    for statement in statements:
        assert statement.startswith(("REVOKE ", "GRANT ")), statement
        assert statement.endswith(("FROM provisioning_intake", "TO provisioning_intake")), statement


def test_00017_runs_only_as_the_migrator_with_its_role_and_has_no_down_section() -> None:
    text = REVOKE.read_text()
    assert text.startswith("-- +goose Up\n")
    assert "-- +goose Down" not in text
    assert "IF current_user <> 'provisioning_migrator' THEN" in text
    assert "unnest(ARRAY['provisioning_intake'])" in text
    assert "ERRCODE = '42704'" in text
