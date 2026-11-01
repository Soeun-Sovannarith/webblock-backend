"""PostgresSiteAccess without a database: its configuration, its failures and its connection
bound. Its behaviour against PostgreSQL 18 is proven by tests/integration/catalog on the prototype
stack."""

import asyncio
import socket
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio

from provisioning.infrastructure.catalog import (
    AdminLogin,
    CatalogConnection,
    CatalogUnavailable,
    PostgresSiteAccess,
)

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
ADMIN = AdminLogin(user="provisioning_catalog_admin", password="CANARY-admin-password")  # noqa: S106 - a test value


def connection(**overrides: object) -> CatalogConnection:
    values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 5432,
        "database": "catalog",
        "sslmode": "disable",
        "connect_timeout_seconds": 2,
        "statement_timeout_ms": 5000,
        "lock_timeout_ms": 1000,
    }
    values.update(overrides)
    return CatalogConnection(**values)  # type: ignore[arg-type]  # exactly the dataclass fields


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("host", ""),
        ("port", 0),
        ("port", 70000),
        ("database", ""),
        ("sslmode", "prefer"),
        ("connect_timeout_seconds", 0),
        ("connect_timeout_seconds", 61),
        ("statement_timeout_ms", 0),
        ("statement_timeout_ms", 60001),
        ("lock_timeout_ms", 99),
    ],
)
def test_an_unsafe_connection_setting_is_refused(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        connection(**{field_name: value})


@pytest.mark.parametrize(
    ("user", "password"),
    [("", "secret"), ("provisioning_catalog_admin", ""), ("site_" + "0" * 32, "secret")],
)
def test_an_administrator_login_that_is_empty_or_a_site_role_is_refused(
    user: str, password: str
) -> None:
    with pytest.raises(ValueError, match="administrator"):
        AdminLogin(user=user, password=password)


def test_the_administrator_password_never_shows() -> None:
    for text in (repr(ADMIN), str(ADMIN), f"{ADMIN}"):
        assert "CANARY" not in text
    site_access = PostgresSiteAccess(connection(), ADMIN)
    assert "CANARY" not in repr(site_access)


@pytest.fixture
def unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest.mark.asyncio
async def test_an_unreachable_catalog_is_unavailable_without_driver_text(unused_port: int) -> None:
    site_access = PostgresSiteAccess(connection(port=unused_port), ADMIN)

    with pytest.raises(CatalogUnavailable) as caught:
        await site_access.role_exists(PROJECT)

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    text = str(caught.value)
    assert "CANARY" not in text
    assert "127.0.0.1" not in text
    assert str(unused_port) not in text


class HeldConnections:
    """A TCP server that accepts connections and never answers; it counts what it accepted."""

    def __init__(self) -> None:
        self.accepted = 0
        self.two_accepted = asyncio.Event()
        self.writers: list[asyncio.StreamWriter] = []

    def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.accepted += 1
        if self.accepted >= 2:
            self.two_accepted.set()
        self.writers.append(writer)


@pytest_asyncio.fixture
async def silent_catalog() -> AsyncIterator[tuple[int, HeldConnections]]:
    held = HeldConnections()
    server = await asyncio.start_server(held.accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, held
    finally:
        for writer in held.writers:
            writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_at_most_two_catalog_sessions_are_open_at_a_time(
    silent_catalog: tuple[int, HeldConnections],
) -> None:
    port, held = silent_catalog
    site_access = PostgresSiteAccess(connection(port=port, connect_timeout_seconds=2), ADMIN)

    calls = [asyncio.create_task(site_access.role_exists(PROJECT)) for _ in range(5)]
    async with asyncio.timeout(5):
        await held.two_accepted.wait()
    # The other three wait for a slot while the first two hang in the start-up of a silent server;
    # a third connection, if the bound were missing, would arrive within this half second.
    await asyncio.sleep(0.5)
    accepted_while_two_hang = held.accepted
    results = await asyncio.gather(*calls, return_exceptions=True)

    assert accepted_while_two_hang == 2
    assert all(isinstance(r, CatalogUnavailable) for r in results), results
    assert held.accepted == 5
