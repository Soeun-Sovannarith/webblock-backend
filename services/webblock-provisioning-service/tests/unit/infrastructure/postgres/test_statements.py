"""The key lookup of ``statements.command_by_key`` (decision 0014 section 23): the client and
hosting key namespaces, and a ``sync_domains`` request refused before any SQL, since custom
domains were removed and Provisioning accepts no such command. A redeploy and an admin hosting key
are looked up like the others (decision 0023 G9, G11)."""

from uuid import UUID

import pytest

from provisioning.core.domain import CommandKind, CommandRequest
from provisioning.infrastructure.postgres import InvalidArgument
from provisioning.infrastructure.postgres.statements import client_command_by_key, command_by_key

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
RELEASE = UUID("5f0a3c9e-2b1d-4c6e-8f7a-9d0e1b2c3d4e")


def _kinds_compared(kind: CommandKind, release: UUID | None) -> list[str]:
    statement = command_by_key(CommandRequest(PROJECT, kind, release, "key-1"))
    compiled = statement.compile(compile_kwargs={"render_postcompile": True})
    names = {k.value for k in CommandKind}
    return sorted(v for v in compiled.params.values() if isinstance(v, str) and v in names)


@pytest.mark.parametrize(
    ("kind", "release", "expected"),
    [
        (CommandKind.DEPLOY, RELEASE, ["deploy", "promote", "redeploy", "rollback"]),
        (CommandKind.PROMOTE, RELEASE, ["deploy", "promote", "redeploy", "rollback"]),
        (CommandKind.SUSPEND_HOSTING, None, ["resume_hosting", "suspend_hosting"]),
        (CommandKind.RESUME_HOSTING, None, ["resume_hosting", "suspend_hosting"]),
    ],
)
def test_a_key_is_looked_up_in_its_kinds_namespace(
    kind: CommandKind, release: UUID | None, expected: list[str]
) -> None:
    assert _kinds_compared(kind, release) == expected


def test_a_sync_domains_request_is_refused_before_any_sql() -> None:
    with pytest.raises(InvalidArgument, match="sync_domains"):
        command_by_key(CommandRequest(PROJECT, CommandKind.SYNC_DOMAINS, None, "key-1"))


def test_a_redeploy_is_looked_up_in_the_client_key_class() -> None:
    """Decision 0023 G9: the API accepts a redeploy now (0021 D10 lifted); its key shares the
    client key class (commands_idempotency_key, migration 00020)."""
    assert _kinds_compared(CommandKind.REDEPLOY, RELEASE) == [
        "deploy",
        "promote",
        "redeploy",
        "rollback",
    ]


@pytest.mark.parametrize("kind", [CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING])
def test_an_admin_hosting_key_is_looked_up_in_the_hosting_key_class(kind: CommandKind) -> None:
    """Decision 0023 G11: the API store accepts admin hosting commands (``admin:<key>``) now; the
    hosting intake keeps refusing a Payment key that starts with ``admin:``."""
    statement = command_by_key(CommandRequest(PROJECT, kind, None, "admin:key-1"))
    compiled = statement.compile(compile_kwargs={"render_postcompile": True})
    assert "admin:key-1" in compiled.params.values()
    assert sorted(v for v in compiled.params.values() if v in {k.value for k in CommandKind}) == [
        "resume_hosting",
        "suspend_hosting",
    ]


def test_the_client_key_lookup_names_the_project_and_the_key() -> None:
    compiled = client_command_by_key(PROJECT, "key-9").compile(
        compile_kwargs={"render_postcompile": True}
    )
    values = list(compiled.params.values())
    assert PROJECT in values
    assert "key-9" in values
    assert "suspend_hosting" not in values
